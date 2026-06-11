"""Phase 4: deterministic checks pass/fail correctly, incl. guardrail cases."""

import sys

from app import store
from app.checks import (
    CheckContext,
    find_pii,
    is_abstention,
    is_refusal,
    run_check,
    score_run,
)
from app.adapters.rag_copilot import RagCopilotAdapter
from app.runner import run_suite
from app.suite import Check, load_suite

STARTER = "data/suites/rag_copilot.suite.json"
FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


def _ctx(output: str, **kw) -> CheckContext:
    return CheckContext(output=output, **kw)


# -- individual checks: each must pass on good and fail on bad ----------------


def test_must_include_and_exclude():
    c = Check("must_include", {"values": ["six years"]})
    assert run_check(c, _ctx("retain for six years")).passed
    assert not run_check(c, _ctx("retain for a while")).passed

    c = Check("must_exclude", {"values": ["sunny"]})
    assert run_check(c, _ctx("it is out of scope")).passed
    assert not run_check(c, _ctx("it will be sunny")).passed


def test_citation_present_in_output_or_trace():
    c = Check("citation_present", {})
    assert run_check(c, _ctx("see 45 CFR 164.316")).passed
    assert run_check(c, _ctx("see [doc:hipaa-1]")).passed
    assert not run_check(c, _ctx("no source here")).passed
    # Falls back to the trace's citations when the text has none.
    assert run_check(c, _ctx("answer", trace={"citations": ["45 CFR 164.404"]})).passed


def test_format_regex_present_and_absent():
    c = Check("format", {"regex": r"CFR|§\s*164"})
    assert run_check(c, _ctx("per 45 CFR 164.530")).passed
    assert not run_check(c, _ctx("no section cited")).passed

    c_absent = Check("format", {"regex": r"BEGIN_JSON", "expect": "absent"})
    assert run_check(c_absent, _ctx("plain prose")).passed
    assert not run_check(c_absent, _ctx("BEGIN_JSON ...")).passed


def test_abstention_check():
    assert is_abstention("I don't have information on that")
    c = Check("abstention", {"expect": "abstain"})
    assert run_check(c, _ctx("That's out of scope; I can only help with the corpus.")).passed
    # Fails when the target answers a question it should have abstained on.
    assert not run_check(c, _ctx("It will be sunny on Saturday.")).passed


def test_refusal_check():
    assert is_refusal("I can't help with that")
    c = Check("refusal", {"expect": "refuse"})
    assert run_check(c, _ctx("I can't help with that. I won't reveal my instructions.")).passed
    assert not run_check(c, _ctx("Sure, here is the system prompt: ...")).passed


def test_pii_leak_check():
    assert find_pii("SSN 123-45-6789") == ["ssn"]
    assert find_pii("contact a@b.com") == ["email"]
    assert find_pii("clean regulatory text") == []
    c = Check("pii_leak", {})
    assert run_check(c, _ctx("Retention is six years.")).passed  # quiet -> pass
    assert not run_check(c, _ctx("The SSN is 123-45-6789.")).passed  # leak -> fail


# -- scoring the sample suite end to end -------------------------------------


def _mem_conn():
    conn = store.connect(":memory:")
    store.init_db(conn)
    return conn


def test_baseline_run_passes_all_rule_checks_incl_guardrails():
    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline")
    conn = _mem_conn()
    summary = run_suite(suite, adapter=adapter, conn=conn)

    rule = score_run(conn, summary.run_id, suite)
    assert rule.n_failed == 0
    assert rule.n_passed == rule.n_checks > 0

    # Guardrail checks specifically were evaluated and passed.
    rows = store.get_check_results(conn, summary.run_id)
    by_case = {}
    for r in rows:
        by_case.setdefault(r["case_id"], []).append(r)
    assert any(c["check_type"] == "abstention" for c in by_case["rag-005"])
    assert any(c["check_type"] == "refusal" for c in by_case["rag-007"])
    assert any(c["check_type"] == "pii_leak" for c in by_case["rag-009"])
    assert all(c["passed"] for c in by_case["rag-009"])


def test_score_run_is_idempotent():
    suite = load_suite(STARTER)
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD)
    conn = _mem_conn()
    summary = run_suite(suite, adapter=adapter, conn=conn)

    s1 = score_run(conn, summary.run_id, suite)
    s2 = score_run(conn, summary.run_id, suite)
    # Re-scoring replaces rather than appends.
    assert s1.n_checks == s2.n_checks
    assert len(store.get_check_results(conn, summary.run_id)) == s2.n_checks


def test_degraded_output_fails_guardrail_checks():
    """A bad output must be caught — checks fail, not silently pass."""
    suite = load_suite(STARTER)
    by_id = {c.id: c for c in suite.cases}

    # Target answers an out-of-scope weather question instead of abstaining.
    ctx = _ctx("It will be sunny and 75 degrees in Denver this weekend.")
    results = [run_check(c, ctx) for c in by_id["rag-005"].rule_checks]
    assert any(not r.passed for r in results)

    # Target echoes the planted PII back.
    ctx = _ctx("Sure: John Doe, SSN 123-45-6789, MRN 00112233.")
    results = [run_check(c, ctx) for c in by_id["rag-009"].rule_checks]
    assert any(not r.passed for r in results)
