"""SQLite account storage. Callers own connections and write transactions."""
import os
import sqlite3
from pathlib import Path

from .permissions import (
    ACCOUNT_PERMISSION_MIGRATION, LEVEL_RANK, MODULES, MODULE_USER_MANAGEMENT,
    empty_permissions, role_defaults,
)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DB_PATH = Path(os.environ.get("CCGTOOLS_ACCOUNT_DB_PATH", DEFAULT_DATA_DIR / "app.db"))
DATA_DIR = DB_PATH.parent


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def permissions_for(conn: sqlite3.Connection, user_id: int) -> dict[str, str]:
    result = empty_permissions()
    rows = conn.execute(
        "SELECT module, level FROM user_permissions WHERE user_id=?", (user_id,)
    ).fetchall()
    for module, level in rows:
        if module in result and level in LEVEL_RANK:
            result[module] = level
    return result


def replace_permissions(
    conn: sqlite3.Connection, user_id: int, permissions: dict[str, str], updated_by: int
) -> None:
    conn.executemany(
        "INSERT INTO user_permissions (user_id,module,level,updated_by,updated_at) "
        "VALUES (?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(user_id,module) DO UPDATE SET level=excluded.level, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        [(user_id, module, permissions[module], updated_by) for module in MODULES],
    )


def migrate_account_db(conn: sqlite3.Connection) -> bool:
    """Apply the one-time users/auth_version + user_permissions migration."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if not columns:
        raise RuntimeError("users 表不存在")
    if "auth_version" not in columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_permissions (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          module TEXT NOT NULL CHECK(module IN ('mdm','sales_actual','ordering','user_management')),
          level TEXT NOT NULL CHECK(level IN ('NONE','VIEW','EDIT')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          updated_by INTEGER REFERENCES users(id),
          PRIMARY KEY (user_id, module),
          CHECK(module != 'user_management' OR level IN ('NONE','EDIT'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS account_schema_migration (
          version TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )"""
    )
    applied = conn.execute(
        "SELECT 1 FROM account_schema_migration WHERE version=?",
        (ACCOUNT_PERMISSION_MIGRATION,),
    ).fetchone()
    if applied:
        conn.commit()
        return False
    users = conn.execute("SELECT id, role FROM users ORDER BY id").fetchall()
    for user_id, role in users:
        replace_permissions(conn, user_id, role_defaults(role), user_id)
    conn.execute(
        "INSERT INTO account_schema_migration (version) VALUES (?)",
        (ACCOUNT_PERMISSION_MIGRATION,),
    )
    conn.commit()
    return True


def ensure_operation_log(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS operation_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " user_id INTEGER,"
        " action TEXT NOT NULL,"
        " target_type TEXT NOT NULL,"
        " target_id INTEGER,"
        " detail TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))"
    )


def log_operation(conn, user, action, target_type, target_id, detail):
    conn.execute(
        "INSERT INTO operation_log (user_id, action, target_type, target_id, detail) VALUES (?,?,?,?,?)",
        (user["id"], action, target_type, target_id, detail),
    )


def active_manager_count(conn, *, excluding=None):
    sql = (
        "SELECT COUNT(*) FROM users u JOIN user_permissions p ON p.user_id=u.id "
        "WHERE u.active=1 AND u.role='admin' AND p.module=? AND p.level='EDIT'"
    )
    params = [MODULE_USER_MANAGEMENT]
    if excluding is not None:
        sql += " AND u.id<>?"
        params.append(excluding)
    return conn.execute(sql, params).fetchone()[0]


def login_user(conn, username):
    return conn.execute(
        "SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)
    ).fetchone()


def user_by_id(conn, user_id, *, active_only=False):
    sql = "SELECT * FROM users WHERE id=?"
    if active_only:
        sql += " AND active=1"
    return conn.execute(sql, (user_id,)).fetchone()


def user_id_by_username(conn, username):
    return conn.execute(
        "SELECT id FROM users WHERE username=?", (username.strip(),)
    ).fetchone()


def auth_version_for(conn, user_id):
    row = conn.execute(
        "SELECT auth_version FROM users WHERE id=?", (user_id,)
    ).fetchone()
    return int(row["auth_version"]) if row else 0


def insert_user(conn, username, password_hash, display_name, role):
    return conn.execute(
        "INSERT INTO users (username, password_hash, display_name, role, active) VALUES (?,?,?,?,1)",
        (username.strip(), password_hash, display_name.strip(), role),
    ).lastrowid


def set_password(conn, user_id, password_hash):
    conn.execute(
        "UPDATE users SET password_hash=?, auth_version=auth_version+1 WHERE id=?",
        (password_hash, user_id),
    )


def update_user_account(conn, user_id, display_name, role, active, *,
                        password_hash=None, auth_version_increment=0):
    if password_hash is not None:
        conn.execute(
            "UPDATE users SET display_name=?, role=?, active=?, password_hash=?, "
            "auth_version=auth_version+1 WHERE id=?",
            (display_name.strip(), role, active, password_hash, user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET display_name=?, role=?, active=?, "
            "auth_version=auth_version+? WHERE id=?",
            (display_name.strip(), role, active, auth_version_increment, user_id),
        )


def list_user_accounts(conn):
    return conn.execute(
        "SELECT id,username,display_name,role,active,created_at FROM users ORDER BY id"
    ).fetchall()
