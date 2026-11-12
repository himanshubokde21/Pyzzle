import os

import pytest

from pyzzle import Base, connect, disconnect


@pytest.fixture
def database():
    original_registry = Base._registry.copy()
    Base._registry.clear()
    created_tables = []
    connected = False
    try:
        connect(os.environ.get("PYZZLE_TEST_DATABASE_URL"))
        connected = True
        yield created_tables
    finally:
        try:
            if connected:
                for table_name in reversed(created_tables):
                    Base.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
        finally:
            try:
                disconnect()
            finally:
                Base._registry.clear()
                Base._registry.update(original_registry)


@pytest.fixture
def prepare_test_table(database):
    def prepare(table_name):
        assert table_name.startswith("pyzzle_test_")
        Base.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
        database.append(table_name)

    return prepare
