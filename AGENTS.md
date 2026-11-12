# Pyzzle project rules

## Goal and scope

Pyzzle is a lightweight Python ORM for PostgreSQL only. Do not add MongoDB or support for any other database.

## Architecture

- `core/` contains `Base`, `Column`, and types.
- `sql/` is the only place that builds SQL strings.
- `pool.py` contains the connection pool.
- `Base` never builds SQL itself.

## Implementation rules

- Always use parameterized SQL with `%s` placeholders. Never put user values inside SQL strings.
- Our code uses only the Python standard library. Import `psycopg` and `psycopg_pool` lazily inside `connect()`.
- Do not use metaclasses; use `__init_subclass__` and descriptors.
- Do not use lazy loading or hidden queries. Relations load only when explicitly requested.
- Keep modules small. Do not import heavy modules at module top level.

## Workflow

- Every change needs tests. Run `pytest -q` before finishing.
- Work one phase at a time. Do not implement features from later phases unless asked.

## SQL builder conventions

- Always validate and double-quote every table and column identifier.
- Put values only in parameter tuples with `%s` placeholders; never interpolate values.
- SQL builder functions return `Statement` objects, except DDL builders that return SQL text.
- Keep SQL construction inside `sql/`; do not execute queries from this package.
- Keep builders deterministic and preserve caller-supplied order.
- Validate SQL types before including them in DDL.

## Model conventions

- Define concrete models with a validated `__tablename__` and exactly one Integer primary key.
- Use `Column` descriptors and supported types; abstract model columns are inherited.
- Track only real field changes and retain the original value until a field is reset.
- Keep the hidden version column managed by Pyzzle and include it last in row/spec order.
- Model classes do not build SQL or connect to the database.
- Do not add relationships, Python-side defaults, or assigned-value validation in the model layer.

## Database tests

- Database tests read `PYZZLE_TEST_DATABASE_URL` and skip when it is missing.
- Use a dedicated, disposable PostgreSQL test database.
- Tests may create and drop only tables whose names start with `pyzzle_test_`.
- Table drops are allowed only in tests, never in library code.

## CRUD conventions

- Use the SQL builders for all insert, select, update, and delete statements.
- Treat objects with `version is None` as new; the database assigns primary keys and versions.
- Persist only explicitly set fields on insert and tracked fields on update.
- Use the version column for optimistic updates and deletes; classify missing rows on the same connection.
- Raise `NotFound` when the row is gone and `VersionConflict` when its version is stale.
- Let database constraint errors propagate unchanged.

## Transactions

- The active connection `ContextVar` lives in `pool.py`.
- `connection()` reuses the active transaction connection.
- Pool connections use autocommit for single statements.
- `_run` is for one statement; `_run_tx` is for several statements that must be all-or-nothing.
- Nested `transaction()` blocks use savepoints, so a caught inner error does not abort the outer transaction.
- The connection check is off by default; `connect(..., check=True)` enables it.
- Let exceptions escape the outer transaction block when all of its work must roll back.

## Scripts
- Measurement scripts live in `scripts/` and are not part of the installed package.
- Use only the documented test database and `pyzzle_test_` tables for database measurements.

## Query conventions
- `select()` returns plain rows only; objects come from `get()`.
- The `max_rows` guard is applied in `Query.all()`.
- `in_()` sends one array parameter and does not sort.

## Bulk and conditional writes
- `create_many()` uses `executemany(returning=True)` inside `_run_tx`.
- `update_where()` bumps each changed row's version.
- `update_where()` and `delete_where()` require conditions and have no row guard.
