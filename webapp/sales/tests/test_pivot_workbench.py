"""Sales Pivot Workbench contract tests (PIVOT-UX-DEMO-V2).

The workbench is an architecture/UX demo: the acceptance is that the *same*
endpoint can express arbitrary field combinations without code changes, while the
whitelist discipline and the many-to-one join guarantee still hold.

Coverage map:

* CatalogContractTests      field catalog shape, groups, no technical columns
* EngineContractTests       whitelist, dynamic columns, filters, join cardinality
* ServedPivotTests          the same contract over the real app + HTTP
* StaticPageContractTests   plain-HTML page, no chart library, no technical wording

Everything is synthetic and offline (in-memory SQLite, no MySQL, no network).
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="sales-pivot-")
os.environ.setdefault("MDM_DATABASE_URL", f"sqlite+pysqlite:///{_TMP}/mdm.db")
os.environ["CCGTOOLS_ACCOUNT_DB_PATH"] = f"{_TMP}/app.db"

from demo.initialize_accounts import initialize_accounts  # noqa: E402

initialize_accounts(os.environ["CCGTOOLS_ACCOUNT_DB_PATH"])

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import webapp.main as main  # noqa: E402
from webapp.mdm.models import Channel, Customer, Product, Province, Region, SKU, SalesRep  # noqa: E402
from webapp.sales import pivot_api  # noqa: E402
from webapp.sales import pivot_query_engine as engine  # noqa: E402
from webapp.sales.models import SalesFact, SalesImportBatch, SalesImportRow  # noqa: E402
from webapp.sales.pivot_catalog import (  # noqa: E402
    FIELDS,
    GROUP_ORDER,
    MAX_GROUP_BY_DIMENSIONS,
    METRIC_ACTUAL_SALES,
    get_catalog_metadata,
)
from webapp.sales.pivot_query_engine import PivotQueryError, run_query, validate_query  # noqa: E402
from webapp.sales.tests.fixtures import (  # noqa: E402
    DEMO_ROWS,
    build_session_factory,
    seed_published_batch,
)

ROOT = Path(__file__).resolve().parents[3]
HTML_PATH = ROOT / "webapp" / "static" / "sales-pivot.html"
JS_PATH = ROOT / "webapp" / "static" / "js" / "sales-pivot.js"
CSS_PATH = ROOT / "webapp" / "static" / "sales-pivot.css"

PIVOT_API = "/api/v1/sales/actual/pivot"
PAGE_ROUTE = "/sales/actual/workbench"
BATCH = "DEMO-BATCH-0001"

ALLOWED_USER = {"id": 1, "username": "demo_admin", "role": "admin",
                "permissions": {"sales_actual": "VIEW"}}

TECHNICAL_COLUMNS = {
    "id", "stable_id", "status", "created_at", "updated_at", "import_row_id",
    "source_system", "source_document_no", "source_line_no",
    "parent_customer_id", "source_created_at", "source_created_ym",
}

# Sample facts use deterministic surrogate ids (see fixtures._stable_index).
SEEDED = {
    "channel": {4031: "Demo Channel A", 4032: "Demo Channel B"},
    "salesrep": {5041: "Demo Rep A", 5042: "Demo Rep B"},
    "customer": {
        3021: dict(customer_code="CUST-DEMO-001", customer_name="Demo Customer A",
                   channel_id=4031, salesrep_id=5041, department="营业一部",
                   market_type="EC", organization="CHC", format_type="流通",
                   business_type="B2B", channel_detail="流通-其他", is_direct=True),
        3022: dict(customer_code="CUST-DEMO-002", customer_name="Demo Customer B",
                   channel_id=4032, salesrep_id=5041, department="营业二部",
                   market_type="REAL", organization="CCC", format_type="RKA",
                   business_type="B2C", channel_detail="EC-Distribution", is_direct=False),
        3023: dict(customer_code="CUST-DEMO-003", customer_name="Demo Customer C",
                   channel_id=4031, salesrep_id=5042, department=None,
                   market_type=None, organization=None, format_type=None,
                   business_type=None, channel_detail=None, is_direct=None),
    },
    "product": {2001: ("PROD-DEMO-001", "Demo Product 1", "Demo Brand A"),
                2002: ("PROD-DEMO-002", "Demo Product 2", "Demo Brand B")},
    "sku": {1011: ("SKU-DEMO-001", "Demo SKU 1", 2001, "SNK"),
            1012: ("SKU-DEMO-002", "Demo SKU 2", 2001, "SNK"),
            1013: ("SKU-DEMO-003", "Demo SKU 3", 2002, "FGR")},
}


def _batch_row(factory, batch_id):
    with factory() as session:
        return session.execute(
            select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id)
        ).scalar_one()


def direct_total(factory, batch_id, *, customer_column=None, customer_value=None,
                 fact_column=None, fact_value=None, extra=()):
    """Independent SUM(actual_qty) — plain SQLAlchemy, no engine helpers."""
    sf, sir = SalesFact.__table__, SalesImportRow.__table__
    customer = Customer.__table__
    batch = _batch_row(factory, batch_id)
    statement = select(func.sum(sf.c.actual_qty)).select_from(
        sf.join(sir, sir.c.id == sf.c.import_row_id)
    )
    if customer_column is not None:
        # LEFT JOIN, exactly like the engine: a fact row whose Customer is missing
        # also falls into 未归属, so an inner join would under-count that group.
        statement = statement.outerjoin(customer, customer.c.id == sf.c.customer_id)
        if customer_value is None:
            statement = statement.where(customer_column.is_(None))
        else:
            statement = statement.where(customer_column == customer_value)
    if fact_column is not None:
        statement = statement.where(fact_column == fact_value)
    statement = statement.where(
        sf.c.source_system == batch.source_system,
        sf.c.snapshot_month == batch.snapshot_month,
        sir.c.import_batch_id == batch.id,
        *extra,
    )
    with factory() as session:
        return session.execute(statement).scalar_one()


def seed_master_data(session_factory) -> None:
    """Current MDM rows for the synthetic facts (2003/1014 stay unseeded)."""
    with session_factory() as session, session.begin():
        region = Region(id=9001, stable_id="REG_DEMO_1", region_code="REG-DEMO-001",
                        region_name="Demo Region A")
        province = Province(id=9101, stable_id="PRV_DEMO_1", province_code="PRV-DEMO-001",
                            province_name="Demo Province A", region_id=9001)
        session.add_all([region, province])
        for channel_id, channel_name in SEEDED["channel"].items():
            session.add(Channel(id=channel_id, stable_id=f"CHN_DEMO_{channel_id}",
                                channel_code=f"CHN-DEMO-{channel_id}", channel_name=channel_name))
        for rep_id, rep_name in SEEDED["salesrep"].items():
            session.add(SalesRep(id=rep_id, stable_id=f"REP_DEMO_{rep_id}",
                                 employee_code=f"EMP-DEMO-{rep_id}", salesrep_name=rep_name))
        for product_id, (code, name, brand) in SEEDED["product"].items():
            session.add(Product(id=product_id, stable_id=f"PRD_DEMO_{product_id}",
                                product_code=code, product_name=name, brand=brand))
        for sku_id, (code, name, product_id, category_l1) in SEEDED["sku"].items():
            session.add(SKU(id=sku_id, stable_id=f"SKU_DEMO_{sku_id}", sku_code=code,
                            sku_name=name, product_id=product_id, category_l1=category_l1,
                            category_l2="礼包", case_pack=Decimal("12")))
        for customer_id, values in SEEDED["customer"].items():
            session.add(Customer(id=customer_id, stable_id=f"CUS_DEMO_{customer_id}",
                                 region_id=9001, province_id=9101, **values))


class CatalogContractTests(unittest.TestCase):
    """The field catalog itself."""

    def test_catalog_covers_five_business_groups(self):
        metadata = get_catalog_metadata()
        self.assertEqual([group["key"] for group in metadata["groups"]], list(GROUP_ORDER))
        self.assertEqual(metadata["dataset"], "sales_pivot")
        self.assertGreaterEqual(len(metadata["fields"]), 30)

    def test_every_field_declares_key_label_group_type_and_flags(self):
        for field in FIELDS:
            with self.subTest(field=field.key):
                self.assertTrue(field.key and field.label and field.group and field.type)
                self.assertIn(field.group, GROUP_ORDER)
                self.assertTrue(field.groupable or field.filterable)
                self.assertTrue(set(field.operators))

    def test_technical_and_governance_columns_are_excluded(self):
        keys = {field.key for field in FIELDS}
        self.assertFalse(keys & TECHNICAL_COLUMNS)
        raw = str([field.key for field in FIELDS]).lower()
        for forbidden in ("stable_id", "created_at", "source_document", "import_row"):
            with self.subTest(column=forbidden):
                self.assertNotIn(forbidden, raw)

    def test_snapshot_and_current_master_variants_coexist_and_are_labelled(self):
        keys = {field.key for field in FIELDS}
        for key in ("product_snapshot", "product_current", "sku_snapshot", "sku_current",
                    "bp_channel_snapshot", "bp_channel_current",
                    "salesrep_snapshot", "salesrep_current"):
            with self.subTest(field=key):
                self.assertIn(key, keys)
        labels = {field.key: field.label for field in FIELDS}
        self.assertIn("销售发生时快照", labels["product_snapshot"])
        self.assertIn("当前主档", labels["product_current"])
        self.assertIn("销售发生时快照", labels["bp_channel_snapshot"])
        self.assertIn("当前主档", labels["bp_channel_current"])

    def test_public_field_metadata_never_leaks_a_column(self):
        metadata = get_catalog_metadata()
        expected = {"key", "label", "group", "type", "origin", "nullable",
                    "groupable", "filterable", "operators", "description"}
        for field in metadata["fields"]:
            with self.subTest(field=field["key"]):
                self.assertEqual(set(field), expected)
        payload = str(metadata).lower()
        for token in ("sqlalchemy", "select ", "text("):
            with self.subTest(token=token):
                self.assertNotIn(token, payload)

    def test_catalog_matches_the_engine_allow_list(self):
        for field in FIELDS:
            for name in (field.key_column, field.label_column, field.code_column):
                if name:
                    with self.subTest(field=field.key, column=name):
                        self.assertIn(name, engine._EXPRESSIONS)


class EngineContractTests(unittest.TestCase):
    """Whitelist + dynamic grouping over the synthetic batch."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)
        seed_master_data(cls.factory)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def _run(self, **body):
        payload = {"batch_id": BATCH, "metrics": [METRIC_ACTUAL_SALES]}
        payload.update(body)
        return run_query(self.factory, validate_query(payload))

    # -- whitelist ---------------------------------------------------------

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(PivotQueryError):
            validate_query({"batch_id": BATCH, "dimensions": ["definitely_not_a_field"]})

    def test_sql_shaped_keys_are_rejected(self):
        for hostile in ("department) --", "SUM(actual_qty)", "sales_fact.department", "1=1"):
            with self.subTest(key=hostile):
                with self.assertRaises(PivotQueryError):
                    validate_query({"batch_id": BATCH, "dimensions": [hostile]})

    def test_at_most_four_dimensions(self):
        ok = validate_query({"batch_id": BATCH, "dimensions": [
            "department", "market_type", "bp_channel_snapshot", "salesrep_snapshot"]})
        self.assertEqual(len(ok.dimensions), 4)
        self.assertEqual(MAX_GROUP_BY_DIMENSIONS, 4)
        with self.assertRaises(PivotQueryError):
            validate_query({"batch_id": BATCH, "dimensions": [
                "department", "market_type", "bp_channel_snapshot",
                "salesrep_snapshot", "sku_snapshot"]})

    def test_only_the_actual_sales_metric_is_accepted(self):
        with self.assertRaises(PivotQueryError):
            validate_query({"batch_id": BATCH, "metrics": ["margin"]})
        with self.assertRaises(PivotQueryError):
            validate_query({"batch_id": BATCH, "metrics": ["actual_sales", "margin"]})

    def test_unknown_request_fields_and_bad_operators_are_rejected(self):
        with self.assertRaises(PivotQueryError):
            validate_query({"batch_id": BATCH, "raw_sql": "SELECT 1"})
        with self.assertRaises(PivotQueryError):
            validate_query({"batch_id": BATCH, "filters": [
                {"field": "department", "operator": "regex", "value": "x"}]})

    # -- dynamic result shape ---------------------------------------------

    def test_columns_follow_the_selected_dimensions(self):
        result = self._run(dimensions=["department", "market_type", "bp_channel_snapshot"])
        self.assertEqual([column["key"] for column in result["columns"]],
                         ["department", "market_type", "bp_channel_snapshot",
                          METRIC_ACTUAL_SALES])
        self.assertEqual([column["label"] for column in result["columns"]][:1], ["部门"])

        other = self._run(dimensions=["sku_current", "salesrep_snapshot"])
        self.assertEqual([column["key"] for column in other["columns"]],
                         ["sku_current", "salesrep_snapshot", METRIC_ACTUAL_SALES])

    def test_total_is_preserved_for_every_combination(self):
        for dimensions in ([], ["department"], ["product_current"], ["bp_channel_snapshot"],
                           ["sku_current", "bp_channel_snapshot"],
                           ["department", "market_type", "bp_channel_snapshot"],
                           ["category_l1", "bp_channel_snapshot", "salesrep_current"]):
            with self.subTest(dimensions=dimensions):
                result = self._run(dimensions=dimensions)
                total = sum(Decimal(item[METRIC_ACTUAL_SALES]) for item in result["items"])
                self.assertEqual(total, Decimal("35"))

    def test_join_is_many_to_one_and_never_multiplies_rows(self):
        result = self._run(dimensions=["sku_current", "customer_current", "province"])
        cardinality = result["plan"]["join_cardinality"]
        self.assertEqual(cardinality["base_rows"], 6)
        self.assertEqual(cardinality["joined_rows"], 6)
        self.assertTrue(cardinality["no_multiplication"])

    def test_unmatched_master_rows_are_reported_as_unassigned(self):
        result = self._run(dimensions=["product_current"])
        labels = {item["product_current"] for item in result["items"]}
        self.assertIn("未归属", labels)   # PROD-DEMO-003 has no master row
        self.assertIn("Demo Product 1", labels)

    def test_all_null_field_is_still_groupable(self):
        result = self._run(dimensions=["product_group"])
        self.assertEqual([item["product_group"] for item in result["items"]], ["未归属"])

    def test_filters_restrict_the_metric(self):
        filtered = self._run(dimensions=["market_type"], filters=[
            {"field": "market_type", "operator": "in", "values": ["EC"]}])
        self.assertEqual([item["market_type"] for item in filtered["items"]], ["EC"])
        self.assertEqual(Decimal(filtered["items"][0][METRIC_ACTUAL_SALES]), Decimal("17"))

        ranged = self._run(dimensions=["salesrep_current"], filters=[
            {"field": "sales_date", "operator": "between",
             "values": ["2000-02-01", "2000-02-28"]}])
        self.assertEqual({item["salesrep_current"] for item in ranged["items"]},
                         {"Demo Rep A", "Demo Rep B", "未归属"})
        excluded = self._run(dimensions=["salesrep_current"], filters=[
            {"field": "sales_date", "operator": "between",
             "values": ["2000-03-01", "2000-03-31"]}])
        self.assertEqual(excluded["items"], [])

    def test_ordering_is_metric_descending_then_dimension_ascending(self):
        result = self._run(dimensions=["bp_channel_snapshot"])
        values = [Decimal(item[METRIC_ACTUAL_SALES]) for item in result["items"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_bar_is_only_supported_for_one_dimension(self):
        self.assertTrue(self._run(dimensions=["department"])["display"]["bar_supported"])
        two = self._run(dimensions=["department", "market_type"])
        self.assertFalse(two["display"]["bar_supported"])
        self.assertIn("横向柱状图暂只支持一个分析维度", two["display"]["bar_hint"])

    def test_field_availability_marks_empty_fields(self):
        availability = engine.field_availability(self.factory, BATCH)
        self.assertTrue(availability["department"])
        self.assertTrue(availability["market_type"])
        self.assertTrue(availability["province"])
        self.assertFalse(availability["product_group"])

    def test_filter_options_come_from_the_data(self):
        options = engine.get_filter_options(self.factory, BATCH, ["market_type", "sales_date"])
        market = {item["key"]: item["label"] for item in options["options"]["market_type"]}
        self.assertEqual(market.get("EC"), "EC")
        self.assertIn("未归属", market.values())
        dates = [item["key"] for item in options["options"]["sales_date"]]
        self.assertEqual(dates, ["2000-02-15"])


class ServedPivotTests(unittest.TestCase):
    """The same contract over the real app."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)
        seed_master_data(cls.factory)
        cls._real_session_factory = pivot_api.get_session_factory
        pivot_api.get_session_factory = lambda: cls.factory
        main.app.dependency_overrides[pivot_api.pivot_viewer] = lambda: ALLOWED_USER
        main.app.dependency_overrides[pivot_api.pivot_page_viewer] = lambda: ALLOWED_USER
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        pivot_api.get_session_factory = cls._real_session_factory
        cls.engine.dispose()

    def _query(self, **body):
        payload = {"batch_id": BATCH, "metrics": [METRIC_ACTUAL_SALES]}
        payload.update(body)
        return self.client.post(f"{PIVOT_API}/query", json=payload)

    def test_page_route_serves_the_workbench(self):
        response = self.client.get(PAGE_ROUTE)
        self.assertEqual(response.status_code, 200, response.text)
        for label in ("销售分析工作台", "分析字段", "执行分析"):
            with self.subTest(label=label):
                self.assertIn(label, response.text)

    def test_fields_endpoint_reports_groups_and_sample_coverage(self):
        body = self.client.get(f"{PIVOT_API}/fields?batch_id={BATCH}").json()
        self.assertEqual([group["key"] for group in body["groups"]], list(GROUP_ORDER))
        self.assertIn("department", body["fields_with_sample_data"])
        self.assertIn("product_group", body["fields_without_sample_data"])
        self.assertEqual(body["metric"]["key"], METRIC_ACTUAL_SALES)

    def test_filter_options_endpoint(self):
        response = self.client.get(
            f"{PIVOT_API}/filter-options?batch_id={BATCH}&fields=market_type,bp_channel_snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        options = response.json()["options"]
        self.assertIn("market_type", options)
        self.assertTrue(options["bp_channel_snapshot"])

    def test_query_endpoint_builds_columns_dynamically(self):
        response = self._query(dimensions=["department", "market_type", "bp_channel_snapshot"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["columns"]), 4)
        self.assertEqual(body["items"][0]["department"], "营业一部")

    def test_query_endpoint_rejects_invalid_requests(self):
        for body in ({"dimensions": ["nope"]},
                     {"dimensions": ["a", "b", "c", "d", "e"]},
                     {"metrics": ["margin"]},
                     {"dimensions": ["actual_qty) FROM sales_fact --"]},
                     {"raw_sql": "SELECT 1"}):
            with self.subTest(body=body):
                response = self._query(**body)
                self.assertEqual(response.status_code, 422, response.text)

    def test_unauthenticated_requests_are_rejected(self):
        overrides = dict(main.app.dependency_overrides)
        main.app.dependency_overrides.clear()
        try:
            anonymous = TestClient(main.app, raise_server_exceptions=False)
            for path in (f"{PIVOT_API}/fields", f"{PIVOT_API}/filter-options?batch_id={BATCH}",
                         PAGE_ROUTE):
                with self.subTest(path=path):
                    self.assertIn(anonymous.get(path).status_code, (401, 403, 302))
            post = anonymous.post(f"{PIVOT_API}/query",
                                  json={"batch_id": BATCH, "metrics": [METRIC_ACTUAL_SALES]})
            self.assertIn(post.status_code, (401, 403))
            anonymous.close()
        finally:
            main.app.dependency_overrides.update(overrides)


class StaticPageContractTests(unittest.TestCase):
    """Plain-browser page contract: no library, no technical wording."""

    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")

    def test_page_assets_exist_and_are_linked(self):
        self.assertTrue(HTML_PATH.exists())
        self.assertTrue(JS_PATH.exists())
        self.assertTrue(CSS_PATH.exists())
        self.assertIn("/static/js/sales-pivot.js", self.html)
        self.assertIn("/static/sales-pivot.css", self.html)

    def test_every_id_the_script_reads_exists_in_the_markup(self):
        referenced = set(re.findall(r"\$\('([a-zA-Z0-9_-]+)'\)", self.js))
        self.assertTrue(referenced)
        for element_id in sorted(referenced):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_single_execution_endpoint(self):
        self.assertIn("const API = '/api/v1/sales/actual/pivot'", self.js)
        self.assertRegex(self.js, r"apiFetch\([`']/fields")
        self.assertRegex(self.js, r"apiFetch\([`']/filter-options")
        self.assertRegex(self.js, r"apiFetch\('/query'")
        self.assertEqual(self.js.count("method: 'POST'"), 1)

    def test_page_uses_business_language_only(self):
        # Only user-visible text is checked: element ids / class names are not UI wording.
        visible = re.sub(r"<[^>]+>", " ", self.html).lower()
        for technical in ("dataset", "dimension", "granularity", "query mode", "sql"):
            with self.subTest(term=technical):
                self.assertNotIn(technical, visible)
        for label in ("分析字段", "分析维度", "指标", "筛选条件", "展示方式", "执行分析"):
            with self.subTest(label=label):
                self.assertIn(label, self.html)

    def test_table_columns_are_data_driven(self):
        self.assertIn("result.columns.map", self.js)
        self.assertIn("dimensions: state.picked", self.js)

    def test_bar_is_css_only_and_guarded(self):
        self.assertIn("sales-pivot-bar-fill", self.css)
        self.assertIn("width:${width}%", self.js)
        self.assertIn("横向柱状图暂只支持一个分析维度", self.html)
        lowered = (self.html + self.js + self.css).lower()
        for library in ("echarts", "chart.js", "chartjs", "highcharts", "plotly",
                        "d3.js", "d3.min", "apexcharts"):
            with self.subTest(library=library):
                self.assertNotIn(library, lowered)

    def test_frontend_never_builds_or_renders_sql(self):
        lowered = self.js.lower()
        for keyword in ("select *", "select count", "select sum", "order by ",
                        " from sales", " join ", "sqlalchemy", "sum("):
            with self.subTest(keyword=keyword):
                self.assertNotIn(keyword, lowered)

    def test_mdm_notice_is_present(self):
        self.assertIn("主档属性按当前本地 MDM 解释，不代表历史冻结口径", self.html)

    # ------------------------------------------------------------- CR02 page

    def test_cr02_controls_exist(self):
        self.assertIn('id="preset-buttons"', self.html)
        self.assertIn('id="preset-notice"', self.html)
        self.assertIn('id="grand-total-toggle"', self.html)
        self.assertIn('name="sales-pivot-time"', self.html)
        self.assertIn('id="time-start"', self.html)
        self.assertIn('id="time-end"', self.html)
        self.assertIn("data-subtotal", self.js)
        self.assertIn("applyPreset", self.js)
        self.assertIn("result.rows", self.js)

    def test_cr02_hierarchy_styles_are_plain_css(self):
        for selector in ("sales-pivot-row", "is-subtotal", "is-total", "is-group"):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.css)


class HierarchyTests(unittest.TestCase):
    """CR02 — Excel-pivot hierarchy, subtotals, grand total and business time."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)
        seed_master_data(cls.factory)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def _run(self, **body):
        payload = {"batch_id": BATCH, "metrics": [METRIC_ACTUAL_SALES], "limit": 500}
        payload.update(body)
        return run_query(self.factory, validate_query(payload))

    # -- hierarchy ---------------------------------------------------------

    def test_tabular_form_has_no_parent_row_with_a_metric(self):
        """CR02A §7: only leaves (and 小计/合计) carry a metric."""
        result = self._run(
            dimensions=["department", "market_type", "bp_channel_snapshot"],
            subtotals=["department"], grand_total=True)
        kinds = {row["kind"] for row in result["rows"]}
        self.assertEqual(kinds, {"leaf", "subtotal", "total"})
        self.assertNotIn("group", kinds)
        for row in result["rows"]:
            if row["kind"] == "leaf":
                self.assertEqual(len(row["path"]), 3)
                self.assertEqual(len(row["cells"]), 3)
            else:
                self.assertIn("label", row)

    def test_parent_values_are_printed_only_once_per_group(self):
        result = self._run(dimensions=["department", "market_type"])
        leaves = [row for row in result["rows"] if row["kind"] == "leaf"]
        self.assertTrue(leaves)
        # First leaf of every parent group carries the parent value, later ones blank.
        for index, row in enumerate(leaves):
            if index == 0 or leaves[index - 1]["path"][0] != row["path"][0]:
                self.assertEqual(row["cells"][0], row["path"][0])
            else:
                self.assertEqual(row["cells"][0], "")
            self.assertEqual(row["cells"][1], row["path"][1])   # deepest level always shown

    def test_subtotal_row_label_spans_the_dimension_columns(self):
        result = self._run(dimensions=["department", "market_type"],
                           subtotals=["department"])
        subtotals = [row for row in result["rows"] if row["kind"] == "subtotal"]
        self.assertEqual(len(subtotals), len({row["path"][0] for row in result["rows"]
                                              if row["kind"] == "leaf"}))
        for row in subtotals:
            self.assertTrue(row["label"].endswith("小计"))
            self.assertNotIn("cells", row)

    def test_each_parent_aggregate_is_rendered_exactly_once(self):
        """No duplicate parent metric: either a 小计 row or nothing — never both."""
        result = self._run(dimensions=["department", "market_type"],
                           subtotals=["department"])
        parents = {row["path"][0] for row in result["rows"] if row["kind"] == "leaf"}
        subtotal_labels = [row["label"] for row in result["rows"] if row["kind"] == "subtotal"]
        self.assertEqual(sorted(subtotal_labels), sorted(f"{value} 小计" for value in parents))
        self.assertFalse([row for row in result["rows"] if row["kind"] == "group"])

    def test_subtotal_only_for_selected_dimensions(self):
        with_sub = self._run(dimensions=["department", "market_type"],
                             subtotals=["department"])
        self.assertEqual(with_sub["totals"]["subtotal_fields"], ["department"])
        self.assertTrue(all(row["dimension"] == "department"
                            for row in with_sub["totals"]["subtotals"]))

        without = self._run(dimensions=["department", "market_type"], subtotals=[])
        self.assertEqual(without["totals"]["subtotals"], [])
        self.assertFalse([row for row in without["rows"] if row["kind"] == "subtotal"])

    def test_subtotal_value_equals_direct_parent_sum(self):
        result = self._run(dimensions=["department", "market_type"],
                           subtotals=["department"])
        subtotals = result["totals"]["subtotals"]
        self.assertTrue(subtotals)
        for entry in subtotals:
            with self.subTest(department=entry["value"]):
                master_value = None if entry["value"] == "未归属" else entry["value"]
                expected = direct_total(
                    self.factory, BATCH,
                    customer_column=Customer.__table__.c.department,
                    customer_value=master_value)
                self.assertEqual(Decimal(entry["metric"]), Decimal(expected))

    def test_subtotals_sum_to_grand_total(self):
        result = self._run(dimensions=["department", "market_type"],
                           subtotals=["department"], grand_total=True)
        total = sum((Decimal(entry["metric"]) for entry in result["totals"]["subtotals"]),
                    Decimal("0"))
        self.assertEqual(total, Decimal(result["totals"]["grand_total"]))
        self.assertEqual(Decimal(result["totals"]["grand_total"]),
                         Decimal(direct_total(self.factory, BATCH)))

    def test_grand_total_equals_direct_filtered_sum(self):
        result = self._run(
            dimensions=["market_type"], subtotals=[],
            filters=[{"field": "market_type", "operator": "in", "values": ["EC"]}])
        expected = direct_total(
            self.factory, BATCH,
            customer_column=Customer.__table__.c.market_type, customer_value="EC")
        self.assertEqual(Decimal(result["totals"]["grand_total"]), Decimal(expected))
        self.assertEqual(Decimal(result["totals"]["grand_total"]), Decimal("17"))

    def test_filter_recomputes_subtotals_and_total(self):
        for market in ("EC", "REAL"):
            with self.subTest(market=market):
                result = self._run(
                    dimensions=["department", "market_type"],
                    subtotals=["department"],
                    filters=[{"field": "market_type", "operator": "in", "values": [market]}])
                total = sum((Decimal(entry["metric"])
                             for entry in result["totals"]["subtotals"]), Decimal("0"))
                expected = direct_total(
                    self.factory, BATCH,
                    customer_column=Customer.__table__.c.market_type, customer_value=market)
                self.assertEqual(total, Decimal(expected))
                self.assertEqual(Decimal(result["totals"]["grand_total"]), Decimal(expected))

    def test_row_limit_never_corrupts_subtotals(self):
        full = self._run(dimensions=["department", "market_type"],
                         subtotals=["department"], limit=500)
        capped = self._run(dimensions=["department", "market_type"],
                           subtotals=["department"], limit=1)
        self.assertEqual(len(capped["items"]), 1)
        self.assertEqual(capped["totals"]["subtotals"], full["totals"]["subtotals"])
        self.assertEqual(capped["totals"]["grand_total"], full["totals"]["grand_total"])

    def test_grand_total_can_be_disabled(self):
        result = self._run(dimensions=["department"], grand_total=False)
        self.assertFalse([row for row in result["rows"] if row["kind"] == "total"])
        self.assertFalse(result["totals"]["grand_total_enabled"])

    # -- business time -----------------------------------------------------

    def test_mtd_uses_batch_month_and_max_business_date(self):
        result = self._run(dimensions=["market_type"], time={"mode": "mtd"})
        self.assertEqual(result["time"]["mode"], "mtd")
        self.assertEqual(result["time"]["start"], "2000-02-01")
        self.assertEqual(result["time"]["end"], "2000-02-15")
        self.assertIn("MTD（2000-02-01 ~ 2000-02-15）", result["time"]["label"])
        self.assertEqual(Decimal(result["totals"]["grand_total"]), Decimal("35"))

    def test_custom_range_changes_the_result_and_still_reconciles(self):
        inside = self._run(dimensions=["market_type"], time={
            "mode": "custom", "start": "2000-02-01", "end": "2000-02-28"})
        outside = self._run(dimensions=["market_type"], time={
            "mode": "custom", "start": "2000-03-01", "end": "2000-03-31"})
        self.assertEqual(Decimal(inside["totals"]["grand_total"]), Decimal("35"))
        self.assertEqual(Decimal(outside["totals"]["grand_total"]), Decimal("0"))
        expected = direct_total(
            self.factory, BATCH,
            extra=(SalesFact.__table__.c.sales_date >= "2000-02-01",
                   SalesFact.__table__.c.sales_date <= "2000-02-28"))
        self.assertEqual(Decimal(inside["totals"]["grand_total"]), Decimal(expected))

    # -- validation --------------------------------------------------------

    def test_invalid_time_and_subtotal_requests_are_rejected(self):
        bad_bodies = (
            {"time": {"mode": "yesterday"}},
            {"time": {"mode": "custom", "start": "2000-03-01", "end": "2000-02-01"}},
            {"subtotals": ["not_a_dimension"]},
            {"dimensions": ["department"], "subtotals": ["market_type"]},
            {"grand_total": "yes"},
        )
        for body in bad_bodies:
            with self.subTest(body=body):
                with self.assertRaises(PivotQueryError):
                    validate_query({"batch_id": BATCH, "dimensions": ["market_type"], **body})


class PresetTests(unittest.TestCase):
    """CR02 — presets are pre-filled configurations, not fixed pages."""

    @classmethod
    def setUpClass(cls):
        cls.metadata = get_catalog_metadata()
        cls.presets = {item["key"]: item for item in cls.metadata["presets"]}
        cls.fields = {field["key"]: field for field in cls.metadata["fields"]}

    def test_four_presets_are_exposed(self):
        self.assertEqual(set(self.presets),
                         {"jp_category_mtd", "demo_category_mtd",
                          "sales_org_mtd", "sales_org_mtd_real"})

    def test_blocked_preset_is_unavailable_and_has_no_dimensions(self):
        preset = self.presets["jp_category_mtd"]
        self.assertTrue(preset["blocked"])
        self.assertFalse(preset["available"])
        self.assertEqual(preset["config"]["dimensions"], [])
        self.assertIn("日本分类字段待正式 MDM 接入", preset["notice"])

    def test_preset_with_filters_carries_a_real_catalog_filter(self):
        preset = self.presets["sales_org_mtd_real"]
        self.assertTrue(preset["available"])
        filters = preset["config"]["filters"]
        self.assertEqual([item["field"] for item in filters], ["market_type"])
        self.assertEqual(filters[0]["values"], ["REAL"])

    def test_jp_category_preset_is_blocked_and_never_fakes_the_field(self):
        preset = self.presets["jp_category_mtd"]
        self.assertNotIn("jp_category", self.fields)
        for item in self.metadata["presets"]:
            self.assertNotIn("category_extra", item["config"]["dimensions"])

    def test_demo_category_preset_is_marked_demo_only(self):
        preset = self.presets["demo_category_mtd"]
        self.assertFalse(preset["blocked"])
        self.assertEqual(preset["config"]["dimensions"], ["category_l1"])
        label = preset["config"]["label_overrides"]["category_l1"]
        self.assertIn("非日本分类", label)
        self.assertIn("分类 MTD Demo", label)

    def test_sales_org_preset_matches_the_requested_structure(self):
        preset = self.presets["sales_org_mtd"]
        config = preset["config"]
        self.assertEqual(config["dimensions"],
                         ["department", "market_type", "bp_channel_snapshot"])
        self.assertEqual(config["subtotals"], ["department"])
        self.assertTrue(config["grand_total"])
        self.assertEqual(config["time"], {"mode": "mtd"})
        # CR02A §6: 营业组 is a confirmed mapping, no longer marked pending.
        self.assertEqual(config["label_overrides"]["bp_channel_snapshot"], "营业组")
        self.assertEqual(config["label_overrides"]["market_type"], "EC/REAL")
        self.assertNotIn("待接入", preset["label"])

    def test_business_mappings_record_the_confirmed_sales_group_path(self):
        mappings = {item["business_term"]: item
                    for item in self.metadata["business_mappings"]}
        self.assertEqual(mappings["部门"]["status"], "CONFIRMED")
        self.assertEqual(mappings["EC/REAL"]["logical_field"], "market_type")
        sales_group = mappings["营业组"]
        self.assertEqual(sales_group["status"], "CONFIRMED")
        self.assertEqual(sales_group["logical_field"], "bp_channel_snapshot")
        self.assertIn("mdm_channel.channel_name", sales_group["source_path"])
        self.assertIn("sales_fact.channel_name_snapshot", sales_group["target"])
        self.assertEqual(mappings["日本分类"]["status"], "PENDING_SCHEMA")

    def test_every_preset_field_exists_in_the_catalog(self):
        for preset in self.metadata["presets"]:
            for key in preset["config"]["dimensions"] + preset["config"]["subtotals"]:
                with self.subTest(preset=preset["key"], field=key):
                    self.assertIn(key, self.fields)
                    self.assertTrue(self.fields[key]["groupable"])
            for item in preset["config"]["filters"]:
                self.assertIn(item["field"], self.fields)

    def test_time_modes_are_exposed(self):
        self.assertEqual([item["key"] for item in self.metadata["time_modes"]],
                         ["all", "mtd", "custom"])


class ServedCr02Tests(unittest.TestCase):
    """CR02 over the real app."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)
        seed_master_data(cls.factory)
        cls._real_session_factory = pivot_api.get_session_factory
        pivot_api.get_session_factory = lambda: cls.factory
        main.app.dependency_overrides[pivot_api.pivot_viewer] = lambda: ALLOWED_USER
        main.app.dependency_overrides[pivot_api.pivot_page_viewer] = lambda: ALLOWED_USER
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        pivot_api.get_session_factory = cls._real_session_factory
        cls.engine.dispose()

    def _query(self, **body):
        payload = {"batch_id": BATCH, "metrics": [METRIC_ACTUAL_SALES], "limit": 500}
        payload.update(body)
        return self.client.post(f"{PIVOT_API}/query", json=payload)

    def test_served_hierarchy_with_subtotal_and_total(self):
        response = self._query(
            dimensions=["department", "market_type", "bp_channel_snapshot"],
            subtotals=["department"], grand_total=True, time={"mode": "mtd"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        kinds = {row["kind"] for row in body["rows"]}
        self.assertEqual(kinds, {"leaf", "subtotal", "total"})
        self.assertEqual(body["time"]["start"], "2000-02-01")
        self.assertIn("presets", self.client.get(f"{PIVOT_API}/fields").json())

    def test_served_invalid_subtotal_and_time_are_rejected(self):
        for body in ({"dimensions": ["market_type"], "subtotals": ["department"]},
                     {"dimensions": ["market_type"], "time": {"mode": "nope"}},
                     {"dimensions": ["market_type"], "sort": [{"field": "customer_code"}]}):
            with self.subTest(body=body):
                self.assertEqual(self._query(**body).status_code, 422)

    def test_result_too_large_maps_to_http_422(self):
        extra_engine, extra_factory = build_session_factory()
        seed_published_batch(extra_factory, batch_id="BOUND-501", rows=bound_rows(501))
        previous = pivot_api.get_session_factory
        pivot_api.get_session_factory = lambda: extra_factory
        try:
            response = self.client.post(f"{PIVOT_API}/query", json={
                "batch_id": "BOUND-501", "metrics": [METRIC_ACTUAL_SALES],
                "dimensions": ["customer_snapshot"]})
            self.assertEqual(response.status_code, 422, response.text)
            detail = response.json()["detail"]
            self.assertEqual(detail["code"], "PIVOT_RESULT_TOO_LARGE")
            self.assertIn("分析结果超过 500 个组合", detail["message"])
        finally:
            pivot_api.get_session_factory = previous
            extra_engine.dispose()


def bound_rows(count: int) -> tuple:
    """One fact per distinct customer → exactly ``count`` leaf combinations."""
    return tuple(
        ("SKU-DEMO-001", "Demo SKU 1", "PROD-DEMO-001", "Demo Product 1",
         "CHN-DEMO-001", "Demo Channel A", "REP-DEMO-001", "Demo Rep A",
         f"CUST-DEMO-{index:03d}", f"Demo Customer {index:03d}", 1)
        for index in range(1, count + 1)
    )


class Cr02aHardeningTests(unittest.TestCase):
    """CR02A — hierarchy identity, result bound, sort whitelist, preset filters."""

    def _factory_with_rows(self, rows, batch_id="CR02A-BATCH"):
        engine, factory = build_session_factory()
        seed_published_batch(factory, batch_id=batch_id, rows=rows)
        seed_master_data(factory)
        return engine, factory, batch_id

    def _run(self, factory, batch_id, **body):
        payload = {"batch_id": batch_id, "metrics": [METRIC_ACTUAL_SALES], "limit": 500}
        payload.update(body)
        return run_query(factory, validate_query(payload))

    # -- 1. composite hierarchy identity ----------------------------------

    SAME_KEY_ROWS = (
        ("SKU-DEMO-001", "Demo SKU 1", "PROD-DEMO-001", "Demo Product 1",
         "CHN-DEMO-001", "Demo Channel A", "REP-DEMO-001", "Demo Rep A",
         "CUST-DEMO-001", "Demo Customer A", 10),
        # Same sku_code → same surrogate sku_id, different historical name snapshot.
        ("SKU-DEMO-001", "Demo SKU 1 (旧)", "PROD-DEMO-001", "Demo Product 1",
         "CHN-DEMO-001", "Demo Channel A", "REP-DEMO-001", "Demo Rep A",
         "CUST-DEMO-001", "Demo Customer A", 5),
    )

    def test_same_key_different_label_is_two_historical_groups(self):
        engine, factory, batch_id = self._factory_with_rows(self.SAME_KEY_ROWS)
        try:
            result = self._run(factory, batch_id, dimensions=["sku_snapshot"],
                               subtotals=["sku_snapshot"], grand_total=True)
            leaves = [row for row in result["rows"] if row["kind"] == "leaf"]
            self.assertEqual(len(leaves), 2)
            self.assertEqual({row["path"][0] for row in leaves},
                             {"Demo SKU 1", "Demo SKU 1 (旧)"})
            # Same surrogate key, different labels → no collision.
            self.assertEqual({item["sku_snapshot__id"] for item in result["items"]}, {1011})

            subtotals = {row["label"]: Decimal(row["metric"])
                         for row in result["rows"] if row["kind"] == "subtotal"}
            self.assertEqual(subtotals, {"Demo SKU 1 小计": Decimal("10"),
                                         "Demo SKU 1 (旧) 小计": Decimal("5")})
            self.assertEqual(Decimal(result["totals"]["grand_total"]), Decimal("15"))
        finally:
            engine.dispose()

    def test_same_key_different_label_nests_without_collision(self):
        engine, factory, batch_id = self._factory_with_rows(self.SAME_KEY_ROWS)
        try:
            result = self._run(factory, batch_id,
                               dimensions=["sku_snapshot", "customer_current"],
                               subtotals=["sku_snapshot"], grand_total=True)
            leaves = [row for row in result["rows"] if row["kind"] == "leaf"]
            self.assertEqual([row["path"] for row in leaves],
                             [["Demo SKU 1", "Demo Customer A"],
                              ["Demo SKU 1 (旧)", "Demo Customer A"]])
            # The parent changed, so both levels are printed again (Excel-like).
            self.assertEqual([row["cells"] for row in leaves],
                             [["Demo SKU 1", "Demo Customer A"],
                              ["Demo SKU 1 (旧)", "Demo Customer A"]])
            total = sum((Decimal(row["metric"]) for row in result["rows"]
                         if row["kind"] == "subtotal"), Decimal("0"))
            self.assertEqual(total, Decimal(result["totals"]["grand_total"]))
            self.assertEqual(Decimal(result["totals"]["grand_total"]),
                             Decimal(direct_total(factory, batch_id)))
        finally:
            engine.dispose()

    # -- 2. result bound ---------------------------------------------------

    def test_result_bound_499_500_501(self):
        for count, accepted in ((499, True), (500, True), (501, False)):
            engine, factory, batch_id = self._factory_with_rows(
                bound_rows(count), batch_id=f"BOUND-{count}")
            try:
                with self.subTest(count=count):
                    if accepted:
                        result = self._run(factory, batch_id,
                                           dimensions=["customer_snapshot"])
                        leaves = [row for row in result["rows"] if row["kind"] == "leaf"]
                        self.assertEqual(len(leaves), count)
                        self.assertEqual(result["pagination"]["leaf_group_count"], count)
                    else:
                        with self.assertRaises(PivotQueryError) as ctx:
                            self._run(factory, batch_id, dimensions=["customer_snapshot"])
                        self.assertEqual(ctx.exception.code, "PIVOT_RESULT_TOO_LARGE")
                        self.assertIn("分析结果超过 500 个组合",
                                      ctx.exception.payload()["message"])
            finally:
                engine.dispose()

    def test_result_bound_never_returns_a_partial_pivot(self):
        engine, factory, batch_id = self._factory_with_rows(
            bound_rows(501), batch_id="BOUND-PARTIAL")
        try:
            with self.assertRaises(PivotQueryError):
                self._run(factory, batch_id, dimensions=["customer_snapshot"],
                          subtotals=["customer_snapshot"], grand_total=True, limit=1)
        finally:
            engine.dispose()

    # -- 3. sort whitelist -------------------------------------------------

    def test_sort_field_is_whitelisted(self):
        validate_query({"batch_id": BATCH, "dimensions": ["market_type"],
                        "sort": [{"field": METRIC_ACTUAL_SALES, "direction": "asc"}]})
        for field in ("customer_code", "department", "actual_qty", "1=1"):
            with self.subTest(field=field):
                with self.assertRaises(PivotQueryError):
                    validate_query({"batch_id": BATCH, "dimensions": ["market_type"],
                                    "sort": [{"field": field}]})

    # -- 4. preset filters reach the engine --------------------------------

    def test_preset_filters_reach_the_query_engine(self):
        from webapp.sales.pivot_catalog import PRESETS
        preset = next(item for item in PRESETS if item["key"] == "sales_org_mtd_real")
        config = preset["config"]
        engine, factory, batch_id = self._factory_with_rows(
            DEMO_ROWS + bound_rows(0), batch_id="PRESET-FILTER")
        try:
            unfiltered = self._run(factory, batch_id,
                                   dimensions=list(config["dimensions"]),
                                   subtotals=list(config["subtotals"]),
                                   grand_total=config["grand_total"],
                                   time=dict(config["time"]))
            filtered = self._run(factory, batch_id,
                                 dimensions=list(config["dimensions"]),
                                 subtotals=list(config["subtotals"]),
                                 grand_total=config["grand_total"],
                                 time=dict(config["time"]),
                                 filters=[dict(item) for item in config["filters"]])
            self.assertLess(Decimal(filtered["totals"]["grand_total"]),
                            Decimal(unfiltered["totals"]["grand_total"]))
            expected = direct_total(factory, batch_id,
                                    customer_column=Customer.__table__.c.market_type,
                                    customer_value="REAL")
            self.assertEqual(Decimal(filtered["totals"]["grand_total"]), Decimal(expected))
            self.assertTrue(filtered["totals"]["subtotals"])
        finally:
            engine.dispose()

    # -- 5. LEFT JOIN NULL fact is preserved -------------------------------

    def test_left_join_null_customer_fact_is_preserved(self):
        engine, factory, batch_id = self._factory_with_rows(DEMO_ROWS)
        try:
            result = self._run(factory, batch_id, dimensions=["customer_current"],
                               subtotals=["customer_current"], grand_total=True)
            rows = {row["path"][0]: row["metric"] for row in result["rows"]
                    if row["kind"] == "leaf"}
            self.assertIn("未归属", rows)                       # NULL customer fact row
            self.assertEqual(Decimal(rows["未归属"]), Decimal("3"))
            self.assertEqual(Decimal(result["totals"]["grand_total"]), Decimal("35"))
            self.assertEqual(Decimal(result["totals"]["grand_total"]),
                             Decimal(direct_total(factory, batch_id)))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
