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


@dataclass
class JudgeDistribution:
    """The aggregate of N judged runs for one check — a distribution, not a single sample.

    The judge is non-deterministic on borderline cases (2026-07-21: a run rescored 13→15→16→17 of
    17), so presenting one run as a verdict is dishonest. This carries the spread and a stability
    flag so a split verdict is surfaced, not hidden.
    """

    criterion: str
    scores: list[float]          # the non-error scores, in run order
    n_runs: int                  # runs attempted (incl. errored)
    n_errors: int
    mean: float | None
    lo: float | None
    hi: float | None
    passed: bool                 # a clean pass only if EVERY scored run passed (stable-pass)
    stable: bool                 # all scored runs agree on pass/fail
    near_threshold: bool         # some run landed within the unstable band of the threshold
    reason: str
    error: str | None = None     # set only if EVERY run errored (no verdict at all)

    @property
    def summary(self) -> str:
        """A compact, human-readable distribution line folded into the persisted reason."""
        if self.error is not None:
            return f"no verdict in {self.n_runs} run(s): {self.error}"
        verdict = "pass" if self.passed else ("UNSTABLE" if not self.stable else "fail")
        n_ok = len(self.scores)
        n_pass = sum(1 for s in self.scores if s is not None and self._passed_flag(s))
        parts = [f"{verdict} {n_pass}/{n_ok} run(s)"]
        if self.mean is not None:
            parts.append(f"μ={self.mean:.2f}")
            if self.lo != self.hi:
                parts.append(f"range {self.lo:.2f}–{self.hi:.2f}")
        if self.near_threshold:
            parts.append("near-threshold — verify")
        if self.n_errors:
            parts.append(f"{self.n_errors} error(s)")
        return " · ".join(parts)

    # min_score is captured at construction time via a closure below; kept simple here.
    _min_score: float = 0.0

    def _passed_flag(self, score: float) -> bool:
        return score >= self._min_score


def aggregate_runs(
    results: list[JudgeResult], *, min_score: float, band: float
) -> JudgeDistribution:
    """Aggregate N per-run JudgeResults for one check into a distribution + stability verdict.

    Pure and deterministic — the unit-testable heart of the stability machinery (inject scores, no
    model needed). Rules:
      - scored runs drive the verdict; a run that errored is excluded from the spread but counted.
      - `passed` (a clean pass) requires EVERY scored run to meet the threshold — an unstable split
        is NOT a pass (fail-closed on instability).
      - `stable` = all scored runs agree on pass/fail.
      - `near_threshold` = any scored run within `band` of the threshold (flagged even at N=1).
      - if EVERY run errored, the distribution carries the error and no verdict.
    """
    criterion = results[0].criterion if results else "unknown"
    n_runs = len(results)
    scored = [r.score for r in results if r.error is None and r.score is not None]
    n_errors = n_runs - len(scored)

    if not scored:  # no verdict at all
        err = next((r.error for r in results if r.error), "judge produced no score")
        dist = JudgeDistribution(
            criterion, [], n_runs, n_errors, None, None, None,
            passed=False, stable=False, near_threshold=False,
            reason=next((r.reason for r in results if r.reason), ""), error=err,
        )
        dist._min_score = min_score
        return dist

    passed_flags = [s >= min_score for s in scored]
    stable = all(passed_flags) or not any(passed_flags)
    passed = all(passed_flags)                       # every run must pass — instability ≠ pass
    near_threshold = any(abs(s - min_score) <= band for s in scored)
    mean = sum(scored) / len(scored)
    # a representative reason: prefer a failing/near run's reason when not a clean pass
    rep = next((r.reason for r in results if r.error is None and r.score is not None
                and (r.score < min_score)), results[0].reason)
    dist = JudgeDistribution(
        criterion=criterion, scores=scored, n_runs=n_runs, n_errors=n_errors,
        mean=mean, lo=min(scored), hi=max(scored),
        passed=passed, stable=stable, near_threshold=near_threshold, reason=rep,
    )
    dist._min_score = min_score
    return dist


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
    n_unstable: int = 0          # checks whose N runs split on pass/fail — verdict not trustworthy
    runs_per_check: int = 1      # how many times each judged check was scored

    @property
    def n_failed(self) -> int:
        return self.n_checks - self.n_passed - self.n_errors


