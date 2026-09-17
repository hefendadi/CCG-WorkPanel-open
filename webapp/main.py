"""CCGtools modern application composition and MDM workspace pages."""
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import account_store
from .auth.account_store import db
from .auth.service import (
    PORTAL_COOKIE_NAME, create_token, decode_token, signing_secret,
)
from .auth.router import router as auth_router
from .portal import router as portal_router
from .account_permissions import (
    MODULE_MDM,
    VIEW,
    migrate_account_db,
    require_page_permission,
)
from .mdm.api import router as mdm_router
from .mdm.customer_import.api import router as customer_import_router
from .mdm.customer_import import ui_contract as customer_import_ui_contract
from .mdm.product_import.api import router as product_import_router
from .mdm.product_import import ui_contract as product_import_ui_contract
from .mdm.sku_import.api import router as sku_import_router
from .mdm.sku_import import ui_contract as sku_import_ui_contract
from .mdm.goods_import.api import router as goods_import_router
from .mdm.goods_import import ui_contract as goods_import_ui_contract
from .mdm.database import dispose_shared_engine
from .sales.api import router as sales_actual_router, pages as sales_actual_pages
from .ordering.api import router as ordering_router, pages as ordering_pages

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="CCGtools-open", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(mdm_router)
app.include_router(customer_import_router)
app.include_router(product_import_router)
app.include_router(sku_import_router)
app.include_router(goods_import_router)
app.include_router(sales_actual_router)
app.include_router(sales_actual_pages)
app.include_router(ordering_router)
app.include_router(ordering_pages)
app.include_router(auth_router)
app.include_router(portal_router)
app.add_event_handler("shutdown", dispose_shared_engine)


@app.exception_handler(HTTPException)
async def mdm_http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/v1/mdm"):
        default_code = (
            "AUTH_UNAUTHORIZED" if exc.status_code == 401
            else "MDM_FORBIDDEN" if exc.status_code == 403
            else "MDM_HTTP_ERROR"
        )
        detail = exc.detail if isinstance(exc.detail, dict) and "error" in exc.detail else {
            "error": {"code": default_code, "message": str(exc.detail), "details": {}}
        }
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def mdm_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/v1/mdm"):
        return JSONResponse(status_code=422, content={"error": {
            "code": "MDM_VALIDATION_ERROR", "message": "请求字段校验失败",
            "details": {"errors": exc.errors()}
        }})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def mdm_internal_exception_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/v1/mdm"):
        return JSONResponse(status_code=500, content={"error": {
            "code": "MDM_INTERNAL_ERROR", "message": "MDM 服务内部错误", "details": {}
        }})
    raise exc


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/static", "/mdm")) or request.url.path in (
            "/", "/login", "/admin/users",
        ):
            response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(NoCacheMiddleware)

# Reuse one dependency object so page tests can override the page gate while
# API authorization remains active.
mdm_page_viewer = require_page_permission(MODULE_MDM, VIEW)


def ensure_tables():
    conn = db()
    try:
        migrate_account_db(conn)
        account_store.ensure_operation_log(conn)
        conn.commit()
    finally:
        conn.close()


SECRET = signing_secret()
ensure_tables()


# ---------- 页面 ----------
@app.get("/mdm", response_class=HTMLResponse)
@app.get("/mdm/customers", response_class=HTMLResponse)
@app.get("/mdm/customers/new", response_class=HTMLResponse)
@app.get("/mdm/products", response_class=HTMLResponse)
@app.get("/mdm/products/new", response_class=HTMLResponse)
@app.get("/mdm/skus", response_class=HTMLResponse)
@app.get("/mdm/references/{resource}", response_class=HTMLResponse)
@app.get("/mdm/references/{resource}/new", response_class=HTMLResponse)
def mdm_page(
    resource: str = None,
    user=Depends(mdm_page_viewer),
):
    return mdm_workspace_page()


@app.get("/mdm/customers/{stable_id}", response_class=HTMLResponse)
@app.get("/mdm/products/{stable_id}", response_class=HTMLResponse)
@app.get("/mdm/skus/{stable_id}", response_class=HTMLResponse)
@app.get("/mdm/references/{resource}/{stable_id}", response_class=HTMLResponse)
def mdm_detail_page(
    stable_id: str,
    resource: str = None,
    user=Depends(mdm_page_viewer),
):
    return mdm_workspace_page()


