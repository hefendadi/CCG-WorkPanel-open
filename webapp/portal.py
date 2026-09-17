"""Portal and login pages using the shared account authentication core."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from .auth.service import PORTAL_COOKIE_NAME, active_user_from_token

STATIC_DIR = Path(__file__).resolve().parent / "static"
router = APIRouter()


@router.get("/login", response_class=FileResponse)
def login_page():
    return STATIC_DIR / "index.html"


@router.get("/")
def portal_page(request: Request):
    token = request.cookies.get(PORTAL_COOKIE_NAME, "")
    if not token or not active_user_from_token(token):
        return RedirectResponse(url="/login", status_code=307)
    return FileResponse(STATIC_DIR / "portal.html")
