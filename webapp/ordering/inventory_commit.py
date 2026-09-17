"""Transactional Inventory preview commit service.

This service writes one immutable Inventory version and its raw/derived facts.
It has no API, UI, lifecycle, Forecast, Final Order, or Incoming behavior.
"""

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

from webapp.ordering.inventory_import import InventoryPreviewResult
from webapp.ordering.models import (
    DatasetType,
    DatasetVersion,
    InventoryProductMonth,
    InventoryRaw,
    InventorySkuMonth,
    PLANNING_AVAILABLE_SCOPE,
)


CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InventoryCommitError(RuntimeError):
    pass


class InventoryPreviewBlocked(InventoryCommitError):
    pass


class InventoryVersionConflict(InventoryCommitError):
    pass


@dataclass(frozen=True)
class InventoryVersionRequest:
    version_key: str
    version_label: str
    source_name: str
    source_checksum: str
    created_by: str


@dataclass(frozen=True)
class InventoryCommitResult:
    dataset_version_id: int
    version_key: str
    raw_rows: int
    sku_month_rows: int
    product_month_rows: int


def inventory_source_checksum(path: str | Path) -> str:
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


def _sum_rows(rows, identity):
    grouped: dict[tuple[int, ...], dict[str, Any]] = {}
    for row in rows:
        key = identity(row)
        item = grouped.setdefault(
            key,
            {
                "row": row,
                "opening_qty": Decimal("0"),
                "receipt_qty": Decimal("0"),
                "issue_qty": Decimal("0"),
                "ending_qty": Decimal("0"),
            },
        )
        for field in ("opening_qty", "receipt_qty", "issue_qty", "ending_qty"):
            item[field] += getattr(row, field)
    return grouped


def commit_inventory_preview(
    session_factory: Callable[[], Session],
    preview: InventoryPreviewResult,
    request: InventoryVersionRequest,
) -> InventoryCommitResult:
    """Commit all four Inventory layers atomically, or write nothing."""

    if not preview.commit_allowed:
        raise InventoryPreviewBlocked("Inventory preview contains blocking errors")
    if not CHECKSUM_PATTERN.fullmatch(request.source_checksum):
        raise InventoryCommitError("source_checksum must be a lowercase SHA-256 hex digest")

    inventory_month = preview.period_start
    planning_rows = [row for row in preview.valid_rows if row.eligible_for_derived_facts]
    sku_groups = _sum_rows(planning_rows, lambda row: (row.sku_id,))
    product_groups = _sum_rows(planning_rows, lambda row: (row.product_id,))

    try:
        with session_factory() as session, session.begin():
            duplicate_key = session.scalar(
                select(DatasetVersion.id).where(DatasetVersion.version_key == request.version_key)
            )
            if duplicate_key is not None:
                raise InventoryVersionConflict(f"version_key already exists: {request.version_key}")
            duplicate_source = session.scalar(
                select(DatasetVersion.id).where(
                    DatasetVersion.dataset_type == DatasetType.INVENTORY,
                    DatasetVersion.period_start == inventory_month,
                    DatasetVersion.period_end == inventory_month,
                    DatasetVersion.source_checksum == request.source_checksum,
                )
            )
            if duplicate_source is not None:
                raise InventoryVersionConflict(
                    "the same source checksum was already imported for this inventory month"
                )

            version = DatasetVersion(
                dataset_type=DatasetType.INVENTORY,
                version_key=request.version_key,
                version_label=request.version_label,
                period_start=inventory_month,
                period_end=inventory_month,
                source_name=request.source_name,
                source_checksum=request.source_checksum,
                created_by=request.created_by,
            )
            session.add(version)
            session.flush()

            for row in preview.valid_rows:
                session.add(
                    InventoryRaw(
                        dataset_version_id=version.id,
                        source_row_no=row.source_row_number,
                        inventory_month=inventory_month,
                        source_sku_code=row.sku_code,
                        sku_id=row.sku_id,
                        sku_stable_id=row.sku_stable_id,
                        product_id=row.product_id,
                        product_stable_id=row.product_stable_id,
                        # This ERP extract has no warehouse-code column.  The explicit
                        # prefix prevents the source name from masquerading as a code.
                        warehouse_code=f"NAME:{row.warehouse_name}",
                        warehouse_name=row.warehouse_name,
                        production_date=row.production_date,
                        batch_no=row.erp_batch_no,
                        opening_qty=row.opening_qty,
                        receipt_qty=row.receipt_qty,
                        issue_qty=row.issue_qty,
                        ending_qty=row.ending_qty,
                        raw_payload={
                            "source_row_number": row.source_row_number,
                            "warehouse_planning_status": row.warehouse_planning_status.value,
                            "planning_policy_version": row.planning_policy_version,
                            "erp_values": {
                                key: _json_value(value) for key, value in row.raw_values.items()
                            },
                        },
                    )
                )

            for item in sku_groups.values():
                row = item["row"]
                session.add(
                    InventorySkuMonth(
                        dataset_version_id=version.id,
                        inventory_month=inventory_month,
                        sku_id=row.sku_id,
                        product_id=row.product_id,
                        warehouse_scope=PLANNING_AVAILABLE_SCOPE,
                        warehouse_policy_version=row.planning_policy_version,
                        **{field: item[field] for field in (
                            "opening_qty", "receipt_qty", "issue_qty", "ending_qty"
                        )},
                    )
                )
            for item in product_groups.values():
                row = item["row"]
                session.add(
                    InventoryProductMonth(
                        dataset_version_id=version.id,
                        inventory_month=inventory_month,
                        product_id=row.product_id,
                        warehouse_scope=PLANNING_AVAILABLE_SCOPE,
                        warehouse_policy_version=row.planning_policy_version,
                        **{field: item[field] for field in (
                            "opening_qty", "receipt_qty", "issue_qty", "ending_qty"
                        )},
                    )
                )
            session.flush()
            result = InventoryCommitResult(
                dataset_version_id=version.id,
                version_key=version.version_key,
                raw_rows=len(preview.valid_rows),
                sku_month_rows=len(sku_groups),
                product_month_rows=len(product_groups),
            )
        return result
    except InventoryVersionConflict:
        raise
    except IntegrityError as exc:
        raise InventoryCommitError("Inventory commit rolled back after a database constraint error") from exc
