"""Pure PostgreSQL SQL statement builders."""

import re
from typing import NamedTuple, Sequence

from .conditions import Condition, OrderBy


class Statement(NamedTuple):
    """SQL text and its ordered parameter values."""

    sql: str
    params: tuple


class ColumnSpec(NamedTuple):
    """Column definition; default is a trusted Pyzzle SQL expression, never user input."""

    name: str
    sql_type: str
    primary_key: bool = False
    identity: bool = False
    nullable: bool = True
    unique: bool = False
    default: str | None = None


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SQL_TYPE = re.compile(r"^[A-Z][A-Z0-9_ ]*(\(\d+(,\s*\d+)?\))?$")


def quote_ident(name: str) -> str:
    """Validate and double-quote a PostgreSQL identifier."""
    if not isinstance(name, str) or _IDENTIFIER.fullmatch(name) is None:
        raise ValueError(f"Invalid identifier: {name!r}")
    return f'"{name}"'


def insert(table, values: dict, returning: Sequence[str] | None = None) -> Statement:
    """Build a parameterized INSERT statement."""
    table_sql = quote_ident(table)
    returning_sql = "*" if returning is None else ", ".join(quote_ident(name) for name in returning)
    if not values:
        return Statement(f"INSERT INTO {table_sql} DEFAULT VALUES RETURNING {returning_sql}", ())
    names = [quote_ident(name) for name in values]
    placeholders = ", ".join(["%s"] * len(values))
    sql = f"INSERT INTO {table_sql} ({', '.join(names)}) VALUES ({placeholders}) RETURNING {returning_sql}"
    return Statement(sql, tuple(values.values()))


def select_by_pk(table, columns: Sequence[str], pk_column: str, pk_value) -> Statement:
    """Build a parameterized SELECT by primary key."""
    if not columns:
        raise ValueError("SELECT requires at least one column")
    columns_sql = ", ".join(quote_ident(column) for column in columns)
    return Statement(
        f"SELECT {columns_sql} FROM {quote_ident(table)} WHERE {quote_ident(pk_column)} = %s",
        (pk_value,),
    )


def _validate_limit(value, label):
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{label} must be an int greater than or equal to 0")


def select(
    table,
    columns: Sequence[str],
    where: Condition | None = None,
    order_by: Sequence[OrderBy] = (),
    limit: int | None = None,
    offset: int | None = None,
) -> Statement:
    """Build a parameterized SELECT statement."""
    if not columns:
        raise ValueError("SELECT requires at least one column")
    _validate_limit(limit, "limit")
    _validate_limit(offset, "offset")
    sql = f"SELECT {', '.join(quote_ident(column) for column in columns)} FROM {quote_ident(table)}"
    params = ()
    if where is not None:
        if not isinstance(where, Condition):
            raise TypeError("where must be a Condition")
        where_sql, where_params = where.render()
        sql += f" WHERE {where_sql}"
        params += where_params
    if order_by:
        order_terms = []
        for item in order_by:
            if not isinstance(item, OrderBy):
                raise ValueError("order_by items must be OrderBy values")
            if item.direction not in ("ASC", "DESC"):
                raise ValueError("order_by direction must be ASC or DESC")
            order_terms.append(f"{quote_ident(item.column)} {item.direction}")
        sql += " ORDER BY " + ", ".join(order_terms)
    if limit is not None:
        sql += " LIMIT %s"
        params += (limit,)
    if offset is not None:
        sql += " OFFSET %s"
        params += (offset,)
    return Statement(sql, params)


def count(table, where: Condition | None = None) -> Statement:
    """Build a parameterized COUNT(*) statement."""
    sql = f"SELECT COUNT(*) FROM {quote_ident(table)}"
    params = ()
    if where is not None:
        if not isinstance(where, Condition):
            raise TypeError("where must be a Condition")
        where_sql, params = where.render()
        sql += f" WHERE {where_sql}"
    return Statement(sql, params)


