# Local development

Run commands from the repository root and use the scoped Compose commands in
[README.md](../README.md). To develop without Docker for the
app, install webapp/requirements-test.txt in your own virtual environment,
initialize SQLite with demo.initialize_accounts and configure MDM_DATABASE_URL
for an isolated MySQL database. Apply 0001_public_baseline before seeding.

Do not point demo.seed or the tests at an external database. Reset uses only
the ccgtools-open-demo project's volumes. Run tests from the repository root.
