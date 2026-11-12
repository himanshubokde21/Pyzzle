"""Connection pool wrapper around psycopg_pool."""

from contextlib import contextmanager
from contextvars import ContextVar

from pyzzle.errors import AlreadyConnectedError, NotConnectedError, PyzzleError


_pool = None
_current = ContextVar("pyzzle_current_connection", default=None)


def connect(
    url: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    timeout: float = 30.0,
    check: bool = False,
) -> None:
    """Set check=True if your database may restart or drop idle connections; it costs one extra round trip per operation."""
    global _pool
    if _pool is not None:
        raise AlreadyConnectedError()

    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=max(1, int(timeout))):
            pass
    except (psycopg.Error, OSError) as error:
        raise PyzzleError("Could not connect to the database.") from error

    from psycopg_pool import ConnectionPool

    pool_options = {
        "min_size": min_size,
        "max_size": max_size,
        "timeout": timeout,
        "open": True,
        "kwargs": {"autocommit": True},
    }
    if check:
        pool_options["check"] = ConnectionPool.check_connection
    pool = ConnectionPool(url, **pool_options)
    try:
        pool.wait(timeout=timeout)
    except Exception as error:
        try:
            pool.close(timeout=1)
        except Exception:
            pass
        _pool = None
        raise PyzzleError("Could not connect to the database.") from error
    _pool = pool


def disconnect() -> None:
    """Close the process-wide pool if it is open."""
    global _pool
    if _pool is not None:
        pool = _pool
        _pool = None
        pool.close()


@contextmanager
def connection():
    """Borrow a pooled connection, or reuse the active transaction connection."""
    current = _current.get()
    if current is not None:
        yield current
        return
    if _pool is None:
        raise NotConnectedError()
    with _pool.connection() as conn:
        yield conn


@contextmanager
def transaction():
    """Run a transaction; nested blocks use savepoints."""
    if _pool is None:
        raise NotConnectedError()
    conn = _current.get()
    if conn is not None:
        with conn.transaction():
            yield
        return
    with _pool.connection() as conn:
        with conn.transaction():
            token = _current.set(conn)
            try:
                yield
            finally:
                _current.reset(token)


def in_transaction() -> bool:
    return _current.get() is not None
