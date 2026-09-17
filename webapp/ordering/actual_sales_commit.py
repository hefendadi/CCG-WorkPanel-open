"""Atomic persistence for validated Actual Sales and its two monthly facts."""

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

from webapp.ordering.actual_sales_import import (
    ActualSalesPreviewResult,
    aggregate_channel_product_month,
    aggregate_product_production_month,
)
from webapp.ordering.models import (
    ActualChannelProductMonth,
    ActualProductProductionMonth,
    ActualRaw,
    DatasetType,
    DatasetVersion,
)


CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ActualSalesCommitError(RuntimeError):
    pass


class ActualSalesPreviewBlocked(ActualSalesCommitError):
    pass


class ActualSalesVersionConflict(ActualSalesCommitError):
    pass


@dataclass(frozen=True)
class ActualSalesVersionRequest:
    version_key: str
    version_label: str
    source_name: str
    source_checksum: str
    created_by: str


@dataclass(frozen=True)
class ActualSalesCommitResult:
    dataset_version_id: int
    version_key: str
    raw_rows: int
    channel_product_month_rows: int
    product_production_month_rows: int


def actual_sales_source_checksum(path: str | Path) -> str:
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


def commit_actual_sales_preview(
    session_factory: Callable[[], Session],
    preview: ActualSalesPreviewResult,
    request: ActualSalesVersionRequest,
) -> ActualSalesCommitResult:
    if not preview.commit_allowed or preview.period_start is None or preview.period_end is None:
        raise ActualSalesPreviewBlocked("Actual Sales preview contains blocking errors")
    if not CHECKSUM_PATTERN.fullmatch(request.source_checksum):
        raise ActualSalesCommitError(
            "source_checksum must be a lowercase SHA-256 hex digest"
        )

    channel_product_totals = aggregate_channel_product_month(preview.valid_rows)
    product_production_totals = aggregate_product_production_month(preview.valid_rows)
    try:
        with session_factory() as session, session.begin():
            duplicate_key = session.scalar(
                select(DatasetVersion.id).where(
                    DatasetVersion.version_key == request.version_key
                )
            )
            if duplicate_key is not None:
                raise ActualSalesVersionConflict(
                    f"version_key already exists: {request.version_key}"
                )
            duplicate_source = session.scalar(
                select(DatasetVersion.id).where(
                    DatasetVersion.dataset_type == DatasetType.ACTUAL,
                    DatasetVersion.period_start == preview.period_start,
                    DatasetVersion.period_end == preview.period_end,
                    DatasetVersion.source_checksum == request.source_checksum,
                )
            )
            if duplicate_source is not None:
                raise ActualSalesVersionConflict(
                    "the same source checksum was already imported for this Actual Sales period"
                )

            version = DatasetVersion(
                dataset_type=DatasetType.ACTUAL,
                version_key=request.version_key,
                version_label=request.version_label,
                period_start=preview.period_start,
                period_end=preview.period_end,
                source_name=request.source_name,
                source_checksum=request.source_checksum,
                created_by=request.created_by,
            )
            session.add(version)
            session.flush()

            for row in preview.valid_rows:
                session.add(
                    ActualRaw(
                        dataset_version_id=version.id,
                        source_row_no=row.source_row_number,
                        erp_customer_code=f"NAME:{row.customer_name}",
                        erp_customer_name=row.customer_name,
                        source_sku_code=row.sku_code,
                        source_sku_name=row.sku_name,
                        customer_id=row.customer_id,
                        customer_stable_id=row.customer_stable_id,
                        channel_id=row.channel_id,
                        channel_stable_id=row.channel_stable_id,
                        sku_id=row.sku_id,
                        sku_stable_id=row.sku_stable_id,
                        product_id=row.product_id,
                        product_stable_id=row.product_stable_id,
                        sales_date=row.sales_date,
                        shipped_qty=row.shipped_qty,
                        warehouse_code=f"NAME:{row.warehouse_name}",
                        warehouse_name=row.warehouse_name,
                        production_date=row.production_date,
                        batch_no=row.batch_no,
                        raw_payload={
                            "source_row_number": row.source_row_number,
                            "erp_values": {
                                key: _json_value(value)
                                for key, value in row.raw_values.items()
                            },
                        },
                    )
                )

            for total in channel_product_totals:
                session.add(
                    ActualChannelProductMonth(
                        dataset_version_id=version.id,
                        channel_id=total.channel_id,
                        product_id=total.product_id,
                        actual_month=total.actual_month,
                        shipped_qty=total.shipped_qty,
                    )
                )
            for total in product_production_totals:
                session.add(
                    ActualProductProductionMonth(
                        dataset_version_id=version.id,
                        product_id=total.product_id,
                        production_date=total.production_date,
                        actual_month=total.actual_month,
                        shipped_qty=total.shipped_qty,
                    )
                )
            session.flush()
            result = ActualSalesCommitResult(
                dataset_version_id=version.id,
                version_key=version.version_key,
                raw_rows=len(preview.valid_rows),
                channel_product_month_rows=len(channel_product_totals),
                product_production_month_rows=len(product_production_totals),
            )
        return result
    except ActualSalesVersionConflict:
        raise
    except IntegrityError as exc:
        raise ActualSalesCommitError(
            "Actual Sales commit rolled back after a database constraint error"
        ) from exc
