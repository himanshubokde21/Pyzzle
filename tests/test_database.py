import os

import pytest

from pyzzle import Base, Column, Integer, String, connect, disconnect
from pyzzle.errors import AlreadyConnectedError


DATABASE_URL = os.environ.get("PYZZLE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set PYZZLE_TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.",
)


def test_execute_selects_and_statements_without_results(database, prepare_test_table):
    assert Base.execute("SELECT 1") == [(1,)]
    assert Base.execute("SELECT %s::int", (5,)) == [(5,)]
    assert Base.execute("SELECT '100%'") == [("100%",)]
    prepare_test_table("pyzzle_test_resultless")
    assert Base.execute("CREATE TABLE pyzzle_test_resultless (id INTEGER)") == []


def test_connect_twice_and_reconnect(database):
    with pytest.raises(AlreadyConnectedError):
        connect(DATABASE_URL)
    disconnect()
    connect(DATABASE_URL)


def test_autocommit_run_commits_and_run_tx_rolls_back(database, prepare_test_table):
    prepare_test_table("pyzzle_test_scratch")
    Base.execute("CREATE TABLE pyzzle_test_scratch (value INTEGER)")
    assert Base.execute("INSERT INTO pyzzle_test_scratch VALUES (%s)", (7,)) == []
    assert Base.execute("SELECT value FROM pyzzle_test_scratch") == [(7,)]

    with pytest.raises(ZeroDivisionError):
        Base._run_tx(
            lambda conn: (
                conn.execute("INSERT INTO pyzzle_test_scratch VALUES (%s)", (9,)),
                1 / 0,
            )
        )
    assert Base.execute("SELECT value FROM pyzzle_test_scratch ORDER BY value") == [(7,)]

    with pytest.raises(ZeroDivisionError):
        Base._run(
            lambda conn: (
                conn.execute("INSERT INTO pyzzle_test_scratch VALUES (%s)", (11,)),
                1 / 0,
            )
        )
    assert Base.execute("SELECT value FROM pyzzle_test_scratch ORDER BY value") == [(7,), (11,)]


def test_autocommit_status_and_transaction_status(database):
    import psycopg

    autocommit, status = Base._run(
        lambda conn: (conn.autocommit, conn.info.transaction_status)
    )
    assert autocommit is True
    assert status is psycopg.pq.TransactionStatus.IDLE

    with Base.transaction():
        status = Base._run(lambda conn: conn.info.transaction_status)
        assert status is psycopg.pq.TransactionStatus.INTRANS


def test_connect_with_connection_check(database):
    from pyzzle import disconnect

    disconnect()
    connect(DATABASE_URL, check=True)
    assert Base.execute("SELECT 1") == [(1,)]


def test_create_tables_builds_expected_model_table(database, prepare_test_table):
    prepare_test_table("pyzzle_test_users")

    class User(Base):
        __tablename__ = "pyzzle_test_users"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        email = Column(String(100), unique=True)

    Base.create_tables()
    columns = Base.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
        ("pyzzle_test_users",),
    )
    assert columns == [("id",), ("name",), ("email",), ("version",)]

    row = Base.execute(
        'INSERT INTO "pyzzle_test_users" ("name") VALUES (%s) RETURNING *',
        ("Amit",),
    )[0]
    assert isinstance(row[0], int)
    assert row[1:] == ("Amit", None, 1)
    user = User._from_row(row)
    assert user.name == "Amit"
    assert user.version == 1

    Base.execute(
        'INSERT INTO "pyzzle_test_users" ("name", "email") VALUES (%s, %s)',
        ("First", "same@example.test"),
    )
    with pytest.raises(Exception):
        Base.execute(
            'INSERT INTO "pyzzle_test_users" ("name", "email") VALUES (%s, %s)',
            ("Second", "same@example.test"),
        )
    Base.create_tables()


def test_create_tables_rolls_back_all_tables_on_error(database, prepare_test_table, monkeypatch):
    import pyzzle.core.base as base_module

    first_table = "pyzzle_test_ddl_first"
    second_table = "pyzzle_test_ddl_second"
    prepare_test_table(first_table)
    prepare_test_table(second_table)

    class First(Base):
        __tablename__ = first_table
        id = Column(Integer, primary_key=True)

    class Second(Base):
        __tablename__ = second_table
        id = Column(Integer, primary_key=True)

    original = base_module.build_create_table
    calls = []

    def fail_on_second(table, columns):
        calls.append(table)
        if len(calls) == 2:
            raise RuntimeError("DDL failure")
        return original(table, columns)

    monkeypatch.setattr(base_module, "build_create_table", fail_on_second)
    with pytest.raises(RuntimeError, match="DDL failure"):
        Base.create_tables()
    assert Base.execute("SELECT to_regclass(%s), to_regclass(%s)", (first_table, second_table)) == [(None, None)]
