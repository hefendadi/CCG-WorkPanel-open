"""Read-only Dashboard queries over the current Published Sales Fact snapshot.

Every data query resolves one public ``batch_id`` to the single current batch,
checks the whole-month Fact counters, and binds Facts back to that batch through
``sales_import_row`` lineage.  Snapshot dimensions on Fact are authoritative;
Candidate and MDM tables are intentionally outside this service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func, or_, select

from webapp.sales.models import (
    SalesBatchStatus,
    SalesFact,
    SalesImportBatch,
    SalesImportRow,
)


CURRENT_BATCH_AMBIGUOUS = "CURRENT_BATCH_AMBIGUOUS"
DASHBOARD_BATCH_NOT_FOUND = "DASHBOARD_BATCH_NOT_FOUND"
DASHBOARD_FACT_INTEGRITY_ERROR = "DASHBOARD_FACT_INTEGRITY_ERROR"
DASHBOARD_SNAPSHOT_CHANGED = "DASHBOARD_SNAPSHOT_CHANGED"
UNASSIGNED = "未归属"


class DashboardQueryError(Exception):
    def __init__(self, code: str, *, payload=None):
        self.code = code
        self.payload = payload or {"code": code}
        super().__init__(code)


class CurrentBatchAmbiguous(DashboardQueryError):
    def __init__(self, source_system: str, snapshot_month: date, batch_ids: Sequence[str]):
        super().__init__(
            CURRENT_BATCH_AMBIGUOUS,
            payload={
                "code": CURRENT_BATCH_AMBIGUOUS,
                "source_system": source_system,
                "snapshot_month": snapshot_month.isoformat(),
                "batch_ids": list(batch_ids),
            },
        )


class DashboardBatchNotFound(DashboardQueryError):
    def __init__(self, batch_id: str):
        super().__init__(
            DASHBOARD_BATCH_NOT_FOUND,
            payload={
                "code": DASHBOARD_BATCH_NOT_FOUND,
                "batch_id": batch_id,
            },
        )


class DashboardSnapshotChanged(DashboardQueryError):
    def __init__(self, requested_batch_id: str, current_batch_id: Optional[str]):
        super().__init__(
            DASHBOARD_SNAPSHOT_CHANGED,
            payload={
                "code": DASHBOARD_SNAPSHOT_CHANGED,
                "requested_batch_id": requested_batch_id,
                "current_batch_id": current_batch_id,
            },
        )


class DashboardFactIntegrityError(DashboardQueryError):
    def __init__(self, payload):
        super().__init__(
            DASHBOARD_FACT_INTEGRITY_ERROR,
            payload={"code": DASHBOARD_FACT_INTEGRITY_ERROR, **payload},
        )


@dataclass(frozen=True)
class DashboardFilters:
    """Fact-snapshot filters. ``None`` inside channel/rep IDs means 未归属."""

    product_ids: tuple[int, ...] = ()
    sku_ids: tuple[int, ...] = ()
    channel_ids: tuple[Optional[int], ...] = ()
    salesrep_ids: tuple[Optional[int], ...] = ()


def _decimal(value) -> str:
    return format(Decimal(value or 0), "f")


def _share(qty, denominator) -> str:
    numerator = Decimal(qty or 0)
    total = Decimal(denominator or 0)
    if not total:
        return "0.000000"
    return format((numerator / total).quantize(Decimal("0.000001")), "f")


def _batch_payload(row) -> dict:
    return {
        "batch_id": row.batch_id,
        "source_system": row.source_system,
        "snapshot_month": row.snapshot_month.isoformat(),
        "data_date_start": row.data_date_start.isoformat() if row.data_date_start else None,
        "data_date_end": row.data_date_end.isoformat() if row.data_date_end else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "ready_rows": row.ready_rows,
        "ready_qty": _decimal(row.ready_qty),
    }


def _current_rows(session, source_system: str, snapshot_month: date):
    return session.scalars(
        select(SalesImportBatch)
        .where(
            SalesImportBatch.source_system == source_system,
            SalesImportBatch.snapshot_month == snapshot_month,
            SalesImportBatch.status == SalesBatchStatus.PUBLISHED,
            SalesImportBatch.replaced_at.is_(None),
        )
        .order_by(SalesImportBatch.id)
    ).all()


def select_current_batch(session_factory, source_system: str, snapshot_month: date):
    """Return the only current Published batch for a source/month, or ``None``."""
    with session_factory() as session:
        rows = _current_rows(session, source_system, snapshot_month)
        if len(rows) > 1:
            raise CurrentBatchAmbiguous(
                source_system, snapshot_month, [row.batch_id for row in rows]
            )
        if not rows:
            return None
        _validate_fact_integrity(session, rows[0])
        return _batch_payload(rows[0])


def list_available_months(session_factory, source_system: str):
    """List only current Published months, rejecting any ambiguous month."""
    with session_factory() as session:
        rows = session.scalars(
            select(SalesImportBatch)
            .where(
                SalesImportBatch.source_system == source_system,
                SalesImportBatch.status == SalesBatchStatus.PUBLISHED,
                SalesImportBatch.replaced_at.is_(None),
            )
            .order_by(SalesImportBatch.snapshot_month.desc(), SalesImportBatch.id)
        ).all()
        grouped = {}
        for row in rows:
            grouped.setdefault(row.snapshot_month, []).append(row)
        for month, batches in grouped.items():
            if len(batches) > 1:
                raise CurrentBatchAmbiguous(
                    source_system, month, [row.batch_id for row in batches]
                )
            _validate_fact_integrity(session, batches[0])
        return [_batch_payload(batches[0]) for batches in grouped.values()]


def _validate_fact_integrity(session, current):
    month_rows, month_qty = session.execute(
        select(
            func.count(SalesFact.id),
            func.coalesce(func.sum(SalesFact.actual_qty), 0),
        ).where(
            SalesFact.source_system == current.source_system,
            SalesFact.snapshot_month == current.snapshot_month,
        )
    ).one()
    bound_rows, bound_qty = session.execute(
        select(
            func.count(SalesFact.id),
            func.coalesce(func.sum(SalesFact.actual_qty), 0),
        )
        .select_from(SalesFact)
        .join(SalesImportRow, SalesImportRow.id == SalesFact.import_row_id)
        .where(
            SalesFact.source_system == current.source_system,
            SalesFact.snapshot_month == current.snapshot_month,
            SalesImportRow.import_batch_id == current.id,
        )
    ).one()
    expected_rows = int(current.ready_rows)
    expected_qty = Decimal(current.ready_qty)
    actual = (
        int(month_rows), Decimal(month_qty), int(bound_rows), Decimal(bound_qty)
    )
    if actual != (expected_rows, expected_qty, expected_rows, expected_qty):
        raise DashboardFactIntegrityError({
            "batch_id": current.batch_id,
            "expected_rows": expected_rows,
            "expected_qty": _decimal(expected_qty),
            "month_fact_rows": int(month_rows),
            "month_fact_qty": _decimal(month_qty),
            "bound_fact_rows": int(bound_rows),
            "bound_fact_qty": _decimal(bound_qty),
        })


def _validated_batch(session, batch_id: str):
    requested = session.scalar(
        select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id)
    )
    if requested is None:
        raise DashboardBatchNotFound(batch_id)

    currents = _current_rows(session, requested.source_system, requested.snapshot_month)
    if len(currents) > 1:
        raise CurrentBatchAmbiguous(
            requested.source_system,
            requested.snapshot_month,
            [row.batch_id for row in currents],
        )
    current = currents[0] if currents else None
    if current is None or current.id != requested.id:
        raise DashboardSnapshotChanged(
            batch_id, current.batch_id if current is not None else None
        )
    _validate_fact_integrity(session, current)
    return current


def _dimension_filter(column, values):
    if not values:
        return None
    concrete = [value for value in values if value is not None]
    clauses = []
    if concrete:
        clauses.append(column.in_(concrete))
    if None in values:
        clauses.append(column.is_(None))
    return or_(*clauses)


def _filter_clauses(filters: DashboardFilters):
    clauses = []
    for clause in (
        _dimension_filter(SalesFact.product_id, filters.product_ids),
        _dimension_filter(SalesFact.sku_id, filters.sku_ids),
        _dimension_filter(SalesFact.channel_id_snapshot, filters.channel_ids),
        _dimension_filter(SalesFact.salesrep_id_snapshot, filters.salesrep_ids),
    ):
        if clause is not None:
            clauses.append(clause)
    return clauses


def _scope(batch, filters: DashboardFilters, *extra):
    return (
        SalesFact.source_system == batch.source_system,
        SalesFact.snapshot_month == batch.snapshot_month,
        SalesImportRow.import_batch_id == batch.id,
        *_filter_clauses(filters),
        *extra,
    )


def _fact_select(*columns):
    return select(*columns).select_from(SalesFact).join(
        SalesImportRow, SalesImportRow.id == SalesFact.import_row_id
    )


def _validate_page(page: int, page_size: int):
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 200:
        raise ValueError("page_size must be between 1 and 200")


def _page_payload(batch_id, items, total_items, page, page_size):
    return {
        "batch_id": batch_id,
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": int(total_items),
        },
    }


def get_summary(session_factory, batch_id: str, filters=DashboardFilters()):
    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        qty, products, skus = session.execute(
            _fact_select(
                func.coalesce(func.sum(SalesFact.actual_qty), 0),
                func.count(func.distinct(SalesFact.product_id)),
                func.count(func.distinct(SalesFact.sku_id)),
            ).where(*_scope(batch, filters))
        ).one()
        return {
            "batch_id": batch_id,
            "mtd_qty": _decimal(qty),
            "product_count": int(products),
            "sku_count": int(skus),
        }


def get_filter_options(
    session_factory, batch_id: str, filters=DashboardFilters(), *,
    include_product_sku: bool = True,
):
    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        scope = _scope(batch, filters)

        def distinct(*columns):
            return session.execute(
                _fact_select(*columns).where(*scope).distinct().order_by(*columns)
            ).all()

        products = (
            distinct(SalesFact.product_name_snapshot, SalesFact.product_id)
            if include_product_sku else []
        )
        skus = (
            distinct(
                SalesFact.sku_name_snapshot, SalesFact.sku_code_snapshot,
                SalesFact.sku_id,
            )
            if include_product_sku else []
        )
        channels = distinct(
            func.coalesce(SalesFact.channel_name_snapshot, UNASSIGNED),
            SalesFact.channel_id_snapshot,
        )
        salesreps = distinct(
            func.coalesce(SalesFact.salesrep_name_snapshot, UNASSIGNED),
            SalesFact.salesrep_id_snapshot,
        )
        return {
            "batch_id": batch_id,
            "products": [{"id": row[1], "name": row[0]} for row in products],
            "skus": [
                {"id": row[2], "code": row[1], "name": row[0]} for row in skus
            ],
            "channels": [{"id": row[1], "name": row[0]} for row in channels],
            "salesreps": [{"id": row[1], "name": row[0]} for row in salesreps],
        }


def _aggregate_page(
    session,
    batch,
    filters,
    *,
    group_columns,
    aggregate_columns=(),
    item_builder,
    sort,
    sort_options,
    page,
    page_size,
    extra=(),
):
    _validate_page(page, page_size)
    direction = "asc"
    sort_key = sort
    if sort.startswith("-"):
        direction, sort_key = "desc", sort[1:]
    if sort_key not in sort_options:
        raise ValueError(f"unsupported sort: {sort}")

    scope = _scope(batch, filters, *extra)
    qty = func.sum(SalesFact.actual_qty).label("qty")
    grouped = (
        _fact_select(*group_columns, *aggregate_columns, qty)
        .where(*scope)
        .group_by(*group_columns)
    )
    groups = grouped.subquery()
    total_items = session.scalar(select(func.count()).select_from(groups))
    denominator = session.scalar(
        _fact_select(func.coalesce(func.sum(SalesFact.actual_qty), 0)).where(*scope)
    )
    order_expr = sort_options[sort_key](groups)
    ordered = order_expr.asc() if direction == "asc" else order_expr.desc()
    stable = [groups.c[column].asc() for column in groups.c.keys() if column != "qty"]
    rows = session.execute(
        select(groups)
        .order_by(ordered, *stable)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings().all()
    return _page_payload(
        batch.batch_id,
        [item_builder(row, denominator) for row in rows],
        total_items,
        page,
        page_size,
    )


def get_product_aggregation(
    session_factory, batch_id: str, filters=DashboardFilters(), *,
    sort="-qty", page=1, page_size=50,
):
    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        product_name = SalesFact.product_name_snapshot.label("product_name")
        sku_count = func.count(func.distinct(SalesFact.sku_id)).label("sku_count")
        return _aggregate_page(
            session, batch, filters,
            group_columns=(SalesFact.product_id, product_name),
            aggregate_columns=(sku_count,),
            item_builder=lambda row, total: {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "qty": _decimal(row["qty"]),
                "share": _share(row["qty"], total),
                "sku_count": int(row["sku_count"]),
            },
            sort=sort,
            sort_options={
                "qty": lambda q: q.c.qty,
                "share": lambda q: q.c.qty,
                "sku_count": lambda q: q.c.sku_count,
                "product_name": lambda q: q.c.product_name,
            },
            page=page, page_size=page_size,
        )


def get_product_sku_drilldown(
    session_factory, batch_id: str, product_id: int, filters=DashboardFilters(), *,
    sort="-qty", page=1, page_size=50,
):
    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        sku_code = SalesFact.sku_code_snapshot.label("sku_code")
        sku_name = SalesFact.sku_name_snapshot.label("sku_name")
        return _aggregate_page(
            session, batch, filters,
            group_columns=(SalesFact.sku_id, sku_code, sku_name),
            item_builder=lambda row, total: {
                "sku_id": row["sku_id"], "sku_code": row["sku_code"],
                "sku_name": row["sku_name"], "qty": _decimal(row["qty"]),
                "share": _share(row["qty"], total),
            },
            sort=sort,
            sort_options={
                "qty": lambda q: q.c.qty, "share": lambda q: q.c.qty,
                "sku_code": lambda q: q.c.sku_code,
                "sku_name": lambda q: q.c.sku_name,
            },
            page=page, page_size=page_size,
            extra=(SalesFact.product_id == product_id,),
        )


def _matrix(
    session_factory, batch_id, filters, *, dimension, dimension_name,
    id_key, name_key, sort, page, page_size,
):
    with session_factory() as session:
        batch = _validated_batch(session, batch_id)
        dimension_label = func.coalesce(dimension_name, UNASSIGNED).label(name_key)
        product_name = SalesFact.product_name_snapshot.label("product_name")
        return _aggregate_page(
            session, batch, filters,
            group_columns=(
                dimension.label(id_key), dimension_label,
                SalesFact.product_id, product_name,
            ),
            item_builder=lambda row, total: {
                id_key: row[id_key], name_key: row[name_key],
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "qty": _decimal(row["qty"]),
                "share": _share(row["qty"], total),
            },
            sort=sort,
            sort_options={
                "qty": lambda q: q.c.qty,
                "share": lambda q: q.c.qty,
                name_key: lambda q: q.c[name_key],
                "product_name": lambda q: q.c.product_name,
            },
            page=page, page_size=page_size,
        )


def get_salesrep_product(
    session_factory, batch_id: str, filters=DashboardFilters(), *,
    sort="-qty", page=1, page_size=50,
):
    return _matrix(
        session_factory, batch_id, filters,
        dimension=SalesFact.salesrep_id_snapshot,
        dimension_name=SalesFact.salesrep_name_snapshot,
        id_key="salesrep_id", name_key="salesrep_name",
        sort=sort, page=page, page_size=page_size,
    )


def get_channel_product(
    session_factory, batch_id: str, filters=DashboardFilters(), *,
    sort="-qty", page=1, page_size=50,
):
    return _matrix(
        session_factory, batch_id, filters,
        dimension=SalesFact.channel_id_snapshot,
        dimension_name=SalesFact.channel_name_snapshot,
        id_key="channel_id", name_key="channel_name",
        sort=sort, page=page, page_size=page_size,
    )
