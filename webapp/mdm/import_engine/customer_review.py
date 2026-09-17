"""Minimal Customer Import V1 warning acknowledgement and readiness service."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import (
    DecisionType,
    ImportDecision,
    ImportFinding,
    ImportMode,
    ImportRow,
    ImportStatus,
)

from .workflow import WorkflowError, enum_value, get_batch, row_meta, rows_for


_REVIEW_STATES = {
    ImportStatus.REVIEW,
    ImportStatus.READY_FOR_REVIEW,
    ImportStatus.READY_TO_COMMIT,
}


class CustomerReviewService:
    """Acknowledge current Warning Findings; no value resolution is supported."""

    entity_type = "CUSTOMER"
    entity_label = "Customer"

    def __init__(self, session: Session):
        self.session = session

    def acknowledge(
        self,
        batch_id: str,
        payload: dict[str, Any],
        operator_id: int,
    ) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id, lock=True)
        if (
            enum_value(batch.import_mode) != ImportMode.OPERATIONAL.value
            or batch.entity_type != self.entity_type
        ):
            raise WorkflowError(
                "MDM_IMPORT_MODE_NOT_SUPPORTED",
                f"{self.entity_label} Review only supports Operational {self.entity_label} batches",
                409,
            )
        if batch.status not in _REVIEW_STATES:
            raise WorkflowError(
                "MDM_IMPORT_STATE_CONFLICT",
                "Current Batch status does not allow Review acknowledgement",
                409,
                {"status": enum_value(batch.status)},
            )
        requested_version = payload.get("review_version")
        if requested_version != batch.review_version:
            raise WorkflowError(
                "MDM_IMPORT_STALE_REVIEW_VERSION",
                "review_version changed; reload Review",
                409,
                {"requested": requested_version, "current": batch.review_version},
            )
        finding_ids = payload.get("finding_ids")
        if (
            not isinstance(finding_ids, list)
            or not finding_ids
            or any(not isinstance(item, int) for item in finding_ids)
            or len(set(finding_ids)) != len(finding_ids)
        ):
            raise WorkflowError(
                "MDM_IMPORT_INVALID_ACKNOWLEDGEMENT",
                "finding_ids must be a non-empty unique integer list",
                422,
            )
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise WorkflowError(
                "MDM_IMPORT_INVALID_ACKNOWLEDGEMENT",
                "reason must be a string when supplied",
                422,
            )
        normalized_reason = reason.strip() if isinstance(reason, str) else None
        normalized_reason = normalized_reason or None

        findings = self._findings(batch.id, batch.review_version)
        by_id = {finding.id: finding for finding in findings}
        selected = [by_id.get(finding_id) for finding_id in finding_ids]
        if any(finding is None or finding.severity != "WARNING" for finding in selected):
            raise WorkflowError(
                "MDM_IMPORT_WARNING_NOT_FOUND",
                "Every finding_id must identify a current Warning Finding",
                422,
            )
        current_decisions = self._decisions(batch.id, batch.review_version)
        already_acknowledged = {
            decision.import_finding_id
            for decision in current_decisions
            if enum_value(decision.decision_type) == DecisionType.WARNING_ACK.value
        }
        if set(finding_ids) & already_acknowledged:
            raise WorkflowError(
                "MDM_IMPORT_WARNING_ALREADY_ACKNOWLEDGED",
                "A selected Warning is already acknowledged",
                409,
            )

        new_version = batch.review_version + 1
        copied_by_old_id: dict[int, ImportFinding] = {}
        for finding in findings:
            details = deepcopy(finding.details or {})
            details.setdefault("origin_finding_id", finding.id)
            copied = ImportFinding(
                import_batch_id=batch.id,
                import_row_id=finding.import_row_id,
                review_version=new_version,
                rule_code=finding.rule_code,
                severity=finding.severity,
                message=finding.message,
                field_name=finding.field_name,
                details=details,
            )
            self.session.add(copied)
            copied_by_old_id[finding.id] = copied
        self.session.flush()

        for decision in current_decisions:
            copied_finding = (
                copied_by_old_id.get(decision.import_finding_id)
                if decision.import_finding_id is not None
                else None
            )
            self.session.add(
                ImportDecision(
                    import_batch_id=batch.id,
                    import_row_id=decision.import_row_id,
                    import_finding_id=(copied_finding.id if copied_finding else None),
                    subject_key=decision.subject_key,
                    issue_code=decision.issue_code,
                    decision_type=decision.decision_type,
                    decision=decision.decision,
                    original_value=deepcopy(decision.original_value),
                    resolved_value=deepcopy(decision.resolved_value),
                    reason=decision.reason,
                    operator_id=decision.operator_id,
                    review_version=new_version,
                )
            )
        for finding in selected:
            copied = copied_by_old_id[finding.id]
            origin_id = (finding.details or {}).get("origin_finding_id", finding.id)
            self.session.add(
                ImportDecision(
                    import_batch_id=batch.id,
                    import_row_id=finding.import_row_id,
                    import_finding_id=copied.id,
                    subject_key=f"FINDING:{origin_id}",
                    issue_code=finding.rule_code,
                    decision_type=DecisionType.WARNING_ACK,
                    decision="ACKNOWLEDGE",
                    original_value={
                        "finding_id": origin_id,
                        "details": deepcopy(finding.details),
                    },
                    resolved_value={"acknowledged": True},
                    reason=normalized_reason,
                    operator_id=operator_id,
                    review_version=new_version,
                )
            )

        batch.review_version = new_version
        self.session.flush()
        result = self._apply_readiness(batch, rows_for(self.session, batch))
        self.session.commit()
        return result

    def get(self, batch_id: str) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id)
        return self._readiness(batch, rows_for(self.session, batch))

    def _apply_readiness(self, batch, rows: list[ImportRow]) -> dict[str, Any]:
        findings = self._findings(batch.id, batch.review_version)
        decisions = self._decisions(batch.id, batch.review_version)
        acknowledged = {
            decision.import_finding_id
            for decision in decisions
            if enum_value(decision.decision_type) == DecisionType.WARNING_ACK.value
        }
        by_row: dict[int, list[ImportFinding]] = {}
        for finding in findings:
            by_row.setdefault(finding.import_row_id, []).append(finding)

        for row in rows:
            if row.commit_result == "EXISTING":
                row.status = "EXISTING"
                row.reviewed_values = None
                continue
            row_findings = by_row.get(row.id, [])
            if any(finding.severity == "ERROR" for finding in row_findings):
                row.status = "ERROR"
                row.reviewed_values = None
                continue
            open_warnings = [
                finding
                for finding in row_findings
                if finding.severity == "WARNING" and finding.id not in acknowledged
            ]
            if open_warnings:
                row.status = "WARNING"
                row.reviewed_values = None
                continue
            reviewed = deepcopy(row_meta(row).get("final_values") or {})
            if any(
                finding.rule_code == "MDM_IMPORT_PARENT_UNRESOLVED"
                for finding in row_findings
            ):
                reviewed["parent_customer_stable_id"] = None
                reviewed["parent_resolution"] = "ACKNOWLEDGED_NULL"
            row.status = "READY"
            row.reviewed_values = reviewed

        batch.error_rows = sum(row.status == "ERROR" for row in rows)
        batch.warning_rows = sum(row.status == "WARNING" for row in rows)
        batch.valid_rows = batch.total_rows - batch.error_rows
        batch.status = (
            ImportStatus.REVIEW
            if batch.error_rows or batch.warning_rows
            else ImportStatus.READY_TO_COMMIT
        )
        return self._readiness(batch, rows)

    def _readiness(self, batch, rows: list[ImportRow]) -> dict[str, Any]:
        decisions = self._decisions(batch.id, batch.review_version)
        return {
            "batch_id": batch.batch_id,
            "status": enum_value(batch.status),
            "review_version": batch.review_version,
            "commit_eligible": batch.status == ImportStatus.READY_TO_COMMIT,
            "counts": {
                "total": batch.total_rows,
                "ready": sum(row.status == "READY" for row in rows),
                "existing": sum(row.status == "EXISTING" for row in rows),
                "warning": sum(row.status == "WARNING" for row in rows),
                "error": sum(row.status == "ERROR" for row in rows),
            },
            "decisions": [
                {
                    "id": decision.id,
                    "finding_id": decision.import_finding_id,
                    "row_number": decision.row.row_number if decision.row else None,
                    "issue_code": decision.issue_code,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "operator_id": decision.operator_id,
                    "review_version": decision.review_version,
                }
                for decision in decisions
            ],
        }

    def _findings(self, batch_id: int, review_version: int) -> list[ImportFinding]:
        return list(
            self.session.scalars(
                select(ImportFinding)
                .where(
                    ImportFinding.import_batch_id == batch_id,
                    ImportFinding.review_version == review_version,
                )
                .order_by(ImportFinding.id)
            ).all()
        )

    def _decisions(self, batch_id: int, review_version: int) -> list[ImportDecision]:
        return list(
            self.session.scalars(
                select(ImportDecision)
                .where(
                    ImportDecision.import_batch_id == batch_id,
                    ImportDecision.review_version == review_version,
                )
                .order_by(ImportDecision.id)
            ).all()
        )
