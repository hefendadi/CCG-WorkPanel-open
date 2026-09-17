"""Customer Import V1 — HTTP adapter (public contract).

Thin FastAPI adapter over the public contract Engine surface (``ImportService``,
``CustomerReviewService``) and the frozen public contract contract
(``webapp.mdm.customer_import.contract``). This module contains NO validation,
matching, lifecycle or acknowledge rules: it authenticates callers, adapts
HTTP <-> Engine, and serializes Engine-produced data into the frozen contract
shapes. The one exception is the Admin Commit endpoint, which is reserved at
the interface level only (501) until the public contract Engine work lands.

Namespace ``/api/v1/mdm/customer-import/batches`` (isolated from the Bootstrap
``/api/v1/mdm/import-batches/...`` namespace; only OPERATIONAL Customer
batches are visible here):

    POST   /batches                         Upload (multipart file) -> 201 UploadResult
    GET    /batches                         Batch list (paginated, status filter)
    GET    /batches/{batch_id}              Batch detail + validation result
    GET    /batches/{batch_id}/review       Review page data (full rows/findings)
    POST   /batches/{batch_id}/acknowledge  minimal acknowledge (review_version + finding_ids + reason?)
    POST   /batches/{batch_id}/commit       Admin Commit (atomic CREATE; public contract engine)

Permission matrix (frozen in docs/import-contracts.md): Upload / List / Detail / Review /
Acknowledge are Operator+Admin; Commit is Admin-only.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api import current_user, error, mdm_edit, mdm_view, session, workflow_response
from ..import_engine.customer_commit import CustomerCommitService
from ..import_engine.customer_review import CustomerReviewService
from ..import_engine.domain import ImportType
from ..import_engine.excel import ImportFileError
from ..import_engine.service import ImportService
from ..import_engine.workflow import (
    WorkflowError,
    decisions_for,
    enum_value,
    get_batch,
    rows_for,
)
from ..import_template import XLSX_MEDIA_TYPE, build_import_template
from ..models import ImportBatch, ImportFinding, ImportMode
from .contract import (
    EXISTING_CODE,
    CUSTOMER_IMPORT_SOURCE_SYSTEM,
    CUSTOMER_TEMPLATE_COLUMNS,
    CUSTOMER_TEMPLATE_SHEET,
    AcknowledgeRequest,
    AcknowledgeResponse,
    BatchDetail,
    BatchListItem,
    BatchStatus,
    ExistingView,
    FileError,
    FindingSeverity,
    FindingView,
    ReviewCounts,
    ReviewGates,
    ReviewPayload,
    ReviewRow,
    RowStatus,
    RowSummary,
    UploadResult,
    ValidationResult,
    allowed_decisions,
    derive_commit_eligible,
    derive_counts,
)

router = APIRouter(prefix="/api/v1/mdm/customer-import", tags=["customer-import"])

_SHEET_LEVEL_FRAGMENTS = (
    "Required sheet is missing",
    "Required columns are missing",
    "Duplicate columns",
)

CUSTOMER_TEMPLATE_FILENAME = "customer_import_v1_template.xlsx"


def _iso(value: Any) -> Optional[str]:
    """Serialize a datetime the same way the shared MDM API does (UTC, Z)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".") + "Z"
    return str(value)


def _file_error_code(message: str) -> str:
    """Adapter mapping of an Engine ImportFileError to a frozen contract code.

    The Engine raises one ImportFileError class for every file-level rejection;
    the contract distinguishes unreadable workbooks (MDM_IMPORT_FILE_INVALID)
    from template-conformance rejections (MDM_IMPORT_SHEET_INVALID). This maps
    the Engine message to the contract vocabulary; no other logic lives here.
    """
    if any(fragment in message for fragment in _SHEET_LEVEL_FRAGMENTS):
        return "MDM_IMPORT_SHEET_INVALID"
    return "MDM_IMPORT_FILE_INVALID"


