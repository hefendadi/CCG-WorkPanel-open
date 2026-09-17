"""Read-only verification of the public Ordering schema contract."""

from __future__ import annotations

import os

from sqlalchemy import UniqueConstraint, create_engine, inspect, text

from webapp.mdm.database import get_database_url
from webapp.mdm.models import Base
from webapp.ordering.models import ORDERING_TABLE_NAMES


EXPECTED_HEAD = "0001_public_baseline"
EXPECTED_TRIGGER_DEFINER = os.environ.get("CCGTOOLS_TRIGGER_DEFINER", "demo_migrate@%")


def expected_trigger_names() -> frozenset[str]:
    names: set[str] = set()

    for label in (
        "ord_actual_raw",
        "ord_inventory_raw",
        "ord_incoming_line",
        "ord_final_revision",
        "ord_lot",
        "ord_lot_source",
        "ord_incoming_snapshot",
    ):
        names.update(
            {f"trg_{label}_bu_immutable", f"trg_{label}_bd_immutable"}
        )

    names.update(
        {
            "trg_ord_cycle_bu_lock",
            "trg_ord_cycle_bd_lock",
            "trg_ord_binding_bi_validate",
            "trg_ord_binding_bu_validate",
            "trg_ord_binding_bd_validate",
            "trg_ord_final_order_bi_validate",
            "trg_ord_final_order_bu_validate",
            "trg_ord_final_order_bd_validate",
            "trg_ord_lot_source_bi_validate",
        }
    )

    three_event_labels = (
        "p6_product_snap",
        "p6_channel_snap",
        "p6_forecast",
        "p6_binding",
        "p6_final",
        "p6_forecast_line",
        "p6_revision",
        "p6_actual_raw",
        "p6_actual_cp",
        "p6_actual_pp",
        "p6_inventory_raw",
        "p6_inventory_sku",
        "p6_inventory_product",
        "p6_incoming_line",
    )
    for label in three_event_labels:
        for event in ("insert", "update", "delete"):
            names.add(f"trg_ord_{label}_{event}")

    names.update(
        {
            "trg_ord_p6_dataset_update",
            "trg_ord_p6_dataset_delete",
            "trg_ord_p6_cycle_update",
        }
    )
    return frozenset(names)


EXPECTED_TRIGGERS = expected_trigger_names()


def expected_contract():
    foreign_keys = set()
    indexes = set()
    unique_constraints = set()
    columns = {}

    for table_name in ORDERING_TABLE_NAMES:
        table = Base.metadata.tables[table_name]
        columns[table_name] = frozenset(column.name for column in table.columns)
        for constraint in table.foreign_key_constraints:
            foreign_keys.add(
                (
                    table_name,
                    constraint.name,
                    tuple(column.name for column in constraint.columns),
                    constraint.referred_table.name,
                    tuple(element.column.name for element in constraint.elements),
                )
            )
        for index in table.indexes:
            indexes.add(
                (
                    table_name,
                    index.name,
                    tuple(column.name for column in index.columns),
                    bool(index.unique),
                )
            )
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                unique_constraints.add(
                    (
                        table_name,
                        constraint.name,
                        tuple(column.name for column in constraint.columns),
                    )
                )

    return columns, foreign_keys, indexes, unique_constraints


def verify() -> dict[str, int | str]:
    engine = create_engine(get_database_url(), pool_pre_ping=True, future=True)
    try:
        inspector = inspect(engine)
        actual_tables = {
            name for name in inspector.get_table_names() if name.startswith("ordering_")
        }
        expected_tables = set(ORDERING_TABLE_NAMES)
        if actual_tables != expected_tables:
            raise RuntimeError(
                "Ordering table mismatch: "
                f"missing={sorted(expected_tables - actual_tables)} "
                f"extra={sorted(actual_tables - expected_tables)}"
            )

        expected_columns, expected_fks, expected_indexes, expected_uniques = (
            expected_contract()
        )
        actual_fks = set()
        actual_indexes = set()
        actual_uniques = set()
        for table_name in sorted(expected_tables):
            actual_columns = frozenset(
                column["name"] for column in inspector.get_columns(table_name)
            )
            if actual_columns != expected_columns[table_name]:
                raise RuntimeError(f"Ordering column mismatch: {table_name}")

            for constraint in inspector.get_foreign_keys(table_name):
                actual_fks.add(
                    (
                        table_name,
                        constraint["name"],
                        tuple(constraint["constrained_columns"]),
                        constraint["referred_table"],
                        tuple(constraint["referred_columns"]),
                    )
                )
            for index in inspector.get_indexes(table_name):
                actual_indexes.add(
                    (
                        table_name,
                        index["name"],
                        tuple(index["column_names"]),
                        bool(index["unique"]),
                    )
                )
            for constraint in inspector.get_unique_constraints(table_name):
                actual_uniques.add(
                    (
                        table_name,
                        constraint["name"],
                        tuple(constraint["column_names"]),
                    )
                )

        if actual_fks != expected_fks:
            raise RuntimeError(
                "Ordering foreign-key mismatch: "
                f"missing={sorted(expected_fks - actual_fks)} "
                f"extra={sorted(actual_fks - expected_fks)}"
            )
        missing_indexes = expected_indexes - actual_indexes
        if missing_indexes:
            raise RuntimeError(f"Missing Ordering indexes: {sorted(missing_indexes)}")
        if actual_uniques != expected_uniques:
            raise RuntimeError(
                "Ordering unique-constraint mismatch: "
                f"missing={sorted(expected_uniques - actual_uniques)} "
                f"extra={sorted(actual_uniques - expected_uniques)}"
            )

        with engine.connect() as connection:
            revisions = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
            if revisions != [EXPECTED_HEAD]:
                raise RuntimeError(f"Unexpected Alembic revisions: {revisions}")
            trigger_rows = connection.execute(
                text(
                    "SELECT trigger_name, definer FROM information_schema.triggers "
                    "WHERE trigger_schema=DATABASE() AND trigger_name LIKE 'trg\\_ord\\_%' "
                    "ORDER BY trigger_name"
                )
            ).all()

        actual_triggers = {name for name, _definer in trigger_rows}
        if actual_triggers != EXPECTED_TRIGGERS:
            raise RuntimeError(
                "Ordering trigger mismatch: "
                f"missing={sorted(EXPECTED_TRIGGERS - actual_triggers)} "
                f"extra={sorted(actual_triggers - EXPECTED_TRIGGERS)}"
            )
        definers = {definer for _name, definer in trigger_rows}
        if definers != {EXPECTED_TRIGGER_DEFINER}:
            raise RuntimeError(f"Unexpected Ordering trigger definers: {sorted(definers)}")

        return {
            "head": EXPECTED_HEAD,
            "tables": len(expected_tables),
            "foreign_keys": len(expected_fks),
            "indexes": len(expected_indexes),
            "unique_constraints": len(expected_uniques),
            "triggers": len(EXPECTED_TRIGGERS),
        }
    finally:
        engine.dispose()


def main() -> None:
    result = verify()
    for key in (
        "head",
        "tables",
        "foreign_keys",
        "indexes",
        "unique_constraints",
        "triggers",
    ):
        print(f"{key.upper()}={result[key]}")
    print("ORDERING_SCHEMA_VERIFICATION=PASS")


if __name__ == "__main__":
    main()
