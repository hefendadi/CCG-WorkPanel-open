"""Semantic query engine tests: grouping, totals, filters, sort and paging.

Runs on in-memory SQLite with the synthetic demo fixture.  No MySQL, no Docker,
no network.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from webapp.sales.semantic_query_engine import (
    SemanticQueryError,
    get_filter_options,
    run_query,
    validate_query,
)
from webapp.sales.tests.fixtures import (
    DEMO_TOTAL_QTY,
    SNAPSHOT_MONTH,
    build_session_factory,
    replace_batch,
    seed_published_batch,
)

BATCH = "DEMO-BATCH-0001"


class SemanticQueryEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def run_semantic(self, **overrides):
        request = {
            "dataset": "sales_actual",
            "batch_id": BATCH,
            "metrics": ["actual_sales"],
            "dimensions": [],
            "filters": [],
            "sort": [{"field": "actual_sales", "direction": "desc"}],
            "limit": 100,
        }
        request.update(overrides)
        return run_query(self.factory, validate_query(request))

    # -- totals ---------------------------------------------------------------

    def test_total_is_identical_for_every_dimension_combination(self):
        """The headline proof: dimensions never change the underlying total."""
        totals = set()
        for dimensions in (
            [], ["product"], ["channel"], ["salesrep"], ["customer"], ["sku"],
            ["sales_date"], ["snapshot_month"],
            ["channel", "product"], ["salesrep", "product"], ["product", "sku"],
            ["channel", "salesrep", "product"],
        ):
            result = self.run_semantic(dimensions=dimensions)
            totals.add(result["total_qty"])
            with self.subTest(dimensions=dimensions):
                self.assertEqual(result["total_qty"], f"{DEMO_TOTAL_QTY}.0000")
        self.assertEqual(len(totals), 1, totals)

    def test_empty_dimensions_returns_a_single_total_row(self):
        result = self.run_semantic(dimensions=[])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["actual_sales"], f"{DEMO_TOTAL_QTY}.0000")
        self.assertEqual(result["pagination"]["total_group_rows"], 1)

    # -- grouping -------------------------------------------------------------

    def test_product_grouping_matches_hand_computed_quantities(self):
        result = self.run_semantic(dimensions=["product"])
        by_name = {item["product_name"]: item["actual_sales"] for item in result["items"]}
        self.assertEqual(by_name["Demo Product 1"], "22.0000")
        self.assertEqual(by_name["Demo Product 2"], "10.0000")
        self.assertEqual(by_name["Demo Product 3"], "3.0000")
        self.assertEqual(sum(Decimal(value) for value in by_name.values()), Decimal(DEMO_TOTAL_QTY))

    def test_channel_product_grouping_is_a_cross_tab(self):
        result = self.run_semantic(dimensions=["channel", "product"])
        pairs = {
            (item["channel_name"], item["product_name"]): item["actual_sales"]
            for item in result["items"]
        }
        self.assertEqual(pairs[("Demo Channel A", "Demo Product 1")], "17.0000")
        self.assertEqual(pairs[("Demo Channel B", "Demo Product 1")], "5.0000")
        self.assertEqual(pairs[("Demo Channel A", "Demo Product 2")], "5.0000")
        self.assertEqual(pairs[("Demo Channel B", "Demo Product 2")], "5.0000")

    def test_salesrep_product_grouping(self):
        result = self.run_semantic(dimensions=["salesrep", "product"])
        pairs = {
            (item["salesrep_name"], item["product_name"]): item["actual_sales"]
            for item in result["items"]
        }
        self.assertEqual(pairs[("Demo Rep A", "Demo Product 1")], "15.0000")
        self.assertEqual(pairs[("Demo Rep B", "Demo Product 1")], "7.0000")
        self.assertEqual(pairs[("Demo Rep A", "Demo Product 2")], "5.0000")

    def test_product_sku_drilldown_grouping(self):
        result = self.run_semantic(dimensions=["product", "sku"])
        pairs = {
            (item["product_name"], item["sku_name"]): item["actual_sales"]
            for item in result["items"]
        }
        self.assertEqual(pairs[("Demo Product 1", "Demo SKU 1")], "15.0000")
        self.assertEqual(pairs[("Demo Product 1", "Demo SKU 2")], "7.0000")
        self.assertEqual(pairs[("Demo Product 2", "Demo SKU 3")], "10.0000")
        self.assertEqual(pairs[("Demo Product 3", "Demo SKU 4")], "3.0000")

    def test_unassigned_snapshot_reference_is_labelled_not_dropped(self):
        result = self.run_semantic(dimensions=["channel"])
        by_name = {item["channel_name"]: item["actual_sales"] for item in result["items"]}
        self.assertIn("未归属", by_name)
        self.assertEqual(by_name["未归属"], "3.0000")

    def test_three_dimensions_are_supported(self):
        result = self.run_semantic(dimensions=["channel", "salesrep", "product"])
        self.assertGreater(result["pagination"]["total_group_rows"], 0)
        self.assertEqual(result["total_qty"], f"{DEMO_TOTAL_QTY}.0000")

    # -- filters --------------------------------------------------------------

    def test_product_id_filter_narrows_total_and_rows(self):
        all_rows = self.run_semantic(dimensions=["product"])
        product_id = next(
            item["product"] for item in all_rows["items"] if item["product_name"] == "Demo Product 1"
        )
        filtered = self.run_semantic(
            dimensions=["product"],
            filters=[{"dimension": "product", "operator": "in", "values": [product_id]}],
        )
        self.assertEqual(filtered["total_qty"], "22.0000")
        self.assertEqual(len(filtered["items"]), 1)

    def test_channel_filter_accepts_the_unassigned_null(self):
        result = self.run_semantic(
            dimensions=["product"],
            filters=[{"dimension": "channel", "operator": "eq", "value": None}],
        )
        self.assertEqual(result["total_qty"], "3.0000")
        self.assertEqual(result["items"][0]["product_name"], "Demo Product 3")

    def test_date_range_filter_on_sales_date(self):
        inside = self.run_semantic(
            filters=[{"dimension": "sales_date", "operator": "between",
                      "values": ["2000-02-01", "2000-02-28"]}],
        )
        outside = self.run_semantic(
            filters=[{"dimension": "sales_date", "operator": "between",
                      "values": ["2000-03-01", "2000-03-31"]}],
        )
        self.assertEqual(inside["total_qty"], f"{DEMO_TOTAL_QTY}.0000")
        self.assertEqual(Decimal(outside["total_qty"]), Decimal(0))

    def test_snapshot_month_filter_accepts_iso_month(self):
        result = self.run_semantic(
            filters=[{"dimension": "snapshot_month", "operator": "eq",
                      "value": SNAPSHOT_MONTH.isoformat()}],
        )
        self.assertEqual(result["total_qty"], f"{DEMO_TOTAL_QTY}.0000")

    def test_combined_filters_are_anded(self):
        rows = self.run_semantic(dimensions=["product"])
        product_id = next(
            item["product"] for item in rows["items"] if item["product_name"] == "Demo Product 1"
        )
        result = self.run_semantic(
            dimensions=["channel"],
            filters=[
                {"dimension": "product", "operator": "in", "values": [product_id]},
                {"dimension": "channel", "operator": "in", "values": [4031]},
            ],
        )
        self.assertEqual(result["total_qty"], "17.0000")

    # -- sort / paging --------------------------------------------------------

    def test_sort_descending_is_the_default_order(self):
        result = self.run_semantic(dimensions=["product"])
        quantities = [Decimal(item["actual_sales"]) for item in result["items"]]
        self.assertEqual(quantities, sorted(quantities, reverse=True))

    def test_ascending_sort_is_honoured(self):
        result = self.run_semantic(
            dimensions=["product"],
            sort=[{"field": "actual_sales", "direction": "asc"}],
        )
        quantities = [Decimal(item["actual_sales"]) for item in result["items"]]
        self.assertEqual(quantities, sorted(quantities))

    def test_dimension_sort_is_honoured(self):
        result = self.run_semantic(
            dimensions=["product"],
            sort=[{"field": "product", "direction": "asc"}],
        )
        ids = [item["product"] for item in result["items"]]
        self.assertEqual(ids, sorted(ids))

    def test_ordering_is_deterministic_across_identical_calls(self):
        first = self.run_semantic(dimensions=["channel", "product"])
        second = self.run_semantic(dimensions=["channel", "product"])
        self.assertEqual(first["items"], second["items"])

    def test_limit_caps_rows_and_marks_truncation(self):
        result = self.run_semantic(dimensions=["channel", "product"], limit=2)
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["pagination"]["truncated"])
        self.assertEqual(result["total_qty"], f"{DEMO_TOTAL_QTY}.0000")

    def test_offset_pages_through_the_grouped_result(self):
        full = self.run_semantic(dimensions=["channel", "product"])
        paged = self.run_semantic(dimensions=["channel", "product"], limit=2, offset=2)
        self.assertEqual(paged["items"], full["items"][2:4])

    # -- filter options -------------------------------------------------------

    def test_filter_options_come_from_the_snapshot_without_an_mdm_join(self):
        options = get_filter_options(self.factory, BATCH, ["channel", "product"])
        self.assertIn("channel", options["options"])
        channel_labels = {item["label"] for item in options["options"]["channel"]}
        self.assertIn("Demo Channel A", channel_labels)
        self.assertIn("未归属", channel_labels)
        product_labels = {item["label"] for item in options["options"]["product"]}
        self.assertIn("Demo Product 1", product_labels)

    def test_filter_options_reject_unknown_dimension(self):
        with self.assertRaises(SemanticQueryError):
            get_filter_options(self.factory, BATCH, ["category_l1"])


class BatchGuardTests(unittest.TestCase):
    """The engine must only ever read the current Published batch."""

    def setUp(self):
        self.engine, self.factory = build_session_factory()

    def tearDown(self):
        self.engine.dispose()

    def test_unknown_batch_is_rejected(self):
        from webapp.sales.dashboard_query_service import DashboardBatchNotFound
        seed_published_batch(self.factory, batch_id="DEMO-BATCH-0001")
        with self.assertRaises(DashboardBatchNotFound):
            run_query(self.factory, validate_query({
                "batch_id": "NOPE-0000", "metrics": ["actual_sales"], "dimensions": [],
            }))

    def test_replaced_batch_is_rejected(self):
        from webapp.sales.dashboard_query_service import DashboardSnapshotChanged
        seed_published_batch(self.factory, batch_id="DEMO-BATCH-0001")
        replace_batch(self.factory, "DEMO-BATCH-0001", "DEMO-BATCH-0002")
        with self.assertRaises(DashboardSnapshotChanged):
            run_query(self.factory, validate_query({
                "batch_id": "DEMO-BATCH-0001", "metrics": ["actual_sales"], "dimensions": [],
            }))

    def test_current_batch_after_replacement_is_readable(self):
        seed_published_batch(self.factory, batch_id="DEMO-BATCH-0001")
        replace_batch(self.factory, "DEMO-BATCH-0001", "DEMO-BATCH-0002")
        result = run_query(self.factory, validate_query({
            "batch_id": "DEMO-BATCH-0002", "metrics": ["actual_sales"], "dimensions": [],
        }))
        self.assertEqual(result["batch_id"], "DEMO-BATCH-0002")
        self.assertEqual(result["total_qty"], f"{DEMO_TOTAL_QTY}.0000")

    def test_fact_integrity_mismatch_is_detected(self):
        from webapp.sales.dashboard_query_service import DashboardFactIntegrityError
        from webapp.sales.models import SalesImportBatch
        seed_published_batch(self.factory, batch_id="DEMO-BATCH-0001")
        with self.factory() as session, session.begin():
            batch = session.query(SalesImportBatch).filter_by(batch_id="DEMO-BATCH-0001").one()
            batch.ready_qty = Decimal(999)
        with self.assertRaises(DashboardFactIntegrityError):
            run_query(self.factory, validate_query({
                "batch_id": "DEMO-BATCH-0001", "metrics": ["actual_sales"], "dimensions": [],
            }))


if __name__ == "__main__":
    unittest.main()
