"""Sales Actual V1 Import Preview + Publish API."""
from datetime import date
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from webapp.account_permissions import (
    EDIT,
    MODULE_SALES_ACTUAL,
    VIEW,
    require_page_permission,
    require_permission,
)
from webapp.auth.dependencies import current_user
from webapp.mdm.database import get_session_factory
from webapp.mdm.models import Product, SKU
from webapp.sales.dashboard_query_service import (
    CurrentBatchAmbiguous,
    DashboardBatchNotFound,
    DashboardFactIntegrityError,
    DashboardFilters,
    DashboardSnapshotChanged,
    get_channel_product,
    get_filter_options,
    get_product_aggregation,
    get_product_sku_drilldown,
    get_salesrep_product,
    get_summary,
    list_available_months,
)
from webapp.sales.models import SalesMappingReason, SalesRowStatus
from webapp.sales.parser import SalesParserBlocked
from webapp.sales.preview_service import (
    DuplicateSalesFile,
    create_sales_preview,
    list_sales_batches,
    read_sales_aggregate,
    read_sales_preview,
    read_sales_rows,
)
from webapp.sales.publish_service import (
    PublishBatchSuperseded,
    PublishConfirmationRequired,
    PublishGateBlocked,
    PublishLockBusy,
    PublishNotFound,
    PublishValidationError,
    publish_batch,
)


router = APIRouter(prefix="/api/v1/sales/actual", tags=["sales-actual"])
pages = APIRouter()
sales_viewer = require_permission(MODULE_SALES_ACTUAL, VIEW, current_user)
sales_editor = require_permission(MODULE_SALES_ACTUAL, EDIT, current_user)
sales_page_viewer = require_page_permission(MODULE_SALES_ACTUAL, VIEW)
DASHBOARD_SOURCE_SYSTEM = "DEMO_ERP"
DASHBOARD_QUERY_INVALID = "DASHBOARD_QUERY_INVALID"


class PublishConfirmationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str


class PublishRequest(BaseModel):
    """Client confirmations. confirmed_by is never accepted from the client."""

    model_config = ConfigDict(extra="forbid")
    confirmations: list[PublishConfirmationIn] = Field(default_factory=list)


def _actor(user) -> str:
    """Stable identity from the authenticated context (never from the body)."""
    if isinstance(user, dict):
        return str(user.get("username") or user.get("id") or "")
    return str(user or "")


def _gate_detail(payload) -> dict:
    detail = dict(payload or {})
    detail["gate_status"] = detail.get("status")
    return detail


def _not_found(value):
    if value is None:
        raise HTTPException(404, {"code": "SALES_BATCH_NOT_FOUND"})
    return value


def _dashboard_query_error(field: str, value, reason: str):
    raise HTTPException(422, detail={
        "code": DASHBOARD_QUERY_INVALID,
        "errors": [{"field": field, "value": value, "reason": reason}],
    })


def _parse_month(value: Optional[str]) -> Optional[date]:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        _dashboard_query_error("month", value, "EXPECTED_YYYY_MM_01")
    if parsed.day != 1 or value != parsed.isoformat():
        _dashboard_query_error("month", value, "EXPECTED_YYYY_MM_01")
    return parsed


def _parse_dimension(values, field: str, *, allow_unassigned: bool):
    result = []
    for value in values or ():
        normalized = value.strip().lower()
        if allow_unassigned and normalized == "unassigned":
            parsed = None
        else:
            if normalized in {"", "null", "none", "unassigned"}:
                reason = "USE_UNASSIGNED" if allow_unassigned else "NULL_NOT_ALLOWED"
                _dashboard_query_error(field, value, reason)
            try:
                parsed = int(normalized)
            except ValueError:
                _dashboard_query_error(field, value, "EXPECTED_POSITIVE_INTEGER")
            if parsed <= 0:
                _dashboard_query_error(field, value, "EXPECTED_POSITIVE_INTEGER")
        if parsed not in result:
            result.append(parsed)
    return tuple(result)


