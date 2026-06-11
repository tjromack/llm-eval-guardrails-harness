"""Aggregation, run comparison, and regression flags (Decision 006).

This is where stored check results become a verdict you can act on:
  - `build_run_report` — per-case and per-suite pass rates, broken down by
    category and by layer (rule vs judge), with the suite threshold applied.
  - `compare` — match checks across two runs by (case, check, layer) and flag
    pass→fail as **regressions** and fail→pass as **improvements**; a run is
    "flagged" if it regressed or fell below threshold versus the baseline.

Scoring (rule + judge) is applied lazily here via `ensure_scored`, so a freshly
captured run can be reported without a separate manual step. A case passes only
if *all* its checks pass; a judge error counts as a fail, never a silent pass.
"""

from __future__ import annotations

import glob
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app import store
from app.checks import score_run as score_rule_checks
from app.judge import Judge, JudgeError
from app.judge import score_run as score_judge_checks
from app.suite import Suite, load_suite

DEFAULT_SUITE = "data/suites/rag_copilot.suite.json"
SUITE_GLOB = "data/suites/*.suite.json"
# A judge score drop at least this large is a regression even if still "passing".
REGRESSION_EPS = 0.05


# ---- Suite resolution ------------------------------------------------------


def find_suite_for_run(suite_id: str) -> Suite:
    """Locate the suite a run was produced from, by id; fall back to the default."""
    for path in glob.glob(SUITE_GLOB):
        try:
            suite = load_suite(path)
        except Exception:
            continue
        if suite.suite_id == suite_id:
            return suite
    return load_suite(DEFAULT_SUITE)


# ---- Scoring (lazy) --------------------------------------------------------


def score_run_full(
    conn: sqlite3.Connection, run_id: int, suite: Suite, judge: Judge | None = None
) -> None:
    """Apply both scoring layers to a run, persisting verdicts."""
    score_rule_checks(conn, run_id, suite)
    if judge is None:
        try:
            judge = Judge()
        except JudgeError as e:
            _record_judge_unavailable(conn, run_id, suite, str(e))
            return
    score_judge_checks(conn, run_id, suite, judge=judge)


def _record_judge_unavailable(
    conn: sqlite3.Connection, run_id: int, suite: Suite, why: str
) -> None:
    """If no judge can be built, record judge checks as errors (not silent passes)."""
    store.clear_check_results(conn, run_id, layer="judge")
    for case in suite.cases:
        for check in case.judge_checks:
            store.add_check_result(
                conn,
                run_id=run_id,
                case_id=case.id,
                check_type=check.type,
                layer="judge",
                passed=False,
                score=None,
                reason=f"judge unavailable: {why}",
            )


def ensure_scored(
    conn: sqlite3.Connection, run_id: int, suite: Suite, judge: Judge | None = None
) -> None:
    if not store.get_check_results(conn, run_id):
        score_run_full(conn, run_id, suite, judge=judge)


# ---- Run report ------------------------------------------------------------


@dataclass
class CheckView:
    case_id: str
    check_type: str
    layer: str
    passed: bool
    score: float | None
    reason: str


@dataclass
class CaseReport:
    case_id: str
    category: str
    checks: list[CheckView]

    @property
    def n_checks(self) -> int:
        return len(self.checks)

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def passed(self) -> bool:
        return self.n_checks > 0 and all(c.passed for c in self.checks)


@dataclass
class Tally:
    total: int = 0
    passed: int = 0

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 1.0


@dataclass
class RunReport:
    run: store.RunRow
    cases: list[CaseReport]
    threshold: float
    by_category: dict[str, Tally] = field(default_factory=dict)
    by_layer: dict[str, Tally] = field(default_factory=dict)

    @property
    def n_cases(self) -> int:
        return len(self.cases)

    @property
    def n_cases_passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def case_pass_rate(self) -> float:
        return self.n_cases_passed / self.n_cases if self.n_cases else 1.0

    @property
    def n_checks(self) -> int:
        return sum(c.n_checks for c in self.cases)

    @property
    def n_checks_passed(self) -> int:
        return sum(c.n_passed for c in self.cases)

    @property
    def meets_threshold(self) -> bool:
        return self.case_pass_rate >= self.threshold


def build_run_report(
    conn: sqlite3.Connection, run_id: int, suite: Suite, judge: Judge | None = None
) -> RunReport | None:
    if store.get_run(conn, run_id) is None:
        return None
    ensure_scored(conn, run_id, suite, judge=judge)
    # Re-fetch so judge_model stamped during scoring is reflected in the report.
    run = store.get_run(conn, run_id)

    rows = store.get_check_results(conn, run_id)
    by_case: dict[str, list[CheckView]] = {}
    for r in rows:
        by_case.setdefault(r["case_id"], []).append(
            CheckView(
                case_id=r["case_id"],
                check_type=r["check_type"],
                layer=r["layer"],
                passed=bool(r["passed"]),
                score=r["score"],
                reason=r["reason"] or "",
            )
        )

    category_of = {c.id: c.category for c in suite.cases}
    order = [c.id for c in suite.cases]
    cases = [
        CaseReport(cid, category_of.get(cid, "?"), by_case[cid])
        for cid in order
        if cid in by_case
    ]

    by_category: dict[str, Tally] = {}
    by_layer: dict[str, Tally] = {}
    for case in cases:
        cat = by_category.setdefault(case.category, Tally())
        cat.total += 1
        if case.passed:
            cat.passed += 1
        for chk in case.checks:
            lay = by_layer.setdefault(chk.layer, Tally())
            lay.total += 1
            if chk.passed:
                lay.passed += 1

    threshold = float(suite.thresholds.get("suite_pass_rate", 0.0))
    return RunReport(run, cases, threshold, by_category, by_layer)


