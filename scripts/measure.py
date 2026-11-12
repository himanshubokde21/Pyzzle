"""Measure Pyzzle imports, size, memory, and PostgreSQL latency; run with --url and --iterations."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gc
import importlib.metadata
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc

from pyzzle import Base, Boolean, Column, Integer, String, connect, disconnect
from pyzzle.sql import insert as build_insert
from pyzzle.sql import select_by_pk as build_select_by_pk
from pyzzle.sql import update as build_update


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "pyzzle"
IMPORTTIME_LINE = re.compile(r"import time:\s+\d+\s+\|\s+(\d+)\s+\|\s+pyzzle\s*$", re.MULTILINE)
WARMUP = 10
MEMORY_ROWS = 10_000
FIELDS = ("id", "name", "email", "age", "active", "version")


def percentile95(values):
    ordered = sorted(values)
    return ordered[int(0.95 * (len(ordered) - 1))]


def markdown_table(headers, rows):
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        print("| " + " | ".join(str(value) for value in row) + " |")


def subprocess_time(arguments, capture=False):
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, *arguments],
        check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        text=True,
    )
    return (time.perf_counter() - started) * 1_000_000, result.stdout if capture else ""


def measure_imports():
    cumulative = []
    pyzzle_wall = []
    drivers_wall = []
    for _ in range(5):
        _, output = subprocess_time(["-X", "importtime", "-c", "import pyzzle"], capture=True)
        matches = IMPORTTIME_LINE.findall(output)
        if not matches:
            raise RuntimeError("Could not parse the cumulative pyzzle importtime line")
        cumulative.append(int(matches[-1]))
        elapsed, _ = subprocess_time(["-c", "import pyzzle"])
        pyzzle_wall.append(elapsed)
        elapsed, _ = subprocess_time(["-c", "import psycopg, psycopg_pool"])
        drivers_wall.append(elapsed)
    markdown_table(
        ["Import measurement", "Median"],
        [
            ("pyzzle cumulative import time (microseconds)", f"{statistics.median(cumulative):.0f}"),
            ("python -c import pyzzle wall time (ms)", f"{statistics.median(pyzzle_wall) / 1000:.2f}"),
            ("python -c import psycopg, psycopg_pool wall time (ms)", f"{statistics.median(drivers_wall) / 1000:.2f}"),
        ],
    )


def package_file_size(path):
    return sum(
        file.stat().st_size
        for file in path.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"
    )


def installed_distribution_size(name):
    distribution = importlib.metadata.distribution(name)
    if name == "pyzzle":
        return sum(file.stat().st_size for file in PACKAGE.rglob("*.py"))
    total = 0
    for entry in distribution.files or ():
        try:
            path = distribution.locate_file(entry)
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def measure_size():
    package_files = sorted(
        file for file in PACKAGE.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"
    )
    markdown_table(
        ["Pyzzle package file", "Size (KB)"],
        [(file.relative_to(ROOT).as_posix(), f"{file.stat().st_size / 1024:.2f}") for file in package_files]
        + [("Total", f"{package_file_size(PACKAGE) / 1024:.2f}")],
    )
    distributions = []
    for name in ("pyzzle", "psycopg", "psycopg-binary", "psycopg-pool"):
        distributions.append((name, f"{installed_distribution_size(name) / 1_000_000:.3f}"))
    markdown_table(["Installed distribution", "Size (MB)"], distributions)

    source_files = sorted(PACKAGE.rglob("*.py"))
    line_counts = []
    for file in source_files:
        count = sum(bool(line.strip()) for line in file.read_text(encoding="utf-8").splitlines())
        line_counts.append((file.relative_to(ROOT).as_posix(), count))
    markdown_table(["Source file", "Non-blank lines"], line_counts + [("Total", sum(row[1] for row in line_counts))])


def memory_sample(factory):
    gc.collect()
    tracemalloc.start()
    objects = factory()
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return current / len(objects)


def measure_memory():
    class User(Base):
        __tablename__ = "pyzzle_test_memory"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        email = Column(String)
        age = Column(Integer)
        active = Column(Boolean)

    class SlotRow:
        __slots__ = FIELDS

        def __init__(self, row):
            for field, value in zip(FIELDS, row):
                setattr(self, field, value)

    def make_rows():
        for index in range(MEMORY_ROWS):
            yield (index, f"User {index}", f"user{index}@example.test", index % 100, bool(index % 2), 1)

    factories = [
        ("Plain tuples", lambda: list(make_rows())),
        ("Dicts", lambda: [dict(zip(FIELDS, row)) for row in make_rows()]),
        ("__slots__ objects", lambda: [SlotRow(row) for row in make_rows()]),
        ("Pyzzle objects", lambda: [User._from_row(row) for row in make_rows()]),
    ]

    def changed_objects():
        objects = [User._from_row(row) for row in make_rows()]
        for obj in objects:
            obj.name = f"{obj.name}!"
        return objects

    factories.append(("Pyzzle objects with one changed column", changed_objects))
    measurements = [(label, memory_sample(factory)) for label, factory in factories]
    baseline = measurements[0][1]
    markdown_table(
        ["Representation", "Bytes per object", "Ratio to tuple"],
        [(label, f"{size:.1f}", f"{size / baseline:.2f}x") for label, size in measurements],
    )
    Base._registry.pop(User.__tablename__, None)


def measure_object_build_time():
    class User(Base):
        __tablename__ = "pyzzle_test_object_bench"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        email = Column(String)
        age = Column(Integer)
        active = Column(Boolean)

    rows = [
        [index, f"User {index}", f"user{index}@example.test", index % 100, bool(index % 2), 1]
        for index in range(10_000)
    ]
    factories = [
        ("Plain tuples", lambda: [tuple(row) for row in rows]),
        ("Dicts", lambda: [dict(zip(FIELDS, row)) for row in rows]),
        ("Pyzzle objects", lambda: [User._from_row(row) for row in rows]),
    ]
    results = []
    try:
        for label, factory in factories:
            factory()
            samples = []
            for _ in range(5):
                started = time.perf_counter()
                factory()
                samples.append((time.perf_counter() - started) * 1_000_000 / len(rows))
            results.append((label, f"{statistics.median(samples):.3f}"))
        markdown_table(["Representation", "Median (microseconds/object)"], results)
    finally:
        Base._registry.pop(User.__tablename__, None)


def measure_sql_build_time():
    calls = 100_000
    builders = [
        ("sql.insert", lambda: build_insert("pyzzle_test_bench", {"name": "A", "age": 30})),
        (
            "sql.select_by_pk",
            lambda: build_select_by_pk(
                "pyzzle_test_bench", ("id", "name", "version"), "id", 1
            ),
        ),
        (
            "sql.update",
            lambda: build_update(
                "pyzzle_test_bench", {"name": "B"}, "id", 1, "version", 1
            ),
        ),
    ]
    rows = []
    for label, builder in builders:
        for _ in range(100):
            builder()
        started = time.perf_counter()
        for _ in range(calls):
            builder()
        elapsed = time.perf_counter() - started
        rows.append((label, f"{elapsed * 1_000_000 / calls:.3f}"))
    markdown_table(["SQL builder", "Time (microseconds/call, 100,000 calls)"], rows)


def measure_query_build_time():
    class User(Base):
        __tablename__ = "pyzzle_test_query_build"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        age = Column(Integer)
        active = Column(Boolean)

    query_builder = lambda: User.select(User.name, User.age).where(
        User.age > 18, User.active == True
    ).order_by(User.name).limit(10).to_sql()
    for _ in range(100):
        query_builder()
    samples = []
    try:
        for _ in range(5):
            started = time.perf_counter()
            for _ in range(20_000):
                query_builder()
            samples.append((time.perf_counter() - started) * 1_000_000 / 20_000)
        markdown_table(
            ["Query build", "Median (microseconds/call, 20,000 calls)"],
            [("select().where().order_by().limit().to_sql()", f"{statistics.median(samples):.3f}")],
        )
    finally:
        Base._registry.pop(User.__tablename__, None)


def timing_stats(action, iterations, before=None, after=None):
    for _ in range(WARMUP):
        item = before() if before else None
        result = action(item)
        if after:
            after(item, result)
    samples = []
    for _ in range(iterations):
        item = before() if before else None
        started = time.perf_counter()
        result = action(item)
        samples.append((time.perf_counter() - started) * 1000)
        if after:
            after(item, result)
    return statistics.median(samples), percentile95(samples), min(samples)


def measure_database(url, iterations):
    import psycopg

    class User(Base):
        __tablename__ = "pyzzle_test_bench"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        email = Column(String(100), unique=True)
        age = Column(Integer)
        active = Column(Boolean)

    connected = False
    try:
        connect(url)
        connected = True
        Base.execute('DROP TABLE IF EXISTS "pyzzle_test_bench"')
        Base.create_tables()
        reference = User.create(name="reference", email="reference@example.test", age=30, active=True)
        plain = psycopg.connect(url, autocommit=True)
        baseline = {
            "Base.execute SELECT 1": timing_stats(
                lambda _: plain.execute("SELECT 1").fetchall(), iterations
            ),
            "User.get by primary key": timing_stats(
                lambda _: plain.execute(
                    'SELECT "id", "name", "email", "age", "active", "version" '
                    'FROM "pyzzle_test_bench" WHERE "id" = %s',
                    (reference.id,),
                ).fetchall(),
                iterations,
            ),
        }
        plain.close()

        runs = {}
        for check in (False, True):
            disconnect()
            connect(url, check=check)
            connected = True
            counter = [0]

            def create_action(_):
                counter[0] += 1
                return User.create(name=f"create-{check}-{counter[0]}")

            def save_action(_):
                counter[0] += 1
                reference.name = f"save-{check}-{counter[0]}"
                return reference.save()

            def delete_setup():
                counter[0] += 1
                return User.create(name=f"delete-{check}-{counter[0]}")

            runs[check] = {
                "Base.execute SELECT 1": timing_stats(
                    lambda _: Base.execute("SELECT 1"), iterations
                ),
                "User.create": timing_stats(
                    create_action,
                    iterations,
                    after=lambda _item, result: result.delete(),
                ),
                "User.get by primary key": timing_stats(
                    lambda _: User.get(reference.id), iterations
                ),
                "user.save after one change": timing_stats(save_action, iterations),
                "user.delete (create excluded)": timing_stats(
                    lambda user: user.delete(), iterations, before=delete_setup
                ),
            }
        operation_names = (
            "Base.execute SELECT 1",
            "User.create",
            "User.get by primary key",
            "user.save after one change",
            "user.delete (create excluded)",
        )
        def format_stats(stats):
            if stats is None:
                return "-"
            return " / ".join(f"{value:.3f}" for value in stats)

        markdown_table(
            [
                "Database operation",
                "psycopg median / p95 / min (ms)",
                "Pyzzle check=False median / p95 / min (ms)",
                "Pyzzle check=True median / p95 / min (ms)",
            ],
            [
                (
                    name,
                    format_stats(baseline.get(name)),
                    format_stats(runs[False][name]),
                    format_stats(runs[True][name]),
                )
                for name in operation_names
            ],
        )

        print("\n### Query result latency")
        measure_query_latency(url, User, iterations)

        def create_batch_in_transaction(count):
            with Base.transaction():
                return [User.create(name=f"batch-tx-{index}") for index in range(count)]

        warm_tx = create_batch_in_transaction(WARMUP)
        for row in warm_tx:
            row.delete()
        warm_short = [User.create(name=f"batch-short-{index}") for index in range(WARMUP)]
        for row in warm_short:
            row.delete()

        batch_size = 1000
        started = time.perf_counter()
        create_batch_in_transaction(batch_size)
        transaction_seconds = time.perf_counter() - started
        started = time.perf_counter()
        for index in range(batch_size):
            User.create(name=f"batch-short-{index}")
        separate_seconds = time.perf_counter() - started
        markdown_table(
            ["Create throughput", "Rows", "Total (s)", "Rows/s"],
            [
                ("One transaction", batch_size, f"{transaction_seconds:.3f}", f"{batch_size / transaction_seconds:.1f}"),
                ("Separate short sessions", batch_size, f"{separate_seconds:.3f}", f"{batch_size / separate_seconds:.1f}"),
            ],
        )
        print("\n### Concurrent User.get")
        measure_concurrency(url, User, reference.id)
        connect(url)
        connected = True
    finally:
        if connected:
            try:
                Base.execute('DROP TABLE IF EXISTS "pyzzle_test_bench"')
            finally:
                disconnect()


def measure_connection_setup(url):
    import psycopg

    plain_samples = []
    for _ in range(10):
        started = time.perf_counter()
        conn = psycopg.connect(url)
        conn.close()
        plain_samples.append((time.perf_counter() - started) * 1000)

    connect(url)
    disconnect()
    pool_samples = []
    for _ in range(5):
        started = time.perf_counter()
        connect(url)
        disconnect()
        pool_samples.append((time.perf_counter() - started) * 1000)
    markdown_table(
        ["Connection setup", "Median (ms)"],
        [
            ("psycopg.connect + close (10 samples)", f"{statistics.median(plain_samples):.3f}"),
            ("Pyzzle connect + disconnect (5 samples)", f"{statistics.median(pool_samples):.3f}"),
        ],
    )


def measure_query_latency(url, model, iterations):
    import psycopg

    disconnect()
    connect(url, check=False)
    plain = psycopg.connect(url, autocommit=True)
    operations = [
        (
            "User.get by condition",
            lambda: model.get(model.email == "reference@example.test"),
        ),
        (
            "Pyzzle select 100 rows",
            lambda: model.select(model.name, model.age).limit(100).all(),
        ),
        (
            "psycopg fetchall (same selected values)",
            lambda: plain.execute(
                'SELECT "name", "age" FROM "pyzzle_test_bench" LIMIT %s',
                (100,),
            ).fetchall(),
        ),
    ]
    rows = []
    try:
        for label, operation in operations:
            median, p95, minimum = timing_stats(lambda _: operation(), iterations)
            rows.append((label, f"{median:.3f}", f"{p95:.3f}", f"{minimum:.3f}"))
        markdown_table(["Query operation", "Median (ms)", "p95 (ms)", "Min (ms)"], rows)
    finally:
        plain.close()
        disconnect()
        connect(url)


def measure_concurrency(url, user_model, user_id):
    results = []
    for thread_count, min_size in ((1, 1), (4, 1), (16, 1), (16, 10)):
        disconnect()
        connect(url, min_size=min_size, max_size=10)
        try:
            for _ in range(10):
                user_model.get(user_id)
            barrier = threading.Barrier(thread_count)

            def worker():
                latencies = []
                barrier.wait()
                for _ in range(200):
                    started = time.perf_counter()
                    user_model.get(user_id)
                    latencies.append((time.perf_counter() - started) * 1000)
                return latencies

            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=thread_count) as executor:
                futures = [executor.submit(worker) for _ in range(thread_count)]
                latencies = [latency for future in futures for latency in future.result()]
            elapsed = time.perf_counter() - started
            count = len(latencies)
            results.append(
                (
                    f"{thread_count} thread(s), min_size={min_size}",
                    f"{elapsed:.3f}",
                    f"{count / elapsed:.1f}",
                    f"{percentile95(latencies):.3f}",
                )
            )
        finally:
            disconnect()
    markdown_table(
        ["Concurrency", "Total wall time (s)", "Operations/s", "p95 latency (ms)"],
        results,
    )


def measure_bulk_insert(url, bulk_rows):
    import psycopg

    table = "pyzzle_test_bench_bulk"

    class BulkRow(Base):
        __tablename__ = table
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        age = Column(Integer)
        active = Column(Boolean)

    conn = psycopg.connect(url)
    plain = None
    connected = False
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute(
            f'CREATE TABLE "{table}" '
            '("id" INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "name" TEXT NOT NULL, '
            '"age" INTEGER, "active" BOOLEAN, "version" INTEGER NOT NULL DEFAULT 1)'
        )
        conn.commit()
        connect(url)
        connected = True
        plain = psycopg.connect(url, autocommit=True)
        rows = [(f"user-{index}", index % 100) for index in range(bulk_rows)]
        model_rows = [
            {"name": name, "age": age, "active": True} for name, age in rows
        ]

        def run_one_by_one():
            with conn.transaction():
                for row in rows:
                    conn.execute(f'INSERT INTO "{table}" ("name", "age") VALUES (%s, %s)', row)

        def run_executemany():
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.executemany(
                        f'INSERT INTO "{table}" ("name", "age") VALUES (%s, %s)', rows
                    )

        def run_copy():
            with conn.transaction():
                with conn.cursor() as cursor:
                    with cursor.copy(f'COPY "{table}" ("name", "age") FROM STDIN') as copy:
                        for row in rows:
                            copy.write_row(row)

        measurements = []

        def sample(label, operation, count):
            samples = []
            for index in range(6):  # one warm-up and five measured runs
                conn.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY')
                conn.commit()
                started = time.perf_counter()
                operation()
                elapsed = time.perf_counter() - started
                conn.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY')
                conn.commit()
                if index:
                    samples.append(elapsed)
            median = statistics.median(samples)
            measurements.append(
                (label, f"{median:.3f}", f"{percentile95(samples):.3f}",
                 f"{min(samples):.3f}", f"{count / median:.1f}")
            )

        for label, operation in (
            ("cur.execute once per row", run_one_by_one),
            ("cur.executemany", run_executemany),
            ("cur.copy", run_copy),
        ):
            sample(label, operation, bulk_rows)

        def run_create_many():
            BulkRow.create_many(model_rows)

        def run_create_in_transaction():
            with Base.transaction():
                for row in model_rows:
                    BulkRow.create(**row)

        sample("Pyzzle create_many", run_create_many, bulk_rows)
        sample("Pyzzle create() in one transaction", run_create_in_transaction, bulk_rows)
        markdown_table(
            ["Bulk method", "Median (s)", "p95 (s)", "Min (s)", "Rows/s"],
            measurements,
        )

        conditional_rows = [
            {"name": f"conditional-{index}", "age": index, "active": True}
            for index in range(1000)
        ]

        def conditional_samples(label, operation):
            samples = []
            for index in range(6):
                conn.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY')
                conn.commit()
                BulkRow.create_many(conditional_rows)
                started = time.perf_counter()
                operation()
                elapsed = time.perf_counter() - started
                conn.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY')
                conn.commit()
                if index:
                    samples.append(elapsed)
            return label, f"{statistics.median(samples) * 1000:.3f}", \
                f"{percentile95(samples) * 1000:.3f}", f"{min(samples) * 1000:.3f}"

        update_sql = (
            f'UPDATE "{table}" SET "active" = %s, "version" = "version" + 1 '
            'WHERE "age" < %s'
        )
        delete_sql = f'DELETE FROM "{table}" WHERE "age" < %s'
        conditional_measurements = [
            conditional_samples(
                "Pyzzle update_where 100 / 1000",
                lambda: BulkRow.update_where(BulkRow.age < 100, active=False),
            ),
            conditional_samples(
                "psycopg UPDATE 100 / 1000",
                lambda: plain.execute(update_sql, (False, 100)),
            ),
            conditional_samples(
                "Pyzzle delete_where 100 / 1000",
                lambda: BulkRow.delete_where(BulkRow.age < 100),
            ),
            conditional_samples(
                "psycopg DELETE 100 / 1000",
                lambda: plain.execute(delete_sql, (100,)),
            ),
        ]
        markdown_table(
            ["Conditional write", "Median (ms)", "p95 (ms)", "Min (ms)"],
            conditional_measurements,
        )
    finally:
        try:
            if plain is not None:
                plain.close()
        finally:
            try:
                if connected:
                    disconnect()
            finally:
                try:
                    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                    conn.commit()
                finally:
                    conn.close()
                    Base._registry.pop(table, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("PYZZLE_TEST_DATABASE_URL"))
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--bulk-rows", type=int, default=1000)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.bulk_rows < 1:
        parser.error("--bulk-rows must be at least 1")

    print("## Import time")
    measure_imports()
    print("\n## Installed size and source lines")
    measure_size()
    print("\n## Memory per object")
    measure_memory()
    print("\n## Object build time")
    measure_object_build_time()
    print("\n## SQL build time")
    measure_sql_build_time()
    print("\n## Query build time")
    measure_query_build_time()
    if not args.url:
        print("\ndatabase measurements skipped (no URL)")
    else:
        print("\n## Connection setup cost")
        measure_connection_setup(args.url)
        print("\n## Database latency and throughput")
        measure_database(args.url, args.iterations)
        print("\n## Bulk insert baseline")
        measure_bulk_insert(args.url, args.bulk_rows)


if __name__ == "__main__":
    main()