def customer_import_ui_manifest():
    """Frozen UI-only vocabulary injected from the canonical public contract contract."""
    contract = customer_import_ui_contract
    return {
        "routes": contract.PAGE_ROUTES,
        "batch_status": contract.BATCH_STATUS_COPY,
        "batch_action": contract.BATCH_ACTION_COPY,
        "row_status": contract.ROW_STATUS_COPY,
        "issue_copy": contract.ISSUE_COPY,
        "tab_labels": contract.TAB_LABELS,
        "tab_status": contract.TAB_STATUS_MAP,
        "reviewable_statuses": list(contract.REVIEWABLE_STATUSES),
        "roles": {"operator": contract.ROLE_OPERATOR, "admin": contract.ROLE_ADMIN},
        "copy": {
            "upload_validated": contract.COPY_UPLOAD_VALIDATED,
            "upload_file_rejected": contract.COPY_UPLOAD_FILE_REJECTED,
            "upload_server_error": contract.COPY_UPLOAD_SERVER_ERROR,
            "upload_not_xlsx": contract.COPY_UPLOAD_NOT_XLSX,
            "review_load_error": contract.COPY_REVIEW_LOAD_ERROR,
            "review_not_found": contract.COPY_REVIEW_NOT_FOUND,
            "acknowledge_success": contract.COPY_ACKNOWLEDGE_SUCCESS,
            "acknowledge_stale": contract.COPY_ACKNOWLEDGE_STALE,
            "acknowledge_error": contract.COPY_ACKNOWLEDGE_ERROR,
            "error_first_blocked": contract.COPY_ERROR_FIRST_BLOCKED,
            "error_first_no_confirm": contract.COPY_ERROR_FIRST_NO_CONFIRM,
            "commit_not_ready": contract.COPY_COMMIT_NOT_READY,
            "commit_forbidden": contract.COPY_COMMIT_FORBIDDEN,
            "commit_server_error": contract.COPY_COMMIT_SERVER_ERROR,
        },
    }


def customer_import_page():
    return mdm_workspace_page()


@app.get("/mdm/import-center", response_class=HTMLResponse)
@app.get("/mdm/import-center/upload", response_class=HTMLResponse)
@app.get("/mdm/import-center/batches/{batch_id}/review", response_class=HTMLResponse)
@app.get("/mdm/import-center/batches/{batch_id}/commit", response_class=HTMLResponse)
def customer_import_page_route(
    batch_id: str = None,
    user=Depends(mdm_page_viewer),
):
    return customer_import_page()


def product_import_ui_manifest():
    contract = product_import_ui_contract
    return {
        "routes": contract.PAGE_ROUTES,
        "batch_status": contract.BATCH_STATUS_COPY,
        "batch_action": contract.BATCH_ACTION_COPY,
        "row_status": contract.ROW_STATUS_COPY,
        "issue_copy": contract.ISSUE_COPY,
        "tab_labels": contract.TAB_LABELS,
        "tab_status": contract.TAB_STATUS_MAP,
        "reviewable_statuses": list(contract.REVIEWABLE_STATUSES),
        "roles": {"operator": contract.ROLE_OPERATOR, "admin": contract.ROLE_ADMIN},
        "copy": {
            "upload_validated": contract.COPY_UPLOAD_VALIDATED,
            "upload_file_rejected": contract.COPY_UPLOAD_FILE_REJECTED,
            "upload_server_error": contract.COPY_UPLOAD_SERVER_ERROR,
            "upload_not_xlsx": contract.COPY_UPLOAD_NOT_XLSX,
            "review_load_error": contract.COPY_REVIEW_LOAD_ERROR,
            "review_not_found": contract.COPY_REVIEW_NOT_FOUND,
            "acknowledge_success": contract.COPY_ACKNOWLEDGE_SUCCESS,
            "acknowledge_stale": contract.COPY_ACKNOWLEDGE_STALE,
            "acknowledge_error": contract.COPY_ACKNOWLEDGE_ERROR,
            "error_first_blocked": contract.COPY_ERROR_FIRST_BLOCKED,
            "error_first_no_confirm": contract.COPY_ERROR_FIRST_NO_CONFIRM,
            "commit_not_ready": contract.COPY_COMMIT_NOT_READY,
            "commit_forbidden": contract.COPY_COMMIT_FORBIDDEN,
            "commit_server_error": contract.COPY_COMMIT_SERVER_ERROR,
        },
    }


def product_import_page():
    return mdm_workspace_page()


@app.get("/mdm/product-import-center", response_class=HTMLResponse)
@app.get("/mdm/product-import-center/upload", response_class=HTMLResponse)
@app.get("/mdm/product-import-center/batches/{batch_id}/review", response_class=HTMLResponse)
@app.get("/mdm/product-import-center/batches/{batch_id}/commit", response_class=HTMLResponse)
def product_import_page_route(
    batch_id: str = None,
    user=Depends(mdm_page_viewer),
):
    return product_import_page()