# ---- Comparison ------------------------------------------------------------


@dataclass
class CheckDelta:
    case_id: str
    check_type: str
    layer: str
    base_passed: bool | None
    cand_passed: bool | None
    base_score: float | None
    cand_score: float | None

    @property
    def status(self) -> str:
        if self.base_passed is None:
            return "added"
        if self.cand_passed is None:
            return "removed"
        if self.base_passed and not self.cand_passed:
            return "regressed"
        if not self.base_passed and self.cand_passed:
            return "improved"
        # Both passed (or both failed): a notable judge score drop still regresses.
        if (
            self.base_score is not None
            and self.cand_score is not None
            and self.base_score - self.cand_score >= REGRESSION_EPS
        ):
            return "regressed"
        return "unchanged"


@dataclass
class Comparison:
    base: RunReport
    cand: RunReport
    deltas: list[CheckDelta]

    @property
    def regressions(self) -> list[CheckDelta]:
        return [d for d in self.deltas if d.status == "regressed"]

    @property
    def improvements(self) -> list[CheckDelta]:
        return [d for d in self.deltas if d.status == "improved"]

    @property
    def case_pass_rate_delta(self) -> float:
        return self.cand.case_pass_rate - self.base.case_pass_rate

    @property
    def flagged(self) -> bool:
        """Regression flag: a check regressed, or candidate slipped below baseline."""
        return (
            bool(self.regressions)
            or self.case_pass_rate_delta < -REGRESSION_EPS
            or (self.base.meets_threshold and not self.cand.meets_threshold)
        )


def _key(v: CheckView) -> tuple[str, str, str]:
    return (v.case_id, v.check_type, v.layer)


def compare(
    conn: sqlite3.Connection,
    base_id: int,
    cand_id: int,
    suite: Suite,
    judge: Judge | None = None,
) -> Comparison | None:
    base = build_run_report(conn, base_id, suite, judge=judge)
    cand = build_run_report(conn, cand_id, suite, judge=judge)
    if base is None or cand is None:
        return None

    base_checks = {_key(c): c for case in base.cases for c in case.checks}
    cand_checks = {_key(c): c for case in cand.cases for c in case.checks}

    deltas: list[CheckDelta] = []
    for key in sorted(base_checks.keys() | cand_checks.keys()):
        b = base_checks.get(key)
        c = cand_checks.get(key)
        deltas.append(
            CheckDelta(
                case_id=key[0],
                check_type=key[1],
                layer=key[2],
                base_passed=None if b is None else b.passed,
                cand_passed=None if c is None else c.passed,
                base_score=None if b is None else b.score,
                cand_score=None if c is None else c.score,
            )
        )
    return Comparison(base, cand, deltas)


# ---- CLI -------------------------------------------------------------------


def _print_run(report: RunReport) -> None:
    r = report.run
    badge = "PASS" if report.meets_threshold else "FAIL"
    print(f"Run #{r.id}  target={r.target} version={r.target_version}  judge={r.judge_model}")
    print(
        f"  cases {report.n_cases_passed}/{report.n_cases} "
        f"({report.case_pass_rate:.0%})  threshold {report.threshold:.0%} -> {badge}"
    )
    print(f"  checks {report.n_checks_passed}/{report.n_checks} passed")
    print(f"  by category: " + ", ".join(
        f"{k} {t.passed}/{t.total}" for k, t in report.by_category.items()))
    print(f"  by layer:    " + ", ".join(
        f"{k} {t.passed}/{t.total}" for k, t in report.by_layer.items()))


def _print_comparison(cmp: Comparison) -> None:
    print(f"\nBASELINE  ", end="")
    _print_run(cmp.base)
    print(f"\nCANDIDATE ", end="")
    _print_run(cmp.cand)
    delta = cmp.case_pass_rate_delta
    print(f"\nCASE PASS RATE  {cmp.base.case_pass_rate:.0%} -> {cmp.cand.case_pass_rate:.0%} "
          f"({delta:+.0%})")
    if cmp.flagged:
        print(f"\n*** REGRESSION FLAGGED ***  {len(cmp.regressions)} check(s) regressed")
    else:
        print("\nNo regression flagged.")
    for d in cmp.regressions:
        bs = "-" if d.base_score is None else f"{d.base_score:.2f}"
        cs = "-" if d.cand_score is None else f"{d.cand_score:.2f}"
        print(f"  REGRESSED  {d.case_id:<8} {d.layer}/{d.check_type:<18} "
              f"pass {d.base_passed}->{d.cand_passed}  score {bs}->{cs}")
    for d in cmp.improvements:
        print(f"  improved   {d.case_id:<8} {d.layer}/{d.check_type}")


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run reports and run-to-run comparison.")
    parser.add_argument("--run", type=int, default=None, help="report a single run (default: latest)")
    parser.add_argument("--compare", nargs=2, type=int, metavar=("BASE", "CAND"),
                        help="compare two runs and flag regressions")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    conn = store.connect(args.db)
    store.init_db(conn)
    try:
        if args.compare:
            base_id, cand_id = args.compare
            suite = find_suite_for_run(store.get_run(conn, base_id).suite_id)
            cmp = compare(conn, base_id, cand_id, suite)
            if cmp is None:
                print("one or both runs not found")
                return 1
            _print_comparison(cmp)
            return 1 if cmp.flagged else 0

        run_id = args.run
        if run_id is None:
            runs = store.list_runs(conn, limit=1)
            if not runs:
                print("no runs found — run `make eval-run` first")
                return 1
            run_id = runs[0].id
        suite = find_suite_for_run(store.get_run(conn, run_id).suite_id)
        report = build_run_report(conn, run_id, suite)
        _print_run(report)
        return 0 if report.meets_threshold else 1
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
