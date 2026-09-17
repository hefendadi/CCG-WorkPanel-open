"""Atomic, idempotent Bootstrap master-data commit service."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.mdm.models import (
    ChangeAction,
    ChangeLog,
    Channel,
    Customer,
    DecisionType,
    ExternalMapping,
    ImportBatch,
    ImportDecision,
    ImportRow,
    ImportStatus,
    MasterStatus,
    Product,
    Province,
    Region,
    SKU,
    SalesRep,
)

from .confirmation import require_dependencies
from .identity import (
    channel_identity,
    customer_identity,
    historical_channel_identity,
    product_identity,
    salesrep_identity,
    sku_identity,
    stable_id,
)
from .resolution import gate_report
from .governance import BOOTSTRAP_GOVERNANCE_RULES
from .workflow import (
    WorkflowError,
    decisions_for,
    ensure_bootstrap,
    enum_value,
    get_batch,
    row_meta,
    rows_for,
)


T = TypeVar("T")


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


class BootstrapCommitService:
    def __init__(self, session: Session):
        self.session = session
        self._audit_count = 0

    def commit(self, batch_id: str, operator_id: int) -> dict[str, Any]:
        batch = get_batch(self.session, batch_id, lock=True)
        if batch.status == ImportStatus.COMMITTED:
            return self._idempotent_result(batch)
        ensure_bootstrap(batch)
        if batch.status != ImportStatus.CONFIRMED:
            raise WorkflowError("MDM_IMPORT_STATE_CONFLICT", "Commit 只接受 CONFIRMED Batch", 409, {"status": enum_value(batch.status)})
        if batch.confirmed_review_version != batch.review_version:
            raise WorkflowError(
                "MDM_IMPORT_STALE_CONFIRMED_VERSION",
                "Confirm 后 review_version 已变化",
                409,
                {"confirmed": batch.confirmed_review_version, "current": batch.review_version},
            )
        rows = rows_for(self.session, batch)
        report = gate_report(self.session, batch, rows)
        if not report["ready_to_confirm"]:
            raise WorkflowError("MDM_IMPORT_REVIEW_INCOMPLETE", "Frozen Review gate 不完整", 409, report)
        require_dependencies(self.session, batch)

        try:
            batch.status = ImportStatus.COMMITTING
            if batch.entity_type == "CHANNEL":
                summary = self._commit_channels(batch, rows, operator_id)
            elif batch.entity_type == "SALESREP":
                summary = self._commit_salesreps(batch, rows, operator_id)
            elif batch.entity_type == "SKU":
                summary = self._commit_skus(batch, rows, operator_id)
            elif batch.entity_type == "CUSTOMER":
                summary = self._commit_customers(batch, rows, operator_id)
            else:
                raise WorkflowError("MDM_IMPORT_ENTITY_NOT_SUPPORTED", "Bootstrap Commit entity_type 不受支持", 409, {"entity_type": batch.entity_type})
            self.session.flush()
            committed_at = datetime.now(timezone.utc)
            summary.update({
                "batch_id": batch.batch_id,
                "entity_type": batch.entity_type,
                "review_version": batch.review_version,
                "audit_rows": self._audit_count,
            })
            batch.result_summary = summary
            batch.committed_by = operator_id
            batch.committed_at = committed_at
            batch.status = ImportStatus.COMMITTED
            self.session.commit()
            return self._idempotent_result(batch)
        except WorkflowError:
            self.session.rollback()
            raise
        except Exception as exc:
            self.session.rollback()
            self._record_failure(batch_id, "MDM_IMPORT_COMMIT_TECHNICAL_FAILURE")
            raise WorkflowError(
                "MDM_IMPORT_COMMIT_FAILED",
                "Bootstrap Commit 技术失败，当前 Batch 已回滚",
                500,
                {"failure_type": type(exc).__name__},
            ) from exc

    @staticmethod
    def _idempotent_result(batch: ImportBatch) -> dict[str, Any]:
        return {
            "batch_id": batch.batch_id,
            "status": ImportStatus.COMMITTED.value,
            "committed_by": batch.committed_by,
            "committed_at": batch.committed_at.isoformat() if batch.committed_at else None,
            "result_summary": batch.result_summary or {},
        }

    def _record_failure(self, batch_id: str, code: str) -> None:
        try:
            failed = get_batch(self.session, batch_id, lock=True)
            if failed.status == ImportStatus.CONFIRMED:
                failed.status = ImportStatus.FAILED
                failed.failed_at = datetime.now(timezone.utc)
                failed.failure_code = code
                self.session.commit()
            else:
                self.session.rollback()
        except Exception:
            self.session.rollback()

    def _mapping_entity(
        self,
        batch: ImportBatch,
        model: type[T],
        entity_type: str,
        identity_key: str,
        identity_label: str,
        build: Callable[[str], T],
    ) -> tuple[T, bool]:
        mapping = self.session.scalar(
            select(ExternalMapping).where(
                ExternalMapping.entity_type == entity_type,
                ExternalMapping.source_system == batch.source_system,
                ExternalMapping.external_code == identity_key,
                ExternalMapping.status == MasterStatus.ACTIVE,
            )
        )
        if mapping:
            entity = self.session.get(model, mapping.entity_id)
            if entity is None or (mapping.external_name and mapping.external_name != identity_label):
                raise WorkflowError(
                    "MDM_IMPORT_SOURCE_KEY_REUSED",
                    "Bootstrap source identity key 已绑定不同主体",
                    409,
                    {"entity_type": entity_type, "identity_key": identity_key},
                )
            return entity, False

        public_id = stable_id(entity_type, identity_key)
        entity = self.session.scalar(select(model).where(model.stable_id == public_id))
        created = entity is None
        if created:
            entity = build(public_id)
            self.session.add(entity)
            self.session.flush()
        self.session.add(ExternalMapping(
            entity_type=entity_type,
            entity_id=entity.id,
            source_system=batch.source_system,
            external_code=identity_key,
            external_name=identity_label,
        ))
        self.session.flush()
        return entity, created

    def _audit(
        self,
        batch: ImportBatch,
        operator_id: int,
        entity_type: str,
        entity_id: int,
        action: ChangeAction,
        *,
        stable: str | None = None,
        field: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(ChangeLog(
            actor_id=operator_id,
            import_batch_id=batch.id,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_stable_id=stable,
            field_name=field,
            snapshot_before=before,
            snapshot_after=after,
            action=action,
        ))
        self._audit_count += 1

    @staticmethod
    def _final(row: ImportRow) -> dict[str, Any]:
        return dict(row_meta(row).get("final_values") or {})

    @staticmethod
    def _finalize(row: ImportRow, entity_type: str, entity_id: int) -> None:
        row.status = "COMMITTED"
        row.resolved_entity_type = entity_type
        row.resolved_entity_id = entity_id

    def _commit_channels(self, batch: ImportBatch, rows: list[ImportRow], operator_id: int) -> dict[str, Any]:
        created = 0
        for row in rows:
            values = self._final(row)
            source_index = str(values["source_index"])
            identity_key = channel_identity(source_index)
            channel, is_new = self._mapping_entity(
                batch,
                Channel,
                "CHANNEL",
                identity_key,
                str(values["channel_name"]),
                lambda public_id: Channel(
                    stable_id=public_id,
                    channel_code=f"CHN-DEMO-{int(source_index):03d}",
                    channel_name=values["channel_name"],
                    status=MasterStatus.ACTIVE,
                ),
            )
            if is_new:
                created += 1
                self._audit(batch, operator_id, "CHANNEL", channel.id, ChangeAction.CREATE, stable=channel.stable_id, after=_snapshot(channel))
            self._finalize(row, "CHANNEL", channel.id)

        historical = tuple(
            (i, f"CHN-HISTORY-{i:03d}", rule.channel_name)
            for i, rule in enumerate(BOOTSTRAP_GOVERNANCE_RULES.historical_channels, 1)
        )
        historical_result = []
        for number, code, name in historical:
            public_id = stable_id("CHANNEL", historical_channel_identity(number))
            channel = self.session.scalar(select(Channel).where(Channel.stable_id == public_id))
            is_new = channel is None
            if is_new:
                channel = Channel(
                    stable_id=public_id,
                    channel_code=code,
                    channel_name=name,
                    channel_type="HISTORICAL",
                    status=MasterStatus.INACTIVE,
                )
                self.session.add(channel)
                self.session.flush()
                self._audit(batch, operator_id, "CHANNEL", channel.id, ChangeAction.CREATE, stable=public_id, after=_snapshot(channel))
                self._audit(batch, operator_id, "CHANNEL", channel.id, ChangeAction.STATUS_CHANGE, stable=public_id, field="status", before={"status": None}, after={"status": "INACTIVE"})
                created += 1
            elif channel.channel_code != code or channel.channel_name != name or enum_value(channel.status) != MasterStatus.INACTIVE.value:
                raise WorkflowError("MDM_IMPORT_IDENTITY_CONFLICT", "Historical Channel identity 与现有数据冲突", 409, {"channel_code": code})
            historical_result.append({"stable_id": public_id, "channel_code": code, "channel_name": name, "status": "INACTIVE"})
        return {
            "committed_master_rows": len(rows) + len(historical),
            "created_master_rows": created,
            "excluded_rows": 0,
            "finalized_rows": len(rows),
            "historical_channels": historical_result,
        }

    def _commit_salesreps(self, batch: ImportBatch, rows: list[ImportRow], operator_id: int) -> dict[str, Any]:
        created = 0
        for row in rows:
            values = self._final(row)
            identity_key = salesrep_identity(row.row_number)
            salesrep, is_new = self._mapping_entity(
                batch,
                SalesRep,
                "SALESREP",
                identity_key,
                str(values["salesrep_name"]),
                lambda public_id: SalesRep(stable_id=public_id, salesrep_name=values["salesrep_name"], employee_code=None),
            )
            if is_new:
                created += 1
                self._audit(batch, operator_id, "SALESREP", salesrep.id, ChangeAction.CREATE, stable=salesrep.stable_id, after=_snapshot(salesrep))
            self._finalize(row, "SALESREP", salesrep.id)
        return {"committed_master_rows": len(rows), "created_master_rows": created, "excluded_rows": 0, "finalized_rows": len(rows)}

    def _product_representatives(self, batch: ImportBatch) -> dict[str, dict[str, Any]]:
        decision = self.session.scalar(
            select(ImportDecision).where(
                ImportDecision.import_batch_id == batch.id,
                ImportDecision.review_version == batch.confirmed_review_version,
                ImportDecision.decision_type == DecisionType.WARNING_ACK,
                ImportDecision.issue_code == "MDM_IMPORT_PRODUCT_CANDIDATE",
            )
        )
        return dict((decision.resolved_value or {}).get("representatives") or {}) if decision else {}

    def _commit_skus(self, batch: ImportBatch, rows: list[ImportRow], operator_id: int) -> dict[str, Any]:
        groups: dict[str, list[ImportRow]] = {}
        for row in rows:
            code = str(self._final(row)["source_product_code"])
            groups.setdefault(code, []).append(row)
        representatives = self._product_representatives(batch)
        products: dict[str, Product] = {}
        created_products = 0
        created_skus = 0
        for code, members in groups.items():
            representative = representatives.get(code) or {}
            product_name = representative.get("product_name")
            if not product_name:
                raise WorkflowError("MDM_IMPORT_PRODUCT_REPRESENTATIVE_REQUIRED", "Confirmed Product representative 缺失", 409, {"source_product_code": code})
            product, is_new = self._mapping_entity(
                batch,
                Product,
                "PRODUCT",
                product_identity(code),
                code,
                lambda public_id: Product(stable_id=public_id, product_code=code, product_name=product_name),
            )
            products[code] = product
            if is_new:
                created_products += 1
                self._audit(batch, operator_id, "PRODUCT", product.id, ChangeAction.CREATE, stable=product.stable_id, after=_snapshot(product))
            if BOOTSTRAP_GOVERNANCE_RULES.confirms_product_merge(code):
                self._audit(
                    batch,
                    operator_id,
                    "PRODUCT",
                    product.id,
                    ChangeAction.MERGE_RESOLUTION,
                    stable=product.stable_id,
                    field="source_product_code",
                    after={
                        "source_product_code": code,
                        "representative_sku_code": representative.get("representative_sku_code"),
                        "sku_codes": [self._final(row).get("sku_code") for row in members],
                        "scope": "BOOTSTRAP_ONLY",
                    },
                )

        for row in rows:
            values = self._final(row)
            code = str(values["sku_code"])
            product = products[str(values["source_product_code"])]
            sku, is_new = self._mapping_entity(
                batch,
                SKU,
                "SKU",
                sku_identity(code),
                str(values["sku_name"]),
                lambda public_id: SKU(
                    stable_id=public_id,
                    sku_code=code,
                    sku_name=values["sku_name"],
                    product_id=product.id,
                    source_product_code=values.get("source_product_code"),
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
                ),
            )
            if sku.product_id != product.id:
                raise WorkflowError("MDM_IMPORT_SOURCE_KEY_REUSED", "SKU source identity 已绑定不同 Product", 409, {"sku_code": code})
            if is_new:
                created_skus += 1
                self._audit(batch, operator_id, "SKU", sku.id, ChangeAction.CREATE, stable=sku.stable_id, after=_snapshot(sku))
            self._finalize(row, "SKU", sku.id)
        return {
            "committed_master_rows": len(rows),
            "created_master_rows": created_skus,
            "created_product_rows": created_products,
            "product_count": len(groups),
            "excluded_rows": 0,
            "finalized_rows": len(rows),
        }

    def _staged_reference(self, reference: dict[str, Any] | None, model: type[T], entity_type: str) -> T | None:
        if not reference:
            return None
        if reference.get("stable_id"):
            entity = self.session.scalar(select(model).where(model.stable_id == reference["stable_id"]))
            if entity:
                return entity
        staging_batch_id = reference.get("staging_batch_id")
        staging_row_number = reference.get("staging_row_number")
        if staging_batch_id and staging_row_number:
            upstream = self.session.scalar(select(ImportBatch).where(ImportBatch.batch_id == staging_batch_id))
            if not upstream or upstream.status != ImportStatus.COMMITTED:
                raise WorkflowError("MDM_IMPORT_DEPENDENCY_MISSING", "引用的上游 staging batch 尚未 COMMITTED", 409, {"entity_type": entity_type})
            staged = self.session.scalar(select(ImportRow).where(ImportRow.import_batch_id == upstream.id, ImportRow.row_number == staging_row_number))
            if staged and staged.resolved_entity_type == entity_type and staged.resolved_entity_id:
                entity = self.session.get(model, staged.resolved_entity_id)
                if entity:
                    return entity
        raise WorkflowError("MDM_IMPORT_DEPENDENCY_MISSING", "无法解析 frozen staging reference", 409, {"entity_type": entity_type})

    def _candidate_master(
        self,
        batch: ImportBatch,
        model: type[T],
        entity_type: str,
        identity_key: str,
        name: str,
        operator_id: int,
    ) -> T:
        code_prefix = "REG" if entity_type == "REGION" else "PRV"
        entity, is_new = self._mapping_entity(
            batch,
            model,
            entity_type,
            identity_key,
            name,
            lambda public_id: model(
                stable_id=public_id,
                **({"region_code": f"{code_prefix}_BS_{public_id[-8:]}", "region_name": name}
                   if entity_type == "REGION"
                   else {"province_code": f"{code_prefix}_BS_{public_id[-8:]}", "province_name": name}),
            ),
        )
        if is_new:
            self._audit(batch, operator_id, entity_type, entity.id, ChangeAction.CREATE, stable=entity.stable_id, after=_snapshot(entity))
        return entity

    def _decision_by_subject(self, batch: ImportBatch) -> dict[tuple[str, str], ImportDecision]:
        return {(item.subject_key, item.issue_code): item for item in decisions_for(self.session, batch, batch.confirmed_review_version)}

    def _commit_customers(self, batch: ImportBatch, rows: list[ImportRow], operator_id: int) -> dict[str, Any]:
        included = [row for row in rows if not row_meta(row).get("exclusion")]
        excluded = [row for row in rows if row_meta(row).get("exclusion")]
        decisions = self._decision_by_subject(batch)
        created = 0
        by_row: dict[int, Customer] = {}
        region_keys: dict[str, str] = {}
        province_keys: dict[str, str] = {}
        for row in included:
            values = self._final(row)
            region_name = values.get("region_candidate")
            province_name = values.get("province_candidate")
            if region_name and region_name not in region_keys:
                region_keys[region_name] = f"REGION:BOOTSTRAP:R{row.row_number}"
            if province_name and province_name not in province_keys:
                province_keys[province_name] = f"PROVINCE:BOOTSTRAP:R{row.row_number}"

        for row in included:
            values = self._final(row)
            meta = row_meta(row)
            refs = dict(meta.get("references") or {})
            channel_resolution = values.get("channel_resolution") or {}
            historical_code = {
                rule.channel_name: f"CHN-HISTORY-{i:03d}"
                for i, rule in enumerate(BOOTSTRAP_GOVERNANCE_RULES.historical_channels, 1)
            }.get(channel_resolution.get("channel_name"))
            if historical_code:
                channel = self.session.scalar(select(Channel).where(Channel.channel_code == historical_code))
                if not channel or enum_value(channel.status) != MasterStatus.INACTIVE.value:
                    raise WorkflowError("MDM_IMPORT_DEPENDENCY_MISSING", "Historical Channel 尚未正确提交", 409)
            else:
                channel = self._staged_reference(refs.get("channel"), Channel, "CHANNEL")
            salesrep = self._staged_reference(refs.get("salesrep"), SalesRep, "SALESREP")
            region_name = values.get("region_candidate")
            province_name = values.get("province_candidate")
            region = self._candidate_master(
                batch, Region, "REGION", region_keys[region_name], region_name, operator_id
            ) if region_name else None
            province = self._candidate_master(
                batch, Province, "PROVINCE", province_keys[province_name], province_name, operator_id
            ) if province_name else None
            identity_key = customer_identity(row.row_number)
            customer, is_new = self._mapping_entity(
                batch,
                Customer,
                "CUSTOMER",
                identity_key,
                str(values["customer_name"]),
                lambda public_id: Customer(
                    stable_id=public_id,
                    customer_code=values["customer_code"],
                    customer_name=values["customer_name"],
                    organization=values.get("organization"),
                    department=values.get("department"),
                    business_type=values.get("business_type"),
                    market_type=values.get("market_type"),
                    channel_id=channel.id,
                    salesrep_id=salesrep.id,
                    region_id=region.id if region else None,
                    province_id=province.id if province else None,
                    format_type=values.get("format_type"),
                    channel_detail=values.get("channel_detail"),
                    is_direct=values.get("is_direct"),
                    parent_customer_id=None,
                    source_created_ym=values.get("source_created_ym"),
                ),
            )
            if customer.channel_id != channel.id or customer.salesrep_id != salesrep.id:
                raise WorkflowError("MDM_IMPORT_SOURCE_KEY_REUSED", "Customer source identity 已绑定不同关系", 409, {"row_number": row.row_number})
            if is_new:
                created += 1
                self._audit(batch, operator_id, "CUSTOMER", customer.id, ChangeAction.CREATE, stable=customer.stable_id, after=_snapshot(customer))
            by_row[row.row_number] = customer
            self._finalize(row, "CUSTOMER", customer.id)

        for row in included:
            customer = by_row[row.row_number]
            values = self._final(row)
            refs = dict(row_meta(row).get("references") or {})
            parent = None
            parent_decision = decisions.get((f"ROW:{row.row_number}", "MDM_IMPORT_PARENT_UNRESOLVED"))
            if parent_decision and parent_decision.decision == "MAP_PARENT":
                public_id = (parent_decision.resolved_value or {}).get("stable_id")
                parent = self.session.scalar(select(Customer).where(Customer.stable_id == public_id))
                if not parent:
                    raise WorkflowError("MDM_IMPORT_PARENT_NOT_FOUND", "Resolved Parent Customer 不存在", 409, {"stable_id": public_id})
            elif values.get("parent_resolution") == "RESOLVED":
                ref = refs.get("parent_customer") or {}
                if ref.get("staging_batch_id") == batch.batch_id and ref.get("staging_row_number"):
                    parent = by_row.get(int(ref["staging_row_number"]))
                elif ref:
                    parent = self._staged_reference(ref, Customer, "CUSTOMER")
            if parent:
                if parent.id == customer.id:
                    raise WorkflowError("MDM_IMPORT_PARENT_SELF", "Customer 不能成为自己的 parent", 409, {"row_number": row.row_number})
                customer.parent_customer_id = parent.id
                self._audit(
                    batch,
                    operator_id,
                    "CUSTOMER",
                    customer.id,
                    ChangeAction.RELATION_CHANGE,
                    stable=customer.stable_id,
                    field="parent_customer_id",
                    before={"parent_customer_stable_id": None},
                    after={"parent_customer_stable_id": parent.stable_id},
                )

        for row in excluded:
            exclusion = dict(row_meta(row).get("exclusion") or {})
            row.status = "EXCLUDED"
            row.resolved_entity_type = None
            row.resolved_entity_id = None
            self._audit(
                batch,
                operator_id,
                "IMPORT_ROW",
                row.id,
                ChangeAction.EXCLUSION,
                field="status",
                before={"status": "SKIPPED", "source_value": exclusion.get("source_value")},
                after={"status": "EXCLUDED", **exclusion},
            )

        return {
            "committed_master_rows": len(included),
            "created_master_rows": created,
            "excluded_rows": len(excluded),
            "finalized_rows": len(rows),
        }
