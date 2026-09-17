"""One-shot Planning Cycle archival. Existing bindings are the chosen source list."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import Product, Channel
from webapp.ordering.models import (
    PlanningCycle, CycleStatus, CycleSourceBinding, DatasetVersion, DatasetType,
    IncomingSnapshot, ForecastVersion, ForecastLine, FinalOrder, FinalOrderStatus,
    CycleProductSnapshot, CycleChannelSnapshot,
)


class PlanningCycleLockError(RuntimeError):
    """Preflight failed; nothing is archived."""


@dataclass(frozen=True)
class PlanningCycleLockResult:
    cycle_id: int
    incoming_snapshot_id: int
    forecast_version_id: int
    locked_by: str
    locked_at: datetime
    source_binding_ids: tuple[int, ...]


def _bindings(session, cycle_id):
    bindings = session.scalars(select(CycleSourceBinding).where(
        CycleSourceBinding.cycle_id == cycle_id
    ).order_by(CycleSourceBinding.id).with_for_update()).all()
    versions = {v.id: v for v in session.scalars(select(DatasetVersion).where(
        DatasetVersion.id.in_({b.dataset_version_id for b in bindings})
    ).order_by(DatasetVersion.id).with_for_update())}
    periods = {DatasetType.ACTUAL: [], DatasetType.INVENTORY: []}
    for binding in bindings:
        version = versions.get(binding.dataset_version_id)
        if version is None or version.dataset_type not in periods:
            raise PlanningCycleLockError('source binding has missing or invalid dataset version')
        start, end = binding.period_start, binding.period_end
        if (start is None or end is None or start.day != 1 or end.day != 1
                or not version.period_start <= start <= end <= version.period_end):
            raise PlanningCycleLockError('binding period is outside source version coverage')
        for previous_start, previous_end, previous_version in periods[version.dataset_type]:
            if start <= previous_end and previous_start <= end and previous_version != version.id:
                raise PlanningCycleLockError('conflicting versions for the same dataset month')
        periods[version.dataset_type].append((start, end, version.id))
    if not all(periods.values()):
        raise PlanningCycleLockError('at least one Actual and one Inventory binding are required')
    return bindings


def lock_planning_cycle(
    session_factory: Callable[[], Session], *, cycle_code: str,
    incoming_snapshot_id: int, forecast_version_id: int, locked_by: str,
) -> PlanningCycleLockResult:
    if not isinstance(locked_by, str) or not locked_by.strip() or len(locked_by.strip()) > 128:
        raise PlanningCycleLockError('locked_by must be nonempty text of at most 128 characters')
    if not isinstance(cycle_code, str) or not cycle_code.strip():
        raise PlanningCycleLockError('cycle_code is required')
    if any(type(value) is not int or value <= 0 for value in (incoming_snapshot_id, forecast_version_id)):
        raise PlanningCycleLockError('explicit positive Incoming and Forecast IDs are required')
    with session_factory() as session, session.begin():
        cycle = session.scalar(select(PlanningCycle).where(
            PlanningCycle.cycle_code == cycle_code.strip()).with_for_update())
        if cycle is None or cycle.status != CycleStatus.OPEN:
            raise PlanningCycleLockError('planning cycle must exist and be OPEN')
        bindings = _bindings(session, cycle.id)
        incoming = session.scalar(select(IncomingSnapshot).where(
            IncomingSnapshot.id == incoming_snapshot_id).with_for_update())
        if incoming is None:
            raise PlanningCycleLockError('Incoming Snapshot does not exist')
        forecasts = session.scalars(select(ForecastVersion).where(
            ForecastVersion.cycle_id == cycle.id).order_by(ForecastVersion.id).with_for_update()).all()
        if forecast_version_id not in {v.id for v in forecasts}:
            raise PlanningCycleLockError('selected Forecast must exist and belong to this cycle')
        orders = session.scalars(select(FinalOrder).where(
            FinalOrder.cycle_id == cycle.id).with_for_update()).all()
        if not orders or any(o.status != FinalOrderStatus.CONFIRMED for o in orders):
            raise PlanningCycleLockError('all existing Final Orders must be CONFIRMED and at least one must exist')
        # All versions belong to the cycle's readable history, including superseded
        # versions. Snapshot their identities too, without copying quantities.
        lines = session.scalars(select(ForecastLine).where(
            ForecastLine.forecast_version_id.in_([v.id for v in forecasts])
        ).with_for_update()).all()
        product_ids = {o.product_id for o in orders} | {l.product_id for l in lines}
        channel_ids = {l.channel_id for l in lines}
        products = session.scalars(select(Product).where(Product.id.in_(product_ids))).all()
        channels = session.scalars(select(Channel).where(Channel.id.in_(channel_ids))).all()
        if {p.id for p in products} != product_ids or {c.id for c in channels} != channel_ids:
            raise PlanningCycleLockError('display identity is unresolved in MDM')
        for model in (CycleProductSnapshot, CycleChannelSnapshot):
            if session.scalar(select(model.id).where(model.cycle_id == cycle.id).limit(1)) is not None:
                raise PlanningCycleLockError('OPEN cycle already contains display snapshots')
        session.add_all([CycleProductSnapshot(
            cycle_id=cycle.id, product_id=p.id, product_stable_id=p.stable_id,
            product_code=p.product_code, product_name=p.product_name,
        ) for p in products])
        session.add_all([CycleChannelSnapshot(
            cycle_id=cycle.id, channel_id=c.id, channel_stable_id=c.stable_id,
            channel_code=c.channel_code, channel_name=c.channel_name,
        ) for c in channels])
        # Snapshot insert guards require OPEN. Flush before the state transition,
        # but keep both flushes inside the same transaction.
        session.flush()
        cycle.incoming_snapshot_id = incoming_snapshot_id
        cycle.forecast_version_id = forecast_version_id
        cycle.locked_by = locked_by.strip()
        cycle.locked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        cycle.status = CycleStatus.LOCKED
        session.flush()
        return PlanningCycleLockResult(cycle.id, incoming_snapshot_id, forecast_version_id,
                                       cycle.locked_by, cycle.locked_at, tuple(b.id for b in bindings))
