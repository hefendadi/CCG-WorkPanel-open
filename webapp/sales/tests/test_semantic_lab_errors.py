"""Regression guards for the live "HTTP 422" seen in the ACT Sales Semantic Lab.

Root cause, recorded here so it cannot silently return:

1. The lab page resolves its ``batch_id`` from
   ``GET /api/v1/sales/actual/dashboard/context``.  The local spike app runs
   against the isolated MySQL *test* container, whose ``/var/lib/mysql`` is a
   tmpfs; a container restart wipes the synthetic demo batch, the context call
   returns ``current_batch: null``, and the page then POSTed ``batch_id: null``.
2. ``POST /api/v1/lab/sales/query`` declares ``batch_id: str``, so FastAPI
   answered with a **list-shaped** validation detail.  The old ``apiFetch`` only
   read ``detail.message`` / ``detail.code`` — neither of which a list has — so
   the page degraded to a bare ``HTTP 422`` with no field and no reason.

The API contract itself is intentionally unchanged; only the Lab UI got better
at explaining it.  These tests pin both halves: the error *shapes* the API
returns, and the frontend switches that render them.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="sales-semantic-lab-errors-")
os.environ.setdefault("MDM_DATABASE_URL", f"sqlite+pysqlite:///{_TMP}/mdm.db")
os.environ.setdefault("CCGTOOLS_ACCOUNT_DB_PATH", f"{_TMP}/app.db")

from demo.initialize_accounts import initialize_accounts  # noqa: E402

initialize_accounts(os.environ["CCGTOOLS_ACCOUNT_DB_PATH"])

from fastapi.testclient import TestClient  # noqa: E402

import webapp.main as main  # noqa: E402
from webapp.sales import semantic_api  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
HTML_PATH = ROOT / "webapp" / "static" / "sales-semantic-demo.html"
JS_PATH = ROOT / "webapp" / "static" / "js" / "sales-semantic-demo.js"

ALLOWED_USER = {"id": 1, "username": "demo_admin", "role": "admin",
                "permissions": {"sales_actual": "VIEW"}}

BASE_BODY = {
    "dataset": "sales_actual",
    "batch_id": "DEMO-BATCH-0001",
    "metrics": ["actual_sales"],
    "dimensions": [],
    "filters": [],
    "sort": [{"field": "actual_sales", "direction": "desc"}],
    "limit": 100,
}


class LabQueryValidationContractTests(unittest.TestCase):
    """The two 422 shapes the Lab UI has to explain, pinned as contracts."""

    @classmethod
    def setUpClass(cls):
        main.app.dependency_overrides[semantic_api.sales_semantic_viewer] = (
            lambda: ALLOWED_USER
        )
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()

    def _post(self, **overrides):
        body = dict(BASE_BODY)
        body.update(overrides)
        return self.client.post("/api/v1/lab/sales/query", json=body)

    def test_null_batch_id_reproduces_the_live_422(self):
        """The exact payload the page sent when no Published batch existed."""
        response = self._post(batch_id=None)
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, list, "FastAPI validation detail is a list")
        self.assertEqual(detail[0]["loc"], ["body", "batch_id"])
        self.assertEqual(detail[0]["type"], "string_type")
        self.assertIn("string", detail[0]["msg"].lower())

    def test_missing_batch_id_is_a_validation_error_too(self):
        body = dict(BASE_BODY)
        del body["batch_id"]
        response = self.client.post("/api/v1/lab/sales/query", json=body)
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, list)
        self.assertEqual(detail[0]["loc"], ["body", "batch_id"])

    def test_unknown_dimension_uses_the_stable_semantic_error_shape(self):
        """The engine's own contract, unchanged: a dict with code/message/details."""
        response = self._post(dimensions=["category_l1"])
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["code"], "SEMANTIC_QUERY_INVALID")
        self.assertIn("category_l1", detail["message"])
        self.assertEqual(detail["details"]["dimension"], "category_l1")


class LabErrorUIRegressionTests(unittest.TestCase):
    """Static contract for the Lab-only error reporting upgrade."""

    @classmethod
    def setUpClass(cls):
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_script_reads_fastapi_list_shaped_validation_detail(self):
        self.assertIn("describeHttpError", self.js)
        self.assertIn("Array.isArray(detail)", self.js)
        # detail, field, reason — the three things the bare status line hid.
        self.assertIn("field:", self.js)
        self.assertIn("reason:", self.js)
        self.assertIn("rule:", self.js)

    def test_script_no_longer_degrades_to_a_bare_status_line(self):
        # The old fallback only fired when detail.message/detail.code existed.
        self.assertNotIn("detail.message || detail.code", self.js)
        self.assertNotIn("`HTTP ${response.status}`", self.js)

    def test_script_refuses_to_post_a_null_batch_id(self):
        self.assertIn("if (!state.batchId)", self.js)
        self.assertIn("state.batchId = batch.batch_id;", self.js)

    def test_all_option_is_never_sent_as_a_filter_value(self):
        # '' is the "(all)" option value; the guard also covers the literal
        # spellings a stale preset/state could leave behind.
        self.assertIn("raw === 'all'", self.js)
        self.assertIn("raw === '(all)'", self.js)
        self.assertIn('value="">(all)</option>', self.js)

    def test_error_box_preserves_line_breaks(self):
        self.assertIn("white-space: pre-line", self.html)


if __name__ == "__main__":
    unittest.main()
