"""Column descriptors and their SQL specifications."""

from pyzzle.core.types import Boolean, DateTime, Integer, String
from pyzzle.sql.builder import ColumnSpec
from pyzzle.sql.conditions import Comparison, InList, NullCheck, OrderBy, Pattern


class Column:
    """A typed model field with change tracking."""

    def __init__(self, type, *, primary_key=False, nullable=True, unique=False):
        supported = (Integer, String, Boolean, DateTime)
        if type in supported:
            type = type()
        elif not isinstance(type, supported):
            raise TypeError("Column type must be Integer, String, Boolean, or DateTime")
        self.type = type
        self.primary_key = primary_key
        self.nullable = nullable
        self.unique = unique
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj.__dict__.get(self.name)

    def __set__(self, obj, value):
        old = obj.__dict__.get(self.name)
        if value is old or (
            not isinstance(value, Column)
            and not isinstance(old, Column)
            and value == old
        ):
            return
        changed = obj.__dict__.get("_changed")
        if changed is None:
            changed = {}
            obj.__dict__["_changed"] = changed
        changed.setdefault(self.name, old)
        original = changed[self.name]
        if value is original or (
            not isinstance(value, Column) and not isinstance(original, Column)
            and value == original
        ):
            del changed[self.name]
        obj.__dict__[self.name] = value

    def __hash__(self):
        return id(self)

    def _compare(self, operator, other):
        if isinstance(other, Column):
            raise TypeError("Comparing two columns is not supported yet")
        return Comparison(self.name, operator, other)

    def __eq__(self, other):
        return self._compare("=", other)

    def __ne__(self, other):
        return self._compare("<>", other)

    def __lt__(self, other):
        return self._compare("<", other)

    def __le__(self, other):
        return self._compare("<=", other)

    def __gt__(self, other):
        return self._compare(">", other)

    def __ge__(self, other):
        return self._compare(">=", other)

    def in_(self, values):
        """Values are sent as one array parameter, so the number of values is not limited."""
        if not isinstance(values, (list, tuple, set, frozenset)):
            raise TypeError("in_() needs a list, tuple, set, or frozenset")
        if isinstance(values, (set, frozenset)):
            values = list(values)
        return InList(self.name, values)

    def like(self, pattern):
        return Pattern(self.name, "LIKE", pattern)

    def ilike(self, pattern):
        return Pattern(self.name, "ILIKE", pattern)

    def is_null(self):
        return NullCheck(self.name)

    def is_not_null(self):
        return NullCheck(self.name, negated=True)

    def asc(self):
        return OrderBy(self.name, "ASC")

    def desc(self):
        return OrderBy(self.name, "DESC")

    def _to_spec(self) -> ColumnSpec:
        """Return the SQL builder specification for this column."""
        return ColumnSpec(
            self.name,
            self.type.sql_type,
            primary_key=self.primary_key,
            identity=self.primary_key,
            nullable=self.nullable,
            unique=self.unique,
        )


class _VersionColumn(Column):
    """Hidden, Pyzzle-managed model version field."""

    def __init__(self):
        super().__init__(Integer, nullable=False)

    def __set__(self, obj, value):
        raise AttributeError("version is managed by Pyzzle")

    def _to_spec(self) -> ColumnSpec:
        """Return the SQL specification for the hidden version field."""
        return ColumnSpec("version", "INTEGER", nullable=False, default="1")
