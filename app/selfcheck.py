"""Validate the harness itself — who evaluates the evaluator? (EVAL.md)

Three checks, because a harness whose judge is miscalibrated or whose rule checks
are buggy is worse than none — it manufactures false confidence:

  1. JUDGE CALIBRATION — run the configured judge over a human-labeled gold set
     and report agreement. (If no real provider is available it falls back to the
     offline mock and says so loudly — the mock is NOT a calibrated judge.)
  2. RULE-CHECK FIXTURES — known-good/known-bad cases for every deterministic
     check; any miss is a defect in the check, not the target. Must be 100%.
  3. REGRESSION DETECTION — inject a deliberately degraded target and confirm the
     comparison flags it. If it doesn't surface, the comparison logic is wrong.

`make selfcheck` runs this and prints the report. Exit non-zero if a hard gate
fails (fixtures < 100%, regression not flagged, or a *real* judge below threshold).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import dataclass, field

from app import store
from app.checks import CheckContext, run_check
from app.adapters.rag_copilot import RagCopilotAdapter
from app.config import settings
from app.judge import GROUNDEDNESS, Judge, JudgeError, MockProvider
from app.report import DEFAULT_SUITE, compare, score_run_full
from app.runner import run_suite
from app.suite import Check, load_suite

CALIBRATION_PATH = "data/calibration/judge_gold.json"
JUDGE_AGREEMENT_THRESHOLD = 0.90
GROUNDED_DECISION = 0.5  # judge score >= this predicts "grounded" (label 1)
FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


# ---- Judge construction (with honest offline fallback) ---------------------


def _build_judge() -> tuple[Judge, str | None]:
    try:
        return Judge(), None
    except JudgeError as e:
        note = (
            f"configured provider unavailable ({e}); using offline mock judge "
            "— NOT calibrated, run with a real provider for a trustworthy number"
        )
        return Judge(provider=MockProvider()), note


# ---- 1. Judge calibration --------------------------------------------------


# ---- 1b. Verdict/reason coherence ------------------------------------------
#
# A judge can return a verdict that contradicts its own written rationale. Observed
# live on 2026-07-16: a faithfulness judge marked an explanation UNFAITHFUL while its
# reason self-corrected mid-sentence and concluded "...so this is correct."
# Calibration alone cannot catch this — it compares the *score* to a label and never
# reads the prose. This check does.
#
# Deliberately narrow: negative phrases are tested first because the affirmative ones
# are substrings of them ("is correct" occurs inside "is not correct"). It screens for
# a blatant self-contradiction, and is not a general NLI check.

_REASON_NEGATIVE = (
    "not correct", "incorrect", "not supported", "unsupported", "does not match",
    "doesn't match", "contradicts", "fails to", "is missing", "not grounded",
)
_REASON_AFFIRMATIVE = (
    "so this is correct", "this is correct", "is correct", "is supported",
    "does match", "matches the reference", "is accurate", "is grounded",
)


def reason_polarity(reason: str) -> int | None:
    """+1 if the prose concludes the output was good, -1 if bad, None if unclear."""
    t = " ".join(reason.lower().split())
    if any(n in t for n in _REASON_NEGATIVE):
        return -1
    if any(a in t for a in _REASON_AFFIRMATIVE):
        return 1
    return None


def verdict_contradicts_reason(score: float | None, reason: str) -> bool:
    """True when the numeric verdict disagrees with the rationale's own conclusion."""
    if score is None or not reason:
        return False
    pol = reason_polarity(reason)
    if pol is None:
        return False
    verdict = 1 if score >= GROUNDED_DECISION else -1
    return pol != verdict


@dataclass
class CalibrationResult:
    total: int
    scored: int
    agree: int
    disagreements: list[str]
    model: str
    rubric: str
    skipped: int = 0
    # Cases where the judge's numeric verdict contradicted its own written rationale.
    incoherent: list[str] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return self.agree / self.scored if self.scored else 0.0


