"""Generic Metric + Dimension + Filter query engine for ACT Sales (Spike).

The engine turns a validated semantic request into one SQLAlchemy ``GROUP BY``
over the Published Sales Fact snapshot.  It never accepts SQL, a model
attribute, a column name or an expression from the client: every request field is
resolved through the server-side registries and a fixed, module-private mapping
from physical column names to model attributes.

Guardrails enforced here (all of them, regardless of caller):

* dataset / metric / dimension / filter whitelists
* at most ``DatasetSpec.max_group_by`` group-by dimensions
* at most ``DatasetSpec.max_rows`` result rows, with an explicit ``limit`` cap
* deterministic ordering: requested metric first, then every group key ascending
* only the current Published (and not replaced) batch is readable
* snapshot columns only — MDM is never joined, so historical attribution keeps
  the identity captured at Publish time

The same batch-scoping rules as ``dashboard_query_service`` are reused rather
than re-implemented: ``_validated_batch`` already proves that the requested
``batch_id`` is the single current Published batch for its month and that Fact
counters agree with the batch totals.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import func, or_, select

from webapp.sales.dashboard_query_service import (
    DashboardFilters,
    DashboardQueryError,
    _decimal,
    _fact_select,
    _scope,
    _validated_batch,
)
from webapp.sales.models import SalesFact
from webapp.sales.semantic_registry import (
    DATASET_SALES_ACTUAL,
    DIRECTION_ASC,
    DIRECTION_DESC,
    OP_BETWEEN,
    OP_EQ,
    OP_GTE,
    OP_IN,
    OP_LTE,
    SORT_DIRECTIONS,
    UNASSIGNED_LABEL,
    DatasetSpec,
    DimensionSpec,
    MetricSpec,
    get_dataset,
)


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------

SEMANTIC_QUERY_INVALID = "SEMANTIC_QUERY_INVALID"


class SemanticQueryError(ValueError):
    """A request the registry cannot express. Carries a stable machine code."""

    def __init__(self, code: str, message: str, *, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def payload(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _invalid(message: str, **details) -> SemanticQueryError:
    return SemanticQueryError(SEMANTIC_QUERY_INVALID, message, details=details)


# ---------------------------------------------------------------------------
# Physical column allow-list
# ---------------------------------------------------------------------------

# Maps the plain column names used by the registry onto model attributes.  A
# registry entry that names anything not listed here fails as a server-side
# misconfiguration instead of silently reaching the database.
_FACT_COLUMNS = {
    "actual_qty": SalesFact.actual_qty,
    "product_id": SalesFact.product_id,
    "product_name_snapshot": SalesFact.product_name_snapshot,
    "sku_id": SalesFact.sku_id,
    "sku_code_snapshot": SalesFact.sku_code_snapshot,
    "sku_name_snapshot": SalesFact.sku_name_snapshot,
    "customer_id": SalesFact.customer_id,
    "channel_id_snapshot": SalesFact.channel_id_snapshot,
    "channel_name_snapshot": SalesFact.channel_name_snapshot,
    "salesrep_id_snapshot": SalesFact.salesrep_id_snapshot,
    "salesrep_name_snapshot": SalesFact.salesrep_name_snapshot,
    "sales_date": SalesFact.sales_date,
    "snapshot_month": SalesFact.snapshot_month,
}


def _column(name: str):
    column = _FACT_COLUMNS.get(name)
    if column is None:
        raise SemanticQueryError(
            "SEMANTIC_REGISTRY_INVALID",
            f"registry references an unknown fact column: {name!r}",
        )
    return column


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticFilter:
    dimension: str
    operator: str
    values: tuple


@dataclass(frozen=True)
class SemanticSort:
    field: str
    direction: str = DIRECTION_DESC


@dataclass(frozen=True)
class SemanticQuery:
    dataset: str
    batch_id: str
    metrics: tuple
    dimensions: tuple = ()
    filters: tuple = ()
    sort: tuple = ()
    limit: Optional[int] = None
    offset: int = 0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _require_dataset(dataset_key: str) -> DatasetSpec:
    dataset = get_dataset(dataset_key)
    if dataset is None:
        raise _invalid(
            f"unknown dataset: {dataset_key!r}",
            dataset=dataset_key,
            allowed=sorted(get_dataset_keys()),
        )
    return dataset


def get_dataset_keys() -> Sequence[str]:
    from webapp.sales.semantic_registry import get_all_datasets
    return tuple(get_all_datasets().keys())


def _require_metric(dataset: DatasetSpec, key: Any) -> MetricSpec:
    if not isinstance(key, str):
        raise _invalid("metric keys must be strings", metric=repr(key))
    metric = dataset.metric(key)
    if metric is None:
        raise _invalid(
            f"unknown metric: {key!r}",
            metric=key,
            allowed=[m.key for m in dataset.metrics],
        )
    return metric


def _require_dimension(dataset: DatasetSpec, key: Any, *, purpose: str) -> DimensionSpec:
    if not isinstance(key, str):
        raise _invalid(f"{purpose} keys must be strings", dimension=repr(key))
    dimension = dataset.dimension(key)
    if dimension is None:
        raise _invalid(
            f"unknown dimension: {key!r}",
            dimension=key,
            purpose=purpose,
            allowed=[d.key for d in dataset.dimensions],
        )
    return dimension


def _coerce_filter_values(dimension: DimensionSpec, operator: str, values) -> tuple:
    from datetime import date

    if operator == OP_IN:
        if not isinstance(values, (list, tuple)) or not values:
            raise _invalid(
                "'in' filters need a non-empty values list", dimension=dimension.key
            )
    elif operator == OP_BETWEEN:
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise _invalid(
                "'between' filters need exactly two values", dimension=dimension.key
            )
    else:
        if isinstance(values, (list, tuple)):
            raise _invalid(
                f"'{operator}' filters take a single value", dimension=dimension.key
            )

    raw = list(values) if isinstance(values, (list, tuple)) else [values]
    coerced = []
    for value in raw:
        coerced.append(_coerce_scalar(dimension, value))
    if operator == OP_BETWEEN and coerced[0] > coerced[1]:
        raise _invalid(
            "'between' bounds are reversed", dimension=dimension.key
        )
    return tuple(coerced)


def _coerce_scalar(dimension: DimensionSpec, value):
    from datetime import date, datetime

    if value is None:
        if not dimension.nullable:
            raise _invalid(
                f"{dimension.key} has no unassigned value", dimension=dimension.key
            )
        return None
    if dimension.type in ("integer", "decimal"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise _invalid(
                f"{dimension.key} expects a numeric value", dimension=dimension.key
            )
        try:
            number = Decimal(str(value))
        except Exception:
            raise _invalid(
                f"{dimension.key} expects a numeric value", dimension=dimension.key
            )
        return int(number) if dimension.type == "integer" else number
    if dimension.type in ("date", "month"):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                raise _invalid(
                    f"{dimension.key} expects an ISO date (YYYY-MM-DD)",
                    dimension=dimension.key,
                )
        raise _invalid(
            f"{dimension.key} expects an ISO date (YYYY-MM-DD)",
            dimension=dimension.key,
        )
    if isinstance(value, (list, tuple, dict)):
        raise _invalid(
            f"{dimension.key} expects a scalar value", dimension=dimension.key
        )
    return str(value)


def _require_filter(dataset: DatasetSpec, item: Any) -> SemanticFilter:
    if not isinstance(item, dict):
        raise _invalid("each filter must be an object")
    unknown = set(item) - {"dimension", "field", "operator", "op", "values", "value"}
    if unknown:
        raise _invalid(
            "unsupported filter fields", fields=sorted(unknown)
        )
    key = item.get("dimension", item.get("field"))
    dimension = _require_dimension(dataset, key, purpose="filter")
    if not dimension.filterable:
        raise _invalid(
            f"{dimension.key} is not filterable", dimension=dimension.key
        )
    operator = item.get("operator", item.get("op"))
    if operator not in dimension.operators:
        raise _invalid(
            f"unsupported operator for {dimension.key}: {operator!r}",
            dimension=dimension.key,
            operator=operator,
            allowed=list(dimension.operators),
        )
    if "values" in item and "value" in item:
        raise _invalid("send either value or values, not both", dimension=dimension.key)
    payload = item["values"] if "values" in item else item.get("value")
    return SemanticFilter(
        dimension=dimension.key,
        operator=operator,
        values=_coerce_filter_values(dimension, operator, payload),
    )


def _require_sort(dataset: DatasetSpec, item: Any, metric_keys, dimension_keys) -> SemanticSort:
    if not isinstance(item, dict):
        raise _invalid("each sort entry must be an object")
    unknown = set(item) - {"field", "direction"}
    if unknown:
        raise _invalid("unsupported sort fields", fields=sorted(unknown))
    field = item.get("field")
    direction = item.get("direction", DIRECTION_DESC)
    if direction not in SORT_DIRECTIONS:
        raise _invalid(
            f"unsupported sort direction: {direction!r}",
            direction=direction,
            allowed=sorted(SORT_DIRECTIONS),
        )
    if field in metric_keys:
        return SemanticSort(field=field, direction=direction)
    if field in dimension_keys:
        dimension = dataset.dimension(field)
        if not dimension.sortable:
            raise _invalid(f"{field} is not sortable", field=field)
        return SemanticSort(field=field, direction=direction)
    raise _invalid(
        f"cannot sort by: {field!r}",
        field=field,
        allowed_sort=[*metric_keys, *dimension_keys],
    )


def validate_query(request: dict, *, dataset_key: Optional[str] = None) -> SemanticQuery:
    """Validate an untrusted request body and freeze it into a SemanticQuery."""
    if not isinstance(request, dict):
        raise _invalid("request body must be an object")
    unknown = set(request) - {
        "dataset", "batch_id", "metrics", "dimensions", "filters", "sort",
        "limit", "offset",
    }
    if unknown:
        raise _invalid("unsupported request fields", fields=sorted(unknown))

    dataset = _require_dataset(request.get("dataset", dataset_key or DATASET_SALES_ACTUAL))

    batch_id = request.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise _invalid("batch_id is required")

    raw_metrics = request.get("metrics")
    if not isinstance(raw_metrics, (list, tuple)) or not raw_metrics:
        raise _invalid("at least one metric is required")
    if len(raw_metrics) > len(dataset.metrics):
        raise _invalid("too many metrics requested")
    metrics = []
    for key in raw_metrics:
        metric = _require_metric(dataset, key)
        if metric.key not in [m.key for m in metrics]:
            metrics.append(metric)
    if not metrics:
        raise _invalid("at least one metric is required")

    raw_dimensions = request.get("dimensions", [])
    if raw_dimensions is None:
        raw_dimensions = []
    if not isinstance(raw_dimensions, (list, tuple)):
        raise _invalid("dimensions must be a list")
    dimensions = []
    for key in raw_dimensions:
        dimension = _require_dimension(dataset, key, purpose="group by")
        if not dimension.groupable:
            raise _invalid(f"{dimension.key} is not groupable", dimension=dimension.key)
        if dimension.key not in [d.key for d in dimensions]:
            dimensions.append(dimension)
    if len(dimensions) > dataset.max_group_by:
        raise _invalid(
            f"at most {dataset.max_group_by} group-by dimensions are supported",
            requested=[d.key for d in dimensions],
            max_group_by=dataset.max_group_by,
        )

    raw_filters = request.get("filters", [])
    if raw_filters is None:
        raw_filters = []
    if not isinstance(raw_filters, (list, tuple)):
        raise _invalid("filters must be a list")
    if len(raw_filters) > len(dataset.dimensions):
        raise _invalid("too many filters requested")
    filters = [_require_filter(dataset, item) for item in raw_filters]

    raw_sort = request.get("sort", [])
    if raw_sort is None:
        raw_sort = []
    if not isinstance(raw_sort, (list, tuple)):
        raise _invalid("sort must be a list")
    metric_keys = [m.key for m in metrics]
    dimension_keys = [d.key for d in dimensions]
    sort = [_require_sort(dataset, item, metric_keys, dimension_keys) for item in raw_sort]

    limit = request.get("limit", dataset.default_row_limit)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise _invalid("limit must be an integer")
    if limit < 1 or limit > dataset.max_rows:
        raise _invalid(
            f"limit must be between 1 and {dataset.max_rows}",
            limit=limit,
            max_rows=dataset.max_rows,
        )

    offset = request.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise _invalid("offset must be a non-negative integer")

    return SemanticQuery(
        dataset=dataset.key,
        batch_id=batch_id.strip(),
        metrics=tuple(metrics),
        dimensions=tuple(dimensions),
        filters=tuple(filters),
        sort=tuple(sort),
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Expression building
# ---------------------------------------------------------------------------

def _filter_expression(item: SemanticFilter):
    dimension = _require_dimension(_require_dataset(DATASET_SALES_ACTUAL), item.dimension, purpose="filter")
    column = _filter_column(dimension)
    values = item.values
    if item.operator == OP_EQ:
        return column.is_(None) if values[0] is None else column == values[0]
    if item.operator == OP_IN:
        concrete = [value for value in values if value is not None]
        clauses = []
        if concrete:
            clauses.append(column.in_(concrete))
        if None in values:
            clauses.append(column.is_(None))
        return or_(*clauses)
    if item.operator == OP_GTE:
        return column >= values[0]
    if item.operator == OP_LTE:
        return column <= values[0]
    if item.operator == OP_BETWEEN:
        return column.between(values[0], values[1])
    raise _invalid(f"unsupported operator: {item.operator!r}")


def _filter_column(dimension: DimensionSpec):
    """Filters always target the snapshot column that supplies the group key."""
    if dimension.group_strategy in ("snapshot_id", "snapshot_code", "snapshot_text"):
        name = dimension.id_column or dimension.code_column or dimension.label_column
        if dimension.group_strategy == "snapshot_code":
            name = dimension.code_column or name
        return _column(name)
    return _column(dimension.key_column)


def _group_columns(dimension: DimensionSpec):
    """Return (key_column, label_column_or_None) for a dimension's group key."""
    if dimension.group_strategy == "fact_column":
        return _column(dimension.key_column), None
    if dimension.group_strategy == "snapshot_id":
        label = dimension.label_column or dimension.code_column
        return _column(dimension.id_column), (_column(label) if label else None)
    if dimension.group_strategy == "snapshot_code":
        label = dimension.label_column or dimension.code_column
        return _column(dimension.code_column), (_column(label) if label else None)
    return _column(dimension.label_column), None