def update(
    table,
    changes: dict,
    pk_column: str,
    pk_value,
    version_column: str,
    version_value,
    returning: Sequence[str] | None = None,
) -> Statement:
    """Build a version-checked parameterized UPDATE."""
    if not changes:
        raise ValueError("UPDATE requires at least one changed column")
    if pk_column in changes or version_column in changes:
        raise ValueError("Changes cannot include the primary key or version column")
    table_sql = quote_ident(table)
    pk_sql = quote_ident(pk_column)
    version_sql = quote_ident(version_column)
    assignments = [f"{quote_ident(name)} = %s" for name in changes]
    assignments.append(f'{version_sql} = {version_sql} + 1')
    returning_sql = "*" if returning is None else ", ".join(quote_ident(name) for name in returning)
    sql = (
        f"UPDATE {table_sql} SET {', '.join(assignments)} WHERE {pk_sql} = %s "
        f"AND {version_sql} = %s RETURNING {returning_sql}"
    )
    return Statement(sql, (*changes.values(), pk_value, version_value))


def update_where(table, changes, where, version_column="version") -> Statement:
    """Build a parameterized UPDATE for rows matching a condition."""
    if not changes:
        raise ValueError("UPDATE requires at least one changed column")
    if where is None:
        raise ValueError("UPDATE requires a condition")
    if not isinstance(where, Condition):
        raise TypeError("where must be a Condition")
    if version_column in changes:
        raise ValueError("Changes cannot include the version column")
    table_sql = quote_ident(table)
    assignments = [f"{quote_ident(name)} = %s" for name in changes]
    version_sql = quote_ident(version_column)
    assignments.append(f'{version_sql} = {version_sql} + 1')
    where_sql, where_params = where.render()
    return Statement(
        f"UPDATE {table_sql} SET {', '.join(assignments)} WHERE {where_sql}",
        (*changes.values(), *where_params),
    )


def delete_where(table, where) -> Statement:
    """Build a parameterized DELETE for rows matching a condition."""
    if where is None:
        raise ValueError("DELETE requires a condition")
    if not isinstance(where, Condition):
        raise TypeError("where must be a Condition")
    where_sql, params = where.render()
    return Statement(f"DELETE FROM {quote_ident(table)} WHERE {where_sql}", params)


def delete(table, pk_column, pk_value, version_column=None, version_value=None) -> Statement:
    """Build a parameterized DELETE, optionally guarded by a version."""
    if (version_column is None) != (version_value is None):
        raise ValueError("version_column and version_value must be provided together")
    sql = f"DELETE FROM {quote_ident(table)} WHERE {quote_ident(pk_column)} = %s"
    params = (pk_value,)
    if version_column is not None:
        version_sql = quote_ident(version_column)
        sql += f" AND {version_sql} = %s"
        params += (version_value,)
    return Statement(sql, params)


def create_table(table, columns: Sequence[ColumnSpec]) -> str:
    """Build CREATE TABLE SQL from validated column specifications."""
    if not columns:
        raise ValueError("CREATE TABLE requires at least one column")
    names = [column.name for column in columns]
    if len(set(names)) != len(names):
        raise ValueError("Column names must be unique")
    if sum(column.primary_key for column in columns) != 1:
        raise ValueError("Exactly one primary key is required")

    definitions = []
    for column in columns:
        name_sql = quote_ident(column.name)
        if not isinstance(column.sql_type, str) or _SQL_TYPE.fullmatch(column.sql_type) is None:
            raise ValueError(f"Invalid SQL type: {column.sql_type!r}")
        if column.identity and not column.primary_key:
            raise ValueError("Identity requires primary_key=True")
        parts = [name_sql, column.sql_type]
        if column.identity:
            parts.append("GENERATED ALWAYS AS IDENTITY")
        if column.primary_key:
            parts.append("PRIMARY KEY")
        elif not column.nullable:
            parts.append("NOT NULL")
        if column.unique:
            parts.append("UNIQUE")
        if column.default is not None:
            parts.extend(("DEFAULT", column.default))
        definitions.append(" ".join(parts))
    return f'CREATE TABLE IF NOT EXISTS {quote_ident(table)} ({", ".join(definitions)})'
