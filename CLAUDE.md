# CLAUDE.md — Operating Contract

Working agreement for building this project with Claude Code. Read it before each session.

## Purpose

Build the **LLM Evaluation & Guardrails Harness**: a FastAPI tool that runs an LLM system over a
test suite through a thin adapter, scores it with deterministic rule checks plus a calibrated
LLM-as-judge, and tracks regressions across versions. First wired target: the Regulatory RAG
Copilot. This is the capstone that evaluates the other portfolio projects, and must itself be
trustworthy.

## Operating principles (guardrails)

1. **Synthetic/public test data only.** No PHI, no internal systems.
2. **Adapter-decoupled.** The harness talks to targets only through a thin adapter interface; it
   never hard-codes a target. The RAG copilot adapter is the first; others are added the same way.
3. **Deterministic checks carry the load; the judge is for the qualitative only.** Use rule checks
   wherever a property can be checked deterministically (format, must-include/exclude, citation
   present, abstention/refusal, PII leak). Use the LLM judge only for things like groundedness or
   correctness-vs-reference.
4. **The judge is versioned and calibrated, never blindly trusted.** Record the judge model +
   rubric version; validate the judge against human labels (`selfcheck`).
5. **Guardrail/red-team cases are first-class.** Adversarial inputs, required refusals/abstentions,
   and PII-leak checks are part of every suite.
6. **Regressions are the point.** Persist runs so any two can be compared and deltas flagged.

## Stack

- Python 3.11+, FastAPI, Uvicorn
- SQLite via `sqlite3` (suites, runs, scores, comparisons)
- HTMX + Jinja2 templates
- Anthropic SDK for the judge; provider abstraction for `MODEL_PROVIDER=ollama`
- Thin adapter interface for targets; `pytest`

## Commands

```bash
make install    # venv + install
make seed       # load the sample suite (RAG copilot + guardrail/adversarial cases)
make eval-run   # run the suite against the configured target
make selfcheck  # validate the harness: judge calibration + rule-check fixtures
make run        # uvicorn app.main:app --reload
make test       # pytest
make reset      # clear runs + re-seed (clean demo state)
make fmt        # format
```

## Conventions

- Small, single-purpose modules (see README structure).
- Adapters implement one interface: given a case input, return the target's output + any trace.
- Persist every run: target + version, per-case outputs, check results, judge scores + rubric
  version, timings.
- No secrets in code; read from `.env`.
- **Commit at each phase boundary** with a readable message; the git history is an interview
  artifact.
- Update `DECISIONS.md` on every non-trivial choice (suite format, adapter interface, check vs
  judge split, rubric, regression logic) with the rejected alternative and the why.

## Definition of done (per phase)

- The phase's checklist in `TODO.md` is complete.
- `make run` works and the relevant behavior is demoable on the seeded suite.
- For the self-check phase: `make selfcheck` runs and reports per `EVAL.md`.
- New decisions recorded; a commit marks the phase boundary.
- **Stop and wait for my approval before the next phase.**

## Do not

- Do not hard-code a target; always go through an adapter.
- Do not use the LLM judge where a deterministic check would do.
- Do not trust the judge without calibration; always record rubric + model version.
- Do not use real/PHI test data; do not pass an approval gate without approval.
