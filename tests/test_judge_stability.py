"""2026-07-28 — the judge reports a DISTRIBUTION over N runs, not a single sample.

The judge is non-deterministic on borderline cases (2026-07-21: a run rescored 13→15→16→17 of 17), so
presenting one run as a verdict is dishonest. These pin the machinery that fixes it: `aggregate_runs` (pure,
tested by injecting scores — no model needed) turns N per-run scores into a mean/spread + a stability flag +
a near-threshold flag; `score_run(runs=N)` repeats only the judged step and surfaces an UNSTABLE split.
"""

from __future__ import annotations

import json
import sys

from app import store
from app.adapters.rag_copilot import RagCopilotAdapter
from app.judge import (
    GROUNDEDNESS,
    Judge,
    JudgeProvider,
    JudgeResult,
    aggregate_runs,
)
from app.judge import score_run as judge_score_run
from app.runner import run_suite
from app.suite import load_suite

STARTER = "data/suites/rag_copilot.suite.json"
FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


def _jr(score, error=None):
    return JudgeResult(GROUNDEDNESS, score, bool(score and score >= 0.5), "why", "m", "g1", error=error)


# --- the pure aggregation machinery (Goal 1 + Goal 2) -------------------------------------

def test_all_runs_pass_is_a_stable_clean_pass():
    d = aggregate_runs([_jr(0.9), _jr(0.85), _jr(0.95)], min_score=0.6, band=0.05)
    assert d.passed and d.stable and d.error is None
    assert d.mean == 0.9 and d.lo == 0.85 and d.hi == 0.95
    assert not d.near_threshold
    assert "pass 3/3" in d.summary


def test_all_runs_fail_is_stable_not_a_pass():
    d = aggregate_runs([_jr(0.2), _jr(0.1), _jr(0.3)], min_score=0.6, band=0.05)
    assert not d.passed and d.stable            # they agree — stably failing
    assert "fail 0/3" in d.summary


def test_a_split_verdict_is_UNSTABLE_and_not_a_pass():
    # three runs straddle the threshold — the exact borderline case
    d = aggregate_runs([_jr(0.9), _jr(0.4), _jr(0.9)], min_score=0.6, band=0.05)
    assert not d.stable and not d.passed        # instability is never a clean pass (fail-closed)
    assert d.lo == 0.4 and d.hi == 0.9
    assert "UNSTABLE" in d.summary and "2/3" in d.summary


def test_near_threshold_is_flagged_even_when_all_runs_pass():
    # every run passes, but they sit right on the line → still call it out
    d = aggregate_runs([_jr(0.62), _jr(0.63)], min_score=0.6, band=0.05)
    assert d.passed and d.stable and d.near_threshold
    assert "near-threshold" in d.summary


def test_near_threshold_not_flagged_when_comfortably_clear():
    d = aggregate_runs([_jr(0.95), _jr(0.9)], min_score=0.6, band=0.05)
    assert not d.near_threshold


def test_all_errors_is_no_verdict_not_a_fail():
    d = aggregate_runs([_jr(None, error="Timeout"), _jr(None, error="Timeout")], min_score=0.6, band=0.05)
    assert d.error is not None and not d.passed
    assert d.mean is None and d.n_errors == 2
    assert "no verdict" in d.summary


def test_errored_runs_are_excluded_from_the_spread_but_counted():
    d = aggregate_runs([_jr(0.9), _jr(None, error="Timeout"), _jr(0.8)], min_score=0.6, band=0.05)
    assert d.error is None                       # some runs produced a score → there IS a verdict
    assert abs(d.mean - 0.85) < 1e-9 and d.n_errors == 1
    assert "1 error(s)" in d.summary


# --- integration: score_run repeats the judged step and surfaces instability --------------

class SequenceProvider(JudgeProvider):
    """Returns scores from a scripted sequence (cycling) — deterministic stand-in for a flaky judge."""

    model_id = "seq:test"

    def __init__(self, scores: list[float]):
        self._scores = scores
        self._i = 0

    def complete_json(self, system: str, user: str) -> str:
        s = self._scores[self._i % len(self._scores)]
        self._i += 1
        return json.dumps({"score": s, "reason": "scripted"})


def _run_with_output():
    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline")
    conn = store.connect(":memory:")
    store.init_db(conn)
    summary = run_suite(suite, adapter=adapter, conn=conn)
    return conn, suite, summary.run_id


def test_score_run_repeats_the_judge_and_flags_unstable_checks():
    conn, suite, run_id = _run_with_output()
    # scores that straddle any threshold in (0.05, 0.95] → every judged check splits → UNSTABLE
    judge = Judge(provider=SequenceProvider([0.95, 0.05]))
    js = judge_score_run(conn, run_id, suite, judge=judge, runs=4, band=0.02)

    assert js.runs_per_check == 4
    assert js.n_unstable >= 1                     # at least one check split on pass/fail
    assert js.n_passed == 0                       # an unstable split is not a clean pass
    rows = [r for r in store.get_check_results(conn, run_id) if r["layer"] == "judge"]
    assert any("UNSTABLE" in (r["reason"] or "") for r in rows)


def test_default_single_run_matches_prior_behaviour():
    """runs=1 (the default) still yields a single stable verdict per check — no regression."""
    conn, suite, run_id = _run_with_output()
    judge = Judge(provider=SequenceProvider([0.95]))   # always-pass
    js = judge_score_run(conn, run_id, suite, judge=judge, runs=1)
    assert js.runs_per_check == 1 and js.n_unstable == 0
    assert js.n_passed == js.n_checks              # all stable passes
