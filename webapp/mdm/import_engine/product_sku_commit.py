"""Atomic Admin Commit for Operational 商品导入 V2 (PRODUCT_SKU) batches.

One workbook holds SKU rows plus, per distinct Product Code, the Product plan.
The Commit runs in a single transaction: first every to-be-created Product
(product plans with action=CREATE, in file order), then every SKU bound to its
existing or freshly created Product. Any failure rolls the whole Batch back.

Mirrors ProductCommitService / SKUCommitService (dedicated MySQL advisory lock,
admin-only, CREATE-only for Master rows, audit ChangeLog with import_batch_id,
final revalidation against concurrent Master writes).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webapp.mdm.models import (
    ChangeAction,
    ChangeLog,
    ImportDecision,
    ImportFinding,
    ImportMode,
    ImportRow,
    ImportStatus,
    MasterStatus,
    Product,
    SKU,
)

from .identity import product_identity, sku_identity, stable_id
from .normalize import normalize_text
from .workflow import WorkflowError, enum_value, get_batch, row_meta, rows_for


_MYSQL_COMMIT_LOCK = "mdm:goods-import-v2:admin-commit"


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value


def _snapshot(obj: Any) -> dict[str, Any]:
    return {
        column.name: _json_value(getattr(obj, column.name))
        for column in obj.__table__.columns
        if column.name != "id"
    }


def _casefold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _name_key(value: Any) -> str:
    """Deterministic identity text key (NFKC + whitespace folding + casefold).

    Kept identical to the evaluator so format-only name differences never fail
    the final revalidation of an Existing SKU row.
    """
    return (normalize_text(value) or "").casefold()


class GoodsCommitService:
    """CREATE-only, all-or-nothing commit for one 商品导入 batch."""

    entity_type = "PRODUCT_SKU"

    def __init__(self, session: Session):
        self.session = session

    def commit(self, batch_id: str, operator_id: int, actor_permission: str) -> dict[str, Any]:
        if actor_permission != "EDIT":
            raise WorkflowError(
                "MDM_IMPORT_ADMIN_REQUIRED",
                "商品导入 Commit requires MDM EDIT",
                403,
            )
        lock_connection = None
        try:
            lock_connection = self._acquire_global_lock()
            batch = get_batch(self.session, batch_id, lock=True)
            self._require_commit_batch(batch)
            rows = rows_for(self.session, batch)
            create_rows, existing_rows = self._final_quality_gate(batch, rows)
            existing_products = self._revalidate(batch, create_rows, existing_rows)
            self._write(batch, create_rows, existing_rows, existing_products, operator_id)
            committed_at = datetime.now(timezone.utc)
            summary = self._summary(create_rows, existing_rows)
            batch.status = ImportStatus.COMMITTED
            batch.committed_by = operator_id
            batch.committed_at = committed_at
            batch.result_summary = summary
            self.session.flush()
            self.session.commit()
            return {
                "batch_id": batch.batch_id,
                "status": ImportStatus.COMMITTED.value,
                "committed_by": operator_id,
                "committed_at": committed_at.isoformat(),
                "result_summary": summary,
            }
        except WorkflowError:
            self.session.rollback()
            raise
        except IntegrityError as exc:
            self.session.rollback()
            raise WorkflowError(
                "MDM_IMPORT_DUPLICATE_CREATE",
                "Product/SKU was created concurrently; Batch was rolled back",
                409,
                {"failure_type": type(exc).__name__},
            ) from exc
        except Exception as exc:
            self.session.rollback()
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_FAILED",
                "商品导入 Commit failed; Batch was rolled back",
                500,
                {"failure_type": type(exc).__name__},
            ) from exc
        finally:
            self._release_global_lock(lock_connection)

    @staticmethod
    def _summary(
        create_rows: list[ImportRow],
        existing_rows: list[ImportRow],
    ) -> dict[str, Any]:
        created_product_codes = {
            _casefold(
                (row_meta(row).get("product_candidate") or {}).get("product_code")
            )
            for row in create_rows
            if (row_meta(row).get("product_candidate") or {}).get("action") == "CREATE"
        }
        return {
            "products_created": len(created_product_codes),
            "skus_created": len(create_rows),
            "existing": len(existing_rows),
            "total": len(create_rows) + len(existing_rows),
        }

    def _write(
        self,
        batch,
        create_rows: list[ImportRow],
        existing_rows: list[ImportRow],
        products_by_code: dict[str, Product],
        operator_id: int,
    ) -> None:
        """Create Products first (file order), then every SKU, in one tx."""
        create_rows = sorted(create_rows, key=lambda row: row.row_number)
        ordered_codes: list[str] = []
        seen_codes: set[str] = set()
        for row in create_rows:
            code = _casefold((row.reviewed_values or {}).get("product_code"))
            if code and code not in seen_codes:
                seen_codes.add(code)
                ordered_codes.append(code)
        for code_key in ordered_codes:
            if code_key in products_by_code:
                continue
            plan = row_meta(next(row for row in create_rows if _casefold((row.reviewed_values or {}).get("product_code")) == code_key)).get(
                "product_candidate"
            ) or {}
            product = Product(
                stable_id=stable_id("PRODUCT", product_identity(plan.get("product_code") or "")),
                product_code=plan.get("product_code"),
                product_name=plan.get("product_name"),
                brand=plan.get("brand"),
                status=MasterStatus.ACTIVE,
            )
            self.session.add(product)
            self.session.flush()
            products_by_code[code_key] = product
            self.session.add(
                ChangeLog(
                    actor_id=operator_id,
                    import_batch_id=batch.id,
                    entity_type="PRODUCT",
                    entity_id=product.id,
                    entity_stable_id=product.stable_id,
                    action=ChangeAction.CREATE,
                    snapshot_after=_snapshot(product),
                )
            )
        for row in create_rows:
            values = deepcopy(row.reviewed_values or {})
            product = products_by_code.get(
                _casefold(values.get("product_code"))
            )
            if product is None:
                raise WorkflowError(
                    "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                    "所属 Product 不存在",
                    409,
                    {"row_number": row.row_number, "product_code": values.get("product_code")},
                )
            sku = SKU(
                stable_id=stable_id("SKU", sku_identity(values["sku_code"])),
                sku_code=values["sku_code"],
                sku_name=values["sku_name"],
                product_id=product.id,
                source_product_code=product.product_code,
                product_group=values.get("product_group"),
                product_form=values.get("product_form"),
                origin=values.get("origin"),
                category_l1=values.get("category_l1"),
                category_l2=values.get("category_l2"),
                category_l3=values.get("category_l3"),
                category_l4=values.get("category_l4"),
                short_name=values.get("short_name"),
                category_extra=values.get("category_extra"),
                case_pack=values.get("case_pack"),
                source_created_at=values.get("source_created_at"),
                status=MasterStatus.ACTIVE,
            )
            self.session.add(sku)
            self.session.flush()
            row.status = "COMMITTED"
            row.commit_result = "CREATED"
            row.final_target_stable_id = sku.stable_id
            row.resolved_entity_type = "SKU"
            row.resolved_entity_id = sku.id
            row.committed_snapshot = deepcopy(row.reviewed_values)
            self.session.add(
                ChangeLog(
                    actor_id=operator_id,
                    import_batch_id=batch.id,
                    entity_type="SKU",
                    entity_id=sku.id,
                    entity_stable_id=sku.stable_id,
                    action=ChangeAction.CREATE,
                    snapshot_after=_snapshot(sku),
                )
            )

    def _acquire_global_lock(self):
        if self.session.get_bind().dialect.name != "mysql":
            return None
        connection = self.session.get_bind().connect()
        try:
            acquired = connection.scalar(select(func.get_lock(_MYSQL_COMMIT_LOCK, 10)))
        except Exception:
            connection.close()
            raise
        if acquired != 1:
            connection.close()
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_BUSY",
                "Another 商品导入 Commit is in progress",
                409,
            )
        return connection

    @staticmethod
    def _release_global_lock(connection) -> None:
        if connection is None:
            return
        try:
            connection.scalar(select(func.release_lock(_MYSQL_COMMIT_LOCK)))
        finally:
            connection.close()

    @staticmethod
    def _require_commit_batch(batch) -> None:
        if (
            enum_value(batch.import_mode) != ImportMode.OPERATIONAL.value
            or batch.entity_type != "PRODUCT_SKU"
        ):
            raise WorkflowError(
                "MDM_IMPORT_MODE_NOT_SUPPORTED",
                "Commit only supports Operational PRODUCT_SKU batches",
                409,
            )
        if batch.status != ImportStatus.READY_TO_COMMIT:
            raise WorkflowError(
                "MDM_IMPORT_STATE_CONFLICT",
                "Commit only accepts READY_TO_COMMIT Batch",
                409,
                {"status": enum_value(batch.status)},
            )

    def _final_quality_gate(
        self, batch, rows: list[ImportRow]
    ) -> tuple[list[ImportRow], list[ImportRow]]:
        findings = list(
            self.session.scalars(
                select(ImportFinding).where(
                    ImportFinding.import_batch_id == batch.id,
                    ImportFinding.review_version == batch.review_version,
                )
            ).all()
        )
        decisions = list(
            self.session.scalars(
                select(ImportDecision).where(
                    ImportDecision.import_batch_id == batch.id,
                    ImportDecision.review_version == batch.review_version,
                )
            ).all()
        )
        acknowledged = {
            item.import_finding_id
            for item in decisions
            if enum_value(item.decision_type) == "WARNING_ACK"
            and item.decision == "ACKNOWLEDGE"
        }
        error_count = sum(item.severity == "ERROR" for item in findings)
        open_warning_count = sum(
            item.severity == "WARNING" and item.id not in acknowledged
            for item in findings
        )
        if error_count or open_warning_count or batch.error_rows or batch.warning_rows:
            raise WorkflowError(
                "MDM_IMPORT_REVIEW_INCOMPLETE",
                "Commit requires ERROR=0 and open_warning=0",
                409,
                {"error": error_count, "open_warning": open_warning_count},
            )
        create_rows = [row for row in rows if row.status == "READY"]
        existing_rows = [row for row in rows if row.status == "EXISTING"]
        if len(create_rows) + len(existing_rows) != len(rows):
            raise WorkflowError(
                "MDM_IMPORT_REVIEW_INCOMPLETE",
                "Every row must be READY or EXISTING at Commit",
                409,
            )
        if any(
            row.reviewed_values is None
            or row.commit_result is not None
            or row.committed_snapshot is not None
            for row in create_rows
        ):
            raise WorkflowError(
                "MDM_IMPORT_CREATE_ONLY_VIOLATION",
                "READY rows require an uncommitted reviewed_values snapshot",
                409,
            )
        if any(
            row.commit_result != "EXISTING"
            or row.reviewed_values is not None
            or row.committed_snapshot is not None
            for row in existing_rows
        ):
            raise WorkflowError(
                "MDM_IMPORT_CREATE_ONLY_VIOLATION",
                "Existing rows cannot participate in Master writes",
                409,
            )
        sku_codes = [_casefold(row.reviewed_values["sku_code"]) for row in create_rows]
        if len(set(sku_codes)) != len(sku_codes):
            raise WorkflowError(
                "MDM_IMPORT_DUPLICATE_CREATE",
                "Batch contains duplicate SKU Codes",
                409,
            )
        return create_rows, existing_rows

    def _revalidate(
        self,
        batch,
        create_rows: list[ImportRow],
        existing_rows: list[ImportRow],
    ) -> dict[str, Product]:
        """Final revalidation against concurrent Master writes.

        Returns the referenced ACTIVE Master Product per normalized code for
        CREATE rows whose Product already exists (action=EXISTING); codes whose
        Product is created by this Batch are absent and created in _write.
        """
        for row in existing_rows:
            values = row_meta(row).get("final_values") or {}
            sku = self.session.scalar(
                select(SKU)
                .where(SKU.stable_id == row.final_target_stable_id)
                .with_for_update()
            )
            if (
                sku is None
                or _casefold(sku.sku_code) != _casefold(values.get("sku_code"))
                or _name_key(sku.sku_name) != _name_key(values.get("sku_name"))
            ):
                raise WorkflowError(
                    "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                    "Existing SKU changed after Review",
                    409,
                    {"row_number": row.row_number},
                )

        products_by_code: dict[str, Product] = {}
        for row in create_rows:
            values = row.reviewed_values or {}
            sku_match = self.session.scalar(
                select(SKU.id)
                .where(func.lower(SKU.sku_code) == _casefold(values.get("sku_code")))
                .with_for_update()
            )
            if sku_match is not None:
                raise WorkflowError(
                    "MDM_IMPORT_DUPLICATE_CREATE",
                    "SKU Code was created after Review",
                    409,
                    {"row_number": row.row_number},
                )
            plan = row_meta(row).get("product_candidate") or {}
            code_key = _casefold(values.get("product_code"))
            if plan.get("action") == "EXISTING":
                product = self.session.scalar(
                    select(Product)
                    .where(Product.stable_id == plan.get("product_stable_id"))
                    .with_for_update()
                )
                if (
                    product is None
                    or _casefold(product.product_code) != code_key
                    or enum_value(product.status) != MasterStatus.ACTIVE.value
                ):
                    raise WorkflowError(
                        "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                        "所属 Product 不存在或已停用",
                        409,
                        {"row_number": row.row_number, "product_code": values.get("product_code")},
                    )
                products_by_code[code_key] = product
            else:
                duplicate = self.session.scalar(
                    select(Product.id)
                    .where(func.lower(Product.product_code) == code_key)
                    .with_for_update()
                )
                if duplicate is not None:
                    raise WorkflowError(
                        "MDM_IMPORT_DUPLICATE_CREATE",
                        "Product Code was created after Review",
                        409,
                        {"row_number": row.row_number, "product_code": values.get("product_code")},
                    )
        return products_by_code
