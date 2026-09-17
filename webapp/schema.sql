PRAGMA foreign_keys = ON;

-- Public users (operator / admin)
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'operator',
  active INTEGER NOT NULL DEFAULT 1,
  auth_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS user_permissions (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  module TEXT NOT NULL CHECK(module IN ('mdm','sales_actual','ordering','user_management')),
  level TEXT NOT NULL CHECK(level IN ('NONE','VIEW','EDIT')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_by INTEGER REFERENCES users(id),
  PRIMARY KEY (user_id, module),
  CHECK(module != 'user_management' OR level IN ('NONE','EDIT'))
);

CREATE TABLE IF NOT EXISTS account_schema_migration (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS operation_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