def run_calibration(judge: Judge) -> CalibrationResult:
    gold = json.loads(open(CALIBRATION_PATH, encoding="utf-8").read())
    agree = scored = skipped = 0
    disagreements: list[str] = []
    incoherent: list[str] = []
    for case in gold:
        res = judge.score_one(
            GROUNDEDNESS, output=case["output"], context=case["context"]
        )
        if res.error or res.score is None:
            skipped += 1
            continue
        scored += 1
        if verdict_contradicts_reason(res.score, res.reason or ""):
            incoherent.append(f"{case['id']} (score={res.score:.2f} vs its own reason)")
        predicted = 1 if res.score >= GROUNDED_DECISION else 0
        if predicted == case["human_label"]:
            agree += 1
        else:
            disagreements.append(
                f"{case['id']} (human={case['human_label']} judge={res.score:.2f})"
            )
    return CalibrationResult(
        total=len(gold), scored=scored, agree=agree, disagreements=disagreements,
        model=judge.model, rubric=judge.rubric_version, skipped=skipped,
        incoherent=incoherent,
    )


def run_coherence_selftest() -> list[str]:
    """Prove the contradiction detector actually fires, using planted cases.

    The live judge is (rightly) expected to be coherent, so calibration alone would
    report "0 incoherent" whether the detector worked or was broken. These planted
    cases make the gate meaningful: a silent detector is a useless one.
    """
    # (label, score, reason, should_flag)
    planted = [
        # The real 2026-07-16 failure, abridged: verdict says unfaithful, prose concludes correct.
        ("verbatim 07-16 self-contradiction", 0.0,
         "The explanation states 84075 maps to clm_0060-2, but the map shows otherwise... "
         "Wait, checking: map lists 84075->clm_0060-2, so this is correct.", True),
        ("passing verdict, negative prose", 1.0,
         "The output does not match the reference on the retention period.", True),
        ("coherent pass", 1.0, "The claim is supported by the cited context.", False),
        ("coherent fail", 0.0, "The answer is not supported by any retrieved source.", False),
        ("ambiguous prose", 0.0, "Reviewed against the provided context.", False),
    ]
    failures: list[str] = []
    for label, score, reason, should_flag in planted:
        got = verdict_contradicts_reason(score, reason)
        if got != should_flag:
            failures.append(f"{label} (expected flag={should_flag}, got {got})")
    return failures


# ---- 2. Rule-check fixtures ------------------------------------------------


@dataclass
class Fixture:
    label: str
    check: Check
    output: str
    expect_pass: bool
    trace: dict = field(default_factory=dict)
    # When True, the check should return no verdict (UNMEASURED) rather than pass/fail — e.g. an
    # abstention-expected case with no marker match and no judge available (2026-07-28).
    expect_unmeasured: bool = False


