import subprocess
import sys

import pytest

from pyzzle import Base, Boolean, Column, Integer, String
from pyzzle.errors import ModelDefinitionError, QueryError, TooManyRowsError
from pyzzle.sql import OrderBy, Statement, count, select


@pytest.fixture
def models():
    original = Base._registry.copy()
    Base._registry.clear()

    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        age = Column(Integer)
        email = Column(String)
        active = Column(Boolean)

    class OtherModel(Base):
        __tablename__ = "other_models"
        id = Column(Integer, primary_key=True)
        total = Column(Integer)

    try:
        yield User, OtherModel
    finally:
        Base._registry.clear()
        Base._registry.update(original)


def test_select_with_selected_columns_and_default_model_columns(models):
    User, _ = models
    assert User.select(User.name, User.age).where(User.age > 18).to_sql() == Statement(
        'SELECT "name", "age" FROM "users" WHERE "age" > %s', (18,)
    )
    assert User.select().to_sql() == Statement(
        'SELECT "id", "name", "age", "email", "active" FROM "users"', ()
    )


def test_where_arguments_and_successive_calls_combine_with_and(models):
    User, _ = models
    combined = User.select().where(User.age > 18, User.active == True).to_sql()
    successive = User.select().where(User.age > 18).where(User.active == True).to_sql()
    expected_sql = (
        'SELECT "id", "name", "age", "email", "active" FROM "users" '
        'WHERE ("age" > %s AND "active" = %s)'
    )
    assert combined == Statement(expected_sql, (18, True))
    assert successive == Statement(expected_sql, (18, True))


def test_or_and_not_conditions(models):
    User, _ = models
    assert User.select().where((User.age < 18) | (User.age > 65)).to_sql().sql.endswith(
        'WHERE ("age" < %s OR "age" > %s)'
    )
    assert User.select().where(~(User.age > 18)).to_sql().sql.endswith(
        'WHERE NOT ("age" > %s)'
    )


def test_order_limit_offset_and_parameter_order(models):
    User, _ = models
    statement = User.select(User.name).order_by(User.name.asc(), User.age.desc()).limit(10).offset(20).to_sql()
    assert statement == Statement(
        'SELECT "name" FROM "users" ORDER BY "name" ASC, "age" DESC LIMIT %s OFFSET %s',
        (10, 20),
    )
    statement = User.select(User.name).where(User.age > 18).limit(5).to_sql()
    assert statement == Statement(
        'SELECT "name" FROM "users" WHERE "age" > %s LIMIT %s', (18, 5)
    )
    assert User.select(User.name).offset(4).to_sql().sql.endswith("OFFSET %s")


def test_query_methods_are_immutable(models):
    User, _ = models
    base = User.select()
    changed = base.where(User.age > 18).limit(10)
    assert base.to_sql() == Statement(
        'SELECT "id", "name", "age", "email", "active" FROM "users"', ()
    )
    assert changed is not base
    with pytest.raises(AttributeError, match="immutable"):
        base.model = User
    reordered = base.order_by(User.name).limit(5).offset(2).where(User.age > 18)
    assert reordered.to_sql() == Statement(
        'SELECT "id", "name", "age", "email", "active" FROM "users" '
        'WHERE "age" > %s ORDER BY "name" ASC LIMIT %s OFFSET %s',
        (18, 5, 2),
    )


def test_query_validation_errors(models):
    User, OtherModel = models
    with pytest.raises(TypeError, match=r"where\(\) needs conditions"):
        User.select().where("name")
    with pytest.raises(TypeError, match=r"where\(\) needs conditions"):
        User.select().where()
    with pytest.raises(QueryError, match="Unknown column"):
        User.select().where(OtherModel.total > 5)
    with pytest.raises(QueryError, match="not a column"):
        User.select(OtherModel.total)
    with pytest.raises(QueryError, match="more than once"):
        User.select(User.name, User.name)
    with pytest.raises(TypeError, match="Columns"):
        User.select("name")
    for bad in (-1, True, 1.5, "10"):
        with pytest.raises(QueryError):
            User.select().limit(bad)
        with pytest.raises(QueryError):
            User.select().offset(bad)
    with pytest.raises(QueryError, match="Unknown column"):
        User.select().order_by(OtherModel.total)


def test_sql_select_and_count_validation(models):
    User, _ = models
    with pytest.raises(ValueError):
        select("users", [])
    with pytest.raises(ValueError):
        select("users", ["id"], order_by=[OrderBy("id", "SIDEWAYS")])
    with pytest.raises(ValueError):
        select("users", ["id"], limit=True)
    with pytest.raises(ValueError):
        select("users", ["id"], offset=-1)
    statement = select("users", ["id"], where=User.age > 18, offset=2)
    assert statement == Statement(
        'SELECT "id" FROM "users" WHERE "age" > %s OFFSET %s', (18, 2)
    )
    assert count("users", User.age > 18) == Statement(
        'SELECT COUNT(*) FROM "users" WHERE "age" > %s', (18,)
    )


