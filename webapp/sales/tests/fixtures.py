"""Synthetic fixtures for the Sales Semantic Layer spike tests.

Everything here is invented for the demo: fictional products, channels, reps and
quantities.  No business source file, no production database and no private
workspace data is read.

The fixture builds an in-memory SQLite database holding exactly the tables the
semantic engine touches, then writes Published Sales Fact rows through the same
model classes the production Publish path writes.  That keeps the tests offline
(no MySQL, no Docker) while still exercising the real query engine, the real
batch guard and the real production ``dashboard_query_service`` functions used by
the compatibility suite.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp.mdm.models import Base as MdmBase
from webapp.sales.models import (
    SalesBatchStatus,
    SalesFact,
    SalesImportBatch,
    SalesImportRow,
    SalesRowStatus,
)

SOURCE_SYSTEM = "DEMO_ERP"
SNAPSHOT_MONTH = date(2000, 2, 1)
SALES_DATE = date(2000, 2, 15)


# (sku_code, sku_name, product_code, product_name, channel_code, channel_name,
#  rep_code, rep_name, customer_code, customer_name, qty)
#
# Deliberate shapes:
#   * product P1 spans two channels and two reps
#   * product P2 has a second SKU
#   * P3/C3 has a NULL channel and a NULL salesrep (未归属) and no customer
#   * equal quantities exist so deterministic ordering can be asserted
DEMO_ROWS = (
    ("SKU-DEMO-001", "Demo SKU 1", "PROD-DEMO-001", "Demo Product 1",
     "CHN-DEMO-001", "Demo Channel A", "REP-DEMO-001", "Demo Rep A",
     "CUST-DEMO-001", "Demo Customer A", 10),
    ("SKU-DEMO-001", "Demo SKU 1", "PROD-DEMO-001", "Demo Product 1",
     "CHN-DEMO-002", "Demo Channel B", "REP-DEMO-001", "Demo Rep A",
     "CUST-DEMO-002", "Demo Customer B", 5),
    ("SKU-DEMO-002", "Demo SKU 2", "PROD-DEMO-001", "Demo Product 1",
     "CHN-DEMO-001", "Demo Channel A", "REP-DEMO-002", "Demo Rep B",
     "CUST-DEMO-001", "Demo Customer A", 7),
    ("SKU-DEMO-003", "Demo SKU 3", "PROD-DEMO-002", "Demo Product 2",
     "CHN-DEMO-001", "Demo Channel A", "REP-DEMO-002", "Demo Rep B",
     "CUST-DEMO-003", "Demo Customer C", 5),
    ("SKU-DEMO-003", "Demo SKU 3", "PROD-DEMO-002", "Demo Product 2",
     "CHN-DEMO-002", "Demo Channel B", "REP-DEMO-001", "Demo Rep A",
     "CUST-DEMO-003", "Demo Customer C", 5),
    ("SKU-DEMO-004", "Demo SKU 4", "PROD-DEMO-003", "Demo Product 3",
     None, None, None, None, None, None, 3),
)

DEMO_TOTAL_QTY = sum(row[-1] for row in DEMO_ROWS)


def build_session_factory():
    """In-memory SQLite with the MDM + Sales tables the engine reads."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    MdmBase.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False, future=True)


def seed_published_batch(session_factory, *, batch_id="DEMO-BATCH-0001", rows=DEMO_ROWS,
                         snapshot_month=SNAPSHOT_MONTH, source_system=SOURCE_SYSTEM):
    """Write one current Published batch plus its Fact rows. Returns batch info."""
    total_qty = sum(row[-1] for row in rows)
    with session_factory() as session, session.begin():
        # Discrete ids keep the synthetic rows independent of any MDM seed.
        batch = SalesImportBatch(
            batch_id=batch_id,
            filename="synthetic-sales-semantic-demo.xlsx",
            file_hash=hashlib.sha256(batch_id.encode("utf-8")).hexdigest(),
            source_system=source_system,
            snapshot_month=snapshot_month,
            data_date_start=snapshot_month,
            data_date_end=snapshot_month,
            status=SalesBatchStatus.PUBLISHED,
            total_rows=len(rows),
            ready_rows=len(rows),
            skipped_rows=0,
            total_qty=Decimal(total_qty),
            ready_qty=Decimal(total_qty),
            published_at=datetime(2000, 2, 20, tzinfo=timezone.utc),
        )
        session.add(batch)
        session.flush()

        next_id = session.query(SalesFact.id).count() or 0
        for offset, row in enumerate(rows):
            (sku_code, sku_name, product_code, product_name, channel_code,
             channel_name, rep_code, rep_name, customer_code, customer_name,
             qty) = row
            import_row = SalesImportRow(
                import_batch_id=batch.id,
                source_row_no=offset + 1,
                source_document_no=f"DOC-{batch_id[-4:]}-{offset + 1}",
                source_line_no=str(offset + 1),
                sales_date=SALES_DATE,
                source_sku_code=sku_code,
                source_sku_name=sku_name,
                actual_qty=Decimal(qty),
                status=SalesRowStatus.READY,
            )
            session.add(import_row)
            session.flush()
            session.add(
                SalesFact(
                    import_row_id=import_row.id,
                    source_system=source_system,
                    source_document_no=f"DOC-{batch_id[-4:]}-{offset + 1}",
                    source_line_no=str(offset + 1),
                    snapshot_month=snapshot_month,
                    sales_date=SALES_DATE,
                    actual_qty=Decimal(qty),
                    sku_id=1000 + _stable_index("sku", sku_code),
                    sku_code_snapshot=sku_code,
                    sku_name_snapshot=sku_name,
                    product_id=2000 + _stable_index("product", product_code),
                    product_name_snapshot=product_name,
                    customer_id=(3000 + _stable_index("customer", customer_code)) if customer_code else None,
                    channel_id_snapshot=(4000 + _stable_index("channel", channel_code)) if channel_code else None,
                    channel_name_snapshot=channel_name,
                    salesrep_id_snapshot=(5000 + _stable_index("rep", rep_code)) if rep_code else None,
                    salesrep_name_snapshot=rep_name,
                )
            )
            next_id += 1
    return {"batch_id": batch_id, "total_qty": Decimal(total_qty), "rows": len(rows)}


def _stable_index(kind: str, code):
    """Small deterministic surrogate id; keeps ids readable inside the fixture."""
    if code is None:
        return 0
    suffix = code.rsplit("-", 1)[-1]
    try:
        base = int(suffix)
    except ValueError:
        base = sum(ord(char) for char in code) % 90 + 1
    offset = {"product": 0, "sku": 10, "customer": 20, "channel": 30, "rep": 40}[kind]
    return base + offset


def replace_batch(session_factory, old_batch_id: str, new_batch_id: str,
                  new_month=date(2000, 3, 1), new_rows=DEMO_ROWS):
    """Publish a newer snapshot month and mark the older batch replaced.

    A newer version covers a later month: the batch guard validates whole-month
    Fact counters, so two batches of the same month are intentionally treated as
    an ambiguous/duplicated month rather than a legitimate replacement.
    """
    info = seed_published_batch(session_factory, batch_id=new_batch_id,
                                rows=new_rows, snapshot_month=new_month)
    with session_factory() as session, session.begin():
        old = session.query(SalesImportBatch).filter_by(batch_id=old_batch_id).one()
        new = session.query(SalesImportBatch).filter_by(batch_id=new_batch_id).one()
        old.replaced_at = datetime(2000, 3, 1, tzinfo=timezone.utc)
        old.replaced_by_batch_id = new.id
    return info
