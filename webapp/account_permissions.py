"""Compatibility exports and CLI for the extracted account auth core."""
import argparse
import sqlite3
from pathlib import Path

# create_user.py historically imports this module directly from webapp/.
if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp.auth.permissions import (
    ACCOUNT_PERMISSION_MIGRATION, EDIT, LEVEL_RANK, MODULE_MDM, MODULE_ORDERING,
    MODULE_SALES_ACTUAL, MODULE_USER_MANAGEMENT, MODULES, NONE, VIEW,
    empty_permissions, has_permission, normalize_permissions, role_defaults,
)
from webapp.auth.account_store import migrate_account_db, permissions_for, replace_permissions
from webapp.auth.dependencies import (
    current_user as _bearer_user,
    require_page_permission, require_permission, require_user_management,
    require_user_management_page, user_management_edit,
)


def main():
    parser = argparse.ArgumentParser(description="迁移 CCGtools 账号权限库")
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    path = Path(args.db)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        changed = migrate_account_db(conn)
    finally:
        conn.close()
    print("APPLIED" if changed else "ALREADY_APPLIED")


if __name__ == "__main__":
    main()
