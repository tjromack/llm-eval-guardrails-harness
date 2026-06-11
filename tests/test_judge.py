"""Phase 5: the LLM-as-judge produces strict-JSON scores, versioned + recorded."""

import sys

import pytest

from app import store
from app.adapters.rag_copilot import RagCopilotAdapter
from app.judge import (
    CORRECTNESS,
    GROUNDEDNESS,
    RUBRIC_VERSION,
    Judge,
    JudgeError,
    JudgeProvider,
    MockProvider,
    parse_strict_json,
)
from app.judge import score_run as judge_score_run
from app.runner import run_suite
from app.suite import load_suite

STARTER = "data/suites/rag_copilot.suite.json"
FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


class StubProvider(JudgeProvider):
    """Returns a canned reply — no network. Optionally wraps JSON in prose."""

    model_id = "stub:test"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


# -- parsing -----------------------------------------------------------------


def test_parse_strict_json_plain_and_embedded():
    assert parse_strict_json('{"score": 1, "reason": "ok"}')["score"] == 1
    # Tolerates a JSON object embedded in prose.
    embedded = 'Here is my verdict:\n{"score": 0.5, "reason": "partial"}\nThanks!'
    assert parse_strict_json(embedded)["score"] == 0.5


def test_parse_strict_json_rejects_non_json():
    with pytest.raises(JudgeError):
        parse_strict_json("the score is one out of one")


# -- scoring one criterion ---------------------------------------------------


def test_judge_scores_and_applies_threshold():
    judge = Judge(provider=StubProvider('{"score": 0.9, "reason": "matches reference"}'))
    r = judge.score_one(CORRECTNESS, output="six years", reference="six years", min_score=0.8)
    assert r.score == 0.9
    assert r.passed is True
    assert r.model == "stub:test"
    assert r.rubric_version == RUBRIC_VERSION  # version recorded on the score

    # Same score, higher bar -> fails.
    r2 = judge.score_one(CORRECTNESS, output="x", reference="y", min_score=0.95)
    assert r2.passed is False


def test_judge_clamps_and_records_provider_error_without_raising():
    # Out-of-range score is clamped to [0, 1].
    j = Judge(provider=StubProvider('{"score": 1.4, "reason": "too high"}'))
    assert j.score_one(GROUNDEDNESS, output="o", context="c").score == 1.0

    # A non-JSON reply becomes a recorded error, not an exception.
    bad = Judge(provider=StubProvider("not json at all"))
    r = bad.score_one(GROUNDEDNESS, output="o", context="c", min_score=0.5)
    assert r.error is not None
    assert r.score is None
    assert r.passed is False


def test_mock_provider_is_responsive_and_labeled():
    judge = Judge(provider=MockProvider())
    good = judge.score_one(
        CORRECTNESS,
        output="Retention is six years from creation.",
        reference="Six years from the date of creation.",
        min_score=0.5,
    )
    assert good.passed
    assert good.model == "mock:heuristic-v1"
    # An answer sharing nothing with the reference scores low.
    bad = judge.score_one(CORRECTNESS, output="completely unrelated", reference="six years retention", min_score=0.5)
    assert not bad.passed


# -- scoring a stored run alongside rule checks ------------------------------


def _mem_conn():
    conn = store.connect(":memory:")
    store.init_db(conn)
    return conn


def test_judge_scores_stored_run_and_records_meta():
    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline")
    conn = _mem_conn()
    summary = run_suite(suite, adapter=adapter, conn=conn)

    judge = Judge(provider=MockProvider())
    js = judge_score_run(conn, summary.run_id, suite, judge=judge)

    # The 5 judge checks in the starter suite were scored (and stored as layer='judge').
    assert js.n_checks == 5
    judge_rows = [r for r in store.get_check_results(conn, summary.run_id) if r["layer"] == "judge"]
    assert len(judge_rows) == 5
    assert all(r["score"] is not None for r in judge_rows)
    # Judge model id is stamped on the run for auditability.
    assert store.get_run(conn, summary.run_id).judge_model == "mock:heuristic-v1"


def test_judge_and_rule_results_coexist_for_a_run():
    """Judge scores are stored alongside rule-check results (Phase 5 gate)."""
    from app.checks import score_run as rule_score_run

    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD)
    conn = _mem_conn()
    summary = run_suite(suite, adapter=adapter, conn=conn)

    rule_score_run(conn, summary.run_id, suite)
    judge_score_run(conn, summary.run_id, suite, judge=Judge(provider=MockProvider()))

    rows = store.get_check_results(conn, summary.run_id)
    layers = {r["layer"] for r in rows}
    assert layers == {"rule", "judge"}
    # rag-001 has both a deterministic citation check and judge groundedness.
    r1 = [r for r in rows if r["case_id"] == "rag-001"]
    assert {r["layer"] for r in r1} == {"rule", "judge"}
