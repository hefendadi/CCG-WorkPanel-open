"""Thin HTTP adapter for 商品导入 V2 (merged Product + SKU import)."""

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
from ..import_engine.domain import ImportType
from ..import_engine.excel import ImportFileError
from ..import_engine.product_sku_commit import GoodsCommitService
from ..import_engine.product_sku_review import GoodsReviewService
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
    AcknowledgeRequest,
    AcknowledgeResponse,
    BatchDetail,
    BatchListItem,
    BatchStatus,
    EXISTING_CODE,
    ExistingView,
    FileError,
    FindingSeverity,
    FindingView,
    GOODS_IMPORT_SOURCE_SYSTEM,
    GOODS_TEMPLATE_COLUMNS,
    GOODS_TEMPLATE_SHEET,
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


router = APIRouter(prefix="/api/v1/mdm/goods-import", tags=["goods-import"])

_SHEET_LEVEL_FRAGMENTS = (
    "Required sheet is missing",
    "Required columns are missing",
    "Duplicate columns",
    "not uniquely identifiable",
)

GOODS_TEMPLATE_FILENAME = "goods_import_v2_template.xlsx"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".") + "Z"
    return str(value)


def _file_error_code(message: str) -> str:
    return (
        "MDM_IMPORT_SHEET_INVALID"
        if any(fragment in message for fragment in _SHEET_LEVEL_FRAGMENTS)
        else "MDM_IMPORT_FILE_INVALID"
    )


@router.get("/template")
def download_template(user=Depends(mdm_view)):
    return Response(
        content=build_import_template(GOODS_TEMPLATE_SHEET, GOODS_TEMPLATE_COLUMNS),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{GOODS_TEMPLATE_FILENAME}"'
        },
    )


def _goods_batch(db: Session, batch_id: str):
    batch = get_batch(db, batch_id)
    if (
        enum_value(batch.import_mode) != ImportMode.OPERATIONAL.value
        or batch.entity_type != ImportType.PRODUCT_SKU.value
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
    return batch, rows, findings, decisions_for(db, batch)


def _counts(rows) -> ReviewCounts:
    return derive_counts(RowStatus(row.status) for row in rows)


def _gates(rows, findings, counts: ReviewCounts) -> ReviewGates:
    row_by_id = {row.id: row for row in rows}
    open_errors = []
    open_warnings = []
    for finding in findings:
        row = row_by_id.get(finding.import_row_id)
        if row is None:
            continue
        if finding.severity == "ERROR" and not bool(
            (finding.details or {}).get("resolved")
        ):
            open_errors.append(
                {"row_number": row.row_number, "issue_code": finding.rule_code}
            )
        elif finding.severity == "WARNING" and row.status == RowStatus.WARNING.value:
            open_warnings.append(
                {"row_number": row.row_number, "issue_code": finding.rule_code}
            )
    return ReviewGates(
        open_errors=open_errors,
        open_warnings=open_warnings,
        ready=derive_commit_eligible(counts),
    )


def _finding_views(findings, decisions_by_finding) -> list[FindingView]:
    result = []
    for finding in findings:
        decision = decisions_by_finding.get(finding.id)
        result.append(
            FindingView(
                finding_id=finding.id,
                code=finding.rule_code,
                severity=finding.severity,
                message=finding.message,
                field=finding.field_name,
                details=(finding.details or {}).get("fact") or finding.details,
                resolved=bool(decision)
                or bool((finding.details or {}).get("resolved")),
                allowed_decisions=list(allowed_decisions(finding.rule_code)),
                decision=decision.decision if decision else None,
            )
        )
    return result


def _existing_view(row, findings) -> ExistingView:
    matched = row.final_target_stable_id
    difference = None
    for finding in findings:
        if finding.rule_code != EXISTING_CODE:
            continue
        fact = (finding.details or {}).get("fact") or {}
        difference = fact.get("differences") if isinstance(fact, dict) else None
        if matched is None and isinstance(fact, dict):
            matched = (fact.get("target") or {}).get("stable_id")
        break
    return ExistingView(
        is_existing=row.status == RowStatus.EXISTING.value,
        matched_stable_id=matched,
        master_detail_url=f"/api/v1/mdm/skus/{matched}" if matched else None,
        difference=difference,
    )


def _product_plan(row) -> dict[str, str]:
    meta = (row.normalized_values or {}).get("_meta") or {}
    plan = meta.get("product_candidate") or {}
    normalized = dict(row.normalized_values or {})
    plan_name = plan.get("product_name")
    return {
        "product_code": str(plan.get("product_code") or normalized.get("product_code") or ""),
        "product_action": str(plan.get("action") or ""),
        "product_name": str(plan_name or ""),
        "product_stable_id": str(plan.get("product_stable_id") or ""),
        "conflict_fields": ",".join(plan.get("conflict_fields") or []),
    }


def _review_rows(rows, findings, decisions_by_finding) -> list[ReviewRow]:
    by_row: dict[int, list[ImportFinding]] = defaultdict(list)
    for finding in findings:
        by_row[finding.import_row_id].append(finding)
    result = []
    for row in rows:
        normalized = dict(row.normalized_values or {})
        normalized.pop("_meta", None)
        row_findings = by_row.get(row.id, [])
        result.append(
            ReviewRow(
                row_number=row.row_number,
                status=row.status,
                identity={
                    "sku_code": str(normalized.get("sku_code") or ""),
                    "sku_name": str(normalized.get("sku_name") or ""),
                    **{key: value for key, value in _product_plan(row).items() if value},
                },
                raw_values=row.raw_values,
                normalized_values=normalized or None,
                reviewed_values=row.reviewed_values,
                findings=_finding_views(row_findings, decisions_by_finding),
                existing=_existing_view(row, row_findings),
            )
        )
    return result


def _decisions_by_finding(decisions):
    return {
        decision.import_finding_id: decision
        for decision in decisions
        if decision.import_finding_id is not None
    }


def _detail(db: Session, batch_id: str) -> BatchDetail:
    batch, rows, findings, _decisions = _goods_batch(db, batch_id)
    counts = _counts(rows)
    summary: dict[str, set[int]] = defaultdict(set)
    row_by_id = {row.id: row for row in rows}
    for finding in findings:
        if finding.import_row_id in row_by_id:
            summary[finding.rule_code].add(row_by_id[finding.import_row_id].row_number)
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
        summary_by_issue={key: sorted(value) for key, value in summary.items()},
    )


