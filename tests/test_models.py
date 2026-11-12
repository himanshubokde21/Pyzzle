import subprocess
import sys

import pytest

from pyzzle import Base, Boolean, Column, DateTime, Integer, String
from pyzzle.errors import ModelDefinitionError
from pyzzle.sql import create_table


@pytest.fixture(autouse=True)
def restore_registry():
    original = Base._registry.copy()
    yield
    Base._registry.clear()
    Base._registry.update(original)


def test_example_model_and_ddl():
    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        email = Column(String(100), unique=True)

    assert create_table(User.__tablename__, User._column_specs()) == (
        'CREATE TABLE IF NOT EXISTS "users" '
        '("id" INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "name" TEXT NOT NULL, '
        '"email" VARCHAR(100) UNIQUE, "version" INTEGER NOT NULL DEFAULT 1)'
    )
    assert list(User._columns) == ["id", "name", "email"]
    assert list(User._all_columns) == ["id", "name", "email", "version"]
    assert User._pk_name == "id"
    assert Base._registry["users"] is User
    assert isinstance(User.name, Column)
    user = User(name="Amit")
    assert user.name == "Amit"
    assert user.id is None
    assert repr(user) == "User(id=None, name='Amit', email=None)"


def test_types_and_column_type_normalization():
    assert String().sql_type == "TEXT"
    assert String(50).sql_type == "VARCHAR(50)"
    with pytest.raises(ValueError):
        String(0)
    with pytest.raises(ValueError):
        String(True)
    assert Integer().sql_type == "INTEGER"
    assert Boolean().sql_type == "BOOLEAN"
    assert DateTime().sql_type == "TIMESTAMPTZ"
    assert isinstance(Column(Integer).type, Integer)
    assert isinstance(Column(String(50)).type, String)


def test_constructor_rejects_unknown_names_and_does_not_track():
    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    with pytest.raises(TypeError, match="Unknown"):
        User(nam="Amit")
    with pytest.raises(TypeError, match="version"):
        User(version=2)
    user = User(name="Amit")
    assert "_changed" not in user.__dict__


def test_from_row_and_lazy_change_tracking():
    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        email = Column(String)

    user = User._from_row((1, "Amit", "a@example.test", 3))
    assert user.version == 3
    assert "_changed" not in user.__dict__
    assert user.name == "Amit"
    assert "_changed" not in user.__dict__
    with pytest.raises(ValueError):
        User._from_row((1, "Amit"))

    user.name = "Rahul"
    assert user._changes() == {"name": "Rahul"}
    user.name = "Amit"
    assert user._changes() == {}
    assert "_changed" in user.__dict__

    same = User._from_row((2, "Amit", None, 1))
    same.name = "Amit"
    assert "_changed" not in same.__dict__

    user.name = "Rahul"
    user.email = "r@example.test"
    assert user._changes() == {"name": "Rahul", "email": "r@example.test"}
    user._mark_clean()
    assert user._changes() == {}
    assert "_changed" not in user.__dict__


def test_hidden_version_is_managed():
    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)

    user = User._from_row((1, 1))
    with pytest.raises(AttributeError, match="version is managed by Pyzzle"):
        user.version = 5
    with pytest.raises(ModelDefinitionError, match="reserved"):
        class BadVersion(Base):
            __tablename__ = "bad_version"
            id = Column(Integer, primary_key=True)
            version = Column(Integer)


def test_abstract_columns_are_inherited_before_concrete_columns():
    class MyBase(Base):
        __abstract__ = True
        created_at = Column(DateTime)

    class User(MyBase):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    assert list(User._all_columns) == ["created_at", "id", "name", "version"]
    assert "MyBase" not in Base._registry
    assert "users" in Base._registry


def test_model_definition_errors():
    with pytest.raises(ModelDefinitionError, match="__tablename__"):
        class MissingTable(Base):
            id = Column(Integer, primary_key=True)

    with pytest.raises(ModelDefinitionError, match="exactly one"):
        class NoPrimaryKey(Base):
            __tablename__ = "no_pk"
            name = Column(String)

    with pytest.raises(ModelDefinitionError, match="exactly one"):
        class TwoPrimaryKeys(Base):
            __tablename__ = "two_pk"
            id = Column(Integer, primary_key=True)
            other = Column(Integer, primary_key=True)

    with pytest.raises(ModelDefinitionError, match="Integer"):
        class StringPrimaryKey(Base):
            __tablename__ = "string_pk"
            id = Column(String, primary_key=True)

    with pytest.raises(ModelDefinitionError, match="reserved"):
        class ReservedName(Base):
            __tablename__ = "reserved_name"
            id = Column(Integer, primary_key=True)
            save = Column(String)

    with pytest.raises(ModelDefinitionError, match="Invalid column"):
        class InvalidName(Base):
            __tablename__ = "invalid_name"
            id = Column(Integer, primary_key=True)
            locals()["bad-name"] = Column(String)

    with pytest.raises(ModelDefinitionError, match="start with"):
        class PrivateName(Base):
            __tablename__ = "private_name"
            id = Column(Integer, primary_key=True)
            _private = Column(String)

    class FirstUser(Base):
        __tablename__ = "duplicate_table"
        id = Column(Integer, primary_key=True)

    with pytest.raises(ModelDefinitionError, match="already registered"):
        class SecondUser(Base):
            __tablename__ = "duplicate_table"
            id = Column(Integer, primary_key=True)

    with pytest.raises(ModelDefinitionError, match="Subclassing"):
        class ChildUser(FirstUser):
            __tablename__ = "child_users"
            extra = Column(String)

    with pytest.raises(ModelDefinitionError, match="Invalid table"):
        class InvalidTable(Base):
            __tablename__ = 'bad"table'
            id = Column(Integer, primary_key=True)


def test_imports_do_not_load_psycopg_drivers():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pyzzle; from pyzzle import Base, Column, Integer, String; "
            "assert Base and Column and Integer and String; "
            "assert 'psycopg' not in sys.modules; assert 'psycopg_pool' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
