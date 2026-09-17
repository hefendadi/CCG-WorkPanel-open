"""Append-only Resolution, acknowledgement, and review-gate services."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from webapp.mdm.models import DecisionType, ImportDecision, ImportRow, ImportStatus

from .governance import BOOTSTRAP_GOVERNANCE_RULES
from .workflow import (
    MUTABLE_REVIEW_STATES,
    WorkflowError,
    decisions_for,
    ensure_bootstrap,
    enum_value,
    get_batch,
    issue_rows,
    row_issues,
    row_meta,
    rows_for,
    serialize_decision,
)


_RESOLUTION_WARNING_CODES = {
    "MDM_IMPORT_DUPLICATE_CUSTOMER_CODE",
    "MDM_IMPORT_PARENT_UNRESOLVED",
}
_GOVERNANCE_CODES = {
    "MDM_IMPORT_HISTORICAL_CHANNEL",
    "MDM_IMPORT_NON_SALES_EXCLUDED",
    "MDM_IMPORT_PRODUCT_MERGE_RESOLVED",
    "MDM_IMPORT_PRODUCT_MERGE_CONFLICT",
}
_ALLOWED = {
    "MDM_IMPORT_PARENT_UNRESOLVED": {"SET_NULL", "MAP_PARENT"},
    "MDM_IMPORT_DUPLICATE_CUSTOMER_CODE": {"KEEP_SEPARATE"},
    "MDM_IMPORT_PRODUCT_MERGE_RESOLVED": {"MERGE_AS_ONE_PRODUCT"},
    "MDM_IMPORT_PRODUCT_MERGE_CONFLICT": {"MERGE_AS_ONE_PRODUCT", "KEEP_SEPARATE", "REJECT_GROUP"},
    "MDM_IMPORT_HISTORICAL_CHANNEL": {"CREATE_INACTIVE_HISTORICAL"},
    "MDM_IMPORT_NON_SALES_EXCLUDED": {"EXCLUDE_NON_SALES_INTERCOMPANY"},
    "MDM_IMPORT_CHANNEL_UNRESOLVED": {"MAP_EXISTING", "CREATE_BOOTSTRAP_ENTITY", "EXCLUDE"},
    "MDM_IMPORT_CHANNEL_CONFLICT": {"MAP_EXISTING", "CREATE_BOOTSTRAP_ENTITY"},
    "MDM_IMPORT_SALESREP_UNRESOLVED": {"MAP_EXISTING", "CREATE_BOOTSTRAP_ENTITY"},
    "MDM_IMPORT_REGION_UNRESOLVED": {"MAP_EXISTING", "CREATE_BOOTSTRAP_ENTITY"},
    "MDM_IMPORT_PROVINCE_UNRESOLVED": {"MAP_EXISTING", "CREATE_BOOTSTRAP_ENTITY"},
}


def _product_groups(rows: list[ImportRow]) -> dict[str, list[ImportRow]]:
    groups: dict[str, list[ImportRow]] = defaultdict(list)
    for row in rows:
        candidate = row_meta(row).get("product_candidate") or {}
        code = candidate.get("source_product_code")
        if code:
            groups[str(code)].append(row)
    return groups


def required_resolution_gates(rows: list[ImportRow], entity_type: str) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    if entity_type == "CHANNEL":
        for number, rule in enumerate(BOOTSTRAP_GOVERNANCE_RULES.historical_channels, 1):
            code, name = f"CHN-HISTORY-{number:03d}", rule.channel_name
            gates.append({
                "subject_key": f"HISTORICAL_CHANNEL:{code}",
                "issue_code": "MDM_IMPORT_HISTORICAL_CHANNEL",
                "decision": "CREATE_INACTIVE_HISTORICAL",
                "expected": {"channel_code": code, "channel_name": name, "historical_number": number},
            })

    if entity_type == "CUSTOMER":
        by_code: dict[str, list[int]] = defaultdict(list)
        parent_rows: list[int] = []
        excluded: list[int] = []
        for row in rows:
            codes = {item.get("code") for item in row_issues(row)}
            normalized = dict(row.normalized_values or {})
            normalized.pop("_meta", None)
            if "MDM_IMPORT_DUPLICATE_CUSTOMER_CODE" in codes:
                by_code[str(normalized.get("customer_code"))].append(row.row_number)
            if "MDM_IMPORT_PARENT_UNRESOLVED" in codes:
                parent_rows.append(row.row_number)
            if "MDM_IMPORT_NON_SALES_EXCLUDED" in codes:
                excluded.append(row.row_number)
        for code, numbers in sorted(by_code.items()):
            gates.append({
                "subject_key": f"CUSTOMER_CODE:{code}",
                "issue_code": "MDM_IMPORT_DUPLICATE_CUSTOMER_CODE",
                "decision": "KEEP_SEPARATE",
                "expected": {"customer_code": code, "row_numbers": sorted(numbers)},
            })
        for number in sorted(parent_rows):
            gates.append({
                "subject_key": f"ROW:{number}",
                "issue_code": "MDM_IMPORT_PARENT_UNRESOLVED",
                "decisions": ["SET_NULL", "MAP_PARENT"],
                "expected": {"row_number": number},
            })
        if excluded:
            gates.append({
                "subject_key": "NON_SALES:INTERCOMPANY",
                "issue_code": "MDM_IMPORT_NON_SALES_EXCLUDED",
                "decision": "EXCLUDE_NON_SALES_INTERCOMPANY",
                "expected": {"classification": "NON_SALES / INTERCOMPANY", "row_numbers": sorted(excluded)},
            })

    if entity_type == "SKU":
        for code, members in sorted(_product_groups(rows).items()):
            candidate = row_meta(members[0]).get("product_candidate") or {}
            if candidate.get("detected_conflict") and candidate.get("resolution"):
                gates.append({
                    "subject_key": f"PRODUCT_GROUP:{code}",
                    "issue_code": "MDM_IMPORT_PRODUCT_MERGE_RESOLVED",
                    "decision": "MERGE_AS_ONE_PRODUCT",
                    "expected": {"source_product_code": code, "row_numbers": [row.row_number for row in members]},
                })
    return gates


def warning_gates(rows: list[ImportRow]) -> dict[str, list[int]]:
    return {
        code: numbers
        for code, numbers in issue_rows(rows, severity="WARNING").items()
        if code not in _RESOLUTION_WARNING_CODES
    }


def gate_report(session: Session, batch, rows: list[ImportRow] | None = None) -> dict[str, Any]:
    stored_rows = rows if rows is not None else rows_for(session, batch)
    decisions = decisions_for(session, batch)
    by_gate = {
        (item.subject_key, item.issue_code, enum_value(item.decision_type)): item
        for item in decisions
    }
    open_errors = []
    for row in stored_rows:
        for item in row.errors or []:
            if not item.get("resolved"):
                open_errors.append({"row_number": row.row_number, "issue_code": item.get("code")})

    open_resolutions = []
    for gate in required_resolution_gates(stored_rows, batch.entity_type):
        decision_type = (
            DecisionType.GOVERNANCE_CONFIRMATION.value
            if gate["issue_code"] in _GOVERNANCE_CODES
            else DecisionType.RESOLUTION.value
        )
        decision = by_gate.get((gate["subject_key"], gate["issue_code"], decision_type))
        allowed = set(gate.get("decisions") or [gate.get("decision")])
        if not decision or decision.decision not in allowed:
            open_resolutions.append(gate)

    open_acknowledgements = []
    for code, numbers in warning_gates(stored_rows).items():
        decision = by_gate.get((f"ISSUE:{code}", code, DecisionType.WARNING_ACK.value))
        resolved = dict(decision.resolved_value or {}) if decision else {}
        if not decision or sorted(resolved.get("row_numbers") or []) != numbers:
            open_acknowledgements.append({"issue_code": code, "row_numbers": numbers, "actual_count": len(numbers)})

    return {
        "open_errors": open_errors,
        "open_resolutions": open_resolutions,
        "open_acknowledgements": open_acknowledgements,
        "ready_to_confirm": not (open_errors or open_resolutions or open_acknowledgements),
    }


class ResolutionService:
    def __init__(self, session: Session):
        self.session = session

    def resolve(self, batch_id: str, payload: dict[str, Any], operator_id: int) -> dict[str, Any]:
        return self._append(batch_id, payload, operator_id, acknowledgement=False)

    def acknowledge(self, batch_id: str, payload: dict[str, Any], operator_id: int) -> dict[str, Any]:
        return self._append(batch_id, payload, operator_id, acknowledgement=True)

    def _append(
        self,
        batch_id: str,
        payload: dict[str, Any],
        operator_id: int,
        *,
        acknowledgement: bool,
    ) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id, lock=True)
        ensure_bootstrap(batch)
        if batch.status not in MUTABLE_REVIEW_STATES:
            raise WorkflowError("MDM_IMPORT_STATE_CONFLICT", "当前 Batch 状态不允许修改 Review", 409, {"status": enum_value(batch.status)})
        requested_version = payload.get("review_version")
        if requested_version != batch.review_version:
            raise WorkflowError(
                "MDM_IMPORT_STALE_REVIEW_VERSION",
                "review_version 已变化，请重新读取 Review",
                409,
                {"requested": requested_version, "current": batch.review_version},
            )
        incoming = payload.get("acknowledgements" if acknowledgement else "decisions")
        if not isinstance(incoming, list) or not incoming:
            raise WorkflowError("MDM_IMPORT_INVALID_DECISION", "请求必须包含非空决定列表", 422)

        rows = rows_for(self.session, batch)
        by_number = {row.row_number: row for row in rows}
        decision_type = DecisionType.WARNING_ACK if acknowledgement else None
        validated: list[dict[str, Any]] = []
        for item in incoming:
            if not isinstance(item, dict):
                raise WorkflowError("MDM_IMPORT_INVALID_DECISION", "Decision 必须是对象", 422)
            validated.append(
                self._validate_ack(item, rows) if acknowledgement else self._validate_resolution(item, batch.entity_type, rows, by_number)
            )

        previous = decisions_for(self.session, batch)
        new_version = batch.review_version + 1
        batch.review_version = new_version
        batch.confirmed_review_version = None
        batch.confirmed_by = None
        batch.confirmed_at = None
        batch.status = ImportStatus.RESOLVING

        replacement_keys = {
            (item["subject_key"], item["issue_code"], enum_value(item["decision_type"]))
            for item in validated
        }
        for old in previous:
            key = (old.subject_key, old.issue_code, enum_value(old.decision_type))
            if key not in replacement_keys:
                self.session.add(ImportDecision(
                    import_batch_id=batch.id,
                    import_row_id=old.import_row_id,
                    subject_key=old.subject_key,
                    issue_code=old.issue_code,
                    decision_type=old.decision_type,
                    decision=old.decision,
                    original_value=deepcopy(old.original_value),
                    resolved_value=deepcopy(old.resolved_value),
                    reason=old.reason,
                    operator_id=old.operator_id,
                    review_version=new_version,
                ))
        for item in validated:
            row = by_number.get(item.pop("row_number", None))
            self.session.add(ImportDecision(
                import_batch_id=batch.id,
                import_row_id=row.id if row else None,
                operator_id=operator_id,
                review_version=new_version,
                **item,
            ))
        self.session.flush()
        self._apply_resolved_errors(rows, decisions_for(self.session, batch))
        batch.error_rows = sum(any(not issue.get("resolved") for issue in (row.errors or [])) for row in rows)
        report = gate_report(self.session, batch, rows)
        batch.status = ImportStatus.READY_TO_CONFIRM if report["ready_to_confirm"] else ImportStatus.RESOLVING
        self.session.commit()
        return {
            "batch_id": batch.batch_id,
            "status": enum_value(batch.status),
            "review_version": batch.review_version,
            "gates": report,
            "decisions": [serialize_decision(item) for item in decisions_for(self.session, batch)],
        }

    def _validate_resolution(
        self,
        item: dict[str, Any],
        entity_type: str,
        rows: list[ImportRow],
        by_number: dict[int, ImportRow],
    ) -> dict[str, Any]:
        issue_code = str(item.get("issue_code") or "")
        decision = str(item.get("decision") or "")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise WorkflowError("MDM_IMPORT_DECISION_REASON_REQUIRED", "Resolution 必须提供 reason/note", 422)
        if issue_code not in _ALLOWED or decision not in _ALLOWED[issue_code]:
            raise WorkflowError("MDM_IMPORT_INVALID_DECISION", "issue_code 与 decision 组合不受支持", 422, {"issue_code": issue_code, "decision": decision})
        resolved = dict(item.get("resolved_value") or {})
        subject = str(item.get("subject_key") or "")
        row_number = item.get("row_number")
        expected_gates = {
            (gate["subject_key"], gate["issue_code"]): gate
            for gate in required_resolution_gates(rows, entity_type)
        }
        gate = expected_gates.get((subject, issue_code))
        if gate:
            expected = gate.get("expected") or {}
            if "row_numbers" in expected and sorted(resolved.get("row_numbers") or []) != sorted(expected["row_numbers"]):
                raise WorkflowError("MDM_IMPORT_DECISION_SCOPE_MISMATCH", "Decision row set 与当前 Review 不一致", 409)
            for key in ("channel_code", "channel_name", "source_product_code", "classification"):
                if key in expected and resolved.get(key) != expected[key]:
                    raise WorkflowError("MDM_IMPORT_DECISION_SCOPE_MISMATCH", f"Decision {key} 与当前 Review 不一致", 409)
            if issue_code == "MDM_IMPORT_PARENT_UNRESOLVED":
                row_number = expected["row_number"]
        elif row_number not in by_number:
            raise WorkflowError("MDM_IMPORT_DECISION_SUBJECT_NOT_FOUND", "Decision 未匹配当前 Review subject", 422)

        if decision in {"MAP_EXISTING", "MAP_PARENT"} and not isinstance(resolved.get("stable_id"), str):
            raise WorkflowError("MDM_IMPORT_INVALID_DECISION", "Mapping Resolution 必须提供 stable_id", 422)
        if decision == "MERGE_AS_ONE_PRODUCT":
            code = resolved.get("source_product_code")
            if str(code) not in _product_groups(rows):
                raise WorkflowError("MDM_IMPORT_PRODUCT_MERGE_NOT_APPROVED", "Product Merge 必须引用当前批次中的 Product group", 409)
            self._validate_representative(code, resolved, _product_groups(rows).get(str(code), []))
        original = item.get("original_value")
        if original is None:
            original = {"subject_key": subject, "issue_code": issue_code}
            if gate:
                original["review_gate"] = deepcopy(gate.get("expected") or {})
            elif row_number in by_number:
                original["row_number"] = row_number
                original["issues"] = [
                    deepcopy(issue)
                    for issue in row_issues(by_number[row_number])
                    if issue.get("code") == issue_code
                ]
        return {
            "row_number": row_number,
            "subject_key": subject,
            "issue_code": issue_code,
            "decision_type": DecisionType.GOVERNANCE_CONFIRMATION if issue_code in _GOVERNANCE_CODES else DecisionType.RESOLUTION,
            "decision": decision,
            "original_value": original,
            "resolved_value": resolved or None,
            "reason": reason.strip(),
        }

    def _validate_ack(self, item: dict[str, Any], rows: list[ImportRow]) -> dict[str, Any]:
        code = str(item.get("issue_code") or "")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise WorkflowError("MDM_IMPORT_DECISION_REASON_REQUIRED", "Acknowledgement 必须提供 reason/note", 422)
        expected = warning_gates(rows).get(code)
        resolved = dict(item.get("resolved_value") or {})
        if expected is None:
            raise WorkflowError("MDM_IMPORT_WARNING_NOT_FOUND", "当前 Review 不存在该 Warning gate", 422, {"issue_code": code})
        if sorted(resolved.get("row_numbers") or []) != expected or item.get("actual_count") != len(expected):
            raise WorkflowError("MDM_IMPORT_ACK_SCOPE_MISMATCH", "Acknowledgement 数量或 row set 与当前 Review 不一致", 409)
        if code == "MDM_IMPORT_PRODUCT_CANDIDATE":
            representatives = resolved.get("representatives")
            groups = _product_groups(rows)
            if not isinstance(representatives, dict) or set(representatives) != set(groups):
                raise WorkflowError("MDM_IMPORT_PRODUCT_REPRESENTATIVE_REQUIRED", "每个 Product candidate 必须选择代表性 SKU", 422)
            for product_code, members in groups.items():
                self._validate_representative(product_code, dict(representatives[product_code]), members)
        return {
            "row_number": None,
            "subject_key": f"ISSUE:{code}",
            "issue_code": code,
            "decision_type": DecisionType.WARNING_ACK,
            "decision": "ACKNOWLEDGE",
            "original_value": item.get("original_value") or {
                "issue_code": code,
                "row_numbers": expected,
                "actual_count": len(expected),
            },
            "resolved_value": resolved,
            "reason": reason.strip(),
        }

    @staticmethod
    def _validate_representative(code: str, resolved: dict[str, Any], members: list[ImportRow]) -> None:
        by_sku = {}
        for row in members:
            values = row_meta(row).get("final_values") or {}
            by_sku[str(values.get("sku_code"))] = values
        selected = str(resolved.get("representative_sku_code") or "")
        values = by_sku.get(selected)
        if not values:
            raise WorkflowError("MDM_IMPORT_PRODUCT_REPRESENTATIVE_REQUIRED", "代表性 SKU 不属于 Product candidate", 422, {"source_product_code": code})
        source_name = values.get("sku_name")
        if resolved.get("representative_sku_name") != source_name or resolved.get("product_name") != source_name:
            raise WorkflowError("MDM_IMPORT_PRODUCT_REPRESENTATIVE_REQUIRED", "Product 初始名称必须等于所选标准 SKU 名称", 422, {"source_product_code": code})

    @staticmethod
    def _apply_resolved_errors(rows: list[ImportRow], decisions: list[ImportDecision]) -> None:
        by_row_code = {
            (item.row.row_number, item.issue_code): item
            for item in decisions
            if item.row is not None and enum_value(item.decision_type) == DecisionType.RESOLUTION.value
        }
        for row in rows:
            changed = False
            errors = deepcopy(row.errors or [])
            for issue in errors:
                if (row.row_number, issue.get("code")) in by_row_code:
                    issue["resolved"] = True
                    changed = True
            if changed:
                row.errors = errors or None
                if not any(not issue.get("resolved") for issue in errors):
                    row.status = "WARNING" if row.warnings else "VALID"


class ReviewQueryService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, batch_id: str) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id)
        rows = rows_for(self.session, batch)
        return {
            "batch_id": batch.batch_id,
            "entity_type": batch.entity_type,
            "import_mode": enum_value(batch.import_mode),
            "source_system": batch.source_system,
            "status": enum_value(batch.status),
            "review_version": batch.review_version,
            "confirmed_review_version": batch.confirmed_review_version,
            "counts": {"total": batch.total_rows, "valid": batch.valid_rows, "warning": batch.warning_rows, "error": batch.error_rows},
            "gates": gate_report(self.session, batch, rows),
            "decisions": [serialize_decision(item) for item in decisions_for(self.session, batch)],
            "rows": [
                {
                    "row_number": row.row_number,
                    "status": row.status,
                    "raw_values": row.raw_values,
                    "normalized_values": row.normalized_values,
                    "issues": row_issues(row),
                    "resolved_entity_type": row.resolved_entity_type,
                    "resolved_entity_id": row.resolved_entity_id,
                }
                for row in rows
            ],
            "result_summary": batch.result_summary,
        }
