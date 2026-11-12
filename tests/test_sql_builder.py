import subprocess
import sys

import pytest

from pyzzle.sql import (
    ColumnSpec,
    Statement,
    create_table,
    delete,
    insert,
    quote_ident,
    select_by_pk,
    update,
)


def test_quote_ident():
    assert quote_ident("users") == '"users"'


def test_insert():
    statement = insert("users", {"name": "Amit", "age": 20})
    assert statement.sql == 'INSERT INTO "users" ("name", "age") VALUES (%s, %s) RETURNING *'
    assert statement.params == ("Amit", 20)


def test_insert_returning_columns():
    statement = insert("users", {"name": "Amit"}, returning=["id", "name", "version"])
    assert statement.sql == 'INSERT INTO "users" ("name") VALUES (%s) RETURNING "id", "name", "version"'
    assert statement.params == ("Amit",)


def test_insert_default_values():
    statement = insert("users", {})
    assert statement.sql == 'INSERT INTO "users" DEFAULT VALUES RETURNING *'
    assert statement.params == ()


def test_select_by_pk():
    statement = select_by_pk("users", ["id", "name"], "id", 1)
    assert statement.sql == 'SELECT "id", "name" FROM "users" WHERE "id" = %s'
    assert statement.params == (1,)


def test_update():
    statement = update(
        "users",
        {"name": "Rahul", "age": 21},
        "id",
        1,
        "version",
        3,
        returning=["id", "name", "age", "version"],
    )
    assert statement.sql == (
        'UPDATE "users" SET "name" = %s, "age" = %s, "version" = "version" + 1 '
        'WHERE "id" = %s AND "version" = %s RETURNING "id", "name", "age", "version"'
    )
    assert statement.params == ("Rahul", 21, 1, 3)


def test_delete_without_version():
    statement = delete("users", "id", 1)
    assert statement.sql == 'DELETE FROM "users" WHERE "id" = %s'
    assert statement.params == (1,)


def test_delete_with_version():
    statement = delete("users", "id", 1, version_column="version", version_value=3)
    assert statement.sql == 'DELETE FROM "users" WHERE "id" = %s AND "version" = %s'
    assert statement.params == (1, 3)


def test_create_table():
    sql = create_table(
        "users",
        [
            ColumnSpec("id", "INTEGER", primary_key=True, identity=True),
            ColumnSpec("name", "TEXT", nullable=False),
            ColumnSpec("email", "VARCHAR(100)", unique=True),
            ColumnSpec("version", "INTEGER", nullable=False, default="1"),
        ],
    )
    assert sql == (
        'CREATE TABLE IF NOT EXISTS "users" '
        '("id" INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "name" TEXT NOT NULL, '
        '"email" VARCHAR(100) UNIQUE, "version" INTEGER NOT NULL DEFAULT 1)'
    )


@pytest.mark.parametrize(
    "call",
    [
        lambda: quote_ident('users"; DROP TABLE x;--'),
        lambda: insert("users", {"bad-name": 1}),
        lambda: select_by_pk("users", [], "id", 1),
        lambda: update("users", {}, "id", 1, "version", 1),
        lambda: update("users", {"id": 2}, "id", 1, "version", 1),
        lambda: update("users", {"version": 2}, "id", 1, "version", 1),
        lambda: delete("users", "id", 1, version_column="version"),
        lambda: delete("users", "id", 1, version_value=1),
        lambda: create_table("users", []),
        lambda: create_table("users", [ColumnSpec("id", "INTEGER")]),
        lambda: create_table(
            "users", [ColumnSpec("id", "INTEGER", primary_key=True), ColumnSpec("id", "TEXT")]
        ),
        lambda: create_table(
            "users",
            [ColumnSpec("id", "INTEGER", primary_key=True), ColumnSpec("other", "INTEGER", primary_key=True)],
        ),
        lambda: create_table(
            "users", [ColumnSpec("id", "INTEGER", primary_key=True), ColumnSpec("other", "INTEGER", identity=True)]
        ),
        lambda: create_table("users", [ColumnSpec("id", "integer", primary_key=True)]),
        lambda: create_table("bad-name", [ColumnSpec("id", "INTEGER", primary_key=True)]),
        lambda: create_table("users", [ColumnSpec("bad-name", "INTEGER", primary_key=True)]),
    ],
)
def test_invalid_inputs_raise_value_error(call):
    with pytest.raises(ValueError):
        call()


def test_malicious_value_is_only_a_parameter():
    value = "'; DROP TABLE users;--"
    statement = insert("users", {"name": value})
    assert value not in statement.sql
    assert statement.params == (value,)


def test_statement_can_be_unpacked():
    sql, params = insert("users", {"name": "Amit"})
    assert sql == 'INSERT INTO "users" ("name") VALUES (%s) RETURNING *'
    assert params == ("Amit",)
    assert isinstance(insert("users", {}), Statement)


def test_importing_sql_does_not_load_psycopg_drivers():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pyzzle.sql; assert 'psycopg' not in sys.modules; "
            "assert 'psycopg_pool' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
