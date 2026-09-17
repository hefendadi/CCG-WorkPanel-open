"""Append-only persistence and reads for manually supplied Forecast versions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webapp.mdm.models import Channel, Product
from webapp.ordering.models import (
    CycleProductSnapshot, CycleChannelSnapshot,
    CycleStatus,
    ForecastLine,
    ForecastVersion,
    PlanningCycle,
)


CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ForecastVersionError(RuntimeError):
    pass


class ForecastCycleNotFound(ForecastVersionError):
    pass


class ForecastCycleLocked(ForecastVersionError):
    pass


class ForecastLineInvalid(ForecastVersionError):
    pass


class ForecastMdmUnresolved(ForecastVersionError):
    pass


class ForecastDuplicateGrain(ForecastVersionError):
    pass


class ForecastVersionConflict(ForecastVersionError):
    pass


@dataclass(frozen=True)
class ForecastLineInput:
    channel_stable_id: str
    product_stable_id: str
    forecast_month: date
    forecast_qty: Decimal


@dataclass(frozen=True)
class ForecastVersionWriteResult:
    forecast_version_id: int
    cycle_id: int
    cycle_code: str
    version_no: int
    line_count: int


@dataclass(frozen=True)
class ForecastLineView:
    channel_id: int
    channel_stable_id: str
    channel_name: str
    product_id: int
    product_stable_id: str
    product_name: str
    forecast_month: date
    forecast_qty: Decimal


@dataclass(frozen=True)
class ForecastVersionView:
    forecast_version_id: int
    cycle_id: int
    cycle_code: str
    cycle_status: CycleStatus
    version_no: int
    source_checksum: str | None
    created_by: str
    created_at: datetime
    lines: tuple[ForecastLineView, ...]


@dataclass(frozen=True)
class _ValidatedLine:
    channel_stable_id: str
    product_stable_id: str
    forecast_month: date
    forecast_qty: Decimal


def _required_text(value: object, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ForecastLineInvalid(f"{field} is required")
    return text


def _quantity(value: object) -> Decimal:
    try:
        quantity = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ForecastLineInvalid("forecast_qty must be a finite non-negative number") from exc
    if not quantity.is_finite() or quantity < 0:
        raise ForecastLineInvalid("forecast_qty must be a finite non-negative number")
    return quantity


def _validate_lines(lines: Iterable[ForecastLineInput]) -> tuple[_ValidatedLine, ...]:
    validated: list[_ValidatedLine] = []
    for line in lines:
        if not isinstance(line.forecast_month, date) or line.forecast_month.day != 1:
            raise ForecastLineInvalid("forecast_month must be the first day of a natural month")
        validated.append(
            _ValidatedLine(
                channel_stable_id=_required_text(
                    line.channel_stable_id, "channel_stable_id"
                ),
                product_stable_id=_required_text(
                    line.product_stable_id, "product_stable_id"
                ),
                forecast_month=line.forecast_month,
                forecast_qty=_quantity(line.forecast_qty),
            )
        )
    return tuple(validated)


def write_forecast_version(
    session_factory: Callable[[], Session],
    *,
    cycle_code: str,
    lines: Iterable[ForecastLineInput],
    created_by: str,
    source_checksum: str | None = None,
) -> ForecastVersionWriteResult:
    cycle_code = _required_text(cycle_code, "cycle_code")
    created_by = _required_text(created_by, "created_by")
    validated = _validate_lines(lines)
    if source_checksum is not None and not CHECKSUM_PATTERN.fullmatch(source_checksum):
        raise ForecastLineInvalid(
            "source_checksum must be a lowercase SHA-256 hex digest"
        )

    try:
        with session_factory() as session, session.begin():
            cycle = session.scalar(
                select(PlanningCycle)
                .where(PlanningCycle.cycle_code == cycle_code)
                .with_for_update()
            )
            if cycle is None:
                raise ForecastCycleNotFound(f"planning cycle not found: {cycle_code}")
            if cycle.status != CycleStatus.OPEN:
                raise ForecastCycleLocked(
                    f"planning cycle is not open: {cycle_code}"
                )

            channel_ids = {line.channel_stable_id for line in validated}
            product_ids = {line.product_stable_id for line in validated}
            channels = {
                row.stable_id: row
                for row in session.scalars(
                    select(Channel).where(Channel.stable_id.in_(channel_ids))
                ).all()
            }
            products = {
                row.stable_id: row
                for row in session.scalars(
                    select(Product).where(Product.stable_id.in_(product_ids))
                ).all()
            }
            if set(channels) != channel_ids:
                unresolved = sorted(channel_ids - set(channels))
                raise ForecastMdmUnresolved(
                    f"channels are unresolved in MDM: {', '.join(unresolved)}"
                )
            if set(products) != product_ids:
                unresolved = sorted(product_ids - set(products))
                raise ForecastMdmUnresolved(
                    f"Products are unresolved in MDM: {', '.join(unresolved)}"
                )

            grains = [
                (
                    channels[line.channel_stable_id].id,
                    products[line.product_stable_id].id,
                    line.forecast_month,
                )
                for line in validated
            ]
            if len(grains) != len(set(grains)):
                raise ForecastDuplicateGrain(
                    "one Forecast version cannot repeat a channel, Product, and month grain"
                )

            latest = session.scalar(
                select(ForecastVersion)
                .where(ForecastVersion.cycle_id == cycle.id)
                .order_by(ForecastVersion.version_no.desc())
                .limit(1)
            )
            version = ForecastVersion(
                cycle_id=cycle.id,
                version_no=1 if latest is None else latest.version_no + 1,
                source_checksum=source_checksum,
                created_by=created_by,
            )
            session.add(version)
            session.flush()

            for line in validated:
                session.add(
                    ForecastLine(
                        forecast_version_id=version.id,
                        channel_id=channels[line.channel_stable_id].id,
                        product_id=products[line.product_stable_id].id,
                        forecast_month=line.forecast_month,
                        forecast_qty=line.forecast_qty,
                        created_by=created_by,
                    )
                )
            session.flush()
            result = ForecastVersionWriteResult(
                forecast_version_id=version.id,
                cycle_id=cycle.id,
                cycle_code=cycle.cycle_code,
                version_no=version.version_no,
                line_count=len(validated),
            )
        return result
    except (
        ForecastCycleNotFound,
        ForecastCycleLocked,
        ForecastLineInvalid,
        ForecastMdmUnresolved,
        ForecastDuplicateGrain,
    ):
        raise
    except IntegrityError as exc:
        raise ForecastVersionConflict(
            "Forecast version write rolled back after a version or grain collision"
        ) from exc


def _read_version(session: Session, cycle: PlanningCycle, version: ForecastVersion) -> ForecastVersionView:
    lines = session.scalars(select(ForecastLine).where(
        ForecastLine.forecast_version_id == version.id)).all()
    if cycle.status == CycleStatus.LOCKED:
        products = {p.product_id: (p.product_stable_id, p.product_name) for p in session.scalars(
            select(CycleProductSnapshot).where(CycleProductSnapshot.cycle_id == cycle.id))}
        channels = {c.channel_id: (c.channel_stable_id, c.channel_name) for c in session.scalars(
            select(CycleChannelSnapshot).where(CycleChannelSnapshot.cycle_id == cycle.id))}
    else:
        products = {p.id: (p.stable_id, p.product_name) for p in session.scalars(
            select(Product).where(Product.id.in_({line.product_id for line in lines})))}
        channels = {c.id: (c.stable_id, c.channel_name) for c in session.scalars(
            select(Channel).where(Channel.id.in_({line.channel_id for line in lines})))}
    if any(line.product_id not in products or line.channel_id not in channels for line in lines):
        raise ForecastMdmUnresolved('Forecast history display identity is missing')
    lines.sort(key=lambda line: (channels[line.channel_id][0], products[line.product_id][0], line.forecast_month))
    return ForecastVersionView(
        forecast_version_id=version.id,
        cycle_id=cycle.id,
        cycle_code=cycle.cycle_code,
        cycle_status=cycle.status,
        version_no=version.version_no,
        source_checksum=version.source_checksum,
        created_by=version.created_by,
        created_at=version.created_at,
        lines=tuple(
            ForecastLineView(
                channel_id=line.channel_id,
                channel_stable_id=channels[line.channel_id][0],
                channel_name=channels[line.channel_id][1],
                product_id=line.product_id,
                product_stable_id=products[line.product_id][0],
                product_name=products[line.product_id][1],
                forecast_month=line.forecast_month,
                forecast_qty=line.forecast_qty,
            )
            for line in lines
        ),
    )


def read_forecast_version_history(
    session_factory: Callable[[], Session], cycle_code: str
) -> tuple[ForecastVersionView, ...]:
    with session_factory() as session, session.begin():
        cycle = session.scalar(
            select(PlanningCycle).where(PlanningCycle.cycle_code == cycle_code).with_for_update()
        )
        if cycle is None:
            raise ForecastCycleNotFound(f"planning cycle not found: {cycle_code}")
        versions = session.scalars(
            select(ForecastVersion)
            .where(ForecastVersion.cycle_id == cycle.id)
            .order_by(ForecastVersion.version_no)
        ).all()
        return tuple(_read_version(session, cycle, version) for version in versions)


def read_latest_forecast_version(
    session_factory: Callable[[], Session], cycle_code: str
) -> ForecastVersionView | None:
    history = read_forecast_version_history(session_factory, cycle_code)
    if not history:
        return None
    if history[0].cycle_status == CycleStatus.LOCKED:
        with session_factory() as session:
            cycle = session.get(PlanningCycle, history[0].cycle_id)
            return next(v for v in history if v.forecast_version_id == cycle.forecast_version_id)
    return history[-1]
