import os

import pytest

from pyzzle import Base, Boolean, Column, Integer, String, and_, or_
from pyzzle.errors import TooManyRowsError


DATABASE_URL = os.environ.get("PYZZLE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set PYZZLE_TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.",
)


@pytest.fixture
def people_model(database, prepare_test_table):
    table_name = "pyzzle_test_people"
    prepare_test_table(table_name)

    class Person(Base):
        __tablename__ = table_name
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        age = Column(Integer)
        email = Column(String(100), unique=True)
        active = Column(Boolean)

    Base.create_tables()
    people = [
        ("Amit", 16, None, True),
        ("Rahul", 20, "rahul@example.test", True),
        ("Sara", 30, "sara@example.test", False),
        ("Asha", 40, None, True),
        ("Karan", 50, "karan@example.test", False),
        ("Nina", 66, "nina@example.test", True),
        ("Amita", 25, None, False),
    ]
    people.extend(
        (f"Generated{index}", None, f"generated{index}@example.test", True)
        for index in range(18)
    )
    rows = [Person.create(name=name, age=age, email=email, active=active) for name, age, email, active in people]
    return Person, rows


@pytest.fixture
def outside_connection():
    import psycopg

    connection = psycopg.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.close()


def test_select_rows_and_selected_rows(people_model):
    Person, inserted = people_model
    rows = Person.select().order_by(Person.id).all()
    assert len(rows) == len(inserted)
    assert rows[0].name == "Amit"
    assert not hasattr(rows[0], "version")
    assert Person.select(Person.name, Person.age).order_by(Person.id).all()[0] == (
        "Amit",
        16,
    )
    row = Person.select(Person.name, Person.age).order_by(Person.id).first()
    assert row.name == "Amit" and row.age == 16
    assert row[0] == "Amit"


def test_comparison_operators_and_none(people_model):
    Person, _ = people_model
    assert [p.age for p in Person.select().where(Person.age > 30).order_by(Person.age).all()] == [40, 50, 66]
    assert [p.age for p in Person.select().where(Person.age >= 30).order_by(Person.age).all()] == [30, 40, 50, 66]
    assert [p.age for p in Person.select().where(Person.age < 20).order_by(Person.age).all()] == [16]
    assert [p.age for p in Person.select().where(Person.age <= 20).order_by(Person.age).all()] == [16, 20]
    assert Person.select().where(Person.age == 30).count() == 1
    assert Person.select().where(Person.age != 30).count() == 6
    assert Person.select().where(Person.email == None).count() == 3
    assert Person.select().where(Person.email != None).count() == 22


def test_in_like_ilike_and_null_conditions(people_model):
    Person, _ = people_model
    assert [p.age for p in Person.select().where(Person.age.in_([16, 30])).order_by(Person.age).all()] == [16, 30]
    assert Person.select().where(Person.age.in_([])).all() == []
    assert Person.select().where(Person.name.like("A%")).count() == 3
    assert Person.select().where(Person.name.ilike("a%")).count() == 3
    assert Person.select().where(Person.email.is_null()).count() == 3
    assert Person.select().where(Person.email.is_not_null()).count() == 22
    assert Person.select().where(Person.id.in_({1, 2, 3})).count() == 3
    assert Person.select().where(Person.id.in_(list(range(1, 20_001)))).count() == 25


def test_boolean_conditions_order_and_paging(people_model):
    Person, _ = people_model
    adult_active = Person.select().where((Person.age >= 18) & (Person.active == True))
    assert [p.name for p in adult_active.order_by(Person.name).all()] == ["Asha", "Nina", "Rahul"]
    seniors = Person.select().where((Person.age < 18) | (Person.age > 65))
    assert [p.name for p in seniors.order_by(Person.age).all()] == ["Amit", "Nina"]
    assert Person.select().where(~(Person.active == True)).count() == 3
    assert Person.select().where(and_(Person.age >= 30, Person.active == False)).count() == 2
    assert Person.select().where(or_(Person.age < 18, Person.age > 65)).count() == 2
    ordered = Person.select().where(Person.age != None).order_by(Person.age.desc(), Person.name.asc())
    assert [p.age for p in ordered.limit(2).all()] == [66, 50]
    first_page = Person.select(Person.name).order_by(Person.id).limit(3).all()
    second_page = Person.select(Person.name).order_by(Person.id).limit(3).offset(3).all()
    assert [row.name for row in first_page] == ["Amit", "Rahul", "Sara"]
    assert [row.name for row in second_page] == ["Asha", "Karan", "Nina"]


def test_first_count_and_queryset_reuse(people_model):
    Person, _ = people_model
    assert Person.select().count() == 25
    query = Person.select().where(Person.age >= 20).order_by(Person.age).limit(2)
    assert query.count() == 6
    assert [person.age for person in query.all()] == [20, 25]
    assert [person.age for person in query.all()] == [20, 25]
    assert Person.select().where(Person.age > 100).first() is None
    assert Person.select().order_by(Person.id).first().name == "Amit"


def test_get_condition_object_can_be_saved_and_deleted(people_model):
    Person, _ = people_model
    person = Person.get(Person.name == "Amit")
    assert person.version == 1
    person.name = "Amit Updated"
    person.save()
    assert person.version == 2
    assert Person.select().where(Person.id == person.id).first().name == "Amit Updated"
    person.delete()
    assert Person.get(Person.id == person.id) is None


def test_get_by_primary_key_or_conditions(people_model):
    Person, _ = people_model
    by_id = Person.get(1)
    assert by_id.version == 1
    assert Person.get(Person.name == "Rahul").id == 2
    assert Person.get(Person.age >= 20, Person.active == True).name == "Rahul"
    assert Person.get(Person.name == "absent") is None


def test_row_limit_guard_and_overrides(people_model, monkeypatch):
    Person, _ = people_model
    monkeypatch.setattr(Person, "max_rows", 10)
    with pytest.raises(TooManyRowsError):
        Person.select().all()
    assert len(Person.select().where(Person.id <= 10).all()) == 10
    assert len(Person.select().limit(20).all()) == 20
    assert len(Person.select().unlimited().all()) == 25
    assert Person.select().first().id == 1
    assert Person.select().count() == 25
    monkeypatch.setattr(Person, "max_rows", None)
    assert len(Person.select().all()) == 25


def test_select_reads_outer_transaction_writes(people_model, outside_connection):
    Person, _ = people_model
    with Base.transaction():
        person = Person.create(name="Uncommitted", age=22)
        assert Person.select().where(Person.id == person.id).first().name == "Uncommitted"
        assert Person.get(Person.name == "Uncommitted").id == person.id
        assert outside_connection.execute(
            'SELECT "id" FROM "pyzzle_test_people" WHERE "id" = %s', (person.id,)
        ).fetchall() == []
    assert Person.select().where(Person.id == person.id).count() == 1


def test_malicious_value_is_parameterized_and_table_remains(people_model):
    Person, _ = people_model
    malicious = "'; DROP TABLE pyzzle_test_people;--"
    assert Person.select().where(Person.name == malicious).all() == []
    assert Base.execute("SELECT to_regclass(%s)", ("pyzzle_test_people",)) == [
        ("pyzzle_test_people",)
    ]
