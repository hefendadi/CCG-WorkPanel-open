"""Field catalog for the Sales Pivot Workbench (UX / Architecture Demo).

This is the declarative surface of the pivot demo: every business-meaningful
column of ``sales_fact`` plus the current local MDM masters (``mdm_product`` /
``mdm_sku`` / ``mdm_customer`` / ``mdm_channel`` / ``mdm_salesrep`` /
``mdm_region`` / ``mdm_province``) is declared here exactly once.

Design rules (same discipline as the semantic lab, but a different dataset):

* The catalog stores **logical keys plus physical column names as plain strings**.
  It never holds a SQLAlchemy object, a SQL fragment or an expression.  The
  private allow-list in ``pivot_query_engine`` is the only place a name becomes a
  SQLAlchemy column.
* ``sales_fact`` snapshot fields and current-MDM fields coexist.  A field whose
  value comes from the live master is labelled ``（当前主档）``; one that comes
  from the frozen fact row is labelled ``（销售发生时快照）``.
* Purely technical / governance columns are deliberately absent (see
  ``EXCLUDED_FIELDS``) so the workbench only exposes business semantics.

Nothing here changes the Published-fact snapshot schema: the demo join reads the
current MDM, which is exactly the semantic the UI must disclose.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

TYPE_TEXT = "text"
TYPE_REFERENCE = "reference"   # surrogate id + display label
TYPE_NUMBER = "number"
TYPE_BOOLEAN = "boolean"
TYPE_DATE = "date"
TYPE_MONTH = "month"

FIELD_TYPES = frozenset(
    {TYPE_TEXT, TYPE_REFERENCE, TYPE_NUMBER, TYPE_BOOLEAN, TYPE_DATE, TYPE_MONTH}
)

OP_EQ = "eq"
OP_IN = "in"
OP_GTE = "gte"
OP_LTE = "lte"
OP_BETWEEN = "between"

OPERATORS_BY_TYPE = {
    TYPE_TEXT: (OP_EQ, OP_IN),
    TYPE_REFERENCE: (OP_EQ, OP_IN),
    TYPE_BOOLEAN: (OP_EQ, OP_IN),
    TYPE_NUMBER: (OP_EQ, OP_GTE, OP_LTE, OP_BETWEEN),
    TYPE_DATE: (OP_EQ, OP_GTE, OP_LTE, OP_BETWEEN),
    TYPE_MONTH: (OP_EQ, OP_GTE, OP_LTE, OP_BETWEEN),
}

DIRECTION_ASC = "asc"
DIRECTION_DESC = "desc"
SORT_DIRECTIONS = frozenset({DIRECTION_ASC, DIRECTION_DESC})

UNASSIGNED_LABEL = "未归属"

GROUP_PRODUCT = "商品"
GROUP_CUSTOMER = "客户"
GROUP_CHANNEL = "渠道"
GROUP_SALES = "营业"
GROUP_TIME = "时间"

GROUP_ORDER = (GROUP_PRODUCT, GROUP_CUSTOMER, GROUP_CHANNEL, GROUP_SALES, GROUP_TIME)

# Guardrails
MAX_GROUP_BY_DIMENSIONS = 4
MAX_RESULT_ROWS = 500
MAX_LEAF_GROUPS = 500
"""No Pivot result may exceed this many leaf combinations (CR02A).

