"""SQLAlchemy models for the revised Ordering V1 schema foundation."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from webapp.mdm.models import Base, bigint_type, datetime6_type, enum_type, mysql_table_args


class DatasetType(str, enum.Enum):
    ACTUAL = "ACTUAL"
    INVENTORY = "INVENTORY"


class CycleStatus(str, enum.Enum):
    OPEN = "OPEN"
    LOCKED = "LOCKED"


class FinalOrderStatus(str, enum.Enum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"


class LotLifecycleState(str, enum.Enum):
    PRE_EXISTING = "PRE_EXISTING"
    ACTIVE = "ACTIVE"
    SOLD_OUT = "SOLD_OUT"


class ReturnClassification(str, enum.Enum):
    MINOR_RETURN = "MINOR_RETURN"
    MATERIAL_RETURN = "MATERIAL_RETURN"


# Warehouse availability is explicit and versioned; no company ownership rule.
PLANNING_AVAILABLE_SCOPE = "PLANNING_AVAILABLE"
WAREHOUSE_PLANNING_SCOPE = PLANNING_AVAILABLE_SCOPE
CHECKSUM_CHECK = (
    "{column} IS NULL OR "
    "(length({column}) = 64 AND {column} REGEXP '^[0-9a-f]{{64}}$')"
)


def restricted_fk(target: str, name: str):
    return ForeignKey(target, name=name, ondelete="RESTRICT", onupdate="RESTRICT")


def created_at_column():
    return mapped_column(datetime6_type(), nullable=False, server_default=func.now())


def quantity_check(column: str, name: str):
    return CheckConstraint(f"{column} >= 0", name=name)


class OrderingIdMixin:
    id: Mapped[int] = mapped_column(bigint_type(), primary_key=True, autoincrement=True)


class PlanningCycle(OrderingIdMixin, Base):
    __tablename__ = "ordering_planning_cycle"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "substr(CAST(cycle_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_cycle_month_start",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND locked_at IS NULL AND locked_by IS NULL) OR "
            "(status = 'LOCKED' AND locked_at IS NOT NULL AND locked_by IS NOT NULL "
            "AND incoming_snapshot_id IS NOT NULL AND forecast_version_id IS NOT NULL)",
            name="ck_ord_cycle_lock_fields",
        ),
        UniqueConstraint("cycle_code", name="uq_ord_cycle_code"),
    )

    cycle_code: Mapped[str] = mapped_column(String(64), nullable=False)
    cycle_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[CycleStatus] = mapped_column(
        enum_type(CycleStatus, "ck_ord_cycle_status_values"),
        nullable=False,
        default=CycleStatus.OPEN,
        server_default=CycleStatus.OPEN.value,
        index=True,
    )
    incoming_snapshot_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(), restricted_fk("ordering_incoming_snapshot.id", "fk_ord_cycle_incoming")
    )
    forecast_version_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(), ForeignKey("ordering_forecast_version.id", name="fk_ord_cycle_forecast",
                                 ondelete="RESTRICT", onupdate="RESTRICT", use_alter=True)
    )
    locked_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    locked_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class CycleProductSnapshot(OrderingIdMixin, Base):
    __tablename__ = "ordering_cycle_product_snapshot"
    __table_args__ = mysql_table_args(
        UniqueConstraint("cycle_id", "product_id", name="uq_ord_cycle_product_snapshot"),
    )
    cycle_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk(
        "ordering_planning_cycle.id", "fk_ord_product_snap_cycle"), nullable=False)
    product_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk(
        "mdm_product.id", "fk_ord_product_snap_product"), nullable=False)
    product_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    product_code: Mapped[Optional[str]] = mapped_column(String(128))
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)


class CycleChannelSnapshot(OrderingIdMixin, Base):
    __tablename__ = "ordering_cycle_channel_snapshot"
    __table_args__ = mysql_table_args(
        UniqueConstraint("cycle_id", "channel_id", name="uq_ord_cycle_channel_snapshot"),
    )
    cycle_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk(
        "ordering_planning_cycle.id", "fk_ord_channel_snap_cycle"), nullable=False)
    channel_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk(
        "mdm_channel.id", "fk_ord_channel_snap_channel"), nullable=False)
    channel_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    channel_code: Mapped[Optional[str]] = mapped_column(String(64))
    channel_name: Mapped[str] = mapped_column(String(128), nullable=False)


class DatasetVersion(OrderingIdMixin, Base):
    __tablename__ = "ordering_dataset_version"
    __table_args__ = mysql_table_args(
        CheckConstraint("period_start <= period_end", name="ck_ord_version_period_order"),
        CheckConstraint(
            "substr(CAST(period_start AS CHAR), 9, 2) = '01' AND "
            "substr(CAST(period_end AS CHAR), 9, 2) = '01'",
            name="ck_ord_version_period_months",
        ),
        CheckConstraint(
            CHECKSUM_CHECK.format(column="source_checksum"),
            name="ck_ord_version_checksum_format",
        ),
        UniqueConstraint("version_key", name="uq_ord_version_key"),
        Index("ix_ord_version_type_period", "dataset_type", "period_start", "period_end"),
    )

    dataset_type: Mapped[DatasetType] = mapped_column(
        enum_type(DatasetType, "ck_ord_version_type_values"), nullable=False
    )
    version_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    source_name: Mapped[Optional[str]] = mapped_column(String(512))
    source_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class CycleSourceBinding(OrderingIdMixin, Base):
    __tablename__ = "ordering_cycle_source_binding"
    __table_args__ = mysql_table_args(
        CheckConstraint("period_start <= period_end", name="ck_ord_binding_period_order"),
        CheckConstraint(
            "substr(CAST(period_start AS CHAR), 9, 2) = '01' AND "
            "substr(CAST(period_end AS CHAR), 9, 2) = '01'",
            name="ck_ord_binding_period_months",
        ),
        UniqueConstraint(
            "cycle_id", "dataset_version_id", "period_start", "period_end",
            name="uq_ord_binding_cycle_version_period",
        ),
        Index("ix_ord_binding_cycle_period", "cycle_id", "period_start", "period_end"),
    )

    cycle_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_planning_cycle.id", "fk_ord_binding_cycle"),
        nullable=False,
    )
    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_binding_version"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ActualRaw(OrderingIdMixin, Base):
    __tablename__ = "ordering_actual_raw"
    __table_args__ = mysql_table_args(
        UniqueConstraint("dataset_version_id", "source_row_no", name="uq_ord_actual_version_row"),
        Index("ix_ord_actual_customer_product_date", "channel_id", "product_id", "sales_date"),
        Index("ix_ord_actual_product_production", "product_id", "production_date", "sales_date"),
    )

    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_actual_version"), nullable=False
    )
    source_row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    erp_customer_code: Mapped[str] = mapped_column(String(128), nullable=False)
    erp_customer_name: Mapped[Optional[str]] = mapped_column(String(255))
    source_sku_code: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sku_name: Mapped[Optional[str]] = mapped_column(String(255))
    customer_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_customer.id", "fk_ord_actual_customer"), nullable=False
    )
    customer_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    channel_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_channel.id", "fk_ord_actual_channel"), nullable=False
    )
    channel_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    sku_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_sku.id", "fk_ord_actual_sku"), nullable=False
    )
    sku_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_actual_product"), nullable=False
    )
    product_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    sales_date: Mapped[date] = mapped_column(Date, nullable=False)
    shipped_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    warehouse_code: Mapped[str] = mapped_column(String(128), nullable=False)
    warehouse_name: Mapped[Optional[str]] = mapped_column(String(255))
    production_date: Mapped[Optional[date]] = mapped_column(Date)
    batch_no: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ActualChannelProductMonth(OrderingIdMixin, Base):
    __tablename__ = "ordering_actual_channel_product_month"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "substr(CAST(actual_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_actual_cp_month_start",
        ),
        UniqueConstraint(
            "dataset_version_id", "channel_id", "product_id", "actual_month",
            name="uq_ord_actual_cp_grain",
        ),
    )

    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_actual_cp_version"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_channel.id", "fk_ord_actual_cp_channel"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_actual_cp_product"), nullable=False
    )
    actual_month: Mapped[date] = mapped_column(Date, nullable=False)
    shipped_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ActualProductProductionMonth(OrderingIdMixin, Base):
    __tablename__ = "ordering_actual_product_production_month"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "substr(CAST(actual_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_actual_pp_month_start",
        ),
        UniqueConstraint(
            "dataset_version_id", "product_id", "production_date", "actual_month",
            name="uq_ord_actual_pp_grain",
        ),
    )

    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_actual_pp_version"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_actual_pp_product"), nullable=False
    )
    production_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_month: Mapped[date] = mapped_column(Date, nullable=False)
    shipped_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class InventoryRaw(OrderingIdMixin, Base):
    __tablename__ = "ordering_inventory_raw"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "opening_qty >= 0 AND ending_qty >= 0",
            name="ck_ord_inventory_qty_nonnegative",
        ),
        CheckConstraint(
            "opening_qty + receipt_qty - issue_qty = ending_qty",
            name="ck_ord_inventory_balance",
        ),
        CheckConstraint(
            "substr(CAST(inventory_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_inventory_month_start",
        ),
        UniqueConstraint(
            "dataset_version_id", "source_row_no", name="uq_ord_inventory_version_row"
        ),
        Index("ix_ord_inventory_version", "dataset_version_id"),
        Index("ix_ord_inventory_product_lot", "product_id", "production_date", "inventory_month"),
    )

    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_inventory_version"), nullable=False
    )
    source_row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory_month: Mapped[date] = mapped_column(Date, nullable=False)
    source_sku_code: Mapped[str] = mapped_column(String(128), nullable=False)
    sku_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_sku.id", "fk_ord_inventory_sku"), nullable=False
    )
    sku_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_inventory_product"), nullable=False
    )
    product_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    warehouse_code: Mapped[str] = mapped_column(String(128), nullable=False)
    warehouse_name: Mapped[Optional[str]] = mapped_column(String(255))
    production_date: Mapped[date] = mapped_column(Date, nullable=False)
    batch_no: Mapped[str] = mapped_column(String(128), nullable=False)
    opening_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    receipt_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    issue_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    ending_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class InventorySkuMonth(OrderingIdMixin, Base):
    __tablename__ = "ordering_inventory_sku_month"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "opening_qty >= 0 AND ending_qty >= 0",
            name="ck_ord_inventory_sku_qty_nonnegative",
        ),
        CheckConstraint(
            "opening_qty + receipt_qty - issue_qty = ending_qty",
            name="ck_ord_inventory_sku_balance",
        ),
        CheckConstraint(
            "warehouse_scope = 'PLANNING_AVAILABLE'",
            name="ck_ord_inventory_sku_warehouse_scope",
        ),
        CheckConstraint(
            "substr(CAST(inventory_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_inventory_sku_month_start",
        ),
        UniqueConstraint(
            "dataset_version_id", "inventory_month", "sku_id",
            name="uq_ord_inventory_sku_grain",
        ),
    )

    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_inv_sku_version"), nullable=False
    )
    inventory_month: Mapped[date] = mapped_column(Date, nullable=False)
    sku_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_sku.id", "fk_ord_inv_sku_mdm"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_inv_sku_product"), nullable=False
    )
    warehouse_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    warehouse_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    opening_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    receipt_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    issue_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    ending_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class InventoryProductMonth(OrderingIdMixin, Base):
    __tablename__ = "ordering_inventory_product_month"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "opening_qty >= 0 AND ending_qty >= 0",
            name="ck_ord_inventory_product_qty_nonnegative",
        ),
        CheckConstraint(
            "opening_qty + receipt_qty - issue_qty = ending_qty",
            name="ck_ord_inventory_product_balance",
        ),
        CheckConstraint(
            "warehouse_scope = 'PLANNING_AVAILABLE'",
            name="ck_ord_inventory_product_warehouse_scope",
        ),
        CheckConstraint(
            "substr(CAST(inventory_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_inventory_product_month_start",
        ),
        UniqueConstraint(
            "dataset_version_id", "inventory_month", "product_id",
            name="uq_ord_inventory_product_grain",
        ),
    )

    dataset_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_dataset_version.id", "fk_ord_inv_product_version"), nullable=False
    )
    inventory_month: Mapped[date] = mapped_column(Date, nullable=False)
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_inv_product_mdm"), nullable=False
    )
    warehouse_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    warehouse_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    opening_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    receipt_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    issue_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    ending_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ProductionLotLifecycle(OrderingIdMixin, Base):
    __tablename__ = "ordering_production_lot_lifecycle"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "warehouse_scope = 'PLANNING_AVAILABLE'",
            name="ck_ord_lot_warehouse_scope",
        ),
        CheckConstraint(
            "consumption_months IS NULL OR consumption_months >= 1",
            name="ck_ord_lot_consumption_nonnegative",
        ),
        CheckConstraint(
            "revision_no >= 1",
            name="ck_ord_lot_revision_positive",
        ),
        CheckConstraint(
            "final_sold_out_month IS NULL OR first_inbound_month IS NULL "
            "OR final_sold_out_month >= first_inbound_month",
            name="ck_ord_lot_date_order",
        ),
        CheckConstraint(
            "(first_inbound_month IS NULL OR "
            "substr(CAST(first_inbound_month AS CHAR), 9, 2) = '01') AND "
            "(final_sold_out_month IS NULL OR "
            "substr(CAST(final_sold_out_month AS CHAR), 9, 2) = '01')",
            name="ck_ord_lot_month_start",
        ),
        CheckConstraint(
            "(lifecycle_state = 'PRE_EXISTING' AND first_inbound_month IS NULL "
            "AND final_sold_out_month IS NULL AND consumption_months IS NULL) OR "
            "(lifecycle_state = 'ACTIVE' AND first_inbound_month IS NOT NULL "
            "AND final_sold_out_month IS NULL AND consumption_months IS NULL) OR "
            "(lifecycle_state = 'SOLD_OUT' AND final_sold_out_month IS NOT NULL AND "
            "((first_inbound_month IS NULL AND consumption_months IS NULL) OR "
            "(first_inbound_month IS NOT NULL AND consumption_months IS NOT NULL)))",
            name="ck_ord_lot_state_columns",
        ),
        CheckConstraint(
            "return_classification IS NULL OR "
            "(policy_code IS NOT NULL "
            "AND policy_version IS NOT NULL AND policy_parameters IS NOT NULL)",
            name="ck_ord_lot_return_policy_fields",
        ),
        UniqueConstraint(
            "product_id", "production_date", "revision_no",
            name="uq_ord_lot_business_grain",
        ),
        Index("ix_ord_lot_product", "product_id"),
    )

    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_lot_product"), nullable=False
    )
    production_date: Mapped[date] = mapped_column(Date, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    warehouse_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_state: Mapped[LotLifecycleState] = mapped_column(
        enum_type(LotLifecycleState, "ck_ord_lot_state_values"), nullable=False
    )
    first_inbound_month: Mapped[Optional[date]] = mapped_column(Date)
    final_sold_out_month: Mapped[Optional[date]] = mapped_column(Date)
    consumption_months: Mapped[Optional[int]] = mapped_column(Integer)
    return_classification: Mapped[Optional[ReturnClassification]] = mapped_column(
        enum_type(ReturnClassification, "ck_ord_lot_return_values")
    )
    policy_code: Mapped[Optional[str]] = mapped_column(String(64))
    policy_version: Mapped[Optional[str]] = mapped_column(String(32))
    policy_parameters: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = created_at_column()


class ProductionLotSourceVersion(OrderingIdMixin, Base):
    __tablename__ = "ordering_production_lot_source_version"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "lifecycle_id", "inventory_version_id", name="uq_ord_lot_source_version"
        ),
        Index("ix_ord_lot_source_inventory_version", "inventory_version_id"),
    )

    lifecycle_id: Mapped[int] = mapped_column(
        bigint_type(),
        restricted_fk("ordering_production_lot_lifecycle.id", "fk_ord_lot_source_lifecycle"),
        nullable=False,
    )
    inventory_version_id: Mapped[int] = mapped_column(
        bigint_type(),
        restricted_fk("ordering_dataset_version.id", "fk_ord_lot_source_inventory_version"),
        nullable=False,
    )
    warehouse_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class IncomingSnapshot(OrderingIdMixin, Base):
    __tablename__ = "ordering_incoming_snapshot"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            CHECKSUM_CHECK.format(column="source_checksum"),
            name="ck_ord_incoming_checksum_format",
        ),
        UniqueConstraint("snapshot_key", name="uq_ord_incoming_snapshot_key"),
    )

    snapshot_key: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(datetime6_type(), nullable=False)
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class IncomingSnapshotLine(OrderingIdMixin, Base):
    __tablename__ = "ordering_incoming_snapshot_line"
    __table_args__ = mysql_table_args(
        quantity_check("incoming_qty", "ck_ord_incoming_qty_nonnegative"),
        UniqueConstraint(
            "snapshot_id", "source_sheet_name", "source_row_no",
            name="uq_ord_incoming_line_row",
        ),
        Index("ix_ord_incoming_sku_eta", "sku_id", "expected_arrival_date"),
        Index(
            "ix_ord_incoming_snapshot_product_entry",
            "snapshot_id", "product_id", "warehouse_entry_recorded",
        ),
    )

    snapshot_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_incoming_snapshot.id", "fk_ord_incoming_line_snapshot"), nullable=False
    )
    source_sheet_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sku_code: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sku_name: Mapped[Optional[str]] = mapped_column(String(255))
    sku_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_sku.id", "fk_ord_incoming_line_sku"), nullable=False
    )
    sku_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_incoming_line_product"), nullable=False
    )
    product_stable_id: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_arrival_date: Mapped[Optional[date]] = mapped_column(Date)
    incoming_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 3))
    warehouse_code: Mapped[Optional[str]] = mapped_column(String(128))
    warehouse_entry_recorded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    production_date: Mapped[Optional[date]] = mapped_column(Date)
    batch_no: Mapped[Optional[str]] = mapped_column(String(128))
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ForecastVersion(OrderingIdMixin, Base):
    __tablename__ = "ordering_forecast_version"
    __table_args__ = mysql_table_args(
        CheckConstraint("version_no >= 1", name="ck_ord_forecast_version_positive"),
        CheckConstraint(
            CHECKSUM_CHECK.format(column="source_checksum"),
            name="ck_ord_forecast_checksum_format",
        ),
        UniqueConstraint("cycle_id", "version_no", name="uq_ord_forecast_cycle_version"),
    )

    cycle_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_planning_cycle.id", "fk_ord_forecast_version_cycle"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ForecastLine(OrderingIdMixin, Base):
    __tablename__ = "ordering_forecast_line"
    __table_args__ = mysql_table_args(
        quantity_check("forecast_qty", "ck_ord_forecast_qty_nonnegative"),
        CheckConstraint(
            "substr(CAST(forecast_month AS CHAR), 9, 2) = '01'",
            name="ck_ord_forecast_month_start",
        ),
        UniqueConstraint(
            "forecast_version_id", "channel_id", "product_id", "forecast_month",
            name="uq_ord_forecast_grain",
        ),
    )

    forecast_version_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_forecast_version.id", "fk_ord_forecast_line_version"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_channel.id", "fk_ord_forecast_channel"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_forecast_product"), nullable=False
    )
    forecast_month: Mapped[date] = mapped_column(Date, nullable=False)
    forecast_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class FinalOrder(OrderingIdMixin, Base):
    __tablename__ = "ordering_final_order"
    __table_args__ = mysql_table_args(
        quantity_check("order_qty", "ck_ord_final_order_qty_nonnegative"),
        CheckConstraint(
            "(status = 'UNCONFIRMED' AND confirmed_by IS NULL AND confirmed_at IS NULL) OR "
            "(status = 'CONFIRMED' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_ord_final_order_confirmation_fields",
        ),
        UniqueConstraint("cycle_id", "product_id", name="uq_ord_final_order_grain"),
    )

    cycle_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_planning_cycle.id", "fk_ord_final_order_cycle"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id", "fk_ord_final_order_product"), nullable=False
    )
    order_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    status: Mapped[FinalOrderStatus] = mapped_column(
        enum_type(FinalOrderStatus, "ck_ord_final_order_status_values"),
        nullable=False,
        default=FinalOrderStatus.UNCONFIRMED,
        server_default=FinalOrderStatus.UNCONFIRMED.value,
    )
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(128))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    remark: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        datetime6_type(), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class FinalOrderRevision(OrderingIdMixin, Base):
    __tablename__ = "ordering_final_order_revision"
    __table_args__ = mysql_table_args(
        CheckConstraint("revision_no >= 1", name="ck_ord_final_revision_positive"),
        quantity_check("order_qty", "ck_ord_final_revision_qty_nonnegative"),
        CheckConstraint(
            "(status = 'UNCONFIRMED' AND confirmed_by IS NULL AND confirmed_at IS NULL) OR "
            "(status = 'CONFIRMED' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_ord_final_revision_confirmation_fields",
        ),
        UniqueConstraint("final_order_id", "revision_no", name="uq_ord_final_revision_no"),
    )

    final_order_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("ordering_final_order.id", "fk_ord_final_revision_order"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    order_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    status: Mapped[FinalOrderStatus] = mapped_column(
        enum_type(FinalOrderStatus, "ck_ord_final_revision_status_values"), nullable=False
    )
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(128))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    remark: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


ORDERING_TABLE_NAMES = frozenset(
    table_name for table_name in Base.metadata.tables if table_name.startswith("ordering_")
)
