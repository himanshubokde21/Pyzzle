import os
import threading
import time
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import pytest

from pyzzle import Base, Column, Integer, String, connect, disconnect
from pyzzle.errors import PyzzleError, VersionConflict


DATABASE_URL = os.environ.get("PYZZLE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set PYZZLE_TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.",
)


@pytest.fixture
def user_model(database, prepare_test_table):
    table_name = "pyzzle_test_users"
    prepare_test_table(table_name)

    class User(Base):
        __tablename__ = table_name
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        email = Column(String(100), unique=True)

    Base.create_tables()
    return User


@pytest.fixture
def outside_connection():
    import psycopg

    connection = psycopg.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.close()


def test_transaction_commits_all_rows(user_model, outside_connection):
    User = user_model
    with Base.transaction():
        first = User.create(name="Amit")
        second = User.create(name="Rahul")

    rows = outside_connection.execute(
        'SELECT "name" FROM "pyzzle_test_users" WHERE "id" IN (%s, %s) ORDER BY "id"',
        (first.id, second.id),
    ).fetchall()
    assert rows == [("Amit",), ("Rahul",)]


def test_transaction_isolation_and_read_your_writes(user_model, outside_connection):
    User = user_model
    with Base.transaction():
        user = User.create(name="Amit")
        assert User.get(user.id).name == "Amit"
        assert outside_connection.execute(
            'SELECT "id" FROM "pyzzle_test_users" WHERE "id" = %s', (user.id,)
        ).fetchall() == []

    assert outside_connection.execute(
        'SELECT "name" FROM "pyzzle_test_users" WHERE "id" = %s', (user.id,)
    ).fetchall() == [("Amit",)]


def test_transaction_reuses_same_connection(user_model):
    with Base.transaction():
        first_pid = Base.execute("SELECT pg_backend_pid()")[0][0]
        second_pid = Base.execute("SELECT pg_backend_pid()")[0][0]
    assert first_pid == second_pid


def test_transaction_rolls_back_on_python_error(user_model):
    User = user_model
    created = None
    with pytest.raises(ZeroDivisionError):
        with Base.transaction():
            created = User.create(name="Amit")
            1 / 0
    assert User.get(created.id) is None


def test_transaction_rolls_back_on_database_error(user_model):
    from psycopg.errors import UniqueViolation

    User = user_model
    with pytest.raises(UniqueViolation):
        with Base.transaction():
            User.create(name="Amit", email="a@x.com")
            User.create(name="Rahul", email="a@x.com")
    assert Base.execute('SELECT "id" FROM "pyzzle_test_users"') == []


def test_version_conflict_rolls_back_earlier_transaction_work(user_model):
    User = user_model
    a = User.create(name="A")
    b = User.create(name="B")
    a2 = User.get(a.id)
    b2 = User.get(b.id)
    b.name = "changed"
    b.save()

    with pytest.raises(VersionConflict):
        with Base.transaction():
            a2.name = "changed"
            a2.save()
            b2.name = "x"
            b2.save()

    assert User.get(a.id).name == "A"
    assert User.get(b.id).name == "changed"


def test_nested_transaction_savepoint_then_outer_error_rolls_back(user_model, outside_connection):
    User = user_model
    created_ids = []
    with pytest.raises(ZeroDivisionError):
        with Base.transaction():
            created_ids.append(User.create(name="outer").id)
            with Base.transaction():
                created_ids.append(User.create(name="inner").id)
            assert outside_connection.execute(
                'SELECT "id" FROM "pyzzle_test_users"'
            ).fetchall() == []
            1 / 0
    assert outside_connection.execute('SELECT "id" FROM "pyzzle_test_users"').fetchall() == []
    assert all(User.get(pk) is None for pk in created_ids)


def test_python_error_in_inner_savepoint_keeps_outer_transaction(user_model):
    User = user_model
    with Base.transaction():
        outer = User.create(name="outer")
        with pytest.raises(ZeroDivisionError):
            with Base.transaction():
                User.create(name="inner")
                1 / 0
        assert User.get(outer.id).name == "outer"
        assert Base.execute('SELECT "name" FROM "pyzzle_test_users" ORDER BY "id"') == [
            ("outer",)
        ]
    assert Base.execute('SELECT "name" FROM "pyzzle_test_users" ORDER BY "id"') == [
        ("outer",)
    ]


def test_database_error_in_inner_savepoint_keeps_outer_usable(user_model):
    from psycopg.errors import UniqueViolation

    User = user_model
    with Base.transaction():
        first = User.create(name="first", email="same@example.test")
        with pytest.raises(UniqueViolation):
            with Base.transaction():
                User.create(name="duplicate", email="same@example.test")
        last = User.create(name="last", email="last@example.test")

    assert Base.execute('SELECT "name" FROM "pyzzle_test_users" ORDER BY "id"') == [
        ("first",),
        ("last",),
    ]
    assert User.get(first.id) is not None
    assert User.get(last.id) is not None


def test_uncaught_inner_error_rolls_back_outer_transaction(user_model):
    User = user_model
    with pytest.raises(ZeroDivisionError):
        with Base.transaction():
            User.create(name="outer")
            with Base.transaction():
                User.create(name="inner")
                1 / 0
    assert Base.execute('SELECT "name" FROM "pyzzle_test_users"') == []


def test_context_cleanup_after_exception_and_next_transaction(user_model):
    User = user_model
    with pytest.raises(ZeroDivisionError):
        with Base.transaction():
            User.create(name="rolled back")
            1 / 0

    assert Base.execute("SELECT 1") == [(1,)]
    with Base.transaction():
        User.create(name="committed")
    assert Base.execute('SELECT "name" FROM "pyzzle_test_users"') == [("committed",)]


def test_thread_does_not_inherit_active_transaction_connection(user_model):
    User = user_model
    thread_result = []

    def count_committed_rows():
        thread_result.extend(Base.execute('SELECT count(*) FROM "pyzzle_test_users"'))

    with Base.transaction():
        User.create(name="uncommitted")
        thread = threading.Thread(target=count_committed_rows)
        thread.start()
        thread.join()
    assert thread_result == [(0,)]
    assert Base.execute('SELECT count(*) FROM "pyzzle_test_users"') == [(1,)]


def test_wrong_password_fails_fast_without_leaking_password(user_model):
    parts = urlsplit(DATABASE_URL)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port is not None else ""
    username = quote(unquote(parts.username or ""), safe="")
    netloc = f"{username}:definitely-wrong@{host}{port}"
    wrong_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    disconnect()
    started = time.perf_counter()
    try:
        with pytest.raises(PyzzleError) as error_info:
            connect(wrong_url, timeout=3)
        elapsed = time.perf_counter() - started
        assert elapsed < 5
        assert "definitely-wrong" not in str(error_info.value)
    finally:
        disconnect()
        connect(DATABASE_URL)