def sku_import_ui_manifest():
    contract = sku_import_ui_contract
    return {
        "routes": contract.PAGE_ROUTES,
        "batch_status": contract.BATCH_STATUS_COPY,
        "batch_action": contract.BATCH_ACTION_COPY,
        "row_status": contract.ROW_STATUS_COPY,
        "issue_copy": contract.ISSUE_COPY,
        "tab_labels": contract.TAB_LABELS,
        "tab_status": contract.TAB_STATUS_MAP,
        "reviewable_statuses": list(contract.REVIEWABLE_STATUSES),
        "roles": {"operator": contract.ROLE_OPERATOR, "admin": contract.ROLE_ADMIN},
        "copy": {
            "upload_validated": contract.COPY_UPLOAD_VALIDATED,
            "upload_file_rejected": contract.COPY_UPLOAD_FILE_REJECTED,
            "upload_server_error": contract.COPY_UPLOAD_SERVER_ERROR,
            "upload_not_xlsx": contract.COPY_UPLOAD_NOT_XLSX,
            "review_load_error": contract.COPY_REVIEW_LOAD_ERROR,
            "review_not_found": contract.COPY_REVIEW_NOT_FOUND,
            "acknowledge_success": contract.COPY_ACKNOWLEDGE_SUCCESS,
            "acknowledge_stale": contract.COPY_ACKNOWLEDGE_STALE,
            "acknowledge_error": contract.COPY_ACKNOWLEDGE_ERROR,
            "error_first_blocked": contract.COPY_ERROR_FIRST_BLOCKED,
            "error_first_no_confirm": contract.COPY_ERROR_FIRST_NO_CONFIRM,
            "commit_not_ready": contract.COPY_COMMIT_NOT_READY,
            "commit_forbidden": contract.COPY_COMMIT_FORBIDDEN,
            "commit_server_error": contract.COPY_COMMIT_SERVER_ERROR,
        },
    }


def goods_import_ui_manifest():
    contract = goods_import_ui_contract
    return {
        "routes": contract.PAGE_ROUTES,
        "batch_status": contract.BATCH_STATUS_COPY,
        "batch_action": contract.BATCH_ACTION_COPY,
        "row_status": contract.ROW_STATUS_COPY,
        "issue_copy": contract.ISSUE_COPY,
        "tab_labels": contract.TAB_LABELS,
        "tab_status": contract.TAB_STATUS_MAP,
        "reviewable_statuses": list(contract.REVIEWABLE_STATUSES),
        "roles": {"operator": contract.ROLE_OPERATOR, "admin": contract.ROLE_ADMIN},
        "copy": {
            "upload_validated": contract.COPY_UPLOAD_VALIDATED,
            "upload_file_rejected": contract.COPY_UPLOAD_FILE_REJECTED,
            "upload_server_error": contract.COPY_UPLOAD_SERVER_ERROR,
            "upload_not_xlsx": contract.COPY_UPLOAD_NOT_XLSX,
            "review_load_error": contract.COPY_REVIEW_LOAD_ERROR,
            "review_not_found": contract.COPY_REVIEW_NOT_FOUND,
            "acknowledge_success": contract.COPY_ACKNOWLEDGE_SUCCESS,
            "acknowledge_stale": contract.COPY_ACKNOWLEDGE_STALE,
            "acknowledge_error": contract.COPY_ACKNOWLEDGE_ERROR,
            "error_first_blocked": contract.COPY_ERROR_FIRST_BLOCKED,
            "error_first_no_confirm": contract.COPY_ERROR_FIRST_NO_CONFIRM,
            "commit_not_ready": contract.COPY_COMMIT_NOT_READY,
            "commit_forbidden": contract.COPY_COMMIT_FORBIDDEN,
            "commit_server_error": contract.COPY_COMMIT_SERVER_ERROR,
        },
    }


def mdm_workspace_page():
    """Render one SPA document with every import UI contract available."""
    html = (STATIC_DIR / "mdm.html").read_text(encoding="utf-8")
    manifests = (
        ("customer-import-ui-contract", customer_import_ui_manifest()),
        ("product-import-ui-contract", product_import_ui_manifest()),
        ("sku-import-ui-contract", sku_import_ui_manifest()),
        ("goods-import-ui-contract", goods_import_ui_manifest()),
    )
    for element_id, manifest in manifests:
        payload = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":")
        ).replace("</", "<\\/")
        marker = f'<script id="{element_id}" type="application/json">{{}}</script>'
        html = html.replace(marker, marker.replace("{}", payload))
    return HTMLResponse(html)


def sku_import_page():
    return mdm_workspace_page()


@app.get("/mdm/sku-import-center", response_class=HTMLResponse)
@app.get("/mdm/sku-import-center/upload", response_class=HTMLResponse)
@app.get("/mdm/sku-import-center/batches/{batch_id}/review", response_class=HTMLResponse)
@app.get("/mdm/sku-import-center/batches/{batch_id}/commit", response_class=HTMLResponse)
def sku_import_page_route(
    batch_id: str = None,
    user=Depends(mdm_page_viewer),
):
    return sku_import_page()


def goods_import_page():
    return mdm_workspace_page()


@app.get("/mdm/goods-import-center", response_class=HTMLResponse)
@app.get("/mdm/goods-import-center/upload", response_class=HTMLResponse)
@app.get("/mdm/goods-import-center/batches/{batch_id}/review", response_class=HTMLResponse)
@app.get("/mdm/goods-import-center/batches/{batch_id}/commit", response_class=HTMLResponse)
def goods_import_page_route(
    batch_id: str = None,
    user=Depends(mdm_page_viewer),
):
    return goods_import_page()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "CCGtools-open"}
