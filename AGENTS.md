# CCG WorkPanel contributor instructions

FMCG Sales & Operations Workspace. The project is licensed under Apache-2.0.

Use only synthetic data generated from scratch and the loopback-only local demo.
Do not connect to external business systems or include business exports,
credentials, runtime databases or logs in contributions.

FastAPI composition is webapp/main.py; auth stores accounts in SQLite;
MDM/Sales/Ordering share the MySQL metadata and public Alembic baseline
0001_public_baseline. Preserve module permission gates, immutable source
versions, blank versus zero semantics, atomic imports and locked-cycle
contracts. No historical daily-reporting module is included.

Keep contributions focused and explain affected contracts. Use Python unittest
and Node tests. Run the local demo from empty volumes and the security scan
before submitting changes. Schema, constraint, trigger and transaction changes
require isolated MySQL validation; SQLite results cannot establish MySQL
compatibility. Verify UI interactions and permissions for frontend changes.
Report missing or skipped validation honestly.

See README.md and CONTRIBUTING.md for setup and contribution guidance.