def test_first_count_and_result_rows(models, monkeypatch):
    User, _ = models
    calls = []

    def execute(cls, sql, params=None):
        calls.append((sql, params))
        if sql.startswith("SELECT COUNT(*)"):
            return [(7,)]
        return [("Amit", 20)]

    monkeypatch.setattr(User, "execute", classmethod(execute))
    query = User.select(User.name, User.age).where(User.age > 18)
    first = query.first()
    assert first.name == "Amit" and first.age == 20
    assert first[0] == "Amit"
    assert query.all()[0].__class__ is first.__class__
    assert query.count() == 7
    assert calls[-1] == ('SELECT COUNT(*) FROM "users" WHERE "age" > %s', (18,))
    assert "LIMIT" not in calls[-1][0]


def test_first_returns_none_and_all_returns_rows(models, monkeypatch):
    User, _ = models
    monkeypatch.setattr(User, "execute", classmethod(lambda cls, sql, params=None: []))
    assert User.select().first() is None

    monkeypatch.setattr(
        User,
        "execute",
        classmethod(lambda cls, sql, params=None: [(1, "Amit", 20, None, True)]),
    )
    result = User.select().all()[0]
    assert result.id == 1 and result.name == "Amit"
    assert not hasattr(result, "version")
    assert result[0] == 1
    assert User.select().all()[0].__class__ is result.__class__


def test_get_conditions_loads_saved_object_and_primary_key_stays_direct(models, monkeypatch):
    User, _ = models
    calls = []

    def execute(cls, sql, params=None):
        calls.append((sql, params))
        return [(1, "Amit", 20, "a@x.com", True, 4)]

    monkeypatch.setattr(User, "execute", classmethod(execute))
    user = User.get(User.email == "a@x.com")
    assert isinstance(user, User)
    assert user.version == 4 and "_changed" not in user.__dict__
    assert calls[-1] == (
        'SELECT "id", "name", "age", "email", "active", "version" FROM "users" '
        'WHERE "email" = %s LIMIT %s',
        ("a@x.com", 1),
    )
    User.get(1)
    assert calls[-1] == ('SELECT "id", "name", "age", "email", "active", "version" FROM "users" WHERE "id" = %s', (1,))


def test_get_condition_validation_and_argument_errors(models, monkeypatch):
    User, OtherModel = models
    monkeypatch.setattr(User, "execute", classmethod(lambda cls, sql, params=None: []))
    assert User.get(User.age > 3) is None
    with pytest.raises(TypeError, match="one primary key value"):
        User.get()
    with pytest.raises(TypeError, match="one primary key value"):
        User.get(1, User.age > 3)
    with pytest.raises(QueryError, match="Unknown column"):
        User.get(OtherModel.total > 3)


def test_row_limit_guard(models, monkeypatch):
    User, _ = models
    monkeypatch.setattr(User, "max_rows", 10)
    received = []

    def execute(cls, sql, params=None):
        received.append((sql, params))
        if "LIMIT" in sql:
            count = params[-1]
            return [(n, f"u{n}", n, None, True) for n in range(count)]
        return [(n, f"u{n}", n, None, True) for n in range(10)]

    monkeypatch.setattr(User, "execute", classmethod(execute))
    query = User.select()
    assert "LIMIT" not in query.to_sql().sql
    with pytest.raises(TooManyRowsError, match="more than 10 rows"):
        query.all()
    assert received[-1][1] == (11,)

    def ten_rows(cls, sql, params=None):
        received.append((sql, params))
        return [(n, "x", n, None, True) for n in range(10)]

    monkeypatch.setattr(User, "execute", classmethod(ten_rows))
    assert len(query.all()) == 10
    assert len(query.limit(20).all()) == 10
    assert received[-1][1] == (20,)
    assert len(query.unlimited().all()) == 10
    assert "LIMIT" not in received[-1][0]
    assert query.first().id == 0
    assert received[-1][1] == (1,)

    monkeypatch.setattr(User, "max_rows", None)
    query.all()
    assert received[-1] == (query.to_sql().sql, ())


def test_max_rows_is_reserved(models):
    with pytest.raises(ModelDefinitionError, match="max_rows"):
        class BadModel(Base):
            __tablename__ = "bad_max_rows"
            id = Column(Integer, primary_key=True)
            max_rows = Column(Integer)


def test_repr_omits_parameter_values(models):
    User, _ = models
    query = User.select().where(User.name == "secret")
    assert "secret" not in repr(query)
    assert repr(query).startswith("Query(")


def test_query_import_does_not_load_database_drivers():
    code = (
        "import sys; from pyzzle import Base, Column, Integer, String; "
        "class_source = 'class User(Base):\\n __tablename__ = \\\"fresh_users\\\"\\n id = Column(Integer, primary_key=True)\\n name = Column(String)'; "
        "exec(class_source); User.select(User.name).where(User.name == 'A').to_sql(); "
        "assert 'psycopg' not in sys.modules; assert 'psycopg_pool' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
