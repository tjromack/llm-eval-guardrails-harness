"""Phase 1: the suite format loads, validates, and rejects malformed cases."""

import pytest

from app.suite import (
    CATEGORIES,
    SuiteError,
    load_suite,
    parse_suite,
)

STARTER = "data/suites/rag_copilot.suite.json"


def test_starter_suite_loads_and_is_well_formed():
    suite = load_suite(STARTER)
    assert suite.suite_id == "rag_copilot_v1"
    assert suite.target == "rag_copilot"
    assert suite.rubric_version  # judge rubric version is recorded on the suite
    assert len(suite.cases) == 9


def test_starter_suite_covers_guardrail_categories():
    suite = load_suite(STARTER)
    counts = suite.counts_by_category()
    # Guardrail/red-team categories are first-class (Decision 005).
    assert counts["out_of_scope"] >= 1
    assert counts["adversarial"] >= 1
    assert counts["pii_probe"] >= 1
    assert set(counts) == CATEGORIES


def test_check_routing_splits_judge_from_rule():
    suite = load_suite(STARTER)
    by_id = {c.id: c for c in suite.cases}
    grounded = by_id["rag-001"]
    assert {c.type for c in grounded.judge_checks} == {
        "judge_groundedness",
        "judge_correctness",
    }
    assert any(c.type == "citation_present" for c in grounded.rule_checks)
    # The PII probe is purely deterministic — no judge involvement.
    assert by_id["rag-009"].judge_checks == ()


def _minimal(**overrides):
    base = {
        "suite_id": "s",
        "name": "n",
        "target": "t",
        "cases": [
            {
                "id": "c1",
                "category": "grounded",
                "input": "q",
                "reference": "r",
                "checks": [{"type": "citation_present"}],
            }
        ],
    }
    base.update(overrides)
    return base


def test_rejects_unknown_check_type():
    raw = _minimal(
        cases=[
            {
                "id": "c1",
                "category": "grounded",
                "input": "q",
                "reference": "r",
                "checks": [{"type": "not_a_real_check"}],
            }
        ]
    )
    with pytest.raises(SuiteError, match="unknown check type"):
        parse_suite(raw)


def test_rejects_judge_check_missing_min():
    raw = _minimal(
        cases=[
            {
                "id": "c1",
                "category": "grounded",
                "input": "q",
                "reference": "r",
                "checks": [{"type": "judge_groundedness"}],
            }
        ]
    )
    with pytest.raises(SuiteError, match="requires param 'min'"):
        parse_suite(raw)


def test_rejects_bad_category_and_duplicate_ids():
    with pytest.raises(SuiteError, match="category must be one of"):
        parse_suite(
            _minimal(
                cases=[
                    {
                        "id": "c1",
                        "category": "nope",
                        "input": "q",
                        "reference": "r",
                        "checks": [{"type": "citation_present"}],
                    }
                ]
            )
        )

    dup = _minimal()["cases"][0]
    with pytest.raises(SuiteError, match="duplicate case ids"):
        parse_suite(_minimal(cases=[dup, dict(dup)]))
