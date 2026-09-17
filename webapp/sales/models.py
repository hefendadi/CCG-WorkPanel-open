"""Sales Actual V1 database foundation; MDM references are read-only.

Quantities are ERP 实发数量 in Pcs. Raw preserves signed values; Fact contains
only positive outbound sales and does not net returns. snapshot_month is
stored as the first day of the month. Snapshot fields preserve import-time MDM
identities; historical reads must not replace them with current MDM values.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from webapp.mdm.models import (
    Base, IdMixin, bigint_type, datetime6_type, enum_type, mysql_table_args,
)


class SalesBatchStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    VALIDATED = "VALIDATED"
    PREVIEW_READY = "PREVIEW_READY"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class SalesRowStatus(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    SKIPPED = "SKIPPED"


class SalesMappingReason(str, enum.Enum):
    KIT_PARENT = "KIT_PARENT"
    NONPOSITIVE_QTY = "NONPOSITIVE_QTY"
    SKU_NOT_FOUND = "SKU_NOT_FOUND"
    SKU_AMBIGUOUS = "SKU_AMBIGUOUS"
    PRODUCT_NOT_ASSIGNED = "PRODUCT_NOT_ASSIGNED"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"


class MappingIssueType(str, enum.Enum):
    SKU_NOT_FOUND = "SKU_NOT_FOUND"
    PRODUCT_NOT_ASSIGNED = "PRODUCT_NOT_ASSIGNED"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"


class MappingIssueSeverity(str, enum.Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


SALES_QUANTITY_UNIT = "Pcs"
# Row-level outcomes only: none of these issues blocks an entire batch.
SKIP_ROW_ISSUE_TYPES = frozenset({MappingIssueType.SKU_NOT_FOUND, MappingIssueType.PRODUCT_NOT_ASSIGNED})
ISSUE_SEVERITY = {
    MappingIssueType.SKU_NOT_FOUND: MappingIssueSeverity.ERROR,
    MappingIssueType.PRODUCT_NOT_ASSIGNED: MappingIssueSeverity.ERROR,
    MappingIssueType.CUSTOMER_NOT_FOUND: MappingIssueSeverity.WARNING,
}


def restricted_fk(target):
    return ForeignKey(target, ondelete="RESTRICT", onupdate="RESTRICT")


# Only confirmation a Publish can demand in the cumulative MTD contract. Kept as
# a plain string (registry, not a DB enum): the single code is CHECK-constrained.
ABNORMAL_DROP_THRESHOLD = "ABNORMAL_DROP_THRESHOLD"
PUBLISH_CONFIRMATION_CODES = (ABNORMAL_DROP_THRESHOLD,)


class SalesImportBatch(IdMixin, Base):
    __tablename__ = "sales_import_batch"
    __table_args__ = mysql_table_args(
        CheckConstraint("length(file_hash) = 64 AND file_hash REGEXP '^[0-9a-f]{64}$'", name="ck_sales_batch_file_hash"),
        CheckConstraint("total_rows >= 0 AND ready_rows >= 0 AND skipped_rows >= 0", name="ck_sales_batch_row_counts"),
        CheckConstraint("data_date_start IS NULL OR data_date_end IS NULL OR data_date_start <= data_date_end", name="ck_sales_batch_date_range"),
        Index("ix_sales_import_batch_source_system_month", "source_system", "snapshot_month"),
    )
    # Match MDM: the application supplies str(uuid4()); no database default.
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    data_date_start: Mapped[Optional[date]] = mapped_column(Date)
    data_date_end: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[SalesBatchStatus] = mapped_column(enum_type(SalesBatchStatus, "ck_sales_batch_status"), nullable=False, server_default="UPLOADED", default=SalesBatchStatus.UPLOADED)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ready_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, server_default="0")
    ready_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, server_default="0")
    skipped_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    mapped_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    published_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    # Month version lineage: a batch stays PUBLISHED once published; when a newer
    # cumulative snapshot replaces the month, replaced_at/replaced_by record who
    # superseded it. Current month = PUBLISHED AND replaced_at IS NULL.
    replaced_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    replaced_by_batch_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(), restricted_fk("sales_import_batch.id"), nullable=True
    )


class SalesImportRow(IdMixin, Base):
    __tablename__ = "sales_import_row"
    __table_args__ = mysql_table_args(
        UniqueConstraint("import_batch_id", "source_row_no", name="uq_sales_row_batch_row"),
        CheckConstraint("source_row_no > 0", name="ck_sales_row_number"),
    )
    import_batch_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk("sales_import_batch.id"), nullable=False)
    source_row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable ERP fields retain incomplete source rows for later validation.
    source_document_no: Mapped[Optional[str]] = mapped_column(String(128))
    source_line_no: Mapped[Optional[str]] = mapped_column(String(64))
    sales_date: Mapped[Optional[date]] = mapped_column(Date)
    source_sku_code: Mapped[Optional[str]] = mapped_column(String(128))
    source_sku_name: Mapped[Optional[str]] = mapped_column(String(255))
    source_product_type: Mapped[Optional[str]] = mapped_column(String(128))
    source_customer_code: Mapped[Optional[str]] = mapped_column(String(128))
    source_customer_name: Mapped[Optional[str]] = mapped_column(String(255))
    actual_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    status: Mapped[SalesRowStatus] = mapped_column(enum_type(SalesRowStatus, "ck_sales_row_status"), nullable=False, server_default="PENDING", default=SalesRowStatus.PENDING)
    mapping_reason: Mapped[Optional[SalesMappingReason]] = mapped_column(
        enum_type(SalesMappingReason, "ck_sales_row_mapping_reason", length=32)
    )


class SalesMappingIssue(IdMixin, Base):
    __tablename__ = "sales_mapping_issue"
    __table_args__ = mysql_table_args(
        CheckConstraint("affected_row_count > 0", name="ck_sales_issue_row_count"),
    )
    import_batch_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk("sales_import_batch.id"), nullable=False, index=True)
    issue_type: Mapped[MappingIssueType] = mapped_column(enum_type(MappingIssueType, "ck_sales_issue_type", length=32), nullable=False)
    external_code: Mapped[Optional[str]] = mapped_column(String(128))
    external_name: Mapped[Optional[str]] = mapped_column(String(255))
    affected_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    affected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    severity: Mapped[MappingIssueSeverity] = mapped_column(enum_type(MappingIssueSeverity, "ck_sales_issue_severity"), nullable=False)


class SalesImportCandidate(IdMixin, Base):
    """Frozen mapping output shared by Preview and the later Publish phase."""
    __tablename__ = "sales_import_candidate"
    __table_args__ = mysql_table_args(
        UniqueConstraint("import_row_id", name="uq_sales_candidate_import_row"),
    )
    import_batch_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("sales_import_batch.id"), nullable=False, index=True
    )
    import_row_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("sales_import_row.id"), nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_sku.id"), nullable=False, index=True
    )
    sku_code_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    sku_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("mdm_product.id"), nullable=False, index=True
    )
    product_code_snapshot: Mapped[Optional[str]] = mapped_column(String(128))
    product_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(), restricted_fk("mdm_customer.id"), index=True
    )
    customer_code_snapshot: Mapped[Optional[str]] = mapped_column(String(128))
    customer_name_snapshot: Mapped[Optional[str]] = mapped_column(String(255))
    channel_id_snapshot: Mapped[Optional[int]] = mapped_column(bigint_type())
    channel_code_snapshot: Mapped[Optional[str]] = mapped_column(String(64))
    channel_name_snapshot: Mapped[Optional[str]] = mapped_column(String(128))
    salesrep_id_snapshot: Mapped[Optional[int]] = mapped_column(bigint_type())
    salesrep_code_snapshot: Mapped[Optional[str]] = mapped_column(String(64))
    salesrep_name_snapshot: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SalesPublishConfirmation(IdMixin, Base):
    """Explicit user confirmation recorded on a successful Publish.

    Only successful publishes write rows (a failed transaction leaves none), so a
    confirmation can never be reused to approve a later, different state.
    """
    __tablename__ = "sales_publish_confirmation"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "import_batch_id", "confirmation_code",
            name="uq_sales_publish_confirmation_batch_code",
        ),
        CheckConstraint(
            "confirmation_code IN ('" + ABNORMAL_DROP_THRESHOLD + "')",
            name="ck_sales_publish_confirmation_code",
        ),
        CheckConstraint(
            "drop_ratio IS NULL OR (drop_ratio >= 0 AND drop_ratio <= 1)",
            name="ck_sales_publish_confirmation_drop_ratio",
        ),
    )
    import_batch_id: Mapped[int] = mapped_column(
        bigint_type(), restricted_fk("sales_import_batch.id"), nullable=False, index=True
    )
    confirmation_code: Mapped[str] = mapped_column(String(32), nullable=False)
    existing_published_batch_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(), restricted_fk("sales_import_batch.id"), index=True
    )
    existing_data_end_date: Mapped[Optional[date]] = mapped_column(Date)
    existing_ready_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    drop_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6))
    confirmed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SalesFact(IdMixin, Base):
    __tablename__ = "sales_fact"
    __table_args__ = mysql_table_args(
        UniqueConstraint("source_system", "source_document_no", "source_line_no", "snapshot_month", name="uq_sales_fact_source_line_month"),
        CheckConstraint("actual_qty > 0", name="ck_sales_fact_actual_qty_positive"),
    )
    import_row_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk("sales_import_row.id"), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_document_no: Mapped[str] = mapped_column(String(128), nullable=False)
    source_line_no: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    sales_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    actual_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    sku_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk("mdm_sku.id"), nullable=False, index=True)
    sku_code_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    sku_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[int] = mapped_column(bigint_type(), restricted_fk("mdm_product.id"), nullable=False, index=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_id: Mapped[Optional[int]] = mapped_column(bigint_type(), restricted_fk("mdm_customer.id"), index=True)
    channel_id_snapshot: Mapped[Optional[int]] = mapped_column(bigint_type())
    channel_name_snapshot: Mapped[Optional[str]] = mapped_column(String(128))
    salesrep_id_snapshot: Mapped[Optional[int]] = mapped_column(bigint_type())
    salesrep_name_snapshot: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


SALES_TABLE_NAMES = (
    "sales_import_batch", "sales_import_row", "sales_mapping_issue",
    "sales_import_candidate", "sales_publish_confirmation", "sales_fact",
)
