"""FastAPI authentication and fixed module permission dependencies."""
from fastapi import Depends, Header, HTTPException, Request

from .service import PORTAL_COOKIE_NAME, active_user_from_token, decode_token, user_from_payload
from .permissions import EDIT, VIEW, MODULES, MODULE_USER_MANAGEMENT, has_permission


def get_current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    payload = decode_token(authorization[7:])
    if not payload:
        raise HTTPException(401, "登录已过期")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, "登录已过期")
    user = user_from_payload(payload, user_id)
    if not user:
        raise HTTPException(401, "用户不存在、已停用或登录已失效")
    return user


def current_user(request: Request) -> dict:
    return get_current_user(request.headers.get("authorization", ""))


def require_permission(module: str, minimum: str, authenticated_user=None):
    if module not in MODULES or minimum not in (VIEW, EDIT):
        raise ValueError("invalid fixed permission")

    auth_dependency = authenticated_user or current_user

    def dependency(user=Depends(auth_dependency)):
        if not has_permission(user, module, minimum):
            raise HTTPException(403, "当前账号无此模块权限")
        return user

    return dependency


user_management_edit = require_permission(MODULE_USER_MANAGEMENT, EDIT)


def require_user_management(user=Depends(user_management_edit)):
    if user.get("role") != "admin":
        raise HTTPException(403, "仅账号管理员可访问")
    return user


def require_page_permission(module: str, minimum: str):
    def dependency(request: Request):
        user = active_user_from_token(request.cookies.get(PORTAL_COOKIE_NAME, ""))
        if not user:
            raise HTTPException(401, "未登录")
        if not has_permission(user, module, minimum):
            raise HTTPException(403, "当前账号无此模块权限")
        return user

    return dependency


def require_user_management_page(request: Request):
    user = active_user_from_token(request.cookies.get(PORTAL_COOKIE_NAME, ""))
    if not user:
        raise HTTPException(401, "未登录")
    if user.get("role") != "admin" or not has_permission(
        user, MODULE_USER_MANAGEMENT, EDIT
    ):
        raise HTTPException(403, "仅账号管理员可访问")
    return user
