"""Domain contracts for the public edition import pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class ImportType(str, Enum):
    CHANNEL = "CHANNEL"
    SALESREP = "SALESREP"
    CUSTOMER = "CUSTOMER"
    PRODUCT = "PRODUCT"
    SKU = "SKU"
    # 商品导入 V2: one 商品表 upload maintaining Product + SKU + binding.
    PRODUCT_SKU = "PRODUCT_SKU"


class ImportMode(str, Enum):
    BOOTSTRAP = "BOOTSTRAP"
    OPERATIONAL = "OPERATIONAL"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class RowStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    EXISTING = "EXISTING"


class RowAction(str, Enum):
    NEW = "NEW"
    UPDATE = "UPDATE"
    UNCHANGED = "UNCHANGED"
    EXISTING = "EXISTING"
    CONFLICT = "CONFLICT"
    UNIMPORTABLE = "UNIMPORTABLE"


ISSUE_CODES = {
    "MDM_IMPORT_FILE_INVALID",
    "MDM_IMPORT_SHEET_INVALID",
    "MDM_IMPORT_REQUIRED_FIELD_MISSING",
    "MDM_IMPORT_VALUE_INVALID",
    "MDM_IMPORT_DATE_INVALID",
    "MDM_IMPORT_SOURCE_DATE_LABEL",
    "MDM_IMPORT_CODE_NOT_STRING_SAFE",
    "MDM_IMPORT_DUPLICATE_EXACT",
    "MDM_IMPORT_DUPLICATE_CUSTOMER_CODE",
    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE",
    "MDM_IMPORT_POSSIBLE_DUPLICATE_NAME",
    "MDM_IMPORT_CHANNEL_UNRESOLVED",
    "MDM_IMPORT_CHANNEL_CONFLICT",
    "MDM_IMPORT_CHANNEL_BOOTSTRAP_REQUIRED",
    "MDM_IMPORT_HISTORICAL_CHANNEL",
    "MDM_IMPORT_NON_SALES_EXCLUDED",
    "MDM_IMPORT_SALESREP_UNRESOLVED",
    "MDM_IMPORT_SALESREP_AMBIGUOUS",
    "MDM_IMPORT_INACTIVE_REFERENCE",
    "MDM_IMPORT_REGION_UNRESOLVED",
    "MDM_IMPORT_PROVINCE_UNRESOLVED",
    "MDM_IMPORT_PARENT_UNRESOLVED",
    "MDM_IMPORT_PARENT_SELF",
    "MDM_IMPORT_PARENT_CYCLE",
    "MDM_IMPORT_PRODUCT_CANDIDATE",
    "MDM_IMPORT_PRODUCT_MERGE_CONFLICT",
    "MDM_IMPORT_PRODUCT_MERGE_RESOLVED",
    "MDM_IMPORT_SKU_PRODUCT_CONFLICT",
    "MDM_IMPORT_FIELD_MISSING_NONCRITICAL",
    "MDM_IMPORT_CUSTOMER_EXISTING",
    "MDM_IMPORT_CUSTOMER_CODE_CONFLICT",
    "MDM_IMPORT_CUSTOMER_NAME_CONFLICT",
    "MDM_IMPORT_PRODUCT_EXISTING",
    "MDM_IMPORT_PRODUCT_CODE_CONFLICT",
    "MDM_IMPORT_PRODUCT_NAME_CONFLICT",
    "MDM_IMPORT_SKU_EXISTING",
    "MDM_IMPORT_SKU_CODE_CONFLICT",
    "MDM_IMPORT_REFERENCE_UNRESOLVED",
    # 商品导入 V2: one source 商品表 row group (same Product Code) contradicts
    # the Product-level attributes required to create one coherent Product.
    "MDM_IMPORT_PRODUCT_ATTR_CONFLICT",
    # 商品导入 V2: a NEW Product needs an explicit Product 名称 or a unique
    # SKU Code == Product Code representative row; otherwise it is blocked.
    "MDM_IMPORT_PRODUCT_NAME_REQUIRED",
    # 商品导入 V2: Existing SKU name matches after deterministic identity
    # normalization (NFKC + whitespace folding); raw strings differ in format
    # only, so the row is Existing and the Master is left untouched.
    "MDM_IMPORT_SKU_NAME_FORMAT_DIFFERENCE",
}


@dataclass(frozen=True)
class Adapter:
    import_type: ImportType
    sheet: str
    columns: dict[str, str]
    field_kinds: dict[str, str]
    required_fields: tuple[str, ...]

    @property
    def required_columns(self) -> tuple[str, ...]:
        return tuple(self.columns)


ADAPTERS = {
    ImportType.CHANNEL: Adapter(
        ImportType.CHANNEL,
        "渠道属性",
        {"索引": "source_index", "渠道": "channel_name"},
        {"source_index": "code", "channel_name": "text"},
        ("channel_name",),
    ),
    ImportType.SALESREP: Adapter(
        ImportType.SALESREP,
        "Sheet1",
        {"销售员": "salesrep_name"},
        {"salesrep_name": "text"},
        ("salesrep_name",),
    ),
    ImportType.CUSTOMER: Adapter(
        ImportType.CUSTOMER,
        "Customers",
        {
            "客户编码": "customer_code",
            "客户名称": "customer_name",
            "组织": "organization",
            "部门": "department",
            "Business Type": "business_type",
            "Market Type": "market_type",
            "渠道": "channel_ref",
            "销售地区": "region_ref",
            "省份": "province_ref",
            "业态": "format_type",
            "渠道明细": "channel_detail",
            "销售员": "salesrep_ref",
            "是否直营": "is_direct",
            "上级客户": "parent_customer_ref",
            "创建年月": "source_created_ym",
        },
        {
            "customer_code": "code",
            "customer_name": "text",
            "organization": "text",
            "department": "text",
            "business_type": "text",
            "market_type": "text",
            "channel_ref": "text",
            "region_ref": "text",
            "province_ref": "text",
            "format_type": "text",
            "channel_detail": "text",
            "salesrep_ref": "text",
            "is_direct": "boolean",
            "parent_customer_ref": "text",
            "source_created_ym": "code",
        },
        ("customer_code", "customer_name", "channel_ref", "salesrep_ref"),
    ),
    ImportType.PRODUCT: Adapter(
        ImportType.PRODUCT,
        "Product导入",
        {
            "Product Code": "product_code",
            "Product 名称": "product_name",
            "品牌": "brand",
        },
        {
            "product_code": "code",
            "product_name": "text",
            "brand": "text",
        },
        ("product_code", "product_name"),
    ),
    ImportType.SKU: Adapter(
        ImportType.SKU,
        "物料列表",
        {
            "商品编码": "sku_code",
            "物料名称": "sku_name",
            "ERP分组": "product_group",
            "产品形式": "product_form",
            "产地": "origin",
            "产品一级分类（大类）": "category_l1",
            "产品二级分类（品类）": "category_l2",
            "产品三级分类（规格）": "category_l3",
            "产品四级分类（克数）": "category_l4",
            "简称": "short_name",
            "扩展分类": "category_extra",
            "箱规": "case_pack",
            "合并编码": "source_product_code",
            "创建日期": "source_created_at",
        },
        {
            "sku_code": "code",
            "sku_name": "text",
            "product_group": "text",
            "product_form": "text",
            "origin": "text",
            "category_l1": "text",
            "category_l2": "text",
            "category_l3": "text",
            "category_l4": "text",
            "short_name": "text",
            "category_extra": "text",
            "case_pack": "decimal",
            "source_product_code": "code",
            "source_created_at": "date",
        },
        ("sku_code", "sku_name"),
    ),
}

# ---------------------------------------------------------------------------
# SKU Import V1 (Operational) frozen template.
#
# Separate from the Bootstrap 物料列表 adapter: the V1 workbook uses a
# different sheet and required columns (SKU Code / SKU 名称 / 所属 Product
# Code); every other existing SKU field is optional (有值则导入，无值不阻塞).
# The adapter rides on ParsedWorkbook so normalization/validation use the same
# column mapping that parsed the file. entity_type stays SKU.
# ---------------------------------------------------------------------------

SKU_V1_SHEET = "SKU导入"
SKU_V1_ADAPTER = Adapter(
    ImportType.SKU,
    SKU_V1_SHEET,
    {
        "SKU Code": "sku_code",
        "SKU 名称": "sku_name",
        "所属 Product Code": "product_code_ref",
        "ERP分组": "product_group",
        "产品形式": "product_form",
        "产地": "origin",
        "产品一级分类（大类）": "category_l1",
        "产品二级分类（品类）": "category_l2",
        "产品三级分类（规格）": "category_l3",
        "产品四级分类（克数）": "category_l4",
        "简称": "short_name",
        "扩展分类": "category_extra",
        "箱规": "case_pack",
        "创建日期": "source_created_at",
    },
    {
        "sku_code": "code",
        "sku_name": "text",
        "product_code_ref": "text",
        "product_group": "text",
        "product_form": "text",
        "origin": "text",
        "category_l1": "text",
        "category_l2": "text",
        "category_l3": "text",
        "category_l4": "text",
        "short_name": "text",
        "category_extra": "text",
        "case_pack": "decimal",
        "source_created_at": "date",
    },
    ("sku_code", "sku_name", "product_code_ref"),
)

# ---------------------------------------------------------------------------
# 商品导入 V2 (Operational) frozen template.
#
# One 商品表 upload maintains Product + SKU + SKU->Product binding. The frozen
# template sheet is 商品导入; the legacy business sheet 物料列表 (identical 14
# required columns, no explicit Product attribute columns) is accepted too so a
# business 商品表 can be uploaded as-is. Column mapping mirrors SKU Import V1
# with 商品编码/物料名称/合并编码 playing the SKU Code/SKU 名称/Product Code
# roles; Product 名称 and 品牌 are OPTIONAL explicit Product attribute columns
# (ignored when absent from the workbook). entity_type stays PRODUCT_SKU.
# ---------------------------------------------------------------------------

GOODS_V1_SHEET = "商品导入"
GOODS_V1_LEGACY_SHEET = "物料列表"
GOODS_V1_OPTIONAL_COLUMNS = ("Product 名称", "品牌")
GOODS_V1_COLUMN_FIELDS = {
    "商品编码": "sku_code",
    "物料名称": "sku_name",
    "ERP分组": "product_group",
    "产品形式": "product_form",
    "产地": "origin",
    "产品一级分类（大类）": "category_l1",
    "产品二级分类（品类）": "category_l2",
    "产品三级分类（规格）": "category_l3",
    "产品四级分类（克数）": "category_l4",
    "简称": "short_name",
    "扩展分类": "category_extra",
    "箱规": "case_pack",
    "合并编码": "product_code",
    "创建日期": "source_created_at",
}
GOODS_V1_REQUIRED_COLUMNS = tuple(GOODS_V1_COLUMN_FIELDS)
GOODS_V1_ADAPTER = Adapter(
    ImportType.PRODUCT_SKU,
    GOODS_V1_SHEET,
    {
        **GOODS_V1_COLUMN_FIELDS,
        "Product 名称": "product_name",
        "品牌": "brand",
    },
    {
        "sku_code": "code",
        "sku_name": "text",
        "product_group": "text",
        "product_form": "text",
        "origin": "text",
        "category_l1": "text",
        "category_l2": "text",
        "category_l3": "text",
        "category_l4": "text",
        "short_name": "text",
        "category_extra": "text",
        "case_pack": "decimal",
        "product_code": "code",
        "source_created_at": "date",
        "product_name": "text",
        "brand": "text",
    },
    ("sku_code", "sku_name", "product_code"),
)


@dataclass(frozen=True)
class CellValue:
    value: Any
    data_type: str
    number_format: str
    is_date: bool


@dataclass
class ParsedRow:
    row_number: int
    raw_values: dict[str, Any]
    cells: dict[str, CellValue]


@dataclass
class ParsedWorkbook:
    source_path: Path
    import_type: ImportType
    sheet: str
    rows: list[ParsedRow]
    ignored_empty_sheets: list[str] = field(default_factory=list)
    # The adapter that actually parsed this workbook; None means the default
    # ADAPTERS[import_type] (Bootstrap). SKU Import V1 carries its own adapter
    # so normalization/validation reuse the same column mapping.
    adapter: "Adapter | None" = None


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    message: str
    field: str | None = None
    row_number: int | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    resolved: bool = False
    details: dict[str, Any] | None = None

    def __post_init__(self):
        if self.code not in ISSUE_CODES:
            raise ValueError(f"Unknown import issue code: {self.code}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "field": self.field,
            "row_number": self.row_number,
            "candidates": list(self.candidates),
            "resolved": self.resolved,
            "details": self.details,
        }


@dataclass(frozen=True)
class Match:
    entity_type: str
    entity_id: int | None
    stable_id: str | None
    name: str | None
    method: str
    candidate_count: int
    confidence_reason: str
    staging_batch_id: str | None = None
    staging_row_number: int | None = None
    governance: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "stable_id": self.stable_id,
            "name": self.name,
            "match_method": self.method,
            "candidate_count": self.candidate_count,
            "confidence_reason": self.confidence_reason,
            "staging_batch_id": self.staging_batch_id,
            "staging_row_number": self.staging_row_number,
            "governance": self.governance,
        }


@dataclass
class EvaluatedRow:
    parsed: ParsedRow
    normalized: dict[str, Any]
    matched_entity: Match | None = None
    references: dict[str, Match | None] = field(default_factory=dict)
    action: RowAction = RowAction.NEW
    status: RowStatus = RowStatus.VALID
    issues: list[Issue] = field(default_factory=list)
    final_values: dict[str, Any] = field(default_factory=dict)
    would_write: bool = False
    product_candidate: dict[str, Any] | None = None
    exclusion: dict[str, Any] | None = None

    def add_issue(self, issue: Issue):
        self.issues.append(issue)

    def finalize(self):
        if self.exclusion:
            self.status = RowStatus.SKIPPED
            self.action = RowAction.UNIMPORTABLE
            self.would_write = False
            return
        severities = {issue.severity for issue in self.issues}
        if Severity.ERROR in severities:
            self.status = RowStatus.ERROR
            if self.action not in {RowAction.CONFLICT}:
                self.action = RowAction.UNIMPORTABLE
        elif self.action == RowAction.EXISTING:
            self.status = RowStatus.EXISTING
        elif Severity.WARNING in severities:
            self.status = RowStatus.WARNING
        else:
            self.status = RowStatus.VALID
        self.would_write = self.status != RowStatus.ERROR and self.action in {
            RowAction.NEW,
            RowAction.UPDATE,
        }


@dataclass(frozen=True)
class PreviewRow:
    row_number: int
    raw: dict[str, Any]
    normalized: dict[str, Any]
    matched_entity: dict[str, Any] | None
    references: dict[str, dict[str, Any] | None]
    action: str
    status: str
    issues: tuple[dict[str, Any], ...]
    final_values: dict[str, Any]
    would_write: bool
    product_candidate: dict[str, Any] | None = None
    exclusion: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "raw": self.raw,
            "normalized": self.normalized,
            "matched_entity": self.matched_entity,
            "references": self.references,
            "proposed_action": self.action,
            "status": self.status,
            "issues": list(self.issues),
            "final_values": self.final_values,
            "would_write": self.would_write,
            "product_candidate": self.product_candidate,
            "exclusion": self.exclusion,
        }


@dataclass(frozen=True)
class Preview:
    batch_id: str
    entity_type: str
    status: str
    summary: dict[str, Any]
    rows: tuple[PreviewRow, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "entity_type": self.entity_type,
            "status": self.status,
            "summary": self.summary,
            "rows": [row.as_dict() for row in self.rows],
            "metadata": self.metadata,
        }

    def filter(self, values: str | Iterable[str] | None = None) -> "Preview":
        if values is None:
            return self
        requested = {values.upper()} if isinstance(values, str) else {item.upper() for item in values}
        allowed = {"ERROR", "WARNING", "NEW", "UPDATE", "CONFLICT", "EXISTING"}
        invalid = requested - allowed
        if invalid:
            raise ValueError(f"Unsupported preview filters: {sorted(invalid)}")
        rows = tuple(
            row
            for row in self.rows
            if row.status in requested
            or row.action in requested
            or any(item.get("severity") in requested for item in row.issues)
        )
        return Preview(self.batch_id, self.entity_type, self.status, self.summary, rows, self.metadata)
