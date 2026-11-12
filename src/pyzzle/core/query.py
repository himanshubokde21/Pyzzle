"""Immutable model queries and result conversion."""

from collections import namedtuple

from pyzzle.core.column import Column
from pyzzle.errors import QueryError, TooManyRowsError
from pyzzle.sql import Condition, OrderBy, and_, count as build_count, select as build_select


_row_types = {}
_UNSET = object()


def _validate_condition_columns(model, conditions):
    for condition in conditions:
        for name in condition.column_names():
            if name not in model._all_columns:
                raise QueryError(f"Unknown column {name!r} for {model.__name__}")


def _row_type(names):
    row_type = _row_types.get(names)
    if row_type is None:
        row_type = namedtuple("Row", names)
        _row_types[names] = row_type
    return row_type


class Query:
    """Immutable query for one model."""

    __slots__ = ("model", "selected", "condition", "ordering", "_limit", "_offset", "_unlimited")

    def __init__(self, model, selected=(), condition=None, ordering=(), limit=None, offset=None, unlimited=False):
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "selected", tuple(selected))
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "ordering", tuple(ordering))
        object.__setattr__(self, "_limit", limit)
        object.__setattr__(self, "_offset", offset)
        object.__setattr__(self, "_unlimited", unlimited)

    def __setattr__(self, name, value):
        raise AttributeError("Query objects are immutable")

    def _new(self, *, condition=_UNSET, ordering=_UNSET, limit=_UNSET, offset=_UNSET, unlimited=_UNSET):
        return Query(
            self.model,
            self.selected,
            self.condition if condition is _UNSET else condition,
            self.ordering if ordering is _UNSET else ordering,
            self._limit if limit is _UNSET else limit,
            self._offset if offset is _UNSET else offset,
            self._unlimited if unlimited is _UNSET else unlimited,
        )

    def where(self, *conditions):
        if not conditions or any(not isinstance(condition, Condition) for condition in conditions):
            raise TypeError("where() needs conditions such as User.age > 18")
        _validate_condition_columns(self.model, conditions)
        combined = and_(*conditions)
        if self.condition is not None:
            combined = self.condition & combined
        return self._new(condition=combined)

    def order_by(self, *items):
        additions = []
        for item in items:
            if isinstance(item, Column):
                name = item.name
                order_item = OrderBy(name, "ASC")
            elif isinstance(item, OrderBy):
                name = item.column
                order_item = item
            else:
                raise TypeError("order_by() needs Columns or OrderBy values")
            if name not in self.model._all_columns:
                raise QueryError(f"Unknown column {name!r} for {self.model.__name__}")
            additions.append(order_item)
        return self._new(ordering=self.ordering + tuple(additions))

    def limit(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise QueryError("limit() needs an int greater than or equal to 0")
        return self._new(limit=value)

    def offset(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise QueryError("offset() needs an int greater than or equal to 0")
        return self._new(offset=value)

    def to_sql(self):
        columns = [column.name for column in self.selected]
        if not columns:
            columns = list(self.model._columns)
        return build_select(
            self.model.__tablename__,
            columns,
            where=self.condition,
            order_by=self.ordering,
            limit=self._limit,
            offset=self._offset,
        )

    def all(self):
        ".all() adds LIMIT max_rows + 1 at run time only to detect an overflow."
        statement = self.to_sql()
        max_rows = self.model.max_rows
        guarded = self._limit is None and not self._unlimited and max_rows is not None
        if guarded:
            statement = build_select(
                self.model.__tablename__,
                [column.name for column in self.selected] or list(self.model._columns),
                where=self.condition,
                order_by=self.ordering,
                limit=max_rows + 1,
                offset=self._offset,
            )
        rows = self.model.execute(statement.sql, statement.params)
        if guarded and len(rows) > max_rows:
            raise TooManyRowsError(
                f"Query returned more than {max_rows} rows. Add limit(), page with offset(), or call unlimited()."
            )
        names = tuple(column.name for column in self.selected) or tuple(self.model._columns)
        row_type = _row_type(names)
        return [row_type(*row) for row in rows]

    def unlimited(self):
        """Return a query without the automatic row guard."""
        return self._new(unlimited=True)

    def first(self):
        rows = self.limit(1).all()
        return rows[0] if rows else None

    def count(self):
        statement = build_count(self.model.__tablename__, self.condition)
        rows = self.model.execute(statement.sql, statement.params)
        return rows[0][0]

    def __iter__(self):
        return iter(self.all())

    def __repr__(self):
        return f"Query({self.to_sql().sql!r})"