def _resolve_stable_dimensions(model, values, field: str):
    stable_ids = []
    for value in values or ():
        normalized = value.strip()
        if not normalized:
            _dashboard_query_error(field, value, "BLANK_NOT_ALLOWED")
        if normalized not in stable_ids:
            stable_ids.append(normalized)
    if not stable_ids:
        return ()
    with get_session_factory()() as db:
        rows = db.execute(
            select(model.stable_id, model.id).where(model.stable_id.in_(stable_ids))
        ).all()
    resolved = {stable_id: internal_id for stable_id, internal_id in rows}
    missing = next((stable_id for stable_id in stable_ids if stable_id not in resolved), None)
    if missing is not None:
        _dashboard_query_error(field, missing, "STABLE_ID_NOT_FOUND")
    return tuple(resolved[stable_id] for stable_id in stable_ids)


def _merge_dimensions(*groups):
    return tuple(dict.fromkeys(value for group in groups for value in group))


def _dashboard_filters(
    product_filter: Optional[list[str]] = Query(
        default=None, alias="product_id",
        description="Repeatable Product ID; null is not allowed."
    ),
    sku_filter: Optional[list[str]] = Query(
        default=None, alias="sku_id",
        description="Repeatable SKU ID; null is not allowed."
    ),
    channel_filter: Optional[list[str]] = Query(
        default=None, alias="channel_id",
        description="Repeatable Channel ID or the literal 'unassigned'."
    ),
    salesrep_filter: Optional[list[str]] = Query(
        default=None, alias="salesrep_id",
        description="Repeatable SalesRep ID or the literal 'unassigned'."
    ),
    product_stable_filter: Optional[list[str]] = Query(
        default=None, alias="product_stable_id",
        description="Repeatable exact MDM Product stable ID."
    ),
    sku_stable_filter: Optional[list[str]] = Query(
        default=None, alias="sku_stable_id",
        description="Repeatable exact MDM SKU stable ID."
    ),
):
    return DashboardFilters(
        product_ids=_merge_dimensions(
            _parse_dimension(product_filter, "product_id", allow_unassigned=False),
            _resolve_stable_dimensions(Product, product_stable_filter, "product_stable_id"),
        ),
        sku_ids=_merge_dimensions(
            _parse_dimension(sku_filter, "sku_id", allow_unassigned=False),
            _resolve_stable_dimensions(SKU, sku_stable_filter, "sku_stable_id"),
        ),
        channel_ids=_parse_dimension(channel_filter, "channel_id", allow_unassigned=True),
        salesrep_ids=_parse_dimension(salesrep_filter, "salesrep_id", allow_unassigned=True),
    )


