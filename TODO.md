# TODO — Phased Build Plan

Build in phases. **Stop at each approval gate.** Commit at every phase boundary.

---

## Phase 0 — Scaffold ✅
- [x] Repo structure per README; `requirements.txt`, `Makefile`, `.env.example`, `.gitignore`.
- [x] FastAPI boots with a health route; base template renders.
- [x] All docs present (`README`, `CLAUDE.md`, `TODO.md`, `DECISIONS.md`, `DEMO.md`, `EVAL.md`).
- **Gate:** app boots; structure agreed.

## Phase 1 — Suite format & sample suite ✅
- [x] `app/suite.py`: a test-case format — input, reference/expected, and a list of checks
      (deterministic + judge), plus a `category` (incl. `guardrail` / `adversarial`).
- [x] `data/suites/rag_copilot.suite.json`: a starter suite for the RAG copilot, including
      out-of-scope cases that must abstain and at least one PII-leak probe.
- **Gate:** suite format reviewed; sample suite inspectable. (`python -m app.suite` inspects it.)

## Phase 2 — Target adapter interface (RAG copilot first) ✅
- [x] `app/adapters/`: a thin interface — given a case input, return target output + trace.
- [x] Implement the RAG copilot adapter (configurable endpoint/command in `.env`).
- **Gate:** the harness can call the RAG copilot for a case and capture its output.
      (`python -m app.adapters.rag_copilot "<question>"`; bundled synthetic stand-in target.)

## Phase 3 — Runner ✅
- [x] `app/runner.py`: execute a suite over the configured target, persisting each output/trace
      and the target version. (`app/store.py` owns the SQLite schema + access.)
- [x] `make eval-run` runs the suite end to end.
- **Gate:** a full suite runs against the target and stores raw results.

## Phase 4 — Deterministic rule checks ✅
- [x] `app/checks.py`: must-include/exclude, format/schema (regex), citation-present,
      abstention/refusal correctness, PII-leak detection. (`format` covers regex/assertions.)
- [x] Each case's checks score pass/fail with reasons; verdicts persist to `check_results`.
- **Gate:** rule checks correctly pass/fail on the sample suite, including guardrail cases.
      (`python -m app.checks --run <id>`; 18/18 pass on baseline; fail-side covered by tests.)

## Phase 5 — LLM-as-judge ✅
- [x] `app/judge.py`: judge qualitative properties (groundedness, correctness vs reference) with a
      **versioned rubric** (`g1`); strict-JSON output; record judge model + rubric version.
      Provider-abstracted (anthropic | ollama | mock); offline `mock` for tests/CI/demo.
- **Gate:** judge scores are produced and stored alongside rule-check results.
      (`python -m app.judge --run <id>`; run 1 → 18 rule + 5 judge verdicts, judge_model stamped.)

## Phase 6 — Aggregation, regression & dashboard ✅
- [x] `app/report.py`: per-case + per-suite scores; pass/fail thresholds; compare two runs and
      flag deltas (regressions/improvements). Lazy scoring (rule + judge) on first report.
- [x] UI: results view (`/runs/{id}`), run-to-run diff (`/compare`), and a quality dashboard (`/`).
- **Gate:** change the target's prompt/model, re-run, and see the regression flagged.
      (Baseline vs `FAKE_RAG_MODE=degraded`: case pass rate 100%→33%, 11 checks flagged.)

## Phase 7 — Validate the evaluator, polish & demo ✅
- [x] `app/selfcheck.py`: judge calibration vs `data/calibration/judge_gold.json` (human labels);
      rule-check correctness against known-good/known-bad fixtures; confirm an injected regression
      is detected. `make selfcheck` prints the report (see `EVAL.md`). Hard gates: fixtures 100% +
      regression flagged; judge gate soft for the offline mock, hard for a real provider.
- [x] Empty/error states; graceful handling of target/judge failures (errors-as-results;
      judge-unavailable recorded, not silent; target errors surfaced in the run view; 404s + empty
      dashboard).
- [x] `app/seed.py` (`make seed` / `make reset`); finalized `DEMO.md`; verified the cold demo path.
- **Gate:** full demo + self-check run from a clean state.

---

## Out of scope (note in README "Path to Production")
CI integration / merge gates, dataset versioning, drift tracking, judge ensembling, large-scale
suites, multi-target dashboards.
