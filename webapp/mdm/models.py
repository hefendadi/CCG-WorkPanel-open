"""SQLAlchemy models for the isolated MDM V1 database foundation."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class MasterStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class ChangeAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    ACTIVATE = "ACTIVATE"
    DEACTIVATE = "DEACTIVATE"
    MERGE = "MERGE"
    STATUS_CHANGE = "STATUS_CHANGE"
    RELATION_CHANGE = "RELATION_CHANGE"
    MERGE_RESOLUTION = "MERGE_RESOLUTION"
    EXCLUSION = "EXCLUSION"


class ImportStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    RESOLVING = "RESOLVING"
    READY_TO_CONFIRM = "READY_TO_CONFIRM"
    CONFIRMED = "CONFIRMED"
    REVIEW = "REVIEW"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ImportMode(str, enum.Enum):
    BOOTSTRAP = "BOOTSTRAP"
    OPERATIONAL = "OPERATIONAL"


class DecisionType(str, enum.Enum):
    RESOLUTION = "RESOLUTION"
    WARNING_ACK = "WARNING_ACK"
    GOVERNANCE_CONFIRMATION = "GOVERNANCE_CONFIRMATION"


def bigint_type():
    """Use unsigned BIGINT on MySQL and a 64-bit INTEGER rowid in unit tests."""
    return mysql.BIGINT(unsigned=True).with_variant(Integer, "sqlite")


def unsigned_integer_type():
    return mysql.INTEGER(unsigned=True).with_variant(Integer, "sqlite")


def datetime6_type():
    return mysql.DATETIME(fsp=6).with_variant(DateTime(timezone=True), "sqlite")


def mysql_table_args(*constraints):
    return (*constraints, dict(MYSQL_TABLE_OPTIONS))


def enum_type(enum_class, name, length=16):
    return SAEnum(
        enum_class,
        name=name,
        length=length,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
    )


class IdMixin:
    id: Mapped[int] = mapped_column(bigint_type(), primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MasterMixin(IdMixin, TimestampMixin):
    stable_id: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)

    @declared_attr
    def status(cls) -> Mapped[MasterStatus]:
        return mapped_column(
            enum_type(MasterStatus, f"ck_{cls.__tablename__}_status_values"),
            nullable=False,
            default=MasterStatus.ACTIVE,
            server_default=MasterStatus.ACTIVE.value,
            index=True,
        )


class Region(MasterMixin, Base):
    __tablename__ = "mdm_region"
    __table_args__ = mysql_table_args()

    region_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    region_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)


class Province(MasterMixin, Base):
    __tablename__ = "mdm_province"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "length(trim(province_name)) > 0", name="ck_mdm_province_name_nonempty"
        ),
    )

    province_code: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    province_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    region_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_region.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    region: Mapped[Optional[Region]] = relationship(passive_deletes=True)


class Channel(MasterMixin, Base):
    __tablename__ = "mdm_channel"
    __table_args__ = mysql_table_args()

    channel_code: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    channel_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    channel_type: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer)


class SalesRep(MasterMixin, Base):
    __tablename__ = "mdm_salesrep"
    __table_args__ = mysql_table_args()

    employee_code: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    salesrep_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    organization: Mapped[Optional[str]] = mapped_column(String(128))
    department: Mapped[Optional[str]] = mapped_column(String(128))
    region_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_region.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    email: Mapped[Optional[str]] = mapped_column(String(255))
    join_date: Mapped[Optional[date]] = mapped_column(Date)
    leave_date: Mapped[Optional[date]] = mapped_column(Date)

    region: Mapped[Optional[Region]] = relationship(passive_deletes=True)


class Customer(MasterMixin, Base):
    __tablename__ = "mdm_customer"
    __table_args__ = mysql_table_args(
        Index("uq_mdm_customer_customer_code", "customer_code", unique=True),
    )

    customer_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    organization: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    department: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    business_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    market_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    channel_id: Mapped[int] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_channel.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        index=True,
    )
    salesrep_id: Mapped[int] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_salesrep.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        index=True,
    )
    region_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_region.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    province_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_province.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    format_type: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    channel_detail: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    is_direct: Mapped[Optional[bool]] = mapped_column(Boolean)
    parent_customer_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_customer.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    source_created_ym: Mapped[Optional[str]] = mapped_column(String(4))

    channel: Mapped[Channel] = relationship(passive_deletes=True)
    salesrep: Mapped[SalesRep] = relationship(passive_deletes=True)
    region: Mapped[Optional[Region]] = relationship(passive_deletes=True)
    province: Mapped[Optional[Province]] = relationship(passive_deletes=True)
    parent_customer: Mapped[Optional[Customer]] = relationship(
        remote_side="Customer.id", foreign_keys=[parent_customer_id], passive_deletes=True
    )


class Product(MasterMixin, Base):
    __tablename__ = "mdm_product"
    __table_args__ = mysql_table_args()

    product_code: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(128))


class SKU(MasterMixin, Base):
    __tablename__ = "mdm_sku"
    __table_args__ = mysql_table_args()

    sku_code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sku_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_product.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    source_product_code: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    product_group: Mapped[Optional[str]] = mapped_column(String(128))
    product_form: Mapped[Optional[str]] = mapped_column(String(64))
    origin: Mapped[Optional[str]] = mapped_column(String(64))
    category_l1: Mapped[Optional[str]] = mapped_column(String(128))
    category_l2: Mapped[Optional[str]] = mapped_column(String(128))
    category_l3: Mapped[Optional[str]] = mapped_column(String(128))
    category_l4: Mapped[Optional[str]] = mapped_column(String(128))
    short_name: Mapped[Optional[str]] = mapped_column(String(255))
    category_extra: Mapped[Optional[str]] = mapped_column(String(128))
    case_pack: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    source_created_at: Mapped[Optional[str]] = mapped_column(String(64))

    product: Mapped[Optional[Product]] = relationship(passive_deletes=True)


class ExternalMapping(IdMixin, TimestampMixin, Base):
    __tablename__ = "mdm_external_mapping"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "entity_type",
            "source_system",
            "external_code",
            name="uq_mdm_external_mapping_source_code",
        ),
        Index("ix_mdm_external_mapping_entity", "entity_type", "entity_id"),
        Index(
            "ix_mdm_external_mapping_source_external",
            "source_system",
            "external_code",
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(bigint_type(), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    external_code: Mapped[str] = mapped_column(String(128), nullable=False)
    external_name: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[MasterStatus] = mapped_column(
        enum_type(MasterStatus, "ck_mdm_external_mapping_status_values"),
        nullable=False,
        default=MasterStatus.ACTIVE,
        server_default=MasterStatus.ACTIVE.value,
        index=True,
    )


class EntityAlias(IdMixin, TimestampMixin, Base):
    __tablename__ = "mdm_entity_alias"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "entity_type",
            "normalized_alias",
            "source",
            name="uq_mdm_entity_alias_normalized_source",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_mdm_entity_alias_confidence_range",
        ),
        Index("ix_mdm_entity_alias_entity", "entity_type", "entity_id"),
        Index("ix_mdm_entity_alias_lookup", "entity_type", "alias"),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(bigint_type(), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    status: Mapped[MasterStatus] = mapped_column(
        enum_type(MasterStatus, "ck_mdm_entity_alias_status_values"),
        nullable=False,
        default=MasterStatus.ACTIVE,
        server_default=MasterStatus.ACTIVE.value,
        index=True,
    )


class ChangeLog(IdMixin, Base):
    __tablename__ = "mdm_change_log"
    __table_args__ = mysql_table_args(
        Index("ix_mdm_change_log_entity", "entity_type", "entity_id"),
        Index(
            "ix_mdm_change_log_import_batch_created_id",
            "import_batch_id",
            "created_at",
            "id",
        ),
    )

    actor_id: Mapped[Optional[int]] = mapped_column(bigint_type())
    import_batch_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_import_batch.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(bigint_type(), nullable=False)
    entity_stable_id: Mapped[Optional[str]] = mapped_column(String(24))
    field_name: Mapped[Optional[str]] = mapped_column(String(128))
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    action: Mapped[ChangeAction] = mapped_column(
        enum_type(ChangeAction, "ck_mdm_change_log_action_values"), nullable=False
    )
    snapshot_before: Mapped[Optional[dict]] = mapped_column(JSON)
    snapshot_after: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class ImportBatch(IdMixin, Base):
    __tablename__ = "mdm_import_batch"
    __table_args__ = mysql_table_args(
        CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND warning_rows >= 0 AND error_rows >= 0",
            name="ck_mdm_import_batch_row_counts_nonnegative",
        ),
        CheckConstraint(
            "review_version >= 1", name="ck_mdm_import_batch_review_version_positive"
        ),
        CheckConstraint(
            "confirmed_review_version IS NULL OR confirmed_review_version >= 1",
            name="ck_mdm_import_batch_confirmed_review_version_positive",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR "
            "(length(source_sha256) = 64 AND source_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_mdm_import_batch_source_sha256_format",
        ),
        CheckConstraint(
            "commit_fingerprint IS NULL OR "
            "(length(commit_fingerprint) = 64 AND commit_fingerprint REGEXP '^[0-9a-f]{64}$')",
            name="ck_mdm_import_batch_commit_fingerprint_format",
        ),
        UniqueConstraint(
            "commit_fingerprint",
            name="uq_mdm_import_batch_commit_fingerprint",
        ),
        Index(
            "ix_mdm_import_batch_source_identity",
            "source_system",
            "entity_type",
            "worksheet",
            "source_sha256",
            "created_at",
            "id",
        ),
        Index(
            "ix_mdm_import_batch_mode_status_created",
            "import_mode",
            "status",
            "created_at",
            "id",
        ),
    )

    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    source_file: Mapped[str] = mapped_column(String(512), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    warning_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[ImportStatus] = mapped_column(
        enum_type(ImportStatus, "ck_mdm_import_batch_status_values"),
        nullable=False,
        default=ImportStatus.UPLOADED,
        server_default=ImportStatus.UPLOADED.value,
        index=True,
    )
    import_mode: Mapped[ImportMode] = mapped_column(
        enum_type(ImportMode, "ck_mdm_import_batch_import_mode_values"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    worksheet: Mapped[Optional[str]] = mapped_column(String(255))
    uploaded_by: Mapped[Optional[int]] = mapped_column(bigint_type())
    commit_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    review_version: Mapped[int] = mapped_column(
        unsigned_integer_type(), nullable=False, default=1, server_default="1"
    )
    confirmed_review_version: Mapped[Optional[int]] = mapped_column(
        unsigned_integer_type()
    )
    confirmed_by: Mapped[Optional[int]] = mapped_column(bigint_type())
    committed_by: Mapped[Optional[int]] = mapped_column(bigint_type())
    committed_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    result_summary: Mapped[Optional[dict]] = mapped_column(JSON)
    failed_at: Mapped[Optional[datetime]] = mapped_column(datetime6_type())
    failure_code: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ImportRow(IdMixin, Base):
    __tablename__ = "mdm_import_row"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "import_batch_id",
            "row_number",
            name="uq_mdm_import_row_batch_row",
        ),
        CheckConstraint("`row_number` > 0", name="ck_mdm_import_row_number_positive"),
        CheckConstraint(
            "status IN ('PENDING', 'VALID', 'WARNING', 'ERROR', 'SKIPPED', "
            "'EXCLUDED', 'REVIEW', 'READY', 'RETURNED', 'COMMITTED', 'EXISTING')",
            name="ck_mdm_import_row_status_values",
        ),
        CheckConstraint(
            "commit_result IS NULL OR commit_result IN "
            "('CREATED', 'EXISTING', 'SKIPPED', 'REJECTED')",
            name="ck_mdm_import_row_commit_result_values",
        ),
        CheckConstraint(
            "committed_snapshot IS NULL OR COALESCE(commit_result, '') = 'CREATED'",
            name="ck_mdm_import_row_committed_snapshot_result",
        ),
        CheckConstraint(
            "commit_result IS NULL OR commit_result <> 'CREATED' OR committed_snapshot IS NOT NULL",
            name="ck_mdm_import_row_created_snapshot_required",
        ),
        Index("ix_mdm_import_row_resolved", "resolved_entity_type", "resolved_entity_id"),
        Index(
            "ix_mdm_import_row_final_target_trace",
            "final_target_stable_id",
            "import_batch_id",
            "row_number",
        ),
    )

    import_batch_id: Mapped[int] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_import_batch.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        index=True,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    normalized_values: Mapped[Optional[dict]] = mapped_column(JSON)
    errors: Mapped[Optional[list]] = mapped_column(JSON)
    warnings: Mapped[Optional[list]] = mapped_column(JSON)
    resolved_entity_type: Mapped[Optional[str]] = mapped_column(String(64))
    resolved_entity_id: Mapped[Optional[int]] = mapped_column(bigint_type())
    final_target_stable_id: Mapped[Optional[str]] = mapped_column(String(24))
    commit_result: Mapped[Optional[str]] = mapped_column(String(32))
    reviewed_values: Mapped[Optional[dict]] = mapped_column(JSON)
    committed_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING", index=True
    )

    batch: Mapped[ImportBatch] = relationship(passive_deletes=True)


class ImportFinding(IdMixin, Base):
    __tablename__ = "mdm_import_finding"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "id",
            "import_row_id",
            "import_batch_id",
            "review_version",
            name="uq_mdm_import_finding_decision_target",
        ),
        CheckConstraint(
            "review_version >= 1",
            name="ck_mdm_import_finding_review_version_positive",
        ),
        CheckConstraint(
            "severity IN ('ERROR', 'WARNING', 'INFO')",
            name="ck_mdm_import_finding_severity_values",
        ),
        Index(
            "ix_mdm_import_finding_row_review",
            "import_row_id",
            "review_version",
            "id",
        ),
        Index(
            "ix_mdm_import_finding_batch_quality",
            "import_batch_id",
            "severity",
            "rule_code",
            "id",
        ),
    )

    import_batch_id: Mapped[int] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_import_batch.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    import_row_id: Mapped[int] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_import_row.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    review_version: Mapped[int] = mapped_column(unsigned_integer_type(), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[Optional[str]] = mapped_column(String(128))
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        datetime6_type(), nullable=False, server_default=func.now()
    )

    batch: Mapped[ImportBatch] = relationship(passive_deletes=True)
    row: Mapped[ImportRow] = relationship(passive_deletes=True)


class ImportDecision(IdMixin, Base):
    __tablename__ = "mdm_import_decision"
    __table_args__ = mysql_table_args(
        UniqueConstraint(
            "import_batch_id",
            "review_version",
            "subject_key",
            "decision_type",
            "issue_code",
            name="uq_mdm_import_decision_version_subject_gate",
        ),
        CheckConstraint(
            "review_version >= 1", name="ck_mdm_import_decision_review_version_positive"
        ),
        CheckConstraint(
            "import_finding_id IS NULL OR import_row_id IS NOT NULL",
            name="ck_mdm_import_decision_finding_requires_row",
        ),
        ForeignKeyConstraint(
            [
                "import_finding_id",
                "import_row_id",
                "import_batch_id",
                "review_version",
            ],
            [
                "mdm_import_finding.id",
                "mdm_import_finding.import_row_id",
                "mdm_import_finding.import_batch_id",
                "mdm_import_finding.review_version",
            ],
            name="fk_mdm_import_decision_finding_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "import_finding_id",
            "review_version",
            name="uq_mdm_import_decision_finding_review",
        ),
        Index(
            "ix_mdm_import_decision_batch_review",
            "import_batch_id",
            "review_version",
        ),
        Index(
            "ix_mdm_import_decision_row_issue", "import_row_id", "issue_code"
        ),
        Index(
            "ix_mdm_import_decision_finding_scope",
            "import_finding_id",
            "import_row_id",
            "import_batch_id",
            "review_version",
        ),
    )

    import_batch_id: Mapped[int] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_import_batch.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    import_row_id: Mapped[Optional[int]] = mapped_column(
        bigint_type(),
        ForeignKey("mdm_import_row.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    import_finding_id: Mapped[Optional[int]] = mapped_column(bigint_type())
    subject_key: Mapped[str] = mapped_column(String(191), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_type: Mapped[DecisionType] = mapped_column(
        enum_type(
            DecisionType,
            "ck_mdm_import_decision_decision_type_values",
            length=32,
        ),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    original_value: Mapped[Optional[dict]] = mapped_column(JSON)
    resolved_value: Mapped[Optional[dict]] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    operator_id: Mapped[int] = mapped_column(bigint_type(), nullable=False)
    review_version: Mapped[int] = mapped_column(
        unsigned_integer_type(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        datetime6_type(),
        nullable=False,
        server_default=func.now(),
    )

    batch: Mapped[ImportBatch] = relationship(
        foreign_keys=[import_batch_id], passive_deletes=True, overlaps="finding"
    )
    row: Mapped[Optional[ImportRow]] = relationship(
        foreign_keys=[import_row_id], passive_deletes=True, overlaps="finding"
    )
    finding: Mapped[Optional[ImportFinding]] = relationship(
        foreign_keys=[
            import_finding_id,
            import_row_id,
            import_batch_id,
            review_version,
        ],
        passive_deletes=True,
        overlaps="batch,row",
    )
