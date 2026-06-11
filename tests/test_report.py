"""Phase 6: aggregation, thresholds, and run-to-run regression flagging."""

import sys

from app import store
from app.adapters.rag_copilot import RagCopilotAdapter
from app.judge import Judge, MockProvider
from app.report import build_run_report, compare
from app.runner import run_suite
from app.suite import load_suite

STARTER = "data/suites/rag_copilot.suite.json"
FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


def _mem_conn():
    conn = store.connect(":memory:")
    store.init_db(conn)
    return conn


def _mock_judge():
    return Judge(provider=MockProvider())


def _run(conn, version, env_mode=None, monkeypatch=None):
    if env_mode and monkeypatch:
        monkeypatch.setenv("FAKE_RAG_MODE", env_mode)
    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version=version)
    summary = run_suite(suite, adapter=adapter, conn=conn)
    if env_mode and monkeypatch:
        monkeypatch.delenv("FAKE_RAG_MODE", raising=False)
    return summary.run_id


def test_baseline_report_meets_threshold():
    conn = _mem_conn()
    suite = load_suite(STARTER)
    run_id = _run(conn, "baseline")

    rep = build_run_report(conn, run_id, suite, judge=_mock_judge())
    assert rep.case_pass_rate == 1.0
    assert rep.meets_threshold is True
    # Both scoring layers are represented in the aggregate.
    assert set(rep.by_layer) == {"rule", "judge"}
    assert rep.by_category["pii_probe"].rate == 1.0


def test_degraded_run_fails_threshold_and_is_flagged_vs_baseline(monkeypatch):
    conn = _mem_conn()
    suite = load_suite(STARTER)
    base_id = _run(conn, "baseline")
    cand_id = _run(conn, "degraded", env_mode="degraded", monkeypatch=monkeypatch)

    cand = build_run_report(conn, cand_id, suite, judge=_mock_judge())
    assert cand.case_pass_rate < 1.0
    assert cand.meets_threshold is False  # below the suite's 85% threshold

    cmp = compare(conn, base_id, cand_id, suite, judge=_mock_judge())
    assert cmp.flagged is True
    assert cmp.case_pass_rate_delta < 0

    regressed = {(d.case_id, d.check_type) for d in cmp.regressions}
    # The quiet break — dropped citations — is caught on grounded cases.
    assert ("rag-001", "citation_present") in regressed
    # And the lost abstention on an out-of-scope case.
    assert ("rag-005", "abstention") in regressed
    # The judge groundedness score dropped too (not just rule checks).
    assert any(
        d.check_type == "judge_groundedness" and d.layer == "judge"
        for d in cmp.regressions
    )


def test_guardrails_that_still_hold_are_not_false_flagged(monkeypatch):
    """Degraded mode keeps refusal + PII scrubbing; those must NOT regress."""
    conn = _mem_conn()
    suite = load_suite(STARTER)
    base_id = _run(conn, "baseline")
    cand_id = _run(conn, "degraded", env_mode="degraded", monkeypatch=monkeypatch)

    cmp = compare(conn, base_id, cand_id, suite, judge=_mock_judge())
    regressed = {(d.case_id, d.check_type) for d in cmp.regressions}
    assert ("rag-007", "refusal") not in regressed  # adversarial refusal holds
    assert ("rag-009", "pii_leak") not in regressed  # PII still scrubbed


def test_identical_runs_are_not_flagged():
    conn = _mem_conn()
    suite = load_suite(STARTER)
    base_id = _run(conn, "baseline")
    cand_id = _run(conn, "baseline-again")

    cmp = compare(conn, base_id, cand_id, suite, judge=_mock_judge())
    assert cmp.flagged is False
    assert cmp.regressions == []