def _review_payload(db: Session, batch_id: str) -> ReviewPayload:
    batch, rows, findings, decisions = _goods_batch(db, batch_id)
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
    batch, rows, findings, _decisions = _goods_batch(db, preview.batch_id)
    by_row: dict[int, list[ImportFinding]] = defaultdict(list)
    for finding in findings:
        by_row[finding.import_row_id].append(finding)
    counts = _counts(rows)
    return UploadResult(
        batch_id=batch.batch_id,
        status=enum_value(batch.status),
        source_file=batch.source_file,
        source_sha256=batch.source_sha256,
        review_version=batch.review_version,
        validation=ValidationResult(
            file_valid=True,
            sheet=batch.worksheet or GOODS_TEMPLATE_SHEET,
            file_errors=[],
            counts=counts,
            commit_eligible=derive_commit_eligible(counts),
        ),
        rows=[
            RowSummary(
                row_number=row.row_number,
                status=row.status,
                issue_codes=[item.rule_code for item in by_row.get(row.id, [])],
            )
            for row in rows
        ],
    )


@router.post("/batches", status_code=201, response_model=UploadResult)
def upload(
    file: UploadFile = File(...),
    source_system: Optional[str] = Form(default=None),
    user=Depends(mdm_edit),
    db: Session = Depends(session),
):
    source_name = Path(file.filename or "").name or "upload.xlsx"
    if not source_name.lower().endswith(".xlsx"):
        return _failed_upload(
            source_name, "MDM_IMPORT_FILE_INVALID", "上传文件必须是 .xlsx 工作簿"
        )
    with tempfile.TemporaryDirectory(prefix="goods-import-") as tmpdir:
        path = Path(tmpdir) / source_name
        path.write_bytes(file.file.read())
        try:
            preview = ImportService(db).stage_file(
                path,
                ImportType.PRODUCT_SKU,
                mode=ImportMode.OPERATIONAL,
                source_system=(source_system or "").strip()
                or GOODS_IMPORT_SOURCE_SYSTEM,
                uploaded_by=user["id"],
            )
        except ImportFileError as exc:
            db.rollback()
            return _failed_upload(source_name, _file_error_code(str(exc)), str(exc))
    return _upload_result(db, preview)


@router.get("/batches")
def list_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(default=None),
    user=Depends(mdm_view),
    db: Session = Depends(session),
):
    if status is not None and status not in {item.value for item in BatchStatus}:
        error("MDM_INVALID_PARAMETER", "status 必须是有效的 BatchStatus", 400)
    query = select(ImportBatch).where(
        ImportBatch.import_mode == ImportMode.OPERATIONAL,
        ImportBatch.entity_type == ImportType.PRODUCT_SKU.value,
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
        counts = _counts(rows_for(db, batch))
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
def batch_detail(batch_id: str, user=Depends(mdm_view), db: Session = Depends(session)):
    return workflow_response(lambda: _detail(db, batch_id))


@router.get("/batches/{batch_id}/review", response_model=ReviewPayload)
def review(batch_id: str, user=Depends(mdm_view), db: Session = Depends(session)):
    return workflow_response(lambda: _review_payload(db, batch_id))


@router.post("/batches/{batch_id}/acknowledge", response_model=AcknowledgeResponse)
def acknowledge(
    batch_id: str,
    payload: AcknowledgeRequest,
    user=Depends(mdm_edit),
    db: Session = Depends(session),
):
    workflow_response(lambda: _goods_batch(db, batch_id))
    result = workflow_response(
        lambda: GoodsReviewService(db).acknowledge(
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
    workflow_response(lambda: _goods_batch(db, batch_id))
    return workflow_response(
        lambda: GoodsCommitService(db).commit(
            batch_id, user["id"], user["permissions"]["mdm"]
        )
    )
