"""Column types supported by Pyzzle models."""


class Integer:
    """PostgreSQL integer column type."""

    sql_type = "INTEGER"


class Boolean:
    """PostgreSQL boolean column type."""

    sql_type = "BOOLEAN"


class DateTime:
    """PostgreSQL timestamp with time zone column type."""

    sql_type = "TIMESTAMPTZ"


class String:
    """PostgreSQL text or bounded varchar column type."""

    def __init__(self, length=None):
        if length is not None and (type(length) is not int or length <= 0):
            raise ValueError("String length must be a positive integer")
        self.sql_type = "TEXT" if length is None else f"VARCHAR({length})"
