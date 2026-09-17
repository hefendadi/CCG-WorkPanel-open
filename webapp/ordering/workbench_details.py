"""Read-only explanations of selected Inventory facts and persisted lot results."""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from .models import (InventoryRaw, InventorySkuMonth, InventoryProductMonth,
    ProductionLotLifecycle, ProductionLotSourceVersion, PLANNING_AVAILABLE_SCOPE,
    CycleStatus, DatasetType)


LOT_LABELS = {'SOLD_OUT': '已售罄', 'ACTIVE': '消耗中',
              'PRE_EXISTING': '期初已存在 / 首次入库未知'}


def read_details(session, cycle, bindings, versions, baseline_month, baseline_ids,
                 baseline_ready, product_ids):
    result = {pid: dict(sku_inventory=[], sku_total=None, reconciliation='缺少库存基准',
                       lots=[], lot_message='暂无与本周期库存来源匹配的批次记录') for pid in product_ids}
    remaining = {}
    # Persisted SKU facts already contain only PLANNING_AVAILABLE. Their physical
    # Read the explicitly selected availability-policy version.
    if baseline_ready:
        version_id = next(iter(baseline_ids))
        sku_rows = list(session.scalars(select(InventorySkuMonth).where(
            InventorySkuMonth.dataset_version_id == version_id,
            InventorySkuMonth.inventory_month == baseline_month,
            InventorySkuMonth.warehouse_scope == PLANNING_AVAILABLE_SCOPE)))
        product_rows = {p.product_id: p for p in session.scalars(select(InventoryProductMonth).where(
            InventoryProductMonth.dataset_version_id == version_id,
            InventoryProductMonth.inventory_month == baseline_month,
            InventoryProductMonth.warehouse_scope == PLANNING_AVAILABLE_SCOPE))}
        raw = list(session.scalars(select(InventoryRaw).where(
            InventoryRaw.dataset_version_id == version_id,
            InventoryRaw.inventory_month == baseline_month)))
        identities = defaultdict(set)
        raw_lots = defaultdict(lambda: Decimal(0))
        raw_totals = defaultdict(lambda: Decimal(0))
        raw_policies = defaultdict(set)
        for r in raw:
            payload = r.raw_payload or {}
            if payload.get('warehouse_planning_status') != 'PLANNING_AVAILABLE':
                continue
            name = (payload.get('erp_values') or {}).get('物料名称')
            identities[r.sku_id].add((r.source_sku_code, str(name).strip() if name else None))
            raw_lots[(r.product_id, r.production_date)] += r.ending_qty
            raw_totals[r.product_id] += r.ending_qty
            raw_policies[r.product_id].add(payload.get('planning_policy_version'))
        grouped = defaultdict(list)
        for row in sku_rows:
            grouped[row.product_id].append(row)
        for pid, detail in result.items():
            rows = grouped[pid]
            product = product_rows.get(pid)
            total = sum((r.ending_qty for r in rows), Decimal(0))
            detail['sku_total'] = str(total) if rows else None
            reconciled = product is not None and bool(rows) and total == product.ending_qty and all(
                r.warehouse_policy_version == product.warehouse_policy_version for r in rows)
            detail['reconciliation'] = '已对账' if reconciled else '数据异常：SKU 库存合计或仓库口径与产品库存不一致'
            if (reconciled and raw_totals.get(pid) == product.ending_qty
                    and raw_policies[pid] == {product.warehouse_policy_version}):
                remaining.update({key: value for key, value in raw_lots.items() if key[0] == pid})
            for r in rows:
                if r.ending_qty == 0:
                    continue
                names = identities[r.sku_id]
                identity = next(iter(names)) if len(names) == 1 else (None, None)
                detail['sku_inventory'].append(dict(sku_id=r.sku_id,
                    code=identity[0] or '缺少来源编码', name=identity[1] or '缺少来源名称',
                    ending_qty=str(r.ending_qty),
                    proportion=str((r.ending_qty / product.ending_qty * 100).quantize(Decimal('.01')))
                        if reconciled and product.ending_qty != 0 else None))
    # Use source provenance, not global revision recency. Only complete source
    # coverage through T-1 is eligible, including every monthly source of a result.
    eligible_versions = set()
    for b in bindings:
        v = versions[b.dataset_version_id]
        if (v.dataset_type == DatasetType.INVENTORY and b.period_start == v.period_start
                and b.period_end == v.period_end and v.period_end <= baseline_month):
            eligible_versions.add(v.id)
    candidates = list(session.scalars(select(ProductionLotLifecycle).where(
        ProductionLotLifecycle.product_id.in_(product_ids),
        ProductionLotLifecycle.warehouse_scope == PLANNING_AVAILABLE_SCOPE)))
    provenance = defaultdict(set)
    for source in session.scalars(select(ProductionLotSourceVersion).where(
            ProductionLotSourceVersion.lifecycle_id.in_([r.id for r in candidates]))):
        provenance[source.lifecycle_id].add(source.inventory_version_id)
    groups = defaultdict(list)
    for r in candidates:
        refs = provenance[r.id]
        if not refs or not refs <= eligible_versions:
            continue
        if cycle.status == CycleStatus.LOCKED and r.created_at > cycle.locked_at:
            continue
        groups[(r.product_id, r.production_date)].append(r)
    for (pid, production_date), revisions in sorted(groups.items()):
        # A unique result covering all other candidates' evidence is unambiguous.
        # Equal/incomparable evidence sets do not justify picking the latest ID.
        covering = [r for r in revisions if all(provenance[other.id] <= provenance[r.id] for other in revisions)]
        detail = result[pid]
        if len(covering) != 1:
            detail['lots'].append(dict(production_date=production_date.isoformat(),
                status_label='数据异常：批次记录来源无法唯一确定', first_inbound_month=None,
                final_sold_out_month=None, consumption_months=None, observed_through=None,
                remaining_qty=None, source_version_ids=[]))
            continue
        r = covering[0]
        detail['lots'].append(dict(production_date=production_date.isoformat(),
            status=r.lifecycle_state.value, status_label=LOT_LABELS[r.lifecycle_state.value],
            first_inbound_month=r.first_inbound_month.isoformat() if r.first_inbound_month else None,
            final_sold_out_month=r.final_sold_out_month.isoformat() if r.final_sold_out_month else None,
            consumption_months=r.consumption_months if r.lifecycle_state.value == 'SOLD_OUT' else None,
            observed_through=max(versions[v].period_end for v in provenance[r.id]).isoformat(),
            remaining_qty=str(remaining[(pid, production_date)]) if (pid, production_date) in remaining else None,
            revision_no=r.revision_no, source_version_ids=sorted(provenance[r.id])))
        detail['lot_message'] = '复用已保存的批次结果；仅展示本周期已绑定库存来源覆盖的记录'
    return result
