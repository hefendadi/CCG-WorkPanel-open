"""Semantic registry tests: shape, guardrails and leak-proof metadata.

No database is needed here: these tests pin the contract the engine and the
Explorer both depend on.
"""
from __future__ import annotations

import json
import unittest

from webapp.sales import semantic_registry as registry
from webapp.sales.semantic_registry import (
    DATASET_SALES_ACTUAL,
    DIMENSIONS,
    METRICS,
    SALES_ACTUAL_DATASET,
    DimensionSpec,
    MetricSpec,
    get_dataset_metadata,
    get_dimension_metadata,
    get_metric_metadata,
    get_preset_metadata,
)


class MetricRegistryTests(unittest.TestCase):
    def test_first_version_exposes_exactly_the_sales_quantity_metric(self):
        self.assertEqual([metric.key for metric in METRICS], ["actual_sales"])
        metric = SALES_ACTUAL_DATASET.metric("actual_sales")
        # TASK-ASV2-001 (AC-ASV2-010) localises the shared metric label to the
        # Chinese business UI; the lab spike previously asserted "Sales Qty".
        self.assertEqual(metric.label, "实际销量")
        self.assertEqual(metric.aggregation, registry.AGG_SUM)
        self.assertEqual(metric.column, "actual_qty")
        self.assertEqual(metric.unit, "Pcs")

    def test_metric_metadata_exposes_required_fields(self):
        metadata = get_metric_metadata(DATASET_SALES_ACTUAL)["actual_sales"]
        for field in ("key", "label", "description", "format"):
            self.assertIn(field, metadata, field)
        self.assertEqual(metadata["format"], "decimal")

    def test_public_metadata_never_leaks_sql_or_physical_columns(self):
        metadata = get_dataset_metadata(DATASET_SALES_ACTUAL)
        payload = json.dumps(metadata).lower()
        # No SQL text, no SQLAlchemy objects, no physical column names.
        for forbidden in (
            "select ", "from sales", "sqlalchemy", "column(", "join ",
            "_snapshot\"", "_snapshot'", "actual_qty)", "sum(",
        ):
            self.assertNotIn(forbidden, payload, forbidden)
        raw = json.dumps(metadata)
        for column in ("product_id", "sku_id", "customer_id", "channel_id", "salesrep_id"):
            self.assertNotIn(column, raw, column)
        # The only metric-level expression information is an abstract aggregation.
        self.assertEqual(metadata["metrics"][0]["aggregation"], "sum")

    def test_unknown_aggregation_is_rejected_at_definition_time(self):
        with self.assertRaises(ValueError):
            MetricSpec(
                key="bad", label="Bad", description="", aggregation="median",
                column="actual_qty",
            )


class DimensionRegistryTests(unittest.TestCase):
    def test_required_first_version_dimensions_exist(self):
        keys = {dimension.key for dimension in DIMENSIONS}
        self.assertEqual(
            keys,
            {"product", "sku", "channel", "salesrep", "customer", "sales_date", "snapshot_month"},
        )

    def test_dimension_metadata_exposes_required_fields(self):
        metadata = get_dimension_metadata(DATASET_SALES_ACTUAL)
        for key, item in metadata.items():
            for field in ("key", "label", "type", "filterable", "sortable", "groupable"):
                self.assertIn(field, item, f"{key}.{field}")

    def test_master_reference_dimensions_are_nullable_and_carry_unassigned_label(self):
        for key in ("channel", "salesrep", "customer"):
            dimension = SALES_ACTUAL_DATASET.dimension(key)
            self.assertTrue(dimension.nullable, key)
            self.assertIn(registry.UNASSIGNED_LABEL, dimension.description)

    def test_dimensions_only_use_snapshot_or_fact_columns(self):
        """No dimension may read an MDM-owned attribute directly."""
        for dimension in DIMENSIONS:
            for name in (
                dimension.key_column, dimension.label_column, dimension.code_column,
                dimension.id_column,
            ):
                if name is None:
                    continue
                self.assertTrue(
                    name.endswith("_snapshot") or name in {"actual_qty", "product_id", "sku_id",
                                                           "customer_id", "sales_date",
                                                           "snapshot_month"},
                    f"{dimension.key} references non-snapshot column {name}",
                )
            self.assertNotIn("category", json.dumps(dimension.to_public_dict()).lower())

    def test_operator_set_is_derived_from_type(self):
        product = SALES_ACTUAL_DATASET.dimension("product")
        self.assertEqual(set(product.operators), {"eq", "in", "gte", "lte", "between"})
        sales_date = SALES_ACTUAL_DATASET.dimension("sales_date")
        self.assertIn("between", sales_date.operators)
        self.assertNotIn("in", sales_date.operators)

    def test_definition_time_validation_rejects_bad_specs(self):
        with self.assertRaises(ValueError):
            DimensionSpec(key="x", label="X", description="", type="json",
                          role=registry.ROLE_REFERENCE,
                          group_strategy=registry.GROUP_SNAPSHOT_ID, id_column="product_id")
        with self.assertRaises(ValueError):
            DimensionSpec(key="x", label="X", description="", type=registry.TYPE_STRING,
                          role=registry.ROLE_REFERENCE,
                          group_strategy=registry.GROUP_SNAPSHOT_ID, id_column="product_id",
                          operators=("regex",))
        with self.assertRaises(ValueError):
            DimensionSpec(key="x", label="X", description="", type=registry.TYPE_STRING,
                          role=registry.ROLE_REFERENCE,
                          group_strategy=registry.GROUP_SNAPSHOT_ID, id_column="product_id",
                          operators=("between",))