def _customer_batch(db: Session, batch_id: str):
    """Load one OPERATIONAL Customer batch with its current-version data.

    Raises WorkflowError (MDM_IMPORT_BATCH_NOT_FOUND / 404) for missing batches
    and for batches outside this namespace (Bootstrap modes / other entities).
    Returns (batch, rows, findings, decisions) with findings and decisions at
    the batch's current review_version.
    """
    batch = get_batch(db, batch_id)
    if (
        enum_value(batch.import_mode) != ImportMode.OPERATIONAL.value
        or batch.entity_type != ImportType.CUSTOMER.value
    ):
        raise WorkflowError(
            "MDM_IMPORT_BATCH_NOT_FOUND",
            "Import batch 不存在",
            404,
            {"batch_id": batch_id},
        )
    rows = rows_for(db, batch)
    findings = list(
        db.scalars(
            select(ImportFinding)
            .where(
                ImportFinding.import_batch_id == batch.id,
                ImportFinding.review_version == batch.review_version,
            )
            .order_by(ImportFinding.id)
        ).all()
    )
    decisions = decisions_for(db, batch)
    return batch, rows, findings, decisions


def _counts(rows) -> ReviewCounts:
    """Counts derived by the App from stored row statuses (contract rule §6)."""
    return derive_counts(RowStatus(row.status) for row in rows)


def _gates(rows, findings, counts: ReviewCounts) -> ReviewGates:
    row_by_id = {row.id: row for row in rows}
    open_errors: list[dict[str, Any]] = []
    open_warnings: list[dict[str, Any]] = []
    for finding in findings:
        row = row_by_id.get(finding.import_row_id)
        if row is None:
            continue
        if finding.severity == FindingSeverity.ERROR.value and not bool(
            (finding.details or {}).get("resolved")
        ):
            open_errors.append(
                {"row_number": row.row_number, "issue_code": finding.rule_code}
            )
        elif finding.severity == FindingSeverity.WARNING.value and row.status == RowStatus.WARNING.value:
            open_warnings.append(
                {"row_number": row.row_number, "issue_code": finding.rule_code}
            )
    return ReviewGates(
        open_errors=open_errors,
        open_warnings=open_warnings,
        ready=derive_commit_eligible(counts),
    )


def _summary_by_issue(rows, findings) -> dict[str, list[int]]:
    row_by_id = {row.id: row for row in rows}
    grouped: dict[str, set[int]] = defaultdict(set)
    for finding in findings:
        row = row_by_id.get(finding.import_row_id)
        if row is not None:
            grouped[finding.rule_code].add(row.row_number)
    return {code: sorted(numbers) for code, numbers in grouped.items()}


def _finding_views(findings, decisions_by_finding: dict[int, Any]) -> list[FindingView]:
    views = []
    for finding in findings:
        fact = (finding.details or {}).get("fact") or finding.details
        decision = decisions_by_finding.get(finding.id)
        views.append(
            FindingView(
                finding_id=finding.id,
                code=finding.rule_code,
                severity=finding.severity,
                message=finding.message,
                field=finding.field_name,
                details=fact,
                resolved=bool(decision) or bool((finding.details or {}).get("resolved")),
                allowed_decisions=list(allowed_decisions(finding.rule_code)),
                decision=decision.decision if decision else None,
            )
        )
    return views


def _existing_view(row, findings) -> ExistingView:
    matched = row.final_target_stable_id
    difference = None
    for finding in findings:
        if finding.rule_code != EXISTING_CODE:
            continue
        fact = (finding.details or {}).get("fact") or {}
        if isinstance(fact, dict) and fact.get("differences"):
            difference = fact["differences"]
        if matched is None:
            target = fact.get("target") if isinstance(fact, dict) else None
            if isinstance(target, dict):
                matched = target.get("stable_id")
        break
    return ExistingView(
        is_existing=row.status == RowStatus.EXISTING.value,
        matched_stable_id=matched,
        master_detail_url=f"/api/v1/mdm/customers/{matched}" if matched else None,
        difference=difference,
    )


def _review_rows(rows, findings, decisions_by_finding: dict[int, Any]) -> list[ReviewRow]:
    findings_by_row: dict[int, list[ImportFinding]] = defaultdict(list)
    for finding in findings:
        findings_by_row[finding.import_row_id].append(finding)
    result = []
    for row in rows:
        row_findings = findings_by_row.get(row.id, [])
        normalized = dict(row.normalized_values or {})
        normalized.pop("_meta", None)
        result.append(
            ReviewRow(
                row_number=row.row_number,
                status=row.status,
                identity={
                    "customer_code": str(normalized.get("customer_code") or ""),
                    "customer_name": str(normalized.get("customer_name") or ""),
                },
                raw_values=row.raw_values,
                normalized_values=normalized or None,
                reviewed_values=row.reviewed_values,
                findings=_finding_views(row_findings, decisions_by_finding),
                existing=_existing_view(row, row_findings),
            )
        )
    return result


