"""Execute a suite over a target, persisting each output/trace + target version.

The runner is target-agnostic: it resolves the adapter by the suite's `target`
name (Decision 002/011), runs every case, and writes the raw result to the store.
It does no scoring — checks (Phase 4) and the judge (Phase 5) consume these stored
results in a later pass, so a run is reproducible and re-scorable without re-calling
the target. Wired to `make eval-run`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import store
from app.adapters import Adapter, get_adapter
from app.config import settings
from app.suite import Suite, load_suite

DEFAULT_SUITE = "data/suites/rag_copilot.suite.json"


@dataclass
class RunSummary:
    run_id: int
    suite_id: str
    target: str
    target_version: str
    n_cases: int
    n_errors: int
    total_latency_ms: int


def run_suite(
    suite: Suite | str | Path,
    *,
    adapter: Adapter | None = None,
    conn: sqlite3.Connection | None = None,
    db_path: str | Path | None = None,
    notes: str | None = None,
) -> RunSummary:
    """Run `suite` against its target and persist raw results. Returns a summary.

    `adapter` and `conn` can be injected (tests / custom wiring); otherwise the
    adapter is resolved from the suite's target name and the store opens the
    configured DB.
    """
    if isinstance(suite, (str, Path)):
        suite = load_suite(suite)
    if adapter is None:
        adapter = get_adapter(suite.target)

    own_conn = conn is None
    if own_conn:
        conn = store.connect(db_path)
    try:
        store.init_db(conn)
        run_id = store.create_run(
            conn,
            suite_id=suite.suite_id,
            suite_name=suite.name,
            target=suite.target,
            target_version=adapter.target_version,
            rubric_version=suite.rubric_version,
            notes=notes,
        )

        n_errors = 0
        total_latency = 0
        for case in suite.cases:
            result = adapter.run(case.input)
            if not result.ok:
                n_errors += 1
            total_latency += result.latency_ms or 0
            store.add_case_result(
                conn,
                run_id=run_id,
                case_id=case.id,
                category=case.category,
                case_input=case.input,
                reference=case.reference,
                output=result.output,
                trace=result.trace,
                latency_ms=result.latency_ms,
                error=result.error,
            )

        status = "done" if n_errors == 0 else "done_with_errors"
        store.finish_run(
            conn, run_id, status=status, n_cases=len(suite.cases), n_errors=n_errors
        )
        return RunSummary(
            run_id=run_id,
            suite_id=suite.suite_id,
            target=suite.target,
            target_version=adapter.target_version,
            n_cases=len(suite.cases),
            n_errors=n_errors,
            total_latency_ms=total_latency,
        )
    finally:
        if own_conn:
            conn.close()


# ---- CLI: `make eval-run` -------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a suite against its target.")
    parser.add_argument("--suite", default=DEFAULT_SUITE, help="path to a suite JSON")
    parser.add_argument("--db", default=None, help="SQLite path (default: configured)")
    parser.add_argument("--notes", default=None, help="free-text note stamped on the run")
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    print(f"Running suite {suite.suite_id!r} ({len(suite.cases)} cases) "
          f"against target {suite.target!r} ...")

    # Re-open the conn here so we can print per-case lines from the stored rows.
    conn = store.connect(args.db)
    try:
        summary = run_suite(suite, conn=conn, notes=args.notes)
        rows = store.get_case_results(conn, summary.run_id)
    finally:
        conn.close()

    for r in rows:
        flag = "ERR " if r["error"] else "ok  "
        snippet = (r["output"] or r["error"] or "").replace("\n", " ")[:72]
        print(f"  [{flag}] {r['case_id']:<8} {r['category']:<12} "
              f"{(str(r['latency_ms']) + 'ms'):>7}  {snippet}")

    print(
        f"\nRun #{summary.run_id} stored: target={summary.target} "
        f"version={summary.target_version}  "
        f"cases={summary.n_cases} errors={summary.n_errors} "
        f"total_latency={summary.total_latency_ms}ms"
    )
    print(f"DB: {settings.db_file if args.db is None else args.db}")
    return 1 if summary.n_errors else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
