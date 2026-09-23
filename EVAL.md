# EVAL.md — Validating the Evaluator

This project evaluates other systems, so its own `EVAL.md` answers the hard question:
**who evaluates the evaluator?** A harness whose judge is miscalibrated or whose checks are buggy
is worse than no harness — it manufactures false confidence. This is how the harness proves it can
be trusted.

Run with `make selfcheck` (`python -m app.selfcheck`).

---

## What we validate, and why

| Check | Question it answers | Why it matters |
|-------|--------------------|----------------|
| **Judge calibration** | Does the LLM judge agree with humans? | If the judge is wrong, every judged score is wrong. |
| **Rule-check correctness** | Do the deterministic checks pass/fail exactly as intended? | A buggy check silently passes bad output or fails good output. |
| **Regression detection** | Does an injected, known regression actually get flagged? | The harness's main job is catching the quiet break — prove it does. |

## Judge calibration

`data/calibration/judge_gold.json` — a set of (output, context) cases each with a **human label**
(pass/fail or a 0/1 groundedness call). The self-check runs the judge over them and reports
agreement with the human labels.

```json
[
  { "id": "cal-001", "output": "...", "context": "...", "human_label": 1,
    "note": "Fully grounded in context — judge should score 1." },
  { "id": "cal-008", "output": "...", "context": "...", "human_label": 0,
    "note": "Adds a fact not in context — judge should score 0." }
]
```

- Report **judge-human agreement** (and, where useful, precision/recall of the judge's "fail"
  call). If agreement is below threshold, the rubric is revised before the harness is trusted.
- Honest limitation: a small human set is directional; spot-check disagreements by hand.

## Rule-check correctness

Known-good / known-bad **fixtures** exercise each deterministic check:
- A citation-present check must pass on an output with a citation and fail on one without.
- A PII-leak check must fire on a planted identifier and stay quiet on clean text.
- An abstention check must pass when the target correctly declines an out-of-scope case and fail
  when it answers anyway.

These run as ordinary unit tests (`make test`) and as part of `selfcheck`. Any miss is a defect in
the check, not the target.

## Regression detection

Inject a deliberately degraded target (e.g., a prompt variant that drops citations or answers
out-of-scope questions), run it against the same suite, and confirm the comparison **flags the
regression** versus the baseline run. If it doesn't surface, the comparison logic is wrong.

## Output

Real output of `make selfcheck` against the configured judge (Opus 4.8) over the 12-case gold set:

```
JUDGE        agreement with human labels 1.00  (12/12; no disagreements)
COHERENCE    verdict vs its own rationale — detector OK; no self-contradictions in the gold set
RULE CHECKS  fixtures 23/23 pass  (citation, PII-leak, abstention, refusal, format, include/exclude)
REGRESSION   injected degraded target flagged: YES  (citation_present 1.00 -> 0.00; case pass 89% -> 33%, 11 checks)
JUDGE META   model=anthropic:claude-opus-4-8  rubric=g1

VERDICT: PASS — judge calibrated, checks sound, regression caught.
```

The gold set is **12 human-labelled cases** — a small, directional calibration set, not a benchmark; disagreements are
spot-checked by hand. Run **without** a provider key, the self-check falls back to an **offline mock judge and says so
loudly** (`model=mock…`): the mock is not a calibrated judge, so its agreement number is not evidence of calibration —
only a real-provider run is.

## Suggested thresholds

- **Judge agreement ≥ 0.90** before the judge's scores are trusted in reporting; otherwise revise
  the rubric and re-calibrate.
- **Rule-check fixtures = 100% pass** — a failing fixture is a harness bug, fix before use.
- **Regression detection must succeed** on the injected case every run.

## Limitations (state these honestly)

- Calibration is only as good as the human-labeled set; keep it current and spot-check.
- LLM-as-judge has irreducible error; that's exactly why deterministic checks carry most of the
  load and the judge is scoped to the qualitative.
- Self-check proves the harness behaves as designed, not that the suites are complete — suite
  coverage is a separate, ongoing effort.
