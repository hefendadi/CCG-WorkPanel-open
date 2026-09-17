"""public edition MDM API: shared CRUD, lifecycle, filtering, permissions and audit."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from webapp.auth.dependencies import current_user
from webapp.account_permissions import (
    EDIT,
    MODULE_MDM,
    VIEW,
    require_permission,
)

from .database import get_session_factory
from .customer_codes import customer_code_query, is_customer_code_violation
from .import_engine.commit import BootstrapCommitService
from .import_engine.confirmation import ConfirmationService
from .import_engine.resolution import ResolutionService, ReviewQueryService
from .import_engine.workflow import WorkflowError
from .import_engine.normalize import normalize_text
from .models import (ChangeAction, ChangeLog, Channel, Customer, MasterStatus,
                     Product, Province, Region, SKU, SalesRep)

router = APIRouter(prefix="/api/v1/mdm", tags=["mdm"])

RESOURCE = {
    "customers": (Customer, "CUSTOMER", "CUS", ["customer_name", "customer_code", "stable_id"]),
    "skus": (SKU, "SKU", "SKU", ["sku_name", "sku_code", "stable_id", "short_name"]),
    "products": (Product, "PRODUCT", "PROD", ["product_name", "product_code", "stable_id"]),
    "channels": (Channel, "CHANNEL", "CH", ["channel_name", "channel_code", "stable_id"]),
    "salesreps": (SalesRep, "SALESREP", "SR", ["salesrep_name", "employee_code", "stable_id"]),
    "regions": (Region, "REGION", "REG", ["region_name", "region_code", "stable_id"]),
    "provinces": (Province, "PROVINCE", "PROV", ["province_name", "province_code", "stable_id"]),
}
SORT_FIELDS = {name: {c.name for c in model.__table__.columns} for name, (model, *_rest) in RESOURCE.items()}
SORT_FIELDS.update({
    "channels": {"stable_id", "channel_code", "channel_name", "channel_type", "sort_order", "status", "created_at", "updated_at"},
    "salesreps": {"stable_id", "employee_code", "salesrep_name", "status", "created_at", "updated_at"},
    "regions": {"stable_id", "region_code", "region_name", "status", "created_at", "updated_at"},
    "provinces": {"stable_id", "province_code", "province_name", "status", "created_at", "updated_at"},
})
FK_FIELDS = {
    "channel_stable_id": ("channel_id", Channel), "salesrep_stable_id": ("salesrep_id", SalesRep),
    "region_stable_id": ("region_id", Region), "province_stable_id": ("province_id", Province),
    "parent_customer_stable_id": ("parent_customer_id", Customer), "product_stable_id": ("product_id", Product),
}

DEACTIVATION_REFERENCES = {
    Channel: ((Customer, Customer.channel_id),),
    SalesRep: ((Customer, Customer.salesrep_id),),
    Province: ((Customer, Customer.province_id),),
    Product: ((SKU, SKU.product_id),),
    Region: ((Customer, Customer.region_id), (Province, Province.region_id), (SalesRep, SalesRep.region_id)),
}

CUSTOMER_INTERNAL_RELATION_FIELDS = {
    "channel_id", "salesrep_id", "region_id", "province_id", "parent_customer_id",
}
SKU_INTERNAL_RELATION_FIELDS = {"product_id"}
SKU_SIGNIFICANT_FIELDS = {"category_l3", "category_l4", "case_pack"}
SKU_DISTINCT_FILTER_FIELDS = (
    "product_group", "product_form", "origin", "category_l1", "category_l2",
    "category_l3", "category_l4", "category_extra",
)
SKU_TEXT_FILTER_FIELDS = {
    "sku_code", "sku_name", "short_name", "source_product_code",
}
REFERENCE_MODELS = {Channel, SalesRep, Region, Province}


class ReferenceTextMixin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ChannelWritePayload(ReferenceTextMixin):
    channel_code: Optional[str] = Field(default=None, max_length=64)
    channel_name: Optional[str] = Field(default=None, max_length=128)
    channel_type: Optional[str] = Field(default=None, max_length=64)
    sort_order: Optional[int] = None

    @field_validator("channel_code", "channel_name")
    @classmethod
    def required_values_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value

    @field_validator("channel_type")
    @classmethod
    def blank_type_is_none(cls, value):
        return value or None


class ChannelCreatePayload(ChannelWritePayload):
    channel_code: str = Field(max_length=64)
    channel_name: str = Field(max_length=128)


class ChannelUpdatePayload(ReferenceTextMixin):
    channel_name: Optional[str] = Field(default=None, max_length=128)
    channel_type: Optional[str] = Field(default=None, max_length=64)
    sort_order: Optional[int] = None

    @field_validator("channel_name")
    @classmethod
    def name_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value

    @field_validator("channel_type")
    @classmethod
    def blank_type_is_none(cls, value):
        return value or None


class SalesRepWritePayload(ReferenceTextMixin):
    salesrep_name: Optional[str] = Field(default=None, max_length=128)
    employee_code: Optional[str] = Field(default=None, max_length=64)

    @field_validator("salesrep_name")
    @classmethod
    def name_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value

    @field_validator("employee_code")
    @classmethod
    def blank_employee_code_is_none(cls, value):
        return value or None


class SalesRepCreatePayload(SalesRepWritePayload):
    salesrep_name: str = Field(max_length=128)


class RegionWritePayload(ReferenceTextMixin):
    region_code: Optional[str] = Field(default=None, max_length=64)
    region_name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("region_code", "region_name")
    @classmethod
    def required_values_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value


class RegionCreatePayload(RegionWritePayload):
    region_code: str = Field(max_length=64)
    region_name: str = Field(max_length=128)
    confirm_create: bool = False


class RegionUpdatePayload(ReferenceTextMixin):
    region_name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("region_name")
    @classmethod
    def name_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value


class ProvinceWritePayload(ReferenceTextMixin):
    province_code: Optional[str] = Field(default=None, max_length=64)
    province_name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("province_code", "province_name")
    @classmethod
    def required_values_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value


class ProvinceCreatePayload(ProvinceWritePayload):
    province_code: str = Field(max_length=64)
    province_name: str = Field(max_length=128)
    confirm_create: bool = False


class ProvinceUpdatePayload(ReferenceTextMixin):
    province_name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("province_name")
    @classmethod
    def name_cannot_be_blank(cls, value):
        if value is None or not value:
            raise ValueError("field cannot be null")
        return value


class ReferenceLifecyclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_references: bool = False


class ProductWritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_code: Optional[str] = Field(default=None, max_length=128)
    product_name: Optional[str] = Field(default=None, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=128)

    @field_validator("product_code", "product_name")
    @classmethod
    def product_required_values_cannot_be_blank(cls, value):
        if value is None or not value.strip():
            raise ValueError("field cannot be null")
        return value.strip()

    @field_validator("brand")
    @classmethod
    def blank_brand_is_none(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ProductCreatePayload(ProductWritePayload):
    product_code: str = Field(max_length=128)
    product_name: str = Field(max_length=255)


class SKUWritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_name: Optional[str] = Field(default=None, max_length=255)
    product_group: Optional[str] = Field(default=None, max_length=128)
    product_form: Optional[str] = Field(default=None, max_length=64)
    origin: Optional[str] = Field(default=None, max_length=64)
    category_l1: Optional[str] = Field(default=None, max_length=128)
    category_l2: Optional[str] = Field(default=None, max_length=128)
    category_l3: Optional[str] = Field(default=None, max_length=128)
    category_l4: Optional[str] = Field(default=None, max_length=128)
    short_name: Optional[str] = Field(default=None, max_length=255)
    category_extra: Optional[str] = Field(default=None, max_length=128)
    case_pack: Optional[Decimal] = None
    source_product_code: Optional[str] = Field(default=None, max_length=128)
    source_created_at: Optional[str] = Field(default=None, max_length=64)
    confirm_significant_change: bool = False

    @field_validator("sku_name")
    @classmethod
    def sku_name_cannot_be_blank(cls, value):
        if value is None or not value.strip():
            raise ValueError("field cannot be null")
        return value.strip()

    @field_validator(
        "product_group", "product_form", "origin", "category_l1", "category_l2",
        "category_l3", "category_l4", "short_name", "category_extra",
        "source_product_code", "source_created_at",
    )
    @classmethod
    def blank_optional_sku_text_is_none(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("case_pack")
    @classmethod
    def case_pack_must_be_positive(cls, value):
        if value is not None and value <= 0:
            raise ValueError("case_pack must be greater than zero")
        return value


class SKUReassignmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_stable_id: str = Field(min_length=1, max_length=24)
    confirm: bool = False


class CustomerWritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_code: Optional[str] = Field(default=None, max_length=128)
    customer_name: Optional[str] = None
    organization: Optional[str] = None
    department: Optional[str] = None
    business_type: Optional[str] = None
    market_type: Optional[str] = None
    format_type: Optional[str] = None
    channel_detail: Optional[str] = None
    is_direct: Optional[bool] = None
    source_created_ym: Optional[str] = None
    channel_stable_id: Optional[str] = None
    salesrep_stable_id: Optional[str] = None
    region_stable_id: Optional[str] = None
    province_stable_id: Optional[str] = None
    parent_customer_stable_id: Optional[str] = None

    @field_validator("customer_code", mode="before")
    @classmethod
    def normalize_customer_code(cls, value):
        return normalize_text(value) if isinstance(value, str) else value

    @field_validator(
        "channel_stable_id", "salesrep_stable_id", "region_stable_id",
        "province_stable_id", "parent_customer_stable_id", mode="before",
    )
    @classmethod
    def empty_relationship_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("customer_code", "customer_name", "channel_stable_id", "salesrep_stable_id")
    @classmethod
    def required_fields_cannot_be_null(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("field cannot be null")
        return value


class CustomerCreatePayload(CustomerWritePayload):
    customer_code: str = Field(max_length=128)
    customer_name: str
    channel_stable_id: str
    salesrep_stable_id: str


def validate_customer_payload(payload: dict, *, create: bool) -> dict:
    schema = CustomerCreatePayload if create else CustomerWritePayload
    try:
        validated = schema.model_validate(payload)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(part) for part in item["loc"]), "type": item["type"]}
            for item in exc.errors()
        ]
        error("MDM_VALIDATION_ERROR", "Customer 请求字段校验失败", 422, {"errors": errors})
    return validated.model_dump(exclude_unset=True)


def validate_product_payload(payload: dict, *, create: bool) -> dict:
    schema = ProductCreatePayload if create else ProductWritePayload
    return validate_payload(schema, payload, "Product")


def validate_sku_payload(payload: dict) -> dict:
    return validate_payload(SKUWritePayload, payload, "SKU")


def validate_reference_payload(model, payload: dict, *, create: bool) -> dict:
    schemas = {
        Channel: (ChannelCreatePayload, ChannelUpdatePayload, "Channel"),
        SalesRep: (SalesRepCreatePayload, SalesRepWritePayload, "SalesRep"),
        Region: (RegionCreatePayload, RegionUpdatePayload, "Region"),
        Province: (ProvinceCreatePayload, ProvinceUpdatePayload, "Province"),
    }
    create_schema, update_schema, entity = schemas[model]
    return validate_payload(create_schema if create else update_schema, payload, entity)


def validate_payload(schema: type[BaseModel], payload: dict, entity: str) -> dict:
    try:
        validated = schema.model_validate(payload)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(part) for part in item["loc"]), "type": item["type"]}
            for item in exc.errors()
        ]
        error("MDM_VALIDATION_ERROR", f"{entity} 请求字段校验失败", 422, {"errors": errors})
    return validated.model_dump(exclude_unset=True)


def error(code: str, message: str, status: int, details: Optional[dict] = None):
    raise HTTPException(status, detail={"error": {"code": code, "message": message, "details": details or {}}})


def admin(user=Depends(lambda: None)):
    return user


def session():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


mdm_view = require_permission(MODULE_MDM, VIEW, current_user)
mdm_edit = require_permission(MODULE_MDM, EDIT, current_user)


def parse_updated_since(value: Optional[str]):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        error("MDM_INVALID_TIMESTAMP", "updated_since 必须是带时区的 ISO 8601 时间", 400)
    if dt.tzinfo is None:
        error("MDM_INVALID_TIMESTAMP", "updated_since 必须包含时区", 400)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def stable(model, db: Session, value: str, entity: str):
    row = db.scalar(select(model).where(model.stable_id == value))
    if not row:
        error(f"MDM_{entity}_NOT_FOUND", f"{entity} 不存在", 404, {"stable_id": value})
    return row


def ref(obj):
    if not obj:
        return None
    name = next((value for field in (
        "customer_name", "sku_name", "product_name", "channel_name",
        "salesrep_name", "region_name", "province_name",
    ) if (value := getattr(obj, field, None)) is not None), None)
    return {"stable_id": obj.stable_id, "name": name}


def product_ref(product: Product | None):
    if not product:
        return None
    return {
        "stable_id": product.stable_id,
        "product_code": product.product_code,
        "product_name": product.product_name,
    }


def serialize(obj, include_id=False, warnings=None):
    def json_value(value):
        if isinstance(value, datetime):
            value = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
            return value.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".") + "Z"
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if hasattr(value, "value"):
            return value.value
        return value

    hidden = {"id", "status"}
    if isinstance(obj, Customer):
        hidden.update(CUSTOMER_INTERNAL_RELATION_FIELDS)
    if isinstance(obj, SKU):
        hidden.update(SKU_INTERNAL_RELATION_FIELDS)
    if isinstance(obj, (SalesRep, Province)):
        hidden.add("region_id")
    if isinstance(obj, SalesRep):
        hidden.update({"organization", "department", "email", "join_date", "leave_date"})
    data = {c.name: json_value(getattr(obj, c.name)) for c in obj.__table__.columns if c.name not in hidden}
    data["status"] = obj.status.value if hasattr(obj.status, "value") else obj.status
    for key in ("channel", "salesrep", "region", "province", "parent_customer", "product"):
        if key == "region" and isinstance(obj, (SalesRep, Province)):
            continue
        if hasattr(obj, key):
            relation = getattr(obj, key)
            data[key] = product_ref(relation) if key == "product" else ref(relation)
    if isinstance(obj, SKU):
        # This demo adapter treats the ERP material code as the SKU barcode. Keep a
        # named public alias without duplicating the value in the database.
        data["barcode"] = obj.sku_code
    if include_id and not isinstance(obj, (Customer, Product, SKU, Channel, SalesRep, Region, Province)):
        data["internal_id"] = obj.id
    if warnings:
        data["warnings"] = warnings
    return data


def audit_value(value):
    if isinstance(value, Decimal):
        value = str(value)
    if hasattr(value, "value"):
        value = value.value
    return json.dumps(value, ensure_ascii=False, default=str)


def audit(db, obj, action, actor, before=None, changed=None, old_value=None, new_value=None, after=None):
    db.add(ChangeLog(
        actor_id=actor,
        entity_type=obj.__tablename__,
        entity_id=obj.id,
        entity_stable_id=obj.stable_id,
        action=action,
        snapshot_before=before,
        snapshot_after=after if after is not None else serialize(obj),
        field_name=changed,
        old_value=audit_value(old_value) if changed is not None else None,
        new_value=audit_value(new_value) if changed is not None else None,
    ))


def public_field_value(snapshot: dict, field: str):
    if field.endswith("_stable_id"):
        relation = snapshot.get(field.removesuffix("_stable_id"))
        return relation.get("stable_id") if relation else None
    return snapshot.get(field)


def audit_updates(db, obj, actor, before: dict, after: dict, fields: list[str]):
    for field in fields:
        old_value = public_field_value(before, field)
        new_value = public_field_value(after, field)
        if old_value == new_value:
            continue
        action = ChangeAction.RELATION_CHANGE if field.endswith("_stable_id") else ChangeAction.UPDATE
        audit(db, obj, action, actor, before, field, old_value, new_value, after)


def audit_create_fields(db, obj, actor, fields: list[str]):
    after = serialize(obj)
    for field in fields:
        new_value = public_field_value(after, field)
        audit(db, obj, ChangeAction.CREATE, actor, None, field, None, new_value, after)


def ensure_unique_reference_code(model, obj, payload: dict, db: Session):
    code_field = {
        Channel: "channel_code", SalesRep: "employee_code",
        Region: "region_code", Province: "province_code",
    }.get(model)
    if not code_field or code_field not in payload or payload[code_field] is None:
        return
    query = select(model).where(getattr(model, code_field) == payload[code_field])
    if obj is not None:
        query = query.where(model.id != obj.id)
    duplicate = db.scalar(query)
    if duplicate:
        error(
            f"MDM_DUPLICATE_{code_field.upper()}",
            f"{code_field} 已存在且不可复用",
            409,
            {"stable_id": duplicate.stable_id},
        )


def model_for(resource):
    if resource not in RESOURCE:
        raise KeyError(resource)
    return RESOURCE[resource]


def active_reference_count(model, obj, db):
    return sum(
        db.scalar(
            select(func.count()).select_from(reference_model).where(
                reference_field == obj.id,
                reference_model.status == MasterStatus.ACTIVE,
            )
        )
        for reference_model, reference_field in DEACTIVATION_REFERENCES.get(model, ())
    )


def linked_reference_count(model, obj, db):
    return sum(
        db.scalar(
            select(func.count()).select_from(reference_model).where(reference_field == obj.id)
        )
        for reference_model, reference_field in DEACTIVATION_REFERENCES.get(model, ())
    )


def sku_summary(sku: SKU) -> dict[str, Any]:
    return {
        "stable_id": sku.stable_id,
        "sku_code": sku.sku_code,
        "sku_name": sku.sku_name,
        "specification": sku.category_l3,
        "net_weight": sku.category_l4,
        "case_pack": str(sku.case_pack) if sku.case_pack is not None else None,
        "barcode": sku.sku_code,
        "status": sku.status.value if hasattr(sku.status, "value") else sku.status,
    }


def list_products(db, page, page_size, search, status, sort, order, updated_since, user, include_internal_id=False):
    if sort not in SORT_FIELDS["products"]:
        error("MDM_INVALID_PARAMETER", "sort 字段不允许", 400)
    if status not in ("ACTIVE", "INACTIVE", "ALL"):
        error("MDM_INVALID_PARAMETER", "status 只能是 ACTIVE、INACTIVE 或 ALL", 400)
    query = select(Product)
    if status != "ALL":
        query = query.where(Product.status == status)
    if search:
        pattern = f"%{search}%"
        sku_match = exists(
            select(SKU.id).where(
                SKU.product_id == Product.id,
                or_(
                    SKU.sku_code.like(pattern),
                    SKU.sku_name.like(pattern),
                    SKU.short_name.like(pattern),
                    SKU.source_product_code.like(pattern),
                ),
            )
        )
        query = query.where(or_(
            Product.product_code.like(pattern),
            Product.product_name.like(pattern),
            Product.stable_id.like(pattern),
            sku_match,
        ))
    if updated_since:
        query = query.where(Product.updated_at >= updated_since)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    column = getattr(Product, sort)
    query = query.order_by(column.asc() if order == "asc" else column.desc())
    products = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    counts = dict(db.execute(
        select(SKU.product_id, func.count(SKU.id)).where(
            SKU.product_id.in_([product.id for product in products])
        ).group_by(SKU.product_id)
    ).all()) if products else {}
    items = []
    for product in products:
        item = serialize(product)
        item["sku_count"] = counts.get(product.id, 0)
        items.append(item)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


def list_items(resource, db, page, page_size, search, status, sort, order, updated_since, user, include_internal_id=False, **filters):
    model, entity, _prefix, search_fields = model_for(resource)
    if resource == "products":
        return list_products(db, page, page_size, search, status, sort, order, updated_since, user, include_internal_id)
    if sort not in SORT_FIELDS[resource]:
        error("MDM_INVALID_PARAMETER", "sort 字段不允许", 400)
    query = select(model)
    relation_names = [name for name in ("channel", "salesrep", "region", "province", "parent_customer", "product") if hasattr(model, name)]
    if relation_names:
        query = query.options(*[joinedload(getattr(model, name)) for name in relation_names])
    if status == "ALL" and resource == "skus":
        pass
    elif status in ("ACTIVE", "INACTIVE"):
        query = query.where(model.status == status)
    else:
        error("MDM_INVALID_PARAMETER", "status 只能是 ACTIVE、INACTIVE 或 SKU 列表的 ALL", 400)
    if search:
        pattern = f"%{search}%" if resource == "skus" else search + "%"
        clauses = [getattr(model, field).like(pattern) for field in search_fields]
        if resource == "skus":
            clauses.extend((
                SKU.source_product_code.like(pattern),
                exists(
                    select(Product.id).where(
                        Product.id == SKU.product_id,
                        or_(
                            Product.product_code.like(pattern),
                            Product.product_name.like(pattern),
                        ),
                    )
                ),
            ))
        query = query.where(or_(*clauses))
    if updated_since:
        query = query.where(model.updated_at >= updated_since)
    for key, value in filters.items():
        if value is not None and ((key in FK_FIELDS and resource == "customers") or (key == "product_stable_id" and resource == "skus")):
            field, target = FK_FIELDS[key]
            query = query.where(getattr(model, field) == stable(target, db, value, target.__name__.upper()).id)
        elif value is not None and key == "barcode" and resource == "skus":
            query = query.where(SKU.sku_code.like(f"%{value}%"))
        elif value is not None and key == "sku_stable_id" and resource == "skus":
            query = query.where(SKU.stable_id == value)
        elif value is not None and key == "product_search" and resource == "skus":
            pattern = f"%{value}%"
            query = query.where(exists(
                select(Product.id).where(
                    Product.id == SKU.product_id,
                    or_(Product.product_code.like(pattern), Product.product_name.like(pattern)),
                )
            ))
        elif value is not None and key in SKU_TEXT_FILTER_FIELDS and resource == "skus":
            query = query.where(getattr(SKU, key).like(f"%{value}%"))
        elif value and key in SKU_DISTINCT_FILTER_FIELDS and resource == "skus":
            query = query.where(getattr(SKU, key).in_(value))
        elif value is not None and hasattr(model, key):
            query = query.where(getattr(model, key) == value)
    column = getattr(model, sort)
    query = query.order_by(column.asc() if order == "asc" else column.desc())
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).unique().all()
    expose_internal = False
    return {"items": [serialize(x, expose_internal) for x in rows], "page": page, "page_size": page_size, "total": total, "pages": math.ceil(total / page_size) if total else 0}


@router.get("/skus/filter-options")
def sku_filter_options(user=Depends(mdm_view), db: Session = Depends(session)):
    fields = {}
    for field in SKU_DISTINCT_FILTER_FIELDS:
        column = getattr(SKU, field)
        fields[field] = list(db.scalars(
            select(column).where(column.is_not(None), column != "").distinct().order_by(column.asc())
        ).all())
    fields["status"] = ["ACTIVE", "INACTIVE"]
    return {"fields": fields}


def ensure_unique_customer_code(db, code, exclude_id=None):
    duplicate = db.scalar(customer_code_query(db, code, exclude_id))
    if duplicate:
        error("MDM_DUPLICATE_CUSTOMER_CODE", "客户编码已被其他 Customer 使用，不允许重复", 409,
              {"customer_code": code, "stable_id": duplicate.stable_id})


def register(resource):
    model, entity, _prefix, _fields = model_for(resource)
    @router.get(f"/{resource}")
    def listing(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), search: Optional[str] = None,
                status: str = "ACTIVE", sort: str = "stable_id", order: str = "asc", updated_since: Optional[str] = None,
                channel_stable_id: Optional[str] = None, salesrep_stable_id: Optional[str] = None,
                region_stable_id: Optional[str] = None, province_stable_id: Optional[str] = None,
                product_stable_id: Optional[str] = None, business_type: Optional[str] = None,
                market_type: Optional[str] = None, is_direct: Optional[bool] = None,
                product_group: Optional[list[str]] = Query(None),
                product_form: Optional[list[str]] = Query(None), origin: Optional[list[str]] = Query(None),
                category_l1: Optional[list[str]] = Query(None), category_l2: Optional[list[str]] = Query(None),
                category_l3: Optional[list[str]] = Query(None), category_l4: Optional[list[str]] = Query(None),
                category_extra: Optional[list[str]] = Query(None),
                sku_code: Optional[str] = None, sku_name: Optional[str] = None,
                short_name: Optional[str] = None, source_product_code: Optional[str] = None,
                product_search: Optional[str] = None, case_pack: Optional[Decimal] = None,
                sku_stable_id: Optional[str] = None,
                barcode: Optional[str] = None,
                include_internal_id: bool = False,
                user=Depends(mdm_view), db: Session = Depends(session)):
        if order not in ("asc", "desc"):
            error("MDM_INVALID_PARAMETER", "order 只能是 asc 或 desc", 400)
        filters = {k: v for k, v in locals().items() if k in {
            "channel_stable_id", "salesrep_stable_id", "region_stable_id", "province_stable_id",
            "product_stable_id", "business_type", "market_type", "is_direct", "product_group",
            "product_form", "origin", "category_l1", "category_l2", "category_l3", "category_l4",
            "category_extra", "sku_code", "sku_name", "short_name", "source_product_code",
            "product_search", "case_pack", "sku_stable_id", "barcode"
        } and v is not None}
        return list_items(resource, db, page, page_size, search, status, sort, order, parse_updated_since(updated_since), user, include_internal_id, **filters)

    @router.get(f"/{resource}/{{stable_id}}")
    def detail(stable_id: str, include_internal_id: bool = False, user=Depends(mdm_view), db: Session = Depends(session)):
        relation_names = [name for name in ("channel", "salesrep", "region", "province", "parent_customer", "product") if hasattr(model, name)]
        query = select(model).where(model.stable_id == stable_id)
        if relation_names:
            query = query.options(*[joinedload(getattr(model, name)) for name in relation_names])
        obj = db.scalar(query)
        if not obj:
            error(f"MDM_{entity}_NOT_FOUND", f"{entity} 不存在", 404, {"stable_id": stable_id})
        expose_internal = False
        data = serialize(obj, expose_internal)
        if model is Product:
            children = db.scalars(
                select(SKU).where(SKU.product_id == obj.id).order_by(SKU.sku_code)
            ).all()
            data["sku_count"] = len(children)
            data["skus"] = [sku_summary(child) for child in children]
        return data

    @router.post(f"/{resource}")
    def create(payload: dict = Body(...), user=Depends(mdm_edit), db: Session = Depends(session)):
        payload = dict(payload)
        if model is Customer:
            payload = validate_customer_payload(payload, create=True)
            ensure_unique_customer_code(db, payload["customer_code"])
        elif model is Product:
            payload = validate_product_payload(payload, create=True)
            duplicate = db.scalar(select(Product).where(Product.product_code == payload["product_code"]))
            if duplicate:
                error("MDM_DUPLICATE_PRODUCT_CODE", "Product Code 已存在且不可复用", 409, {"stable_id": duplicate.stable_id})
        elif model is SKU:
            error("MDM_SKU_CREATE_NOT_ALLOWED", "Product/SKU V1 不提供手工新建 SKU", 405)
        elif model in REFERENCE_MODELS:
            payload = validate_reference_payload(model, payload, create=True)
            if model in {Region, Province} and not payload.pop("confirm_create", False):
                error(
                    f"MDM_{entity}_CREATE_CONFIRMATION_REQUIRED",
                    f"创建 {model.__name__} 需要显式确认",
                    409,
                    {"confirmation_required": True},
                )
            ensure_unique_reference_code(model, None, payload, db)
        else:
            payload.pop("stable_id", None)
        warnings = []
        if model is Product and db.scalar(select(Product).where(func.lower(Product.product_name) == payload["product_name"].lower())):
            warnings.append({"code": "MDM_POSSIBLE_PRODUCT_MATCH", "message": "存在同名 Product，请确认业务上需要独立 Product"})
        obj = model(**resolve_payload(model, payload, db))
        obj.stable_id = next_stable(model, _prefix, db)
        obj.status = MasterStatus.ACTIVE
        db.add(obj)
        try:
            db.flush()
            if model in REFERENCE_MODELS:
                audit_create_fields(db, obj, user["id"], list(payload) + ["status"])
            else:
                audit(db, obj, ChangeAction.CREATE, user["id"])
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if model is Customer and is_customer_code_violation(exc):
                error("MDM_DUPLICATE_CUSTOMER_CODE", "客户编码已被其他 Customer 使用，不允许重复", 409)
            error("MDM_CONFLICT", "资源字段冲突", 409)
        return serialize(obj, False, warnings)

    @router.patch(f"/{resource}/{{stable_id}}")
    def update(stable_id: str, payload: dict = Body(...), user=Depends(mdm_edit), db: Session = Depends(session)):
        obj = stable(model, db, stable_id, entity); before = serialize(obj); payload = dict(payload)
        if model is Customer:
            payload = validate_customer_payload(payload, create=False)
            if "customer_code" in payload:
                ensure_unique_customer_code(db, payload["customer_code"], obj.id)
        elif model is Product:
            payload = validate_product_payload(payload, create=False)
            if "product_code" in payload:
                duplicate = db.scalar(select(Product).where(
                    Product.product_code == payload["product_code"], Product.id != obj.id,
                ))
                if duplicate:
                    error("MDM_DUPLICATE_PRODUCT_CODE", "Product Code 已存在且不可复用", 409, {"stable_id": duplicate.stable_id})
        elif model is SKU:
            payload = validate_sku_payload(payload)
            confirmed = payload.pop("confirm_significant_change", False)
            changed_significant = sorted(
                field for field in SKU_SIGNIFICANT_FIELDS
                if field in payload and getattr(obj, field) != payload[field]
            )
            if changed_significant and not confirmed:
                error(
                    "MDM_SKU_SIGNIFICANT_CHANGE_CONFIRMATION_REQUIRED",
                    "规格、净重或箱规发生重大变化，需要显式确认",
                    409,
                    {"confirmation_required": True, "fields": changed_significant},
                )
        elif model in REFERENCE_MODELS:
            payload = validate_reference_payload(model, payload, create=False)
            ensure_unique_reference_code(model, obj, payload, db)
        else:
            payload.pop("stable_id", None)
        if model is Customer and payload.get("parent_customer_stable_id") == stable_id:
            error("MDM_SELF_PARENT", "Customer 不能成为自己的 parent", 422)
        audit_fields = list(payload)
        values = resolve_payload(model, payload, db)
        for key, value in values.items(): setattr(obj, key, value)
        try:
            db.flush()
            after = serialize(obj)
            audit_updates(db, obj, user["id"], before, after, audit_fields)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if model is Customer and is_customer_code_violation(exc):
                error("MDM_DUPLICATE_CUSTOMER_CODE", "客户编码已被其他 Customer 使用，不允许重复", 409)
            error("MDM_CONFLICT", "资源字段冲突", 409)
        return after

    for action, status_value, action_enum in (("activate", MasterStatus.ACTIVE, ChangeAction.ACTIVATE), ("deactivate", MasterStatus.INACTIVE, ChangeAction.DEACTIVATE)):
        @router.post(f"/{resource}/{{stable_id}}/{action}")
        def lifecycle(stable_id: str, payload: Optional[dict] = Body(default=None), _action=action, _status=status_value, _enum=action_enum, user=Depends(mdm_edit), db: Session = Depends(session)):
            obj = stable(model, db, stable_id, entity)
            lifecycle_payload = payload or {}
            if model in REFERENCE_MODELS:
                lifecycle_payload = validate_payload(ReferenceLifecyclePayload, lifecycle_payload, model.__name__)
            reference_count = 0
            if _action == "deactivate":
                reference_count = linked_reference_count(model, obj, db) if model in REFERENCE_MODELS else active_reference_count(model, obj, db)
                if model is Product and reference_count and not lifecycle_payload.get("confirm_active_skus"):
                    error(
                        "MDM_PRODUCT_ACTIVE_SKUS_CONFIRMATION_REQUIRED",
                        "Product 仍有 ACTIVE SKU，停用前需要显式确认",
                        409,
                        {"active_sku_count": reference_count, "confirmation_required": True},
                    )
                if model in REFERENCE_MODELS and reference_count and not lifecycle_payload.get("confirm_references"):
                    error(
                        f"MDM_{entity}_DEACTIVATE_CONFIRMATION_REQUIRED",
                        f"{model.__name__} 仍被既有记录引用，停用前需要显式确认",
                        409,
                        {"reference_count": reference_count, "active_reference_count": reference_count, "confirmation_required": True},
                    )
                if model not in REFERENCE_MODELS and model is not Product and reference_count:
                    error(f"MDM_{entity}_IN_USE", f"{model.__name__} 仍被 ACTIVE 记录引用", 409, {"active_reference_count": reference_count})
            if obj.status == _status:
                return {**serialize(obj), "changed": False}
            before = serialize(obj)
            old_status = before["status"]
            obj.status = _status
            db.flush()
            after = serialize(obj)
            audit(db, obj, ChangeAction.STATUS_CHANGE, user["id"], before, "status", old_status, after["status"], after)
            db.commit()
            response = {**after, "changed": True}
            if model is Product and reference_count:
                response["warnings"] = [{"code": "MDM_PRODUCT_ACTIVE_SKUS_RETAINED", "active_sku_count": reference_count}]
            if model in REFERENCE_MODELS and reference_count:
                response["warnings"] = [{"code": f"MDM_{entity}_REFERENCES_RETAINED", "reference_count": reference_count, "active_reference_count": reference_count}]
            return response


def next_stable(model, prefix, db):
    rows = db.scalars(select(model.stable_id).where(model.stable_id.like(prefix + "%"))).all()
    number = max([int(x[len(prefix):]) for x in rows if x[len(prefix):].isdigit()] or [0]) + 1
    return f"{prefix}{number:06d}"


def resolve_payload(model, payload, db):
    allowed = {c.name for c in model.__table__.columns} - {"id", "stable_id", "status", "created_at", "updated_at"}
    values = {k: v for k, v in payload.items() if k in allowed}
    for public, (field, target) in FK_FIELDS.items():
        if public in payload:
            if payload[public] is None:
                values[field] = None
            else:
                obj = stable(target, db, payload[public], target.__name__.upper())
                if obj.status != MasterStatus.ACTIVE:
                    error("MDM_INACTIVE_REFERENCE", "关系目标必须为 ACTIVE", 409)
                values[field] = obj.id
    return values


for _resource in RESOURCE:
    register(_resource)


@router.get("/products/{stable_id}/skus")
def product_skus(stable_id: str, user=Depends(mdm_view), db: Session = Depends(session)):
    product = stable(Product, db, stable_id, "PRODUCT")
    rows = db.scalars(
        select(SKU).where(SKU.product_id == product.id).order_by(SKU.sku_code)
    ).all()
    return {"items": [sku_summary(row) for row in rows], "total": len(rows)}


@router.post("/skus/{stable_id}/reassign-product")
def reassign_sku_product(
    stable_id: str,
    payload: SKUReassignmentPayload,
    user=Depends(mdm_edit),
    db: Session = Depends(session),
):
    sku = db.scalar(
        select(SKU).where(SKU.stable_id == stable_id).options(joinedload(SKU.product))
    )
    if not sku:
        error("MDM_SKU_NOT_FOUND", "SKU 不存在", 404, {"stable_id": stable_id})
    if not sku.product:
        error("MDM_SKU_PRODUCT_REQUIRED", "Formal SKU 必须属于一个 Product", 409)
    target = stable(Product, db, payload.product_stable_id, "PRODUCT")
    if target.status != MasterStatus.ACTIVE:
        error("MDM_INACTIVE_REFERENCE", "目标 Product 必须为 ACTIVE", 409, {"stable_id": target.stable_id})
    if sku.product_id == target.id:
        return {**serialize(sku), "changed": False, "source_product_empty": False}
    source_count = db.scalar(select(func.count()).select_from(SKU).where(SKU.product_id == sku.product_id))
    source_will_be_empty = source_count == 1
    if not payload.confirm:
        error(
            "MDM_SKU_REASSIGN_CONFIRMATION_REQUIRED",
            "SKU Product reassignment 需要显式确认",
            409,
            {
                "confirmation_required": True,
                "old_product_stable_id": sku.product.stable_id,
                "new_product_stable_id": target.stable_id,
                "source_product_will_be_empty": source_will_be_empty,
            },
        )
    before = serialize(sku)
    old_product_stable_id = sku.product.stable_id
    sku.product_id = target.id
    sku.product = target
    db.flush()
    after = serialize(sku)
    audit(
        db,
        sku,
        ChangeAction.RELATION_CHANGE,
        user["id"],
        before,
        "product_stable_id",
        old_product_stable_id,
        target.stable_id,
        after,
    )
    db.commit()
    response = {**after, "changed": True, "source_product_empty": source_will_be_empty}
    if source_will_be_empty:
        response["warnings"] = [{
            "code": "MDM_SOURCE_PRODUCT_ZERO_SKU",
            "message": "原 Product 已无 SKU，状态保持不变",
            "product_stable_id": old_product_stable_id,
        }]
    return response


def workflow_response(call):
    try:
        return call()
    except WorkflowError as exc:
        error(exc.code, exc.message, exc.http_status, exc.details)


@router.get("/import-batches/{batch_id}/review")
def import_review(batch_id: str, user=Depends(mdm_view), db: Session = Depends(session)):
    return workflow_response(lambda: ReviewQueryService(db).get(batch_id))


@router.post("/import-batches/{batch_id}/resolutions")
def import_resolution(batch_id: str, payload: dict = Body(...), user=Depends(mdm_edit), db: Session = Depends(session)):
    return workflow_response(lambda: ResolutionService(db).resolve(batch_id, payload, user["id"]))


@router.post("/import-batches/{batch_id}/acknowledgements")
def import_acknowledgement(batch_id: str, payload: dict = Body(...), user=Depends(mdm_edit), db: Session = Depends(session)):
    return workflow_response(lambda: ResolutionService(db).acknowledge(batch_id, payload, user["id"]))


@router.post("/import-batches/{batch_id}/confirm")
def import_confirm(batch_id: str, payload: dict = Body(...), user=Depends(mdm_edit), db: Session = Depends(session)):
    return workflow_response(lambda: ConfirmationService(db).confirm(batch_id, payload, user["id"]))


@router.post("/import-batches/{batch_id}/commit")
def import_commit(batch_id: str, user=Depends(mdm_edit), db: Session = Depends(session)):
    return workflow_response(lambda: BootstrapCommitService(db).commit(batch_id, user["id"]))


@router.post("/import-batches/{batch_id}/cancel")
def import_cancel(batch_id: str, user=Depends(mdm_edit), db: Session = Depends(session)):
    return workflow_response(lambda: ConfirmationService(db).cancel(batch_id, user["id"]))
