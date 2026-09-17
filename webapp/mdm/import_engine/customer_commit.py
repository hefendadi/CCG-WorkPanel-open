"""Atomic Admin Commit for Operational Customer Import V1 batches."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webapp.mdm.models import (
    ChangeAction,
    ChangeLog,
    Channel,
    Customer,
    ImportDecision,
    ImportFinding,
    ImportMode,
    ImportRow,
    ImportStatus,
    MasterStatus,
    Province,
    Region,
    SalesRep,
)

from .identity import stable_id
from .workflow import WorkflowError, enum_value, get_batch, row_meta, rows_for
from .normalize import normalize_text
from webapp.mdm.customer_codes import customer_code_key, customer_code_query, is_customer_code_violation


T = TypeVar("T")
_MYSQL_COMMIT_LOCK = "mdm:customer-import-v1:admin-commit"


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


class CustomerCommitService:
    """CREATE-only, all-or-nothing commit for one READY_TO_COMMIT Batch."""

    def __init__(self, session: Session):
        self.session = session

    def commit(
        self,
        batch_id: str,
        operator_id: int,
        actor_permission: str,
    ) -> dict[str, Any]:
        if actor_permission != "EDIT":
            raise WorkflowError(
                "MDM_IMPORT_ADMIN_REQUIRED",
                "Customer Import Commit requires MDM EDIT",
                403,
            )

        lock_connection = None
        try:
            lock_connection = self._acquire_global_lock()
            batch = get_batch(self.session, batch_id, lock=True)
            self._require_commit_batch(batch)
            rows = rows_for(self.session, batch)
            create_rows, existing_rows = self._final_quality_gate(batch, rows)
            references = self._revalidate_masters(batch, create_rows, existing_rows)

            created: dict[int, Customer] = {}
            for row in create_rows:
                values = deepcopy(row.reviewed_values or {})
                public_id = stable_id(
                    "CUSTOMER",
                    f"CUSTOMER:CODE_NAME:{values['customer_code']}|{values['customer_name']}",
                )
                if self.session.scalar(
                    select(Customer.id).where(Customer.stable_id == public_id)
                ) is not None:
                    raise WorkflowError(
                        "MDM_IMPORT_DUPLICATE_CREATE",
                        "Customer identity was created after Review",
                        409,
                        {"row_number": row.row_number},
                    )
                refs = references[row.id]
                customer = Customer(
                    stable_id=public_id,
                    customer_code=values["customer_code"],
                    customer_name=values["customer_name"],
                    organization=values.get("organization"),
                    department=values.get("department"),
                    business_type=values.get("business_type"),
                    market_type=values.get("market_type"),
                    channel_id=refs["channel"].id,
                    salesrep_id=refs["salesrep"].id,
                    region_id=refs["region"].id if refs["region"] else None,
                    province_id=refs["province"].id if refs["province"] else None,
                    format_type=values.get("format_type"),
                    channel_detail=values.get("channel_detail"),
                    is_direct=values.get("is_direct"),
                    parent_customer_id=None,
                    source_created_ym=values.get("source_created_ym"),
                    status=MasterStatus.ACTIVE,
                )
                self.session.add(customer)
                self.session.flush()
                created[row.id] = customer

            for row in create_rows:
                customer = created[row.id]
                parent = self._resolve_parent(batch, row, created, references[row.id])
                if parent is not None:
                    if parent.id == customer.id:
                        raise WorkflowError(
                            "MDM_IMPORT_PARENT_SELF",
                            "Customer cannot be its own parent",
                            409,
                            {"row_number": row.row_number},
                        )
                    customer.parent_customer_id = parent.id

                self.session.flush()
                master_snapshot = _snapshot(customer)
                row.status = "COMMITTED"
                row.commit_result = "CREATED"
                row.final_target_stable_id = customer.stable_id
                row.resolved_entity_type = "CUSTOMER"
                row.resolved_entity_id = customer.id
                row.committed_snapshot = deepcopy(row.reviewed_values)
                self.session.add(
                    ChangeLog(
                        actor_id=operator_id,
                        import_batch_id=batch.id,
                        entity_type="CUSTOMER",
                        entity_id=customer.id,
                        entity_stable_id=customer.stable_id,
                        action=ChangeAction.CREATE,
                        snapshot_after=master_snapshot,
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
            if is_customer_code_violation(exc):
                raise WorkflowError(
                    "MDM_IMPORT_DUPLICATE_CREATE",
                    "Customer code was claimed concurrently; the entire Batch was rolled back",
                    409,
                ) from exc
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_FAILED", "Customer Import Commit failed; Batch was rolled back",
                500, {"failure_type": type(exc).__name__},
            ) from exc
        except Exception as exc:
            self.session.rollback()
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_FAILED",
                "Customer Import Commit failed; Batch was rolled back",
                500,
                {"failure_type": type(exc).__name__},
            ) from exc
        finally:
            self._release_global_lock(lock_connection)

    def _acquire_global_lock(self):
        """Acquire the MySQL advisory lock on ONE dedicated connection.

        GET_LOCK/RELEASE_LOCK are connection-scoped. The Session may switch
        pooled connections across its commit boundaries, so the lock must live
        on its own connection that is held for the whole Commit and released on
        that same connection in ``finally``. Fail-closed: any outcome other
        than acquired == 1 (busy or timeout) rejects the Commit with 409 and
        never proceeds. Non-MySQL dialects (tests, SQLite) skip the lock.
        """
        if self.session.get_bind().dialect.name != "mysql":
            return None
        connection = self.session.get_bind().connect()
        try:
            acquired = connection.scalar(
                select(func.get_lock(_MYSQL_COMMIT_LOCK, 10))
            )
        except Exception:
            connection.close()
            raise
        if acquired != 1:
            connection.close()
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_BUSY",
                "Another Customer Import Commit is in progress",
                409,
            )
        return connection

    def _release_global_lock(self, connection) -> None:
        if connection is None:
            return
        try:
            connection.scalar(
                select(func.release_lock(_MYSQL_COMMIT_LOCK))
            )
        finally:
            connection.close()

    @staticmethod
    def _require_commit_batch(batch) -> None:
        if (
            enum_value(batch.import_mode) != ImportMode.OPERATIONAL.value
            or batch.entity_type != "CUSTOMER"
        ):
            raise WorkflowError(
                "MDM_IMPORT_MODE_NOT_SUPPORTED",
                "Commit only supports Operational Customer batches",
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
        # Includes Existing rows: pre-upgrade READY batches must not bypass the
        # new workbook rule merely because their old warnings were acknowledged.
        codes = [
            customer_code_key(self.session, values["customer_code"])
            for row in rows
            for values in [row.reviewed_values or row_meta(row).get("final_values") or {}]
        ]
        if len(set(codes)) != len(codes):
            raise WorkflowError(
                "MDM_IMPORT_DUPLICATE_CREATE",
                "Batch contains duplicate Customer codes",
                409,
            )
        return create_rows, existing_rows

    def _revalidate_masters(
        self,
        batch,
        create_rows: list[ImportRow],
        existing_rows: list[ImportRow],
    ) -> dict[int, dict[str, Any]]:
        # Existing rows never write (frozen contract: EXISTING 排除出 CREATE,
        # informational and non-blocking). Revalidate only identity (existence
        # + code/name) so a deactivated Existing Master does not unconditionally
        # block the whole Batch: if a CREATE row references that Master as its
        # parent, the parent _active_reference() check below blocks it instead
        # (public contract Final Gate review item 1).
        for row in existing_rows:
            values = row_meta(row).get("final_values") or {}
            customer = self.session.scalar(
                select(Customer).where(Customer.stable_id == row.final_target_stable_id)
            )
            if (
                customer is None
                or customer_code_key(self.session, customer.customer_code) != customer_code_key(self.session, values.get("customer_code") or "")
                or normalize_text(customer.customer_name) != values.get("customer_name")
                or self.session.scalar(customer_code_query(self.session, customer.customer_code, customer.id)) is not None
            ):
                raise WorkflowError(
                    "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                    "Existing Customer changed after Review",
                    409,
                    {"row_number": row.row_number},
                )

        references: dict[int, dict[str, Any]] = {}
        for row in create_rows:
            values = row.reviewed_values or {}
            exact = self.session.scalar(customer_code_query(
                self.session, values.get("customer_code")
            ).with_for_update())
            if exact is not None:
                raise WorkflowError(
                    "MDM_IMPORT_DUPLICATE_CREATE",
                    "Customer code already exists at final revalidation; CREATE is forbidden",
                    409,
                    {"row_number": row.row_number},
                )
            references[row.id] = {
                "channel": self._active_reference(
                    Channel, "channel", values.get("channel_stable_id"), required=True
                ),
                "salesrep": self._active_reference(
                    SalesRep, "salesrep", values.get("salesrep_stable_id"), required=True
                ),
                "region": self._active_reference(
                    Region, "region", values.get("region_stable_id"), required=False
                ),
                "province": self._active_reference(
                    Province, "province", values.get("province_stable_id"), required=False
                ),
            }
            parent_stable_id = values.get("parent_customer_stable_id")
            references[row.id]["parent"] = self._active_reference(
                Customer,
                "parent_customer",
                parent_stable_id,
                required=False,
            )
        return references

    def _active_reference(
        self,
        model: type[T],
        label: str,
        public_id: str | None,
        *,
        required: bool,
    ) -> T | None:
        if not public_id:
            if required:
                raise WorkflowError(
                    "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                    f"{label} reference is required",
                    409,
                )
            return None
        entity = self.session.scalar(
            select(model).where(model.stable_id == public_id).with_for_update()
        )
        if entity is None or enum_value(entity.status) != MasterStatus.ACTIVE.value:
            raise WorkflowError(
                "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                f"{label} reference is missing or inactive",
                409,
                {"stable_id": public_id},
            )
        return entity

    def _resolve_parent(
        self,
        batch,
        row: ImportRow,
        created: dict[int, Customer],
        references: dict[str, Any],
    ) -> Customer | None:
        if references.get("parent") is not None:
            return references["parent"]
        values = row.reviewed_values or {}
        if values.get("parent_resolution") != "RESOLVED":
            return None
        frozen = dict(row_meta(row).get("references") or {}).get("parent_customer") or {}
        if frozen.get("staging_batch_id") != batch.batch_id:
            raise WorkflowError(
                "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                "Frozen parent reference cannot be resolved",
                409,
                {"row_number": row.row_number},
            )
        target_number = frozen.get("staging_row_number")
        target_row = self.session.scalar(
            select(ImportRow).where(
                ImportRow.import_batch_id == batch.id,
                ImportRow.row_number == target_number,
            )
        )
        parent = created.get(target_row.id) if target_row is not None else None
        if parent is None:
            raise WorkflowError(
                "MDM_IMPORT_FINAL_REVALIDATION_FAILED",
                "Frozen parent row is not a CREATE row",
                409,
                {"row_number": row.row_number},
            )
        return parent
