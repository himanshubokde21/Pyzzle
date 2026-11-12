"""Compare direct PostgreSQL and pool query latency; run with --url and --iterations."""

import argparse
import os
import statistics
import time

import psycopg
from psycopg_pool import ConnectionPool


WARMUP = 10


def percentile95(values):
    ordered = sorted(values)
    return ordered[int(0.95 * (len(ordered) - 1))]


def measure(action, iterations):
    for _ in range(WARMUP):
        action()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), percentile95(samples), min(samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("PYZZLE_TEST_DATABASE_URL"))
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if not args.url:
        print("database measurements skipped (no URL)")
        return

    results = []
    with psycopg.connect(args.url, autocommit=True) as direct:
        results.append(("Direct psycopg, reused", measure(lambda: direct.execute("SELECT 1").fetchone(), args.iterations)))

    variants = [
        ("Pool, connection check", True, {}),
        ("Pool, no check", False, {}),
        ("Pool, autocommit", False, {"kwargs": {"autocommit": True}}),
        ("Pool, autocommit and connection check", True, {"kwargs": {"autocommit": True}}),
    ]
    for label, use_check, extra in variants:
        options = {"min_size": 1, "max_size": 5, "open": True, **extra}
        if use_check:
            options["check"] = ConnectionPool.check_connection
        pool = ConnectionPool(args.url, **options)
        try:
            pool.wait(timeout=30)

            def query_pool():
                with pool.connection() as conn:
                    conn.execute("SELECT 1").fetchone()

            results.append((label, measure(query_pool, args.iterations)))
        finally:
            pool.close()

    print("| Variant | Median (ms) | p95 (ms) | Min (ms) |")
    print("| --- | ---: | ---: | ---: |")
    for label, values in results:
        print("| " + label + " | " + " | ".join(f"{value:.3f}" for value in values) + " |")
    fastest = min(results, key=lambda item: item[1][0])
    print(f"Fastest by median: {fastest[0]} ({fastest[1][0]:.3f} ms).")


if __name__ == "__main__":
    main()
