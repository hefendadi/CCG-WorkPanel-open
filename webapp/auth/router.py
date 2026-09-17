"""Auth HTTP endpoints; account identity and storage are provided by Auth Core."""
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from . import account_store, service
from .account_store import db, migrate_account_db, permissions_for
from .dependencies import get_current_user, require_user_management, require_user_management_page
from .service import (
    PORTAL_COOKIE_NAME, PORTAL_SESSION_SECONDS, PORTAL_COOKIE_SECURE_ENV,
    create_token,
)
from ..security import hash_password, verify_password

router = APIRouter()


def portal_cookie_secure():
    return os.environ.get(PORTAL_COOKIE_SECURE_ENV, "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def set_portal_cookie(response, token):
    response.set_cookie(
        PORTAL_COOKIE_NAME,
        token,
        max_age=PORTAL_SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=portal_cookie_secure(),
        path="/",
    )


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    conn = db()
    try:
        migrate_account_db(conn)
        row = account_store.login_user(conn, body.username)
        permissions = permissions_for(conn, row["id"]) if row else None
    finally:
        conn.close()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "账号或密码错误")
    user = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "permissions": permissions,
    }
    token = create_token(row["id"])
    set_portal_cookie(response, token)
    return {"token": token, "user": user}


@router.post("/api/auth/logout", status_code=204)
def logout():
    response = Response(status_code=204)
    response.delete_cookie(
        PORTAL_COOKIE_NAME,
        path="/",
        secure=portal_cookie_secure(),
        httponly=True,
        samesite="lax",
    )
    return response


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/auth/change-password")
def change_password(body: ChangePasswordBody, user: dict = Depends(get_current_user)):
    conn = db()
    try:
        row = account_store.user_by_id(conn, user["id"])
    finally:
        conn.close()
    if not row or not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(400, "原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if body.new_password == body.old_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    conn = db()
    try:
        account_store.set_password(conn, user["id"], hash_password(body.new_password))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/api/auth/me")
def me(
    response: Response,
    authorization: str = Header(default=""),
    user: dict = Depends(get_current_user),
):
    set_portal_cookie(response, authorization[7:])
    return {"user": user}


class UserCreate(BaseModel):
    username: str
    display_name: str
    role: str = "operator"
    password: str
    permissions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_retired_channel_ids(cls, value):
        if isinstance(value, dict) and "channel_ids" in value:
            raise PydanticCustomError("retired_field", "channel_ids has been retired")
        return value


class UserUpdate(BaseModel):
    display_name: str
    role: str = "operator"
    active: int = 1
    password: str = ""
    permissions: Optional[dict[str, str]] = None

    @model_validator(mode="before")
    @classmethod
    def reject_retired_channel_ids(cls, value):
        if isinstance(value, dict) and "channel_ids" in value:
            raise PydanticCustomError("retired_field", "channel_ids has been retired")
        return value


class PasswordReset(BaseModel):
    password: str


@router.get("/admin/users", response_class=FileResponse)
def user_management_page(user=Depends(require_user_management_page)):
    return Path(__file__).resolve().parents[1] / "static" / "user-management.html"


@router.get("/api/admin/users")
def admin_users(user: dict = Depends(require_user_management)):
    return service.list_accounts()


@router.post("/api/admin/users", status_code=201)
def create_user(body: UserCreate, user: dict = Depends(require_user_management)):
    return service.create_account(**body.model_dump(), user=user)


@router.put("/api/admin/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, user: dict = Depends(require_user_management)):
    return service.update_account(user_id, **body.model_dump(), user=user)


@router.post("/api/admin/users/{user_id}/reset-password")
def reset_user_password(user_id: int, body: PasswordReset, user: dict = Depends(require_user_management)):
    return service.reset_account_password(user_id, body.password, user)
