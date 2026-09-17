"""Publish one PREVIEW_READY batch as the month's current cumulative snapshot.

Contract (public edition):
- No re-parse, no re-mapping, no MDM reads: only the frozen Sales tables below.
- Publish is a single atomic transaction: DELETE the whole snapshot_month Fact
  set, then INSERT every Candidate of the published batch. Old Facts are not
  versioned; the previous month version survives as the replaced Batch lineage.
- The month's current Batch is PUBLISHED AND replaced_at IS NULL. A re-publish of
  that same batch is an idempotent no-op; a superseded batch cannot re-publish.
- On MySQL an advisory lock keyed by source_system + snapshot_month is acquired
  on the same connection before any transaction work, so two admins cannot
  interleave two whole-month replaces of the same month.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import delete, func, insert, literal, or_, select, text, update
from sqlalchemy.engine import Connection

from webapp.sales.models import (
    ABNORMAL_DROP_THRESHOLD,
    PUBLISH_CONFIRMATION_CODES,
    SalesBatchStatus,
    SalesFact,
    SalesImportBatch,
    SalesImportCandidate,
    SalesImportRow,
    SalesPublishConfirmation,
    SalesRowStatus,
)

# Demo default; deployments may configure their own review policy.
DROP_THRESHOLD = Decimal(__import__("os").environ.get("CCGTOOLS_SALES_DROP_THRESHOLD", "0.8"))
if not DROP_THRESHOLD.is_finite() or not Decimal("0") < DROP_THRESHOLD <= Decimal("1"):
    raise ValueError("CCGTOOLS_SALES_DROP_THRESHOLD must be in (0, 1]")

# Gate outcome constants.
READY = "READY"
REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
BLOCKED = "BLOCKED"

# Gate / block codes.
BATCH_NOT_PREVIEW_READY = "BATCH_NOT_PREVIEW_READY"
BATCH_SUPERSEDED = "BATCH_SUPERSEDED"
PREVIEW_INTEGRITY_INVALID = "PREVIEW_INTEGRITY_INVALID"
CURRENT_MONTH_FACT_INTEGRITY_ERROR = "CURRENT_MONTH_FACT_INTEGRITY_ERROR"
DATA_COVERAGE_REGRESSION = "DATA_COVERAGE_REGRESSION"
DATA_COVERAGE_UNAVAILABLE = "DATA_COVERAGE_UNAVAILABLE"
CONFIRMATION_MISMATCH = "CONFIRMATION_MISMATCH"
FACT_INSERT_MISMATCH = "FACT_INSERT_MISMATCH"

# Warning codes.
CUMULATIVE_QTY_DECLINE = "CUMULATIVE_QTY_DECLINE"


class PublishError(Exception):
    """Base publish error with an optional structured payload."""

    def __init__(self, message: str, *, code: Optional[str] = None, payload=None):
        self.code = code
        self.payload = payload
        super().__init__(message)


class PublishNotFound(PublishError):
    pass


class PublishValidationError(PublishError):
    pass


class PublishLockBusy(PublishError):
    pass


class PublishBatchSuperseded(PublishError):
    def __init__(self, current_batch_id: Optional[str]):
        self.current_batch_id = current_batch_id
        payload = {"code": BATCH_SUPERSEDED, "current_batch_id": current_batch_id}
        super().__init__(BATCH_SUPERSEDED, code=BATCH_SUPERSEDED, payload=payload)


class PublishGateBlocked(PublishError):
    def __init__(self, payload):
        super().__init__(BLOCKED, code=BLOCKED, payload=payload)


class PublishConfirmationRequired(PublishError):
    def __init__(self, payload):
        super().__init__(REQUIRES_CONFIRMATION, code=REQUIRES_CONFIRMATION, payload=payload)


@dataclass(frozen=True)
class PublishResult:
    batch_id: str
    status: str = "PUBLISHED"
    published_at: Optional[datetime] = None
    replaced_batch_id: Optional[str] = None
    fact_rows: int = 0
    fact_qty: Decimal = Decimal(0)
    warnings: tuple = ()
    confirmations_recorded: tuple = ()
    idempotent: bool = False
    snapshot_month: Optional[date] = None
    data_end_date: Optional[date] = None


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _month_fact_stats(connection: Connection, source_system: str, snapshot_month: date):
    rows, qty = connection.execute(
        select(
            func.count(SalesFact.id),
            func.coalesce(func.sum(SalesFact.actual_qty), 0),
        ).where(
            SalesFact.source_system == source_system,
            SalesFact.snapshot_month == snapshot_month,
        )
    ).one()
    return int(rows), Decimal(qty)


def _current_month_batch(
    connection: Connection,
    source_system: str,
    snapshot_month: date,
    *,
    exclude_batch_id: Optional[int] = None,
):
    stmt = select(SalesImportBatch).where(
        SalesImportBatch.source_system == source_system,
        SalesImportBatch.snapshot_month == snapshot_month,
        SalesImportBatch.status == SalesBatchStatus.PUBLISHED,
        SalesImportBatch.replaced_at.is_(None),
    )
    if exclude_batch_id is not None:
        stmt = stmt.where(SalesImportBatch.id != exclude_batch_id)
    return connection.execute(stmt).mappings().first()


def _batch_mapping(connection: Connection, batch_id: str, *, for_update=False):
    stmt = select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id)
    if for_update:
        stmt = stmt.with_for_update()
    return connection.execute(stmt).mappings().first()


def _lock_name(source_system: str, snapshot_month: date) -> str:
    return f"sales:publish:{source_system}:{snapshot_month.isoformat()}"


def _preview_integrity_violations(
    connection: Connection, batch_id: int, batch: dict
) -> list[str]:
    """Recompute the frozen Preview set from raw tables (issues excluded by design).

    Correct integrity is purely the Raw/Candidate/Batch counter reconciliation
    below; sales_mapping_issue rows are audit/display only and never enter it.
    """
    where = SalesImportRow.import_batch_id == batch_id

    def row_stats(*filters):
        stmt = select(
            func.count(SalesImportRow.id),
            func.coalesce(func.sum(SalesImportRow.actual_qty), 0),
        ).where(where, *filters)
        return connection.execute(stmt).one()

    raw_rows, raw_qty = row_stats()
    ready_rows, ready_qty = row_stats(SalesImportRow.status == SalesRowStatus.READY)
    skipped_rows, skipped_qty = row_stats(SalesImportRow.status == SalesRowStatus.SKIPPED)
    pending_rows = connection.scalar(
        select(func.count(SalesImportRow.id)).where(
            where, SalesImportRow.status == SalesRowStatus.PENDING
        )
    )
    candidate_rows, candidate_qty, candidate_unique_rows = connection.execute(
        select(
            func.count(SalesImportCandidate.id),
            func.coalesce(func.sum(SalesImportRow.actual_qty), 0),
            func.count(func.distinct(SalesImportCandidate.import_row_id)),
        )
        .join(SalesImportRow, SalesImportRow.id == SalesImportCandidate.import_row_id)
        .where(SalesImportCandidate.import_batch_id == batch_id)
    ).one()
    ready_without_candidate = connection.scalar(
        select(func.count(SalesImportRow.id))
        .outerjoin(
            SalesImportCandidate,
            SalesImportCandidate.import_row_id == SalesImportRow.id,
        )
        .where(
            where,
            SalesImportRow.status == SalesRowStatus.READY,
            SalesImportCandidate.id.is_(None),
        )
    )
    misaligned_candidates = connection.scalar(
        select(func.count(SalesImportCandidate.id))
        .join(SalesImportRow, SalesImportRow.id == SalesImportCandidate.import_row_id)
        .where(SalesImportCandidate.import_batch_id == batch_id)
        .where(
            or_(
                SalesImportRow.import_batch_id != SalesImportCandidate.import_batch_id,
                SalesImportRow.status != SalesRowStatus.READY,
            )
        )
    )

    violations = []
    checks = (
        ("raw_count", raw_rows, batch["total_rows"]),
        ("row_sum", ready_rows + skipped_rows, raw_rows),
        ("pending", pending_rows, 0),
        ("ready_count", ready_rows, batch["ready_rows"]),
        ("skipped_count", skipped_rows, batch["skipped_rows"]),
        ("candidate_count", candidate_rows, batch["ready_rows"]),
        ("candidate_unique_rows", candidate_unique_rows, candidate_rows),
        ("ready_without_candidate", ready_without_candidate, 0),
        ("misaligned_candidate", misaligned_candidates, 0),
        ("ready_qty", ready_qty, batch["ready_qty"]),
        ("candidate_qty", candidate_qty, batch["ready_qty"]),
        ("skipped_qty", skipped_qty, batch["skipped_qty"]),
        ("raw_qty", raw_qty, batch["total_qty"]),
    )
    for label, actual, expected in checks:
        if actual != expected:
            violations.append(f"{label}:{actual}!=:{expected}")
    return violations


def _evaluate_gate(connection: Connection, batch: dict) -> dict:
    """Return one of READY / REQUIRES_CONFIRMATION / BLOCKED with its payload."""
    blocking = []
    warnings = []
    required_confirmations = []
    comparison = None

    violations = _preview_integrity_violations(connection, batch["id"], batch)
    if violations:
        blocking.append({"code": PREVIEW_INTEGRITY_INVALID, "violations": violations})

    current = _current_month_batch(
        connection, batch["source_system"], batch["snapshot_month"],
        exclude_batch_id=batch["id"],
    )
    if current is None:
        return {
            "status": READY if not blocking else BLOCKED,
            "blocking": blocking,
            "required_confirmations": required_confirmations,
            "warnings": warnings,
            "published_month_comparison": None,
        }

    current_end = current["data_date_end"]
    new_end = batch["data_date_end"]
    if current_end is None or new_end is None:
        blocking.append({"code": DATA_COVERAGE_UNAVAILABLE, "message": "data_date_end is required for cumulative coverage"})
    elif new_end < current_end:
        blocking.append({
            "code": DATA_COVERAGE_REGRESSION,
            "current_data_end_date": current_end.isoformat(),
            "new_data_end_date": new_end.isoformat(),
        })
    else:
        fact_rows, fact_qty = _month_fact_stats(
            connection, batch["source_system"], batch["snapshot_month"]
        )
        if fact_rows != current["ready_rows"] or fact_qty != current["ready_qty"]:
            blocking.append({
                "code": CURRENT_MONTH_FACT_INTEGRITY_ERROR,
                "current_fact_rows": fact_rows,
                "current_batch_ready_rows": current["ready_rows"],
                "current_fact_qty": str(fact_qty),
                "current_batch_ready_qty": str(current["ready_qty"]),
            })
        else:
            new_qty = batch["ready_qty"]
            comparison = {
                "current_batch_id": current["batch_id"],
                "current_data_end_date": current_end.isoformat(),
                "current_fact_rows": fact_rows,
                "current_fact_qty": str(fact_qty),
                "new_data_end_date": new_end.isoformat(),
                "new_qty": str(new_qty),
            }
            if new_qty < fact_qty:
                warnings.append(CUMULATIVE_QTY_DECLINE)
            drop_ratio = Decimal(0)
            if fact_qty > 0:
                drop_ratio = (fact_qty - new_qty) / fact_qty
            comparison["drop_ratio"] = str(drop_ratio)
            if drop_ratio >= DROP_THRESHOLD:
                required_confirmations.append({
                    "code": ABNORMAL_DROP_THRESHOLD,
                    "existing_published_batch_id": current["batch_id"],
                    "existing_data_end_date": current_end.isoformat(),
                    "existing_ready_qty": str(fact_qty),
                    "drop_ratio": str(drop_ratio),
                })

    status = READY
    if blocking:
        status = BLOCKED
    elif required_confirmations:
        status = REQUIRES_CONFIRMATION
    return {
        "status": status,
        "blocking": blocking,
        "required_confirmations": required_confirmations,
        "warnings": warnings,
        "published_month_comparison": comparison,
    }


def _gate_view(connection: Connection, batch: dict) -> dict:
    """State-aware, read-only publish gate shared by Preview and Publish.

    One code path answers 'what would Publish do?' for a batch: READY /
    REQUIRES_CONFIRMATION / BLOCKED, plus idempotent-current and superseded
    markers. Publish uses it to decide; Preview GET returns it directly, so the
    two surfaces can never diverge.
    """
    view = {
        "status": BLOCKED,
        "blocking": [],
        "required_confirmations": [],
        "warnings": [],
        "published_month_comparison": None,
        "batch_status": batch["status"].value,
        "idempotent_current": False,
        "current_batch_id": None,
    }
    if batch["status"] == SalesBatchStatus.PUBLISHED:
        if batch["replaced_at"] is not None:
            current = _current_month_batch(
                connection, batch["source_system"], batch["snapshot_month"],
                exclude_batch_id=batch["id"],
            )
            current_batch_id = current["batch_id"] if current is not None else None
            view["current_batch_id"] = current_batch_id
            view["blocking"] = [{
                "code": BATCH_SUPERSEDED,
                "current_batch_id": current_batch_id,
            }]
            return view
        # This batch is the month's current snapshot: republish is a no-op.
        fact_rows, fact_qty = _month_fact_stats(
            connection, batch["source_system"], batch["snapshot_month"]
        )
        view["idempotent_current"] = True
        view["current_batch_id"] = batch["batch_id"]
        if fact_rows != batch["ready_rows"] or fact_qty != batch["ready_qty"]:
            view["blocking"] = [{
                "code": CURRENT_MONTH_FACT_INTEGRITY_ERROR,
                "current_fact_rows": fact_rows,
                "current_batch_ready_rows": batch["ready_rows"],
                "current_fact_qty": str(fact_qty),
                "current_batch_ready_qty": str(batch["ready_qty"]),
            }]
        else:
            view["status"] = READY
        return view
    if batch["status"] != SalesBatchStatus.PREVIEW_READY:
        view["blocking"] = [{
            "code": BATCH_NOT_PREVIEW_READY,
            "current_status": batch["status"].value,
        }]
        return view

    gate = _evaluate_gate(connection, batch)
    comparison = gate["published_month_comparison"]
    view.update(gate)
    if comparison is not None:
        view["current_batch_id"] = comparison["current_batch_id"]
    return view


def read_publish_gate(session_factory, batch_id: str) -> Optional[dict]:
    """Read-only Publish gate for a batch (no lock, no writes)."""
    with session_factory() as session:
        engine = session.get_bind()
    with engine.connect() as connection:
        batch = _batch_mapping(connection, batch_id)
        if batch is None:
            return None
        return _gate_view(connection, batch)


def _replace_month_facts(connection: Connection, batch: dict) -> int:
    """DELETE the whole snapshot_month Fact set, then INSERT every Candidate."""
    connection.execute(
        delete(SalesFact).where(
            SalesFact.source_system == batch["source_system"],
            SalesFact.snapshot_month == batch["snapshot_month"],
        )
    )

    fact_columns = (
        SalesFact.import_row_id,
        SalesFact.source_system,
        SalesFact.source_document_no,
        SalesFact.source_line_no,
        SalesFact.snapshot_month,
        SalesFact.sales_date,
        SalesFact.actual_qty,
        SalesFact.sku_id,
        SalesFact.sku_code_snapshot,
        SalesFact.sku_name_snapshot,
        SalesFact.product_id,
        SalesFact.product_name_snapshot,
        SalesFact.customer_id,
        SalesFact.channel_id_snapshot,
        SalesFact.channel_name_snapshot,
        SalesFact.salesrep_id_snapshot,
        SalesFact.salesrep_name_snapshot,
    )
    candidate_select = (
        select(
            SalesImportCandidate.import_row_id,
            literal(batch["source_system"]),
            SalesImportRow.source_document_no,
            SalesImportRow.source_line_no,
            literal(batch["snapshot_month"]),
            SalesImportRow.sales_date,
            SalesImportRow.actual_qty,
            SalesImportCandidate.sku_id,
            SalesImportCandidate.sku_code_snapshot,
            SalesImportCandidate.sku_name_snapshot,
            SalesImportCandidate.product_id,
            SalesImportCandidate.product_name_snapshot,
            SalesImportCandidate.customer_id,
            SalesImportCandidate.channel_id_snapshot,
            SalesImportCandidate.channel_name_snapshot,
            SalesImportCandidate.salesrep_id_snapshot,
            SalesImportCandidate.salesrep_name_snapshot,
        )
        .join(SalesImportRow, SalesImportRow.id == SalesImportCandidate.import_row_id)
        .where(SalesImportCandidate.import_batch_id == batch["id"])
    )
    connection.execute(
        insert(SalesFact).from_select(list(fact_columns), candidate_select)
    )
    inserted_rows = connection.scalar(
        select(func.count(SalesFact.id)).where(
            SalesFact.source_system == batch["source_system"],
            SalesFact.snapshot_month == batch["snapshot_month"],
        )
    )
    if inserted_rows != batch["ready_rows"]:
        raise PublishError(
            f"fact insert mismatch: {inserted_rows} != {batch['ready_rows']}",
            code=FACT_INSERT_MISMATCH,
        )
    return inserted_rows


def _publish_without_lock(
    connection: Connection,
    batch_id: str,
    *,
    confirmations: tuple[str, ...],
    user: str,
    now: datetime,
):
    batch = _batch_mapping(connection, batch_id, for_update=True)
    if batch is None:
        raise PublishNotFound(f"batch not found: {batch_id}")

    view = _gate_view(connection, batch)
    if view["idempotent_current"]:
        if view["status"] == BLOCKED:
            raise PublishGateBlocked(view)
        fact_rows, fact_qty = _month_fact_stats(
            connection, batch["source_system"], batch["snapshot_month"]
        )
        return PublishResult(
            batch_id=batch["batch_id"],
            published_at=batch["published_at"],
            replaced_batch_id=None,
            fact_rows=fact_rows,
            fact_qty=fact_qty,
            warnings=(),
            confirmations_recorded=(),
            idempotent=True,
            snapshot_month=batch["snapshot_month"],
            data_end_date=batch["data_date_end"],
        )

    if view["status"] == BLOCKED:
        superseded = next(
            (item for item in view["blocking"] if item["code"] == BATCH_SUPERSEDED),
            None,
        )
        if superseded is not None:
            raise PublishBatchSuperseded(superseded.get("current_batch_id"))
        raise PublishGateBlocked(view)

    if view["status"] == REQUIRES_CONFIRMATION:
        required_codes = {item["code"] for item in view["required_confirmations"]}
        if set(confirmations) != required_codes:
            raise PublishConfirmationRequired(view)
    elif confirmations:
        raise PublishValidationError(
            "unexpected confirmation for a READY publish",
            code=CONFIRMATION_MISMATCH,
            payload={"code": CONFIRMATION_MISMATCH, "gate": view},
        )

    current = _current_month_batch(
        connection, batch["source_system"], batch["snapshot_month"],
        exclude_batch_id=batch["id"],
    )
    _replace_month_facts(connection, batch)

    replaced_batch_id = None
    if current is not None:
        connection.execute(
            update(SalesImportBatch)
            .where(SalesImportBatch.id == current["id"])
            .values(replaced_at=now, replaced_by_batch_id=batch["id"])
        )
        replaced_batch_id = current["batch_id"]

    connection.execute(
        update(SalesImportBatch)
        .where(SalesImportBatch.id == batch["id"])
        .values(status=SalesBatchStatus.PUBLISHED, published_at=now)
    )

    recorded = []
    for item in view["required_confirmations"]:
        connection.execute(
            insert(SalesPublishConfirmation).values(
                import_batch_id=batch["id"],
                confirmation_code=item["code"],
                existing_published_batch_id=current["id"] if current is not None else None,
                existing_data_end_date=(
                    current["data_date_end"] if current is not None else None
                ),
                existing_ready_qty=(
                    Decimal(item["existing_ready_qty"]) if current is not None else None
                ),
                drop_ratio=Decimal(item["drop_ratio"]),
                confirmed_by=user,
            )
        )
        recorded.append(item["code"])

    fact_rows, fact_qty = _month_fact_stats(
        connection, batch["source_system"], batch["snapshot_month"]
    )
    return PublishResult(
        batch_id=batch["batch_id"],
        published_at=now,
        replaced_batch_id=replaced_batch_id,
        fact_rows=fact_rows,
        fact_qty=fact_qty,
        warnings=tuple(view["warnings"]),
        confirmations_recorded=tuple(recorded),
        idempotent=False,
        snapshot_month=batch["snapshot_month"],
        data_end_date=batch["data_date_end"],
    )


def publish_batch(
    session_factory,
    batch_id: str,
    *,
    confirmations: Sequence[str] = (),
    user: Optional[str] = None,
    now: Optional[datetime] = None,
    lock_timeout: float = 10.0,
) -> PublishResult:
    """Acquire the month advisory lock (MySQL), then run one atomic publish."""
    if not user or not str(user).strip():
        raise PublishValidationError("publish requires an authenticated user")
    if not isinstance(user, str):
        raise PublishValidationError("user must be a string")
    provided = tuple(confirmations)
    unknown = set(provided) - set(PUBLISH_CONFIRMATION_CODES)
    if unknown:
        raise PublishValidationError(
            f"unknown confirmation code(s): {sorted(unknown)}"
        )
    if len(set(provided)) != len(provided):
        raise PublishValidationError("duplicate confirmation code")

    with session_factory() as session:
        engine = session.get_bind()
    publish_now = now or _utcnow()

    with engine.connect() as connection:
        dialect = engine.dialect.name
        lock_name = None
        acquired = False
        if dialect == "mysql":
            probe = connection.execute(
                select(SalesImportBatch.source_system, SalesImportBatch.snapshot_month)
                .where(SalesImportBatch.batch_id == batch_id)
            ).mappings().first()
            if probe is None:
                raise PublishNotFound(f"batch not found: {batch_id}")
            lock_name = _lock_name(probe["source_system"], probe["snapshot_month"])
            got = connection.execute(
                text("SELECT GET_LOCK(:lock_name, :timeout)"),
                {"lock_name": lock_name, "timeout": int(lock_timeout)},
            ).scalar()
            if got != 1:
                raise PublishLockBusy(
                    f"month {lock_name} is locked by another publisher",
                    code="PUBLISH_IN_PROGRESS",
                    payload={"code": "PUBLISH_IN_PROGRESS", "lock_name": lock_name},
                )
            acquired = True
        try:
            result = _publish_without_lock(
                connection, batch_id,
                confirmations=provided, user=user, now=publish_now,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            if acquired:
                connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": lock_name},
                )
