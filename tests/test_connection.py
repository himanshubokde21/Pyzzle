import subprocess
import sys
import time
import traceback

import pytest

from pyzzle import Base, connect, disconnect
from pyzzle.errors import NotConnectedError, PyzzleError
from pyzzle.pool import connection


@pytest.fixture(autouse=True)
def disconnected_pool():
    disconnect()
    yield
    disconnect()


def test_database_operations_require_a_connection():
    with pytest.raises(NotConnectedError):
        Base.execute("SELECT 1")
    with pytest.raises(NotConnectedError):
        with connection():
            pass


def test_disconnect_when_not_connected_is_safe():
    disconnect()


def test_unreachable_connection_fails_fast_without_leaking_url_or_leaving_pool():
    url = "postgresql://user:secret@127.0.0.1:1/db"
    started = time.perf_counter()
    with pytest.raises(PyzzleError) as error_info:
        connect(url, timeout=2)
    elapsed = time.perf_counter() - started
    error = error_info.value
    assert str(error) == "Could not connect to the database."
    assert "secret" not in str(error)
    assert "secret" not in "".join(traceback.format_exception(error))
    # Refused connections can take about 2 seconds per attempt on Windows.
    assert elapsed < 10
    with pytest.raises(NotConnectedError):
        Base.execute("SELECT 1")
    with pytest.raises(PyzzleError) as retry_error:
        connect(url, timeout=2)
    assert not isinstance(retry_error.value, NotConnectedError)
    assert "secret" not in str(retry_error.value)


def test_transaction_requires_a_connection():
    with pytest.raises(NotConnectedError):
        with Base.transaction():
            pass


def test_importing_package_does_not_load_psycopg_drivers():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pyzzle; from pyzzle import connect, disconnect, Base; "
            "assert connect and disconnect and Base; "
            "assert 'psycopg' not in sys.modules; assert 'psycopg_pool' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
