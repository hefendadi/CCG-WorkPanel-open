"""商品导入 V2 (Operational): one 商品表 maintaining Product + SKU + binding.

Frozen rules (design doc docs/import-contracts.md):

- Every Excel row is one SKU; rows are grouped by normalized Product Code
  (合并编码, case-insensitive) and every row carries its group's Product
  plan in `product_candidate`:
    action=CREATE   -> the Product does not exist; it is created by this batch
    action=EXISTING -> the Product exists (referenced only, never rewritten)
    conflict=True   -> the group cannot produce one coherent Product
- Group conflicts: differing non-null 产品形式 / 一级 / 二级 across a CREATE
  group (bootstrap semantics) and differing non-null explicit Product 名称 /
  品牌 block the whole group (MDM_IMPORT_PRODUCT_ATTR_CONFLICT). No auto
  merge, no fuzzy matching.
- Existing SKU identity mirrors SKU Import V1 exactly (code/name/product
  conflicts stay blocking; exact matches are EXISTING and never overwritten).
- Creation dates follow the documented public adapter date format.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from webapp.mdm.import_engine.governance import BootstrapGovernanceRules
from webapp.mdm.import_engine.matching import ReferenceCatalog

from .domain import (
    EvaluatedRow,
    ImportType,
    Issue,
    ParsedWorkbook,
    RowAction,
    Severity,
)
from .normalize import normalize_text
from .validation import (
    SKU_V1_DATE_INVALID_COPY,
    SKU_V1_OPTIONAL_FIELDS,
    _normalize,
    _product_candidate,
    _sku_candidate,
    _value_key,
    issue,
)

# Product attribute fields whose within-group variation blocks a NEW Product
# candidate (frozen bootstrap semantic; NO governance whitelist in V2).
PRODUCT_CONFLICT_FIELDS = ("product_form", "category_l1", "category_l2")

PRODUCT_ATTR_CONFLICT_COPY = (
    "合并编码对应的 Product 属性存在冲突，无法在本批创建该 Product，请修改 Excel 后重新上传"
)
PRODUCT_NAME_REQUIRED_COPY = (
    "合并编码对应 Product 尚不存在，且缺少显式 Product 名称与 SKU Code == Product Code 的代表行，"
    "无法确定 Product 名称，请修改 Excel 后重新上传"
)
INACTIVE_PRODUCT_COPY = "合并编码对应 Product 已停用"
SKU_PRODUCT_COLLISION_COPY = "SKU Code 已归属其他 Product，禁止隐式调整归属"
SKU_NAME_CONFLICT_COPY = "SKU Code 已存在但名称不同"
SKU_NAME_FORMAT_DIFFERENCE_COPY = "SKU 名称仅存在确定性格式差异（NFKC/首尾与连续空白），按同名处理，Master 保持不变"
PRODUCT_REF_AMBIGUOUS_COPY = "合并编码无法唯一解析到 Product"
DATE_LABEL_COPY = "创建日期必须符合公开 adapter 日期格式"


def _sku_name_key(value) -> str:
    """Deterministic identity text key for SKU name comparison.

    NFKC + trim + collapse every (incl. full-width) whitespace run into one
    half-width space, then casefold. Only deterministic format differences are
    ignored; fuzzy matching is never applied and Master text is never edited.
    """
    normalized = normalize_text(value)
    return (normalized or "").casefold()


def _goods_sku_candidates(candidates) -> list[dict[str, Any]]:
    return [_sku_candidate(item) for item in candidates]


def evaluate_product_sku_v1(
    parsed: ParsedWorkbook, catalog: ReferenceCatalog, batch_id: str
) -> list[EvaluatedRow]:
    """Normalize and validate one Operational 商品导入 workbook."""
    if parsed.import_type != ImportType.PRODUCT_SKU:
        raise ValueError("商品导入 V2 accepts PRODUCT_SKU workbooks only")
    rows = _normalize(parsed, None)
    _goods_date_cleanup(rows)
    _resolve_product_groups(rows, catalog)
    _goods_sku_identity(rows, catalog)
    _goods_sku_duplicates(rows)
    for row in rows:
        row.final_values = dict(row.normalized)
        row.finalize()
    return rows


def _goods_date_cleanup(rows: list[EvaluatedRow]) -> None:
    """创建日期 month/business labels -> empty (INFO); other invalid dates stay
    blocking ERRORs (SKU V1 frozen decision); the parser is never widened."""
    for row in rows:
        raw_value = row.parsed.raw_values.get("创建日期")
        is_label = BootstrapGovernanceRules.is_source_created_at_label(raw_value)
        removed: list[int] = []
        for index, item in enumerate(row.issues):
            if item.code != "MDM_IMPORT_DATE_INVALID":
                continue
            if is_label:
                removed.append(index)
            else:
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
        for index in reversed(removed):
            row.issues.pop(index)
        if is_label:
            row.add_issue(
                issue(
                    "MDM_IMPORT_SOURCE_DATE_LABEL",
                    Severity.INFO,
                    DATE_LABEL_COPY,
                    row,
                    "source_created_at",
                )
            )


def _goods_create_candidate(
    code: str, members: list[EvaluatedRow]
) -> dict[str, Any]:
    """One coherent CREATE Product plan for a group, or a conflict report.

    New-Product naming (frozen, deterministic, order-independent):
      1. unique explicit Product 名称 column value wins;
      2. otherwise the unique representative SKU row (SKU Code == Product Code)
         provides the name from 物料名称;
      3. neither present -> the group cannot create a Product: Blocking Error
         (MDM_IMPORT_PRODUCT_NAME_REQUIRED); the first-row fallback is gone.
    """
    semantic_conflicts = []
    for field in PRODUCT_CONFLICT_FIELDS:
        values = {
            row.normalized.get(field)
            for row in members
            if row.normalized.get(field) is not None
        }
        if len(values) > 1:
            semantic_conflicts.append(field)
    declared_names = {
        row.normalized.get("product_name")
        for row in members
        if row.normalized.get("product_name") is not None
    }
    declared_brands = {
        row.normalized.get("brand")
        for row in members
        if row.normalized.get("brand") is not None
    }
    explicit_conflicts = []
    if len(declared_names) > 1:
        explicit_conflicts.append("product_name")
    if len(declared_brands) > 1:
        explicit_conflicts.append("brand")
    conflict_fields = semantic_conflicts + explicit_conflicts
    declared_name = next(iter(declared_names)) if len(declared_names) == 1 else None
    declared_brand = next(iter(declared_brands)) if len(declared_brands) == 1 else None
    if conflict_fields:
        # Contradictory Product attributes already block the whole group; do
        # not add a second (redundant) naming issue on top.
        return {
            "product_code": code,
            "action": "CREATE",
            "conflict": True,
            "conflict_fields": conflict_fields,
            "declared_product_name": declared_name,
            "declared_brand": declared_brand,
            "product_name": None,
            "brand": None,
            "representative_row": None,
            "row_count": len(members),
        }
    code_key = code.casefold()
    representatives = [
        row
        for row in members
        if (row.normalized.get("sku_code") or "").casefold() == code_key
    ]
    representative = representatives[0] if len(representatives) == 1 else None
    if declared_name is not None:
        product_name = declared_name
    elif representative is not None:
        product_name = representative.normalized.get("sku_name")
    else:
        # No explicit Product 名称 and no unique SKU Code == Product Code row:
        # Blocking — do not fall back to the group's first row.
        return {
            "product_code": code,
            "action": "CREATE",
            "conflict": True,
            "name_missing": True,
            "conflict_fields": [],
            "declared_product_name": None,
            "declared_brand": declared_brand,
            "product_name": None,
            "brand": declared_brand,
            "representative_row": None,
            "row_count": len(members),
        }
    return {
        "product_code": code,
        "action": "CREATE",
        "conflict": False,
        "conflict_fields": [],
        "declared_product_name": declared_name,
        "declared_brand": declared_brand,
        "product_name": product_name,
        "brand": declared_brand,
        "representative_row": representative.parsed.row_number
        if representative is not None
        else None,
        "row_count": len(members),
    }


def _resolve_product_groups(
    rows: list[EvaluatedRow], catalog: ReferenceCatalog
) -> None:
    """Deduplicate the SKU rows by Product Code and plan every group."""
    master_by_code = catalog.by_code[ImportType.PRODUCT]
    groups: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        code = row.normalized.get("product_code")
        if code:
            groups[code.casefold()].append(row)
    for code_key, members in groups.items():
        code = members[0].normalized["product_code"]
        candidates = master_by_code.get(code_key, [])
        if len(candidates) > 1:
            for row in members:
                row.action = RowAction.CONFLICT
                row.add_issue(
                    issue(
                        "MDM_IMPORT_REFERENCE_UNRESOLVED",
                        Severity.ERROR,
                        PRODUCT_REF_AMBIGUOUS_COPY,
                        row,
                        "product_code",
                        [_product_candidate(item) for item in candidates],
                    )
                )
            continue
        if not candidates:
            plan = _goods_create_candidate(code, members)
            for row in members:
                row.product_candidate = plan
                if not plan["conflict"]:
                    continue
                row.action = RowAction.CONFLICT
                if plan.get("name_missing"):
                    row.add_issue(
                        issue(
                            "MDM_IMPORT_PRODUCT_NAME_REQUIRED",
                            Severity.ERROR,
                            PRODUCT_NAME_REQUIRED_COPY,
                            row,
                            "product_name",
                            details={
                                "product_code": code,
                                "representative_row": plan["representative_row"],
                                "declared_product_name": plan["declared_product_name"],
                            },
                        )
                    )
                else:
                    row.add_issue(
                        issue(
                            "MDM_IMPORT_PRODUCT_ATTR_CONFLICT",
                            Severity.ERROR,
                            PRODUCT_ATTR_CONFLICT_COPY,
                            row,
                            "product_code",
                            details={
                                "product_code": code,
                                "conflict_fields": plan["conflict_fields"],
                                "representative_row": plan["representative_row"],
                            },
                        )
                    )
            continue
        master = candidates[0]
        plan = {
            "product_code": code,
            "action": "EXISTING",
            "conflict": False,
            "product_stable_id": master.stable_id,
            "product_entity_id": master.entity_id,
            "product_name": master.values.get("product_name"),
            "brand": master.values.get("brand"),
            "inactive": not master.active,
            "row_count": len(members),
        }
        for row in members:
            row.product_candidate = plan
            if not master.active:
                row.action = RowAction.CONFLICT
                row.add_issue(
                    issue(
                        "MDM_IMPORT_INACTIVE_REFERENCE",
                        Severity.ERROR,
                        INACTIVE_PRODUCT_COPY,
                        row,
                        "product_code",
                        [_product_candidate(master)],
                    )
                )


def _goods_sku_identity(
    rows: list[EvaluatedRow], catalog: ReferenceCatalog
) -> None:
    """Existing SKU rules mirror SKU Import V1 (no silent overwrite)."""
    master_skus: dict[str, list] = {
        str(key).casefold(): candidates
        for key, candidates in catalog.by_code[ImportType.SKU].items()
    }
    for row in rows:
        sku_code = row.normalized.get("sku_code")
        plan = row.product_candidate
        if not sku_code or not plan or plan.get("inactive"):
            continue
        code_key = sku_code.casefold()
        name_key = _sku_name_key(row.normalized.get("sku_name"))
        masters = master_skus.get(code_key, [])
        master = masters[0] if len(masters) == 1 else None
        if master is None:
            continue
        master_values = master.values or {}
        master_name = str(master_values.get("sku_name") or "")
        if plan["action"] == "CREATE":
            # The code is occupied by a Master SKU that belongs to another
            # Product (or none) while this batch would create its Product.
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_SKU_PRODUCT_CONFLICT",
                    Severity.ERROR,
                    SKU_PRODUCT_COLLISION_COPY,
                    row,
                    "product_code",
                    [_sku_candidate(master)],
                )
            )
            continue
        product_entity_id = plan.get("product_entity_id")
        master_product_id = master_values.get("product_id")
        if master_product_id != product_entity_id:
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_SKU_PRODUCT_CONFLICT",
                    Severity.ERROR,
                    SKU_PRODUCT_COLLISION_COPY,
                    row,
                    "product_code",
                    [_sku_candidate(master)],
                )
            )
        elif name_key != _sku_name_key(master_name):
            row.action = RowAction.CONFLICT
            row.add_issue(
                issue(
                    "MDM_IMPORT_SKU_CODE_CONFLICT",
                    Severity.ERROR,
                    SKU_NAME_CONFLICT_COPY,
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
            source_raw = str(row.parsed.raw_values.get("物料名称") or "")
            if source_raw != str(master_name or ""):
                row.add_issue(
                    issue(
                        "MDM_IMPORT_SKU_NAME_FORMAT_DIFFERENCE",
                        Severity.INFO,
                        SKU_NAME_FORMAT_DIFFERENCE_COPY,
                        row,
                        "sku_name",
                        details={
                            "source_sku_name": source_raw,
                            "master_sku_name": master_name,
                        },
                    )
                )
            differences = {
                field: {
                    "source": row.normalized.get(field),
                    "master": master_values.get(field),
                }
                for field in SKU_V1_OPTIONAL_FIELDS
                if _value_key(row.normalized.get(field))
                != _value_key(master_values.get(field))
            }
            declared_name = row.normalized.get("product_name")
            if _value_key(declared_name) and _value_key(declared_name) != _value_key(
                plan.get("product_name")
            ):
                differences["product_name"] = {
                    "source": declared_name,
                    "master": plan.get("product_name"),
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


def _goods_sku_duplicates(rows: list[EvaluatedRow]) -> None:
    by_code: dict[str, list[EvaluatedRow]] = defaultdict(list)
    for row in rows:
        sku_code = row.normalized.get("sku_code")
        if sku_code:
            by_code[sku_code.casefold()].append(row)
    for members in by_code.values():
        if len(members) < 2:
            continue
        candidates = [
            {"row_number": item.parsed.row_number, "sku_name": item.normalized.get("sku_name")}
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
