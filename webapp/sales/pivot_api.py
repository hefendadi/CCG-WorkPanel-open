"""HTTP surface for the Sales Pivot Workbench (UX / Architecture Demo).

Three endpoints behind the same ``sales_actual`` VIEW permission as the
production dashboard:

* ``GET  /api/v1/sales/actual/pivot/fields``          field catalog (+ sample-data flags)
* ``GET  /api/v1/sales/actual/pivot/filter-options``  distinct values of any filterable field
* ``POST /api/v1/sales/actual/pivot/query``           the single execution endpoint

The page route ``/sales/actual/workbench`` serves the plain-HTML workbench.  There
is deliberately no endpoint per report: every row/column combination the user
builds is executed by the same validated query endpoint.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from webapp.account_permissions import (
    MODULE_SALES_ACTUAL,
    VIEW,
    require_page_permission,
    require_permission,
)
from webapp.auth.dependencies import current_user
from webapp.mdm.database import get_session_factory
from webapp.sales.dashboard_query_service import DashboardQueryError
from webapp.sales.pivot_catalog import get_catalog_metadata
from webapp.sales.pivot_query_engine import (
    PivotQueryError,
    field_availability,
    get_filter_options,
    run_query,
    validate_query,
)
from webapp.sales.semantic_api import _connection_error


router = APIRouter(prefix="/api/v1/sales/actual/pivot", tags=["sales-pivot-workbench"])
pages = APIRouter()

pivot_viewer = require_permission(MODULE_SALES_ACTUAL, VIEW, current_user)
pivot_page_viewer = require_page_permission(MODULE_SALES_ACTUAL, VIEW)

MAX_FILTER_FIELDS = 12


class PivotFilterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    operator: str = "eq"
    value: Optional[Any] = None
    values: Optional[list[Any]] = None


class PivotSortIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = "actual_sales"
    direction: str = "desc"


class PivotTimeIn(BaseModel):
    """Business-time selector. ``mtd`` is resolved server-side from the batch."""

    model_config = ConfigDict(extra="forbid")
    mode: str = "all"
    start: Optional[str] = None
    end: Optional[str] = None


class PivotQueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=lambda: ["actual_sales"])
    filters: list[PivotFilterIn] = Field(default_factory=list)
    sort: list[PivotSortIn] = Field(default_factory=list)
    limit: int = 100
    offset: int = 0
    time: Optional[PivotTimeIn] = None
    subtotals: list[str] = Field(default_factory=list)
    grand_total: bool = True


def _guard(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except PivotQueryError as exc:
        raise HTTPException(422, detail=exc.payload()) from exc
    except DashboardQueryError as exc:
        raise _connection_error(exc) from exc


@router.get("/fields")
def pivot_fields(batch_id: Optional[str] = None, user=Depends(pivot_viewer)):
    """Field catalog. With ``batch_id`` every field also reports sample coverage."""
    metadata = get_catalog_metadata()
    metadata["fields_with_sample_data"] = None
    metadata["fields_without_sample_data"] = None
    if batch_id:
        availability = _guard(field_availability, get_session_factory(), batch_id)
        for item in metadata["fields"]:
            item["has_sample_data"] = availability.get(item["key"], False)
        for group in metadata["groups"]:
            for item in group["fields"]:
                item["has_sample_data"] = availability.get(item["key"], False)
        metadata["fields_with_sample_data"] = sorted(
            key for key, present in availability.items() if present
        )
        metadata["fields_without_sample_data"] = sorted(
            key for key, present in availability.items() if not present
        )
    return metadata


@router.get("/filter-options")
def pivot_filter_options(batch_id: str, fields: str = "", user=Depends(pivot_viewer)):
    """Distinct values for the requested fields, read inside the current batch."""
    keys = tuple(item.strip() for item in fields.split(",") if item.strip())
    if len(keys) > MAX_FILTER_FIELDS:
        raise HTTPException(
            422,
            detail={
                "code": "PIVOT_QUERY_INVALID",
                "message": f"一次最多读取 {MAX_FILTER_FIELDS} 个字段的筛选值",
                "details": {"max": MAX_FILTER_FIELDS},
            },
        )
    return _guard(get_filter_options, get_session_factory(), batch_id, keys)


@router.post("/query")
def pivot_query(body: PivotQueryIn, user=Depends(pivot_viewer)):
    """The single execution entry point for every workbench combination."""
    payload = body.model_dump(exclude_none=True)
    query = _guard(validate_query, payload)
    return _guard(run_query, get_session_factory(), query)


@pages.get("/sales/actual/workbench")
def sales_pivot_workbench_page(user=Depends(pivot_page_viewer)):
    """销售分析工作台 — generic field-driven pivot demo."""
    return FileResponse(
        Path(__file__).resolve().parents[1] / "static" / "sales-pivot.html",
        headers={"Cache-Control": "no-store"},
    )
