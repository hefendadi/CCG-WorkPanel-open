"""Matching and contract validation for normalized import rows."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .domain import (
    ADAPTERS,
    SKU_V1_ADAPTER,
    EvaluatedRow,
    ImportMode,
    ImportType,
    Issue,
    Match,
    ParsedWorkbook,
    RowAction,
    Severity,
)
from .excel import ImportFileError
from .matching import ReferenceCatalog, proposed_action
from .governance import BOOTSTRAP_GOVERNANCE_RULES, BootstrapGovernanceRules
from .normalize import normalize_row, normalize_text
from webapp.mdm.customer_codes import customer_code_key


def issue(
    code: str,
    severity: Severity,
    message: str,
    row: EvaluatedRow,
    field: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    resolved: bool = False,
    details: dict[str, Any] | None = None,
) -> Issue:
    return Issue(
        code,
        severity,
        message,
        field,
        row.parsed.row_number,
        tuple(candidates or ()),
        resolved,
        details,
    )


def _match_dicts(candidates) -> list[dict[str, Any]]:
    return [
        {
            "stable_id": candidate.stable_id,
            "name": candidate.name,
            "staging_batch_id": candidate.staging_batch_id,
            "staging_row_number": candidate.staging_row_number,
        }
        for candidate in candidates
    ]


def _normalize(
    parsed: ParsedWorkbook,
    governance: BootstrapGovernanceRules | None,
) -> list[EvaluatedRow]:
    evaluated: list[EvaluatedRow] = []
    adapter = parsed.adapter or ADAPTERS[parsed.import_type]
    for parsed_row in parsed.rows:
        normalized, errors = normalize_row(
            parsed.import_type, parsed_row, adapter=adapter
        )
        row = EvaluatedRow(parsed_row, normalized, final_values=dict(normalized))
        for field, detail in errors.items():
            raw_value = parsed_row.cells[field].value
            if (
                parsed.import_type == ImportType.SKU
                and field == "source_created_at"
                and governance
                and governance.is_source_created_at_label(raw_value)
            ):
                row.add_issue(
                    issue(
                        "MDM_IMPORT_SOURCE_DATE_LABEL",
                        Severity.INFO,
                        "Source value is a month/business label, not a reliable created_at; raw value retained and created_at remains NULL",
                        row,
                        field,
                    )
                )
            elif adapter.field_kinds[field] == "date":
                row.add_issue(issue("MDM_IMPORT_DATE_INVALID", Severity.WARNING, detail, row, field))
            elif adapter.field_kinds[field] == "code":
                row.add_issue(issue("MDM_IMPORT_CODE_NOT_STRING_SAFE", Severity.ERROR, detail, row, field))
            else:
                row.add_issue(issue("MDM_IMPORT_VALUE_INVALID", Severity.ERROR, detail, row, field))
        for field in adapter.required_fields:
            if normalized.get(field) is None:
                row.add_issue(
                    issue(
                        "MDM_IMPORT_REQUIRED_FIELD_MISSING",
                        Severity.ERROR,
                        f"Required field is missing: {field}",
                        row,
                        field,
                    )
                )
        evaluated.append(row)
    return evaluated


def _mark_exact_duplicates(rows: list[EvaluatedRow]):
    seen: dict[tuple[tuple[str, str], ...], EvaluatedRow] = {}
    for row in rows:
        signature = tuple(sorted((key, repr(value)) for key, value in row.normalized.items()))
        if signature in seen:
            row.add_issue(
                issue(
                    "MDM_IMPORT_DUPLICATE_EXACT",
                    Severity.INFO,
                    f"Exact normalized duplicate of row {seen[signature].parsed.row_number}",
                    row,
                )
            )
            row.action = RowAction.UNCHANGED
        else:
            seen[signature] = row


def _match_identity(rows: list[EvaluatedRow], import_type: ImportType, catalog: ReferenceCatalog, mode: ImportMode):
    for row in rows:
        candidate, candidates = catalog.identity(import_type, row.normalized, mode)
        if candidate:
            row.matched_entity = candidate.match("BUSINESS_CODE_OR_BOOTSTRAP_NAME", 1, "unique deterministic master match")
            row.action = proposed_action(candidate, row.normalized)
        elif len(candidates) > 1:
            row.action = RowAction.CONFLICT


def _channel(rows: list[EvaluatedRow], mode: ImportMode):
    names: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        name = row.normalized.get("channel_name")
        if name:
            names[name].append(row)
        if mode == ImportMode.BOOTSTRAP and row.action == RowAction.NEW:
            row.add_issue(
                issue(
                    "MDM_IMPORT_CHANNEL_BOOTSTRAP_REQUIRED",
                    Severity.WARNING,
                    "Bootstrap Channel candidate requires Admin confirmation",
                    row,
                    "channel_name",
                )
            )
    for duplicate_rows in names.values():
        if len(duplicate_rows) < 2:
            continue
        candidates = [{"row_number": item.parsed.row_number} for item in duplicate_rows]
        for row in duplicate_rows:
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_POSSIBLE_DUPLICATE_NAME",
                    Severity.WARNING,
                    "Normalized Channel name appears more than once",
                    row,
                    "channel_name",
                    candidates,
                )
            )


def _salesrep(rows: list[EvaluatedRow]):
    names: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        name = row.normalized.get("salesrep_name")
        if name:
            names[name].append(row)
    for duplicate_rows in names.values():
        if len(duplicate_rows) < 2:
            continue
        candidates = [{"row_number": item.parsed.row_number} for item in duplicate_rows]
        for row in duplicate_rows:
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_SALESREP_AMBIGUOUS",
                    Severity.WARNING,
                    "Normalized SalesRep name has multiple candidates",
                    row,
                    "salesrep_name",
                    candidates,
                )
            )


def _customer_duplicates(rows: list[EvaluatedRow]):
    by_code: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        code = row.normalized.get("customer_code")
        if code:
            by_code[code].append(row)
    for duplicate_rows in by_code.values():
        if len(duplicate_rows) < 2:
            continue
        candidates = [
            {"row_number": item.parsed.row_number, "customer_name": item.normalized.get("customer_name")}
            for item in duplicate_rows
        ]
        for row in duplicate_rows:
            row.add_issue(
                issue(
                    "MDM_IMPORT_DUPLICATE_CUSTOMER_CODE",
                    Severity.WARNING,
                    "customer_code is duplicated; rows remain separate and are not merged",
                    row,
                    "customer_code",
                    candidates,
                )
            )


def _customer_references(
    rows: list[EvaluatedRow],
    catalog: ReferenceCatalog,
    mode: ImportMode,
    batch_id: str,
    governance: BootstrapGovernanceRules | None,
):
    by_name: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        name = row.normalized.get("customer_name")
        if name:
            by_name[name].append(row)

    parent_edges: dict[int, int] = {}
    for row in rows:
        channel_ref = row.normalized.get("channel_ref")
        historical_rule = governance.historical_channel(channel_ref) if governance else None
        exclusion_rule = governance.non_sales_exclusion(channel_ref) if governance else None
        channel_resolution: dict[str, Any] | None = None
        channel_candidates = []
        if exclusion_rule:
            channel_match = None
            row.exclusion = {
                "classification": exclusion_rule.classification,
                "reason": exclusion_rule.exclusion_reason,
                "source_field": "channel_ref",
                "source_value": channel_ref,
                "governance_rule_id": governance.rule_id,
            }
            row.add_issue(
                issue(
                    "MDM_IMPORT_NON_SALES_EXCLUDED",
                    Severity.INFO,
                    "Intercompany source record is preserved but excluded from the normal sales Customer master",
                    row,
                    "channel_ref",
                    resolved=True,
                )
            )
        elif historical_rule:
            channel_resolution = historical_rule.as_dict()
            channel_resolution["governance_rule_id"] = governance.rule_id
            channel_match = Match(
                "CHANNEL",
                None,
                None,
                historical_rule.channel_name,
                "BOOTSTRAP_GOVERNANCE_HISTORICAL_CHANNEL",
                1,
                historical_rule.reason,
                governance=channel_resolution,
            )
            row.add_issue(
                issue(
                    "MDM_IMPORT_HISTORICAL_CHANNEL",
                    Severity.INFO,
                    "Historical Channel is retained as an independent INACTIVE candidate",
                    row,
                    "channel_ref",
                    resolved=True,
                )
            )
        else:
            channel_match, channel_candidates = catalog.reference(ImportType.CHANNEL, channel_ref, mode)
        row.references["channel"] = channel_match
        if not channel_match and not exclusion_rule:
            if channel_candidates and all(not candidate.active for candidate in channel_candidates):
                row.add_issue(issue("MDM_IMPORT_INACTIVE_REFERENCE", Severity.ERROR, "Channel reference is inactive", row, "channel_ref", _match_dicts(channel_candidates)))
            elif len(channel_candidates) > 1:
                row.add_issue(issue("MDM_IMPORT_CHANNEL_CONFLICT", Severity.ERROR, "Channel reference has multiple candidates", row, "channel_ref", _match_dicts(channel_candidates)))
            else:
                row.add_issue(issue("MDM_IMPORT_CHANNEL_UNRESOLVED", Severity.ERROR, "Channel reference cannot be resolved", row, "channel_ref"))

        salesrep_match, salesrep_candidates = catalog.reference(ImportType.SALESREP, row.normalized.get("salesrep_ref"), mode)
        row.references["salesrep"] = salesrep_match
        if not salesrep_match:
            if salesrep_candidates and all(not candidate.active for candidate in salesrep_candidates):
                row.add_issue(issue("MDM_IMPORT_INACTIVE_REFERENCE", Severity.ERROR, "SalesRep reference is inactive", row, "salesrep_ref", _match_dicts(salesrep_candidates)))
            else:
                if len(salesrep_candidates) > 1:
                    row.add_issue(issue("MDM_IMPORT_SALESREP_AMBIGUOUS", Severity.WARNING, "SalesRep reference has multiple candidates", row, "salesrep_ref", _match_dicts(salesrep_candidates)))
                row.add_issue(issue("MDM_IMPORT_SALESREP_UNRESOLVED", Severity.ERROR, "SalesRep reference cannot be resolved", row, "salesrep_ref", _match_dicts(salesrep_candidates)))

        for field, entity in (("region_ref", "REGION"), ("province_ref", "PROVINCE")):
            name = row.normalized.get(field)
            row.references[field.removesuffix("_ref")] = (
                Match(entity, None, None, name, "REFERENCE_CANDIDATE", 1, "Bootstrap reference candidate")
                if name
                else None
            )

        parent = row.normalized.get("parent_customer_ref")
        customer_name = row.normalized.get("customer_name")
        parent_resolution = "EMPTY"
        if not parent:
            row.references["parent_customer"] = None
        elif parent == customer_name:
            # The source workbook uses the customer's own name to represent a
            # root node.  It is a source convention, not an MDM self-relation.
            row.references["parent_customer"] = None
            parent_resolution = "ROOT_SOURCE_SELF"
        elif len(by_name.get(parent, [])) == 1:
            target = by_name[parent][0]
            target_match = target.matched_entity
            parent_match = Match(
                "CUSTOMER",
                target_match.entity_id if target_match else None,
                target_match.stable_id if target_match else None,
                parent,
                "CURRENT_BATCH_NORMALIZED_NAME",
                1,
                "unique normalized parent name in current batch",
                batch_id,
                target.parsed.row_number,
            )
            row.references["parent_customer"] = parent_match
            if (
                row.matched_entity
                and row.matched_entity.entity_id is not None
                and parent_match.entity_id == row.matched_entity.entity_id
            ):
                parent_resolution = "TRUE_SELF_REFERENCE_ERROR"
                row.add_issue(
                    issue(
                        "MDM_IMPORT_PARENT_SELF",
                        Severity.ERROR,
                        "Resolved Parent Customer is the same MDM entity as the current Customer",
                        row,
                        "parent_customer_ref",
                    )
                )
            else:
                parent_resolution = "RESOLVED"
                parent_edges[row.parsed.row_number] = target.parsed.row_number
        else:
            master_match, candidates = catalog.reference(ImportType.CUSTOMER, parent, mode)
            row.references["parent_customer"] = master_match
            if not master_match:
                parent_resolution = "UNRESOLVED"
                row.add_issue(issue("MDM_IMPORT_PARENT_UNRESOLVED", Severity.WARNING, "Parent Customer cannot be uniquely resolved; final parent is NULL", row, "parent_customer_ref", _match_dicts(candidates)))
            elif (
                row.matched_entity
                and row.matched_entity.entity_id is not None
                and master_match.entity_id == row.matched_entity.entity_id
            ):
                parent_resolution = "TRUE_SELF_REFERENCE_ERROR"
                row.add_issue(
                    issue(
                        "MDM_IMPORT_PARENT_SELF",
                        Severity.ERROR,
                        "Resolved Parent Customer is the same MDM entity as the current Customer",
                        row,
                        "parent_customer_ref",
                    )
                )
            else:
                parent_resolution = "RESOLVED"

        row.final_values = {
            key: value
            for key, value in row.normalized.items()
            if key not in {"channel_ref", "salesrep_ref", "region_ref", "province_ref", "parent_customer_ref"}
        }
        row.final_values.update(
            {
                "channel_stable_id": channel_match.stable_id if channel_match else None,
                "channel_resolution": channel_resolution,
                "salesrep_stable_id": salesrep_match.stable_id if salesrep_match else None,
                "region_candidate": row.normalized.get("region_ref"),
                "province_candidate": (
                    row.normalized.get("province_ref")
                ),
                "parent_customer_stable_id": (
                    row.references["parent_customer"].stable_id
                    if parent_resolution == "RESOLVED" and row.references.get("parent_customer")
                    else None
                ),
                "parent_resolution": parent_resolution,
            }
        )

    _mark_parent_cycles(rows, parent_edges)


def _mark_parent_cycles(
    rows: list[EvaluatedRow], parent_edges: dict[int, int]
) -> None:
    cycle_nodes: set[int] = set()
    for start in parent_edges:
        path: list[int] = []
        positions: dict[int, int] = {}
        current = start
        while current in parent_edges:
            if current in positions:
                cycle_nodes.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = parent_edges[current]
    for row in rows:
        if row.parsed.row_number in cycle_nodes:
            row.add_issue(issue("MDM_IMPORT_PARENT_CYCLE", Severity.ERROR, "Customer parent relation forms a cycle", row, "parent_customer_ref"))


def _sku(rows: list[EvaluatedRow], governance: BootstrapGovernanceRules | None):
    def raw_blank(row: EvaluatedRow, column: str) -> bool:
        value = row.parsed.raw_values.get(column)
        return value is None or (isinstance(value, str) and not value.strip())

    by_code: dict[str, list[EvaluatedRow]] = defaultdict(list)
    by_product: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        code = row.normalized.get("sku_code")
        product_code = row.normalized.get("source_product_code")
        if code:
            by_code[code].append(row)
        if product_code:
            by_product[product_code].append(row)
        else:
            row.add_issue(issue("MDM_IMPORT_PRODUCT_CANDIDATE", Severity.WARNING, "Product candidate code is missing", row, "source_product_code"))
        if raw_blank(row, "简称"):
            row.add_issue(issue("MDM_IMPORT_FIELD_MISSING_NONCRITICAL", Severity.INFO, "short_name is missing", row, "short_name"))
        if raw_blank(row, "箱规"):
            severity = Severity.WARNING if row.normalized.get("product_form") == "单品" else Severity.INFO
            code_name = "MDM_IMPORT_FIELD_MISSING_NONCRITICAL"
            row.add_issue(issue(code_name, severity, "case_pack is missing", row, "case_pack"))
        if raw_blank(row, "创建日期"):
            row.add_issue(issue("MDM_IMPORT_FIELD_MISSING_NONCRITICAL", Severity.INFO, "source_created_at is missing", row, "source_created_at"))

    for duplicate_rows in by_code.values():
        if len(duplicate_rows) < 2:
            continue
        candidates = [{"row_number": item.parsed.row_number, "sku_name": item.normalized.get("sku_name")} for item in duplicate_rows]
        for row in duplicate_rows:
            row.action = RowAction.CONFLICT
            row.add_issue(issue("MDM_IMPORT_DUPLICATE_UNIQUE_CODE", Severity.ERROR, "sku_code is duplicated", row, "sku_code", candidates))

    for product_code, members in by_product.items():
        variations = {
            field: sorted({row.normalized.get(field) for row in members if row.normalized.get(field) is not None})
            for field in ("product_form", "category_l1", "category_l2")
        }
        conflict_fields = [field for field, values in variations.items() if len(values) > 1]
        merge_confirmed = bool(
            conflict_fields and governance and governance.confirms_product_merge(product_code)
        )
        resolution = (
            {
                "decision": "MERGE_AS_ONE_PRODUCT",
                "scope": "BOOTSTRAP_ONLY",
                "governance_rule_id": governance.rule_id,
                "reason": "Business owner confirmed this specific historical Product group",
            }
            if merge_confirmed
            else None
        )
        candidate = {
            "source_product_code": product_code,
            "sku_count": len(members),
            "conflict": bool(conflict_fields) and not merge_confirmed,
            "detected_conflict": bool(conflict_fields),
            "conflict_fields": conflict_fields,
            "proposed_product_name": members[0].normalized.get("sku_name"),
            "resolution": resolution,
        }
        for row in members:
            row.product_candidate = candidate
            row.add_issue(issue("MDM_IMPORT_PRODUCT_CANDIDATE", Severity.WARNING, "SKU creates or joins a Product Candidate that requires confirmation", row, "source_product_code"))
            if merge_confirmed:
                row.add_issue(
                    issue(
                        "MDM_IMPORT_PRODUCT_MERGE_RESOLVED",
                        Severity.INFO,
                        "Detected Product conflict is resolved by the explicit bootstrap-only governance decision",
                        row,
                        "source_product_code",
                        resolved=True,
                    )
                )
            elif conflict_fields:
                row.action = RowAction.CONFLICT
                row.add_issue(issue("MDM_IMPORT_PRODUCT_MERGE_CONFLICT", Severity.ERROR, f"Product Candidate conflicts on: {', '.join(conflict_fields)}", row, "source_product_code"))


def _customer_candidate(candidate) -> dict[str, Any]:
    return {
        "entity_type": candidate.entity_type,
        "entity_id": candidate.entity_id,
        "stable_id": candidate.stable_id,
        "name": candidate.name,
        "active": candidate.active,
    }


def _customer_v1_identity(
    rows: list[EvaluatedRow], catalog: ReferenceCatalog
) -> None:
    """Globally unique Customer Code; an unambiguous exact identity is Existing."""
    keys = {}
    def code_key(code):
        if code not in keys:
            keys[code] = customer_code_key(catalog.session, code)
        return keys[code]

    master_by_code = defaultdict(list)
    for code, candidates in catalog.by_code[ImportType.CUSTOMER].items():
        master_by_code[code_key(code)].extend(candidates)
    by_code: dict[str, list[EvaluatedRow]] = defaultdict(list)
    by_name: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        code = row.normalized.get("customer_code")
        name = row.normalized.get("customer_name")
        if code:
            by_code[code_key(code)].append(row)
        if name:
            by_name[name].append(row)

        code_candidates = master_by_code.get(code_key(code), []) if code else []
        name_candidates = catalog.by_name[ImportType.CUSTOMER].get(name or "", [])
        exact = [
            candidate
            for candidate in code_candidates
            if normalize_text(candidate.values.get("customer_name")) == name
        ]
        if len(code_candidates) == 1 and len(exact) == 1:
            candidate = exact[0]
            source_findings = [item.as_dict() for item in row.issues]
            row.issues = []
            row.matched_entity = candidate.match(
                "EXACT_CUSTOMER_CODE_NAME",
                1,
                "exact normalized Customer code and name",
            )
            row.action = RowAction.EXISTING
            fields = (
                "organization",
                "department",
                "business_type",
                "market_type",
                "format_type",
                "channel_detail",
                "is_direct",
                "source_created_ym",
            )
            differences = {
                field: {
                    "source": row.normalized.get(field),
                    "master": candidate.values.get(field),
                }
                for field in fields
                if row.normalized.get(field) != candidate.values.get(field)
            }
            row.add_issue(
                issue(
                    "MDM_IMPORT_CUSTOMER_EXISTING",
                    Severity.INFO,
                    "Exact Customer code and name already exist; no Master update will occur",
                    row,
                    resolved=True,
                    details={
                        "target": _customer_candidate(candidate),
                        "differences": differences,
                        "source_findings": source_findings,
                    },
                )
            )
        else:
            code_conflicts = code_candidates
            name_conflicts = [
                candidate
                for candidate in name_candidates
                if normalize_text(candidate.values.get("customer_code")) != code
            ]
            if code_conflicts:
                row.action = RowAction.CONFLICT
                row.add_issue(
                    issue(
                        "MDM_IMPORT_CUSTOMER_CODE_CONFLICT",
                        Severity.ERROR,
                        "Customer code already exists with a different or ambiguous identity; CREATE is forbidden",
                        row,
                        "customer_code",
                        [_customer_candidate(item) for item in code_conflicts],
                    )
                )
            if name_conflicts:
                row.add_issue(
                    issue(
                        "MDM_IMPORT_CUSTOMER_NAME_CONFLICT",
                        Severity.WARNING,
                        "Customer name exists with a different Customer code",
                        row,
                        "customer_name",
                        [_customer_candidate(item) for item in name_conflicts],
                    )
                )

    for code, members in by_code.items():
        if len(members) < 2:
            continue
        candidates = [{"row_number": item.parsed.row_number} for item in members]
        same_name = len({item.normalized.get("customer_name") for item in members}) == 1
        for row in members:
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_DUPLICATE_EXACT" if same_name else "MDM_IMPORT_CUSTOMER_CODE_CONFLICT",
                    Severity.ERROR,
                    "Customer code is duplicated within the workbook; every occurrence is blocked",
                    row,
                    candidates=candidates,
                    details={"customer_code": row.normalized.get("customer_code")},
                )
            )

    for field, groups, other, rule_code, message in (
        (
            "customer_name",
            by_name,
            "customer_code",
            "MDM_IMPORT_CUSTOMER_NAME_CONFLICT",
            "Customer name appears with different codes within the workbook",
        ),
    ):
        for members in groups.values():
            if len({item.normalized.get(other) for item in members}) < 2:
                continue
            candidates = [
                {"row_number": item.parsed.row_number, other: item.normalized.get(other)}
                for item in members
            ]
            for row in members:
                if row.action != RowAction.EXISTING:
                    row.add_issue(
                        issue(rule_code, Severity.WARNING, message, row, field, candidates)
                    )


def _customer_v1_references(
    rows: list[EvaluatedRow], catalog: ReferenceCatalog, batch_id: str
) -> None:
    by_name: dict[str, list[EvaluatedRow]] = defaultdict(list)
    parent_edges: dict[int, int] = {}
    for row in rows:
        name = row.normalized.get("customer_name")
        if name:
            by_name[name].append(row)

    for row in rows:
        final_values = {
            key: value
            for key, value in row.normalized.items()
            if key
            not in {
                "channel_ref",
                "salesrep_ref",
                "region_ref",
                "province_ref",
                "parent_customer_ref",
            }
        }
        for key, field in (
            ("channel", "channel_ref"),
            ("salesrep", "salesrep_ref"),
            ("region", "region_ref"),
            ("province", "province_ref"),
        ):
            value = row.normalized.get(field)
            match, candidates = catalog.exact_customer_reference(key, value)
            row.references[key] = match
            if value and not match and row.action != RowAction.EXISTING:
                inactive = bool(candidates) and all(
                    not candidate.active for candidate in candidates
                )
                row.add_issue(
                    issue(
                        "MDM_IMPORT_INACTIVE_REFERENCE"
                        if inactive
                        else "MDM_IMPORT_REFERENCE_UNRESOLVED",
                        Severity.ERROR,
                        f"{key} Reference Master is inactive"
                        if inactive
                        else f"{key} Reference Master cannot be resolved exactly",
                        row,
                        field,
                        [_customer_candidate(item) for item in candidates],
                    )
                )
            final_values[f"{key}_stable_id"] = match.stable_id if match else None

        parent = row.normalized.get("parent_customer_ref")
        customer_name = row.normalized.get("customer_name")
        parent_match = None
        parent_resolution = "EMPTY"
        if parent == customer_name and parent:
            parent_resolution = "ROOT_SOURCE_SELF"
        elif parent and len(by_name.get(parent, [])) == 1:
            target = by_name[parent][0]
            parent_match = Match(
                "CUSTOMER",
                target.matched_entity.entity_id if target.matched_entity else None,
                target.matched_entity.stable_id if target.matched_entity else None,
                parent,
                "CURRENT_BATCH_EXACT_NAME",
                1,
                "unique normalized Customer name in current Batch",
                batch_id,
                target.parsed.row_number,
            )
            parent_resolution = "RESOLVED"
            if row.action != RowAction.EXISTING:
                parent_edges[row.parsed.row_number] = target.parsed.row_number
        elif parent:
            parent_match, candidates = catalog.exact_customer_reference(
                "parent_customer", parent
            )
            if parent_match:
                parent_resolution = "RESOLVED"
            elif row.action != RowAction.EXISTING:
                row.add_issue(
                    issue(
                        "MDM_IMPORT_PARENT_UNRESOLVED",
                        Severity.WARNING,
                        "Parent Customer cannot be resolved; acknowledged Review uses NULL",
                        row,
                        "parent_customer_ref",
                        [_customer_candidate(item) for item in candidates],
                    )
                )
                parent_resolution = "UNRESOLVED"
        row.references["parent_customer"] = parent_match
        final_values["parent_customer_stable_id"] = (
            parent_match.stable_id if parent_match else None
        )
        final_values["parent_resolution"] = parent_resolution
        row.final_values = final_values
        if row.action == RowAction.EXISTING and row.matched_entity:
            candidate = catalog.by_internal_id[ImportType.CUSTOMER].get(
                row.matched_entity.entity_id or -1
            )
            existing_finding = next(
                (
                    item
                    for item in row.issues
                    if item.code == "MDM_IMPORT_CUSTOMER_EXISTING"
                ),
                None,
            )
            if candidate and existing_finding and existing_finding.details is not None:
                reference_fields = {
                    "channel": ("channel_ref", "channel_id"),
                    "salesrep": ("salesrep_ref", "salesrep_id"),
                    "region": ("region_ref", "region_id"),
                    "province": ("province_ref", "province_id"),
                    "parent_customer": (
                        "parent_customer_ref",
                        "parent_customer_id",
                    ),
                }
                master_reference_details = {}
                source_reference_details = {}
                master_references = {}
                for key, (source_field, master_field) in reference_fields.items():
                    master_candidate = catalog.customer_reference_by_id[key].get(
                        candidate.values.get(master_field) or -1
                    )
                    stable_field = f"{key}_stable_id"
                    master_stable_id = (
                        master_candidate.stable_id if master_candidate else None
                    )
                    master_references[stable_field] = master_stable_id
                    source_reference_details[key] = {
                        "value": row.normalized.get(source_field),
                        "stable_id": final_values.get(stable_field),
                    }
                    master_reference_details[key] = {
                        "name": master_candidate.name if master_candidate else None,
                        "stable_id": master_stable_id,
                    }
                differences = existing_finding.details["differences"]
                for field, master_value in master_references.items():
                    if final_values.get(field) != master_value:
                        differences[field] = {
                            "source": final_values.get(field),
                            "master": master_value,
                        }
                existing_finding.details["source_snapshot"] = {
                    **dict(final_values),
                    "references": source_reference_details,
                }
                existing_finding.details["master_snapshot"] = {
                    **{
                        field: candidate.values.get(field)
                        for field in (
                            "customer_code",
                            "customer_name",
                            "organization",
                            "department",
                            "business_type",
                            "market_type",
                            "format_type",
                            "channel_detail",
                            "is_direct",
                            "source_created_ym",
                        )
                    },
                    **master_references,
                    "references": master_reference_details,
                    "stable_id": candidate.stable_id,
                    "status": "ACTIVE" if candidate.active else "INACTIVE",
                }

    _mark_parent_cycles(rows, parent_edges)


def evaluate_customer_v1(
    parsed: ParsedWorkbook, catalog: ReferenceCatalog, batch_id: str
) -> list[EvaluatedRow]:
    """Normalize and validate one Operational, CREATE-only Customer workbook."""
    if parsed.import_type != ImportType.CUSTOMER:
        raise ValueError("Customer Import V1 accepts Customer workbooks only")
    rows = _normalize(parsed, None)
    _customer_v1_identity(rows, catalog)
    _customer_v1_references(rows, catalog, batch_id)
    for row in rows:
        row.finalize()
    return rows


def _product_candidate(candidate) -> dict[str, Any]:
    return {
        "stable_id": candidate.stable_id,
        "product_code": candidate.values.get("product_code"),
        "product_name": candidate.values.get("product_name"),
        "brand": candidate.values.get("brand"),
        "status": "ACTIVE" if candidate.active else "INACTIVE",
    }


def _product_v1_identity(
    rows: list[EvaluatedRow], catalog: ReferenceCatalog
) -> None:
    """Apply the frozen CREATE-only Product identity rules."""
    by_code: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        code = row.normalized.get("product_code")
        name = row.normalized.get("product_name")
        code_key = code.casefold() if code else ""
        name_key = name.casefold() if name else ""
        if code:
            by_code[code_key].append(row)

        code_candidates = catalog.by_code[ImportType.PRODUCT].get(code_key, [])
        name_candidates = catalog.by_name[ImportType.PRODUCT].get(name_key, [])
        if code_candidates:
            exact = [
                candidate
                for candidate in code_candidates
                if (
                    normalize_text(candidate.values.get("product_name")) or ""
                ).casefold() == name_key
            ]
            if len(exact) == 1:
                candidate = exact[0]
                row.matched_entity = candidate.match(
                    "EXACT_PRODUCT_CODE_NAME",
                    1,
                    "exact normalized Product code and name",
                )
                row.action = RowAction.EXISTING
                differences = {
                    "brand": {
                        "source": row.normalized.get("brand"),
                        "master": candidate.values.get("brand"),
                    }
                } if row.normalized.get("brand") != candidate.values.get("brand") else {}
                row.add_issue(
                    issue(
                        "MDM_IMPORT_PRODUCT_EXISTING",
                        Severity.INFO,
                        "Exact Product code and name already exist; no Master update will occur",
                        row,
                        resolved=True,
                        details={
                            "target": _product_candidate(candidate),
                            "differences": differences,
                        },
                    )
                )
            else:
                row.action = RowAction.CONFLICT
                row.add_issue(
                    issue(
                        "MDM_IMPORT_PRODUCT_CODE_CONFLICT",
                        Severity.ERROR,
                        "Product Code exists with a different Product name",
                        row,
                        "product_code",
                        [_product_candidate(item) for item in code_candidates],
                    )
                )
        elif name_candidates:
            row.add_issue(
                issue(
                    "MDM_IMPORT_PRODUCT_NAME_CONFLICT",
                    Severity.WARNING,
                    "Product name exists with a different Product Code",
                    row,
                    "product_name",
                    [_product_candidate(item) for item in name_candidates],
                )
            )

        row.final_values = dict(row.normalized)

    for members in by_code.values():
        if len(members) < 2:
            continue
        candidates = [
            {
                "row_number": item.parsed.row_number,
                "product_name": item.normalized.get("product_name"),
            }
            for item in members
        ]
        for row in members:
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE",
                    Severity.ERROR,
                    "Product Code is duplicated within the workbook",
                    row,
                    "product_code",
                    candidates,
                )
            )


def evaluate_product_v1(
    parsed: ParsedWorkbook, catalog: ReferenceCatalog, batch_id: str
) -> list[EvaluatedRow]:
    """Normalize and validate one Operational, CREATE-only Product workbook."""
    if parsed.import_type != ImportType.PRODUCT:
        raise ValueError("Product Import V1 accepts Product workbooks only")
    rows = _normalize(parsed, None)
    _product_v1_identity(rows, catalog)
    for row in rows:
        row.finalize()
    return rows


# ---------------------------------------------------------------------------
# SKU Import V1
# ---------------------------------------------------------------------------

# Optional SKU fields compared for the EXISTING difference snapshot (V1
# template columns other than the three required ones).
SKU_V1_OPTIONAL_FIELDS = (
    "product_group",
    "product_form",
    "origin",
    "category_l1",
    "category_l2",
    "category_l3",
    "category_l4",
    "short_name",
    "category_extra",
    "case_pack",
    "source_created_at",
)


def _value_key(value: Any) -> str:
    """Comparison key tolerant of Decimal/str and casing (MySQL case-insensitive)."""
    if value is None:
        return ""
    return str(value).strip().casefold()


def _sku_candidate(candidate) -> dict[str, Any]:
    values = candidate.values or {}
    return {
        "stable_id": candidate.stable_id,
        "sku_code": values.get("sku_code"),
        "sku_name": values.get("sku_name"),
        "product_id": values.get("product_id"),
        "status": "ACTIVE" if candidate.active else "INACTIVE",
    }


def _product_ref_candidate(candidate) -> dict[str, Any]:
    values = candidate.values or {}
    return {
        "stable_id": candidate.stable_id,
        "product_code": values.get("product_code"),
        "product_name": values.get("product_name"),
        "status": "ACTIVE" if candidate.active else "INACTIVE",
    }


def _sku_v1_identity(
    rows: list[EvaluatedRow], catalog: ReferenceCatalog
) -> None:
    """Apply the frozen SKU Import V1 identity and reference rules.

    - Product reference resolves only against the formal Product Master
      (committed data), so an uncommitted Product Import Batch can never be a
      SKU reference (rule 7 holds structurally).
    - Master SKU identity is case-insensitive (mirrors MySQL unique sku_code).
    """
    master_by_code = {
        str(key).casefold(): candidates
        for key, candidates in catalog.by_code[ImportType.SKU].items()
    }
    by_code: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        sku_code = row.normalized.get("sku_code")
        sku_name = row.normalized.get("sku_name")
        product_ref = row.normalized.get("product_code_ref")
        code_key = sku_code.casefold() if sku_code else ""
        name_key = sku_name.casefold() if sku_name else ""
        if sku_code:
            by_code[code_key].append(row)

        product_match = None
        if product_ref:
            candidate, candidates = catalog.identity(
                ImportType.PRODUCT,
                {"product_code": product_ref},
                ImportMode.OPERATIONAL,
            )
            if candidate is None:
                row.add_issue(
                    issue(
                        "MDM_IMPORT_REFERENCE_UNRESOLVED",
                        Severity.ERROR,
                        "所属 Product Code 未在主数据中找到",
                        row,
                        "product_code_ref",
                        [_product_ref_candidate(item) for item in candidates],
                    )
                )
            elif not candidate.active:
                row.add_issue(
                    issue(
                        "MDM_IMPORT_INACTIVE_REFERENCE",
                        Severity.ERROR,
                        "所属 Product 已停用",
                        row,
                        "product_code_ref",
                        [_product_ref_candidate(candidate)],
                    )
                )
            else:
                row.references["product"] = candidate.match(
                    "PRODUCT_CODE_REF",
                    1,
                    "exact active Product by product_code",
                )
                product_match = candidate

        # MySQL enforces a case-insensitive unique sku_code, so a casefolded
        # code can resolve to at most one Master SKU in production.
        masters = master_by_code.get(code_key, []) if code_key else []
        master = masters[0] if len(masters) == 1 else None
        if master is not None and product_match is not None:
            master_values = master.values or {}
            master_name = str(master_values.get("sku_name") or "").casefold()
            master_product_id = master_values.get("product_id")
            if master_product_id != product_match.entity_id:
                row.action = RowAction.CONFLICT
                row.add_issue(
                    issue(
                        "MDM_IMPORT_SKU_PRODUCT_CONFLICT",
                        Severity.ERROR,
                        "SKU Code 已归属其他 Product，禁止隐式调整归属",
                        row,
                        "product_code_ref",
                        [_sku_candidate(master)],
                    )
                )
            elif name_key != master_name:
                row.action = RowAction.CONFLICT
                row.add_issue(
                    issue(
                        "MDM_IMPORT_SKU_CODE_CONFLICT",
                        Severity.ERROR,
                        "SKU Code 已存在但名称不同",
                        row,
                        "sku_name",
                        [_sku_candidate(master)],
                    )
                )
            else:
                row.matched_entity = master.match(
                    "EXACT_SKU_CODE_NAME_PRODUCT",
                    1,
                    "exact normalized SKU code, name and Product",
                )
                row.action = RowAction.EXISTING
                differences = {
                    field: {
                        "source": row.normalized.get(field),
                        "master": master_values.get(field),
                    }
                    for field in SKU_V1_OPTIONAL_FIELDS
                    if _value_key(row.normalized.get(field))
                    != _value_key(master_values.get(field))
                }
                row.add_issue(
                    issue(
                        "MDM_IMPORT_SKU_EXISTING",
                        Severity.INFO,
                        "Exact SKU already exists; no Master update will occur",
                        row,
                        resolved=True,
                        details={
                            "target": _sku_candidate(master),
                            "differences": differences,
                        },
                    )
                )

        row.final_values = dict(row.normalized)

    for members in by_code.values():
        if len(members) < 2:
            continue
        candidates = [
            {
                "row_number": item.parsed.row_number,
                "sku_name": item.normalized.get("sku_name"),
            }
            for item in members
        ]
        for row in members:
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE",
                    Severity.ERROR,
                    "SKU Code is duplicated within the workbook",
                    row,
                    "sku_code",
                    candidates,
                )
            )


def evaluate_sku_v1(
    parsed: ParsedWorkbook, catalog: ReferenceCatalog, batch_id: str
) -> list[EvaluatedRow]:
    """Normalize and validate one Operational, CREATE-only SKU workbook.

    Only the frozen SKU Import V1 template (SKU导入 sheet) is accepted; a
    Bootstrap 物料列表 workbook raised here becomes a file-level rejection
    (no batch is created), mirroring Product Import behaviour.
    """
    if parsed.import_type != ImportType.SKU:
        raise ValueError("SKU Import V1 accepts SKU workbooks only")
    if parsed.adapter is not SKU_V1_ADAPTER:
        raise ImportFileError(f"Required sheet is missing: {SKU_V1_ADAPTER.sheet}")
    rows = _normalize(parsed, None)
    _upgrade_invalid_dates(rows)
    _sku_v1_identity(rows, catalog)
    for row in rows:
        row.finalize()
    return rows


# Frozen business decision (Product/SKU Final Gate blocker fix): an unparseable
# optional 创建日期 in SKU Import V1 is a blocking ERROR, never an
# acknowledgeable Warning, and never silently written as NULL. Empty and valid
# values are untouched.
SKU_V1_DATE_INVALID_COPY = "创建日期格式无法识别，请修改 Excel 后重新上传"


def _upgrade_invalid_dates(rows: list[EvaluatedRow]) -> None:
    """Upgrade shared MDM_IMPORT_DATE_INVALID Warnings to blocking Errors for
    the SKU Import V1 flow only (the V1 template has a single date field)."""
    for row in rows:
        for index, item in enumerate(row.issues):
            if item.code == "MDM_IMPORT_DATE_INVALID":
                row.issues[index] = Issue(
                    code=item.code,
                    severity=Severity.ERROR,
                    message=SKU_V1_DATE_INVALID_COPY,
                    field=item.field,
                    row_number=item.row_number,
                    candidates=item.candidates,
                    resolved=item.resolved,
                    details=item.details,
                )


def evaluate_workbook(
    parsed: ParsedWorkbook,
    catalog: ReferenceCatalog,
    mode: ImportMode,
    batch_id: str,
    governance: BootstrapGovernanceRules | None = None,
) -> list[EvaluatedRow]:
    selected_governance = (
        governance or BOOTSTRAP_GOVERNANCE_RULES
        if mode == ImportMode.BOOTSTRAP
        else None
    )
    rows = _normalize(parsed, selected_governance)
    _mark_exact_duplicates(rows)
    _match_identity(rows, parsed.import_type, catalog, mode)
    if parsed.import_type == ImportType.CHANNEL:
        _channel(rows, mode)
    elif parsed.import_type == ImportType.SALESREP:
        _salesrep(rows)
    elif parsed.import_type == ImportType.CUSTOMER:
        _customer_duplicates(rows)
        _customer_references(rows, catalog, mode, batch_id, selected_governance)
    elif parsed.import_type == ImportType.SKU:
        _sku(rows, selected_governance)
    for row in rows:
        row.finalize()
    return rows
