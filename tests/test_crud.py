import os

import pytest

from pyzzle import Base, Column, Integer, String
from pyzzle.errors import NotFound, PyzzleError, VersionConflict


DATABASE_URL = os.environ.get("PYZZLE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set PYZZLE_TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.",
)


@pytest.fixture
def user_model(database, prepare_test_table):
    table_name = "pyzzle_test_users"
    prepare_test_table(table_name)

    class User(Base):
        __tablename__ = table_name
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        email = Column(String(100), unique=True)

    Base.create_tables()
    return User


def test_create_persists_row_and_generated_fields(user_model):
    User = user_model
    user = User.create(name="Amit")
    assert isinstance(user.id, int)
    assert user.version == 1
    assert user.email is None
    assert Base.execute(
        'SELECT "id", "name", "email", "version" FROM "pyzzle_test_users" WHERE "id" = %s',
        (user.id,),
    ) == [(user.id, "Amit", None, 1)]


def test_create_rejects_primary_key_and_unknown_columns(user_model):
    User = user_model
    with pytest.raises(TypeError, match="primary key is generated"):
        User.create(name="Amit", id=5)
    with pytest.raises(TypeError, match="Unknown"):
        User.create(name="Amit", missing="value")


def test_save_inserts_new_object(user_model):
    User = user_model
    user = User(name="x").save()
    assert isinstance(user.id, int)
    assert user.version == 1
    assert Base.execute(
        'SELECT "name", "version" FROM "pyzzle_test_users" WHERE "id" = %s',
        (user.id,),
    ) == [("x", 1)]


def test_get_existing_and_missing_rows(user_model):
    User = user_model
    saved = User.create(name="Amit", email="amit@example.test")
    loaded = User.get(saved.id)
    assert (loaded.id, loaded.name, loaded.email, loaded.version) == (
        saved.id,
        "Amit",
        "amit@example.test",
        1,
    )
    assert "_changed" not in loaded.__dict__
    assert User.get(-123456) is None


def test_save_without_changes_does_not_borrow_connection(user_model, monkeypatch):
    user = user_model.create(name="Amit")
    version = user.version
    calls = []
    original_run = Base._run

    def count_runs(cls, fn):
        calls.append(fn)
        return original_run(fn)

    monkeypatch.setattr(Base, "_run", classmethod(count_runs))
    assert user.save() is user
    assert calls == []
    assert user.version == version


def test_save_changes_increments_version_and_marks_clean(user_model):
    User = user_model
    user = User.create(name="Amit")
    user.name = "Rahul"
    assert user.save() is user
    assert user.version == 2
    assert user._changes() == {}
    assert Base.execute(
        'SELECT "name", "version" FROM "pyzzle_test_users" WHERE "id" = %s',
        (user.id,),
    ) == [("Rahul", 2)]

    user.name = "Ravi"
    user.save()
    assert user.version == 3


def test_partial_update_preserves_unmodified_database_values(user_model):
    User = user_model
    user = User.create(name="Amit", email="first@example.test")
    Base.execute(
        'UPDATE "pyzzle_test_users" SET "email" = %s WHERE "id" = %s',
        ("raw@example.test", user.id),
    )
    user.name = "Rahul"
    user.save()
    assert Base.execute(
        'SELECT "name", "email" FROM "pyzzle_test_users" WHERE "id" = %s',
        (user.id,),
    ) == [("Rahul", "raw@example.test")]
    assert user.email == "raw@example.test"


def test_stale_save_raises_version_conflict(user_model):
    User = user_model
    first = User.create(name="Amit")
    second = User.get(first.id)
    first.name = "A"
    first.save()
    second.name = "B"
    with pytest.raises(VersionConflict) as error_info:
        second.save()
    assert "pyzzle_test_users" in str(error_info.value)
    assert str(first.id) in str(error_info.value)
    assert Base.execute(
        'SELECT "name" FROM "pyzzle_test_users" WHERE "id" = %s', (first.id,)
    ) == [("A",)]


def test_save_of_deleted_row_raises_not_found(user_model):
    User = user_model
    user = User.create(name="Amit")
    Base.execute('DELETE FROM "pyzzle_test_users" WHERE "id" = %s', (user.id,))
    user.name = "Rahul"
    with pytest.raises(NotFound) as error_info:
        user.save()
    assert "pyzzle_test_users" in str(error_info.value)
    assert str(user.id) in str(error_info.value)


def test_delete_removes_row_and_repeated_delete_raises_not_found(user_model):
    User = user_model
    user = User.create(name="Amit")
    user.delete()
    assert Base.execute(
        'SELECT "id" FROM "pyzzle_test_users" WHERE "id" = %s', (user.id,)
    ) == []
    with pytest.raises(NotFound):
        user.delete()


def test_delete_with_stale_version_raises_conflict_and_keeps_row(user_model):
    User = user_model
    first = User.create(name="Amit")
    stale = User.get(first.id)
    first.name = "Rahul"
    first.save()
    with pytest.raises(VersionConflict):
        stale.delete()
    assert Base.execute(
        'SELECT "name" FROM "pyzzle_test_users" WHERE "id" = %s', (first.id,)
    ) == [("Rahul",)]


def test_delete_new_object_raises_pyzzle_error(user_model):
    with pytest.raises(PyzzleError, match="not been saved"):
        user_model(name="Amit").delete()


def test_saved_primary_key_cannot_be_changed(user_model):
    user = user_model.create(name="Amit")
    user.id = user.id + 1
    with pytest.raises(PyzzleError, match="primary key cannot be changed"):
        user.save()


def test_unique_violation_propagates_and_first_row_is_unchanged(user_model):
    from psycopg.errors import UniqueViolation

    User = user_model
    first = User.create(name="Amit", email="same@example.test")
    with pytest.raises(UniqueViolation):
        User.create(name="Another", email="same@example.test")
    assert Base.execute(
        'SELECT "name", "email" FROM "pyzzle_test_users" WHERE "id" = %s',
        (first.id,),
    ) == [("Amit", "same@example.test")]


def test_full_lifecycle(user_model):
    User = user_model
    user = User.create(name="Amit")
    loaded = User.get(user.id)
    assert loaded.name == "Amit"
    loaded.name = "Rahul"
    loaded.save()
    assert User.get(user.id).name == "Rahul"
    loaded.delete()
    assert User.get(user.id) is None
