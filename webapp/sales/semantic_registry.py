"""Declarative semantic registry for the ACT Sales Demo (Architecture Spike).

This module is the only place that knows how a business metric or dimension maps
onto the ``sales_actual`` dataset.  Everything above it — the metadata API, the
generic query engine and the Explorer UI — is driven by these specs.

Deliberate boundaries of this spike:

* The dataset is the Published Sales Fact snapshot.  Dimensions read the
  **Fact snapshot columns only**.  No MDM table is joined here, because a JOIN to
  current MDM would silently re-classify historical Channel / SalesRep / Product
  attribution.  ``SalesFact`` keeps the identity captured at Publish time.
* ``category_l1..l4`` are intentionally absent: ``SalesFact`` has no Category
  snapshot yet.  See ``docs/spikes/sales-semantic-layer-demo.md`` and the
  Architecture tab for the pending snapshot-policy decision.
* Specs carry no SQL, no SQLAlchemy Column, and no model attribute.  They carry
  the physical column *name* as a plain string, and the engine resolves that name
  against a fixed server-side mapping.

Adding a new metric or dimension is a registry edit plus one engine mapping
entry.  It is not a new query function, endpoint or page.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Dataset identity
# ---------------------------------------------------------------------------

DATASET_SALES_ACTUAL = "sales_actual"

SOURCE_SYSTEM = "DEMO_ERP"
"""Synthetic demo source system.  Never point this at production data."""


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

# A metric is a single additive aggregation over the Fact table.
AGG_SUM = "sum"

METRIC_AGGREGATIONS = frozenset({AGG_SUM})

# Physical value types, used for filter validation and result coercion.
TYPE_DECIMAL = "decimal"
TYPE_INTEGER = "integer"
TYPE_STRING = "string"
TYPE_DATE = "date"
TYPE_MONTH = "month"
TYPE_BOOLEAN = "boolean"

# A dimension's role decides how it can be filtered.
ROLE_REFERENCE = "reference"   # keyed master reference; has a stable code
ROLE_LABEL = "label"           # free-text snapshot label, no stable code
ROLE_DATE = "date"             # calendar date
ROLE_MONTH = "month"           # calendar month (first day of month)

DIMENSION_ROLES = frozenset({ROLE_REFERENCE, ROLE_LABEL, ROLE_DATE, ROLE_MONTH})

# Filter operators the engine will translate.  Anything else is rejected.
OP_EQ = "eq"
OP_IN = "in"
OP_GTE = "gte"
OP_LTE = "lte"
OP_BETWEEN = "between"

FILTER_OPERATORS = frozenset({OP_EQ, OP_IN, OP_GTE, OP_LTE, OP_BETWEEN})

# Which operators a dimension's underlying type may use.
OPERATORS_BY_TYPE = {
    TYPE_STRING: (OP_EQ, OP_IN),
    TYPE_DECIMAL: (OP_EQ, OP_GTE, OP_LTE, OP_BETWEEN),
    TYPE_INTEGER: (OP_EQ, OP_IN, OP_GTE, OP_LTE, OP_BETWEEN),
    TYPE_DATE: (OP_EQ, OP_GTE, OP_LTE, OP_BETWEEN),
    TYPE_MONTH: (OP_EQ, OP_GTE, OP_LTE, OP_BETWEEN),
    TYPE_BOOLEAN: (OP_EQ, OP_IN),
}

# Sort directions.
DIRECTION_ASC = "asc"
DIRECTION_DESC = "desc"
SORT_DIRECTIONS = frozenset({DIRECTION_ASC, DIRECTION_DESC})

# How a dimension participates in grouping and how its key is produced.
#   * "snapshot_id"   — grouped by the Fact snapshot id (nullable => 未归属)
#   * "snapshot_code" — grouped by the Fact snapshot code (nullable => 未归属)
#   * "snapshot_text" — grouped by a free-text Fact snapshot label
#   * "fact_column"   — grouped directly by a non-null Fact column
GROUP_SNAPSHOT_ID = "snapshot_id"
GROUP_SNAPSHOT_CODE = "snapshot_code"
GROUP_SNAPSHOT_TEXT = "snapshot_text"
GROUP_FACT_COLUMN = "fact_column"

GROUP_STRATEGIES = frozenset({
    GROUP_SNAPSHOT_ID, GROUP_SNAPSHOT_CODE, GROUP_SNAPSHOT_TEXT, GROUP_FACT_COLUMN,
})

UNASSIGNED_LABEL = "未归属"
"""Same label the production dashboard uses for a NULL snapshot reference."""


# Default guardrail values, mirrored in the API layer.
DEFAULT_MAX_GROUP_BY = 3
MAX_GROUP_BY_DIMENSIONS = 3
DEFAULT_MAX_ROWS = 500
MAX_RESULT_ROWS = 500
DEFAULT_ROW_LIMIT = 100


@dataclass(frozen=True)
class MetricSpec:
    """One additive measure over the dataset.

    ``aggregation`` + ``column`` describe ``SUM(<column>)``.  ``column`` is a
    plain physical-name string resolved by the engine's allow-list, so no
    SQLAlchemy object ever leaves this module.
    """

    key: str
    label: str
    description: str
    aggregation: str
    column: str
    format: str = "decimal"
    unit: Optional[str] = None

    def __post_init__(self):
        if self.aggregation not in METRIC_AGGREGATIONS:
            raise ValueError(f"unsupported aggregation: {self.aggregation!r}")

    def to_public_dict(self) -> dict:
        """Metadata safe to expose over HTTP: no SQL, no column objects."""
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "aggregation": self.aggregation,
            "format": self.format,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class DimensionSpec:
    """One groupable / filterable axis of the dataset.

    ``group_strategy`` and the ``*_column`` names tell the engine which Fact
    snapshot column supplies the group key.  ``operators`` is derived from
    ``type`` so a dimension can never accept an operator its type cannot carry.
    """

    key: str
    label: str
    description: str
    type: str
    role: str
    group_strategy: str
    key_column: Optional[str] = None
    label_column: Optional[str] = None
    code_column: Optional[str] = None
    id_column: Optional[str] = None
    groupable: bool = True
    filterable: bool = True
    sortable: bool = True
    nullable: bool = False
    operators: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self):
        if self.type not in OPERATORS_BY_TYPE:
            raise ValueError(f"unsupported dimension type: {self.type!r}")
        if self.role not in DIMENSION_ROLES:
            raise ValueError(f"unsupported dimension role: {self.role!r}")
        if self.group_strategy not in GROUP_STRATEGIES:
            raise ValueError(f"unsupported group strategy: {self.group_strategy!r}")
        if not self.operators:
            object.__setattr__(self, "operators", OPERATORS_BY_TYPE[self.type])
        unknown = set(self.operators) - FILTER_OPERATORS
        if unknown:
            raise ValueError(f"unsupported operators: {sorted(unknown)}")
        unsupported_for_type = set(self.operators) - set(OPERATORS_BY_TYPE[self.type])
        if unsupported_for_type:
            raise ValueError(
                f"operators {sorted(unsupported_for_type)} not valid for {self.type}"
            )

    def to_public_dict(self) -> dict:
        """Metadata safe to expose over HTTP: no SQL, no column objects."""
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "type": self.type,
            "role": self.role,
            "groupable": self.groupable,
            "filterable": self.filterable,
            "sortable": self.sortable,
            "nullable": self.nullable,
            "operators": list(self.operators),
        }


@dataclass(frozen=True)
class DatasetSpec:
    """A queryable dataset: its metrics, its dimensions and its guardrails."""

    key: str
    label: str
    description: str
    metrics: Sequence[MetricSpec]
    dimensions: Sequence[DimensionSpec]
    max_group_by: int = DEFAULT_MAX_GROUP_BY
    max_rows: int = MAX_RESULT_ROWS
    default_row_limit: int = DEFAULT_ROW_LIMIT

    def __post_init__(self):
        metric_keys = [m.key for m in self.metrics]
        dimension_keys = [d.key for d in self.dimensions]
        if len(set(metric_keys)) != len(metric_keys):
            raise ValueError("duplicate metric key in dataset")
        if len(set(dimension_keys)) != len(dimension_keys):
            raise ValueError("duplicate dimension key in dataset")
        if self.max_group_by < 1:
            raise ValueError("max_group_by must be >= 1")
        if self.max_rows < 1:
            raise ValueError("max_rows must be >= 1")

    @property
    def metric_map(self) -> dict:
        return {m.key: m for m in self.metrics}

    @property
    def dimension_map(self) -> dict:
        return {d.key: d for d in self.dimensions}

    def metric(self, key: str) -> Optional[MetricSpec]:
        return self.metric_map.get(key)

    def dimension(self, key: str) -> Optional[DimensionSpec]:
        return self.dimension_map.get(key)

    def to_public_dict(self) -> dict:
        return {
            "dataset": self.key,
            "label": self.label,
            "description": self.description,
            "metrics": [m.to_public_dict() for m in self.metrics],
            "dimensions": [d.to_public_dict() for d in self.dimensions],
            "presets": get_preset_metadata(),
            "future_dimensions": get_future_dimension_metadata(),
            "guardrails": {
                "max_group_by_dimensions": self.max_group_by,
                "max_result_rows": self.max_rows,
                "default_row_limit": self.default_row_limit,
            },
        }


# ---------------------------------------------------------------------------
# Metric registry (first version: exactly one metric)
# ---------------------------------------------------------------------------

ACTUAL_SALES = MetricSpec(
    key="actual_sales",
    label="实际销量",
    description=(
        "当前已发布快照的 ERP 实际出库数量合计 (Pcs)。"
        "第一版只有这一个加性指标，口径 = 实际出库数量合计。"
    ),
    aggregation=AGG_SUM,
    column="actual_qty",
    format="decimal",
    unit="Pcs",
)

METRICS = (ACTUAL_SALES,)


# ---------------------------------------------------------------------------
# Dimension registry
# ---------------------------------------------------------------------------

PRODUCT = DimensionSpec(
    key="product",
    label="商品",
    description="Product identity captured on the Fact row at Publish time.",
    type=TYPE_INTEGER,
    role=ROLE_REFERENCE,
    group_strategy=GROUP_SNAPSHOT_ID,
    id_column="product_id",
    label_column="product_name_snapshot",
    groupable=True,
    filterable=True,
    nullable=False,
)

SKU = DimensionSpec(
    key="sku",
    label="SKU",
    description="SKU identity captured on the Fact row at Publish time.",
    type=TYPE_INTEGER,
    role=ROLE_REFERENCE,
    group_strategy=GROUP_SNAPSHOT_ID,
    id_column="sku_id",
    label_column="sku_name_snapshot",
    code_column="sku_code_snapshot",
    groupable=True,
    filterable=True,
    nullable=False,
)

CHANNEL = DimensionSpec(
    key="channel",
    label="BP渠道",
    description=(
        "Channel snapshot from the Publish-time mapping. NULL is reported as "
        f"{UNASSIGNED_LABEL}; current MDM Channel is never substituted."
    ),
    type=TYPE_INTEGER,
    role=ROLE_REFERENCE,
    group_strategy=GROUP_SNAPSHOT_ID,
    id_column="channel_id_snapshot",
    label_column="channel_name_snapshot",
    groupable=True,
    filterable=True,
    nullable=True,
)

SALESREP = DimensionSpec(
    key="salesrep",
    label="营业员",
    description=(
        "SalesRep snapshot from the Publish-time mapping. NULL is reported as "
        f"{UNASSIGNED_LABEL}; current MDM SalesRep is never substituted."
    ),
    type=TYPE_INTEGER,
    role=ROLE_REFERENCE,
    group_strategy=GROUP_SNAPSHOT_ID,
    id_column="salesrep_id_snapshot",
    label_column="salesrep_name_snapshot",
    groupable=True,
    filterable=True,
    nullable=True,
)

CUSTOMER = DimensionSpec(
    key="customer",
    label="Customer",
    description=(
        "Customer reference frozen at Publish time. Only the surrogate id is "
        f"captured, so the id itself is the label. NULL is {UNASSIGNED_LABEL}."
    ),
    type=TYPE_INTEGER,
    role=ROLE_REFERENCE,
    group_strategy=GROUP_SNAPSHOT_ID,
    id_column="customer_id",
    groupable=True,
    filterable=True,
    nullable=True,
)

SALES_DATE = DimensionSpec(
    key="sales_date",
    label="销售日期",
    description="Business date of the sale line, as imported.",
    type=TYPE_DATE,
    role=ROLE_DATE,
    group_strategy=GROUP_FACT_COLUMN,
    key_column="sales_date",
    groupable=True,
    filterable=True,
    nullable=False,
)

SNAPSHOT_MONTH = DimensionSpec(
    key="snapshot_month",
    label="Snapshot Month",
    description="Snapshot month (first day of month) the Fact row belongs to.",
    type=TYPE_MONTH,
    role=ROLE_MONTH,
    group_strategy=GROUP_FACT_COLUMN,
    key_column="snapshot_month",
    groupable=True,
    filterable=True,
    nullable=False,
)

DIMENSIONS = (
    PRODUCT,
    SKU,
    CHANNEL,
    SALESREP,
    CUSTOMER,
    SALES_DATE,
    SNAPSHOT_MONTH,
)

# Convenience aliases the Explorer and tests use.
PRODUCT_KEY = PRODUCT.key
SKU_KEY = SKU.key
CHANNEL_KEY = CHANNEL.key
SALESREP_KEY = SALESREP.key
CUSTOMER_KEY = CUSTOMER.key
SALES_DATE_KEY = SALES_DATE.key
SNAPSHOT_MONTH_KEY = SNAPSHOT_MONTH.key


# ---------------------------------------------------------------------------
# Dataset registry
# ---------------------------------------------------------------------------

SALES_ACTUAL_DATASET = DatasetSpec(
    key=DATASET_SALES_ACTUAL,
    label="销售实绩",
    description=(
        "已发布的实际销售快照（sales_fact）。每个维度都取自 Publish 时冻结的归属，"
        "因此重跑历史月份不会用当前主数据重新分类。业务时间 = sales_date。"
    ),
    metrics=METRICS,
    dimensions=DIMENSIONS,
    max_group_by=MAX_GROUP_BY_DIMENSIONS,
    max_rows=MAX_RESULT_ROWS,
    default_row_limit=DEFAULT_ROW_LIMIT,
)

DATASETS = (SALES_ACTUAL_DATASET,)


def get_all_datasets() -> dict:
    return {d.key: d for d in DATASETS}


def get_dataset(key: str) -> Optional[DatasetSpec]:
    return get_all_datasets().get(key)


def get_metric_metadata(dataset_key: str = DATASET_SALES_ACTUAL) -> dict:
    """Public metric list for a dataset, or an empty dict when unknown."""
    dataset = get_dataset(dataset_key)
    if dataset is None:
        return {}
    return {m.key: m.to_public_dict() for m in dataset.metrics}


def get_dimension_metadata(dataset_key: str = DATASET_SALES_ACTUAL) -> dict:
    """Public dimension list for a dataset, or an empty dict when unknown."""
    dataset = get_dataset(dataset_key)
    if dataset is None:
        return {}
    return {d.key: d.to_public_dict() for d in dataset.dimensions}


def get_dataset_metadata(dataset_key: str = DATASET_SALES_ACTUAL) -> Optional[dict]:
    dataset = get_dataset(dataset_key)
    return dataset.to_public_dict() if dataset else None


# ---------------------------------------------------------------------------
# Presets — the four existing dashboard views expressed as Metric + Dimensions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PresetSpec:
    """A named Metric + Dimension combination.

    Presets exist only to pre-fill the Explorer selection.  They do **not** map
    to a dedicated endpoint: every preset issues the same
    ``POST /api/v1/lab/sales/query`` request.
    """

    key: str
    label: str
    metric: str
    dimensions: Sequence[str]
    equivalent_existing_view: str

    def to_public_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "equivalent_existing_view": self.equivalent_existing_view,
        }


PRESETS = (
    PresetSpec(
        key="product",
        label="Product",
        metric=ACTUAL_SALES.key,
        dimensions=(PRODUCT.key,),
        equivalent_existing_view="Product aggregation",
    ),
    PresetSpec(
        key="channel_product",
        label="Channel × Product",
        metric=ACTUAL_SALES.key,
        dimensions=(CHANNEL.key, PRODUCT.key),
        equivalent_existing_view="Channel × Product",
    ),
    PresetSpec(
        key="salesrep_product",
        label="SalesRep × Product",
        metric=ACTUAL_SALES.key,
        dimensions=(SALESREP.key, PRODUCT.key),
        equivalent_existing_view="SalesRep × Product",
    ),
    PresetSpec(
        key="product_sku",
        label="Product × SKU",
        metric=ACTUAL_SALES.key,
        dimensions=(PRODUCT.key, SKU.key),
        equivalent_existing_view="Product → SKU drilldown",
    ),
)


def get_presets() -> dict:
    return {p.key: p for p in PRESETS}


def get_preset_metadata() -> list:
    return [p.to_public_dict() for p in PRESETS]


# ---------------------------------------------------------------------------
# Future dimensions: explicitly NOT queryable
# ---------------------------------------------------------------------------

FUTURE_DIMENSIONS = (
    {
        "key": "category_l1",
        "label": "Category L1",
        "status": "REQUIRES_SNAPSHOT_POLICY_DECISION",
        "reason": "No Category snapshot is stored on the sales fact row.",
    },
    {
        "key": "category_l2",
        "label": "Category L2",
        "status": "REQUIRES_SNAPSHOT_POLICY_DECISION",
        "reason": "No Category snapshot is stored on the sales fact row.",
    },
    {
        "key": "category_l3",
        "label": "Category L3",
        "status": "REQUIRES_SNAPSHOT_POLICY_DECISION",
        "reason": "No Category snapshot is stored on the sales fact row.",
    },
    {
        "key": "category_l4",
        "label": "Category L4",
        "status": "REQUIRES_SNAPSHOT_POLICY_DECISION",
        "reason": "No Category snapshot is stored on the sales fact row.",
    },
)
"""Declared for the Architecture tab only. The engine rejects these keys."""


def get_future_dimension_metadata() -> list:
    return [dict(item) for item in FUTURE_DIMENSIONS]
