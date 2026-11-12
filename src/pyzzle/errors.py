"""Pyzzle exception types."""


class PyzzleError(Exception):
    """Base exception for Pyzzle errors."""


class ModelDefinitionError(PyzzleError):
    """Raised when a model class is defined incorrectly."""


class NotConnectedError(PyzzleError):
    """Raised when a database operation is attempted without a connection pool."""

    def __init__(self):
        super().__init__("Not connected. Call pyzzle.connect(url) first.")


class AlreadyConnectedError(PyzzleError):
    """Raised when connect is called while a pool is already active."""

    def __init__(self):
        super().__init__("Already connected. Call pyzzle.disconnect() first.")


class NotFound(PyzzleError):
    """Raised when a model row no longer exists."""

    def __init__(self, table, pk_value):
        super().__init__(f"No row found in table {table!r} with primary key {pk_value!r}.")


class VersionConflict(PyzzleError):
    """Raised when a model row has a newer version than the loaded object."""

    def __init__(self, table, pk_value):
        super().__init__(f"Row in table {table!r} with primary key {pk_value!r} was changed by someone else.")


class QueryError(PyzzleError):
    """Raised when a query is invalid for its model."""


class TooManyRowsError(QueryError):
    """Raised when an unbounded query exceeds its configured row limit."""
