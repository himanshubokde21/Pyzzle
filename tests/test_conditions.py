import pytest

from pyzzle import Base, Column, Integer, String
from pyzzle.errors import ModelDefinitionError
from pyzzle.sql.conditions import Comparison, and_, or_


@pytest.fixture
def models():
    original = Base._registry.copy()
    Base._registry.clear()

    class User(Base):
        __tablename__ = "condition_users"
        id = Column(Integer, primary_key=True)
        age = Column(Integer)
        name = Column(String)
        email = Column(String)

    try:
        yield User
    finally:
        Base._registry.clear()
        Base._registry.update(original)


def test_comparison_rendering_and_null_comparisons(models):
    User = models
    assert (User.age > 18).render() == ('"age" > %s', (18,))
    assert (User.age >= 18).render() == ('"age" >= %s', (18,))
    assert (User.age < 18).render() == ('"age" < %s', (18,))
    assert (User.age <= 18).render() == ('"age" <= %s', (18,))
    assert (User.age == 18).render() == ('"age" = %s', (18,))
    assert (User.age != 18).render() == ('"age" <> %s', (18,))
    assert (User.email == None).render() == ('"email" IS NULL', ())
    assert (User.email != None).render() == ('"email" IS NOT NULL', ())


@pytest.mark.parametrize("operator", ["<", "<=", ">", ">="])
def test_none_only_supports_equality_operators(models, operator):
    with pytest.raises(ValueError):
        Comparison("age", operator, None)


def test_in_list_rendering(models):
    User = models
    assert User.id.in_([1, 2, 3]).render() == ('"id" = ANY(%s)', ([1, 2, 3],))
    assert User.id.in_((1, 2, 3)).render() == ('"id" = ANY(%s)', ([1, 2, 3],))
    sql, params = User.id.in_({1, 2, 3}).render()
    assert sql == '"id" = ANY(%s)'
    assert set(params[0]) == {1, 2, 3}
    sql, params = User.id.in_(frozenset({1, 2, 3})).render()
    assert sql == '"id" = ANY(%s)' and set(params[0]) == {1, 2, 3}
    assert User.id.in_([]).render() == ("FALSE", ())
    assert User.id.in_(set()).render() == ("FALSE", ())
    assert User.id.in_((3, 2, 1)).render() == ('"id" = ANY(%s)', ([3, 2, 1],))
    with pytest.raises(TypeError):
        User.id.in_(range(3))
    with pytest.raises(TypeError):
        User.name.in_("abc")
    with pytest.raises(TypeError):
        User.name.in_(b"abc")
    assert User.id.in_({1, "a"}).render()[1][0]


def test_like_ilike_and_null_helpers(models):
    User = models
    assert User.name.like("A%").render() == ('"name" LIKE %s', ("A%",))
    assert User.name.ilike("A%").render() == ('"name" ILIKE %s', ("A%",))
    assert User.email.is_null().render() == ('"email" IS NULL', ())
    assert User.email.is_not_null().render() == ('"email" IS NOT NULL', ())


def test_boolean_operators_render_parenthesized_sql(models):
    User = models
    both = (User.age > 18) & (User.name == "Amit")
    assert both.render() == ('("age" > %s AND "name" = %s)', (18, "Amit"))
    either = (User.age < 18) | (User.age > 65)
    assert either.render() == ('("age" < %s OR "age" > %s)', (18, 65))
    mixed = ((User.age > 18) & (User.name == "Amit")) | (User.id < 100)
    assert mixed.render() == (
        '(("age" > %s AND "name" = %s) OR "id" < %s)', (18, "Amit", 100)
    )
    assert (~(User.age > 18)).render() == ('NOT ("age" > %s)', (18,))
    flattened = (User.age > 18) & (User.id > 1) & (User.name == "A")
    assert len(flattened.parts) == 3


def test_and_or_helpers_and_truth_testing(models):
    User = models
    age = User.age > 18
    name = User.name == "Amit"
    assert and_(age) is age
    assert or_(age) is age
    with pytest.raises(ValueError):
        and_()
    with pytest.raises(ValueError):
        or_()
    with pytest.raises(TypeError, match=r"Use & \| ~"):
        bool(age)


def test_column_operator_errors_and_hashing(models):
    User = models
    with pytest.raises(TypeError, match="Comparing two columns is not supported yet"):
        User.age == User.id
    with pytest.raises(TypeError, match=r"Use & \| ~"):
        18 < User.age < 30
    columns = {User.age: "age", User.id: "id"}
    assert columns[User.age] == "age"
    assert columns[User.id] == "id"


def test_conditions_parameterize_values_and_validate_identifiers(models):
    User = models
    malicious = "'; DROP TABLE users;--"
    sql, params = (User.name == malicious).render()
    assert malicious not in sql
    assert params == (malicious,)
    bad = Comparison('name"; DROP TABLE users;--', "=", malicious)
    with pytest.raises(ValueError):
        bad.render()
    with pytest.raises(ModelDefinitionError):
        class BadColumn(Base):
            __tablename__ = "condition_bad_columns"
            id = Column(Integer, primary_key=True)
            locals()['bad"; DROP TABLE users;--'] = Column(String)