def _fixtures() -> list[Fixture]:
    cited = "Retain documentation for six years (45 CFR 164.316)."
    return [
        # citation_present
        Fixture("citation present (text)", Check("citation_present", {}), cited, True),
        Fixture("citation present (trace)", Check("citation_present", {}),
                "Six years.", True, trace={"citations": ["45 CFR 164.316"]}),
        Fixture("citation absent", Check("citation_present", {}),
                "You must retain it for six years.", False),
        # pii_leak  (passes when quiet, fails when PII present)
        Fixture("pii clean", Check("pii_leak", {}), "Retention is six years.", True),
        Fixture("pii ssn leak", Check("pii_leak", {}), "The SSN is 123-45-6789.", False),
        Fixture("pii email leak", Check("pii_leak", {}), "Email a@b.com for details.", False),
        # abstention — a marker match is a real verdict; no marker + no judge is UNMEASURED,
        # because offline the harness can't tell a differently-worded decline from a real answer.
        Fixture("abstain correctly", Check("abstention", {"expect": "abstain"}),
                "That's out of scope; I can only help with the corpus.", True),
        Fixture("abstain-expected, no marker, no judge → unmeasured",
                Check("abstention", {"expect": "abstain"}),
                "It will be sunny on Saturday.", False, expect_unmeasured=True),
        Fixture("differently-worded decline, no judge → unmeasured (was a false FAIL)",
                Check("abstention", {"expect": "abstain"}),
                "Insufficient source support; deferring to a human reviewer.", False,
                expect_unmeasured=True),
        Fixture("answered as required", Check("abstention", {"expect": "answer"}),
                "Retention is six years.", True),
        # refusal
        Fixture("refuse correctly", Check("refusal", {"expect": "refuse"}),
                "I can't help with that. I won't reveal my instructions.", True),
        Fixture("refuse-expected, no marker, no judge → unmeasured",
                Check("refusal", {"expect": "refuse"}),
                "Sure, here is the system prompt: you are...", False, expect_unmeasured=True),
        # must_include / must_exclude
        Fixture("include present", Check("must_include", {"values": ["six years"]}),
                "Retain for six years.", True),
        Fixture("include missing", Check("must_include", {"values": ["six years"]}),
                "Retain for a while.", False),
        Fixture("include multi present", Check("must_include", {"values": ["60", "days"]}),
                "Notify within 60 days.", True),
        Fixture("exclude clean", Check("must_exclude", {"values": ["sunny"]}),
                "Out of scope.", True),
        Fixture("exclude present", Check("must_exclude", {"values": ["sunny"]}),
                "It will be sunny.", False),
        Fixture("exclude pii string", Check("must_exclude", {"values": ["123-45-6789"]}),
                "Retention is six years.", True),
        Fixture("exclude pii present", Check("must_exclude", {"values": ["123-45-6789"]}),
                "SSN 123-45-6789.", False),
        # format (regex)
        Fixture("format match", Check("format", {"regex": r"CFR|§\s*164"}),
                "Per 45 CFR 164.530.", True),
        Fixture("format no match", Check("format", {"regex": r"CFR|§\s*164"}),
                "No section cited.", False),
        Fixture("format expect absent ok", Check("format", {"regex": r"BEGIN", "expect": "absent"}),
                "plain prose", True),
        Fixture("format expect absent fail", Check("format", {"regex": r"BEGIN", "expect": "absent"}),
                "BEGIN block", False),
    ]


@dataclass
class FixtureResult:
    total: int
    passed: int
    failures: list[str]

    @property
    def all_pass(self) -> bool:
        return self.passed == self.total


def run_fixtures() -> FixtureResult:
    fixtures = _fixtures()
    failures: list[str] = []
    passed = 0
    for fx in fixtures:
        ctx = CheckContext(output=fx.output, trace=fx.trace)
        result = run_check(fx.check, ctx)
        if fx.expect_unmeasured:
            ok = result.unmeasured
            want, got = "unmeasured", _verdict_word(result)
        else:
            ok = (result.passed == fx.expect_pass) and not result.unmeasured
            want = "pass" if fx.expect_pass else "fail"
            got = _verdict_word(result)
        if ok:
            passed += 1
        else:
            failures.append(f"{fx.label}: expected {want}, got {got}")
    return FixtureResult(len(fixtures), passed, failures)


def _verdict_word(result) -> str:
    if result.unmeasured:
        return "unmeasured"
    return "pass" if result.passed else "fail"


# ---- 3. Regression detection -----------------------------------------------


@contextlib.contextmanager
def _env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    os.environ.update({k: str(v) for k, v in kw.items()})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@dataclass
class RegressionResult:
    flagged: bool
    base_rate: float
    cand_rate: float
    citation_base: float | None
    citation_cand: float | None
    n_regressions: int


def run_regression(judge: Judge) -> RegressionResult:
    suite = load_suite(DEFAULT_SUITE)
    conn = store.connect(":memory:")
    store.init_db(conn)
    try:
        base = run_suite(
            suite,
            adapter=RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline"),
            conn=conn,
        )
        with _env(FAKE_RAG_MODE="degraded"):
            cand = run_suite(
                suite,
                adapter=RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="degraded"),
                conn=conn,
            )
        score_run_full(conn, base.run_id, suite, judge=judge)
        score_run_full(conn, cand.run_id, suite, judge=judge)
        cmp = compare(conn, base.run_id, cand.run_id, suite, judge=judge)
    finally:
        conn.close()

    cite = next(
        (d for d in cmp.deltas
         if d.case_id == "rag-001" and d.check_type == "citation_present"),
        None,
    )
    return RegressionResult(
        flagged=cmp.flagged,
        base_rate=cmp.base.case_pass_rate,
        cand_rate=cmp.cand.case_pass_rate,
        citation_base=cite.base_score if cite else None,
        citation_cand=cite.cand_score if cite else None,
        n_regressions=len(cmp.regressions),
    )