def _decisions_by_finding(decisions) -> dict[int, Any]:
    return {
        decision.import_finding_id: decision
        for decision in decisions
        if decision.import_finding_id is not None
    }


def _batch_detail(db: Session, batch_id: str) -> BatchDetail:
    batch, rows, findings, decisions = _customer_batch(db, batch_id)
    counts = _counts(rows)
    return BatchDetail(
        batch_id=batch.batch_id,
        status=enum_value(batch.status),
        import_mode=enum_value(batch.import_mode),
        source_file=batch.source_file,
        source_sha256=batch.source_sha256,
        uploaded_by=batch.uploaded_by,
        created_at=_iso(batch.created_at),
        review_version=batch.review_version,
        counts=counts,
        gates=_gates(rows, findings, counts),
        commit_eligible=derive_commit_eligible(counts),
        summary_by_issue=_summary_by_issue(rows, findings),
    )


def _review_payload(db: Session, batch_id: str) -> ReviewPayload:
    batch, rows, findings, decisions = _customer_batch(db, batch_id)
    counts = _counts(rows)
    return ReviewPayload(
        batch_id=batch.batch_id,
        status=enum_value(batch.status),
        import_mode=enum_value(batch.import_mode),
        source_file=batch.source_file,
        review_version=batch.review_version,
        counts=counts,
        gates=_gates(rows, findings, counts),
        commit_eligible=derive_commit_eligible(counts),
        rows=_review_rows(rows, findings, _decisions_by_finding(decisions)),
    )


def _failed_upload(source_file: str, code: str, message: str) -> UploadResult:
    return UploadResult(
        batch_id="",
        status=BatchStatus.FAILED.value,
        source_file=source_file,
        source_sha256=None,
        review_version=None,
        validation=ValidationResult(
            file_valid=False,
            sheet=None,
            file_errors=[
                FileError(code=code, severity=FindingSeverity.ERROR, message=message)
            ],
            counts=ReviewCounts(),
            commit_eligible=False,
        ),
        rows=[],
    )


