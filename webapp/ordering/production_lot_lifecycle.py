"""Pure Production Lot lifecycle calculation.

Persistence is deliberately absent.  A calculation consumes an explicit set of
Inventory monthly versions so a correction can never masquerade as "the latest"
source and silently rewrite an older lifecycle result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Optional

from webapp.ordering.inventory_import import NormalizedInventoryRow
from webapp.ordering.models import LotLifecycleState, ReturnClassification


class LifecycleInputError(ValueError):
    """The explicitly selected Inventory versions cannot form one calculation."""


@dataclass(frozen=True)
class InventoryLifecycleSource:
    """One selected, immutable Inventory version and its validated preview rows."""

    inventory_version_id: int
    inventory_month: date
    rows: tuple[NormalizedInventoryRow, ...]


@dataclass(frozen=True)
class ProductionLotMonth:
    inventory_month: date
    opening_qty: Decimal
    receipt_qty: Decimal
    issue_qty: Decimal
    ending_qty: Decimal
    inventory_version_id: int
    warehouse_policy_version: str


@dataclass(frozen=True)
class ReturnDecision:
    """Decision supplied only by an explicitly configured, frozen return policy."""

    classification: ReturnClassification
    policy_code: str
    policy_version: str
    policy_parameters: Mapping[str, object]


ReturnPolicyHook = Callable[[tuple[ProductionLotMonth, ...]], Optional[ReturnDecision]]


@dataclass(frozen=True)
class ProductionLotLifecycleResult:
    product_id: int
    product_stable_id: str
    production_date: date
    lifecycle_state: LotLifecycleState
    first_inbound_month: date | None
    final_sold_out_month: date | None
    consumption_months: int | None
    monthly_observations: tuple[ProductionLotMonth, ...]
    source_inventory_version_ids: tuple[int, ...]
    return_decision: ReturnDecision | None

    @property
    def first_inbound_known(self) -> bool:
        return self.first_inbound_month is not None


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _inclusive_months(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _terminal_sold_out_month(months: tuple[ProductionLotMonth, ...]) -> date | None:
    """Return the first zero-ending month in the terminal zero-balance suffix."""

    if not months or months[-1].ending_qty != 0:
        return None
    index = len(months) - 1
    while index > 0 and months[index - 1].ending_qty == 0:
        index -= 1
    return months[index].inventory_month


def calculate_production_lot_lifecycles(
    sources: Iterable[InventoryLifecycleSource],
    *,
    return_policy: ReturnPolicyHook | None = None,
) -> tuple[ProductionLotLifecycleResult, ...]:
    """Aggregate PLANNING_AVAILABLE rows at Product + Production Date grain.

    Each observed month must come from exactly one explicitly selected Inventory
    version.  Selecting both an original and a correction for the same month is
    rejected instead of being summed or resolved by recency.
    """

    selected = tuple(sources)
    version_ids = [source.inventory_version_id for source in selected]
    if any(version_id <= 0 for version_id in version_ids):
        raise LifecycleInputError("inventory_version_id must be positive")
    if len(version_ids) != len(set(version_ids)):
        raise LifecycleInputError("each Inventory source version may be selected only once")

    month_versions: dict[date, int] = {}
    for source in selected:
        month = _month_start(source.inventory_month)
        if source.inventory_month != month:
            raise LifecycleInputError("inventory_month must be the first day of a month")
        previous = month_versions.setdefault(month, source.inventory_version_id)
        if previous != source.inventory_version_id:
            raise LifecycleInputError(
                "multiple Inventory correction versions were selected for the same month"
            )

    grouped: dict[tuple[int, date], dict[date, dict[str, object]]] = {}
    stable_ids: dict[tuple[int, date], str] = {}
    for source in selected:
        for row in source.rows:
            if not row.eligible_for_lifecycle:
                continue
            key = (row.product_id, row.production_date)
            prior_stable_id = stable_ids.setdefault(key, row.product_stable_id)
            if prior_stable_id != row.product_stable_id:
                raise LifecycleInputError("one Product ID maps to multiple stable IDs")
            months = grouped.setdefault(key, {})
            item = months.setdefault(
                source.inventory_month,
                {
                    "opening_qty": Decimal("0"),
                    "receipt_qty": Decimal("0"),
                    "issue_qty": Decimal("0"),
                    "ending_qty": Decimal("0"),
                    "inventory_version_id": source.inventory_version_id,
                    "warehouse_policy_version": row.planning_policy_version,
                },
            )
            if item["inventory_version_id"] != source.inventory_version_id:
                raise LifecycleInputError("one lot-month cannot bind multiple Inventory versions")
            if item["warehouse_policy_version"] != row.planning_policy_version:
                raise LifecycleInputError("one Inventory version cannot mix warehouse policies")
            for field in ("opening_qty", "receipt_qty", "issue_qty", "ending_qty"):
                item[field] += getattr(row, field)

    results: list[ProductionLotLifecycleResult] = []
    for (product_id, production_date), values_by_month in sorted(grouped.items()):
        observations = tuple(
            ProductionLotMonth(inventory_month=month, **values)
            for month, values in sorted(values_by_month.items())
        )
        pre_existing = observations[0].opening_qty > 0
        observed_positive_receipt = next(
            (
                observation.inventory_month
                for observation in observations
                if observation.receipt_qty > 0
            ),
            None,
        )
        # ERP extracts retain all-zero historical lot rows.  Ending=0 alone is
        # not lifecycle evidence when neither opening stock nor inbound has ever
        # been observed.  Signed reverse movements also do not invent inbound.
        if not pre_existing and observed_positive_receipt is None:
            continue
        first_inbound = None if pre_existing else observed_positive_receipt
        final_sold_out = _terminal_sold_out_month(observations)

        if final_sold_out is not None:
            state = LotLifecycleState.SOLD_OUT
        elif pre_existing:
            state = LotLifecycleState.PRE_EXISTING
        else:
            state = LotLifecycleState.ACTIVE

        consumption_months = (
            _inclusive_months(first_inbound, final_sold_out)
            if first_inbound is not None and final_sold_out is not None
            else None
        )
        return_decision = return_policy(observations) if return_policy is not None else None
        if return_decision is not None and not isinstance(return_decision, ReturnDecision):
            raise LifecycleInputError("return policy must return ReturnDecision or None")

        results.append(
            ProductionLotLifecycleResult(
                product_id=product_id,
                product_stable_id=stable_ids[(product_id, production_date)],
                production_date=production_date,
                lifecycle_state=state,
                first_inbound_month=first_inbound,
                final_sold_out_month=final_sold_out,
                consumption_months=consumption_months,
                monthly_observations=observations,
                source_inventory_version_ids=tuple(
                    observation.inventory_version_id for observation in observations
                ),
                return_decision=return_decision,
            )
        )
    return tuple(results)
