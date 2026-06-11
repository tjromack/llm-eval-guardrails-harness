"""Seed / reset the harness to a clean, repeatable demo state.

Suites live as files (not in the DB), so seeding means: validate the sample
suite loads, and ensure the DB exists with the current schema. `--reset` also
clears prior runs/results so a demo starts from #1 every time (`make reset`).
"""

from __future__ import annotations

from app import store
from app.report import DEFAULT_SUITE
from app.suite import SuiteError, load_suite


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Seed or reset the harness demo state.")
    parser.add_argument("--reset", action="store_true", help="clear runs/results first")
    parser.add_argument("--suite", default=DEFAULT_SUITE)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    # Validate the sample suite up front — a broken suite should fail loudly here.
    try:
        suite = load_suite(args.suite)
    except SuiteError as e:
        print(f"SEED FAILED — sample suite invalid:\n  {e}")
        return 1

    conn = store.connect(args.db)
    try:
        if args.reset:
            store.reset_db(conn)
            print("Reset: cleared all runs, case results, and check results.")
        else:
            store.init_db(conn)
        n_runs = len(store.list_runs(conn))
    finally:
        conn.close()

    print(
        f"Seeded suite {suite.suite_id!r} ({len(suite.cases)} cases, "
        f"by category {suite.counts_by_category()})."
    )
    print(f"DB ready with {n_runs} run(s). Next: `make eval-run`.")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
