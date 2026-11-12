"""Composable, parameterized PostgreSQL query conditions."""

from typing import NamedTuple


class Condition:
    """Base class for query conditions."""

    def render(self) -> tuple[str, tuple]:
        raise NotImplementedError

    def column_names(self) -> list[str]:
        raise NotImplementedError

    def __and__(self, other):
        if not isinstance(other, Condition):
            return NotImplemented
        left = self.parts if isinstance(self, And) else (self,)
        right = other.parts if isinstance(other, And) else (other,)
        return And(left + right)

    def __or__(self, other):
        if not isinstance(other, Condition):
            return NotImplemented
        left = self.parts if isinstance(self, Or) else (self,)
        right = other.parts if isinstance(other, Or) else (other,)
        return Or(left + right)

    def __invert__(self):
        return Not(self)

    def __bool__(self):
        raise TypeError(
            "Use & | ~ (with parentheses), or and_()/or_(), instead of and/or/not or chained comparisons."
        )


def _quote(name):
    from .builder import quote_ident

    return quote_ident(name)


class Comparison(Condition):
    __slots__ = ("name", "operator", "value")
    _operators = {"=", "<>", "<", "<=", ">", ">="}

    def __init__(self, name, operator, value):
        if operator not in self._operators:
            raise ValueError(f"Unsupported comparison operator: {operator!r}")
        if value is None and operator not in ("=", "<>"):
            raise ValueError("None supports only = and <> comparisons")
        self.name = name
        self.operator = operator
        self.value = value

    def render(self):
        column = _quote(self.name)
        if self.value is None:
            suffix = "IS NULL" if self.operator == "=" else "IS NOT NULL"
            return f"{column} {suffix}", ()
        return f"{column} {self.operator} %s", (self.value,)

    def column_names(self):
        return [self.name]


class InList(Condition):
    __slots__ = ("name", "values")

    def __init__(self, name, values):
        self.name = name
        self.values = tuple(values)

    def render(self):
        column = _quote(self.name)
        if not self.values:
            return "FALSE", ()
        return f"{column} = ANY(%s)", (list(self.values),)

    def column_names(self):
        return [self.name]


class Pattern(Condition):
    __slots__ = ("name", "operator", "pattern")

    def __init__(self, name, operator, pattern):
        self.name = name
        self.operator = operator
        self.pattern = pattern

    def render(self):
        return f"{_quote(self.name)} {self.operator} %s", (self.pattern,)

    def column_names(self):
        return [self.name]


class NullCheck(Condition):
    __slots__ = ("name", "negated")

    def __init__(self, name, negated=False):
        self.name = name
        self.negated = negated

    def render(self):
        suffix = "IS NOT NULL" if self.negated else "IS NULL"
        return f"{_quote(self.name)} {suffix}", ()

    def column_names(self):
        return [self.name]


class And(Condition):
    __slots__ = ("parts",)

    def __init__(self, parts):
        flattened = []
        for part in parts:
            flattened.extend(part.parts if isinstance(part, And) else (part,))
        self.parts = tuple(flattened)

    def render(self):
        rendered = [part.render() for part in self.parts]
        return "(" + " AND ".join(sql for sql, _ in rendered) + ")", tuple(
            param for _, params in rendered for param in params
        )

    def column_names(self):
        return [name for part in self.parts for name in part.column_names()]


class Or(Condition):
    __slots__ = ("parts",)

    def __init__(self, parts):
        flattened = []
        for part in parts:
            flattened.extend(part.parts if isinstance(part, Or) else (part,))
        self.parts = tuple(flattened)

    def render(self):
        rendered = [part.render() for part in self.parts]
        return "(" + " OR ".join(sql for sql, _ in rendered) + ")", tuple(
            param for _, params in rendered for param in params
        )

    def column_names(self):
        return [name for part in self.parts for name in part.column_names()]


class Not(Condition):
    __slots__ = ("condition",)

    def __init__(self, condition):
        self.condition = condition

    def render(self):
        sql, params = self.condition.render()
        return f"NOT ({sql})", params

    def column_names(self):
        return self.condition.column_names()


class OrderBy(NamedTuple):
    column: str
    direction: str


def and_(*conditions):
    if not conditions:
        raise ValueError("and_() needs at least one condition")
    if any(not isinstance(condition, Condition) for condition in conditions):
        raise TypeError("and_() arguments must be conditions")
    if len(conditions) == 1:
        return conditions[0]
    return And(conditions)


def or_(*conditions):
    if not conditions:
        raise ValueError("or_() needs at least one condition")
    if any(not isinstance(condition, Condition) for condition in conditions):
        raise TypeError("or_() arguments must be conditions")
    if len(conditions) == 1:
        return conditions[0]
    return Or(conditions)
