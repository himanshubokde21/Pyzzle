import subprocess
import sys


def test_package_import_and_version():
    import pyzzle

    assert pyzzle.__version__ == "0.0.1"


def test_package_modules_are_importable():
    import pyzzle.core
    import pyzzle.pool
    import pyzzle.sql

    assert pyzzle.core
    assert pyzzle.sql
    assert pyzzle.pool


def test_import_does_not_load_psycopg_drivers():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pyzzle; assert 'psycopg' not in sys.modules; "
            "assert 'psycopg_pool' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