def _score_with_retry(judge: Judge, criterion: str, **kwargs) -> JudgeResult:
    """One judged score with a single bounded retry on a transport/parse error.

    Long outputs are the usual trigger and the failure is intermittent (2026-07-20), so a single
    retry recovers most of them. Never retry a rubric-level 'unknown_criterion' — that is deterministic.
    """
    res = judge.score_one(criterion, **kwargs)
    if res.error and res.error != "unknown_criterion":
        res = judge.score_one(criterion, **kwargs)
    return res


def score_run(
    conn: sqlite3.Connection, run_id: int, suite: Suite, judge: Judge | None = None,
    *, runs: int | None = None, band: float | None = None,
) -> JudgeScoreSummary:
    """Run the suite's judge checks over a run's stored output; persist the distribution + verdict.

    `runs` (default `JUDGE_RUNS`) repeats ONLY the judged step per check and aggregates the scores
    into a distribution with a stability flag — so a borderline case that the judge scores
    differently run to run is surfaced as UNSTABLE, not presented as a clean verdict. The
    deterministic rule checks are elsewhere and never repeat.
    """
    judge = judge or Judge()
    runs = settings.judge_runs if runs is None else max(1, runs)
    band = settings.judge_unstable_band if band is None else band
    cases = {c.id: c for c in suite.cases}
    store.clear_check_results(conn, run_id, layer="judge")

    n_checks = n_passed = n_errors = n_unstable = 0
    for row in store.get_case_results(conn, run_id):
        case = cases.get(row["case_id"])
        if case is None or not case.judge_checks:
            continue
        trace = json.loads(row["trace_json"]) if row["trace_json"] else {}
        context = json.dumps(trace, ensure_ascii=False)
        for check in case.judge_checks:
            min_score = float(check.params.get("min", 0.0))
            kwargs = dict(
                output=row["output"] or "",
                context=context,
                reference=row["reference"] or "",
                min_score=min_score,
            )
            # An 'unknown_criterion' is deterministic — one run is enough (repeating wastes calls).
            first = _score_with_retry(judge, check.type, **kwargs)
            results = [first]
            if first.error != "unknown_criterion":
                results += [_score_with_retry(judge, check.type, **kwargs)
                            for _ in range(runs - 1)]
            dist = aggregate_runs(results, min_score=min_score, band=band)

            n_checks += 1
            if dist.error:
                n_errors += 1
            elif dist.passed:
                n_passed += 1
            elif not dist.stable:
                n_unstable += 1
            store.add_check_result(
                conn,
                run_id=run_id,
                case_id=case.id,
                check_type=dist.criterion,
                layer="judge",
                passed=dist.passed,
                score=dist.mean,
                reason=f"[{judge.model} {judge.rubric_version}] {dist.summary} — {dist.reason}",
                # Persist the error so the report can separate "no verdict" from "failed".
                error=dist.error,
                decided_by="judge",
            )

    store.set_run_judge_model(conn, run_id, judge.model)
    return JudgeScoreSummary(
        run_id, judge.model, judge.rubric_version, n_checks, n_passed, n_errors,
        n_unstable=n_unstable, runs_per_check=runs,
    )


# ---- CLI: score a stored run's judge checks --------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Score a stored run's judge checks.")
    parser.add_argument("--run", type=int, default=None, help="run id (default: latest)")
    parser.add_argument("--suite", default="data/suites/rag_copilot.suite.json")
    parser.add_argument("--db", default=None)
    parser.add_argument("--repeat", type=int, default=None,
                        help="score each judged check N times → distribution + stability flag "
                             "(default: JUDGE_RUNS, else 1)")
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
        summary = score_run(conn, run_id, suite, runs=args.repeat)
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

    unstable = f", unstable={summary.n_unstable}" if summary.n_unstable else ""
    print(
        f"\nRun #{run_id} judge checks: {summary.n_passed}/{summary.n_checks} passed "
        f"(errors={summary.n_errors}{unstable})"
    )
    if summary.runs_per_check > 1:
        print(f"JUDGE STABILITY  {summary.runs_per_check} run(s)/check — "
              f"{summary.n_unstable} check(s) scored a split (UNSTABLE) verdict")
    print(f"JUDGE META  model={summary.model}  rubric={summary.rubric_version}")
    # An unstable verdict is not a trustworthy pass — surface it in the exit code too.
    return 1 if (summary.n_failed or summary.n_errors or summary.n_unstable) else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
