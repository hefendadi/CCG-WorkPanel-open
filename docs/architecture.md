# Public architecture

Browser -> FastAPI composition -> authenticated domain services -> SQLAlchemy
models -> MySQL 8.0.46. Accounts and module permissions use a separate SQLite DB.

Permission modules are mdm, sales_actual, ordering and user_management. The first
three permit NONE/VIEW/EDIT; account administration requires admin plus EDIT.
Page authentication uses an HttpOnly SameSite=Lax cookie. API authentication uses
Bearer JWT. Password changes increment auth_version and invalidate older tokens.

The Portal links to /mdm, /sales/actual, /ordering and /admin/users. Source imports
are distinct from cycle selection: versions are explicitly adopted, never
silently selected by recency. Locked cycles snapshot display identities and
reject mutations using services and MySQL triggers.

One frozen public migration initializes all tables, FK/index/unique/check objects
and append-only/lock triggers. No earlier schema lineage is part of the project.
