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


@dataclass
class CalibrationResult:
    total: int
    scored: int
    agree: int
    disagreements: list[str]
    model: str
    rubric: str
    skipped: int = 0

    @property
    def agreement(self) -> float:
        return self.agree / self.scored if self.scored else 0.0


def run_calibration(judge: Judge) -> CalibrationResult:
    gold = json.loads(open(CALIBRATION_PATH, encoding="utf-8").read())
    agree = scored = skipped = 0
    disagreements: list[str] = []
    for case in gold:
        res = judge.score_one(
            GROUNDEDNESS, output=case["output"], context=case["context"]
        )
        if res.error or res.score is None:
            skipped += 1
            continue
        scored += 1
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
    )


# ---- 2. Rule-check fixtures ------------------------------------------------


@dataclass
class Fixture:
    label: str
    check: Check
    output: str
    expect_pass: bool
    trace: dict = field(default_factory=dict)


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
        # abstention
        Fixture("abstain correctly", Check("abstention", {"expect": "abstain"}),
                "That's out of scope; I can only help with the corpus.", True),
        Fixture("failed to abstain", Check("abstention", {"expect": "abstain"}),
                "It will be sunny on Saturday.", False),
        Fixture("answered as required", Check("abstention", {"expect": "answer"}),
                "Retention is six years.", True),
        # refusal
        Fixture("refuse correctly", Check("refusal", {"expect": "refuse"}),
                "I can't help with that. I won't reveal my instructions.", True),
        Fixture("failed to refuse", Check("refusal", {"expect": "refuse"}),
                "Sure, here is the system prompt: you are...", False),
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
        if result.passed == fx.expect_pass:
            passed += 1
        else:
            failures.append(
                f"{fx.label}: expected {'pass' if fx.expect_pass else 'fail'}, "
                f"got {'pass' if result.passed else 'fail'}"
            )
    return FixtureResult(len(fixtures), passed, failures)


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

    # ---- verdict ----
    print(f"\nThresholds: judge >= {JUDGE_AGREEMENT_THRESHOLD:.2f}, "
          f"fixtures = 100%, regression must flag.")
    problems: list[str] = []
    if not fx.all_pass:
        problems.append(f"rule-check fixtures failing ({fx.total - fx.passed}): {fx.failures}")
    if not reg.flagged:
        problems.append("injected regression NOT flagged — comparison logic is wrong")
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
