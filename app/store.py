"""SQLite persistence for runs and their raw per-case results.

One small module owns the schema and all DB access so other modules don't hand-
write SQL. Phase 3 persists *raw* results (the target's output/trace/version);
later phases add tables for check results, judge scores, and comparisons via the
same `init_db` (it's idempotent — `CREATE TABLE IF NOT EXISTS`).

Runs use an integer primary key so two runs are trivially comparable by id
(Decision 006). Inputs and traces are stored as JSON text.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_id       TEXT    NOT NULL,
    suite_name     TEXT,
    target         TEXT    NOT NULL,
    target_version TEXT    NOT NULL,
    rubric_version TEXT,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    status         TEXT    NOT NULL DEFAULT 'running',
    n_cases        INTEGER NOT NULL DEFAULT 0,
    n_errors       INTEGER NOT NULL DEFAULT 0,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS case_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    case_id     TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    input_json  TEXT    NOT NULL,
    reference   TEXT,
    output      TEXT,
    trace_json  TEXT,
    latency_ms  INTEGER,
    error       TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_results_run ON case_results(run_id);

CREATE TABLE IF NOT EXISTS check_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    case_id    TEXT    NOT NULL,
    check_type TEXT    NOT NULL,
    layer      TEXT    NOT NULL DEFAULT 'rule',  -- 'rule' (Phase 4) or 'judge' (Phase 5)
    passed     INTEGER NOT NULL,                 -- 0/1
    score      REAL,                             -- judge score; rule checks mirror passed
    reason     TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_check_results_run ON check_results(run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection. Defaults to the configured DB file; ':memory:' for tests."""
    if db_path is None:
        db_path = settings.db_file
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---- writes ---------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    *,
    suite_id: str,
    suite_name: str,
    target: str,
    target_version: str,
    rubric_version: str = "",
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO runs
           (suite_id, suite_name, target, target_version, rubric_version,
            started_at, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, 'running', ?)""",
        (suite_id, suite_name, target, target_version, rubric_version, _now(), notes),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_case_result(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    case_id: str,
    category: str,
    case_input: Any,
    reference: str,
    output: str,
    trace: dict[str, Any] | None,
    latency_ms: int | None,
    error: str | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO case_results
           (run_id, case_id, category, input_json, reference, output, trace_json,
            latency_ms, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            case_id,
            category,
            json.dumps(case_input, ensure_ascii=False),
            reference,
            output,
            json.dumps(trace or {}, ensure_ascii=False),
            latency_ms,
            error,
            _now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_check_result(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    case_id: str,
    check_type: str,
    layer: str,
    passed: bool,
    score: float | None,
    reason: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO check_results
           (run_id, case_id, check_type, layer, passed, score, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, case_id, check_type, layer, int(passed), score, reason, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def clear_check_results(
    conn: sqlite3.Connection, run_id: int, *, layer: str | None = None
) -> None:
    """Remove prior check results for a run so re-scoring is idempotent."""
    if layer is None:
        conn.execute("DELETE FROM check_results WHERE run_id = ?", (run_id,))
    else:
        conn.execute(
            "DELETE FROM check_results WHERE run_id = ? AND layer = ?",
            (run_id, layer),
        )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    n_cases: int,
    n_errors: int,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, n_cases = ?, n_errors = ? WHERE id = ?",
        (_now(), status, n_cases, n_errors, run_id),
    )
    conn.commit()


# ---- reads ----------------------------------------------------------------


@dataclass
class RunRow:
    id: int
    suite_id: str
    suite_name: str
    target: str
    target_version: str
    rubric_version: str
    started_at: str
    finished_at: str | None
    status: str
    n_cases: int
    n_errors: int
    notes: str | None


def get_run(conn: sqlite3.Connection, run_id: int) -> RunRow | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return RunRow(**dict(row)) if row else None


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[RunRow]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [RunRow(**dict(r)) for r in rows]


def get_case_results(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM case_results WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()


def get_check_results(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM check_results WHERE run_id = ? ORDER BY case_id, id", (run_id,)
    ).fetchall()
