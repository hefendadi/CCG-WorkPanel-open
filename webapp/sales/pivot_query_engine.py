"""Query engine for the Sales Pivot Workbench (UX / Architecture Demo).

One endpoint executes every table / bar / filter combination of the workbench.
The engine never accepts SQL, a table name, a column name or an expression from
the client:

    logical field key  ->  pivot_catalog.FieldSpec  ->  private allow-list
                       ->  SQLAlchemy column         ->  SELECT / GROUP BY

Guarantees enforced here:

* field / metric / operator whitelists (from ``pivot_catalog``)
* at most 4 group-by dimensions, at most 500 result rows
* every MDM join is **many-to-one on a primary key**; the engine counts the base
  Fact rows and the joined rows on every request and refuses to return a result
  if the join multiplied rows
* only the current Published (and not replaced) batch is readable — the same
  ``_validated_batch`` guard the production dashboard uses
* deterministic ordering: metric descending, then every group key ascending

The join reads the **current** local MDM.  That is a deliberate demo semantic and
is disclosed in the catalog notice; the Published Fact snapshot columns stay
untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from sqlalchemy import case, func, or_, select

from webapp.mdm.models import (
    Channel,
    Customer,
    Product,
    Province,
    Region,
    SKU,
    SalesRep,
)
from webapp.sales.dashboard_query_service import (
    DashboardFilters,
    _decimal,
    _scope,
    _validated_batch,
)
from webapp.sales.models import SalesFact, SalesImportRow
from webapp.sales.pivot_catalog import (
    CURRENT_MDM_NOTICE,
    DATASET_SALES_PIVOT,
    DEFAULT_ROW_LIMIT,
    DIRECTION_ASC,
    DIRECTION_DESC,
    MAX_GROUP_BY_DIMENSIONS,
    MAX_LEAF_GROUPS,
    MAX_RESULT_ROWS,
    METRIC_ACTUAL_SALES,
    RESULT_TOO_LARGE_MESSAGE,
    OP_BETWEEN,
    OP_EQ,
    OP_GTE,
    OP_IN,
    OP_LTE,
    SORT_DIRECTIONS,
    TIME_MODE_ALL,
    TIME_MODE_CUSTOM,
    TIME_MODE_MTD,
    TYPE_BOOLEAN,
    TYPE_DATE,
    TYPE_MONTH,
    TYPE_NUMBER,
    TYPE_REFERENCE,
    UNASSIGNED_LABEL,
    FieldSpec,
    get_field,
)

from webapp.sales.pivot_catalog import FIELDS as _CATALOG_FIELDS

PIVOT_QUERY_INVALID = "PIVOT_QUERY_INVALID"
PIVOT_JOIN_MULTIPLIED = "PIVOT_JOIN_MULTIPLIED"
PIVOT_REGISTRY_INVALID = "PIVOT_REGISTRY_INVALID"
PIVOT_RESULT_TOO_LARGE = "PIVOT_RESULT_TOO_LARGE"

_SORTABLE_FIELDS = frozenset({METRIC_ACTUAL_SALES})

_METRICS = {METRIC_ACTUAL_SALES: SalesFact.actual_qty}

_REQUEST_FIELDS = frozenset(
    {"batch_id", "dimensions", "metrics", "filters", "sort", "limit", "offset",
     "time", "subtotals", "grand_total"}
)
_FILTER_FIELDS = frozenset({"field", "operator", "value", "values"})
_TIME_FIELDS = frozenset({"mode", "start", "end"})


class PivotQueryError(ValueError):
    """A request the catalog cannot express. Carries a stable machine code."""

    def __init__(self, code: str, message: str, *, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def _invalid(message: str, **details) -> PivotQueryError:
    return PivotQueryError(PIVOT_QUERY_INVALID, message, details=details)


# ---------------------------------------------------------------------------
# Physical expression allow-list
# ---------------------------------------------------------------------------

_SF = SalesFact.__table__
_SIR = SalesImportRow.__table__
_SKU = SKU.__table__
_PRODUCT = Product.__table__
_CUSTOMER = Customer.__table__
_CHANNEL = Channel.__table__
_SALESREP = SalesRep.__table__
_REGION = Region.__table__
_PROVINCE = Province.__table__


def _base_from():
    """Fact rows joined to the current MDM masters, all many-to-one on a PK."""
    return (
        _SF.join(_SIR, _SIR.c.id == _SF.c.import_row_id)
        .outerjoin(_SKU, _SKU.c.id == _SF.c.sku_id)
        .outerjoin(_PRODUCT, _PRODUCT.c.id == _SKU.c.product_id)
        .outerjoin(_CUSTOMER, _CUSTOMER.c.id == _SF.c.customer_id)
        .outerjoin(_CHANNEL, _CHANNEL.c.id == _CUSTOMER.c.channel_id)
        .outerjoin(_SALESREP, _SALESREP.c.id == _CUSTOMER.c.salesrep_id)
        .outerjoin(_REGION, _REGION.c.id == _CUSTOMER.c.region_id)
        .outerjoin(_PROVINCE, _PROVINCE.c.id == _CUSTOMER.c.province_id)
    )


_EXPRESSIONS = {
    # metric
    "actual_qty": _SF.c.actual_qty,
    # fact snapshots
    "fact_product_id": _SF.c.product_id,
    "fact_product_name_snapshot": _SF.c.product_name_snapshot,
    "fact_sku_id": _SF.c.sku_id,
    "fact_sku_name_snapshot": _SF.c.sku_name_snapshot,
    "fact_sku_code_snapshot": _SF.c.sku_code_snapshot,
    "fact_customer_id": _SF.c.customer_id,
    "fact_channel_id_snapshot": _SF.c.channel_id_snapshot,
    "fact_channel_name_snapshot": _SF.c.channel_name_snapshot,
    "fact_salesrep_id_snapshot": _SF.c.salesrep_id_snapshot,
    "fact_salesrep_name_snapshot": _SF.c.salesrep_name_snapshot,
    "fact_sales_date": _SF.c.sales_date,
    "fact_snapshot_month": _SF.c.snapshot_month,
    # current master — product
    "master_product_id": _PRODUCT.c.id,
    "master_product_name": _PRODUCT.c.product_name,
    "master_product_brand": _PRODUCT.c.brand,
    # current master — sku
    "master_sku_id": _SKU.c.id,
    "master_sku_name": _SKU.c.sku_name,
    "master_sku_code": _SKU.c.sku_code,
    "master_sku_product_group": _SKU.c.product_group,
    "master_sku_product_form": _SKU.c.product_form,
    "master_sku_origin": _SKU.c.origin,
    "master_sku_category_l1": _SKU.c.category_l1,
    "master_sku_category_l2": _SKU.c.category_l2,
    "master_sku_category_l3": _SKU.c.category_l3,
    "master_sku_category_l4": _SKU.c.category_l4,
    "master_sku_short_name": _SKU.c.short_name,
    "master_sku_category_extra": _SKU.c.category_extra,
    "master_sku_case_pack": _SKU.c.case_pack,
    # current master — customer
    "master_customer_id": _CUSTOMER.c.id,
    "master_customer_name": _CUSTOMER.c.customer_name,
    "master_customer_code": _CUSTOMER.c.customer_code,
    "master_customer_organization": _CUSTOMER.c.organization,
    "master_customer_department": _CUSTOMER.c.department,
    "master_customer_business_type": _CUSTOMER.c.business_type,
    "master_customer_market_type": _CUSTOMER.c.market_type,
    "master_customer_format_type": _CUSTOMER.c.format_type,
    "master_customer_channel_detail": _CUSTOMER.c.channel_detail,
    "master_customer_is_direct": _CUSTOMER.c.is_direct,
    # current master — channel / salesrep / region / province
    "master_channel_id": _CHANNEL.c.id,
    "master_channel_name": _CHANNEL.c.channel_name,
    "master_channel_code": _CHANNEL.c.channel_code,
    "master_salesrep_id": _SALESREP.c.id,
    "master_salesrep_name": _SALESREP.c.salesrep_name,
    "master_salesrep_code": _SALESREP.c.employee_code,
    "master_region_name": _REGION.c.region_name,
    "master_province_name": _PROVINCE.c.province_name,
}


def _expression(name: str):
    column = _EXPRESSIONS.get(name)
    if column is None:
        raise PivotQueryError(
            PIVOT_REGISTRY_INVALID,
            f"catalog references an unknown column: {name!r}",
        )
    return column


def _key_column(spec: FieldSpec):
    return _expression(spec.key_column)


def _label_column(spec: FieldSpec):
    return _expression(spec.label_column or spec.key_column)


def _code_column(spec: FieldSpec):
    return _expression(spec.code_column) if spec.code_column else None


# ---------------------------------------------------------------------------
# Validated request
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PivotFilter:
    spec: FieldSpec
    operator: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class PivotQuery:
    batch_id: str
    dimensions: tuple[FieldSpec, ...]
    metric: str
    filters: tuple[PivotFilter, ...] = ()
    limit: int = DEFAULT_ROW_LIMIT
    offset: int = 0
    sort: tuple[tuple[str, str], ...] = ()
    time_mode: str = TIME_MODE_ALL
    time_start: Optional[date] = None
    time_end: Optional[date] = None
    subtotals: tuple[str, ...] = ()
    grand_total: bool = True

    @property
    def ascending(self) -> bool:
        return dict(self.sort).get(self.metric) == DIRECTION_ASC

    @property
    def bar_supported(self) -> bool:
        return len(self.dimensions) == 1

    @property
    def bar_hint(self) -> Optional[str]:
        if self.bar_supported:
            return None
        return "横向柱状图暂只支持一个分析维度，请使用表格。"


def _as_sequence(value, label: str) -> Sequence:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _invalid(f"{label} 必须是数组", field=label)
    return value


def _coerce(spec: FieldSpec, raw: Any) -> Any:
    if raw is None:
        return None
    if spec.type == TYPE_NUMBER:
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise _invalid(f"{spec.label} 需要数字", field=spec.key)
    if spec.type == TYPE_BOOLEAN:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"true", "1", "是", "☑", "yes", "y"}:
            return True
        if text in {"false", "0", "否", "☐", "no", "n"}:
            return False
        raise _invalid(f"{spec.label} 需要是/否", field=spec.key)
    if spec.type in (TYPE_DATE, TYPE_MONTH):
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw).strip()[:10])
        except ValueError:
            raise _invalid(f"{spec.label} 需要 YYYY-MM-DD 日期", field=spec.key)
    if spec.type == TYPE_REFERENCE:
        if isinstance(raw, bool):
            raise _invalid(f"{spec.label} 需要引用值", field=spec.key)
        if isinstance(raw, int):
            return raw
        text = str(raw).strip()
        if text.lstrip("-").isdigit():
            return int(text)
        raise _invalid(f"{spec.label} 需要引用值", field=spec.key)
    return str(raw)


def _parse_filter(raw: Any) -> PivotFilter:
    if not isinstance(raw, dict):
        raise _invalid("筛选条件格式不正确")
    unknown = set(raw) - _FILTER_FIELDS
    if unknown:
        raise _invalid(f"筛选条件包含未知字段: {', '.join(sorted(unknown))}")
    key = raw.get("field")
    spec = get_field(key) if isinstance(key, str) else None
    if spec is None or not spec.filterable:
        raise _invalid(f"未知或不可筛选的字段: {key!r}", field=key)
    operator = raw.get("operator", OP_EQ)
    if operator not in spec.operators:
        raise _invalid(
            f"{spec.label} 不支持该匹配方式: {operator!r}",
            field=spec.key,
            allowed=list(spec.operators),
        )
    supplied = [name for name in ("value", "values") if raw.get(name) is not None]
    if not supplied:
        raise _invalid(f"{spec.label} 缺少筛选值", field=spec.key)
    if "values" in supplied:
        tokens = _as_sequence(raw["values"], spec.key)
        values = tuple(_coerce(spec, item) for item in tokens)
    else:
        values = (_coerce(spec, raw["value"]),)
    if operator in (OP_IN,) and not values:
        raise _invalid(f"{spec.label} 的取值列表不能为空", field=spec.key)
    if operator in (OP_GTE, OP_LTE, OP_EQ) and len(values) != 1:
        raise _invalid(f"{spec.label} 该匹配方式只接受一个值", field=spec.key)
    if operator == OP_BETWEEN:
        if len(values) != 2 or values[0] is None or values[1] is None:
            raise _invalid(f"{spec.label} 的时间范围需要起止两个值", field=spec.key)
    return PivotFilter(spec=spec, operator=operator, values=values)


def validate_query(payload: Any) -> PivotQuery:
    """Turn a raw request body into a frozen, fully validated query."""
    if not isinstance(payload, dict):
        raise _invalid("请求体格式不正确")
    unknown = set(payload) - _REQUEST_FIELDS
    if unknown:
        raise _invalid(f"请求包含未知字段: {', '.join(sorted(unknown))}")

    batch_id = payload.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise _invalid("batch_id 必填")

    metrics = list(_as_sequence(payload.get("metrics", [METRIC_ACTUAL_SALES]), "metrics"))
    if metrics != [METRIC_ACTUAL_SALES]:
        raise _invalid(
            "第一版只支持一个指标：实际销量",
            requested=metrics,
            allowed=[METRIC_ACTUAL_SALES],
        )

    raw_dimensions = list(_as_sequence(payload.get("dimensions", []), "dimensions"))
    if len(raw_dimensions) > MAX_GROUP_BY_DIMENSIONS:
        raise _invalid(
            f"分析维度最多 {MAX_GROUP_BY_DIMENSIONS} 个",
            max=MAX_GROUP_BY_DIMENSIONS,
            requested=len(raw_dimensions),
        )
    dimensions: list[FieldSpec] = []
    for item in raw_dimensions:
        spec = get_field(item) if isinstance(item, str) else None
        if spec is None:
            raise _invalid(f"未知的分析字段: {item!r}", field=item)
        if not spec.groupable:
            raise _invalid(f"{spec.label} 不能作为分析维度", field=spec.key)
        if spec.key not in {existing.key for existing in dimensions}:
            dimensions.append(spec)

    raw_filters = list(_as_sequence(payload.get("filters", []), "filters"))
    filters = tuple(_parse_filter(item) for item in raw_filters)

    limit = payload.get("limit", DEFAULT_ROW_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise _invalid("limit 必须是正整数")
    limit = min(limit, MAX_RESULT_ROWS)
    offset = payload.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise _invalid("offset 必须是非负整数")

    sort: list[tuple[str, str]] = []
    for item in _as_sequence(payload.get("sort", []), "sort"):
        if not isinstance(item, dict):
            raise _invalid("排序格式不正确")
        field = item.get("field", METRIC_ACTUAL_SALES)
        if field not in _SORTABLE_FIELDS:
            raise _invalid(f"不支持按该字段排序: {field!r}", field=field,
                           allowed=sorted(_SORTABLE_FIELDS))
        direction = item.get("direction", DIRECTION_DESC)
        if direction not in SORT_DIRECTIONS:
            raise _invalid(f"未知排序方向: {direction!r}")
        sort.append((str(field), direction))

    time_mode, time_start, time_end = _parse_time(payload.get("time"))

    raw_subtotals = list(_as_sequence(payload.get("subtotals", []), "subtotals"))
    dimension_keys = [spec.key for spec in dimensions]
    subtotals: list[str] = []
    for item in raw_subtotals:
        if not isinstance(item, str) or item not in dimension_keys:
            raise _invalid(
                f"小计字段必须是已选分析维度之一: {item!r}", field=item,
                dimensions=dimension_keys,
            )
        if item not in subtotals:
            subtotals.append(item)

    grand_total = payload.get("grand_total", True)
    if not isinstance(grand_total, bool):
        raise _invalid("grand_total 必须是布尔值")

    return PivotQuery(
        batch_id=batch_id.strip(),
        dimensions=tuple(dimensions),
        metric=METRIC_ACTUAL_SALES,
        filters=filters,
        limit=limit,
        offset=offset,
        sort=tuple(sort),
        time_mode=time_mode,
        time_start=time_start,
        time_end=time_end,
        subtotals=tuple(subtotals),
        grand_total=grand_total,
    )


def _parse_time(raw: Any) -> tuple[str, Optional[date], Optional[date]]:
    """Validate the business-time selector. ``created_at`` is never involved."""
    if raw is None:
        return TIME_MODE_ALL, None, None
    if not isinstance(raw, dict):
        raise _invalid("时间口径格式不正确")
    unknown = set(raw) - _TIME_FIELDS
    if unknown:
        raise _invalid(f"时间口径包含未知字段: {', '.join(sorted(unknown))}")
    mode = raw.get("mode", TIME_MODE_ALL)
    if mode not in {TIME_MODE_ALL, TIME_MODE_MTD, TIME_MODE_CUSTOM}:
        raise _invalid(f"未知时间口径: {mode!r}", allowed=[TIME_MODE_ALL, TIME_MODE_MTD, TIME_MODE_CUSTOM])
    if mode != TIME_MODE_CUSTOM:
        return mode, None, None

    def _day(value, label):
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise _invalid(f"{label} 需要 YYYY-MM-DD 日期")

    start = _day(raw.get("start"), "时间口径开始日期")
    end = _day(raw.get("end"), "时间口径结束日期")
    if start and end and start > end:
        raise _invalid("时间口径开始日期晚于结束日期")
    return mode, start, end


# ---------------------------------------------------------------------------
# WHERE clauses
# ---------------------------------------------------------------------------

def _filter_clause(item: PivotFilter):
    column = _key_column(item.spec)
    operator = item.operator
    values = item.values
    if operator == OP_EQ:
        return column.is_(None) if values[0] is None else column == values[0]
    if operator == OP_IN:
        present = [value for value in values if value is not None]
        clauses = [column.in_(present)] if present else []
        if len(present) != len(values):
            clauses.append(column.is_(None))
        return or_(*clauses) if len(clauses) > 1 else clauses[0]
    if operator == OP_GTE:
        return column >= values[0]
    if operator == OP_LTE:
        return column <= values[0]
    if operator == OP_BETWEEN:
        return column.between(values[0], values[1])
    raise _invalid(f"未知匹配方式: {operator!r}")


def _scope_clauses(batch, query: PivotQuery):
    return (*_scope(batch, DashboardFilters()),
            *[_filter_clause(item) for item in query.filters])


# ---------------------------------------------------------------------------
# Join cardinality guard
# ---------------------------------------------------------------------------

def verify_join_cardinality(session, scope) -> dict:
    """Base Fact rows vs. rows after the current-MDM joins (must be equal)."""
    base_rows = session.execute(
        select(func.count()).select_from(_SF.join(_SIR, _SIR.c.id == _SF.c.import_row_id))
        .where(*scope)
    ).scalar_one()
    joined_rows = session.execute(
        select(func.count()).select_from(_base_from()).where(*scope)
    ).scalar_one()
    return {"base_rows": int(base_rows), "joined_rows": int(joined_rows),
            "no_multiplication": int(base_rows) == int(joined_rows)}


def _assert_no_multiplication(cardinality: dict) -> None:
    if not cardinality["no_multiplication"]:
        raise PivotQueryError(
            PIVOT_JOIN_MULTIPLIED,
            "主档 join 发生了行放大，已拒绝返回结果",
            details=cardinality,
        )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _group_expressions(spec: FieldSpec):
    """(key expression, label expression) for one grouped field."""
    key = _key_column(spec)
    label = _label_column(spec)
    return key, label


def _display(value, spec: FieldSpec):
    if value is None:
        return UNASSIGNED_LABEL
    if spec.type in (TYPE_DATE, TYPE_MONTH):
        return value.isoformat()
    if spec.type == TYPE_NUMBER:
        return _decimal(value)
    if spec.type == TYPE_BOOLEAN:
        return "是" if value else "否"
    return str(value)


def _columns_metadata(query: PivotQuery) -> list[dict]:
    columns = []
    for spec in query.dimensions:
        column = {
            "key": spec.key,
            "label": spec.label,
            "kind": "dimension",
            "type": spec.type,
            "value_key": spec.key,
            "origin": spec.origin,
        }
        if spec.type == TYPE_REFERENCE:
            column["raw_key"] = f"{spec.key}__id"
        columns.append(column)
    columns.append({
        "key": METRIC_ACTUAL_SALES,
        "label": "实际销量",
        "kind": "metric",
        "type": TYPE_NUMBER,
        "value_key": METRIC_ACTUAL_SALES,
        "format": "decimal",
        "unit": "Pcs",
    })
    return columns


def _resolve_time(session, batch, query: PivotQuery) -> dict:
    """Resolve the business-time window. MTD never uses the system clock.

    MTD = latest available snapshot month's 1st day → ``MAX(sales_date)`` inside
    that batch.  ``created_at`` is never part of any time口径.
    """
    if query.time_mode == TIME_MODE_MTD:
        start = batch.snapshot_month
        end = session.execute(
            select(func.max(_SF.c.sales_date))
            .select_from(_SF.join(_SIR, _SIR.c.id == _SF.c.import_row_id))
            .where(*_scope(batch, DashboardFilters()))
        ).scalar_one()
        label = f"MTD（{start.isoformat()} ~ {end.isoformat() if end else '—'}）"
        clauses = [_SF.c.sales_date >= start]
        if end is not None:
            clauses.append(_SF.c.sales_date <= end)
        return {"mode": TIME_MODE_MTD, "start": start, "end": end, "label": label,
                "clauses": tuple(clauses)}
    if query.time_mode == TIME_MODE_CUSTOM:
        clauses = []
        if query.time_start is not None:
            clauses.append(_SF.c.sales_date >= query.time_start)
        if query.time_end is not None:
            clauses.append(_SF.c.sales_date <= query.time_end)
        start = query.time_start.isoformat() if query.time_start else "—"
        end = query.time_end.isoformat() if query.time_end else "—"
        return {"mode": TIME_MODE_CUSTOM, "start": query.time_start,
                "end": query.time_end, "label": f"自定义日期（{start} ~ {end}）",
                "clauses": tuple(clauses)}
    return {"mode": TIME_MODE_ALL, "start": batch.snapshot_month, "end": None,
            "label": "全部时间", "clauses": ()}


def _leaf_group_by(query: PivotQuery, specs) -> list:
    group_by = []
    for spec in specs:
        key, label = _group_expressions(spec)
        group_by.append(key)
        if label is not key:
            group_by.append(label)
        if spec.type == TYPE_REFERENCE and spec.code_column:
            group_by.append(_code_column(spec))
    return group_by


def _leaf_selections(query: PivotQuery, specs) -> list:
    selections = []
    for spec in specs:
        key, label = _group_expressions(spec)
        selections.append(key.label(spec.key))
        selections.append(label.label(f"{spec.key}__label"))
        if spec.type == TYPE_REFERENCE and spec.code_column:
            selections.append(_code_column(spec).label(f"{spec.key}__code"))
    return selections


def _aggregate_levels(session, query: PivotQuery, scope, metric_column) -> dict:
    """One grouped aggregation per prefix length (1..n).

    Coarser levels (used for parent ordering and subtotals) are **direct
    aggregations**, so a truncated leaf list can never corrupt a subtotal.
    """
    direction = func.sum(metric_column).asc() if query.ascending else func.sum(metric_column).desc()
    levels = {}
    for length in range(1, len(query.dimensions) + 1):
        specs = query.dimensions[:length]
        statement = (
            select(*_leaf_selections(query, specs), func.sum(metric_column).label("__metric"))
            .select_from(_base_from())
            .where(*scope)
            .group_by(*_leaf_group_by(query, specs))
            .order_by(direction)
        )
        levels[length] = [dict(row) for row in session.execute(statement).mappings().all()]
    return levels


def _display_cell(spec: FieldSpec, raw, label) -> str:
    value = label if label is not None else raw
    return _display(value, spec)


def _identity(spec: FieldSpec, group: dict) -> tuple:
    """SQL GROUP BY identity of one level: (key, label)."""
    return (group[spec.key], group.get(f"{spec.key}__label"))


def _build_hierarchy(query: PivotQuery, levels: dict, grand_total_value: str) -> tuple:
    """Assemble the TABULAR pivot rows (CR02A §7).

    Layout: leaf rows carry the whole dimension path, but a parent value is only
    written into the first leaf row of its group; later leaves keep that cell
    empty.  There is deliberately **no** separate parent row with a metric — the
    only aggregates rendered are 小计 (direct per-parent aggregation) and 合计.

    Row identity uses the composite ``(key, label)`` tuple, matching the SQL
    GROUP BY, so the same surrogate key with a different snapshot label produces
    two distinct historical groups instead of colliding.
    """
    rows: list[dict] = []
    subtotal_rows: list[dict] = []
    depth_total = len(query.dimensions)
    subtotal_keys = set(query.subtotals)
    previous_identity: list = []

    def emit_leaf(path_identity: tuple, group: dict, metric: str) -> None:
        path_display = []
        cells = []
        for index, spec in enumerate(query.dimensions):
            display = _display_cell(spec, group[spec.key], group[f"{spec.key}__label"])
            path_display.append(display)
            repeated = (
                bool(previous_identity)
                and tuple(previous_identity[: index + 1]) == path_identity[: index + 1]
            )
            cells.append("" if repeated else display)
        previous_identity[:] = list(path_identity)
        rows.append({
            "kind": "leaf",
            "depth": depth_total - 1,
            "path": path_display,
            "cells": cells,
            "metric": metric,
        })

    def walk(level: int, prefix: tuple) -> None:
        if level >= depth_total:
            return
        spec = query.dimensions[level]
        for group in levels[level + 1]:
            # Composite hierarchy identity = (key, label) of every level, exactly
            # like the SQL GROUP BY.  Two facts with the same surrogate key but a
            # different snapshot label stay two distinct historical groups.
            identity = tuple(
                _identity(item, group) for item in query.dimensions[: level + 1]
            )
            if identity[:level] != prefix:
                continue
            value = _display_cell(spec, group[spec.key], group[f"{spec.key}__label"])
            metric = _decimal(group["__metric"])
            if level + 1 == depth_total:
                emit_leaf(identity, group, metric)
            else:
                walk(level + 1, identity)
            if spec.key in subtotal_keys:
                entry = {
                    "kind": "subtotal", "depth": level, "dimension": spec.key,
                    "value": value, "label": f"{value} 小计", "metric": metric,
                }
                rows.append(entry)
                subtotal_rows.append(entry)

    if depth_total:
        walk(0, ())

    if query.grand_total:
        rows.append({
            "kind": "total", "depth": -1, "dimension": None,
            "value": "合计", "label": "合计", "metric": grand_total_value,
        })
    return rows, subtotal_rows


def run_query(session_factory, query: PivotQuery) -> dict:
    with session_factory() as session:
        batch = _validated_batch(session, query.batch_id)
        # Row multiplication is a property of the join graph, not of the filters:
        # a filter can only remove rows, never duplicate them.  The guard therefore
        # compares base Fact rows and joined rows over the whole batch scope.
        batch_scope = _scope(batch, DashboardFilters())
        cardinality = verify_join_cardinality(session, batch_scope)
        _assert_no_multiplication(cardinality)

        time_info = _resolve_time(session, batch, query)
        scope = (*_scope_clauses(batch, query), *time_info["clauses"])
        metric_column = _METRICS[query.metric]

        # Leaf rows (limited) drive the flat `items` list, the bar chart and the
        # detail rows of the hierarchy.
        statement = (
            select(*_leaf_selections(query, query.dimensions),
                   func.sum(metric_column).label(query.metric))
            .select_from(_base_from())
            .where(*scope)
        )
        if query.dimensions:
            statement = statement.group_by(*_leaf_group_by(query, query.dimensions))
        order_by = [func.sum(metric_column).asc() if query.ascending
                    else func.sum(metric_column).desc()]
        for spec in query.dimensions:
            key, _ = _group_expressions(spec)
            order_by.append(key.is_(None))
            order_by.append(key.asc())
        # Result bound (CR02A §2): count the leaf combinations first and refuse the
        # whole request above the bound.  Never silently truncate: a Pivot that
        # shows 500 leaves while the subtotals carry the full total would look
        # complete but be wrong.
        count_statement = (
            select(func.count()).select_from(
                select(*[_key_column(spec).label(spec.key) for spec in query.dimensions])
                .select_from(_base_from())
                .where(*scope)
                .group_by(*[_key_column(spec) for spec in query.dimensions])
                .subquery()
            )
            if query.dimensions
            else select(func.count()).select_from(_base_from()).where(*scope)
        )
        total_group_rows = int(session.execute(count_statement).scalar_one())
        if total_group_rows > MAX_LEAF_GROUPS:
            raise PivotQueryError(
                PIVOT_RESULT_TOO_LARGE,
                RESULT_TOO_LARGE_MESSAGE,
                details={
                    "leaf_groups": total_group_rows,
                    "max_leaf_groups": MAX_LEAF_GROUPS,
                    "requested_dimensions": [spec.key for spec in query.dimensions],
                },
            )

        leaf_statement = statement.order_by(*order_by).limit(MAX_LEAF_GROUPS)
        leaf_rows = [dict(row) for row in session.execute(leaf_statement).mappings().all()]

        def _item(row) -> dict:
            item: dict[str, Any] = {}
            for spec in query.dimensions:
                raw = row[spec.key]
                label = row[f"{spec.key}__label"]
                item[spec.key] = _display(label if label is not None else raw, spec)
                if spec.type == TYPE_REFERENCE:
                    item[f"{spec.key}__id"] = raw
                if spec.type == TYPE_REFERENCE and spec.code_column:
                    item[f"{spec.key}__code"] = row[f"{spec.key}__code"]
            item[query.metric] = _decimal(row[query.metric])
            return item

        items = [_item(row) for row in leaf_rows[query.offset: query.offset + query.limit]]

        grand_total_value = _decimal(
            session.execute(
                select(func.sum(metric_column)).select_from(_base_from()).where(*scope)
            ).scalar_one()
        )

        levels = _aggregate_levels(session, query, scope, metric_column) if query.dimensions else {}
        rows, subtotal_rows = _build_hierarchy(query, levels, grand_total_value)

        return {
            "batch_id": batch.batch_id,
            "dataset": DATASET_SALES_PIVOT,
            "columns": _columns_metadata(query),
            "items": items,
            "rows": rows,
            "time": {
                "mode": time_info["mode"],
                "start": time_info["start"].isoformat() if time_info["start"] else None,
                "end": time_info["end"].isoformat() if time_info["end"] else None,
                "label": time_info["label"],
            },
            "totals": {
                "grand_total": grand_total_value,
                "grand_total_label": "合计",
                "subtotals": [
                    {"dimension": row["dimension"], "value": row["value"],
                     "label": row["label"], "metric": row["metric"]}
                    for row in subtotal_rows
                ],
                "subtotal_fields": list(query.subtotals),
                "grand_total_enabled": query.grand_total,
            },
            "pagination": {
                "offset": query.offset,
                "limit": query.limit,
                "row_count": len(items),
                "total_group_rows": total_group_rows,
                "truncated": query.offset + len(items) < total_group_rows,
                "hierarchy_truncated": False,
                "leaf_group_count": total_group_rows,
                "max_result_rows": MAX_RESULT_ROWS,
            },
            "display": {
                "default": "table",
                "bar_supported": query.bar_supported,
                "bar_hint": query.bar_hint,
            },
            "plan": {
                "base": "sales_fact",
                "joins": [_join_label()],
                "dimensions": [spec.key for spec in query.dimensions],
                "metric": query.metric,
                "subtotals": list(query.subtotals),
                "grand_total": query.grand_total,
                "time_mode": time_info["mode"],
                "filters": [
                    {"field": item.spec.key, "operator": item.operator,
                     "values": [None if value is None else str(value) for value in item.values]}
                    for item in query.filters
                ],
                "join_cardinality": cardinality,
                "snapshot_notice": CURRENT_MDM_NOTICE,
            },
            "scope": {
                "batch_id": batch.batch_id,
                "snapshot_month": batch.snapshot_month.isoformat(),
                "attribution": "CURRENT_MDM",
            },
        }


def _join_label() -> str:
    return "sales_fact → mdm_sku → mdm_product / mdm_customer → mdm_channel · mdm_salesrep · mdm_region · mdm_province"


# ---------------------------------------------------------------------------
# Field availability + filter options
# ---------------------------------------------------------------------------

def field_availability(session_factory, batch_id: str) -> dict:
    """Which catalog fields carry at least one non-null value in this batch."""
    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        scope = _scope(batch, DashboardFilters())
        selections = []
        for spec in _CATALOG_FIELDS:
            column = _key_column(spec)
            selections.append(
                func.max(case((column.isnot(None), 1), else_=0)).label(spec.key)
            )
        row = session.execute(
            select(*selections).select_from(_base_from()).where(*scope)
        ).mappings().one()
        return {spec.key: bool(row[spec.key]) for spec in _CATALOG_FIELDS}


def get_filter_options(session_factory, batch_id: str, keys: Sequence[str]) -> dict:
    """Distinct values of the requested fields inside the current batch scope."""
    specs: list[FieldSpec] = []
    for key in keys:
        spec = get_field(key)
        if spec is None:
            raise _invalid(f"未知的分析字段: {key!r}", field=key)
        if not spec.filterable:
            raise _invalid(f"{spec.label} 不支持筛选", field=spec.key)
        if spec.key not in {item.key for item in specs}:
            specs.append(spec)
    if len(specs) > 12:
        raise _invalid("一次最多读取 12 个字段的筛选值", max=12)

    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        scope = _scope(batch, DashboardFilters())
        options: dict[str, list] = {}
        for spec in specs:
            key = _key_column(spec)
            label = _label_column(spec)
            selections = [key.label("key")]
            selections.append(label.label("label"))
            rows = session.execute(
                select(*selections)
                .select_from(_base_from())
                .where(*scope)
                .group_by(key, label)
                .order_by(key.is_(None), key.asc())
                .limit(500)
            ).mappings().all()
            values = []
            for row in rows:
                raw = row["key"]
                display = row["label"] if row["label"] is not None else raw
                values.append({
                    "key": raw.isoformat() if isinstance(raw, date) else raw,
                    "label": _display(display, spec),
                })
            options[spec.key] = values
        return {
            "batch_id": batch.batch_id,
            "options": options,
            "attribution": "CURRENT_MDM",
        }
