"""创建用户账号。

用法：python3 webapp/create_user.py --username admin --display-name 管理员 --role admin [--password xxx]
"""
import argparse
import os
import secrets
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from security import hash_password
from account_permissions import migrate_account_db, replace_permissions, role_defaults

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "app.db")


def main():
    parser = argparse.ArgumentParser(description="创建用户账号")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", choices=("admin", "operator"), default="operator")
    parser.add_argument("--password", default=None)
    args = parser.parse_args()
    if not os.path.exists(args.db):
        print("数据库不存在：%s（请先使用 webapp/schema.sql 初始化账号数据库）" % args.db)
        sys.exit(1)
    password = args.password or secrets.token_urlsafe(8)
    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrate_account_db(conn)
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, role) VALUES (?,?,?,?)",
            (args.username, hash_password(password), args.display_name, args.role),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        replace_permissions(conn, user_id, role_defaults(args.role), user_id)
        conn.commit()
    except sqlite3.IntegrityError:
        print("用户已存在：%s" % args.username)
        sys.exit(1)
    finally:
        conn.close()
    print("用户创建成功：%s（%s，%s）" % (args.username, args.display_name, args.role))
    if not args.password:
        print("初始密码：%s（请首次登录后修改）" % password)


if __name__ == "__main__":
    main()
