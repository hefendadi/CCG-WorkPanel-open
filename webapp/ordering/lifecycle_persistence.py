"""Atomic, append-only persistence for Production Lot lifecycle revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webapp.ordering.models import (
    DatasetType,
    DatasetVersion,
    PLANNING_AVAILABLE_SCOPE,
    ProductionLotLifecycle,
    ProductionLotSourceVersion,
)
from webapp.ordering.production_lot_lifecycle import ProductionLotLifecycleResult


class LifecyclePersistenceError(RuntimeError):
    """A lifecycle batch was rejected and rolled back."""


class LifecycleRevisionConflict(LifecyclePersistenceError):
    """Revision allocation collided with a concurrent or conflicting write."""


@dataclass(frozen=True)
class PersistedLifecycleRevision:
    lifecycle_id: int
    product_id: int
    production_date: date
    revision_no: int
    created: bool


def _provenance(result: ProductionLotLifecycleResult) -> tuple[tuple[int, str], ...]:
    bindings = tuple(
        (observation.inventory_version_id, observation.warehouse_policy_version)
        for observation in result.monthly_observations
    )
    if not bindings:
        raise LifecyclePersistenceError("a lifecycle revision must bind its Inventory sources")
    version_ids = tuple(version_id for version_id, _policy in bindings)
    if version_ids != result.source_inventory_version_ids:
        raise LifecyclePersistenceError(
            "lifecycle source ids do not match the versions that participated in calculation"
        )
    if len(version_ids) != len(set(version_ids)):
        raise LifecyclePersistenceError("a lifecycle revision cannot bind one source version twice")
    return tuple(sorted(bindings))


def _business_values(result: ProductionLotLifecycleResult) -> tuple[object, ...]:
    decision = result.return_decision
    return (
        result.lifecycle_state,
        result.first_inbound_month,
        result.final_sold_out_month,
        result.consumption_months,
        decision.classification if decision is not None else None,
        decision.policy_code if decision is not None else None,
        decision.policy_version if decision is not None else None,
        dict(decision.policy_parameters) if decision is not None else None,
    )


def _stored_business_values(row: ProductionLotLifecycle) -> tuple[object, ...]:
    return (
        row.lifecycle_state,
        row.first_inbound_month,
        row.final_sold_out_month,
        row.consumption_months,
        row.return_classification,
        row.policy_code,
        row.policy_version,
        row.policy_parameters,
    )


def _stored_provenance(
    session: Session, lifecycle_id: int
) -> tuple[tuple[int, str], ...]:
    return tuple(
        sorted(
            session.execute(
                select(
                    ProductionLotSourceVersion.inventory_version_id,
                    ProductionLotSourceVersion.warehouse_policy_version,
                ).where(ProductionLotSourceVersion.lifecycle_id == lifecycle_id)
            ).all()
        )
    )


def persist_lifecycle_results(
    session_factory: Callable[[], Session],
    results: Iterable[ProductionLotLifecycleResult],
) -> tuple[PersistedLifecycleRevision, ...]:
    """Persist a calculated batch atomically without resolving any source by recency."""

    calculated = tuple(results)
    grains = tuple((item.product_id, item.production_date) for item in calculated)
    if len(grains) != len(set(grains)):
        raise LifecyclePersistenceError("one persistence batch may contain each lot only once")

    try:
        persisted: list[PersistedLifecycleRevision] = []
        with session_factory() as session, session.begin():
            for result in calculated:
                provenance = _provenance(result)
                source_ids = tuple(version_id for version_id, _policy in provenance)
                inventory_versions = {
                    version.id: version
                    for version in session.scalars(
                        select(DatasetVersion).where(
                            DatasetVersion.id.in_(source_ids),
                            DatasetVersion.dataset_type == DatasetType.INVENTORY,
                        )
                    ).all()
                }
                if set(inventory_versions) != set(source_ids):
                    raise LifecyclePersistenceError(
                        "every lifecycle source must be an existing Inventory version"
                    )
                for observation in result.monthly_observations:
                    version = inventory_versions[observation.inventory_version_id]
                    if not (
                        version.period_start
                        <= observation.inventory_month
                        <= version.period_end
                    ):
                        raise LifecyclePersistenceError(
                            "lifecycle observation month is outside its Inventory version"
                        )

                latest = session.scalar(
                    select(ProductionLotLifecycle)
                    .where(
                        ProductionLotLifecycle.product_id == result.product_id,
                        ProductionLotLifecycle.production_date == result.production_date,
                    )
                    .order_by(ProductionLotLifecycle.revision_no.desc())
                    .limit(1)
                    .with_for_update()
                )
                if (
                    latest is not None
                    and _stored_business_values(latest) == _business_values(result)
                    and _stored_provenance(session, latest.id) == provenance
                ):
                    persisted.append(
                        PersistedLifecycleRevision(
                            latest.id,
                            result.product_id,
                            result.production_date,
                            latest.revision_no,
                            False,
                        )
                    )
                    continue

                decision = result.return_decision
                lifecycle = ProductionLotLifecycle(
                    product_id=result.product_id,
                    production_date=result.production_date,
                    revision_no=1 if latest is None else latest.revision_no + 1,
                    warehouse_scope=PLANNING_AVAILABLE_SCOPE,
                    lifecycle_state=result.lifecycle_state,
                    first_inbound_month=result.first_inbound_month,
                    final_sold_out_month=result.final_sold_out_month,
                    consumption_months=result.consumption_months,
                    return_classification=(
                        decision.classification if decision is not None else None
                    ),
                    policy_code=decision.policy_code if decision is not None else None,
                    policy_version=decision.policy_version if decision is not None else None,
                    policy_parameters=(
                        dict(decision.policy_parameters) if decision is not None else None
                    ),
                )
                session.add(lifecycle)
                session.flush()
                for inventory_version_id, warehouse_policy_version in provenance:
                    session.add(
                        ProductionLotSourceVersion(
                            lifecycle_id=lifecycle.id,
                            inventory_version_id=inventory_version_id,
                            warehouse_policy_version=warehouse_policy_version,
                        )
                    )
                session.flush()
                persisted.append(
                    PersistedLifecycleRevision(
                        lifecycle.id,
                        result.product_id,
                        result.production_date,
                        lifecycle.revision_no,
                        True,
                    )
                )
        return tuple(persisted)
    except LifecyclePersistenceError:
        raise
    except IntegrityError as exc:
        raise LifecycleRevisionConflict(
            "lifecycle persistence rolled back after a revision or provenance collision"
        ) from exc
