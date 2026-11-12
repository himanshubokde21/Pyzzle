# Pyzzle

A lightweight Python ORM for PostgreSQL.

## Quick example

```python
from pyzzle import Base, Column, Integer, String, connect, disconnect
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
connect("postgresql://postgres:pyzzle@127.0.0.1:5432/pyzzle_test")
Base.create_tables()
user = User.create(name="Amit")
user.name = "Rahul"
user.save()
User.get(user.id)
user.delete()
disconnect()
```

## Running tests

Run the suite with `pytest -q`. Tests that need PostgreSQL read `PYZZLE_TEST_DATABASE_URL` and are skipped when it is not set.
For example: `postgresql://postgres:pyzzle@127.0.0.1:5432/pyzzle_test`.
The test database must be dedicated and disposable.

Run related changes together:

```python
with Base.transaction():
    a.balance -= 10; a.save()
    b.balance += 10; b.save()
```

- The block commits all changes together or rolls them all back.
- Nested blocks use savepoints, so you can catch an error inside an inner block and keep going in the outer one.
- If an error leaves the outer block, the whole transaction rolls back.
- Single statements run in autocommit; use `Base.transaction()` when several statements must succeed together.
- `connect(..., check=True)` checks connections before use, adding one database round trip per operation.

## Measuring

Run `python scripts/measure.py` for import, size, memory, SQL, latency, connection setup, concurrency, and bulk-insert measurements.
Run `python scripts/measure_pool.py` to compare direct psycopg and pool latency.
Both scripts accept `--url` and default to `PYZZLE_TEST_DATABASE_URL`.
Use `--iterations` to change the database latency sample count (default: 200), and `--bulk-rows` to change the bulk insert row count (default: 1000).
Without a URL, database measurements are skipped while local measurements still run.
Database measurements create and drop only `pyzzle_test_` tables in a disposable database.

## Querying

`User.select()` returns plain rows for all user columns; `User.select(User.name, User.age)` returns only those values. Rows support attribute and index access.

```python
rows = User.select().where(User.age > 18).all()
user = User.get(1)                    # primary key lookup
user = User.get(User.email == "a@x.com")
```

Filter with `.where(...)`; combine conditions with parenthesized `&`, `|`, and `~`, or with `and_(...)` and `or_(...)`. Compare NULL with `== None` or `!= None`; use `.in_([1, 2])` (lists, tuples, and sets), `.like("A%")`, or `.ilike("a%")`. Sort with `.order_by(User.name.asc())` and page with `.limit(20).offset(40)`. `.first()`, `.count()`, and `.to_sql()` return the first row, a count, and the SQL statement with parameters.

`all()` raises `TooManyRowsError` when more than 10,000 rows would be returned. Add `limit()`, use `unlimited()`, or set `Base.max_rows` / `Model.max_rows`; `None` disables the guard.

Loading many objects at once is not supported yet.

## Writing many rows

`User.create_many(rows)` inserts dictionaries with the same keys atomically and returns generated ids; either all rows are inserted or none are.
`User.update_where(condition, active=False)` and `User.delete_where(condition)` require conditions and return the number of affected rows. `update_where()` also increments each changed row's version.
To change or delete every row explicitly, use `User.id.is_not_null()`.
