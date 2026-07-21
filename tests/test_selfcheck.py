"""Phase 7: the harness validates itself — calibration, fixtures, regression."""

from app import store
from app.judge import Judge, MockProvider
from app.selfcheck import (
    JUDGE_AGREEMENT_THRESHOLD,
    run_calibration,
    run_fixtures,
    run_regression,
)


def _mock_judge():
    return Judge(provider=MockProvider())


def test_rule_check_fixtures_all_pass():
    """Every deterministic check must pass/fail exactly as intended (EVAL.md)."""
    fx = run_fixtures()
    assert fx.all_pass, fx.failures
    assert fx.total >= 20  # covers citation, PII, abstention, refusal, format, include/exclude


def test_judge_calibration_meets_threshold_on_gold_set():
    cal = run_calibration(_mock_judge())
    assert cal.scored == cal.total  # every gold case was scored
    assert cal.agreement >= JUDGE_AGREEMENT_THRESHOLD
    # The one expected miss is the subtle 30-vs-60-days contradiction.
    assert any("cal-012" in d for d in cal.disagreements)


def test_injected_regression_is_detected():
    reg = run_regression(_mock_judge())
    assert reg.flagged is True
    assert reg.cand_rate < reg.base_rate
    # The headline signal: citations were present at baseline and dropped.
    assert reg.citation_base == 1.0
    assert reg.citation_cand == 0.0


def test_fixtures_would_catch_a_broken_check():
    """Sanity: the fixture harness is actually discriminating, not vacuous."""
    from app.checks import CheckContext, run_check
    from app.suite import Check

    # A citation check on un-cited text must report FAIL — if it passed, the
    # fixture comparison (expect_pass=False) would catch the regression.
    r = run_check(Check("citation_present", {}), CheckContext(output="no source"))
    assert r.passed is False


def test_seed_reset_clears_runs():
    from app.seed import _main as seed_main
    from app.runner import run_suite
    from app.adapters.rag_copilot import RagCopilotAdapter
    from app.suite import load_suite
    import sys

    conn = store.connect(":memory:")
    store.init_db(conn)
    suite = load_suite("data/suites/rag_copilot.suite.json")
    adapter = RagCopilotAdapter(
        transport="command", cmd=f"{sys.executable} tools/fake_rag_copilot.py"
    )
    run_suite(suite, adapter=adapter, conn=conn)
    assert len(store.list_runs(conn)) == 1

    store.reset_db(conn)
    assert store.list_runs(conn) == []
    # check_results are cleared too.
    assert conn.execute("SELECT COUNT(*) FROM check_results").fetchone()[0] == 0


# ---- Verdict/reason coherence (2026-07-21) ----------------------------------


def test_detects_verdict_contradicting_its_own_reason():
    """The 2026-07-16 failure mode: verdict says fail, prose concludes correct.

    Calibration compares the *score* to a label and never reads the rationale, so
    this class of judge error was structurally invisible until now.
    """
    from app.selfcheck import verdict_contradicts_reason

    reason = (
        "The explanation states 84075 maps to clm_0060-2, but the map shows otherwise... "
        "Wait, checking: map lists 84075->clm_0060-2, so this is correct."
    )
    assert verdict_contradicts_reason(0.0, reason) is True
    # ...and the mirror image: a passing score with negative prose.
    assert verdict_contradicts_reason(1.0, "The output does not match the reference.") is True


def test_coherent_verdicts_are_not_flagged():
    """Guard against a detector that cries wolf — negatives are checked before
    affirmatives because 'is correct' is a substring of 'is not correct'."""
    from app.selfcheck import verdict_contradicts_reason

    assert verdict_contradicts_reason(1.0, "The claim is supported by the cited context.") is False
    assert verdict_contradicts_reason(0.0, "The answer is not supported by any source.") is False
    # Ambiguous prose must not be forced into a polarity.
    assert verdict_contradicts_reason(0.0, "Reviewed against the provided context.") is False


def test_coherence_selftest_proves_the_detector_fires():
    """A silent detector is a useless one — selfcheck must verify its own screen."""
    from app.selfcheck import run_coherence_selftest

    assert run_coherence_selftest() == []
