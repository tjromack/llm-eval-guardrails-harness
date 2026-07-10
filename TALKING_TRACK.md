# Talking Track — LLM Evaluation & Guardrails Harness

> Your study reference for speaking on this project. Stage in the portfolio arc: **MEASURE** (the
> capstone — the tool that grades the other five). Building the thing that measures your own work is
> the signal that separates an engineer from a hobbyist.

## ⚡ At a glance

- **Pitch:** The capstone that grades the other five — runs any LLM system through a test suite,
  scores with deterministic rules + a calibrated judge, and flags regressions before they ship.
- **Architecture:** Suite JSON → adapter to any target → runner captures raw output → two scoring
  layers (deterministic checks + LLM-judge) → report aggregates and compares runs.
- **Signature decision:** Two scoring layers, and capture separated from scoring — so you can
  re-score after a rubric change without re-calling the target.
- **Eval story:** The harness validates *itself* — rule fixtures 22/22, judge agreement 0.92 vs human
  labels, and an injected regression (citations 1.00→0.00) is correctly detected.

---

## The 60-second pitch

**Business framing:**
"This is the tool that grades the others. It runs any LLM system through a test suite, scores it
with deterministic checks plus a calibrated judge, and flags regressions between versions — so a
prompt tweak that quietly drops citations gets caught *before* it ships. It moves us from 'it worked
when I tried it' to 'here are the numbers,' and it treats guardrails — refusing jailbreaks, not
leaking PII, abstaining on out-of-scope — as first-class test coverage."

**Technical framing:**
"A measurement harness. Suite JSON defines cases with typed checks; a thin adapter points at any
target over HTTP or CLI; the runner captures the raw output. Then **two scoring layers**:
deterministic rule checks (citation, abstention, refusal, PII-leak, format) and an LLM-as-judge
(groundedness, correctness) with a versioned rubric and strict JSON. The report aggregates pass rates
and flags regressions past an epsilon of 0.05. Capture and scoring are **separate passes**, so I can
re-score after a rubric change without re-calling the target."

---

## Architecture (staged pipeline: Suite → Run → Capture → Check+Judge → Report)

```
suite.json ──▶ adapter (HTTP/CLI to any target) ──▶ runner (capture RAW output, no scoring)
                                                          │
                              ┌───────────────────────────┴───────────────┐
                              ▼                                            ▼
                  deterministic checks                              LLM-as-judge
                  (citation, abstention, refusal,                   (groundedness, correctness;
                   PII-leak, format)                                 versioned rubric, strict JSON)
                              └───────────────────────────┬───────────────┘
                                                          ▼
                                          report: aggregate + COMPARE runs ──▶ flag regressions
                                                          ▼
                                                      dashboard (/, /runs, /compare)
```

- `app/suite.py` — load/validate suite JSON.
- `app/adapters/` — the swap layer; a **new target ≈ 40 lines**. The runner resolves targets by
  *name* and never imports a concrete adapter.
- `app/runner.py` — execute the suite, persist **raw** output (no scoring yet).
- `app/checks.py` — deterministic rules (regex, marker sets, PII patterns).
- `app/judge.py` — the LLM judge; versioned rubric (`g1`), strict `{score, reason}` JSON.
- `app/report.py` — aggregation + run comparison; `REGRESSION_EPS = 0.05`.
- `app/selfcheck.py` — **validates the evaluator itself** (below).
- Tables: `runs`, `case_results`, `check_results`.

---

## The eval story (the harness validates ITSELF)

The headline move: this project's `make selfcheck` turns the measurement tooling on itself.

1. **Rule fixtures: 22/22** — the deterministic checks are validated on known-good/known-bad cases.
   *This gate doesn't depend on any provider — it passes every run.*
2. **Judge agreement: 0.92** — the judge vs 12 human-labeled cases (one deliberate contradiction at
   `cal-012` kept in to keep disagreement visible).
3. **Injected-regression detection: YES** — it intentionally drops citations (1.00 → 0.00, case pass
   100% → 33%) and confirms the harness *flags* it. *Also provider-independent — passes every run.*

A case passes only if **all** its checks pass; a judge error counts as a fail. `compare` matches
checks across runs by (case, check, layer) and flags pass→fail or a score drop beyond the epsilon.

---

## The signature decision

**Two scoring layers, and capture separated from scoring.** Rules handle anything decidable (cheap,
reproducible, trustworthy); the judge is reserved for genuinely qualitative properties. Separating
capture from scoring means the regression story is auditable — you know the *output* didn't change,
only your *judgment* of it.

---

## Honest weakness (say it before they do)

- The **headline 0.92 judge agreement is from the offline mock heuristic**, not a calibrated provider,
  and the gold set is **12 cases — directional, not statistical.** Trustworthy judge numbers need a
  real provider + a larger human-labeled set. *(But the two hard gates — rule fixtures 22/22 and
  injected-regression detection — don't depend on a provider and pass every run. Lead with those.)*

---

## Other things worth mentioning

- **Guardrails are first-class:** the sample suite has 9 cases — 4 grounded, 2 out-of-scope, 2
  adversarial (jailbreak/injection), 1 PII probe. Most real LLM risk is on the unhappy path.
- **How it grades the other projects:** adapters. The RAG copilot adapter is first; others are added
  the same way. Write an adapter; the harness doesn't change.
- **Why re-scoring matters:** after a rubric tweak you re-score a stored run without re-calling the
  target — fast, and it keeps the comparison honest.
- **Production extension:** CI integration — run suites on every prompt/model change, gate merges on
  regressions.

---

## The one-liner to remember

> **"It's the tool that grades the others — and to prove I can trust it, it runs its own checks on
> itself: the rule fixtures and the injected-regression test pass with no model in the loop."**
