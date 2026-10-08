"""Deterministic publication contracts on the isolated synthetic MySQL demo."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import hashlib
import json
from threading import Event, current_thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import delete, event, select, text, update

from demo.seed import ensure_demo_target
from webapp.mdm.database import get_engine, get_session_factory
from webapp.mdm.models import SKU
from webapp.sales import publish_service
from webapp.sales.dashboard_query_service import DashboardQueryError, select_current_batch
from webapp.sales.models import (
    SalesBatchStatus, SalesFact, SalesImportBatch, SalesImportCandidate,
    SalesImportRow, SalesPublishConfirmation, SalesRowStatus,
)


class SalesPublishConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_demo_target()
        cls.engine = get_engine()
        cls.factory = get_session_factory()
        if cls.engine.dialect.name != "mysql":
            raise RuntimeError("Publication concurrency requires real MySQL")

    def setUp(self):
        self.source = "SYNTHETIC-" + uuid4().hex[:16]
        self.month = date(2000, 6, 1)
        self.addCleanup(self._remove_fixture)
        with self.factory() as session, session.begin():
            sku = session.scalar(select(SKU).where(SKU.stable_id == "SKU_DEMO_A"))
            self.assertIsNotNone(sku, "Start the fresh synthetic demo seed first")
            self.batch_ids = []
            self.internal_ids = []
            self.row_ids = []
            for number in (1, 2):
                public_id = str(uuid4())
                qty = Decimal(number * 10)
                batch = SalesImportBatch(
                    batch_id=public_id, filename=f"synthetic-publish-{number}.xlsx",
                    file_hash=hashlib.sha256(public_id.encode()).hexdigest(),
                    source_system=self.source, snapshot_month=self.month,
                    data_date_start=self.month, data_date_end=date(2000, 6, number),
                    status=SalesBatchStatus.PREVIEW_READY,
                    total_rows=1, ready_rows=1, skipped_rows=0,
                    total_qty=qty, ready_qty=qty, skipped_qty=Decimal(0),
                )
                session.add(batch)
                session.flush()
                row = SalesImportRow(
                    import_batch_id=batch.id, source_row_no=1,
                    source_document_no=f"SYNTHETIC-PUBLISH-{number}", source_line_no="1",
                    sales_date=self.month, actual_qty=qty, status=SalesRowStatus.READY,
                )
                session.add(row)
                session.flush()
                session.add(SalesImportCandidate(
                    import_batch_id=batch.id, import_row_id=row.id,
                    sku_id=sku.id, sku_code_snapshot="SKU-DEMO-001",
                    sku_name_snapshot="Synthetic Publish SKU", product_id=sku.product_id,
                    product_name_snapshot="Synthetic Publish Product",
                ))
                self.batch_ids.append(public_id)
                self.internal_ids.append(batch.id)
                self.row_ids.append(row.id)

    def _remove_fixture(self):
        with self.engine.begin() as connection:
            ids = select(SalesImportBatch.id).where(SalesImportBatch.source_system == self.source)
            connection.execute(delete(SalesFact).where(SalesFact.source_system == self.source))
            for model in (SalesPublishConfirmation, SalesImportCandidate, SalesImportRow):
                connection.execute(delete(model).where(model.import_batch_id.in_(ids)))
            connection.execute(update(SalesImportBatch).where(
                SalesImportBatch.source_system == self.source
            ).values(replaced_by_batch_id=None))
            connection.execute(delete(SalesImportBatch).where(
                SalesImportBatch.source_system == self.source
            ))

    def _state(self):
        with self.factory() as session:
            batches = session.scalars(select(SalesImportBatch).where(
                SalesImportBatch.source_system == self.source
            ).order_by(SalesImportBatch.id)).all()
            facts = session.scalars(select(SalesFact).where(
                SalesFact.source_system == self.source
            ).order_by(SalesFact.import_row_id)).all()
            confirmations = session.scalars(select(SalesPublishConfirmation).where(
                SalesPublishConfirmation.import_batch_id.in_(self.internal_ids)
            )).all()
            return {
                "batches": [(b.batch_id, b.status.value, b.published_at,
                             b.replaced_at, b.replaced_by_batch_id) for b in batches],
                "facts": [(f.import_row_id, f.actual_qty) for f in facts],
                "confirmations": [c.id for c in confirmations],
            }

    def test_same_month_publish_serializes_after_prelock_probe(self):
        """Both succeed; B replaces A even when B's probe preceded A's commit."""
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT @@transaction_isolation")),
                             "REPEATABLE-READ")
        first_locked = Event()
        second_at_lock = Event()
        original = publish_service._publish_without_lock

        def publish_after_barrier(connection, batch_id, **kwargs):
            if batch_id == self.batch_ids[0]:
                first_locked.set()
                if not second_at_lock.wait(10):
                    raise AssertionError("Second publisher never reached GET_LOCK")
            return original(connection, batch_id, **kwargs)

        def before_sql(connection, cursor, statement, parameters, context, executemany):
            if current_thread().name.endswith("_1") and "SELECT GET_LOCK(" in statement:
                second_at_lock.set()

        event.listen(self.engine, "before_cursor_execute", before_sql)
        try:
            with patch.object(publish_service, "_publish_without_lock", publish_after_barrier):
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="synthetic-publisher") as pool:
                    first = pool.submit(publish_service.publish_batch, self.factory,
                                        self.batch_ids[0], user="demo_admin")
                    if not first_locked.wait(10):
                        if first.done():
                            first.result()
                        self.fail("First publisher did not acquire lock")
                    second = pool.submit(publish_service.publish_batch, self.factory,
                                         self.batch_ids[1], user="demo_admin")
                    results = [first.result(timeout=20), second.result(timeout=20)]
        finally:
            event.remove(self.engine, "before_cursor_execute", before_sql)

        state = self._state()
        currents = [b for b in state["batches"]
                    if b[1] == "PUBLISHED" and b[3] is None]
        query_error = None
        current = None
        try:
            current = select_current_batch(self.factory, self.source, self.month)
        except DashboardQueryError as exc:
            query_error = exc.code
        diagnostic = {
            "current_batch_count": len(currents), "query_error": query_error,
            "replaced_batch_ids": [r.replaced_batch_id for r in results],
            "state": state,
        }
        print("SYNTHETIC_PUBLISH_CONCURRENCY=" + json.dumps(diagnostic, default=str))
        self.assertEqual(len(currents), 1, diagnostic)
        self.assertIsNone(query_error, diagnostic)
        self.assertEqual(current["batch_id"], self.batch_ids[1])
        self.assertEqual(results[1].replaced_batch_id, self.batch_ids[0])
        self.assertEqual(state["batches"][0][1], "PUBLISHED")
        self.assertIsNotNone(state["batches"][0][2])
        self.assertIsNotNone(state["batches"][0][3])
        self.assertEqual(state["batches"][0][4], self.internal_ids[1])
        self.assertIsNotNone(state["batches"][1][2])
        self.assertIsNone(state["batches"][1][4])
        self.assertEqual(state["facts"], [(self.row_ids[1], Decimal("20"))])
        self.assertEqual(current["ready_rows"], 1)
        self.assertEqual(Decimal(current["ready_qty"]), Decimal("20"))
        self.assertEqual(state["confirmations"], [])
        # Existing semantics remain: current re-publish is a no-op; old is superseded.
        self.assertTrue(publish_service.publish_batch(
            self.factory, self.batch_ids[1], user="demo_admin"
        ).idempotent)
        with self.assertRaises(publish_service.PublishBatchSuperseded):
            publish_service.publish_batch(self.factory, self.batch_ids[0], user="demo_admin")
        self.assertEqual(self._state(), state)

    def test_failed_replace_rolls_back_before_month_lock_release(self):
        publish_service.publish_batch(self.factory, self.batch_ids[0], user="demo_admin")
        before = self._state()
        lock_name = publish_service._lock_name(self.source, self.month)
        original = publish_service._replace_month_facts
        rollback_lock_owners = []

        def fail_after_replace(connection, batch):
            original(connection, batch)
            raise RuntimeError("Synthetic failure after replacing facts")

        def on_rollback(connection):
            cursor = connection.connection.cursor()
            try:
                cursor.execute("SELECT IS_USED_LOCK(%s)", (lock_name,))
                rollback_lock_owners.append(cursor.fetchone()[0])
            finally:
                cursor.close()

        event.listen(self.engine, "rollback", on_rollback)
        try:
            with patch.object(publish_service, "_replace_month_facts", fail_after_replace):
                with self.assertRaisesRegex(RuntimeError, "Synthetic failure"):
                    publish_service.publish_batch(self.factory, self.batch_ids[1], user="demo_admin")
        finally:
            event.remove(self.engine, "rollback", on_rollback)
        # The initial read-only probe may roll back before locking; the failure
        # rollback must still own the lock, followed by release/connection close.
        self.assertTrue(any(owner is not None for owner in rollback_lock_owners))
        self.assertEqual(self._state(), before)
        self.assertEqual(select_current_batch(self.factory, self.source, self.month)["batch_id"],
                         self.batch_ids[0])
        with self.engine.connect() as connection:
            self.assertIsNone(connection.scalar(text("SELECT IS_USED_LOCK(:name)"),
                                                {"name": lock_name}))


if __name__ == "__main__":
    unittest.main()
