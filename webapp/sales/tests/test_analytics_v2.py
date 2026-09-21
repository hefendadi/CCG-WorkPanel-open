"""Actual Sales V2 — lightweight analytics contract tests (TASK-ASV2-001).

AC coverage map (one test class per layer):

* AC-ASV2-001  metric ``actual_sales`` = SUM(actual_qty); exactly one metric
* AC-ASV2-002  dimension whitelist is the Publish-time snapshot field set
* AC-ASV2-003  business time is ``sales_date``; ``created_at`` is never time
* AC-ASV2-004  logical key -> registry metadata -> SQLAlchemy expression
* AC-ASV2-005  exactly one execution endpoint; no per-view endpoint
* AC-ASV2-006  time / product / BP channel / salesrep filters in one query
* AC-ASV2-007  Product × actual_sales table
* AC-ASV2-008  BP Channel × actual_sales horizontal bar, CSS only
* AC-ASV2-009  VIEW permission + current-Published-batch guard
* AC-ASV2-010  scope discipline: no KPI, no BI builder, Chinese business UI

Everything is synthetic and offline (in-memory SQLite, no MySQL, no network).
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="sales-analytics-v2-")
os.environ.setdefault("MDM_DATABASE_URL", f"sqlite+pysqlite:///{_TMP}/mdm.db")
os.environ["CCGTOOLS_ACCOUNT_DB_PATH"] = f"{_TMP}/app.db"

from demo.initialize_accounts import initialize_accounts  # noqa: E402

initialize_accounts(os.environ["CCGTOOLS_ACCOUNT_DB_PATH"])

from fastapi.testclient import TestClient  # noqa: E402

import webapp.main as main  # noqa: E402
from webapp.sales import semantic_api  # noqa: E402
from webapp.sales.semantic_query_engine import SemanticQueryError, validate_query  # noqa: E402
from webapp.sales.semantic_registry import (  # noqa: E402
    DATASET_SALES_ACTUAL,
    DIMENSIONS,
    METRICS,
    SALES_ACTUAL_DATASET,
    DimensionSpec,
    get_metric_metadata,
)
from webapp.sales.tests.fixtures import build_session_factory, seed_published_batch  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
HTML_PATH = ROOT / "webapp" / "static" / "sales-analytics.html"
JS_PATH = ROOT / "webapp" / "static" / "js" / "sales-analytics.js"
CSS_PATH = ROOT / "webapp" / "static" / "sales-analytics.css"

ANALYTICS_API = "/api/v1/sales/actual/analytics"
PAGE_ROUTE = "/sales/actual/analytics"
BATCH = "DEMO-BATCH-0001"

ALLOWED_USER = {"id": 1, "username": "demo_admin", "role": "admin",
                "permissions": {"sales_actual": "VIEW"}}

# Physical column names that are NOT legitimate business time on sales_fact.
FORBIDDEN_TIME_COLUMNS = {"created_at", "mapped_at", "published_at", "replaced_at"}


class RegistryContractTests(unittest.TestCase):
    """AC-ASV2-001 / 002 / 003 — the semantic layer itself."""

    def test_metric_is_actual_sales_sum_of_actual_qty(self):
        self.assertEqual([metric.key for metric in METRICS], ["actual_sales"])
        metric = SALES_ACTUAL_DATASET.metric("actual_sales")
        self.assertIsNotNone(metric)
        self.assertEqual(metric.aggregation, "sum")
        self.assertEqual(metric.column, "actual_qty")
        self.assertEqual(metric.unit, "Pcs")

    def test_public_metric_metadata_exposes_no_physical_column(self):
        metadata = get_metric_metadata(DATASET_SALES_ACTUAL)["actual_sales"]
        self.assertEqual(metadata["aggregation"], "sum")
        self.assertEqual(metadata["label"], "实际销量")
        self.assertNotIn("column", metadata)
        self.assertNotIn("actual_qty", str(metadata))

    def test_dimension_whitelist_is_the_snapshot_field_set(self):
        keys = {dimension.key for dimension in DIMENSIONS}
        for required in ("product", "channel", "salesrep", "sku", "sales_date"):
            with self.subTest(dimension=required):
                self.assertIn(required, keys)

    def test_business_time_is_sales_date(self):
        sales_date = SALES_ACTUAL_DATASET.dimension("sales_date")
        self.assertIsNotNone(sales_date)
        self.assertEqual(sales_date.key_column, "sales_date")
        self.assertEqual(sales_date.role, "date")

    def test_no_dimension_maps_a_non_business_timestamp_column(self):
        """AC-ASV2-003: created_at must never be reachable as business time."""
        for dimension in DIMENSIONS:
            if not isinstance(dimension, DimensionSpec):
                continue
            for attribute in ("key_column", "label_column", "code_column", "id_column"):
                value = getattr(dimension, attribute)
                with self.subTest(dimension=dimension.key, attribute=attribute):
                    self.assertNotIn(value, FORBIDDEN_TIME_COLUMNS)

    def test_engine_rejects_created_at_as_dimension_and_filter(self):
        base = {"batch_id": BATCH, "metrics": ["actual_sales"]}
        with self.assertRaises(SemanticQueryError):
            validate_query(dict(base, dimensions=["created_at"]))
        with self.assertRaises(SemanticQueryError):
            validate_query(dict(base, filters=[
                {"dimension": "created_at", "operator": "gte", "value": "2000-01-01"},
            ]))

    def test_engine_rejects_arbitrary_sql_shaped_keys(self):
        """AC-ASV2-004: nothing but a whitelisted logical key can reach SQL."""
        base = {"batch_id": BATCH, "dimensions": []}
        for hostile in ("actual_qty) FROM sales_fact --", "SUM(actual_qty)",
                        "sales_fact.actual_qty", "1=1"):
            with self.subTest(metric=hostile):
                with self.assertRaises(SemanticQueryError):
                    validate_query(dict(base, metrics=[hostile]))


class StaticPageContractTests(unittest.TestCase):
    """AC-ASV2-005 / 006 / 007 / 008 / 010 — the V2 page contract."""

    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")

    def test_page_and_assets_exist_and_are_linked(self):
        self.assertTrue(HTML_PATH.exists())
        self.assertTrue(JS_PATH.exists())
        self.assertTrue(CSS_PATH.exists())
        self.assertIn("/static/js/sales-analytics.js", self.html)
        self.assertIn("/static/sales-analytics.css", self.html)

    def test_every_id_the_script_reads_exists_in_the_markup(self):
        referenced = set(re.findall(r"\$\('([a-zA-Z0-9_-]+)'\)", self.js))
        self.assertTrue(referenced, "no element ids referenced by the script")
        for element_id in sorted(referenced):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_single_execution_endpoint(self):
        """AC-ASV2-005: one POST target, and no per-view dashboard endpoint."""
        self.assertIn(f"const API = '{ANALYTICS_API}'", self.js)
        for path in ("/metadata", "/query", "/filter-options"):
            with self.subTest(path=path):
                self.assertRegex(self.js, rf"apiFetch\([`']{re.escape(path)}")
        self.assertEqual(self.js.count("method: 'POST'"), 1)
        for dedicated in ("channel-products", "salesrep-products",
                          "/dashboard/products", "aggregates/"):
            with self.subTest(endpoint=dedicated):
                self.assertNotIn(dedicated, self.js, dedicated)

    def test_global_filters_are_present(self):
        """AC-ASV2-006: time / product / BP channel / salesrep."""
        for element_id in ("filter-from", "filter-to", "filter-product",
                           "filter-channel", "filter-salesrep", "clear-filters"):
            with self.subTest(control=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        for dimension in ("sales_date", "product", "channel", "salesrep"):
            with self.subTest(dimension=dimension):
                self.assertIn(dimension, self.js)

    def test_table_and_bar_containers_exist(self):
        """AC-ASV2-007 / 008."""
        self.assertIn('id="product-table"', self.html)
        self.assertIn('id="channel-bars"', self.html)
        self.assertIn("sales-analytics-bar-fill", self.css)
        self.assertIn("sales-analytics-bar-track", self.css)

    def test_horizontal_bar_width_is_data_driven_and_css_only(self):
        """AC-ASV2-008: bar width comes from the query result, not a library."""
        self.assertIn("width:${width}%", self.js)
        self.assertIn("max", self.js)
        lowered = (self.html + self.js).lower()
        for library in ("echarts", "chart.js", "chartjs", "highcharts", "plotly",
                        "d3.js", "d3.min", "apexcharts"):
            with self.subTest(library=library):
                self.assertNotIn(library, lowered, library)

    def test_frontend_never_builds_or_renders_sql(self):
        """AC-ASV2-004."""
        lowered = self.js.lower()
        for keyword in ("select *", "select count", "select sum", "order by ",
                        " from sales", " join ", "sqlalchemy", "sum("):
            with self.subTest(keyword=keyword):
                self.assertNotIn(keyword, lowered, keyword)

    def test_scope_discipline_no_kpi_no_bi_features(self):
        """AC-ASV2-010 (plus the Orchestrator patch removing the header KPI)."""
        lowered_html = self.html.lower()
        self.assertNotIn("kpi", lowered_html)
        self.assertNotIn("出库总量", self.html)
        self.assertNotIn("total_qty", self.js)
        for out_of_scope in ("Saved View", "Pivot", "Dashboard Builder",
                             "自定义指标", "自定义 SQL", "Forecast", "拖拽"):
            with self.subTest(feature=out_of_scope):
                self.assertNotIn(out_of_scope.lower(), lowered_html)
                self.assertNotIn(out_of_scope.lower(), self.js.lower())

    def test_chinese_business_labels(self):
        """AC-ASV2-010: 中文业务 UI."""
        for label in ("销售实绩", "商品", "BP渠道", "营业员", "实际销量", "时间"):
            with self.subTest(label=label):
                self.assertIn(label, self.html)
        self.assertIn("实际销量", self.js)


class ServedAnalyticsTests(unittest.TestCase):
    """AC-ASV2-001 / 005 / 006 / 007 / 008 / 009 — served over the real app."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)
        # The production session factory insists on the MySQL driver; the served
        # tests run offline against the synthetic SQLite fixture.
        cls._real_session_factory = semantic_api.get_session_factory
        semantic_api.get_session_factory = lambda: cls.factory
        main.app.dependency_overrides[semantic_api.sales_semantic_viewer] = lambda: ALLOWED_USER
        main.app.dependency_overrides[semantic_api.sales_semantic_page_viewer] = lambda: ALLOWED_USER
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        semantic_api.get_session_factory = cls._real_session_factory
        cls.engine.dispose()

    def _query(self, **body):
        payload = {"batch_id": BATCH, "metrics": ["actual_sales"]}
        payload.update(body)
        return self.client.post(f"{ANALYTICS_API}/query", json=payload)

    def test_page_route_serves_the_v2_page(self):
        response = self.client.get(PAGE_ROUTE)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("销售实绩", response.text)
        self.assertIn("/static/js/sales-analytics.js", response.text)

    def test_script_is_served(self):
        response = self.client.get("/static/js/sales-analytics.js")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(f"{ANALYTICS_API}/query", response.text)

    def test_metadata_endpoint_exposes_actual_sales_only(self):
        body = self.client.get(f"{ANALYTICS_API}/metadata").json()
        self.assertEqual(body["dataset"], "sales_actual")
        self.assertEqual([metric["key"] for metric in body["metrics"]], ["actual_sales"])
        self.assertEqual(body["metrics"][0]["label"], "实际销量")
        dimension_keys = {dimension["key"] for dimension in body["dimensions"]}
        for key in ("product", "channel", "salesrep", "sales_date"):
            self.assertIn(key, dimension_keys)
        self.assertNotIn("created_at", dimension_keys)

    def test_product_table_rows_carry_actual_sales(self):
        """AC-ASV2-007."""
        response = self._query(dimensions=["product"], sort=[{"field": "actual_sales", "direction": "desc"}])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["items"], body)
        for item in body["items"]:
            self.assertIn("product_name", item)
            self.assertIsNotNone(item["actual_sales"])
        totals = [Decimal(item["actual_sales"]) for item in body["items"]]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_channel_rows_for_a_selected_product(self):
        """AC-ASV2-008 backing query: product filter -> channel breakdown."""
        product = self._query(dimensions=["product"]).json()["items"][0]["product"]
        response = self._query(
            dimensions=["channel"],
            filters=[{"dimension": "product", "operator": "eq", "value": product}],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["items"], body)
        for item in body["items"]:
            self.assertIn("channel_name", item)
            self.assertIsNotNone(item["actual_sales"])

    def test_time_and_reference_filters_combine_in_one_query(self):
        """AC-ASV2-006."""
        options = self.client.get(
            f"{ANALYTICS_API}/filter-options?batch_id={BATCH}&dimensions=channel,salesrep"
        ).json()["options"]
        channel = next(option["key"] for option in options["channel"] if option["key"] is not None)
        filters = [
            {"dimension": "sales_date", "operator": "between",
             "values": ["2000-02-01", "2000-02-28"]},
            {"dimension": "channel", "operator": "in", "values": [channel]},
        ]
        unfiltered = self._query(dimensions=[]).json()["items"][0]["actual_sales"]
        response = self._query(dimensions=["product"], filters=filters)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        filtered = self._query(dimensions=[], filters=filters).json()["items"][0]["actual_sales"]
        self.assertLessEqual(Decimal(filtered), Decimal(unfiltered))
        self.assertTrue(body["items"], body)

    def test_created_at_is_rejected_over_http(self):
        """AC-ASV2-003 end to end."""
        response = self._query(dimensions=["created_at"])
        self.assertEqual(response.status_code, 422, response.text)

    def test_unauthenticated_requests_are_rejected(self):
        """AC-ASV2-009."""
        overrides = dict(main.app.dependency_overrides)
        main.app.dependency_overrides.clear()
        try:
            anonymous = TestClient(main.app, raise_server_exceptions=False)
            for method, path in (("get", f"{ANALYTICS_API}/metadata"),
                                 ("get", f"{ANALYTICS_API}/filter-options?batch_id={BATCH}"),
                                 ("get", PAGE_ROUTE)):
                with self.subTest(path=path):
                    response = getattr(anonymous, method)(path)
                    self.assertIn(response.status_code, (401, 403, 302), response.text)
            post = anonymous.post(f"{ANALYTICS_API}/query",
                                  json={"batch_id": BATCH, "metrics": ["actual_sales"]})
            self.assertIn(post.status_code, (401, 403), post.text)
            anonymous.close()
        finally:
            main.app.dependency_overrides.update(overrides)


if __name__ == "__main__":
    unittest.main()
