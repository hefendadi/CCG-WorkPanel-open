"""Frontend smoke tests for the ACT Sales Semantic Explorer spike.

Two layers:

* static contract — the page markup, the script bindings, and the "one execution
  endpoint, four presets" guarantee;
* served-asset contract — the page route and the static script really are served
  by the FastAPI app, with dependencies overridden so no operator database is
  touched.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="sales-semantic-frontend-")
os.environ.setdefault("MDM_DATABASE_URL", f"sqlite+pysqlite:///{_TMP}/mdm.db")
os.environ["CCGTOOLS_ACCOUNT_DB_PATH"] = f"{_TMP}/app.db"

from demo.initialize_accounts import initialize_accounts  # noqa: E402

initialize_accounts(os.environ["CCGTOOLS_ACCOUNT_DB_PATH"])

from fastapi.testclient import TestClient  # noqa: E402

import webapp.main as main  # noqa: E402
from webapp.sales import semantic_api  # noqa: E402
from webapp.sales.tests.fixtures import build_session_factory, seed_published_batch  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
HTML_PATH = ROOT / "webapp" / "static" / "sales-semantic-demo.html"
JS_PATH = ROOT / "webapp" / "static" / "js" / "sales-semantic-demo.js"

ALLOWED_USER = {"id": 1, "username": "demo_admin", "role": "admin",
                "permissions": {"sales_actual": "VIEW"}}


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")

    def test_page_and_script_exist_and_are_linked(self):
        self.assertTrue(HTML_PATH.exists())
        self.assertTrue(JS_PATH.exists())
        self.assertIn("/static/js/sales-semantic-demo.js", self.html)

    def test_required_controls_exist(self):
        for control in ("sem-metric", "sem-dimensions", "sem-filters", "sem-sort",
                        "sem-run", "sem-presets", "sem-inspector", "sem-table",
                        "sem-thead", "sem-tbody", "sem-plan", "sem-future-dims",
                        "sem-view-explorer", "sem-view-architecture", "sem-sim-tbody"):
            with self.subTest(control=control):
                self.assertIn(f'id="{control}"', self.html)

    def test_every_id_the_script_reads_exists_in_the_markup(self):
        import re

        referenced = set(re.findall(r"el\(['\"]([a-zA-Z0-9_-]+)['\"]\)", self.js))
        referenced |= set(re.findall(r"getElementById\(['\"]([a-zA-Z0-9_-]+)['\"]\)", self.js))
        self.assertTrue(referenced, "no element ids referenced by the script")
        for element_id in sorted(referenced):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_four_presets_are_metadata_driven_not_hardcoded(self):
        # The labels come from the API; the script must not embed the view list.
        self.assertIn("data-sem-preset", self.js)
        self.assertIn("state.presets", self.js)
        for hardcoded in ("channel_product", "salesrep_product", '"product_sku"'):
            self.assertNotIn(hardcoded, self.js, hardcoded)

    def test_script_never_calls_a_dedicated_dashboard_api(self):
        for dedicated in (
            "/dashboard/channel-products",
            "/dashboard/salesrep-products",
            "/dashboard/products",
            "channel-products",
            "salesrep-products",
        ):
            with self.subTest(endpoint=dedicated):
                self.assertNotIn(dedicated, self.js, dedicated)

    def test_script_uses_the_single_semantic_endpoints(self):
        self.assertIn("/api/v1/lab/sales/query", self.js)
        self.assertIn("/api/v1/lab/sales/semantic", self.js)
        self.assertIn("/api/v1/lab/sales/filter-options", self.js)
        # Exactly one POST target in the whole script.
        self.assertEqual(self.js.count("method: 'POST'"), 1)

    def test_architecture_tab_contains_before_after_and_snapshot_decision(self):
        for marker in ("BEFORE", "AFTER", "Semantic Query Engine",
                       "get_channel_product()", "get_salesrep_product()"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html, marker)
        for future in ("category_l1", "REQUIRES_SNAPSHOT_POLICY_DECISION"):
            with self.subTest(future=future):
                # Rendered from metadata, so only the renderer name is asserted here.
                self.assertTrue(future in self.js or future in self.html or
                                "future_dimensions" in self.js, future)
        self.assertIn("future_dimensions", self.js)
        self.assertIn("Option A", self.html)
        self.assertIn("Option B", self.html)

    def test_simulated_category_preview_is_explicitly_labelled(self):
        self.assertIn("SIMULATED", self.html)
        self.assertIn("SIMULATED", self.js)
        # The simulation must be local constants, never an engine call.
        self.assertIn("renderSimulatedPreview", self.js)

    def test_frontend_never_builds_or_renders_sql(self):
        """SQL only ever exists server-side; the page shows a logical plan.

        ``max_group_by`` is the guardrail name shared with the API, so the check
        targets SQL syntax rather than the words "group by".
        """
        lowered = self.js.lower()
        for keyword in ("select *", "select count", "select sum", "select distinct",
                        "order by ", " from sales", " join ", "sqlalchemy",
                        "sum(", "where ", ";drop "):
            with self.subTest(keyword=keyword):
                self.assertNotIn(keyword, lowered, keyword)
        # No ad-hoc query string construction beyond documented API params.
        self.assertIn("json.stringify(request)", lowered)
        # The plan is rendered from the API response, only into the Architecture tab.
        self.assertIn("renderplan", lowered)
        self.assertIn("sem-plan", self.js)

    def test_group_by_cap_is_enforced_in_the_ui(self):
        self.assertIn("MAX_GROUP_BY = 3", self.js)
        self.assertIn("最多", self.js)


class ServedAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id="DEMO-BATCH-0001")
        main.app.dependency_overrides[semantic_api.sales_semantic_viewer] = lambda: ALLOWED_USER
        main.app.dependency_overrides[semantic_api.sales_semantic_page_viewer] = lambda: ALLOWED_USER
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        cls.engine.dispose()

    def test_page_route_serves_the_explorer(self):
        response = self.client.get("/lab/sales-semantic-layer")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("ACT Sales Semantic Explorer", response.text)
        self.assertIn("/static/js/sales-semantic-demo.js", response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_static_script_is_served(self):
        response = self.client.get("/static/js/sales-semantic-demo.js")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("/api/v1/lab/sales/query", response.text)

    def test_metadata_endpoint_payload_drives_the_ui(self):
        body = self.client.get("/api/v1/lab/sales/semantic").json()
        metric_keys = {metric["key"] for metric in body["metrics"]}
        dimension_keys = {dimension["key"] for dimension in body["dimensions"]}
        # The UI builds its metric select and dimension checkboxes from these.
        self.assertIn("actual_sales", metric_keys)
        for key in ("product", "sku", "channel", "salesrep", "customer"):
            self.assertIn(key, dimension_keys)
        self.assertEqual(len(body["presets"]), 4)


class AuthIntegrationRegressionTests(unittest.TestCase):
    """Regression guards for the two integration bugs found in the live demo.

    1. The lab API is gated by ``require_permission(..., current_user)``, which
       reads a Bearer token. The page route is cookie-gated. Fetching the page
       therefore succeeds while every API call returns 401, which surfaced as
       "Semantic metadata 初始化失败" with an empty Explorer. The page must load
       ``common.js`` and route API calls through its ``authFetch``.
    2. ``state.dimensions`` is the group-by *selection*; assigning the dimension
       metadata array to it made the selection look 7 dimensions long and tripped
       the 3-dimension guard on the first automatic query.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")

    def test_page_loads_the_bearer_token_helper(self):
        self.assertIn("/static/js/common.js", self.html)
        # common.js must be evaluated before the demo script uses authFetch.
        self.assertLess(
            self.html.index("/static/js/common.js"),
            self.html.index("/static/js/sales-semantic-demo.js"),
        )

    def test_api_calls_go_through_authfetch_not_raw_fetch(self):
        self.assertIn("authFetch(", self.js)
        self.assertNotIn("await fetch(", self.js)
        self.assertNotIn("fetch(url", self.js)

    def test_page_verifies_the_session_before_loading_metadata(self):
        import re

        call = re.search(r"await requireLogin\s*\(\s*\)", self.js)
        self.assertIsNotNone(call, "init() does not await requireLogin()")
        self.assertLess(call.start(), self.js.index("await loadMetadata()"))

    def test_group_by_selection_starts_empty_and_is_not_the_metadata_array(self):
        self.assertIn("state.dimensions = [];", self.js)
        self.assertNotIn("state.dimensions = metadata.dimensions", self.js)
        # The option list must be read from metadata, not from the selection.
        self.assertIn("state.metadata.dimensions", self.js)

    def test_page_loads_the_stylesheets_that_define_its_classes(self):
        # ccg-page-header / ccg-button / ccg-select are not in the design-system
        # bundle, so the page must load the same shared sheets as Sales Actual.
        for sheet in ("/static/overview.css", "/static/sales-actual.css"):
            with self.subTest(sheet=sheet):
                self.assertIn(sheet, self.html)
        for shared_class in ("ccg-button", "ccg-select", "ccg-page-header"):
            with self.subTest(shared_class=shared_class):
                self.assertIn(shared_class, self.html)


if __name__ == "__main__":
    unittest.main()
