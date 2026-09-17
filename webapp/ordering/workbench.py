"""Cycle-scoped application reads and manual Forecast adoption. MDM is read-only."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from webapp.mdm.models import Product, Channel
from .models import (PlanningCycle, CycleStatus, CycleSourceBinding, DatasetVersion,
                     DatasetType, InventoryProductMonth, ActualChannelProductMonth,
                     IncomingSnapshot, IncomingSnapshotLine, ForecastVersion, ForecastLine,
                     FinalOrder, CycleProductSnapshot, CycleChannelSnapshot)
from .forecast_version import write_forecast_version
from .workbench_details import read_details


class WorkbenchError(ValueError):
    pass


def month_offset(month, offset):
    index = month.year * 12 + month.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def cycle_context(session, cycle_id, writing=False):
    cycle = session.scalar(select(PlanningCycle).where(PlanningCycle.id == cycle_id).with_for_update())
    if cycle is None:
        raise WorkbenchError('Cycle not found')
    if writing and cycle.status != CycleStatus.OPEN:
        raise WorkbenchError('LOCKED cycle is read-only')
    return cycle


def read_workbench(factory, cycle_id):
    with factory() as s, s.begin():
        c = cycle_context(s, cycle_id)
        months = [month_offset(c.cycle_month, n) for n in range(4)]
        baseline_month = month_offset(c.cycle_month, -1)
        bindings = s.scalars(select(CycleSourceBinding).where(CycleSourceBinding.cycle_id == c.id)).all()
        versions = {v.id: v for v in s.scalars(select(DatasetVersion).where(
            DatasetVersion.id.in_([b.dataset_version_id for b in bindings])))}
        sources = {'ACTUAL': [], 'INVENTORY': []}
        baseline_ids, actual_ids, inventory_ids = set(), set(), set()
        invalid_baseline = False
        for b in bindings:
            v = versions.get(b.dataset_version_id)
            if v is None:
                raise WorkbenchError('Bound source version missing')
            valid = v.period_start <= b.period_start <= b.period_end <= v.period_end
            sources[v.dataset_type.value].append(dict(id=v.id, label=v.version_label,
                start=b.period_start.isoformat(), end=b.period_end.isoformat(), valid=valid))
            if v.dataset_type == DatasetType.INVENTORY:
                if b.period_start <= baseline_month <= b.period_end:
                    baseline_ids.add(v.id)
                    invalid_baseline |= not valid
                inventory_ids.update(s.scalars(select(InventoryProductMonth.product_id).where(
                    InventoryProductMonth.dataset_version_id == v.id,
                    InventoryProductMonth.inventory_month.between(b.period_start, b.period_end))))
            else:
                actual_ids.update(s.scalars(select(ActualChannelProductMonth.product_id).where(
                    ActualChannelProductMonth.dataset_version_id == v.id,
                    ActualChannelProductMonth.actual_month.between(b.period_start, b.period_end))))
        baseline_state = 'Ready' if len(baseline_ids) == 1 else ('Baseline Missing' if not baseline_ids else 'Baseline Conflict')
        if invalid_baseline:
            baseline_state = 'Baseline Conflict'
        inventory = {}
        if baseline_state == 'Ready':
            inventory = {r.product_id: r.ending_qty for r in s.scalars(select(InventoryProductMonth).where(
                InventoryProductMonth.dataset_version_id == next(iter(baseline_ids)),
                InventoryProductMonth.inventory_month == baseline_month))}
        incoming = s.get(IncomingSnapshot, c.incoming_snapshot_id) if c.incoming_snapshot_id else None
        incoming_lines = list(s.scalars(select(IncomingSnapshotLine).where(
            IncomingSnapshotLine.snapshot_id == incoming.id))) if incoming else []
        forecast = s.get(ForecastVersion, c.forecast_version_id) if c.forecast_version_id else None
        if forecast and forecast.cycle_id != c.id:
            raise WorkbenchError('Selected Forecast does not belong to cycle')
        lines = list(s.scalars(select(ForecastLine).where(ForecastLine.forecast_version_id == forecast.id))) if forecast else []
        orders = {o.product_id: o for o in s.scalars(select(FinalOrder).where(FinalOrder.cycle_id == c.id))}
        ids = actual_ids | inventory_ids | {l.product_id for l in incoming_lines} | {l.product_id for l in lines} | set(orders)
        locked = c.status == CycleStatus.LOCKED
        if locked:
            products = {p.product_id: (p.product_stable_id, p.product_code, p.product_name) for p in s.scalars(
                select(CycleProductSnapshot).where(CycleProductSnapshot.cycle_id == c.id))}
            channels = {ch.channel_id: (ch.channel_stable_id, ch.channel_name) for ch in s.scalars(
                select(CycleChannelSnapshot).where(CycleChannelSnapshot.cycle_id == c.id))}
        else:
            products = {p.id: (p.stable_id, p.product_code, p.product_name) for p in s.scalars(select(Product).where(Product.id.in_(ids)))}
            channels = {ch.id: (ch.stable_id, ch.channel_name) for ch in s.scalars(select(Channel))}
        gaps = []
        if ids - products.keys():
            gaps.append('缺少产品历史名称快照；已锁定周期不会使用当前主数据替代。')
        if {l.channel_id for l in lines} - channels.keys():
            gaps.append('缺少渠道历史名称快照。')
        if any(l.forecast_month not in months for l in lines):
            gaps.append('所选销售预测包含四个月窗口外的数据；为保留原记录，当前页面禁止保存。')
        details = read_details(s, c, bindings, versions, baseline_month, baseline_ids,
                               baseline_state == 'Ready', ids)
        rows = []
        for pid in sorted(ids):
            identity = products.get(pid, (None, None, f'缺少产品历史快照（{pid}）'))
            scheduled = [Decimal(0) for _ in months] if incoming else [None] * 4
            unscheduled, overdue = Decimal(0), Decimal(0)
            for l in incoming_lines:
                if l.product_id != pid or l.incoming_qty is None or l.warehouse_entry_recorded:
                    continue
                arrival = l.expected_arrival_date
                if arrival is None:
                    unscheduled += l.incoming_qty
                elif arrival < months[0]:
                    overdue += l.incoming_qty
                elif arrival.replace(day=1) in months:
                    scheduled[months.index(arrival.replace(day=1))] += l.incoming_qty
                # Outside the fixed horizon is not allocated into it.
            channel_rows = []
            relevant = set(channels) | {l.channel_id for l in lines if l.product_id == pid}
            for cid in sorted(relevant):
                ch = channels.get(cid, (None, f'缺少渠道历史快照（{cid}）'))
                values = {l.forecast_month: str(l.forecast_qty) for l in lines if l.product_id == pid and l.channel_id == cid}
                channel_rows.append(dict(id=cid, stable_id=ch[0], name=ch[1], forecast=[values.get(m) for m in months]))
            order = orders.get(pid)
            rows.append(dict(id=pid, stable_id=identity[0], code=identity[1], name=identity[2],
                opening=str(inventory[pid]) if pid in inventory else None,
                incoming=[str(q) if q is not None else None for q in scheduled],
                unscheduled=str(unscheduled) if incoming else None, overdue=str(overdue) if incoming else None,
                channels=channel_rows, final_order=str(order.order_qty) if order else None,
                details=details[pid],
                confirmation=order.status.value if order else 'Missing'))
        return dict(id=c.id, name=c.cycle_code, status=c.status.value, months=[m.isoformat() for m in months],
            baseline_month=baseline_month.isoformat(), baseline_state=baseline_state,
            sources=sources, incoming_snapshot=dict(id=incoming.id, name=incoming.snapshot_key, at=incoming.snapshot_at.isoformat()) if incoming else None,
            forecast_version=dict(id=forecast.id, number=forecast.version_no) if forecast else None,
            confirmation=dict(confirmed=sum(o.status.value == 'CONFIRMED' for o in orders.values()), total=len(orders)),
            rows=rows, gaps=gaps, save_allowed=not locked and not gaps)


def save_forecast(factory, cycle_id, expected_version_id, lines, actor):
    # Existing Core owns a savepoint; version creation and explicit adoption are
    # committed together by this outer transaction. No public edition edits needed.
    with factory() as s, s.begin():
        c = cycle_context(s, cycle_id, writing=True)
        if c.forecast_version_id != expected_version_id:
            raise WorkbenchError('Forecast selection changed; reload before saving')
        months = {month_offset(c.cycle_month, i) for i in range(4)}
        if any(l.forecast_month not in months for l in lines):
            raise WorkbenchError('Forecast months must be T through T+3')
        for l in lines:
            q = l.forecast_qty
            if not q.is_finite() or q < 0 or q >= Decimal('1000000000000000') or q != q.quantize(Decimal('.001')):
                raise WorkbenchError('Forecast quantity must fit non-negative NUMERIC(18,3)')
        if c.forecast_version_id and s.scalar(select(ForecastLine.id).where(
            ForecastLine.forecast_version_id == c.forecast_version_id,
            ForecastLine.forecast_month.not_in(months)).limit(1)):
            raise WorkbenchError('Selected Forecast contains months outside T–T+3')
        nested = sessionmaker(bind=s.connection(), expire_on_commit=False, join_transaction_mode='create_savepoint')
        result = write_forecast_version(nested, cycle_code=c.cycle_code, lines=lines, created_by=actor)
        c.forecast_version_id = result.forecast_version_id
        s.flush()
        return result
