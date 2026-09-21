"""Lab API for the ACT Sales Semantic Layer spike.

Exposes three metadata/query endpoints and one page route.  The query endpoint is
the *only* execution path: every preset, every Explorer interaction and every
future metric/dimension combination goes through the same
``POST /api/v1/lab/sales/query`` call.  There is deliberately no endpoint per view.

Security posture
----------------
* Every endpoint requires the same ``MODULE_SALES_ACTUAL`` VIEW permission as the
  production dashboard; the page route additionally passes the page gate.
* The request body is validated against the semantic registry, never against the
  database.  SQL, model attributes and column names cannot be supplied by the
  client, and the response never echoes SQL or a column object.
* Only the current Published batch is readable, enforced by the shared
  ``_validated_batch`` batch guard in ``dashboard_query_service``.
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
from webapp.sales.semantic_query_engine import (
    SemanticQueryError,
    get_filter_options,
    run_query,
    validate_query,
)
from webapp.sales.semantic_registry import (
    DATASET_SALES_ACTUAL,
    get_all_datasets,
    get_dataset_metadata,
)


router = APIRouter(prefix="/api/v1/lab/sales", tags=["sales-semantic-lab"])
pages = APIRouter()

sales_semantic_viewer = require_permission(MODULE_SALES_ACTUAL, VIEW, current_user)
sales_semantic_page_viewer = require_page_permission(MODULE_SALES_ACTUAL, VIEW)

SEMANTIC_QUERY_FORBIDDEN = "SEMANTIC_QUERY_FORBIDDEN"


class SemanticSortIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    direction: str = "desc"


class SemanticFilterIn(BaseModel):
    """A filter targets a registry dimension key, never a column."""

    model_config = ConfigDict(extra="forbid")
    dimension: str
    operator: str
    values: Optional[list[Any]] = None
    value: Optional[Any] = None


class SemanticQueryIn(BaseModel):
    """The spike request body. ``dataset`` is optional and defaults to sales_actual."""

    model_config = ConfigDict(extra="forbid")
    dataset: str = DATASET_SALES_ACTUAL
    batch_id: str
    metrics: list[str] = Field(default_factory=lambda: ["actual_sales"])
    dimensions: list[str] = Field(default_factory=list)
    filters: list[SemanticFilterIn] = Field(default_factory=list)
    sort: list[SemanticSortIn] = Field(default_factory=list)
    limit: int = 100
    offset: int = 0


def _connection_error(exc: DashboardQueryError) -> HTTPException:
    """Reuse the production batch-guard semantics for lab queries."""
    from webapp.sales.dashboard_query_service import (
        CurrentBatchAmbiguous,
        DashboardBatchNotFound,
        DashboardFactIntegrityError,
        DashboardSnapshotChanged,
    )

    if isinstance(exc, DashboardBatchNotFound):
        return HTTPException(404, detail=exc.payload)
    if isinstance(
        exc, (CurrentBatchAmbiguous, DashboardSnapshotChanged, DashboardFactIntegrityError)
    ):
        return HTTPException(409, detail=exc.payload)
    return HTTPException(400, detail=exc.payload)


def _guard(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except SemanticQueryError as exc:
        raise HTTPException(422, detail=exc.payload()) from exc
    except DashboardQueryError as exc:
        raise _connection_error(exc) from exc


@router.get("/semantic")
def semantic_metadata(user=Depends(sales_semantic_viewer)):
    """Metric / dimension / preset metadata. Never returns SQL or column objects."""
    metadata = get_dataset_metadata(DATASET_SALES_ACTUAL)
    if metadata is None:
        raise HTTPException(500, detail={"code": "SEMANTIC_REGISTRY_MISSING"})
    return {
        "dataset": metadata["dataset"],
        "label": metadata["label"],
        "description": metadata["description"],
        "metrics": metadata["metrics"],
        "dimensions": metadata["dimensions"],
        "presets": metadata["presets"],
        "future_dimensions": metadata["future_dimensions"],
        "guardrails": metadata["guardrails"],
        "datasets": sorted(get_all_datasets().keys()),
    }


@router.post("/query")
def semantic_query(body: SemanticQueryIn, user=Depends(sales_semantic_viewer)):
    """The single generic query entry point for every semantic view."""
    payload = body.model_dump(exclude_none=True)
    query = _guard(validate_query, payload)
    return _guard(run_query, get_session_factory(), query)


@router.get("/filter-options")
def semantic_filter_options(
    batch_id: str,
    dimensions: Optional[str] = None,
    user=Depends(sales_semantic_viewer),
):
    """Distinct snapshot values per dimension. Reads Fact snapshot columns only."""
    requested = tuple(
        item.strip() for item in (dimensions or "").split(",") if item.strip()
    )
    return _guard(get_filter_options, get_session_factory(), batch_id, requested)


@pages.get("/lab/sales-semantic-layer")
def sales_semantic_layer_page(user=Depends(sales_semantic_page_viewer)):
    """Isolated lab page for the semantic layer spike."""
    return FileResponse(
        Path(__file__).resolve().parents[1] / "static" / "sales-semantic-demo.html",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Actual Sales V2 — lightweight analytics surface (TASK-ASV2-001)
# ---------------------------------------------------------------------------
# The V2 page is a thin, production-shaped surface over the *same* registry and
# engine as the lab spike: no new query logic, no second execution path.  The
# three handlers are re-registered under a production prefix so the V2 page does
# not depend on a lab URL.  Exactly one execution endpoint exists per prefix.

analytics_router = APIRouter(
    prefix="/api/v1/sales/actual/analytics", tags=["sales-actual-analytics"]
)


def _analytics_register() -> None:
    analytics_router.add_api_route(
        "/metadata", semantic_metadata, methods=["GET"], name="analytics_metadata"
    )
    analytics_router.add_api_route(
        "/query", semantic_query, methods=["POST"], name="analytics_query"
    )
    analytics_router.add_api_route(
        "/filter-options",
        semantic_filter_options,
        methods=["GET"],
        name="analytics_filter_options",
    )


_analytics_register()


@pages.get("/sales/actual/analytics")
def sales_actual_analytics_page(user=Depends(sales_semantic_page_viewer)):
    """Actual Sales V2 — global filters, Product table, BP Channel bar chart."""
    return FileResponse(
        Path(__file__).resolve().parents[1] / "static" / "sales-analytics.html",
        headers={"Cache-Control": "no-store"},
    )
