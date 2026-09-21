"""Whitelist, guardrail and permission tests for the semantic layer spike.

Security posture under test:

* metric / dimension / filter whitelists (registry-resolved, never client-supplied)
* no SQL, expression, model attribute or physical column can enter through the body
* at most 3 group-by dimensions and at most ``max_result_rows`` returned
* the API requires the same ``MODULE_SALES_ACTUAL`` VIEW permission as production

The API tests run against a temporary SQLite database and override only the auth
and session dependencies, because the production app factory would otherwise open
the operator's real MySQL database.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Configure the isolated test environment before importing the application.
_TMP = tempfile.mkdtemp(prefix="sales-semantic-lab-")
os.environ.setdefault("MDM_DATABASE_URL", f"sqlite+pysqlite:///{_TMP}/mdm.db")
os.environ["CCGTOOLS_ACCOUNT_DB_PATH"] = f"{_TMP}/app.db"

# The account store must exist before the app composition runs its bootstrap.
from demo.initialize_accounts import initialize_accounts  # noqa: E402

initialize_accounts(os.environ["CCGTOOLS_ACCOUNT_DB_PATH"])

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import webapp.main as main  # noqa: E402
from webapp.mdm.models import Base as MdmBase  # noqa: E402
from webapp.sales import semantic_api  # noqa: E402
from webapp.sales.semantic_query_engine import (  # noqa: E402
    SemanticQueryError,
    validate_query,
)
from webapp.sales.semantic_registry import MAX_RESULT_ROWS  # noqa: E402
from webapp.sales.tests.fixtures import build_session_factory, seed_published_batch  # noqa: E402

BATCH = "DEMO-BATCH-0001"
ALLOWED_USER = {"id": 1, "username": "demo_admin", "role": "admin",
                "permissions": {"sales_actual": "VIEW"}}


class WhitelistTests(unittest.TestCase):
    """Nothing reaches SQL that the registry did not declare."""

    def base(self, **overrides):
        request = {"batch_id": BATCH, "metrics": ["actual_sales"], "dimensions": []}
        request.update(overrides)
        return request

    def test_metrics_must_be_whitelisted(self):
        with self.assertRaises(SemanticQueryError) as ctx:
            validate_query(self.base(metrics=["revenue"]))
        self.assertEqual(ctx.exception.code, "SEMANTIC_QUERY_INVALID")
        self.assertEqual(ctx.exception.details["metric"], "revenue")

    def test_at_least_one_metric_is_required(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(metrics=[]))

    def test_dimensions_must_be_whitelisted(self):
        for key in ("profit", "category_l1", "category_l2", "sales_fact.actual_qty"):
            with self.subTest(key=key):
                with self.assertRaises(SemanticQueryError) as ctx:
                    validate_query(self.base(dimensions=[key]))
                self.assertEqual(ctx.exception.details["dimension"], key)

    def test_filter_dimension_must_be_whitelisted(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(filters=[
                {"dimension": "category_l3", "operator": "eq", "value": 1}
            ]))

    def test_filter_operator_must_be_whitelisted(self):
        with self.assertRaises(SemanticQueryError) as ctx:
            validate_query(self.base(filters=[
                {"dimension": "product", "operator": "like", "value": "%"}
            ]))
        self.assertEqual(ctx.exception.details["operator"], "like")

    def test_operator_must_be_valid_for_the_dimension_type(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(filters=[
                {"dimension": "sales_date", "operator": "in", "values": ["2000-02-01"]}
            ]))

    def test_sql_fragments_are_rejected_as_metric_names(self):
        for payload in (
            "actual_qty) FROM sales_fact --",
            "actual_qty; DROP TABLE sales_fact",
            "(SELECT 1)",
            "SUM(actual_qty)",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(SemanticQueryError):
                    validate_query(self.base(metrics=[payload]))

    def test_unknown_request_fields_are_rejected(self):
        for field in ("sql", "raw_sql", "expression", "expression_sql", "where",
                      "group_by", "having", "table"):
            with self.subTest(field=field):
                with self.assertRaises(SemanticQueryError) as ctx:
                    validate_query(self.base(**{field: "1=1"}))
                self.assertIn("unsupported request fields", ctx.exception.message)

    def test_filter_entries_reject_unknown_fields(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(filters=[
                {"dimension": "product", "operator": "eq", "value": 1, "column": "product_id"}
            ]))

    def test_sort_field_must_be_a_selected_metric_or_dimension(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(sort=[{"field": "created_at", "direction": "desc"}]))
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(dimensions=["product"],
                                     sort=[{"field": "channel", "direction": "desc"}]))

    def test_sort_direction_is_validated(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(sort=[{"field": "actual_sales", "direction": "sideways"}]))

    def test_dataset_must_be_whitelisted(self):
        with self.assertRaises(SemanticQueryError):
            validate_query(self.base(dataset="information_schema"))

    def test_batch_id_is_required(self):
        with self.assertRaises(SemanticQueryError):
            validate_query({"metrics": ["actual_sales"], "dimensions": []})


class GuardrailTests(unittest.TestCase):
    def test_group_by_is_capped_at_three_dimensions(self):
        with self.assertRaises(SemanticQueryError) as ctx:
            validate_query({
                "batch_id": BATCH, "metrics": ["actual_sales"],
                "dimensions": ["channel", "salesrep", "product", "sku"],
            })
        self.assertEqual(ctx.exception.details["max_group_by"], 3)

    def test_limit_is_capped_at_max_result_rows(self):
        with self.assertRaises(SemanticQueryError) as ctx:
            validate_query({"batch_id": BATCH, "metrics": ["actual_sales"],
                            "dimensions": [], "limit": MAX_RESULT_ROWS + 1})
        self.assertEqual(ctx.exception.details["max_rows"], MAX_RESULT_ROWS)

    def test_limit_rejects_non_positive_and_non_integer_values(self):
        for limit in (0, -1, True, "50", 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(SemanticQueryError):
                    validate_query({"batch_id": BATCH, "metrics": ["actual_sales"],
                                    "dimensions": [], "limit": limit})

    def test_offset_must_be_non_negative(self):
        with self.assertRaises(SemanticQueryError):
            validate_query({"batch_id": BATCH, "metrics": ["actual_sales"],
                            "dimensions": [], "offset": -5})

    def test_duplicate_dimensions_are_deduplicated_not_rejected(self):
        query = validate_query({"batch_id": BATCH, "metrics": ["actual_sales"],
                                "dimensions": ["product", "product"]})
        self.assertEqual([d.key for d in query.dimensions], ["product"])


class SemanticApiSecurityTests(unittest.TestCase):
    """Endpoint-level checks using overridden dependencies only."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = build_session_factory()
        seed_published_batch(cls.factory, batch_id=BATCH)
        main.app.dependency_overrides[semantic_api.sales_semantic_viewer] = lambda: ALLOWED_USER
        main.app.dependency_overrides[semantic_api.sales_semantic_page_viewer] = lambda: ALLOWED_USER
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        cls.engine.dispose()

    def test_metadata_endpoint_exposes_metrics_and_dimensions(self):
        response = self.client.get("/api/v1/lab/sales/semantic")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["dataset"], "sales_actual")
        self.assertEqual([m["key"] for m in body["metrics"]], ["actual_sales"])
        self.assertEqual(
            {d["key"] for d in body["dimensions"]},
            {"product", "sku", "channel", "salesrep", "customer", "sales_date", "snapshot_month"},
        )
        self.assertEqual([p["key"] for p in body["presets"]],
                         ["product", "channel_product", "salesrep_product", "product_sku"])

    def test_metadata_endpoint_never_returns_sql_or_columns(self):
        raw = self.client.get("/api/v1/lab/sales/semantic").text
        lowered = raw.lower()
        for forbidden in ("select ", "select\"", "sqlalchemy", "column(", " join "):
            self.assertNotIn(forbidden, lowered, forbidden)
        for column in ("product_id", "sku_id", "channel_id_snapshot", "actual_qty)"):
            self.assertNotIn(column, raw, column)

    def test_query_endpoint_rejects_injection_style_body(self):
        response = self.client.post("/api/v1/lab/sales/query", json={
            "batch_id": BATCH,
            "metrics": ["actual_sales"],
            "dimensions": ["1=1"],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "SEMANTIC_QUERY_INVALID")

    def test_query_endpoint_rejects_extra_fields(self):
        response = self.client.post("/api/v1/lab/sales/query", json={
            "batch_id": BATCH, "metrics": ["actual_sales"], "dimensions": [],
            "raw_sql": "SELECT 1",
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_query_endpoint_rejects_group_by_overflow(self):
        response = self.client.post("/api/v1/lab/sales/query", json={
            "batch_id": BATCH, "metrics": ["actual_sales"],
            "dimensions": ["channel", "salesrep", "product", "sku"],
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_query_endpoint_returns_not_found_for_unknown_batch(self):
        # The production batch guard raises before touching any Fact row.
        from webapp.sales.dashboard_query_service import DashboardBatchNotFound
        with self.assertRaises(DashboardBatchNotFound):
            from webapp.sales.semantic_query_engine import run_query
            from webapp.sales.semantic_query_engine import validate_query as vq
            run_query(self.factory, vq({"batch_id": "UNKNOWN", "metrics": ["actual_sales"],
                                        "dimensions": []}))

    def test_guard_maps_semantic_errors_to_422(self):
        from webapp.sales.semantic_api import _guard
        with self.assertRaises(HTTPException) as ctx:
            _guard(validate_query, {"batch_id": BATCH, "metrics": ["nope"], "dimensions": []})
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["code"], "SEMANTIC_QUERY_INVALID")

    def test_lab_page_route_is_registered(self):
        paths = {route.path for route in main.app.routes}
        self.assertIn("/lab/sales-semantic-layer", paths)
        self.assertIn("/api/v1/lab/sales/semantic", paths)
        self.assertIn("/api/v1/lab/sales/query", paths)
        self.assertIn("/api/v1/lab/sales/filter-options", paths)


class PermissionWiringTests(unittest.TestCase):
    """The lab must reuse the production Sales Actual VIEW permission.

    ``require_permission`` / ``require_page_permission`` return opaque closures, so
    the gate is pinned two ways: the declaration in the lab module, and the real
    HTTP behaviour of an unauthenticated caller.
    """

    def test_lab_declares_the_sales_actual_view_and_page_gates(self):
        source = Path(semantic_api.__file__).read_text(encoding="utf-8")
        self.assertIn("require_permission(MODULE_SALES_ACTUAL, VIEW, current_user)", source)
        self.assertIn("require_page_permission(MODULE_SALES_ACTUAL, VIEW)", source)

    def test_lab_declares_no_edit_or_other_module_permission(self):
        source = Path(semantic_api.__file__).read_text(encoding="utf-8")
        self.assertNotIn("MODULE_SALES_ACTUAL, EDIT", source)
        self.assertNotIn("MODULE_MDM", source)
        self.assertNotIn("MODULE_ORDERING", source)

    def test_every_lab_endpoint_is_gated(self):
        from fastapi.routing import APIRoute

        lab_routes = [
            route for route in main.app.routes
            if isinstance(route, APIRoute)
            and (route.path.startswith("/api/v1/lab/sales")
                 or route.path == "/lab/sales-semantic-layer")
        ]
        self.assertGreaterEqual(len(lab_routes), 4)
        for route in lab_routes:
            with self.subTest(route=route.path):
                self.assertTrue(
                    route.dependant.dependencies,
                    f"{route.path} declares no security dependency",
                )

    def test_unauthenticated_calls_are_rejected(self):
        """No cookie => the real sales_actual VIEW gate answers 401."""
        client = TestClient(main.app, raise_server_exceptions=False)
        try:
            self.assertEqual(client.get("/api/v1/lab/sales/semantic").status_code, 401)
            self.assertEqual(client.post("/api/v1/lab/sales/query", json={
                "batch_id": BATCH, "metrics": ["actual_sales"], "dimensions": [],
            }).status_code, 401)
            self.assertEqual(client.get("/api/v1/lab/sales/filter-options",
                                       params={"batch_id": BATCH}).status_code, 401)
            self.assertEqual(client.get("/lab/sales-semantic-layer").status_code, 401)
        finally:
            client.close()

    def test_lab_module_defines_no_write_endpoints(self):
        source = Path(semantic_api.__file__).read_text(encoding="utf-8")
        self.assertIn('@router.post("/query")', source)
        self.assertIn('@router.get("/semantic")', source)
        for mutation in ("@router.put", "@router.patch", "@router.delete"):
            self.assertNotIn(mutation, source)

    def test_single_execution_endpoint_exists(self):
        """All four presets must funnel through one POST endpoint."""
        from fastapi.routing import APIRoute

        posts = [
            route.path for route in main.app.routes
            if isinstance(route, APIRoute) and "POST" in route.methods
            and "/api/v1/lab/sales/" in route.path
        ]
        self.assertEqual(posts, ["/api/v1/lab/sales/query"])


if __name__ == "__main__":
    unittest.main()
