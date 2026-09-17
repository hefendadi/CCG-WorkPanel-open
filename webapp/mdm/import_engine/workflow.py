"""Shared Public import workflow primitives and contract-level errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import ImportBatch, ImportDecision, ImportMode, ImportRow, ImportStatus


@dataclass
class WorkflowError(Exception):
    code: str
    message: str
    http_status: int = 409
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


MUTABLE_REVIEW_STATES = {
    ImportStatus.READY_FOR_REVIEW,
    ImportStatus.RESOLVING,
    ImportStatus.READY_TO_CONFIRM,
}


def enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def get_batch(session: Session, batch_id: str, *, lock: bool = False) -> ImportBatch:
    statement = select(ImportBatch).where(ImportBatch.batch_id == batch_id)
    if lock:
        statement = statement.with_for_update()
    batch = session.scalar(statement)
    if not batch:
        raise WorkflowError(
            "MDM_IMPORT_BATCH_NOT_FOUND",
            "Import batch 不存在",
            404,
            {"batch_id": batch_id},
        )
    return batch


def ensure_bootstrap(batch: ImportBatch) -> None:
    if enum_value(batch.import_mode) != ImportMode.BOOTSTRAP.value:
        raise WorkflowError(
            "MDM_IMPORT_MODE_NOT_SUPPORTED",
            "Public import 仅支持 Bootstrap Commit",
            409,
            {"import_mode": enum_value(batch.import_mode)},
        )


def rows_for(session: Session, batch: ImportBatch) -> list[ImportRow]:
    return list(
        session.scalars(
            select(ImportRow)
            .where(ImportRow.import_batch_id == batch.id)
            .order_by(ImportRow.row_number)
        ).all()
    )


def decisions_for(
    session: Session, batch: ImportBatch, review_version: int | None = None
) -> list[ImportDecision]:
    version = review_version if review_version is not None else batch.review_version
    return list(
        session.scalars(
            select(ImportDecision)
            .where(
                ImportDecision.import_batch_id == batch.id,
                ImportDecision.review_version == version,
            )
            .order_by(ImportDecision.id)
        ).all()
    )


def row_meta(row: ImportRow) -> dict[str, Any]:
    return dict((row.normalized_values or {}).get("_meta") or {})


def row_issues(row: ImportRow) -> list[dict[str, Any]]:
    meta = row_meta(row)
    return list(row.errors or []) + list(row.warnings or []) + list(meta.get("information") or [])


def issue_rows(rows: Iterable[ImportRow], *, severity: str | None = None) -> dict[str, list[int]]:
    grouped: dict[str, set[int]] = {}
    for row in rows:
        for issue in row_issues(row):
            if severity is not None and issue.get("severity") != severity:
                continue
            grouped.setdefault(str(issue.get("code")), set()).add(row.row_number)
    return {code: sorted(numbers) for code, numbers in grouped.items()}


def serialize_decision(item: ImportDecision) -> dict[str, Any]:
    return {
        "id": item.id,
        "row_number": item.row.row_number if item.row else None,
        "subject_key": item.subject_key,
        "issue_code": item.issue_code,
        "decision_type": enum_value(item.decision_type),
        "decision": item.decision,
        "original_value": item.original_value,
        "resolved_value": item.resolved_value,
        "reason": item.reason,
        "operator_id": item.operator_id,
        "review_version": item.review_version,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
