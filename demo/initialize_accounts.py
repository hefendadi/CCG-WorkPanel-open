"""Initialize only the public account schema and fictional local accounts."""
import sqlite3
from pathlib import Path
from webapp.security import hash_password
from webapp.auth.permissions import ACCOUNT_PERMISSION_MIGRATION, role_defaults
from webapp.auth.account_store import migrate_account_db, replace_permissions

DEMO_PASSWORD = 'demo-only-change-me'

def initialize_accounts(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        schema = Path(__file__).resolve().parents[1] / 'webapp' / 'schema.sql'
        conn.executescript(schema.read_text())
        migrate_account_db(conn)
        if not conn.execute(
            'SELECT 1 FROM account_schema_migration WHERE version=?',
            (ACCOUNT_PERMISSION_MIGRATION,),
        ).fetchone():
            raise RuntimeError('Account permission migration must complete before demo seed')
        for username, name, role in [
            ('demo_admin', 'Demo Admin A', 'admin'),
            ('demo_operator', 'Demo Operator A', 'operator'),
            ('demo_viewer', 'Demo Viewer A', 'operator'),
        ]:
            if conn.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                continue
            uid = conn.execute('INSERT INTO users(username,display_name,role,password_hash) VALUES (?,?,?,?)',
                (username, name, role, hash_password(DEMO_PASSWORD))).lastrowid
            permissions = role_defaults(role)
            if username == 'demo_operator':
                permissions.update(mdm='EDIT', sales_actual='EDIT', ordering='EDIT')
            replace_permissions(conn, uid, permissions, uid)
        conn.commit()
    finally:
        conn.close()