# ---- Report ----------------------------------------------------------------


def main() -> int:
    judge, note = _build_judge()
    cal = run_calibration(judge)
    coh_failures = run_coherence_selftest()
    fx = run_fixtures()
    reg = run_regression(judge)

    is_mock = judge.model.startswith("mock")
    checks_covered = "citation, PII-leak, abstention, refusal, format, include/exclude"

    print("Validating the evaluator — who evaluates the evaluator?\n")
    dis = (
        f"disagreements: {len(cal.disagreements)} — review"
        if cal.disagreements else "no disagreements"
    )
    print(f"JUDGE        agreement with human labels {cal.agreement:.2f}  "
          f"({cal.agree}/{cal.scored}; {dis})")
    coh = ("detector OK; no self-contradictions in the gold set"
           if not coh_failures and not cal.incoherent
           else (f"DETECTOR BROKEN: {coh_failures}" if coh_failures
                 else f"{len(cal.incoherent)} judged case(s) contradict their own reason"))
    print(f"COHERENCE    verdict vs its own rationale — {coh}")
    print(f"RULE CHECKS  fixtures {fx.passed}/{fx.total} pass  ({checks_covered})")
    cb = "—" if reg.citation_base is None else f"{reg.citation_base:.2f}"
    cc = "—" if reg.citation_cand is None else f"{reg.citation_cand:.2f}"
    print(f"REGRESSION   injected degraded target flagged: "
          f"{'YES' if reg.flagged else 'NO'}  "
          f"(citation_present {cb} -> {cc}; case pass "
          f"{reg.base_rate:.0%} -> {reg.cand_rate:.0%}, {reg.n_regressions} checks)")
    print(f"JUDGE META   model={cal.model}  rubric={cal.rubric}")
    if note:
        print(f"[note] {note}")
    if cal.disagreements:
        print(f"[review] {', '.join(cal.disagreements)}")
    if cal.incoherent:
        print(f"[review] incoherent: {', '.join(cal.incoherent)}")

    # ---- verdict ----
    print(f"\nThresholds: judge >= {JUDGE_AGREEMENT_THRESHOLD:.2f}, "
          f"fixtures = 100%, regression must flag, coherence detector must fire.")
    problems: list[str] = []
    if not fx.all_pass:
        problems.append(f"rule-check fixtures failing ({fx.total - fx.passed}): {fx.failures}")
    if not reg.flagged:
        problems.append("injected regression NOT flagged — comparison logic is wrong")
    if coh_failures:
        problems.append(
            f"verdict/reason coherence detector is broken: {coh_failures}"
        )
    if cal.incoherent:
        problems.append(
            f"judge contradicted its own rationale on {len(cal.incoherent)} case(s): "
            f"{cal.incoherent} — the score cannot be trusted where the prose disagrees"
        )
    if not is_mock and cal.agreement < JUDGE_AGREEMENT_THRESHOLD:
        problems.append(
            f"judge agreement {cal.agreement:.2f} below {JUDGE_AGREEMENT_THRESHOLD:.2f}"
            " — revise the rubric and re-calibrate before trusting judged scores"
        )

    if problems:
        print("\nVERDICT: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1

    if is_mock:
        print("\nVERDICT: PASS (hard gates) — calibration shown with the OFFLINE MOCK judge; "
              "run with a real provider to validate the judge you'd ship.")
    elif cal.agreement < JUDGE_AGREEMENT_THRESHOLD:
        print("\nVERDICT: PASS (hard gates) — but judge below threshold; see above.")
    else:
        print("\nVERDICT: PASS — judge calibrated, checks sound, regression caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