Above the bound the engine refuses the request instead of silently truncating —
a partial Pivot that looks complete is worse than a clear error.
"""
RESULT_TOO_LARGE_MESSAGE = "分析结果超过 500 个组合，请增加筛选条件或减少分析维度。"
DEFAULT_ROW_LIMIT = 100

DATASET_SALES_PIVOT = "sales_pivot"

METRIC_ACTUAL_SALES = "actual_sales"
"""The only metric in the first version: SUM(sales_fact.actual_qty)."""

SNAPSHOT_SUFFIX = "（销售发生时快照）"
CURRENT_SUFFIX = "（当前主档）"

NO_SAMPLE_DATA_HINT = "当前 Sample 无数据"
CURRENT_MDM_NOTICE = (
    "Pivot UX Demo：主档属性按当前本地 MDM 解释，不代表历史冻结口径。"
)


# ---------------------------------------------------------------------------
# Field spec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    """One analyzable field.

    ``key_column`` / ``label_column`` / ``code_column`` are *plain physical
    column names* resolved by the engine's private allow-list; they are never
    exposed through the public metadata.
    """

    key: str
    label: str
    group: str
    type: str
    key_column: Optional[str] = None
    label_column: Optional[str] = None
    code_column: Optional[str] = None
    origin: str = "current_master"      # current_master | fact_snapshot | fact
    nullable: bool = True
    groupable: bool = True
    filterable: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"unknown field type: {self.type!r}")
        if self.group not in GROUP_ORDER:
            raise ValueError(f"unknown field group: {self.group!r}")
        if not self.key_column and self.type != TYPE_REFERENCE:
            raise ValueError(f"{self.key}: key_column is required")
        if self.type == TYPE_REFERENCE and not self.key_column:
            raise ValueError(f"{self.key}: reference needs an id column")

    @property
    def operators(self) -> tuple[str, ...]:
        return OPERATORS_BY_TYPE[self.type]

    def to_public_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "type": self.type,
            "origin": self.origin,
            "nullable": self.nullable,
            "groupable": self.groupable,
            "filterable": self.filterable,
            "operators": list(self.operators),
            "description": self.description,
        }


def _f(**kwargs) -> FieldSpec:
    return FieldSpec(**kwargs)


# ---------------------------------------------------------------------------
# Catalog — 商品
# ---------------------------------------------------------------------------

PRODUCT_FIELDS = (
    _f(key="product_snapshot", label=f"Product{SNAPSHOT_SUFFIX}", group=GROUP_PRODUCT,
       type=TYPE_REFERENCE, key_column="fact_product_id",
       label_column="fact_product_name_snapshot", origin="fact_snapshot",
       nullable=False, description="Publish 时冻结在 Fact 行上的 Product 归属。"),
    _f(key="sku_snapshot", label=f"SKU{SNAPSHOT_SUFFIX}", group=GROUP_PRODUCT,
       type=TYPE_REFERENCE, key_column="fact_sku_id",
       label_column="fact_sku_name_snapshot", code_column="fact_sku_code_snapshot",
       origin="fact_snapshot", nullable=False,
       description="Publish 时冻结在 Fact 行上的 SKU 归属。"),
    _f(key="product_current", label=f"Product{CURRENT_SUFFIX}", group=GROUP_PRODUCT,
       type=TYPE_REFERENCE, key_column="master_product_id",
       label_column="master_product_name", origin="current_master", nullable=False,
       description="按 SKU 的当前主档 Product 归属解释。"),
    _f(key="sku_current", label=f"SKU{CURRENT_SUFFIX}", group=GROUP_PRODUCT,
       type=TYPE_REFERENCE, key_column="master_sku_id",
       label_column="master_sku_name", code_column="master_sku_code",
       origin="current_master", nullable=False,
       description="按 SKU 的当前主档归属解释。"),
    _f(key="brand", label="品牌", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_product_brand", origin="current_master",
       description="当前主档 Product 的品牌。"),
    _f(key="product_group", label="ERP分组", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_product_group", origin="current_master"),
    _f(key="product_form", label="产品形式", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_product_form", origin="current_master"),
    _f(key="origin", label="产地", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_origin", origin="current_master"),
    _f(key="category_l1", label="一级分类（大类）", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_category_l1", origin="current_master"),
    _f(key="category_l2", label="二级分类（品类）", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_category_l2", origin="current_master"),
    _f(key="category_l3", label="三级分类（规格）", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_category_l3", origin="current_master"),
    _f(key="category_l4", label="四级分类（克数）", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_category_l4", origin="current_master"),
    _f(key="short_name", label="简称", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_short_name", origin="current_master"),
    _f(key="category_extra", label="扩展分类", group=GROUP_PRODUCT, type=TYPE_TEXT,
       key_column="master_sku_category_extra", origin="current_master"),
    _f(key="case_pack", label="箱规", group=GROUP_PRODUCT, type=TYPE_NUMBER,
       key_column="master_sku_case_pack", origin="current_master"),
)

# ---------------------------------------------------------------------------
# Catalog — 客户
# ---------------------------------------------------------------------------

CUSTOMER_FIELDS = (
    _f(key="customer_snapshot", label=f"Customer{SNAPSHOT_SUFFIX}", group=GROUP_CUSTOMER,
       type=TYPE_REFERENCE, key_column="fact_customer_id",
       label_column="fact_customer_id", origin="fact_snapshot", nullable=True,
       description="Fact 行只冻结 Customer 代理键，没有名称快照，因此以代理键为标签。"),
    _f(key="customer_current", label=f"Customer{CURRENT_SUFFIX}", group=GROUP_CUSTOMER,
       type=TYPE_REFERENCE, key_column="master_customer_id",
       label_column="master_customer_name", origin="current_master", nullable=True,
       description="按当前主档 Customer 名称解释。"),
    _f(key="customer_code", label="客户编码", group=GROUP_CUSTOMER, type=TYPE_TEXT,
       key_column="master_customer_code", origin="current_master", nullable=True),
    _f(key="organization", label="组织", group=GROUP_CUSTOMER, type=TYPE_TEXT,
       key_column="master_customer_organization", origin="current_master"),
    _f(key="department", label="部门", group=GROUP_CUSTOMER, type=TYPE_TEXT,
       key_column="master_customer_department", origin="current_master"),
    _f(key="business_type", label="业务类型", group=GROUP_CUSTOMER, type=TYPE_TEXT,
       key_column="master_customer_business_type", origin="current_master"),
    _f(key="market_type", label="市场类型（EC/REAL）", group=GROUP_CUSTOMER, type=TYPE_TEXT,
       key_column="master_customer_market_type", origin="current_master"),
    _f(key="format_type", label="业态", group=GROUP_CUSTOMER, type=TYPE_TEXT,
       key_column="master_customer_format_type", origin="current_master"),
    _f(key="is_direct", label="是否直营", group=GROUP_CUSTOMER, type=TYPE_BOOLEAN,
       key_column="master_customer_is_direct", origin="current_master"),
)

# ---------------------------------------------------------------------------
# Catalog — 渠道
# ---------------------------------------------------------------------------

CHANNEL_FIELDS = (
    _f(key="bp_channel_snapshot", label=f"BP渠道{SNAPSHOT_SUFFIX}", group=GROUP_CHANNEL,
       type=TYPE_REFERENCE, key_column="fact_channel_id_snapshot",
       label_column="fact_channel_name_snapshot", origin="fact_snapshot",
       nullable=True, description="Publish 时冻结在 Fact 行上的 BP渠道 归属。"),
    _f(key="bp_channel_current", label=f"BP渠道{CURRENT_SUFFIX}", group=GROUP_CHANNEL,
       type=TYPE_REFERENCE, key_column="master_channel_id",
       label_column="master_channel_name", origin="current_master", nullable=True,
       description="按客户当前主档的 Channel 归属解释（客户当前归属，不等于销售时点）。"),
    _f(key="channel_code", label="渠道编码", group=GROUP_CHANNEL, type=TYPE_TEXT,
       key_column="master_channel_code", origin="current_master", nullable=True),
    _f(key="channel_detail", label="渠道明细", group=GROUP_CHANNEL, type=TYPE_TEXT,
       key_column="master_customer_channel_detail", origin="current_master"),
)

# ---------------------------------------------------------------------------
# Catalog — 营业
# ---------------------------------------------------------------------------

SALES_FIELDS = (
    _f(key="salesrep_snapshot", label=f"营业{SNAPSHOT_SUFFIX}", group=GROUP_SALES,
       type=TYPE_REFERENCE, key_column="fact_salesrep_id_snapshot",
       label_column="fact_salesrep_name_snapshot", origin="fact_snapshot",
       nullable=True, description="Publish 时冻结在 Fact 行上的 SalesRep 归属。"),
    _f(key="salesrep_current", label=f"营业{CURRENT_SUFFIX}", group=GROUP_SALES,
       type=TYPE_REFERENCE, key_column="master_salesrep_id",
       label_column="master_salesrep_name", origin="current_master", nullable=True,
       description="按客户当前主档的 SalesRep 归属解释。"),
    _f(key="employee_code", label="员工编码", group=GROUP_SALES, type=TYPE_TEXT,
       key_column="master_salesrep_code", origin="current_master", nullable=True),
    _f(key="region", label="销售地区", group=GROUP_SALES, type=TYPE_TEXT,
       key_column="master_region_name", origin="current_master", nullable=True),
    _f(key="province", label="省份", group=GROUP_SALES, type=TYPE_TEXT,
       key_column="master_province_name", origin="current_master", nullable=True),
)

# ---------------------------------------------------------------------------
# Catalog — 时间
# ---------------------------------------------------------------------------

TIME_FIELDS = (
    _f(key="sales_date", label="销售日期", group=GROUP_TIME, type=TYPE_DATE,
       key_column="fact_sales_date", origin="fact", nullable=False,
       description="业务发生日期（Fact 的 sales_date）。"),
    _f(key="snapshot_month", label="快照月份", group=GROUP_TIME, type=TYPE_MONTH,
       key_column="fact_snapshot_month", origin="fact", nullable=False),
)

FIELDS: tuple[FieldSpec, ...] = (
    PRODUCT_FIELDS + CUSTOMER_FIELDS + CHANNEL_FIELDS + SALES_FIELDS + TIME_FIELDS
)

FIELD_MAP = {field.key: field for field in FIELDS}


def get_field(key: str) -> Optional[FieldSpec]:
    return FIELD_MAP.get(key)


# ---------------------------------------------------------------------------
# Join model (declared for review; the engine builds it)
# ---------------------------------------------------------------------------

JOIN_MODEL = (
    {"from": "sales_fact.sku_id", "to": "mdm_sku.id", "type": "many_to_one",
     "purpose": "SKU / Product / 分类 主档"},
    {"from": "mdm_sku.product_id", "to": "mdm_product.id", "type": "many_to_one",
     "purpose": "Product 当前主档"},
    {"from": "sales_fact.customer_id", "to": "mdm_customer.id", "type": "many_to_one",
     "purpose": "Customer 当前主档"},
    {"from": "mdm_customer.channel_id", "to": "mdm_channel.id", "type": "many_to_one",
     "purpose": "客户当前 BP渠道"},
    {"from": "mdm_customer.salesrep_id", "to": "mdm_salesrep.id", "type": "many_to_one",
     "purpose": "客户当前营业"},
    {"from": "mdm_customer.region_id", "to": "mdm_region.id", "type": "many_to_one",
     "purpose": "客户当前销售地区"},
    {"from": "mdm_customer.province_id", "to": "mdm_province.id", "type": "many_to_one",
     "purpose": "客户当前省份"},
)

# ---------------------------------------------------------------------------
# Deliberate exclusions — technical / governance columns
# ---------------------------------------------------------------------------

EXCLUDED_FIELDS = {
    "sales_fact": (
        "id", "import_row_id", "source_system", "source_document_no",
        "source_line_no", "created_at",
    ),
    "mdm_*": ("id", "stable_id", "status", "created_at", "updated_at",
              "parent_customer_id", "region_id", "province_id",
              "source_created_at", "source_created_ym"),
}
"""Notes: source_system / source_document_no / source_line_no / import_row_id are
ingestion provenance, not business dimensions; stable_id / status / timestamps are
master-data governance; the surrogate FK columns are represented by their resolved
business fields (region / province / customer / channel) instead."""


# ---------------------------------------------------------------------------
# Time modes (business time only — never created_at)
# ---------------------------------------------------------------------------

TIME_MODE_ALL = "all"
TIME_MODE_MTD = "mtd"
TIME_MODE_CUSTOM = "custom"

TIME_MODES = (
    {"key": TIME_MODE_ALL, "label": "全部时间",
     "description": "不限时间口径，读取当前快照月全部业务日期。"},
    {"key": TIME_MODE_MTD, "label": "MTD",
     "description": "最新可用快照月 1 日 → 该月 MAX(sales_date)，不使用系统今天、不使用 created_at。"},
    {"key": TIME_MODE_CUSTOM, "label": "自定义日期",
     "description": "指定业务日期起止范围。"},
)

JP_CATEGORY_MISSING_NOTICE = (
    "日本分类字段待正式 MDM 接入：当前本地 MDM 无 mdm_sku.jp_category，"
    "不得用「扩展分类」或「一级分类」冒充，因此本 Preset 暂不生成该维度/筛选。"
)


# ---------------------------------------------------------------------------
# Confirmed business mappings (CR02A §6)
# ---------------------------------------------------------------------------

BUSINESS_MAPPINGS = (
    {
        "business_term": "部门",
        "logical_field": "department",
        "target": "mdm_customer.department",
        "status": "CONFIRMED",
        "note": "Customer Import 契约「部门」→ department。",
    },
    {
        "business_term": "EC/REAL",
        "logical_field": "market_type",
        "target": "mdm_customer.market_type",
        "status": "CONFIRMED",
        "note": "Orchestrator 正式确认 EC/REAL = market_type；Preset 02 UI 显示 EC/REAL。",
    },
    {
        "business_term": "营业组",
        "logical_field": "bp_channel_snapshot",
        "target": "sales_fact.channel_name_snapshot（销售发生时快照）",
        "source_path": "Customer Source「营业组」→ mdm_customer.channel_id → mdm_channel.channel_name",
        "status": "CONFIRMED",
        "note": (
            "主档链路用于 Customer 刷新；Sales Actual 历史分析固定使用 Fact 快照列，"
            "不用 current channel 反推历史归属。Preset 02 UI 标签 = 营业组。不再标记 pending。"
        ),
    },
    {
        "business_term": "日本分类",
        "logical_field": None,
        "target": "mdm_sku.jp_category",
        "status": "PENDING_SCHEMA",
        "note": "当前本地 MDM 无该列；不得用 category_extra / category_l1 冒充。",
    },
)


# ---------------------------------------------------------------------------
# Presets — pre-filled pivot configurations (never fixed pages)
# ---------------------------------------------------------------------------

PRESETS = (
    {
        "key": "jp_category_mtd",
        "label": "日本分类 MTD 销售",
        "description": "Rows：日本分类 · Value：实际销量 · Time：MTD · Grand Total：ON",
        "blocked_by": ["jp_category"],
        "available": False,
        "notice": JP_CATEGORY_MISSING_NOTICE,
        "config": {
            "dimensions": [],
            "subtotals": [],
            "grand_total": True,
            "time": {"mode": TIME_MODE_MTD},
            "filters": [],
            "label_overrides": {},
        },
    },
    {
        "key": "demo_category_mtd",
        "label": "分类 MTD Demo（非日本分类）",
        "description": "布局演示：一级分类（大类）× 实际销量 · Time：MTD · Grand Total：ON",
        "blocked_by": [],
        "available": True,
        "notice": (
            "Demo-only 字段：用「一级分类（大类）」演示 MTD 报表布局，"
            "它**不是**日本分类，仅用于验证层级/小计/总计的视觉。"
        ),
        "config": {
            "dimensions": ["category_l1"],
            "subtotals": [],
            "grand_total": True,
            "time": {"mode": TIME_MODE_MTD},
            "filters": [],
            "label_overrides": {
                "category_l1": "分类 MTD Demo（非日本分类）",
            },
        },
    },
    {
        "key": "sales_org_mtd",
        "label": "营业组织 MTD 销售",
        "description": "Rows：部门 → EC/REAL → 营业组 · Value：实际销量 · Time：MTD · 部门小计 · Grand Total：ON",
        "blocked_by": [],
        "available": True,
        "notice": (
            "日本分类筛选待正式 MDM 接入，已跳过该筛选条件。"
            "营业组 = sales_fact.channel_name_snapshot（销售发生时快照，已正式确认）。"
        ),
        "config": {
            "dimensions": ["department", "market_type", "bp_channel_snapshot"],
            "subtotals": ["department"],
            "grand_total": True,
            "time": {"mode": TIME_MODE_MTD},
            "filters": [],
            "label_overrides": {
                "market_type": "EC/REAL",
                "bp_channel_snapshot": "营业组",
            },
        },
    },
    {
        "key": "sales_org_mtd_real",
        "label": "营业组织 MTD 销售（仅 REAL）",
        "description": "同「营业组织 MTD 销售」，但预置筛选 EC/REAL = REAL（验证 Preset 筛选链路）",
        "blocked_by": [],
        "available": True,
        "notice": "模板筛选 EC/REAL = REAL 已带入筛选条件，可继续修改或清空。",
        "config": {
            "dimensions": ["department", "market_type", "bp_channel_snapshot"],
            "subtotals": ["department"],
            "grand_total": True,
            "time": {"mode": TIME_MODE_MTD},
            "filters": [{"field": "market_type", "operator": "in", "values": ["REAL"]}],
            "label_overrides": {
                "market_type": "EC/REAL",
                "bp_channel_snapshot": "营业组",
            },
        },
    },
)


def get_preset_metadata() -> list:
    return [
        {
            "key": preset["key"],
            "label": preset["label"],
            "description": preset["description"],
            "blocked_by": list(preset["blocked_by"]),
            "blocked": not preset["available"],
            "available": preset["available"],
            "notice": preset["notice"],
            "config": {
                "dimensions": list(preset["config"]["dimensions"]),
                "subtotals": list(preset["config"]["subtotals"]),
                "grand_total": preset["config"]["grand_total"],
                "time": dict(preset["config"]["time"]),
                "filters": [dict(item) for item in preset["config"]["filters"]],
                "label_overrides": dict(preset["config"]["label_overrides"]),
            },
        }
        for preset in PRESETS
    ]


# ---------------------------------------------------------------------------
# Public metadata
# ---------------------------------------------------------------------------

def get_catalog_metadata() -> dict:
    groups = []
    for group in GROUP_ORDER:
        members = [field.to_public_dict() for field in FIELDS if field.group == group]
        if members:
            groups.append({"key": group, "label": group, "fields": members})
    return {
        "dataset": DATASET_SALES_PIVOT,
        "label": "销售分析工作台",
        "notice": CURRENT_MDM_NOTICE,
        "metric": {
            "key": METRIC_ACTUAL_SALES,
            "label": "实际销量",
            "aggregation": "sum",
            "format": "decimal",
            "unit": "Pcs",
        },
        "max_group_by_dimensions": MAX_GROUP_BY_DIMENSIONS,
        "max_result_rows": MAX_RESULT_ROWS,
        "max_leaf_groups": MAX_LEAF_GROUPS,
        "default_row_limit": DEFAULT_ROW_LIMIT,
        "groups": groups,
        "fields": [field.to_public_dict() for field in FIELDS],
        "joins": [dict(item) for item in JOIN_MODEL],
        "excluded": {table: list(columns) for table, columns in EXCLUDED_FIELDS.items()},
        "no_sample_data_hint": NO_SAMPLE_DATA_HINT,
        "time_modes": [dict(item) for item in TIME_MODES],
        "presets": get_preset_metadata(),
        "business_mappings": [dict(item) for item in BUSINESS_MAPPINGS],
    }
