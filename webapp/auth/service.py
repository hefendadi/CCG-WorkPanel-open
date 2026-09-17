"""JWT issuance and current account validation without application imports."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import jwt
from fastapi import HTTPException

from ..security import hash_password
from .permissions import EDIT, MODULE_USER_MANAGEMENT, normalize_permissions

from . import account_store

DATA_DIR = account_store.DATA_DIR
TZ = ZoneInfo("Asia/Shanghai")
PORTAL_COOKIE_NAME = "ccg_portal_token"
PORTAL_SESSION_SECONDS = 12 * 60 * 60
PORTAL_COOKIE_SECURE_ENV = "CCGTOOLS_PORTAL_COOKIE_SECURE"
# Lazy loading keeps imports free of secret-file and database side effects.
SECRET = None


def secret_key():
    DATA_DIR.mkdir(exist_ok=True)
    f = DATA_DIR / "secret.txt"
    if not f.exists():
        f.write_text(uuid.uuid4().hex + uuid.uuid4().hex, encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()


def signing_secret():
    global SECRET
    if SECRET is None:
        SECRET = secret_key()
    return SECRET


def now_tz():
    return datetime.now(TZ)


def create_token(user_id):
    conn = account_store.db()
    try:
        account_store.migrate_account_db(conn)
        auth_version = account_store.auth_version_for(conn, user_id)
    finally:
        conn.close()
    payload = {
        "sub": str(user_id),
        "av": auth_version,
        "exp": now_tz() + timedelta(hours=12),
    }
    return jwt.encode(payload, signing_secret(), algorithm="HS256")


def decode_token(token):
    try:
        return jwt.decode(token, signing_secret(), algorithms=["HS256"])
    except Exception:
        return None


def user_from_payload(payload, user_id):
    conn = account_store.db()
    try:
        account_store.migrate_account_db(conn)
        row = account_store.user_by_id(conn, user_id, active_only=True)
        if not row or int(payload.get("av", 0)) != int(row["auth_version"]):
            return None
        permissions = account_store.permissions_for(conn, user_id)
    finally:
        conn.close()
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "permissions": permissions,
    }


def active_user_from_token(token):
    payload = decode_token(token)
    if not payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return user_from_payload(payload, user_id)


def validate_role(role):
    if role not in ("admin", "operator"):
        raise HTTPException(400, "角色只能是 admin 或 operator")


def validated_permissions(value, role):
    try:
        return normalize_permissions(value, role=role)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def list_accounts():
    conn = account_store.db()
    try:
        result = []
        for row in account_store.list_user_accounts(conn):
            item = dict(row)
            item["permissions"] = account_store.permissions_for(conn, row["id"])
            result.append(item)
        return result
    finally:
        conn.close()


def create_account(username, display_name, role, password, permissions, user):
    validate_role(role)
    permissions = validated_permissions(permissions, role)
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    conn = account_store.db()
    try:
        dup = account_store.user_id_by_username(conn, username)
        if dup:
            raise HTTPException(400, "用户名已存在")
        uid = account_store.insert_user(
            conn, username, hash_password(password), display_name, role,
        )
        account_store.replace_permissions(conn, uid, permissions, user["id"])
        account_store.log_operation(conn, user, "create", "user", uid, "新增账号：%s" % username.strip())
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": uid}


def update_account(user_id, display_name, role, active, password, permissions, user):
    validate_role(role)
    if active not in (0, 1):
        raise HTTPException(400, "账号状态只能是启用或停用")
    conn = account_store.db()
    try:
        # Serialize manager revocation checks with the account update so two
        # concurrent requests cannot both remove the final effective manager.
        conn.execute("BEGIN IMMEDIATE")
        existing = account_store.user_by_id(conn, user_id)
        if not existing:
            raise HTTPException(404, "账号不存在")
        if user_id == user["id"] and active == 0:
            raise HTTPException(400, "不能停用自己的账号")
        if permissions is None:
            permissions = account_store.permissions_for(conn, user_id)
            if role != "admin":
                permissions[MODULE_USER_MANAGEMENT] = "NONE"
        else:
            permissions = validated_permissions(permissions, role)
        remains_manager = (
            active == 1
            and role == "admin"
            and permissions[MODULE_USER_MANAGEMENT] == EDIT
        )
        if not remains_manager and account_store.active_manager_count(conn, excluding=user_id) == 0:
            raise HTTPException(400, "必须保留至少一个有效账号管理员")
        auth_version_increment = (
            bool(password) or (existing["active"] == 1 and active == 0)
        )
        if password:
            if len(password) < 6:
                raise HTTPException(400, "密码至少 6 位")
            account_store.update_user_account(
                conn, user_id, display_name, role, active,
                password_hash=hash_password(password),
            )
        else:
            account_store.update_user_account(
                conn, user_id, display_name, role, active,
                auth_version_increment=1 if auth_version_increment else 0,
            )
        account_store.replace_permissions(conn, user_id, permissions, user["id"])
        account_store.log_operation(conn, user, "update", "user", user_id, "编辑账号：%s" % existing["username"])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def reset_account_password(user_id, password, user):
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    conn = account_store.db()
    try:
        existing = account_store.user_by_id(conn, user_id)
        if not existing:
            raise HTTPException(404, "账号不存在")
        account_store.set_password(conn, user_id, hash_password(password))
        account_store.log_operation(
            conn, user, "reset_password", "user", user_id,
            "重置账号密码：%s" % existing["username"],
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
