import os

import pytest

from pyzzle import Base, Boolean, Column, Integer, String
from pyzzle.errors import NotFound, VersionConflict


DATABASE_URL = os.environ.get("PYZZLE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set PYZZLE_TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.",
)


@pytest.fixture
def write_models(database, prepare_test_table):
    prepare_test_table("pyzzle_test_people")
    prepare_test_table("pyzzle_test_blank")

    class Person(Base):
        __tablename__ = "pyzzle_test_people"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        age = Column(Integer)
        email = Column(String(100), unique=True)
        active = Column(Boolean)

    class Blank(Base):
        __tablename__ = "pyzzle_test_blank"
        id = Column(Integer, primary_key=True)
        note = Column(String)
        active = Column(Boolean)

    Base.create_tables()
    return Person, Blank


@pytest.fixture
def outside_connection():
    import psycopg

    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def test_create_many_returns_ids_in_input_order_and_defaults(write_models):
    Person, Blank = write_models
    rows = [
        {"name": f"Person {index}", "age": 20 + index, "active": True}
        for index in range(5)
    ]
    ids = Person.create_many(rows)
    assert len(ids) == 5 and all(isinstance(value, int) for value in ids)
    loaded = [Person.get(value) for value in ids]
    assert [person.name for person in loaded] == [row["name"] for row in rows]
    assert all(person.version == 1 for person in loaded)

    assert Person.create_many([]) == []
    assert Person.select().count() == 5
    null_id = Person.create_many([{"name": "Null values", "age": None, "email": None, "active": None}])[0]
    assert Person.get(null_id).age is None
    blank_ids = Blank.create_many([{}, {}, {}])
    assert len(blank_ids) == 3
    assert all(Blank.get(value).version == 1 for value in blank_ids)


def test_create_many_is_atomic_on_constraint_error(write_models):
    import psycopg

    Person, _ = write_models
    rows = [
        {"name": "First", "email": "duplicate@example.test"},
        {"name": "Second", "email": "unique@example.test"},
        {"name": "Third", "email": "duplicate@example.test"},
        {"name": "Fourth", "email": "fourth@example.test"},
        {"name": "Fifth", "email": "fifth@example.test"},
    ]
    with pytest.raises(psycopg.errors.UniqueViolation):
        Person.create_many(rows)
    assert Person.select().count() == 0


def test_create_many_transaction_visibility_and_rollback(write_models, outside_connection):
    Person, _ = write_models
    with Base.transaction():
        ids = Person.create_many([{"name": "Inside one"}, {"name": "Inside two"}])
        assert Person.select().where(Person.id.in_(ids)).count() == 2
        assert outside_connection.execute(
            'SELECT "id" FROM "pyzzle_test_people" WHERE "id" = ANY(%s)', (ids,)
        ).fetchall() == []

    assert Person.select().count() == 2
    with pytest.raises(RuntimeError):
        with Base.transaction():
            Person.create_many([{"name": "Rolled back"}])
            raise RuntimeError("rollback")
    assert Person.select().count() == 2


def test_create_many_handles_one_thousand_rows(write_models):
    Person, _ = write_models
    ids = Person.create_many(
        [{"name": f"Bulk {index}", "age": index} for index in range(1000)]
    )
    assert len(ids) == 1000
    assert len(set(ids)) == 1000


def test_update_where_changes_matching_rows_and_invalidates_stale_objects(write_models):
    Person, _ = write_models
    people = [Person.create(name=f"Person {age}", age=age) for age in (10, 20, 30)]
    stale = Person.get(people[0].id)
    assert Person.update_where(Person.age < 20, age=11, active=True) == 1
    changed = Person.get(people[0].id)
    untouched = Person.get(people[1].id)
    assert (changed.age, changed.active, changed.version) == (11, True, 2)
    assert untouched.version == 1 and untouched.age == 20
    stale.name = "stale edit"
    with pytest.raises(VersionConflict):
        stale.save()


def test_update_where_conditions_null_no_match_and_transaction_rollback(write_models):
    Person, _ = write_models
    first = Person.create(name="Amit", age=10, email="amit@example.test", active=True)
    Person.create(name="Rahul", age=20, email="rahul@example.test", active=True)
    Person.create(name="Sara", age=30, email="sara@example.test", active=False)
    assert Person.update_where(Person.age > 0, Person.age < 20, age=None) == 1
    assert Person.get(first.id).age is None
    assert Person.update_where(Person.age == 99, name="absent") == 0
    assert Person.update_where((Person.age == 20) | ~(Person.active == True), name="matched") == 2
    with pytest.raises(RuntimeError):
        with Base.transaction():
            Person.update_where(Person.name == "matched", active=False)
            raise RuntimeError("rollback")
    assert Person.select().where(Person.name == "matched").count() == 2
    assert Person.select().where(Person.name == "matched", Person.active == True).count() == 1


def test_update_where_stores_malicious_values_literally(write_models):
    Person, _ = write_models
    person = Person.create(name="before")
    malicious = "'; DROP TABLE pyzzle_test_people;--"
    assert Person.update_where(Person.id == person.id, name=malicious) == 1
    assert Person.get(person.id).name == malicious
    assert Base.execute("SELECT to_regclass(%s)", ("pyzzle_test_people",)) == [
        ("pyzzle_test_people",)
    ]


def test_delete_where_matching_no_match_stale_object_and_rollback(write_models):
    Person, _ = write_models
    people = [
        Person.create(name=name, age=age)
        for name, age in (("Amit", 10), ("Anu", 12), ("Rahul", 20))
    ]
    stale = Person.get(people[0].id)
    assert Person.delete_where(Person.id.in_([people[0].id, people[1].id]), Person.name.like("A%")) == 2
    assert Person.select().count() == 1
    with pytest.raises(NotFound):
        stale.delete()
    assert Person.delete_where(Person.age < 0) == 0

    with pytest.raises(RuntimeError):
        with Base.transaction():
            Person.delete_where(Person.id == people[2].id)
            raise RuntimeError("rollback")
    assert Person.get(people[2].id) is not None


def test_delete_where_can_match_every_row_explicitly(write_models):
    Person, _ = write_models
    Person.create_many([{"name": f"Person {index}"} for index in range(3)])
    assert Person.delete_where(Person.id.is_not_null()) == 3
    assert Person.select().count() == 0
