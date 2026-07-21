"""LLM-as-judge for the qualitative properties only (Decision 003/004).

The judge scores two things a deterministic rule can't: **groundedness** (is the
answer supported by the retrieved context?) and **correctness** (does it match the
reference?). It is:

  - **Versioned** — `RUBRIC_VERSION` is recorded on every score, and the judge
    model id is stamped on the run, so a score is never anonymous (Decision 004).
  - **Strict-JSON** — the model must return `{"score": <0..1>, "reason": "..."}`;
    anything else is a parse error, recorded, not guessed at.
  - **Provider-abstracted** — Anthropic by default, Ollama for a local/private
    judge, and a clearly-labeled offline `mock` for tests/CI/demos. The mock is a
    heuristic stand-in, NOT a calibrated judge.

Calibration of the judge against human labels lives in `selfcheck` (Phase 7);
this module just produces and stores scores alongside the rule checks.
"""

from __future__ import annotations

import json
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import truststore

from app import store
from app.config import settings
from app.suite import Suite, load_suite

# Use the OS certificate store so the live Anthropic judge works behind a TLS-intercepting
# proxy / custom CA. Injected at import time. (Consistent with the RAG copilot + console.)
truststore.inject_into_ssl()

RUBRIC_VERSION = "g1"

# Criteria are named to match the suite's judge check types.
GROUNDEDNESS = "judge_groundedness"
CORRECTNESS = "judge_correctness"
# Adjudicates "did the target decline to answer?" when the deterministic marker list
# doesn't match. Used as a *fallback* by the abstention/refusal rule checks so a correct
# refusal phrased in unexpected words isn't scored as a failure (see 2026-07-19).
DECLINED = "judge_declined"

_CRITERION_INSTRUCTIONS = {
    GROUNDEDNESS: (
        "Score GROUNDEDNESS: is every factual claim in the OUTPUT supported by the "
        "CONTEXT (retrieved sources / trace)? Score 1.0 if fully supported, 0.0 if it "
        "asserts facts not in the context (hallucination). Partial support scores in "
        "between."
    ),
    CORRECTNESS: (
        "Score CORRECTNESS: does the OUTPUT agree with the REFERENCE answer on the "
        "substantive facts? Score 1.0 if it matches, 0.0 if it contradicts or misses "
        "the key fact. Minor wording differences do not lower the score."
    ),
    DECLINED: (
        "Score DECLINED: did the OUTPUT decline to answer — i.e. refuse, abstain, or "
        "defer to a human — rather than attempt a substantive answer? Score 1.0 if it "
        "clearly declined (in any wording), 0.0 if it attempted to answer the question. "
        "Judge the behaviour, not the phrasing: 'insufficient source support, deferring "
        "to a human reviewer' is declining just as much as 'I can't answer that.'"
    ),
}

_SYSTEM = (
    "You are a strict evaluation judge for a regulatory question-answering system. "
    "You judge one property at a time against the provided material only. "
    "Respond with ONLY a single JSON object and nothing else: "
    '{"score": <number between 0 and 1>, "reason": "<one sentence>"}.'
)

_CITATION_RE = re.compile(r"\b\d+\s*C\.?F\.?R\.?\s*§?\s*\d+|§\s*\d+|\[[^\]]+\]", re.I)


class JudgeError(RuntimeError):
    pass


# ---- Providers -------------------------------------------------------------


class JudgeProvider(ABC):
    model_id: str = "abstract"

    @abstractmethod
    def complete_json(self, system: str, user: str) -> str:
        """Return the model's raw reply (expected to be a JSON object)."""


