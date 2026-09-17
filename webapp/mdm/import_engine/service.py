"""public edition orchestration: parse through staging and preview, never master commit."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import ImportBatch, ImportFinding, ImportRow, ImportStatus

from .domain import (
    ImportMode,
    ImportType,
    Preview,
    PreviewRow,
    RowAction,
    RowStatus,
    Severity,
)
from .excel import parse_workbook
from .governance import BOOTSTRAP_GOVERNANCE_RULES, BootstrapGovernanceRules
from .matching import ReferenceCatalog
from .product_sku import evaluate_product_sku_v1
from .validation import (
    evaluate_customer_v1,
    evaluate_product_v1,
    evaluate_sku_v1,
    evaluate_workbook,
)


READY_FOR_REVIEW_SCHEMA_STATUS = ImportStatus.READY_FOR_REVIEW


def _json_safe(value: Any) -> Any:
    """Return a recursively JSON-safe staging snapshot without losing decimals."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class ImportService:
    """Internal service limited to ImportBatch and ImportRow persistence."""

    def __init__(self, session: Session):
        self.session = session

    def stage_file(
        self,
        path: str | Path,
        import_type: ImportType | str | None = None,
        *,
        mode: ImportMode | str = ImportMode.BOOTSTRAP,
        source_system: str = "EXCEL_BOOTSTRAP",
        created_by: str | None = None,
        uploaded_by: int | None = None,
        batch_id: str | None = None,
        commit: bool = True,
        governance: BootstrapGovernanceRules | None = None,
    ) -> Preview:
        selected_mode = ImportMode(mode)
        if selected_mode == ImportMode.OPERATIONAL and source_system == "EXCEL_BOOTSTRAP":
            source_system = "EXCEL_OPERATIONAL"
        selected_governance = (
            governance or BOOTSTRAP_GOVERNANCE_RULES
            if selected_mode == ImportMode.BOOTSTRAP
            else None
        )
        batch_id = batch_id or str(uuid4())
        parsed = parse_workbook(path, import_type)
        if selected_mode == ImportMode.OPERATIONAL and parsed.import_type not in {
            ImportType.CUSTOMER,
            ImportType.PRODUCT,
            ImportType.SKU,
            ImportType.PRODUCT_SKU,
        }:
            raise ValueError(
                "Customer/Product/SKU/商品导入 Import V1 each accept their own workbook"
            )
        digest = sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        source_digest = digest.hexdigest()
        existing = self.session.scalar(
            select(ImportBatch).where(ImportBatch.batch_id == batch_id)
        )
        if existing:
            existing_mode = (
                existing.import_mode.value
                if hasattr(existing.import_mode, "value")
                else str(existing.import_mode)
            )
            if (
                existing.entity_type != parsed.import_type.value
                or existing_mode != selected_mode.value
                or existing.source_sha256 != source_digest
            ):
                raise ValueError("batch_id already belongs to a different upload")
            return self.preview(existing.batch_id)

        catalog = ReferenceCatalog(self.session, source_system)
        if selected_mode == ImportMode.OPERATIONAL:
            if parsed.import_type == ImportType.CUSTOMER:
                rows = evaluate_customer_v1(parsed, catalog, batch_id)
            elif parsed.import_type == ImportType.PRODUCT:
                rows = evaluate_product_v1(parsed, catalog, batch_id)
            elif parsed.import_type == ImportType.SKU:
                rows = evaluate_sku_v1(parsed, catalog, batch_id)
            elif parsed.import_type == ImportType.PRODUCT_SKU:
                rows = evaluate_product_sku_v1(parsed, catalog, batch_id)
            else:  # pragma: no cover - guarded above
                raise ValueError(f"Unsupported import type: {parsed.import_type}")
        else:
            rows = evaluate_workbook(
                parsed,
                catalog,
                selected_mode,
                batch_id,
                selected_governance,
            )
        counts = Counter(row.status.value for row in rows)
        has_errors = counts[RowStatus.ERROR.value] > 0
        has_warnings = counts[RowStatus.WARNING.value] > 0
        batch = ImportBatch(
            batch_id=batch_id,
            source_file=(
                parsed.source_path.name
                if selected_mode == ImportMode.OPERATIONAL
                else str(parsed.source_path)
            ),
            entity_type=parsed.import_type.value,
            total_rows=len(rows),
            valid_rows=len(rows) - counts[RowStatus.ERROR.value],
            warning_rows=counts[RowStatus.WARNING.value],
            error_rows=counts[RowStatus.ERROR.value],
            status=(
                ImportStatus.REVIEW
                if selected_mode == ImportMode.OPERATIONAL
                and (has_errors or has_warnings)
                else ImportStatus.READY_TO_COMMIT
                if selected_mode == ImportMode.OPERATIONAL
                else READY_FOR_REVIEW_SCHEMA_STATUS
            ),
            import_mode=selected_mode.value,
            source_system=source_system,
            source_sha256=source_digest,
            worksheet=parsed.sheet,
            uploaded_by=uploaded_by,
            created_by=created_by,
        )
        self.session.add(batch)
        self.session.flush()
        for evaluated in rows:
            errors = [item.as_dict() for item in evaluated.issues if item.severity == Severity.ERROR]
            warnings = [item.as_dict() for item in evaluated.issues if item.severity == Severity.WARNING]
            information = [item.as_dict() for item in evaluated.issues if item.severity == Severity.INFO]
            metadata = {
                "phase_status": (
                    batch.status.value
                    if hasattr(batch.status, "value")
                    else str(batch.status)
                ),
                "source": {
                    "source_system": source_system,
                    "sheet": parsed.sheet,
                    "row_number": evaluated.parsed.row_number,
                    "ignored_empty_sheets": parsed.ignored_empty_sheets,
                },
                "mode": selected_mode.value,
                "governance": selected_governance.as_dict() if selected_governance else None,
                "matched_entity": evaluated.matched_entity.as_dict() if evaluated.matched_entity else None,
                "references": {
                    name: match.as_dict() if match else None
                    for name, match in evaluated.references.items()
                },
                "action": evaluated.action.value,
                "final_values": evaluated.final_values,
                "would_write": evaluated.would_write,
                "information": information,
                "product_candidate": evaluated.product_candidate,
                "exclusion": evaluated.exclusion,
            }
            normalized = dict(evaluated.normalized)
            normalized["_meta"] = metadata
            stored_status = (
                "READY"
                if selected_mode == ImportMode.OPERATIONAL
                and evaluated.status == RowStatus.VALID
                else evaluated.status.value
            )
            stored_row = ImportRow(
                import_batch_id=batch.id,
                row_number=evaluated.parsed.row_number,
                raw_values=_json_safe(evaluated.parsed.raw_values),
                normalized_values=_json_safe(normalized),
                errors=_json_safe(errors) or None,
                warnings=_json_safe(warnings) or None,
                resolved_entity_type=(
                    evaluated.matched_entity.entity_type
                    if evaluated.matched_entity
                    else None
                ),
                resolved_entity_id=(
                    evaluated.matched_entity.entity_id
                    if evaluated.matched_entity
                    else None
                ),
                final_target_stable_id=(
                    evaluated.matched_entity.stable_id
                    if evaluated.action == RowAction.EXISTING
                    and evaluated.matched_entity
                    else None
                ),
                commit_result=(
                    "EXISTING" if evaluated.action == RowAction.EXISTING else None
                ),
                reviewed_values=(
                    _json_safe(evaluated.final_values)
                    if stored_status == "READY"
                    else None
                ),
                status=stored_status,
            )
            self.session.add(stored_row)
            self.session.flush()
            if selected_mode == ImportMode.OPERATIONAL:
                for finding in evaluated.issues:
                    self.session.add(
                        ImportFinding(
                            import_batch_id=batch.id,
                            import_row_id=stored_row.id,
                            review_version=batch.review_version,
                            rule_code=finding.code,
                            severity=finding.severity.value,
                            message=finding.message,
                            field_name=finding.field,
                            details=_json_safe(
                                {
                                    "row_number": finding.row_number,
                                    "candidates": list(finding.candidates),
                                    "resolved": finding.resolved,
                                    "fact": finding.details,
                                }
                            ),
                        )
                    )
        self.session.flush()
        if commit:
            self.session.commit()
        return self.preview(batch_id)

    def preview(self, batch_id: str, filters: str | list[str] | tuple[str, ...] | None = None) -> Preview:
        batch = self.session.scalar(select(ImportBatch).where(ImportBatch.batch_id == batch_id))
        if not batch:
            raise LookupError(f"Import batch not found: {batch_id}")
        stored_rows = self.session.scalars(
            select(ImportRow)
            .where(ImportRow.import_batch_id == batch.id)
            .order_by(ImportRow.row_number)
        ).all()
        rows: list[PreviewRow] = []
        batch_governance: dict[str, Any] | None = None
        for stored in stored_rows:
            normalized = dict(stored.normalized_values or {})
            metadata = normalized.pop("_meta", {})
            batch_governance = batch_governance or metadata.get("governance")
            issues = tuple((stored.errors or []) + (stored.warnings or []) + metadata.get("information", []))
            rows.append(
                PreviewRow(
                    row_number=stored.row_number,
                    raw=dict(stored.raw_values),
                    normalized=normalized,
                    matched_entity=metadata.get("matched_entity"),
                    references=dict(metadata.get("references") or {}),
                    action=metadata.get("action", RowAction.UNIMPORTABLE.value),
                    status=stored.status,
                    issues=issues,
                    final_values=dict(metadata.get("final_values") or {}),
                    would_write=bool(metadata.get("would_write")),
                    product_candidate=metadata.get("product_candidate"),
                    exclusion=metadata.get("exclusion"),
                )
            )
        operational = (
            batch.import_mode.value
            if hasattr(batch.import_mode, "value")
            else str(batch.import_mode)
        ) == ImportMode.OPERATIONAL.value
        summary = self._summary(rows, operational=operational)
        metadata = {
            "schema_status": batch.status.value if hasattr(batch.status, "value") else batch.status,
            "phase_status": (
                batch.status.value if hasattr(batch.status, "value") else str(batch.status)
            ),
            "source_file": batch.source_file,
            "analysis": self._analysis(batch.entity_type, rows, batch_governance),
            "governance": batch_governance,
        }
        preview = Preview(batch.batch_id, batch.entity_type, metadata["phase_status"], summary, tuple(rows), metadata)
        return preview.filter(filters)

    @staticmethod
    def _summary(rows: list[PreviewRow], *, operational: bool = False) -> dict[str, Any]:
        actions = Counter(row.action for row in rows)
        statuses = Counter(row.status for row in rows)
        warning_rows = sum(
            any(item.get("severity") == Severity.WARNING.value for item in row.issues)
            for row in rows
        )
        error_rows = sum(
            any(item.get("severity") == Severity.ERROR.value for item in row.issues)
            for row in rows
        )
        commit_allowed = (
            bool(rows)
            and all(row.status in {"READY", "EXISTING"} for row in rows)
            if operational
            else error_rows == 0
        )
        summary = {
            "total": len(rows),
            "new": actions[RowAction.NEW.value],
            "update": actions[RowAction.UPDATE.value],
            "unchanged": actions[RowAction.UNCHANGED.value],
            "warning": warning_rows,
            "error": error_rows,
            "conflict": actions[RowAction.CONFLICT.value],
            "skipped": statuses[RowStatus.SKIPPED.value],
            "excluded": sum(bool(row.exclusion) for row in rows),
            "commit_allowed": commit_allowed,
        }
        if operational:
            summary["open_warning"] = statuses[RowStatus.WARNING.value]
        if actions[RowAction.EXISTING.value]:
            summary["existing"] = actions[RowAction.EXISTING.value]
        return summary

    @staticmethod
    def _analysis(
        entity_type: str,
        rows: list[PreviewRow],
        governance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        issue_rows: dict[str, set[int]] = {}
        for row in rows:
            for item in row.issues:
                issue_rows.setdefault(item["code"], set()).add(row.row_number)

        def count_issue(code: str) -> int:
            return len(issue_rows.get(code, set()))

        valid = sum(row.status != RowStatus.ERROR.value for row in rows)
        warning = sum(
            any(item.get("severity") == Severity.WARNING.value for item in row.issues)
            for row in rows
        )
        error = sum(
            any(item.get("severity") == Severity.ERROR.value for item in row.issues)
            for row in rows
        )
        if entity_type == ImportType.CHANNEL.value:
            names = Counter(row.normalized.get("channel_name") for row in rows if row.normalized.get("channel_name"))
            analysis = {
                "total_rows": len(rows),
                "valid": valid,
                "warning": warning,
                "error": error,
                "duplicate": sum(count > 1 for count in names.values()),
                "governed_historical_channels": (
                    governance.get("historical_channels", []) if governance else []
                ),
            }
            return analysis
        if entity_type == ImportType.SALESREP.value:
            names = Counter(row.normalized.get("salesrep_name") for row in rows if row.normalized.get("salesrep_name"))
            analysis = {
                "total_rows": len(rows),
                "valid": valid,
                "warning": warning,
                "error": error,
                "duplicate": sum(count > 1 for count in names.values()),
            }
            return analysis
        if entity_type == ImportType.CUSTOMER.value:
            codes = Counter(row.normalized.get("customer_code") for row in rows if row.normalized.get("customer_code"))
            analysis = {
                "total": len(rows),
                "included": sum(not row.exclusion for row in rows),
                "excluded": sum(bool(row.exclusion) for row in rows),
                "new": sum(row.action == RowAction.NEW.value for row in rows),
                "warning": warning,
                "error": error,
                "duplicate_customer_code": sum(count > 1 for count in codes.values()),
                "duplicate_customer_code_rows": count_issue("MDM_IMPORT_DUPLICATE_CUSTOMER_CODE"),
                "unresolved_channel": count_issue("MDM_IMPORT_CHANNEL_UNRESOLVED"),
                "historical_channel": count_issue("MDM_IMPORT_HISTORICAL_CHANNEL"),
                "non_sales_excluded": count_issue("MDM_IMPORT_NON_SALES_EXCLUDED"),
                "unresolved_salesrep": count_issue("MDM_IMPORT_SALESREP_UNRESOLVED"),
                "root_customer": sum(
                    row.final_values.get("parent_resolution") == "ROOT_SOURCE_SELF"
                    for row in rows
                ),
                "resolved_parent": sum(
                    row.final_values.get("parent_resolution") == "RESOLVED"
                    for row in rows
                ),
                "unresolved_parent": sum(
                    row.final_values.get("parent_resolution") == "UNRESOLVED"
                    for row in rows
                ),
                "true_self_reference_error": count_issue("MDM_IMPORT_PARENT_SELF"),
            }
            existing = sum(row.action == RowAction.EXISTING.value for row in rows)
            if existing:
                analysis["existing"] = existing
            return analysis
        if entity_type == ImportType.SKU.value:
            candidates = {
                row.product_candidate["source_product_code"]
                for row in rows
                if row.product_candidate and row.product_candidate.get("source_product_code")
            }
            conflicts = {
                row.product_candidate["source_product_code"]
                for row in rows
                if row.product_candidate and row.product_candidate.get("conflict")
            }
            detected_conflicts = {
                row.product_candidate["source_product_code"]
                for row in rows
                if row.product_candidate and row.product_candidate.get("detected_conflict")
            }
            resolved_conflicts = {
                row.product_candidate["source_product_code"]
                for row in rows
                if row.product_candidate
                and row.product_candidate.get("detected_conflict")
                and row.product_candidate.get("resolution")
            }
            return {
                "total": len(rows),
                "warning": warning,
                "error": error,
                "missing_short_name": sum(row.normalized.get("short_name") is None for row in rows),
                "missing_case_pack": sum(row.normalized.get("case_pack") is None for row in rows),
                "missing_created_at": sum(
                    row.raw.get("创建日期") is None
                    or (isinstance(row.raw.get("创建日期"), str) and not row.raw["创建日期"].strip())
                    for row in rows
                ),
                "product_candidate_count": len(candidates),
                "product_conflict_group_count": len(detected_conflicts),
                "product_conflict_groups": sorted(detected_conflicts),
                "product_unresolved_conflict_group_count": len(conflicts),
                "product_unresolved_conflict_groups": sorted(conflicts),
                "product_resolved_conflict_group_count": len(resolved_conflicts),
                "product_resolved_conflict_groups": sorted(resolved_conflicts),
            }
        return {"total": len(rows), "valid": valid, "warning": warning, "error": error}