def _metric_expression(metric: MetricSpec):
    if metric.aggregation == "sum":
        return func.sum(_column(metric.column))
    raise SemanticQueryError(
        "SEMANTIC_REGISTRY_INVALID",
        f"unsupported aggregation: {metric.aggregation!r}",
    )


def describe_plan(query: SemanticQuery) -> dict:
    """A reviewable, SQL-free description of what the engine will execute.

    Used by the Architecture tab.  No SQL text, no model objects: only the
    registry keys and the resolved group/filter/sort decisions.
    """
    return {
        "dataset": query.dataset,
        "source_table": "sales_fact JOIN sales_import_row",
        "snapshot_semantics": "FACT_SNAPSHOT_ONLY",
        "mcp_note": "no MDM join; historical attribution is never re-derived",
        "aggregations": [
            {
                "metric": metric.key,
                "expression": f"{metric.aggregation.upper()}({metric.column})",
                "as": metric.key,
            }
            for metric in query.metrics
        ],
        "group_by": [
            {
                "dimension": dimension.key,
                "strategy": dimension.group_strategy,
                "key_column": (dimension.id_column or dimension.code_column
                               or dimension.key_column or dimension.label_column),
                "label_column": dimension.label_column if dimension.group_strategy != "fact_column" else None,
            }
            for dimension in query.dimensions
        ],
        "filters": [
            {
                "dimension": item.dimension,
                "operator": item.operator,
                "values": [value.isoformat() if hasattr(value, "isoformat") else value
                           for value in item.values],
            }
            for item in query.filters
        ],
        "order_by": [
            {"field": item.field, "direction": item.direction}
            for item in query.sort
        ] or [{"field": query.metrics[0].key, "direction": DIRECTION_DESC}],
        "stable_tiebreak": "all group-key columns ascending",
        "limit": query.limit,
        "offset": query.offset,
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _row_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return _decimal(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def run_query(session_factory, query: SemanticQuery) -> dict:
    """Execute a validated SemanticQuery against the current Published batch."""
    with session_factory() as session:
        batch = _validated_batch(session, query.batch_id)

        # (dimension, key_column, label_field_name or None, label_column or None)
        group_specs = []
        for dimension in query.dimensions:
            key_col, label_col = _group_columns(dimension)
            label_field = None
            if label_col is not None and label_col is not key_col:
                label_field = f"{dimension.key}_name"
            group_specs.append((dimension, key_col, label_field, label_col))

        metric_fields = {metric.key: metric.key for metric in query.metrics}

        select_columns = []
        group_by_columns = []
        for dimension, key_col, label_field, label_col in group_specs:
            select_columns.append(key_col.label(dimension.key))
            group_by_columns.append(key_col)
            if label_field:
                select_columns.append(
                    func.coalesce(label_col, UNASSIGNED_LABEL).label(label_field)
                )
                group_by_columns.append(label_col)
        for metric in query.metrics:
            select_columns.append(_metric_expression(metric).label(metric_fields[metric.key]))

        # _scope() keeps the shared batch/snapshot scoping from the production
        # service; semantic filters are appended as ordinary clauses.
        scope = _scope(
            batch,
            DashboardFilters(),
            *[_filter_expression(item) for item in query.filters],
        )

        groups = (
            _fact_select(*select_columns)
            .where(*scope)
            .group_by(*group_by_columns)
        ).subquery()

        # Grand total over the same scope, without grouping. This is the value
        # that must equal the production dashboard total for the same filters.
        denominator = session.scalar(
            _fact_select(func.coalesce(func.sum(SalesFact.actual_qty), 0)).where(*scope)
        )
        total_group_rows = session.scalar(select(func.count()).select_from(groups)) or 0

        order_by = []
        if query.sort:
            for item in query.sort:
                column = groups.c[metric_fields.get(item.field, item.field)]
                order_by.append(
                    column.asc() if item.direction == DIRECTION_ASC else column.desc()
                )
        else:
            order_by.append(groups.c[metric_fields[query.metrics[0].key]].desc())
        # Deterministic tiebreak: every group key ascending.
        for dimension, _, _, _ in group_specs:
            order_by.append(groups.c[dimension.key].asc())

        rows = session.execute(
            select(groups)
            .order_by(*order_by)
            .offset(query.offset)
            .limit(query.limit)
        ).mappings().all()

        items = []
        for row in rows:
            item = {}
            for dimension, _, label_field, _ in group_specs:
                item[dimension.key] = _row_value(row[dimension.key])
                if label_field:
                    value = row[label_field]
                    item[label_field] = value if value is not None else UNASSIGNED_LABEL
            for metric in query.metrics:
                item[metric.key] = _decimal(row[metric_fields[metric.key]])
            items.append(item)

        truncated = (query.offset + len(items)) < int(total_group_rows)
        return {
            "dataset": query.dataset,
            "batch_id": batch.batch_id,
            "snapshot_month": batch.snapshot_month.isoformat(),
            "source_system": batch.source_system,
            "metrics": [metric.to_public_dict() for metric in query.metrics],
            "dimensions": [
                {
                    **dimension.to_public_dict(),
                    "result_field": dimension.key,
                    "label_field": label_field,
                }
                for dimension, _, label_field, _ in group_specs
            ],
            "items": items,
            "total_qty": _decimal(denominator),
            "plan": describe_plan(query),
            "pagination": {
                "offset": query.offset,
                "limit": query.limit,
                "total_group_rows": int(total_group_rows),
                "returned_rows": len(items),
                "truncated": truncated,
                "max_result_rows": _max_rows_for(query.dataset),
            },
        }


def _max_rows_for(dataset_key: str) -> int:
    dataset = get_dataset(dataset_key)
    return dataset.max_rows if dataset else 0


# ---------------------------------------------------------------------------
# Filter option discovery (snapshot-only, no MDM join)
# ---------------------------------------------------------------------------

def get_filter_options(session_factory, batch_id: str, dimensions: Sequence[str] = ()) -> dict:
    """Distinct snapshot values per requested dimension, optional NULL included.

    Reads the Fact snapshot columns only, so the option list always matches the
    attribution actually stored on the rows.
    """
    from webapp.sales.dashboard_query_service import DashboardFilters

    dataset = _require_dataset(DATASET_SALES_ACTUAL)
    requested = list(dimensions) or [
        d.key for d in dataset.dimensions
        if d.role == "reference" or d.role in ("date", "month")
    ]
    specs = []
    for key in requested:
        dimension = _require_dimension(dataset, key, purpose="filter option")
        if not dimension.filterable:
            raise _invalid(f"{dimension.key} is not filterable", dimension=dimension.key)
        specs.append(dimension)

    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        scope = _scope(batch, DashboardFilters())
        options = {}
        for dimension in specs:
            key_col, label_col = _group_columns(dimension)
            columns = [key_col.label("key")]
            if label_col is not None and label_col is not key_col:
                columns.append(label_col.label("label"))
            else:
                columns.append(key_col.label("label"))
            rows = session.execute(
                _fact_select(*columns)
                .where(*scope)
                .distinct()
                .order_by(key_col.asc())
            ).mappings().all()
            options[dimension.key] = [
                {
                    "key": _row_value(row["key"]),
                    "label": row["label"] if row["label"] is not None else UNASSIGNED_LABEL,
                }
                for row in rows
            ]
        return {
            "batch_id": batch.batch_id,
            "dataset": DATASET_SALES_ACTUAL,
            "dimensions": [dimension.to_public_dict() for dimension in specs],
            "options": options,
        }