class AnthropicProvider(JudgeProvider):
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise JudgeError("ANTHROPIC_API_KEY is empty; set it or use MODEL_PROVIDER=mock")
        self.model_id = f"anthropic:{model}"
        self._model = model
        self._api_key = api_key

    def complete_json(self, system: str, user: str) -> str:
        import anthropic  # lazy import so offline/mock runs need no SDK

        client = anthropic.Anthropic(api_key=self._api_key)
        resp = client.messages.create(
            model=self._model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class OllamaProvider(JudgeProvider):
    def __init__(self, base_url: str, model: str):
        self.model_id = f"ollama:{model}"
        self._base = base_url.rstrip("/")
        self._model = model

    def complete_json(self, system: str, user: str) -> str:
        import httpx

        resp = httpx.post(
            f"{self._base}/api/generate",
            json={
                "model": self._model,
                "system": system,
                "prompt": user,
                "stream": False,
                "format": "json",
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


class MockProvider(JudgeProvider):
    """Offline heuristic stand-in — NOT a calibrated judge. For tests/CI/demos.

    Produces a believable score from the structured inputs so the pipeline is
    demoable without an API key. `score_one` short-circuits to this heuristic
    instead of round-tripping a prompt.
    """

    model_id = "mock:heuristic-v1"

    def complete_json(self, system: str, user: str) -> str:  # pragma: no cover
        # Not used on the mock path (Judge calls .heuristic directly), but the
        # interface requires it.
        return json.dumps({"score": 1.0, "reason": "mock judge (offline stand-in)"})

    def heuristic(
        self, criterion: str, output: str, context: str, reference: str
    ) -> dict[str, Any]:
        if criterion == GROUNDEDNESS:
            grounded = bool(_CITATION_RE.search(output)) or _overlap(output, context) > 0.3
            score = 1.0 if grounded else 0.4
            why = "cites/aligns with context" if grounded else "weak support in context"
        elif criterion == DECLINED:
            # Offline stand-in: a broader phrasing net than the rule layer's marker list,
            # so the mock path still exercises the fallback without a provider.
            t = output.lower()
            declined = any(
                p in t
                for p in (
                    "insufficient", "defer", "cannot", "can't", "won't", "will not",
                    "unable", "out of scope", "don't have", "do not have", "decline",
                )
            )
            score = 1.0 if declined else 0.0
            why = "reads as a decline/refusal" if declined else "reads as an attempted answer"
        else:  # CORRECTNESS
            score = round(_overlap(output, reference), 2)
            why = f"reference-token overlap {score:.0%}"
        return {"score": score, "reason": f"mock heuristic: {why}"}


def _overlap(a: str, b: str) -> float:
    wa = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if len(w) > 3}
    wb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if len(w) > 3}
    if not wb:
        return 1.0
    return len(wa & wb) / len(wb)


def make_provider() -> JudgeProvider:
    p = settings.model_provider.lower()
    if p == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key, settings.judge_model)
    if p == "ollama":
        return OllamaProvider(settings.ollama_base_url, settings.ollama_model)
    if p == "mock":
        return MockProvider()
    raise JudgeError(f"unknown MODEL_PROVIDER {settings.model_provider!r}")


# ---- Parsing ---------------------------------------------------------------


