# LLM Evaluation & Guardrails Harness

A test harness for LLM systems: **test set → run the target → score with rule checks +
LLM-as-judge → regression & quality dashboard.** It points at any LLM system through a thin
adapter; the first wired target is the Regulatory RAG Copilot, and the same harness can grade
the rest. This is the capstone — the tool that evaluates the other projects.

> Built and demonstrated on **synthetic and public data only** — no PHI, no internal systems.
> A personal portfolio prototype.

---

## The problem it solves

Anyone can get an LLM feature to look good in a demo. Knowing whether it's actually good — and
whether a prompt tweak, a model swap, or a new release just quietly broke it — requires
measurement. Most projects don't have it. This harness is that missing layer: a repeatable way
to run an LLM system against a test set, score it with a mix of deterministic checks and a
calibrated LLM judge, catch regressions between versions, and red-team it against adversarial
and guardrail cases. It's the difference between "it worked when I tried it" and "here are the
numbers, and here's what changed."

## Who it's for

Anyone shipping an LLM product who needs evaluation in their development loop and CI — not as a
one-time check, but as a standing gate.

## What it does

1. **Test set** — cases of input + reference/expected behavior + the checks that should pass
   (including adversarial and guardrail cases).
2. **Runner** — executes the target system over the suite through a **thin adapter**, capturing
   each output and trace.
3. **Two-layer scoring:**
   - **Rule checks (deterministic):** must-include / must-not-include, format/schema, citation
     present, correct **abstention/refusal** behavior, **PII-leak** checks, regex/assertions.
   - **LLM-as-judge (qualitative):** groundedness, correctness vs reference, helpfulness — using
     a **versioned rubric**, with the judge itself calibrated (see `EVAL.md`). The judge is
     non-deterministic on borderline cases, so set **`JUDGE_RUNS=N`** to score each judged check N
     times and report the **distribution + a stability flag** — a split verdict shows as `UNSTABLE`
     rather than a clean pass, and a score within `JUDGE_UNSTABLE_BAND` of the threshold is flagged
     `near-threshold` (Decision 018). A single run is a sample, not a measurement.
4. **Aggregation & thresholds** — per-case and per-suite scores with pass/fail gates.
5. **Regression & comparison** — store runs; compare across prompts, models, and versions; flag
   deltas; render a quality dashboard.

## Lead target: the Regulatory RAG Copilot

The first wired adapter runs the RAG copilot over a suite that scores retrieval grounding,
citation presence, correct abstention on out-of-scope questions, and answer groundedness — then
lets you change the copilot's prompt or model and **see the regression** in the dashboard.

## Transfers to

Adapter-based, so it grades any LLM system: prompt-regression testing in CI, model comparison
(A/B across providers), output-quality gates, and **red-teaming** (adversarial/guardrail suites).
With thin adapters it points at the other projects in this portfolio — and at any LLM product,
in or out of healthcare. Write an adapter; the harness doesn't change.

> **The capstone point:** this harness evaluates the other projects. Building the thing that
> measures your other work — rather than just building more demos — is the signal that separates
> an engineer from a hobbyist.

## Tech stack

- **Backend:** FastAPI (Python)
- **Storage:** SQLite (suites, runs, scores, comparisons)
- **Frontend:** HTMX + server-rendered templates (run results, dashboard, diff view)
- **Judge:** Anthropic Claude with a versioned rubric; pluggable to a local model via Ollama
- **Targets:** a thin adapter interface; first adapter wraps the RAG copilot

See `DECISIONS.md` for why each choice was made over the alternatives.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

make seed      # load the sample suite (RAG copilot, incl. guardrail/adversarial cases)
make run       # uvicorn app.main:app --reload  → http://localhost:8000
make eval-run  # run the suite against the configured target
make selfcheck # validate the harness itself: judge calibration + rule-check fixtures (EVAL.md)
make reset     # clear runs + re-seed for a clean demo
```

Set `ANTHROPIC_API_KEY` in `.env`, or `MODEL_PROVIDER=ollama` to run the judge locally. The RAG
copilot target is configured via an adapter (URL/command in `.env`).

## Validating the evaluator

An evaluator you can't trust is worse than none. `EVAL.md` describes how the harness checks
*itself*: judge-vs-human agreement (calibration), rule-check correctness against known-good /
known-bad fixtures, and a confirmation that an injected regression actually gets flagged.

## Responsible AI & data

- **Synthetic/public test data only** — no PHI, no internal systems.
- **Deterministic where possible; the judge only for the qualitative.** Rule checks carry the
  load; the LLM judge is calibrated and versioned, never blindly trusted.
- **Guardrail and red-team cases are first-class** — abstention, refusal, and PII-leak checks are
  part of the suite, not an afterthought.

## Path to production

- **CI integration:** run suites on every prompt/model change; block merges on regressions.
- **Coverage:** larger suites, dataset versioning, per-feature thresholds, drift tracking.
- **Judge quality:** ongoing calibration against human labels; multiple judges / ensembling.
- **Governance:** for any sensitive target, the data-handling controls in the privacy methodology
  (BAA / in-boundary model, de-identification of test data).

## Project structure

```
app/
  main.py          # FastAPI app + routes
  suite.py         # test-set format: cases, references, checks
  adapters/        # target adapters (rag_copilot first; thin interface)
  runner.py        # execute the suite over a target, capture outputs/traces
  checks.py        # deterministic rule checks (incl. guardrail/PII/abstention)
  judge.py         # LLM-as-judge with a versioned rubric
  report.py        # aggregation, run comparison, regression flags
  selfcheck.py     # validate the harness itself (judge calibration + fixtures)
  templates/       # results, dashboard, run diff
data/
  suites/rag_copilot.suite.json
  calibration/judge_gold.json      # human-labeled set for judge calibration
DECISIONS.md  DEMO.md  EVAL.md  TODO.md  CLAUDE.md
```

## Status

In development. See `TODO.md` for the phased plan.