def _dashboard_call(operation: Callable, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except DashboardBatchNotFound as exc:
        raise HTTPException(404, detail=exc.payload) from exc
    except (
        CurrentBatchAmbiguous,
        DashboardSnapshotChanged,
        DashboardFactIntegrityError,
    ) as exc:
        raise HTTPException(409, detail=exc.payload) from exc


def _pagination(page: str, limit: str):
    try:
        parsed_page = int(page)
    except (TypeError, ValueError):
        _dashboard_query_error("page", page, "EXPECTED_POSITIVE_INTEGER")
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        _dashboard_query_error("limit", limit, "EXPECTED_INTEGER_1_TO_200")
    if str(parsed_page) != page or parsed_page < 1:
        _dashboard_query_error("page", page, "EXPECTED_POSITIVE_INTEGER")
    if str(parsed_limit) != limit or not 1 <= parsed_limit <= 200:
        _dashboard_query_error("limit", limit, "EXPECTED_INTEGER_1_TO_200")
    return parsed_page, parsed_limit


def _sort(sort: str, allowed: set[str]):
    key = sort[1:] if sort.startswith("-") else sort
    if not key or key not in allowed:
        _dashboard_query_error("sort", sort, "UNSUPPORTED_SORT")
    return sort


def _api_share(result: dict, field: str, denominator: str):
    result = dict(result)
    result["share_denominator"] = denominator
    result["items"] = [
        {**{key: value for key, value in item.items() if key != "share"},
         field: item["share"]}
        for item in result["items"]
    ]
    return result


@router.post("/import-batches", status_code=201)
def upload_import_batch(
    file: UploadFile = File(...),
    sheet_name: Optional[str] = Query(default=None),
    user=Depends(sales_editor),
):
    original_name = Path(file.filename or "upload.xlsx").name
    suffix = Path(original_name).suffix or ".bin"
    try:
        with tempfile.TemporaryDirectory(prefix="sales-preview-") as directory:
            path = Path(directory) / ("source" + suffix)
            with path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            created = create_sales_preview(
                get_session_factory(), path, sheet_name=sheet_name, filename=original_name
            )
    except SalesParserBlocked as exc:
        raise HTTPException(422, {
            "code": "SALES_PARSER_BLOCKED",
            "errors": [{
                "code": error.code.value,
                "message": error.message,
                "row_number": error.row_number,
                "field": error.field,
            } for error in exc.result.errors],
        }) from exc
    except DuplicateSalesFile as exc:
        raise HTTPException(409, {
            "code": "DUPLICATE_SOURCE_FILE",
            "batch_id": exc.batch_id,
            "status": exc.status.value,
        }) from exc
    return {
        "batch_id": created.batch_id,
        "status": "PREVIEW_READY",
        "source_file_sha256": created.source_file_sha256,
        "snapshot_month": created.snapshot_month.isoformat(),
        "raw_rows": created.raw_rows,
        "ready_rows": created.ready_rows,
        "skipped_rows": created.skipped_rows,
        "ready_qty": str(created.ready_qty),
        "skipped_qty": str(created.skipped_qty),
        "preview_url": f"/sales/actual/import-batches/{created.batch_id}/preview",
    }


@router.get("/import-batches")
def batches(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(sales_viewer),
):
    return list_sales_batches(get_session_factory(), offset=offset, limit=limit)


@router.get("/import-batches/{batch_id}")
def batch_detail(batch_id: str, user=Depends(sales_viewer)):
    return _not_found(read_sales_preview(get_session_factory(), batch_id))["batch"]


@router.get("/import-batches/{batch_id}/preview")
def preview(batch_id: str, user=Depends(sales_viewer)):
    return _not_found(read_sales_preview(get_session_factory(), batch_id))


@router.post("/import-batches/{batch_id}/publish")
def publish_import_batch(
    batch_id: str,
    payload: PublishRequest,
    user=Depends(sales_editor),
):
    """Publish one PREVIEW_READY batch. Gate judgment lives in publish_service."""
    actor = _actor(user)
    if not actor:
        raise HTTPException(401, {"code": "SALES_AUTH_IDENTITY_MISSING"})
    try:
        result = publish_batch(
            get_session_factory(),
            batch_id,
            confirmations=[item.code for item in payload.confirmations],
            user=actor,
        )
    except PublishNotFound as exc:
        raise HTTPException(404, {"code": "SALES_BATCH_NOT_FOUND"}) from exc
    except PublishLockBusy as exc:
        raise HTTPException(423, detail=exc.payload or {"code": "PUBLISH_IN_PROGRESS"}) from exc
    except PublishBatchSuperseded as exc:
        raise HTTPException(409, detail=exc.payload) from exc
    except PublishConfirmationRequired as exc:
        raise HTTPException(409, detail=_gate_detail(exc.payload)) from exc
    except PublishGateBlocked as exc:
        raise HTTPException(409, detail=_gate_detail(exc.payload)) from exc
    except PublishValidationError as exc:
        raise HTTPException(422, detail=exc.payload or {"code": exc.code or "INVALID_PUBLISH_REQUEST"}) from exc
    return {
        "batch_id": result.batch_id,
        "status": result.status,
        "idempotent": result.idempotent,
        "published_at": result.published_at.isoformat() if result.published_at else None,
        "snapshot_month": result.snapshot_month.isoformat() if result.snapshot_month else None,
        "data_end_date": result.data_end_date.isoformat() if result.data_end_date else None,
        "fact_rows": result.fact_rows,
        "fact_qty": str(result.fact_qty),
        "replaced_batch_id": result.replaced_batch_id,
        "warnings": list(result.warnings),
        "confirmations_recorded": list(result.confirmations_recorded),
    }


@router.get("/import-batches/{batch_id}/rows")
def rows(
    batch_id: str,
    status: Optional[SalesRowStatus] = None,
    mapping_reason: Optional[SalesMappingReason] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(sales_viewer),
):
    return _not_found(read_sales_rows(
        get_session_factory(), batch_id,
        status=status, mapping_reason=mapping_reason, offset=offset, limit=limit,
    ))


@router.get("/import-batches/{batch_id}/aggregates/{dimension}")
def aggregates(
    batch_id: str,
    dimension: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(sales_viewer),
):
    try:
        result = read_sales_aggregate(
            get_session_factory(), batch_id, dimension, offset=offset, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(422, {"code": "INVALID_AGGREGATE_DIMENSION"}) from exc
    return _not_found(result)


@router.get("/dashboard/context")
def dashboard_context(
    month: Optional[str] = Query(
        default=None, description="Optional snapshot month in strict YYYY-MM-01 format."
    ),
    user=Depends(sales_viewer),
):
    """Resolve the requested month, or the latest current Published Fact month."""
    requested_month = _parse_month(month)
    available = _dashboard_call(
        list_available_months, get_session_factory(), DASHBOARD_SOURCE_SYSTEM
    )
    selected = None
    if requested_month is None:
        selected = available[0] if available else None
    else:
        requested = requested_month.isoformat()
        selected = next(
            (item for item in available if item["snapshot_month"] == requested), None
        )
    return {
        "available_months": [item["snapshot_month"] for item in available],
        "snapshot_month": (
            selected["snapshot_month"] if selected else
            requested_month.isoformat() if requested_month else None
        ),
        "data_end_date": selected["data_date_end"] if selected else None,
        "current_batch": selected,
    }


@router.get("/dashboard/summary")
def dashboard_summary(
    batch_id: str = Query(..., description="Current Published batch ID."),
    filters: DashboardFilters = Depends(_dashboard_filters),
    user=Depends(sales_viewer),
):
    return _dashboard_call(
        get_summary, get_session_factory(), batch_id, filters
    )


@router.get("/dashboard/filter-options")
def dashboard_filter_options(
    batch_id: str = Query(..., description="Current Published batch ID."),
    include_product_sku: bool = Query(
        default=True,
        description="Keep legacy Product/SKU options unless the shared finder is used.",
    ),
    filters: DashboardFilters = Depends(_dashboard_filters),
    user=Depends(sales_viewer),
):
    """Return cascading options after applying all supplied snapshot filters."""
    return _dashboard_call(
        get_filter_options, get_session_factory(), batch_id, filters,
        include_product_sku=include_product_sku,
    )


@router.get("/dashboard/products")
def dashboard_products(
    batch_id: str = Query(..., description="Current Published batch ID."),
    page: str = Query(default="1"),
    limit: str = Query(default="50"),
    sort: str = Query(
        default="-qty",
        description="qty, share, sku_count, or product_name; prefix - for descending.",
    ),
    filters: DashboardFilters = Depends(_dashboard_filters),
    user=Depends(sales_viewer),
):
    """Product share is relative to the current filtered result quantity."""
    parsed_page, parsed_limit = _pagination(page, limit)
    parsed_sort = _sort(sort, {"qty", "share", "sku_count", "product_name"})
    result = _dashboard_call(
        get_product_aggregation,
        get_session_factory(), batch_id, filters,
        sort=parsed_sort, page=parsed_page, page_size=parsed_limit,
    )
    return _api_share(
        result, "share_of_filtered_result", "FILTERED_RESULT_QTY"
    )


@router.get("/dashboard/products/{product_id}/skus")
def dashboard_product_skus(
    product_id: int,
    batch_id: str = Query(..., description="Current Published batch ID."),
    page: str = Query(default="1"),
    limit: str = Query(default="50"),
    sort: str = Query(
        default="-qty",
        description="qty, share, sku_code, or sku_name; prefix - for descending.",
    ),
    filters: DashboardFilters = Depends(_dashboard_filters),
    user=Depends(sales_viewer),
):
    """SKU share is relative to this Product after all other filters."""
    if product_id <= 0:
        _dashboard_query_error("product_id", product_id, "EXPECTED_POSITIVE_INTEGER")
    parsed_page, parsed_limit = _pagination(page, limit)
    parsed_sort = _sort(sort, {"qty", "share", "sku_code", "sku_name"})
    result = _dashboard_call(
        get_product_sku_drilldown,
        get_session_factory(), batch_id, product_id, filters,
        sort=parsed_sort, page=parsed_page, page_size=parsed_limit,
    )
    return _api_share(result, "share_of_product", "CURRENT_PRODUCT_QTY")


@router.get("/dashboard/salesrep-products")
def dashboard_salesrep_products(
    batch_id: str = Query(..., description="Current Published batch ID."),
    page: str = Query(default="1"),
    limit: str = Query(default="50"),
    sort: str = Query(
        default="-qty",
        description="qty, share, salesrep_name, or product_name; prefix - for descending.",
    ),
    filters: DashboardFilters = Depends(_dashboard_filters),
    user=Depends(sales_viewer),
):
    """Each cell's share is relative to the current filtered result quantity."""
    parsed_page, parsed_limit = _pagination(page, limit)
    parsed_sort = _sort(sort, {"qty", "share", "salesrep_name", "product_name"})
    result = _dashboard_call(
        get_salesrep_product,
        get_session_factory(), batch_id, filters,
        sort=parsed_sort, page=parsed_page, page_size=parsed_limit,
    )
    return _api_share(
        result, "share_of_filtered_result", "FILTERED_RESULT_QTY"
    )


@router.get("/dashboard/channel-products")
def dashboard_channel_products(
    batch_id: str = Query(..., description="Current Published batch ID."),
    page: str = Query(default="1"),
    limit: str = Query(default="50"),
    sort: str = Query(
        default="-qty",
        description="qty, share, channel_name, or product_name; prefix - for descending.",
    ),
    filters: DashboardFilters = Depends(_dashboard_filters),
    user=Depends(sales_viewer),
):
    """Each cell's share is relative to the current filtered result quantity."""
    parsed_page, parsed_limit = _pagination(page, limit)
    parsed_sort = _sort(sort, {"qty", "share", "channel_name", "product_name"})
    result = _dashboard_call(
        get_channel_product,
        get_session_factory(), batch_id, filters,
        sort=parsed_sort, page=parsed_page, page_size=parsed_limit,
    )
    return _api_share(
        result, "share_of_filtered_result", "FILTERED_RESULT_QTY"
    )


@pages.get("/sales/actual")
@pages.get("/sales/actual/import")
@pages.get("/sales/actual/import-batches/{batch_id}/preview")
def sales_actual_page(
    batch_id: Optional[str] = None,
    user=Depends(sales_page_viewer),
):
    return FileResponse(
        Path(__file__).resolve().parents[1] / "static" / "sales-actual.html",
        headers={"Cache-Control": "no-store"},
    )
