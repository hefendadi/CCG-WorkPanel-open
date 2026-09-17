"""Deterministic master and prior-staging matching for public import."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.mdm.models import (
    Channel,
    Customer,
    EntityAlias,
    ExternalMapping,
    ImportBatch,
    ImportRow,
    ImportStatus,
    MasterStatus,
    Province,
    Product,
    Region,
    SKU,
    SalesRep,
)

from .domain import ImportMode, ImportType, Match, RowAction
from .normalize import normalize_text


@dataclass(frozen=True)
class Candidate:
    entity_type: str
    entity_id: int | None
    stable_id: str | None
    name: str | None
    values: dict[str, Any]
    active: bool = True
    staging_batch_id: str | None = None
    staging_row_number: int | None = None

    def match(self, method: str, count: int, reason: str) -> Match:
        return Match(
            self.entity_type,
            self.entity_id,
            self.stable_id,
            self.name,
            method,
            count,
            reason,
            self.staging_batch_id,
            self.staging_row_number,
        )


class ReferenceCatalog:
    """Read-only index over confirmed masters and earlier public import staging rows."""

    MODEL_CONFIG = {
        ImportType.CHANNEL: (Channel, "channel_name", "channel_code"),
        ImportType.SALESREP: (SalesRep, "salesrep_name", "employee_code"),
        ImportType.CUSTOMER: (Customer, "customer_name", "customer_code"),
        ImportType.PRODUCT: (Product, "product_name", "product_code"),
        ImportType.SKU: (SKU, "sku_name", "sku_code"),
    }

    def __init__(self, session: Session, source_system: str):
        self.session = session
        self.by_name: dict[ImportType, dict[str, list[Candidate]]] = defaultdict(lambda: defaultdict(list))
        self.by_code: dict[ImportType, dict[str, list[Candidate]]] = defaultdict(lambda: defaultdict(list))
        self.by_stable_id: dict[ImportType, dict[str, Candidate]] = defaultdict(dict)
        self.by_external_code: dict[ImportType, dict[str, list[Candidate]]] = defaultdict(lambda: defaultdict(list))
        self.by_alias: dict[ImportType, dict[str, list[Candidate]]] = defaultdict(lambda: defaultdict(list))
        self.by_internal_id: dict[ImportType, dict[int, Candidate]] = defaultdict(dict)
        self.customer_references: dict[str, dict[str, list[Candidate]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.customer_reference_by_id: dict[str, dict[int, Candidate]] = defaultdict(dict)
        self._load_masters(session)
        self._load_mappings(session, source_system)
        self._load_aliases(session)
        self._load_staging(session)

    def _load_masters(self, session: Session):
        for import_type, (model, name_field, code_field) in self.MODEL_CONFIG.items():
            for obj in session.scalars(select(model)).all():
                values = {column.name: getattr(obj, column.name) for column in model.__table__.columns}
                candidate = Candidate(
                    import_type.value,
                    obj.id,
                    obj.stable_id,
                    getattr(obj, name_field),
                    values,
                    obj.status == MasterStatus.ACTIVE,
                )
                name = normalize_text(getattr(obj, name_field))
                code = normalize_text(getattr(obj, code_field))
                if import_type == ImportType.PRODUCT:
                    name = name.casefold() if name else None
                    code = code.casefold() if code else None
                if name:
                    self.by_name[import_type][name].append(candidate)
                if code:
                    self.by_code[import_type][code].append(candidate)
                self.by_stable_id[import_type][obj.stable_id] = candidate
                self.by_internal_id[import_type][obj.id] = candidate
        for key, model, name_field in (
            ("channel", Channel, "channel_name"),
            ("salesrep", SalesRep, "salesrep_name"),
            ("region", Region, "region_name"),
            ("province", Province, "province_name"),
            ("parent_customer", Customer, "customer_name"),
        ):
            for obj in session.scalars(select(model)).all():
                name = normalize_text(getattr(obj, name_field))
                if not name:
                    continue
                values = {
                    column.name: getattr(obj, column.name)
                    for column in model.__table__.columns
                }
                candidate = Candidate(
                    model.__tablename__.removeprefix("mdm_").upper(),
                    obj.id,
                    obj.stable_id,
                    getattr(obj, name_field),
                    values,
                    obj.status == MasterStatus.ACTIVE,
                )
                self.customer_references[key][name].append(candidate)
                self.customer_reference_by_id[key][obj.id] = candidate

    def exact_customer_reference(
        self, key: str, name: str | None
    ) -> tuple[Match | None, list[Candidate]]:
        """Resolve an Operational Customer reference by exact normalized Master name."""
        normalized = normalize_text(name)
        if not normalized:
            return None, []
        candidates = self.customer_references[key].get(normalized, [])
        active = [candidate for candidate in candidates if candidate.active]
        if len(active) == 1:
            return active[0].match(
                "EXACT_ACTIVE_MASTER_NAME",
                1,
                "unique exact active Reference Master name",
            ), candidates
        return None, candidates

    def _load_mappings(self, session: Session, source_system: str):
        mappings = session.scalars(
            select(ExternalMapping).where(
                ExternalMapping.source_system == source_system,
                ExternalMapping.status == MasterStatus.ACTIVE,
            )
        ).all()
        for mapping in mappings:
            try:
                import_type = ImportType(mapping.entity_type)
            except ValueError:
                continue
            candidate = self.by_internal_id[import_type].get(mapping.entity_id)
            if candidate:
                self.by_external_code[import_type][mapping.external_code].append(candidate)

    def _load_aliases(self, session: Session):
        aliases = session.scalars(
            select(EntityAlias).where(EntityAlias.status == MasterStatus.ACTIVE)
        ).all()
        for alias in aliases:
            try:
                import_type = ImportType(alias.entity_type)
            except ValueError:
                continue
            candidate = self.by_internal_id[import_type].get(alias.entity_id)
            normalized = normalize_text(alias.normalized_alias or alias.alias)
            if candidate and normalized:
                self.by_alias[import_type][normalized].append(candidate)

    def _load_staging(self, session: Session):
        query = (
            select(ImportBatch, ImportRow)
            .join(ImportRow, ImportRow.import_batch_id == ImportBatch.id)
            .where(
                ImportBatch.entity_type.in_((ImportType.CHANNEL.value, ImportType.SALESREP.value)),
                ImportBatch.status == ImportStatus.READY_FOR_REVIEW,
                ImportRow.status.in_(("VALID", "WARNING")),
            )
            .order_by(ImportBatch.id, ImportRow.row_number)
        )
        for batch, row in session.execute(query).all():
            values = dict(row.normalized_values or {})
            values.pop("_meta", None)
            import_type = ImportType(batch.entity_type)
            name_field = "channel_name" if import_type == ImportType.CHANNEL else "salesrep_name"
            name = normalize_text(values.get(name_field))
            if not name:
                continue
            candidate = Candidate(
                import_type.value,
                None,
                None,
                name,
                values,
                True,
                batch.batch_id,
                row.row_number,
            )
            self.by_name[import_type][name].append(candidate)

    def reference(self, import_type: ImportType, name: str | None, mode: ImportMode) -> tuple[Match | None, list[Candidate]]:
        normalized = normalize_text(name)
        if not normalized:
            return None, []
        aliases = self.by_alias[import_type].get(normalized, [])
        active_aliases = [candidate for candidate in aliases if candidate.active]
        if len(active_aliases) == 1:
            return active_aliases[0].match("CONFIRMED_ALIAS", 1, "unique active confirmed alias"), aliases
        candidates = self.by_name[import_type].get(normalized, [])
        active = [candidate for candidate in candidates if candidate.active]
        if len(active) == 1 and mode == ImportMode.BOOTSTRAP:
            method = "STAGED_NORMALIZED_NAME" if active[0].staging_batch_id else "NORMALIZED_NAME"
            reason = "unique normalized name in earlier staging" if active[0].staging_batch_id else "unique normalized name in active MDM"
            return active[0].match(method, 1, reason), candidates
        return None, candidates

    def identity(self, import_type: ImportType, normalized: dict[str, Any], mode: ImportMode) -> tuple[Candidate | None, list[Candidate]]:
        _model, name_field, code_field = self.MODEL_CONFIG[import_type]
        stable_id = normalize_text(normalized.get("stable_id"))
        if stable_id:
            candidate = self.by_stable_id[import_type].get(stable_id)
            return (candidate, [candidate]) if candidate else (None, [])
        code = normalize_text(normalized.get(code_field))
        if import_type == ImportType.PRODUCT and code:
            code = code.casefold()
        if code:
            mappings = self.by_external_code[import_type].get(code, [])
            if len(mappings) == 1:
                return mappings[0], mappings
            if len(mappings) > 1:
                return None, mappings
            candidates = self.by_code[import_type].get(code, [])
            if len(candidates) == 1:
                return candidates[0], candidates
            if len(candidates) > 1:
                return None, candidates
        name = normalize_text(normalized.get(name_field))
        if import_type == ImportType.PRODUCT and name:
            name = name.casefold()
        aliases = self.by_alias[import_type].get(name or "", [])
        if len(aliases) == 1:
            return aliases[0], aliases
        if len(aliases) > 1:
            return None, aliases
        candidates = self.by_name[import_type].get(name or "", [])
        masters = [candidate for candidate in candidates if candidate.stable_id is not None]
        if len(masters) == 1 and mode == ImportMode.BOOTSTRAP:
            return masters[0], masters
        return None, masters


def proposed_action(candidate: Candidate | None, normalized: dict[str, Any]) -> RowAction:
    if candidate is None:
        return RowAction.NEW
    comparable = {
        key: value
        for key, value in normalized.items()
        if key in candidate.values and key not in {"id", "stable_id", "status", "created_at", "updated_at"}
    }
    unchanged = all(
        normalize_text(candidate.values.get(key)) == normalize_text(value)
        for key, value in comparable.items()
    )
    return RowAction.UNCHANGED if unchanged else RowAction.UPDATE
