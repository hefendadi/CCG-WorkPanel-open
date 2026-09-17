"""Bootstrap Confirm and lifecycle services; Confirm never writes Master Data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import ImportBatch, ImportStatus

from .resolution import gate_report
from .workflow import WorkflowError, ensure_bootstrap, enum_value, get_batch, rows_for


def require_dependencies(session: Session, batch: ImportBatch) -> None:
    required = {"CHANNEL", "SALESREP"} if batch.entity_type == "CUSTOMER" else set()
    if not required:
        return
    committed = set(
        session.scalars(
            select(ImportBatch.entity_type).where(
                ImportBatch.source_system == batch.source_system,
                ImportBatch.status == ImportStatus.COMMITTED,
                ImportBatch.entity_type.in_(required),
            )
        ).all()
    )
    missing = sorted(required - committed)
    if missing:
        raise WorkflowError(
            "MDM_IMPORT_DEPENDENCY_MISSING",
            "上游 Bootstrap batch 尚未 COMMITTED",
            409,
            {"missing_entity_types": missing},
        )


class ConfirmationService:
    def __init__(self, session: Session):
        self.session = session

    def confirm(self, batch_id: str, payload: dict[str, Any], operator_id: int) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id, lock=True)
        ensure_bootstrap(batch)
        if batch.status not in {
            ImportStatus.READY_FOR_REVIEW,
            ImportStatus.RESOLVING,
            ImportStatus.READY_TO_CONFIRM,
        }:
            raise WorkflowError("MDM_IMPORT_STATE_CONFLICT", "当前 Batch 状态不允许 Confirm", 409, {"status": enum_value(batch.status)})
        requested_version = payload.get("review_version")
        if requested_version != batch.review_version:
            raise WorkflowError(
                "MDM_IMPORT_STALE_REVIEW_VERSION",
                "Confirm review_version 与当前 Review 不一致",
                409,
                {"requested": requested_version, "current": batch.review_version},
            )
        report = gate_report(self.session, batch, rows_for(self.session, batch))
        if not report["ready_to_confirm"] or batch.error_rows:
            raise WorkflowError("MDM_IMPORT_REVIEW_INCOMPLETE", "当前 Review 仍有未关闭 gate", 409, report)
        require_dependencies(self.session, batch)
        batch.status = ImportStatus.READY_TO_CONFIRM
        self.session.flush()
        batch.status = ImportStatus.CONFIRMED
        batch.confirmed_review_version = batch.review_version
        batch.confirmed_by = operator_id
        batch.confirmed_at = datetime.now(timezone.utc)
        self.session.commit()
        return {
            "batch_id": batch.batch_id,
            "status": enum_value(batch.status),
            "review_version": batch.review_version,
            "confirmed_review_version": batch.confirmed_review_version,
            "confirmed_by": batch.confirmed_by,
            "confirmed_at": batch.confirmed_at.isoformat() if batch.confirmed_at else None,
        }

    def cancel(self, batch_id: str, operator_id: int) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id, lock=True)
        ensure_bootstrap(batch)
        if batch.status not in {
            ImportStatus.READY_FOR_REVIEW,
            ImportStatus.RESOLVING,
            ImportStatus.READY_TO_CONFIRM,
            ImportStatus.CONFIRMED,
        }:
            raise WorkflowError("MDM_IMPORT_STATE_CONFLICT", "当前 Batch 状态不允许 Cancel", 409, {"status": enum_value(batch.status)})
        batch.status = ImportStatus.CANCELLED
        batch.result_summary = {"cancelled_by": operator_id, "cancelled_at": datetime.now(timezone.utc).isoformat()}
        self.session.commit()
        return {"batch_id": batch.batch_id, "status": enum_value(batch.status)}