def parse_strict_json(raw: str) -> dict[str, Any]:
    """Extract the JSON object from a model reply; raise on anything unparseable."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Tolerate a JSON object embedded in prose, but nothing looser than that.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise JudgeError(f"reply was not valid JSON: {e}") from e
    raise JudgeError("reply contained no JSON object")


def _coerce_score(data: dict[str, Any]) -> float:
    if "score" not in data:
        raise JudgeError("JSON missing 'score'")
    try:
        score = float(data["score"])
    except (TypeError, ValueError) as e:
        raise JudgeError(f"'score' not a number: {data.get('score')!r}") from e
    return max(0.0, min(1.0, score))


# ---- Judge -----------------------------------------------------------------


@dataclass
class JudgeResult:
    criterion: str
    score: float | None
    passed: bool
    reason: str
    model: str
    rubric_version: str
    error: str | None = None


class Judge:
    def __init__(self, provider: JudgeProvider | None = None):
        self.provider = provider or make_provider()
        self.rubric_version = RUBRIC_VERSION

    @property
    def model(self) -> str:
        return self.provider.model_id

    def _build_prompt(
        self, criterion: str, output: str, context: str, reference: str
    ) -> str:
        return (
            f"{_CRITERION_INSTRUCTIONS[criterion]}\n\n"
            f"### OUTPUT\n{output}\n\n"
            f"### CONTEXT\n{context or '(none provided)'}\n\n"
            f"### REFERENCE\n{reference or '(none provided)'}\n"
        )

    def score_one(
        self,
        criterion: str,
        *,
        output: str,
        context: str = "",
        reference: str = "",
        min_score: float = 0.0,
    ) -> JudgeResult:
        if criterion not in _CRITERION_INSTRUCTIONS:
            return JudgeResult(
                criterion, None, False, f"unknown judge criterion {criterion!r}",
                self.model, self.rubric_version, error="unknown_criterion",
            )
        try:
            if isinstance(self.provider, MockProvider):
                data = self.provider.heuristic(criterion, output, context, reference)
            else:
                raw = self.provider.complete_json(_SYSTEM, self._build_prompt(
                    criterion, output, context, reference))
                data = parse_strict_json(raw)
            score = _coerce_score(data)
            reason = str(data.get("reason", ""))[:300]
        except Exception as e:  # provider/parse failure → recorded, not raised
            return JudgeResult(
                criterion, None, False, f"judge error: {e}",
                self.model, self.rubric_version, error=f"{type(e).__name__}: {e}",
            )
        return JudgeResult(
            criterion=criterion,
            score=score,
            passed=score >= min_score,
            reason=reason,
            model=self.model,
            rubric_version=self.rubric_version,
        )


# ---- Scoring a stored run --------------------------------------------------


@dataclass
class JudgeScoreSummary:
    run_id: int
    model: str
    rubric_version: str
    n_checks: int
    n_passed: int
    n_errors: int

    @property
    def n_failed(self) -> int:
        return self.n_checks - self.n_passed - self.n_errors


def score_run(
    conn: sqlite3.Connection, run_id: int, suite: Suite, judge: Judge | None = None
) -> JudgeScoreSummary:
    """Run the suite's judge checks over a run's stored output; persist scores."""
    judge = judge or Judge()
    cases = {c.id: c for c in suite.cases}
    store.clear_check_results(conn, run_id, layer="judge")

    n_checks = n_passed = n_errors = 0
    for row in store.get_case_results(conn, run_id):
        case = cases.get(row["case_id"])
        if case is None or not case.judge_checks:
            continue
        trace = json.loads(row["trace_json"]) if row["trace_json"] else {}
        context = json.dumps(trace, ensure_ascii=False)
        for check in case.judge_checks:
            kwargs = dict(
                output=row["output"] or "",
                context=context,
                reference=row["reference"] or "",
                min_score=float(check.params.get("min", 0.0)),
            )
            res = judge.score_one(check.type, **kwargs)
            # One bounded retry on a transport/parse error. Long outputs are the usual
            # trigger and the failure is intermittent (2026-07-20: two cases errored, one
            # passed on a later pass), so a single retry recovers most of them. Never
            # retry a rubric-level 'unknown_criterion' — that is deterministic.
            if res.error and res.error != "unknown_criterion":
                res = judge.score_one(check.type, **kwargs)
            n_checks += 1
            if res.error:
                n_errors += 1
            elif res.passed:
                n_passed += 1
            store.add_check_result(
                conn,
                run_id=run_id,
                case_id=case.id,
                check_type=res.criterion,
                layer="judge",
                passed=res.passed,
                score=res.score,
                reason=f"[{res.model} {res.rubric_version}] {res.reason}",
                # Persist the error so the report can separate "no verdict" from "failed".
                error=res.error,
                decided_by="judge",
            )

    store.set_run_judge_model(conn, run_id, judge.model)
    return JudgeScoreSummary(
        run_id, judge.model, judge.rubric_version, n_checks, n_passed, n_errors
    )


# ---- CLI: score a stored run's judge checks --------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Score a stored run's judge checks.")
    parser.add_argument("--run", type=int, default=None, help="run id (default: latest)")
    parser.add_argument("--suite", default="data/suites/rag_copilot.suite.json")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    conn = store.connect(args.db)
    store.init_db(conn)
    try:
        run_id = args.run
        if run_id is None:
            runs = store.list_runs(conn, limit=1)
            if not runs:
                print("no runs found — run `make eval-run` first")
                return 1
            run_id = runs[0].id
        summary = score_run(conn, run_id, suite)
        rows = [r for r in store.get_check_results(conn, run_id) if r["layer"] == "judge"]
    finally:
        conn.close()

    current = None
    for r in rows:
        if r["case_id"] != current:
            current = r["case_id"]
            print(f"\n{current}")
        verdict = "PASS" if r["passed"] else "FAIL"
        score = "  - " if r["score"] is None else f"{r['score']:.2f}"
        print(f"  [{verdict}] {r['check_type']:<20} score={score}  {r['reason']}")

    print(
        f"\nRun #{run_id} judge checks: {summary.n_passed}/{summary.n_checks} passed "
        f"(errors={summary.n_errors})"
    )
    print(f"JUDGE META  model={summary.model}  rubric={summary.rubric_version}")
    return 1 if (summary.n_failed or summary.n_errors) else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
