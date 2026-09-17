"""Atomic persistence for an immutable Incoming Supply snapshot."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webapp.ordering.incoming_import import (
    IncomingPreviewResult,
    ProductFutureSupplyTotal,
)
from webapp.ordering.models import IncomingSnapshot, IncomingSnapshotLine


CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class IncomingCommitError(RuntimeError):
    pass


class IncomingPreviewBlocked(IncomingCommitError):
    pass


class IncomingSnapshotConflict(IncomingCommitError):
    pass


@dataclass(frozen=True)
class IncomingSnapshotRequest:
    snapshot_key: str
    snapshot_at: datetime
    source_name: str
    source_checksum: str
    created_by: str


@dataclass(frozen=True)
class IncomingCommitResult:
    snapshot_id: int
    snapshot_key: str
    line_rows: int
    product_rows: int
    current_future_supply_qty: Decimal
    product_totals: tuple[ProductFutureSupplyTotal, ...]


def incoming_source_checksum(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def commit_incoming_preview(
    session_factory: Callable[[], Session],
    preview: IncomingPreviewResult,
    request: IncomingSnapshotRequest,
) -> IncomingCommitResult:
    if not preview.commit_allowed:
        raise IncomingPreviewBlocked("Incoming preview contains blocking errors")
    if not CHECKSUM_PATTERN.fullmatch(request.source_checksum):
        raise IncomingCommitError(
            "source_checksum must be a lowercase SHA-256 hex digest"
        )

    try:
        with session_factory() as session, session.begin():
            duplicate_key = session.scalar(
                select(IncomingSnapshot.id).where(
                    IncomingSnapshot.snapshot_key == request.snapshot_key
                )
            )
            if duplicate_key is not None:
                raise IncomingSnapshotConflict(
                    f"snapshot_key already exists: {request.snapshot_key}"
                )
            snapshot = IncomingSnapshot(
                snapshot_key=request.snapshot_key,
                snapshot_at=request.snapshot_at,
                source_name=request.source_name,
                source_checksum=request.source_checksum,
                created_by=request.created_by,
            )
            session.add(snapshot)
            session.flush()

            for row in preview.valid_rows:
                session.add(
                    IncomingSnapshotLine(
                        snapshot_id=snapshot.id,
                        source_sheet_name=row.source_sheet_name,
                        source_row_no=row.source_row_number,
                        source_sku_code=row.sku_code,
                        source_sku_name=row.sku_name,
                        sku_id=row.sku_id,
                        sku_stable_id=row.sku_stable_id,
                        product_id=row.product_id,
                        product_stable_id=row.product_stable_id,
                        expected_arrival_date=row.expected_arrival_date,
                        incoming_qty=row.incoming_qty,
                        warehouse_code=None,
                        warehouse_entry_recorded=row.warehouse_entry_recorded,
                        production_date=None,
                        batch_no=row.batch_no,
                        raw_payload={
                            "source_sheet_name": row.source_sheet_name,
                            "source_row_number": row.source_row_number,
                            "order_group": row.order_group,
                            "order_no": row.order_no,
                            "order_qty": _json_value(row.order_qty),
                            "shipped_total": _json_value(row.shipped_total),
                            "difference_qty": _json_value(row.difference_qty),
                            "erp_values": {
                                key: _json_value(value)
                                for key, value in row.raw_values.items()
                            },
                        },
                    )
                )
            session.flush()
            result = IncomingCommitResult(
                snapshot_id=snapshot.id,
                snapshot_key=snapshot.snapshot_key,
                line_rows=len(preview.valid_rows),
                product_rows=len(preview.product_totals),
                current_future_supply_qty=preview.summary.current_future_supply_qty,
                product_totals=preview.product_totals,
            )
        return result
    except IncomingSnapshotConflict:
        raise
    except IntegrityError as exc:
        raise IncomingCommitError(
            "Incoming commit rolled back after a database constraint error"
        ) from exc
