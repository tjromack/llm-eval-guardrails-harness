# INIT_PROMPT.md — Claude Code Kickoff

Paste this into Claude Code in a repo that already contains `README.md`, `CLAUDE.md`, `TODO.md`,
`DECISIONS.md`, `DEMO.md`, and `EVAL.md`.

---

You are helping me build the **LLM Evaluation & Guardrails Harness**. Before writing any code,
read `README.md`, `CLAUDE.md`, `TODO.md`, `DECISIONS.md`, and `EVAL.md`. `CLAUDE.md` is your
operating contract — follow its guardrails exactly, especially: synthetic/public test data only;
**adapter-decoupled** from targets (never hard-code a target — the RAG copilot is the first
adapter); **deterministic rule checks carry the load, the LLM judge is for qualitative properties
only**; the **judge is versioned and calibrated**, never blindly trusted; and **guardrail/red-team
cases (abstention, refusal, PII leak) are first-class** in every suite.

Work through `TODO.md` **one phase at a time**. For each phase:

1. Briefly state your plan and any decision points before starting.
2. Implement only that phase. Keep modules small and single-purpose.
3. Make sure `make run` works and the relevant behavior is demoable on the seeded suite.
4. Record any non-trivial choice (suite format, adapter interface, check-vs-judge split, rubric,
   regression logic) in `DECISIONS.md` with the rejected alternative and the why.
5. Make a single, readable commit summarizing what shipped and why.
6. **Stop and wait for my approval before starting the next phase.**

Key requirements:
- A suite is a list of cases (input, reference/expected, checks, category) including out-of-scope
  cases that must abstain and at least one PII-leak probe.
- Targets are reached only through a thin adapter; implement the RAG copilot adapter first
  (configurable endpoint/command in `.env`).
- The runner executes a suite over the target and persists outputs/traces + the target version.
- Deterministic checks cover must-include/exclude, format/schema, citation-present, abstention/
  refusal correctness, and PII leak. The LLM judge (versioned rubric, strict-JSON output) covers
  groundedness and correctness-vs-reference; record judge model + rubric version.
- Reporting aggregates per-case/per-suite scores, applies thresholds, and compares two runs to
  flag regressions; the dashboard shows it.
- Phase 7 builds `selfcheck` exactly as in `EVAL.md`: judge calibration vs human labels,
  rule-check fixtures, and confirmation that an injected regression is flagged; `make selfcheck`
  prints the report.
- `make reset` returns to a clean, seeded state for repeatable demos.

For Phase 1, propose the suite format and a starter RAG-copilot suite (including the guardrail/
adversarial cases) for my approval. Then implement Phase 0 (scaffold) and stop at the gate.