class DatasetGuardrailTests(unittest.TestCase):
    def test_dataset_enforces_group_by_and_row_caps(self):
        self.assertEqual(SALES_ACTUAL_DATASET.max_group_by, 3)
        self.assertEqual(SALES_ACTUAL_DATASET.max_rows, registry.MAX_RESULT_ROWS)
        self.assertLessEqual(SALES_ACTUAL_DATASET.default_row_limit, SALES_ACTUAL_DATASET.max_rows)

    def test_public_metadata_declares_guardrails(self):
        guardrails = get_dataset_metadata(DATASET_SALES_ACTUAL)["guardrails"]
        self.assertEqual(guardrails["max_group_by_dimensions"], 3)
        self.assertEqual(guardrails["max_result_rows"], registry.MAX_RESULT_ROWS)

    def test_duplicate_keys_are_rejected(self):
        metric = METRICS[0]
        with self.assertRaises(ValueError):
            registry.DatasetSpec(
                key="dup", label="Dup", description="", metrics=(metric, metric),
                dimensions=(DIMENSIONS[0],),
            )


class PresetTests(unittest.TestCase):
    def test_four_presets_cover_the_existing_dashboard_views(self):
        presets = {preset["key"]: preset for preset in get_preset_metadata()}
        self.assertEqual(
            set(presets),
            {"product", "channel_product", "salesrep_product", "product_sku"},
        )
        self.assertEqual(presets["product"]["dimensions"], ["product"])
        self.assertEqual(presets["channel_product"]["dimensions"], ["channel", "product"])
        self.assertEqual(presets["salesrep_product"]["dimensions"], ["salesrep", "product"])
        self.assertEqual(presets["product_sku"]["dimensions"], ["product", "sku"])

    def test_every_preset_targets_one_registered_metric(self):
        for preset in registry.PRESETS:
            self.assertIsNotNone(SALES_ACTUAL_DATASET.metric(preset.metric), preset.key)
            for key in preset.dimensions:
                self.assertIsNotNone(SALES_ACTUAL_DATASET.dimension(key), preset.key)
            self.assertLessEqual(len(preset.dimensions), SALES_ACTUAL_DATASET.max_group_by)


class FutureDimensionTests(unittest.TestCase):
    def test_category_dimensions_are_declared_but_not_queryable(self):
        future = {item["key"] for item in registry.get_future_dimension_metadata()}
        self.assertEqual(future, {"category_l1", "category_l2", "category_l3", "category_l4"})
        for key in future:
            self.assertIsNone(SALES_ACTUAL_DATASET.dimension(key))
        for item in registry.get_future_dimension_metadata():
            self.assertEqual(item["status"], "REQUIRES_SNAPSHOT_POLICY_DECISION")


if __name__ == "__main__":
    unittest.main()
