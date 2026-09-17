"""Persist and read frozen Sales Actual import previews; no Publish behavior."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from webapp.sales.mapping import map_sales_rows
from webapp.sales.models import (
    SalesBatchStatus,
    SalesImportBatch,
    SalesImportCandidate,
    SalesImportRow,
    SalesMappingIssue,
    SalesMappingReason,
)
from webapp.sales.parser import SalesParserBlocked, parse_sales_workbook
from webapp.sales.publish_service import read_publish_gate


class DuplicateSalesFile(ValueError):
    def __init__(self, batch_id: str, status: SalesBatchStatus):
        self.batch_id = batch_id
        self.status = status
        super().__init__(f"source file already exists in batch {batch_id}")


@dataclass(frozen=True)
class SalesPreviewCreated:
    id: int
    batch_id: str
    source_file_sha256: str
    snapshot_month: object
    raw_rows: int
    ready_rows: int
    skipped_rows: int
    ready_qty: Decimal
    skipped_qty: Decimal


def _duplicate(session, file_hash: str):
    return session.execute(
        select(SalesImportBatch.batch_id, SalesImportBatch.status)
        .where(SalesImportBatch.file_hash == file_hash)
    ).first()


def create_sales_preview(
    session_factory,
    path: str | Path,
    *,
    sheet_name: str | None = None,
    filename: str | None = None,
) -> SalesPreviewCreated:
    """Parse, map and persist one immutable preview in a single DB transaction."""
    parsed = parse_sales_workbook(path, sheet_name=sheet_name)
    if parsed.blocked:
        raise SalesParserBlocked(parsed)

    try:
        with session_factory() as session, session.begin():
            duplicate = _duplicate(session, parsed.file_hash)
            if duplicate:
                raise DuplicateSalesFile(duplicate.batch_id, duplicate.status)

            # Mapping runs before any Sales objects are staged, preserving the
            # mapper's existing read-only MDM contract and business rules.
            mapping = map_sales_rows(session, parsed.rows)
            total_qty = sum((row.actual_qty for row in parsed.rows), Decimal(0))
            batch = SalesImportBatch(
                batch_id=str(uuid4()),
                filename=filename or parsed.filename,
                file_hash=parsed.file_hash,
                source_system="DEMO_ERP",
                snapshot_month=parsed.snapshot_month,
                data_date_start=parsed.data_date_start,
                data_date_end=parsed.data_date_end,
                status=SalesBatchStatus.VALIDATED,
                total_rows=mapping.summary.raw_rows,
                ready_rows=mapping.summary.ready_rows,
                skipped_rows=mapping.summary.skipped_rows,
                total_qty=total_qty,
                ready_qty=mapping.summary.ready_qty,
                skipped_qty=mapping.summary.skipped_qty,
            )
            session.add(batch)
            session.flush()

            outcomes = {row.source_row_no: row for row in mapping.rows}
            raw_models = []
            for parsed_row in parsed.rows:
                outcome = outcomes[parsed_row.source_row_no]
                raw = SalesImportRow(
                    import_batch_id=batch.id,
                    **asdict(parsed_row),
                    status=outcome.status,
                    mapping_reason=(
                        SalesMappingReason(outcome.reason) if outcome.reason else None
                    ),
                )
                session.add(raw)
                raw_models.append((raw, outcome))
            session.flush()

            for raw, outcome in raw_models:
                if outcome.snapshot is not None:
                    session.add(SalesImportCandidate(
                        import_batch_id=batch.id,
                        import_row_id=raw.id,
                        **asdict(outcome.snapshot),
                    ))
            for issue in mapping.issues:
                session.add(SalesMappingIssue(
                    import_batch_id=batch.id,
                    issue_type=issue.issue_type,
                    external_code=issue.external_code,
                    external_name=issue.external_name,
                    affected_row_count=issue.affected_row_count,
                    affected_qty=issue.affected_qty,
                    severity=issue.severity,
                ))

            batch.mapped_at = datetime.now(timezone.utc)
            batch.status = SalesBatchStatus.PREVIEW_READY
            session.flush()
            return SalesPreviewCreated(
                batch.id,
                batch.batch_id,
                batch.file_hash,
                batch.snapshot_month,
                batch.total_rows,
                batch.ready_rows,
                batch.skipped_rows,
                batch.ready_qty,
                batch.skipped_qty,
            )
    except IntegrityError:
        # The unique SHA constraint is the concurrency backstop. Translate only
        # when the conflicting file now exists; preserve unrelated DB errors.
        with session_factory() as session:
            duplicate = _duplicate(session, parsed.file_hash)
            if duplicate:
                raise DuplicateSalesFile(duplicate.batch_id, duplicate.status) from None
        raise


def _decimal(value) -> str:
    return str(value if value is not None else Decimal(0))


def _reason_summary(session, batch_id: int, reason: SalesMappingReason):
    count, qty = session.execute(
        select(func.count(SalesImportRow.id), func.coalesce(func.sum(SalesImportRow.actual_qty), 0))
        .where(
            SalesImportRow.import_batch_id == batch_id,
            SalesImportRow.mapping_reason == reason,
        )
    ).one()
    return {"rows": count, "qty": _decimal(qty)}


def read_sales_preview(session_factory, batch_id: str):
    """Read only persisted Sales preview tables; never parse or access MDM."""
    with session_factory() as session:
        batch = session.scalar(select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id))
        if batch is None:
            return None
        unassigned_rows, unassigned_qty = session.execute(
            select(
                func.count(SalesImportCandidate.id),
                func.coalesce(func.sum(SalesImportRow.actual_qty), 0),
            )
            .join(SalesImportRow, SalesImportRow.id == SalesImportCandidate.import_row_id)
            .where(
                SalesImportCandidate.import_batch_id == batch.id,
                or_(
                    SalesImportCandidate.channel_id_snapshot.is_(None),
                    SalesImportCandidate.salesrep_id_snapshot.is_(None),
                ),
            )
        ).one()
        reasons = {
            reason.value.lower(): _reason_summary(session, batch.id, reason)
            for reason in SalesMappingReason
        }
        issues = [
            {
                "issue_type": issue.issue_type.value,
                "external_code": issue.external_code,
                "external_name": issue.external_name,
                "affected_rows": issue.affected_row_count,
                "affected_qty": _decimal(issue.affected_qty),
                "severity": issue.severity.value,
            }
            for issue in session.scalars(
                select(SalesMappingIssue)
                .where(SalesMappingIssue.import_batch_id == batch.id)
                .order_by(SalesMappingIssue.issue_type, SalesMappingIssue.external_code)
            )
        ]
        return {
            "batch": {
                "batch_id": batch.batch_id,
                "filename": batch.filename,
                "source_file_sha256": batch.file_hash,
                "source_system": batch.source_system,
                "snapshot_month": batch.snapshot_month.isoformat(),
                "data_date_start": batch.data_date_start.isoformat() if batch.data_date_start else None,
                "data_date_end": batch.data_date_end.isoformat() if batch.data_date_end else None,
                "status": batch.status.value,
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "mapped_at": batch.mapped_at.isoformat() if batch.mapped_at else None,
                "published_at": batch.published_at.isoformat() if batch.published_at else None,
            },
            "summary": {
                "raw": {"rows": batch.total_rows, "qty": _decimal(batch.total_qty)},
                "ready": {"rows": batch.ready_rows, "qty": _decimal(batch.ready_qty)},
                "skipped": {"rows": batch.skipped_rows, "qty": _decimal(batch.skipped_qty)},
                "kit_parent": reasons[SalesMappingReason.KIT_PARENT.value.lower()],
                "nonpositive_qty": reasons[SalesMappingReason.NONPOSITIVE_QTY.value.lower()],
                "sku_not_found": reasons[SalesMappingReason.SKU_NOT_FOUND.value.lower()],
                "sku_ambiguous": reasons[SalesMappingReason.SKU_AMBIGUOUS.value.lower()],
                "product_unassigned": reasons[SalesMappingReason.PRODUCT_NOT_ASSIGNED.value.lower()],
                "customer_unmatched": reasons[SalesMappingReason.CUSTOMER_NOT_FOUND.value.lower()],
                "channel_salesrep_unassigned": {
                    "rows": unassigned_rows,
                    "qty": _decimal(unassigned_qty),
                },
            },
            "issues": issues,
            "publish_gate": read_publish_gate(session_factory, batch.batch_id),
        }


AGGREGATE_DIMENSIONS = {
    "product": (
        SalesImportCandidate.product_id,
        SalesImportCandidate.product_code_snapshot,
        SalesImportCandidate.product_name_snapshot,
    ),
    "sku": (
        SalesImportCandidate.sku_id,
        SalesImportCandidate.sku_code_snapshot,
        SalesImportCandidate.sku_name_snapshot,
        SalesImportCandidate.product_code_snapshot,
        SalesImportCandidate.product_name_snapshot,
    ),
    "channel-product": (
        SalesImportCandidate.channel_id_snapshot,
        SalesImportCandidate.channel_code_snapshot,
        SalesImportCandidate.channel_name_snapshot,
        SalesImportCandidate.product_code_snapshot,
        SalesImportCandidate.product_name_snapshot,
    ),
    "salesrep-product": (
        SalesImportCandidate.salesrep_id_snapshot,
        SalesImportCandidate.salesrep_code_snapshot,
        SalesImportCandidate.salesrep_name_snapshot,
        SalesImportCandidate.product_code_snapshot,
        SalesImportCandidate.product_name_snapshot,
    ),
}


def read_sales_aggregate(session_factory, batch_id: str, dimension: str, *, offset=0, limit=100):
    columns = AGGREGATE_DIMENSIONS.get(dimension)
    if columns is None:
        raise ValueError("unsupported aggregate dimension")
    with session_factory() as session:
        batch = session.scalar(select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id))
        if batch is None:
            return None
        grouped = (
            select(*columns, func.count(SalesImportCandidate.id), func.sum(SalesImportRow.actual_qty))
            .join(SalesImportRow, SalesImportRow.id == SalesImportCandidate.import_row_id)
            .where(SalesImportCandidate.import_batch_id == batch.id)
            .group_by(*columns)
        )
        total = session.scalar(select(func.count()).select_from(grouped.subquery()))
        rows = session.execute(
            grouped.order_by(func.sum(SalesImportRow.actual_qty).desc(), *columns)
            .offset(offset).limit(limit)
        ).all()
        names = [column.key for column in columns]
        items = []
        for row in rows:
            item = dict(zip(names, row[:len(columns)]))
            item.update(rows=row[-2], qty=_decimal(row[-1]))
            items.append(item)
        return {"dimension": dimension, "total": total, "offset": offset, "limit": limit, "items": items}


def read_sales_rows(session_factory, batch_id: str, *, status=None, mapping_reason=None, offset=0, limit=100):
    with session_factory() as session:
        batch = session.scalar(select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id))
        if batch is None:
            return None
        filters = [SalesImportRow.import_batch_id == batch.id]
        if status:
            filters.append(SalesImportRow.status == status)
        if mapping_reason:
            filters.append(SalesImportRow.mapping_reason == mapping_reason)
        query = select(SalesImportRow).where(*filters)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(SalesImportRow.source_row_no).offset(offset).limit(limit))
        return {
            "total": total, "offset": offset, "limit": limit,
            "items": [{
                "source_row_no": row.source_row_no,
                "source_document_no": row.source_document_no,
                "source_line_no": row.source_line_no,
                "sales_date": row.sales_date.isoformat() if row.sales_date else None,
                "source_sku_code": row.source_sku_code,
                "source_sku_name": row.source_sku_name,
                "source_product_type": row.source_product_type,
                "source_customer_name": row.source_customer_name,
                "actual_qty": _decimal(row.actual_qty),
                "status": row.status.value,
                "mapping_reason": row.mapping_reason.value if row.mapping_reason else None,
            } for row in rows],
        }


def list_sales_batches(session_factory, *, offset=0, limit=50):
    with session_factory() as session:
        total = session.scalar(select(func.count()).select_from(SalesImportBatch))
        batches = session.scalars(
            select(SalesImportBatch).order_by(SalesImportBatch.id.desc()).offset(offset).limit(limit)
        )
        return {
            "total": total, "offset": offset, "limit": limit,
            "items": [{
                "batch_id": batch.batch_id,
                "filename": batch.filename,
                "source_file_sha256": batch.file_hash,
                "snapshot_month": batch.snapshot_month.isoformat(),
                "status": batch.status.value,
                "raw_rows": batch.total_rows,
                "ready_rows": batch.ready_rows,
                "skipped_rows": batch.skipped_rows,
                "ready_qty": _decimal(batch.ready_qty),
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "mapped_at": batch.mapped_at.isoformat() if batch.mapped_at else None,
            } for batch in batches],
        }
