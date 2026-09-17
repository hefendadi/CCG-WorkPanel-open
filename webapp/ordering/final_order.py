"""Manual Product decisions. Revisions are pre-change quantity snapshots only.

Each public operation owns its session/transaction. Cycle row locks serialize
writers (including first inserts) and keep history reads consistent on MySQL.
SQLite is supported for functional tests, not concurrent writer guarantees.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import Product
from webapp.ordering.models import (
    CycleProductSnapshot, CycleStatus, FinalOrder, FinalOrderRevision, FinalOrderStatus, PlanningCycle,
)
from webapp.ordering.service_contract import confirmation_after_quantity_change


class FinalOrderError(RuntimeError):
    pass


class FinalOrderInvalid(FinalOrderError):
    pass


class FinalOrderCycleNotFound(FinalOrderError):
    pass


class FinalOrderCycleLocked(FinalOrderError):
    pass


class FinalOrderMdmUnresolved(FinalOrderError):
    pass


class FinalOrderNotFound(FinalOrderError):
    pass


@dataclass(frozen=True)
class FinalOrderView:
    final_order_id: int
    cycle_id: int
    cycle_code: str
    cycle_status: CycleStatus
    product_id: int
    product_stable_id: str
    product_name: str
    order_qty: Decimal
    status: FinalOrderStatus
    confirmed_by: str | None
    confirmed_at: datetime | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class FinalOrderRevisionView:
    revision_no: int
    from_qty: Decimal
    to_qty: Decimal
    changed_by: str
    changed_at: datetime


def _text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise FinalOrderInvalid(f"{field} must be nonempty text of at most {limit} characters")
    return value.strip()


def _quantity(value: object) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FinalOrderInvalid("order_qty must fit non-negative NUMERIC(18,3)") from exc
    if (not qty.is_finite() or qty < 0 or qty >= Decimal('1000000000000000')
            or qty != qty.quantize(Decimal('0.001'))):
        raise FinalOrderInvalid("order_qty must fit non-negative NUMERIC(18,3) without rounding")
    return qty.quantize(Decimal('0.001'))


def _context(session, cycle_code, product_stable_id, *, writing):
    cycle = session.scalar(select(PlanningCycle).where(
        PlanningCycle.cycle_code == _text(cycle_code, 'cycle_code', 64)
    ).with_for_update())
    if cycle is None:
        raise FinalOrderCycleNotFound(f"planning cycle not found: {cycle_code}")
    if writing and cycle.status != CycleStatus.OPEN:
        raise FinalOrderCycleLocked(f"planning cycle is not open: {cycle_code}")
    stable_id = _text(product_stable_id, 'product_stable_id', 24)
    if cycle.status == CycleStatus.LOCKED:
        product = session.scalar(select(CycleProductSnapshot).where(
            CycleProductSnapshot.cycle_id == cycle.id,
            CycleProductSnapshot.product_stable_id == stable_id,
        ))
    else:
        product = session.scalar(select(Product).where(Product.stable_id == stable_id))
    if product is None:
        raise FinalOrderMdmUnresolved(f"Product unresolved in MDM: {product_stable_id}")
    order = session.scalar(select(FinalOrder).where(
        FinalOrder.cycle_id == cycle.id, FinalOrder.product_id == (product.product_id if isinstance(product, CycleProductSnapshot) else product.id)
    ))
    return cycle, product, order


def _view(cycle, product, order):
    return FinalOrderView(
        order.id, cycle.id, cycle.cycle_code, cycle.status,
        product.product_id if isinstance(product, CycleProductSnapshot) else product.id,
        product.product_stable_id if isinstance(product, CycleProductSnapshot) else product.stable_id,
        product.product_name,
        order.order_qty, order.status, order.confirmed_by, order.confirmed_at,
        order.created_by, order.created_at, order.updated_by, order.updated_at,
    )


def write_final_order(
    session_factory: Callable[[], Session], *, cycle_code: str,
    product_stable_id: str, order_qty: Decimal, updated_by: str,
) -> FinalOrderView:
    qty = _quantity(order_qty)
    actor = _text(updated_by, 'updated_by', 128)
    with session_factory() as session, session.begin():
        cycle, product, order = _context(session, cycle_code, product_stable_id, writing=True)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if order is None:
            order = FinalOrder(
                cycle_id=cycle.id, product_id=product.id, order_qty=qty,
                status=FinalOrderStatus.UNCONFIRMED, created_by=actor,
                updated_by=actor, created_at=now, updated_at=now,
            )
            session.add(order)
        elif order.order_qty != qty:
            latest = session.scalar(select(FinalOrderRevision).where(
                FinalOrderRevision.final_order_id == order.id
            ).order_by(FinalOrderRevision.revision_no.desc()).limit(1))
            session.add(FinalOrderRevision(
                final_order_id=order.id,
                revision_no=1 if latest is None else latest.revision_no + 1,
                order_qty=order.order_qty, status=order.status,
                confirmed_by=order.confirmed_by, confirmed_at=order.confirmed_at,
                remark=order.remark, created_by=actor, created_at=now,
            ))
            order.status, order.confirmed_by, order.confirmed_at = confirmation_after_quantity_change(
                current_qty=order.order_qty, new_qty=qty, status=order.status,
                confirmed_by=order.confirmed_by, confirmed_at=order.confirmed_at,
            )
            order.order_qty = qty
            order.updated_by, order.updated_at = actor, now
        session.flush()
        return _view(cycle, product, order)


def confirm_final_order(
    session_factory: Callable[[], Session], *, cycle_code: str,
    product_stable_id: str, confirmed_by: str,
) -> FinalOrderView:
    actor = _text(confirmed_by, 'confirmed_by', 128)
    with session_factory() as session, session.begin():
        cycle, product, order = _context(session, cycle_code, product_stable_id, writing=True)
        if order is None:
            raise FinalOrderNotFound('create the manual decision before confirming it')
        if order.status != FinalOrderStatus.CONFIRMED:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            order.status = FinalOrderStatus.CONFIRMED
            order.confirmed_by, order.confirmed_at = actor, now
            order.updated_by, order.updated_at = actor, now
        session.flush()
        return _view(cycle, product, order)


def read_final_order(
    session_factory: Callable[[], Session], *, cycle_code: str, product_stable_id: str,
) -> FinalOrderView | None:
    with session_factory() as session, session.begin():
        cycle, product, order = _context(session, cycle_code, product_stable_id, writing=False)
        return None if order is None else _view(cycle, product, order)


def read_final_order_history(
    session_factory: Callable[[], Session], *, cycle_code: str, product_stable_id: str,
) -> tuple[FinalOrderRevisionView, ...]:
    with session_factory() as session, session.begin():
        _, _, order = _context(session, cycle_code, product_stable_id, writing=False)
        if order is None:
            return ()
        revisions = session.scalars(select(FinalOrderRevision).where(
            FinalOrderRevision.final_order_id == order.id
        ).order_by(FinalOrderRevision.revision_no)).all()
        # The next pre-change snapshot is this change's destination; the last
        # destination lives in Current. Confirmation never changes this chain.
        return tuple(FinalOrderRevisionView(
            row.revision_no, row.order_qty,
            revisions[index + 1].order_qty if index + 1 < len(revisions) else order.order_qty,
            row.created_by, row.created_at,
        ) for index, row in enumerate(revisions))
