"""Compatibility between the semantic engine and the four production views.

For the same batch and filter conditions the semantic engine must reproduce the
existing dashboard numbers, and — most importantly — the total ``actual_qty``
must be identical for every one of them.  The production query functions are used
unmodified; nothing here changes production behaviour to make the spike pass.

Classification
--------------
COMPATIBLE
    Same grouping keys and same quantities for the same batch/filters.
INTENTIONALLY_DIFFERENT
    Same quantities, different presentation contract (pagination shape, share
    naming, ordering source). Documented per case.
NOT_SUPPORTED
    Expressly outside the first semantic version (e.g. category dimensions).
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from webapp.sales.dashboard_query_service import (
    DashboardFilters,
    get_channel_product,
    get_product_aggregation,
    get_product_sku_drilldown,
    get_salesrep_product,
    get_summary,
)
from webapp.sales.semantic_query_engine import run_query, validate_query
from webapp.sales.tests.fixtures import (
    DEMO_TOTAL_QTY,
    build_session_factory,
    seed_published_batch,
)

BATCH = "DEMO-BATCH-0001"
CHANNEL_A_ID = 4031
CHANNEL_B_ID = 4032
REP_A_ID = 5041
REP_B_ID = 5042


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def semantic(self, dimensions, filters=None, **overrides):
        request = {
            "batch_id": BATCH,
            "metrics": ["actual_sales"],
            "dimensions": list(dimensions),
            "filters": filters or [],
            "sort": [{"field": "actual_sales", "direction": "desc"}],
            "limit": 200,
        }
        request.update(overrides)
        return run_query(self.factory, validate_query(request))

    @staticmethod
    def _production_total(results):
        return sum(Decimal(item["qty"]) for item in results["items"])

    # -- the headline invariant ----------------------------------------------

    def test_total_actual_qty_matches_summary_for_every_view(self):
        """Same batch/filters => identical total across all five entry points."""
        summary = get_summary(self.factory, BATCH)
        self.assertEqual(Decimal(summary["mtd_qty"]), Decimal(DEMO_TOTAL_QTY))

        production_totals = {
            "Product aggregation": self._production_total(
                get_product_aggregation(self.factory, BATCH, page_size=200)),
            "Channel × Product": self._production_total(
                get_channel_product(self.factory, BATCH, page_size=200)),
            "SalesRep × Product": self._production_total(
                get_salesrep_product(self.factory, BATCH, page_size=200)),
        }
        semantic_totals = {
            "Product aggregation": self.semantic(["product"])["total_qty"],
            "Channel × Product": self.semantic(["channel", "product"])["total_qty"],
            "SalesRep × Product": self.semantic(["salesrep", "product"])["total_qty"],
            "Product → SKU drilldown": self.semantic(["product", "sku"])["total_qty"],
        }
        for view, total in semantic_totals.items():
            with self.subTest(view=view):
                self.assertEqual(Decimal(total), Decimal(DEMO_TOTAL_QTY))
        for view, total in production_totals.items():
            with self.subTest(view=view):
                self.assertEqual(total, Decimal(DEMO_TOTAL_QTY))

    # -- 1. Product aggregation ----------------------------------------------

    def test_product_aggregation_is_compatible(self):
        production = get_product_aggregation(self.factory, BATCH, page_size=200)
        semantic = self.semantic(["product"])

        production_by_id = {item["product_id"]: item for item in production["items"]}
        semantic_by_id = {item["product"]: item for item in semantic["items"]}

        self.assertEqual(set(production_by_id), set(semantic_by_id))
        for product_id, expected in production_by_id.items():
            with self.subTest(product_id=product_id):
                actual = semantic_by_id[product_id]
                self.assertEqual(Decimal(actual["actual_sales"]), Decimal(expected["qty"]))
                self.assertEqual(actual["product_name"], expected["product_name"])

        # Same ordering for the default (metric desc) sort.
        self.assertEqual(
            [item["product_id"] for item in production["items"]],
            [item["product"] for item in semantic["items"]],
        )

    def test_product_aggregation_extra_production_fields_are_intentionally_absent(self):
        """INTENTIONALLY_DIFFERENT: share/sku_count are not first-version metrics."""
        production = get_product_aggregation(self.factory, BATCH, page_size=200)
        semantic = self.semantic(["product"])
        self.assertIn("share", production["items"][0])
        self.assertIn("sku_count", production["items"][0])
        self.assertNotIn("share", semantic["items"][0])
        # Derivable from the same engine without new code:
        drilldown = self.semantic(["product", "sku"])
        sku_counts = {}
        for item in drilldown["items"]:
            sku_counts[item["product"]] = sku_counts.get(item["product"], 0) + 1
        for item in production["items"]:
            with self.subTest(product_id=item["product_id"]):
                self.assertEqual(sku_counts[item["product_id"]], item["sku_count"])

    # -- 2. Channel × Product ------------------------------------------------

    def test_channel_product_is_compatible(self):
        production = get_channel_product(self.factory, BATCH, page_size=200)
        semantic = self.semantic(["channel", "product"])

        def key(item):
            return (item["channel_id"], item["product_id"])

        production_by_key = {key(item): item for item in production["items"]}
        semantic_by_key = {
            (item["channel"], item["product"]): item for item in semantic["items"]
        }
        self.assertEqual(set(production_by_key), set(semantic_by_key))
        for pair, expected in production_by_key.items():
            with self.subTest(pair=pair):
                actual = semantic_by_key[pair]
                self.assertEqual(Decimal(actual["actual_sales"]), Decimal(expected["qty"]))
                self.assertEqual(actual["channel_name"], expected["channel_name"])
                self.assertEqual(actual["product_name"], expected["product_name"])

        self.assertEqual(
            [key(item) for item in production["items"]],
            [(item["channel"], item["product"]) for item in semantic["items"]],
        )

    def test_channel_product_filtered_by_channel_is_compatible(self):
        filters = {"channel_ids": (CHANNEL_A_ID,)}
        production = get_channel_product(
            self.factory, BATCH, DashboardFilters(**filters), page_size=200
        )
        semantic = self.semantic(
            ["channel", "product"],
            filters=[{"dimension": "channel", "operator": "in", "values": [CHANNEL_A_ID]}],
        )
        self.assertEqual(self._production_total(production), Decimal(22))
        self.assertEqual(Decimal(semantic["total_qty"]), Decimal(22))
        production_by_key = {
            (item["channel_id"], item["product_id"]): Decimal(item["qty"])
            for item in production["items"]
        }
        semantic_by_key = {
            (item["channel"], item["product"]): Decimal(item["actual_sales"])
            for item in semantic["items"]
        }
        self.assertEqual(production_by_key, semantic_by_key)

    # -- 3. SalesRep × Product -----------------------------------------------

    def test_salesrep_product_is_compatible(self):
        production = get_salesrep_product(self.factory, BATCH, page_size=200)
        semantic = self.semantic(["salesrep", "product"])

        def key(item):
            return (item["salesrep_id"], item["product_id"])

        production_by_key = {key(item): item for item in production["items"]}
        semantic_by_key = {
            (item["salesrep"], item["product"]): item for item in semantic["items"]
        }
        self.assertEqual(set(production_by_key), set(semantic_by_key))
        for pair, expected in production_by_key.items():
            with self.subTest(pair=pair):
                actual = semantic_by_key[pair]
                self.assertEqual(Decimal(actual["actual_sales"]), Decimal(expected["qty"]))
                self.assertEqual(actual["salesrep_name"], expected["salesrep_name"])
        self.assertEqual(
            [key(item) for item in production["items"]],
            [(item["salesrep"], item["product"]) for item in semantic["items"]],
        )

    def test_salesrep_product_filtered_by_salesrep_is_compatible(self):
        production = get_salesrep_product(
            self.factory, BATCH, DashboardFilters(salesrep_ids=(REP_B_ID,)), page_size=200
        )
        semantic = self.semantic(
            ["salesrep", "product"],
            filters=[{"dimension": "salesrep", "operator": "in", "values": [REP_B_ID]}],
        )
        self.assertEqual(self._production_total(production), Decimal(12))
        self.assertEqual(Decimal(semantic["total_qty"]), Decimal(12))
        self.assertEqual(
            {(item["salesrep_id"], item["product_id"]): Decimal(item["qty"])
             for item in production["items"]},
            {(item["salesrep"], item["product"]): Decimal(item["actual_sales"])
             for item in semantic["items"]},
        )

    # -- 4. Product → SKU drilldown ------------------------------------------

    def test_product_sku_drilldown_is_compatible(self):
        production_products = get_product_aggregation(self.factory, BATCH, page_size=200)
        for product in production_products["items"]:
            product_id = product["product_id"]
            with self.subTest(product_id=product_id):
                production = get_product_sku_drilldown(
                    self.factory, BATCH, product_id, page_size=200
                )
                semantic = self.semantic(
                    ["sku"],
                    filters=[{"dimension": "product", "operator": "in",
                              "values": [product_id]}],
                )
                production_by_id = {
                    item["sku_id"]: Decimal(item["qty"]) for item in production["items"]
                }
                semantic_by_id = {
                    item["sku"]: Decimal(item["actual_sales"]) for item in semantic["items"]
                }
                self.assertEqual(production_by_id, semantic_by_id)
                self.assertEqual(
                    sum(production_by_id.values()), sum(semantic_by_id.values())
                )
                self.assertEqual(
                    [item["sku_id"] for item in production["items"]],
                    [item["sku"] for item in semantic["items"]],
                )

    def test_product_sku_drilldown_grouping_equals_product_sku_dimensions(self):
        """A drilldown for one product equals the filtered 2-dimension cross-tab."""
        for product_id in (2001, 2002, 2003):
            with self.subTest(product_id=product_id):
                drilldown = self.semantic(
                    ["sku"],
                    filters=[{"dimension": "product", "operator": "in",
                              "values": [product_id]}],
                )
                cross_tab = self.semantic(
                    ["product", "sku"],
                    filters=[{"dimension": "product", "operator": "in",
                              "values": [product_id]}],
                )
                self.assertEqual(
                    {item["sku"]: item["actual_sales"] for item in drilldown["items"]},
                    {item["sku"]: item["actual_sales"] for item in cross_tab["items"]},
                )

    # -- genuinely new capability --------------------------------------------

    def test_new_dimension_combinations_need_no_new_code(self):
        """Views that do not exist in production are free on the semantic engine."""
        combinations = [
            (["customer"], 4),
            (["sales_date"], 1),
            (["snapshot_month"], 1),
            (["channel", "salesrep"], 4),
            (["channel", "salesrep", "product"], 6),
        ]
        for dimensions, expected_groups in combinations:
            with self.subTest(dimensions=dimensions):
                result = self.semantic(dimensions)
                self.assertEqual(result["pagination"]["total_group_rows"], expected_groups)
                self.assertEqual(Decimal(result["total_qty"]), Decimal(DEMO_TOTAL_QTY))

    def test_unassigned_channel_row_is_visible_in_production_and_semantic(self):
        """The NULL-channel row is retained by both implementations."""
        production = get_channel_product(self.factory, BATCH, page_size=200)
        semantic = self.semantic(["channel", "product"])
        production_nulls = [item for item in production["items"] if item["channel_id"] is None]
        semantic_nulls = [item for item in semantic["items"] if item["channel"] is None]
        self.assertEqual(len(production_nulls), len(semantic_nulls))
        self.assertEqual(
            sum(Decimal(item["qty"]) for item in production_nulls),
            sum(Decimal(item["actual_sales"]) for item in semantic_nulls),
        )
        self.assertEqual(
            {item["channel_name"] for item in semantic_nulls}, {"未归属"}
        )

    # -- explicit non-support -------------------------------------------------

    def test_category_dimensions_are_not_supported(self):
        """NOT_SUPPORTED: requires a snapshot policy decision, never a silent JOIN."""
        from webapp.sales.semantic_query_engine import SemanticQueryError

        for key in ("category_l1", "category_l2", "category_l3", "category_l4"):
            with self.subTest(key=key):
                with self.assertRaises(SemanticQueryError):
                    validate_query({
                        "batch_id": BATCH, "metrics": ["actual_sales"],
                        "dimensions": [key],
                    })

    def test_no_mdm_join_is_emitted_for_any_dimension(self):
        """Snapshot-only guarantee: the plan never mentions an MDM table."""
        plan = self.semantic(["channel", "salesrep", "product"])["plan"]
        blob = repr(plan).lower()
        for mdm_table in ("mdm_channel", "mdm_salesrep", "mdm_product", "mdm_sku",
                          "mdm_customer", "join mdm"):
            self.assertNotIn(mdm_table, blob, mdm_table)
        self.assertEqual(plan["snapshot_semantics"], "FACT_SNAPSHOT_ONLY")


if __name__ == "__main__":
    unittest.main()
