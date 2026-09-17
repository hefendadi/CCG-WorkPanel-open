# Contributing

CCG WorkPanel is licensed under Apache-2.0. Keep changes focused, describe
affected contracts, and run the local demo and tests before proposing changes.
Use only obviously synthetic data. New fixture directories must end in
tests/fixtures/synthetic/. Do not include business exports, identifiable people,
operational credentials, environment addresses, dumps or deployment evidence.

For schema changes update the public Alembic chain and add isolated MySQL tests
for constraints, triggers and transactions. SQLite cannot prove MySQL behavior.
For UI changes retain plain browser assets and test permissions and interactions.
