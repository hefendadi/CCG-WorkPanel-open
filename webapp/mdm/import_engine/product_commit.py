"""Atomic Admin Commit for Operational Product Import V1 batches."""

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
)

from .identity import product_identity, stable_id
from .workflow import WorkflowError, enum_value, get_batch, row_meta, rows_for


_MYSQL_COMMIT_LOCK = "mdm:product-import-v1:admin-commit"


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


class ProductCommitService:
    """CREATE-only, all-or-nothing commit for one Product Batch."""

    def __init__(self, session: Session):
        self.session = session

    def commit(
        self, batch_id: str, operator_id: int, actor_permission: str
    ) -> dict[str, Any]:
        if actor_permission != "EDIT":
            raise WorkflowError(
                "MDM_IMPORT_ADMIN_REQUIRED",
                "Product Import Commit requires MDM EDIT",
                403,
            )

        lock_connection = None
        try:
            lock_connection = self._acquire_global_lock()
            batch = get_batch(self.session, batch_id, lock=True)
            self._require_commit_batch(batch)
            rows = rows_for(self.session, batch)
            create_rows, existing_rows = self._final_quality_gate(batch, rows)
            self._revalidate_products(create_rows, existing_rows)

            for row in create_rows:
                values = deepcopy(row.reviewed_values or {})
                product = Product(
                    stable_id=stable_id(
                        "PRODUCT", product_identity(values["product_code"])
                    ),
                    product_code=values["product_code"],
                    product_name=values["product_name"],
                    brand=values.get("brand"),
                    status=MasterStatus.ACTIVE,
                )
                self.session.add(product)
                self.session.flush()

                row.status = "COMMITTED"
                row.commit_result = "CREATED"
                row.final_target_stable_id = product.stable_id
                row.resolved_entity_type = "PRODUCT"
                row.resolved_entity_id = product.id
                row.committed_snapshot = deepcopy(row.reviewed_values)
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

            committed_at = datetime.now(timezone.utc)
            summary = {
                "created": len(create_rows),
                "existing": len(existing_rows),
                "total": len(rows),
                "review_version": batch.review_version,
            }
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
                "Product was created concurrently; Batch was rolled back",
                409,
                {"failure_type": type(exc).__name__},
            ) from exc
        except Exception as exc:
            self.session.rollback()
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_FAILED",
                "Product Import Commit failed; Batch was rolled back",
                500,
                {"failure_type": type(exc).__name__},
            ) from exc
        finally:
            self._release_global_lock(lock_connection)

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
                "Another Product Import Commit is in progress",
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
            or batch.entity_type != "PRODUCT"
        ):
            raise WorkflowError(
                "MDM_IMPORT_MODE_NOT_SUPPORTED",
                "Commit only supports Operational Product batches",
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
        codes = [
            row.reviewed_values["product_code"].casefold() for row in create_rows
        ]
        if len(set(codes)) != len(codes):
            raise WorkflowError(
                "MDM_IMPORT_DUPLICATE_CREATE",
                "Batch contains duplicate Product Codes",
                409,
            )
        return create_rows, existing_rows

    def _revalidate_products(
        self, create_rows: list[ImportRow], existing_rows: list[ImportRow]
    ) -> None:
        for row in existing_rows:
            values = row_meta(row).get("final_values") or {}
            product = self.session.scalar(
                select(Product)
                .where(Product.stable_id == row.final_target_stable_id)
                .with_for_update()
            )
            if (
                product is None
                or (product.product_code or "").casefold()
                != (values.get("product_code") or "").casefold()
                or (product.product_name or "").casefold()
                != (values.get("product_name") or "").casefold()
            ):
                raise WorkflowError(
                    "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                    "Existing Product changed after Review",
                    409,
                    {"row_number": row.row_number},
                )

        for row in create_rows:
            values = row.reviewed_values or {}
            code_match = self.session.scalar(
                select(Product.id)
                .where(Product.product_code == values.get("product_code"))
                .with_for_update()
            )
            if code_match is not None:
                raise WorkflowError(
                    "MDM_IMPORT_DUPLICATE_CREATE",
                    "Product Code was created after Review",
                    409,
                    {"row_number": row.row_number},
                )
            name_matches = list(
                self.session.scalars(
                    select(Product)
                .where(Product.product_name == values.get("product_name"))
                .with_for_update()
                ).all()
            )
            if name_matches and not self._same_name_conflicts_were_acknowledged(
                row, name_matches
            ):
                raise WorkflowError(
                    "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                    "Product name conflict appeared after Review",
                    409,
                    {"row_number": row.row_number},
                )

    def _same_name_conflicts_were_acknowledged(
        self, row: ImportRow, products: list[Product]
    ) -> bool:
        finding = self.session.scalar(
            select(ImportFinding).where(
                ImportFinding.import_row_id == row.id,
                ImportFinding.rule_code == "MDM_IMPORT_PRODUCT_NAME_CONFLICT",
                ImportFinding.review_version == row.batch.review_version,
            )
        )
        if finding is None:
            return False
        decision = self.session.scalar(
            select(ImportDecision.id).where(
                ImportDecision.import_finding_id == finding.id,
                ImportDecision.review_version == row.batch.review_version,
                ImportDecision.decision_type == "WARNING_ACK",
                ImportDecision.decision == "ACKNOWLEDGE",
            )
        )
        frozen = {
            item.get("stable_id")
            for item in ((finding.details or {}).get("candidates") or [])
            if isinstance(item, dict) and item.get("stable_id")
        }
        return decision is not None and {item.stable_id for item in products} == frozen
