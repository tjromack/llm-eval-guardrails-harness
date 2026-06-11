"""Phase 3: the runner executes a suite over the target and persists raw results."""

import json
import sys

from app import store
from app.adapters.base import Adapter, TargetResult, register
from app.adapters.rag_copilot import RagCopilotAdapter
from app.runner import run_suite
from app.suite import load_suite

STARTER = "data/suites/rag_copilot.suite.json"
FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


def _mem_conn():
    conn = store.connect(":memory:")
    store.init_db(conn)
    return conn


def test_full_suite_runs_and_persists_raw_results():
    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline")
    conn = _mem_conn()

    summary = run_suite(suite, adapter=adapter, conn=conn)

    assert summary.n_cases == len(suite.cases) == 9
    assert summary.n_errors == 0

    run = store.get_run(conn, summary.run_id)
    assert run.status == "done"
    assert run.target == "rag_copilot"
    assert run.target_version == "baseline"
    assert run.rubric_version == "g1"  # judge rubric version is stamped on the run
    assert run.finished_at is not None

    rows = store.get_case_results(conn, summary.run_id)
    assert len(rows) == 9
    # Every stored result keeps the input + a trace, for re-scoring without re-calling.
    for r in rows:
        assert r["case_id"]
        assert r["input_json"]
        assert json.loads(r["trace_json"]) is not None

    by_id = {r["case_id"]: r for r in rows}
    assert "six years" in by_id["rag-001"]["output"].lower()
    # The PII probe's raw output must not contain the planted identifiers.
    assert "123-45-6789" not in by_id["rag-009"]["output"]


def test_target_version_is_stamped_per_run():
    suite = load_suite(STARTER)
    conn = _mem_conn()
    a1 = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline")
    a2 = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="prompt-v2")

    s1 = run_suite(suite, adapter=a1, conn=conn)
    s2 = run_suite(suite, adapter=a2, conn=conn)

    assert store.get_run(conn, s1.run_id).target_version == "baseline"
    assert store.get_run(conn, s2.run_id).target_version == "prompt-v2"
    # Two independent runs are retained for later comparison (Decision 006).
    assert {r.id for r in store.list_runs(conn)} >= {s1.run_id, s2.run_id}


def test_target_failure_is_recorded_not_raised():
    """A failing target degrades to recorded errors, never aborts the run."""

    @register("always_fails")
    class _Failing(Adapter):
        @property
        def target_version(self):
            return "broken"

        def run(self, case_input):
            return TargetResult(output="", target_version="broken", error="boom")

    suite = load_suite(STARTER)
    conn = _mem_conn()
    summary = run_suite(suite, adapter=_Failing(), conn=conn)

    assert summary.n_errors == summary.n_cases
    run = store.get_run(conn, summary.run_id)
    assert run.status == "done_with_errors"
    rows = store.get_case_results(conn, summary.run_id)
    assert all(r["error"] == "boom" for r in rows)
