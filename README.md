[English](README.md) | [简体中文](README.zh-CN.md)

# CCG WorkPanel

FMCG Sales & Operations Workspace

## Overview

CCG WorkPanel is an open-source workspace for FMCG sales and operations.
It brings master data, imports, sales actuals, inventory, incoming supply,
manual forecasts and ordering into a shared workspace built on MDM
(master data management).

The bundled configuration runs independently on a local machine. All bundled
business data is fictional and generated from scratch.

## Why

Customers, products, SKUs, channels, sales actuals, inventory, incoming supply,
forecasts and orders often sit in separate spreadsheets, ERP exports and manual
workflows. The challenge is keeping them tied to the same master data and
business relationships, with a traceable record of how data changes.

CCG WorkPanel uses shared master data and explicit source versions to organize
these workflows around the same business facts. It is a lightweight workspace,
not a complete ERP or S&OP platform.

## Core Modules

- **Auth and permissions:** NONE / VIEW / EDIT module access and account administration.
- **MDM:** customers, products, SKUs, channels, sales representatives and reference data.
- **Import Center:** parsing, validation, review and atomic master-data commits.
- **ACT Sales:** mapping, preview, publication and quantity dashboards.
- **Ordering / Forecast:** inventory, incoming supply, planning cycles, manual
  forecast versions, final orders and locked-cycle history.
- **Portal:** permission-aware overview and module navigation.

## Architecture

The browser uses plain HTML/CSS/JavaScript. FastAPI composes the domain routers.
SQLite stores accounts and permissions; SQLAlchemy/MySQL stores business and
import data shared by MDM, Sales and Ordering.

Alembic has one public baseline, `0001_public_baseline`, including relational
constraints and triggers. Source versions are explicitly adopted into planning
cycles. Forecast versions are immutable, and locked cycles preserve history.
No historical daily-reporting module is included.

See [Architecture](docs/architecture.md) and
[Import contracts](docs/import-contracts.md).

## Local Docker Quick Start

Requires Docker with Compose. Run commands from the repository root:

```sh
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml up --build -d --wait
```

Open <http://127.0.0.1:18080/login>. The isolated MySQL service binds only to
`127.0.0.1:13306` for local tests.

Initialization runs in this order:

1. Create the MySQL business schema from the public Alembic baseline.
2. Create the SQLite account schema.
3. Apply the account permission migration and verify its marker.
4. Seed synthetic accounts and their final permissions, then synthetic business data.
5. Start the application.

Startup and login recheck the applied account migration without resetting
permissions. No manual SQLite editing is required. App, seed and migrate use
the same local build image.

Environment overrides are described in [`.env.example`](deploy/local/.env.example).
Compose reads host environment values; it does not load that example file
automatically. See [Local development](docs/local-development.md).

### Reset the local demo

This deletes only this Compose project's fictional data volumes:

```sh
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml down --volumes
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml up --build -d --wait
```

## Demo Accounts

All three accounts use the password `demo-only-change-me`.
These credentials are for the loopback-only local demo.
Do not reuse them in any deployed environment.

| Account | MDM | ACT Sales | Ordering / Forecast | User Management |
| --- | --- | --- | --- | --- |
| Demo Admin (`demo_admin`) | EDIT | EDIT | EDIT | EDIT |
| Demo Operator (`demo_operator`) | EDIT | EDIT | EDIT | NONE |
| Demo Viewer (`demo_viewer`) | VIEW | VIEW | VIEW | NONE |

## Synthetic Demo

`python -m demo.generate_data` reproducibly generates XLSX/CSV/JSON fixtures in
`webapp/tests/fixtures/synthetic/`. `python -m demo.seed` populates only an
explicitly enabled demo database.

Names, codes, quantities and relationships are invented, not anonymized
business records. The demo uses a four-month planning window, explicit
warehouse availability and a configurable Sales decline gate
(`CCGTOOLS_SALES_DROP_THRESHOLD`, demo default `0.8`).

## Tests

```sh
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml run --rm tests
node --test webapp/tests/public_frontend_smoke.cjs
python -m demo.security_scan
```

The tests cover empty SQLite initialization, account migrations and permissions,
MySQL schema/constraints/triggers, synthetic imports, sales publication,
forecasts/orders/locks, login, Portal and frontend contracts.
MySQL tests must not silently fall back to SQLite.

See [Contributing](CONTRIBUTING.md) for contribution guidance.

## Limitations

- The UI is primarily Chinese. Import adapters document a demo workbook format,
  not universal ERP compatibility.
- Ordering import services and final-order core exist, but not every operation
  has a browser workflow. Seed tools demonstrate the supported core flows.
- Forecasts are supplied manually. The four-month forecast horizon is the
  current public contract.
- Local HTTP defaults Cookie Secure to false. HTTPS installations must set
  `CCGTOOLS_PORTAL_COOKIE_SECURE=true`. HttpOnly and SameSite=Lax remain enabled.

## License

License: Apache-2.0. See [LICENSE](LICENSE) for the full text.