def _upload_result(db: Session, preview) -> UploadResult:
    batch = get_batch(db, preview.batch_id)
    rows = rows_for(db, batch)
    findings = list(
        db.scalars(
            select(ImportFinding)
            .where(
                ImportFinding.import_batch_id == batch.id,
                ImportFinding.review_version == batch.review_version,
            )
            .order_by(ImportFinding.id)
        ).all()
    )
    findings_by_row: dict[int, list[ImportFinding]] = defaultdict(list)
    for finding in findings:
        findings_by_row[finding.import_row_id].append(finding)
    counts = _counts(rows)
    return UploadResult(
        batch_id=batch.batch_id,
        status=enum_value(batch.status),
        source_file=batch.source_file,
        source_sha256=batch.source_sha256,
        review_version=batch.review_version,
        validation=ValidationResult(
            file_valid=True,
            sheet=batch.worksheet or CUSTOMER_TEMPLATE_SHEET,
            file_errors=[],
            counts=counts,
            commit_eligible=derive_commit_eligible(counts),
        ),
        rows=[
            RowSummary(
                row_number=row.row_number,
                status=row.status,
                issue_codes=[
                    finding.rule_code for finding in findings_by_row.get(row.id, [])
                ],
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/template")
def download_template(user=Depends(mdm_view)):
    return Response(
        content=build_import_template(
            CUSTOMER_TEMPLATE_SHEET, CUSTOMER_TEMPLATE_COLUMNS
        ),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{CUSTOMER_TEMPLATE_FILENAME}"'
            )
        },
    )


@router.post("/batches", status_code=201, response_model=UploadResult)
def upload(
    file: UploadFile = File(...),
    source_system: Optional[str] = Form(default=None),
    user=Depends(mdm_edit),
    db: Session = Depends(session),
):
    """Upload a frozen-template workbook -> 201 UploadResult (or FAILED body)."""
    source_name = Path(file.filename or "").name or "upload.xlsx"
    system = (source_system or "").strip() or CUSTOMER_IMPORT_SOURCE_SYSTEM
    if not source_name.lower().endswith(".xlsx"):
        return _failed_upload(
            source_name, "MDM_IMPORT_FILE_INVALID", "上传文件必须是 .xlsx 工作簿"
        )
    content = file.file.read()
    with tempfile.TemporaryDirectory(prefix="customer-import-") as tmpdir:
        path = Path(tmpdir) / source_name
        path.write_bytes(content)
        try:
            preview = ImportService(db).stage_file(
                path,
                ImportType.CUSTOMER,
                mode=ImportMode.OPERATIONAL,
                source_system=system,
                uploaded_by=user["id"],
            )
        except ImportFileError as exc:
            db.rollback()
            return _failed_upload(
                source_name, _file_error_code(str(exc)), str(exc)
            )
    return _upload_result(db, preview)


@router.get("/batches")
def list_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(default=None),
    user=Depends(mdm_view),
    db: Session = Depends(session),
):
    """Paginated OPERATIONAL Customer batch list with status filter."""
    if status is not None and status not in {item.value for item in BatchStatus}:
        error("MDM_INVALID_PARAMETER", "status 必须是有效的 BatchStatus", 400, {"status": status})
    query = select(ImportBatch).where(
        ImportBatch.import_mode == ImportMode.OPERATIONAL,
        ImportBatch.entity_type == ImportType.CUSTOMER.value,
    )
    if status is not None:
        query = query.where(ImportBatch.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    batches = db.scalars(
        query.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for batch in batches:
        rows = rows_for(db, batch)
        counts = _counts(rows)
        items.append(
            BatchListItem(
                batch_id=batch.batch_id,
                source_file=batch.source_file,
                status=enum_value(batch.status),
                uploaded_by=batch.uploaded_by,
                created_at=_iso(batch.created_at),
                counts=counts,
                commit_eligible=derive_commit_eligible(counts),
            )
        )
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/batches/{batch_id}", response_model=BatchDetail)
def batch_detail(
    batch_id: str,
    user=Depends(mdm_view),
    db: Session = Depends(session),
):
    return workflow_response(lambda: _batch_detail(db, batch_id))


@router.get("/batches/{batch_id}/review", response_model=ReviewPayload)
def review(
    batch_id: str,
    user=Depends(mdm_view),
    db: Session = Depends(session),
):
    return workflow_response(lambda: _review_payload(db, batch_id))


@router.post("/batches/{batch_id}/acknowledge", response_model=AcknowledgeResponse)
def acknowledge(
    batch_id: str,
    payload: AcknowledgeRequest,
    user=Depends(mdm_edit),
    db: Session = Depends(session),
):
    """Minimal Warning acknowledge; stale review_version is rejected with 409.

    reason is optional on the wire (public contract contract); it is passed through to
    the Engine unchanged — the Engine currently still enforces a reason and
    will relax it in public contract (see docs/import-contracts.md §8 and the public contract report).
    """
    workflow_response(lambda: _customer_batch(db, batch_id))  # namespace scope check (404)
    result = workflow_response(
        lambda: CustomerReviewService(db).acknowledge(
            batch_id, payload.model_dump(), user["id"]
        )
    )
    counts = dict(result["counts"])
    counts["create"] = counts["total"] - counts["error"] - counts["existing"]
    return AcknowledgeResponse(
        batch_id=result["batch_id"],
        status=result["status"],
        review_version=result["review_version"],
        commit_eligible=result["commit_eligible"],
        counts=counts,
        decisions=result["decisions"],
    )


@router.post("/batches/{batch_id}/commit")
def commit(
    batch_id: str,
    user=Depends(mdm_edit),
    db: Session = Depends(session),
):
    """MDM EDIT Commit — atomic CREATE for one READY_TO_COMMIT Batch.

    Pure adapter: namespace-scoped (404 outside this namespace) then delegated
    to the Engine's CustomerCommitService, which owns the global lock, the
    final revalidation, the all-or-nothing write and the audit trail. Missing
    MDM EDIT is rejected here (403) and re-checked inside the service; no
    Commit business rule lives in this module.
    """
    workflow_response(lambda: _customer_batch(db, batch_id))  # namespace scope (404)
    return workflow_response(
        lambda: CustomerCommitService(db).commit(
            batch_id, user["id"], user["permissions"]["mdm"]
        )
    )
