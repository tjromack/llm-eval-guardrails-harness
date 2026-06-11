"""Test-set format: cases, references, and the checks each must pass.

A *suite* is an envelope (id, target, judge rubric version, thresholds) plus a
list of *cases*. Each case has an `input`, a `reference`/expected behavior, a
`category`, and a list of typed `checks`. The check `type` is the seam between
the two scoring layers (Decision 003): types prefixed `judge_` go to the
LLM-as-judge (Phase 5); everything else is a deterministic rule check (Phase 4).

This module owns the format + loading + validation only. It does not run checks
or call targets — that keeps it small and lets the runner/checks/judge depend on
a stable, validated shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---- Vocabulary ------------------------------------------------------------

# Case categories. Guardrail/red-team categories are first-class (Decision 005).
CATEGORIES = {"grounded", "out_of_scope", "adversarial", "pii_probe"}

# Deterministic check types (Phase 4) -> the params they require.
# An empty set means the type takes no required params (optional ones allowed).
DETERMINISTIC_CHECKS: dict[str, set[str]] = {
    "must_include": {"values"},
    "must_exclude": {"values"},
    "citation_present": set(),
    "format": {"regex"},
    "abstention": set(),  # optional: expect (defaults to "abstain")
    "refusal": set(),  # optional: expect (defaults to "refuse")
    "pii_leak": set(),  # optional: patterns
}

# Judge check types (Phase 5) -> required params.
JUDGE_CHECKS: dict[str, set[str]] = {
    "judge_groundedness": {"min"},
    "judge_correctness": {"min"},
}

ALL_CHECK_TYPES = {**DETERMINISTIC_CHECKS, **JUDGE_CHECKS}


class SuiteError(ValueError):
    """Raised when a suite file is malformed. Message names the offending case."""


# ---- Model -----------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_judge(self) -> bool:
        return self.type in JUDGE_CHECKS


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    input: Any  # str (question) or dict (e.g. {"question", "context"}) for hermetic cases
    reference: str
    checks: tuple[Check, ...]

    @property
    def judge_checks(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.is_judge)

    @property
    def rule_checks(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.is_judge)


@dataclass(frozen=True)
class Suite:
    suite_id: str
    name: str
    target: str
    rubric_version: str
    thresholds: dict[str, Any]
    cases: tuple[Case, ...]

    def counts_by_category(self) -> dict[str, int]:
        out: dict[str, int] = {c: 0 for c in sorted(CATEGORIES)}
        for case in self.cases:
            out[case.category] = out.get(case.category, 0) + 1
        return out


# ---- Loading + validation --------------------------------------------------


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SuiteError(msg)


def _parse_check(raw: Any, where: str) -> Check:
    _require(isinstance(raw, dict), f"{where}: each check must be an object")
    ctype = raw.get("type")
    _require(
        ctype in ALL_CHECK_TYPES,
        f"{where}: unknown check type {ctype!r}; known: {sorted(ALL_CHECK_TYPES)}",
    )
    params = {k: v for k, v in raw.items() if k != "type"}
    for req in ALL_CHECK_TYPES[ctype]:
        _require(req in params, f"{where}: check {ctype!r} requires param {req!r}")
    # Light per-type shape checks — fail loudly now rather than mid-run.
    if ctype in ("must_include", "must_exclude"):
        vals = params.get("values")
        _require(
            isinstance(vals, list) and all(isinstance(v, str) for v in vals) and vals,
            f"{where}: {ctype!r} 'values' must be a non-empty list of strings",
        )
    if ctype in JUDGE_CHECKS:
        m = params.get("min")
        _require(
            isinstance(m, (int, float)) and 0.0 <= float(m) <= 1.0,
            f"{where}: {ctype!r} 'min' must be a number in [0, 1]",
        )
    return Check(type=ctype, params=params)


def _parse_case(raw: Any, index: int) -> Case:
    where = f"case[{index}]"
    _require(isinstance(raw, dict), f"{where}: must be an object")
    cid = raw.get("id")
    _require(isinstance(cid, str) and cid, f"{where}: missing string 'id'")
    where = f"case {cid!r}"

    category = raw.get("category")
    _require(
        category in CATEGORIES,
        f"{where}: category must be one of {sorted(CATEGORIES)}, got {category!r}",
    )
    _require("input" in raw, f"{where}: missing 'input'")
    inp = raw["input"]
    _require(
        isinstance(inp, (str, dict)),
        f"{where}: 'input' must be a string or an object",
    )
    reference = raw.get("reference", "")
    _require(isinstance(reference, str), f"{where}: 'reference' must be a string")

    raw_checks = raw.get("checks", [])
    _require(isinstance(raw_checks, list), f"{where}: 'checks' must be a list")
    _require(len(raw_checks) > 0, f"{where}: must declare at least one check")
    checks = tuple(_parse_check(c, where) for c in raw_checks)

    return Case(
        id=cid, category=category, input=inp, reference=reference, checks=checks
    )


def parse_suite(raw: dict[str, Any]) -> Suite:
    """Validate a decoded suite dict into a Suite, raising SuiteError on any issue."""
    _require(isinstance(raw, dict), "suite: top level must be an object")
    for key in ("suite_id", "name", "target"):
        _require(isinstance(raw.get(key), str) and raw[key], f"suite: missing '{key}'")

    raw_cases = raw.get("cases")
    _require(
        isinstance(raw_cases, list) and len(raw_cases) > 0,
        "suite: 'cases' must be a non-empty list",
    )
    cases = tuple(_parse_case(c, i) for i, c in enumerate(raw_cases))

    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    _require(not dupes, f"suite: duplicate case ids: {dupes}")

    return Suite(
        suite_id=raw["suite_id"],
        name=raw["name"],
        target=raw["target"],
        rubric_version=raw.get("rubric_version", ""),
        thresholds=raw.get("thresholds", {}) or {},
        cases=cases,
    )


def load_suite(path: str | Path) -> Suite:
    """Load + validate a suite JSON file."""
    p = Path(path)
    _require(p.exists(), f"suite file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SuiteError(f"{p}: invalid JSON — {e}") from e
    return parse_suite(raw)


# ---- CLI: inspect / validate a suite ("make"-free quick look) --------------


def _main(argv: list[str]) -> int:
    import sys

    path = argv[0] if argv else "data/suites/rag_copilot.suite.json"
    try:
        suite = load_suite(path)
    except SuiteError as e:
        print(f"INVALID  {path}\n  {e}")
        return 1

    print(f"OK  {suite.suite_id}  —  {suite.name}")
    print(f"    target={suite.target}  rubric={suite.rubric_version}  cases={len(suite.cases)}")
    print(f"    by category: {suite.counts_by_category()}")
    n_judge = sum(len(c.judge_checks) for c in suite.cases)
    n_rule = sum(len(c.rule_checks) for c in suite.cases)
    print(f"    checks: {n_rule} deterministic, {n_judge} judge")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
