# DECISIONS.md — Decision Log

Lightweight ADRs. One entry per non-trivial choice: the decision, the alternative, the why.
These are the script for "why did you build it this way?" Add an entry on every real tradeoff.

**Template**
```
## NNN. <Decision title>
- Date / phase:
- Decision:
- Alternatives considered:
- Why:
- Tradeoff accepted:
- Revisit if:
```

---

## 001. Stack: FastAPI + SQLite + HTMX
- Phase: 0
- Decision: Server-rendered HTMX on FastAPI with file-based SQLite.
- Alternatives considered: A CLI-only tool; a heavier eval platform.
- Why: One developer, must demo reliably and reset in one command. SQLite stores suites/runs/
  scores and makes run-to-run comparison trivial; HTMX gives a real dashboard and diff view with
  no build step. (A CLI alone wouldn't show the regression story as well in a live demo.)
- Revisit if: Team-scale usage or CI-first workflows dominate → add a CLI + API surface.

## 002. Adapter-decoupled from targets (RAG copilot first)
- Phase: 2
- Decision: The harness talks to any target only through a thin adapter interface; the RAG copilot
  is the first adapter.
- Alternatives considered: Building the eval logic directly into one project.
- Why: Decoupling is what makes this a reusable harness rather than a one-off test script — and
  it's what lets the harness grade the *other* projects. Wiring the RAG copilot first gives a
  concrete, demoable target without instrumenting everything on day one.
- Tradeoff accepted: An adapter must be written per target; that's the extension point, by design.

## 003. Two-layer scoring: deterministic checks + LLM-as-judge
- Phase: 4/5
- Decision: Use deterministic rule checks wherever possible; reserve the LLM judge for qualitative
  properties only.
- Alternatives considered: Judge everything with an LLM; check everything with rules.
- Why: Rules are cheap, reproducible, and trustworthy (format, citations present, abstention, PII
  leak); a judge is needed only for things like groundedness or correctness-vs-reference. Leaning
  on rules keeps the harness fast and reliable and minimizes dependence on a fallible judge.
- Tradeoff accepted: Some nuance still needs the judge; that part is calibrated (Decision 004).

## 004. The judge is versioned and calibrated, not blindly trusted
- Phase: 5/7
- Decision: Record the judge model + rubric version on every score; validate the judge against
  human labels in `selfcheck`.
- Alternatives considered: Trust LLM-as-judge output at face value.
- Why: "Who evaluates the evaluator?" is the obvious failure mode. If the judge isn't calibrated,
  the whole harness is theater. Calibration makes its reliability a measured number, not a hope.
- Tradeoff accepted: A human-labeled calibration set is effort; it's the credibility of the tool.

## 005. Guardrail / red-team cases are first-class
- Phase: 1/4
- Decision: Every suite includes adversarial inputs, required refusals/abstentions, and PII-leak
  checks — not just happy-path correctness.
- Why: Most real LLM risk is in the unhappy path. Treating guardrail behavior as core test
  coverage is what makes this an *evaluation & guardrails* harness, not just a quality scorer.

## 006. Regression detection as a first-class output
- Phase: 6
- Decision: Persist runs and compare any two, flagging deltas across prompts/models/versions.
- Why: The point of evaluation in a dev loop is to catch the quiet break a prompt tweak or model
  swap introduces. A single score is less useful than a trustworthy "this got worse."

## 007. Synthetic/public test data + local-judge option
- Phase: 1/5
- Decision: Suites use synthetic/public data; the judge can run on a local model.
- Why: Governance (no real/internal data) and a privacy-preserving path for sensitive targets.

## 008. This is the capstone — it evaluates the other projects
- Phase: 0
- Decision: Build the harness explicitly as the tool that grades the rest of the portfolio,
  starting with the RAG copilot.
- Why: Anyone can build more demos. Building the thing that *measures* your other work — judge
  calibration and all — is the signal that separates an engineer from a hobbyist, and it ties the
  portfolio together into one body of work with a shared quality bar.

## 009. Suite format: one JSON file, typed checks inline
- Phase: 1
- Decision: A suite is a single JSON file — an envelope (`suite_id`, `target`, `rubric_version`,
  `thresholds`) plus a `cases` list. Each case holds `input`, `reference`, `category`, and a list
  of `checks`, where each check is `{ "type": ..., ...params }`. The check `type` routes scoring:
  types prefixed `judge_` go to the LLM judge; all others are deterministic. `app/suite.py` loads
  and validates (unknown types, missing params, bad category, duplicate ids) and exposes the
  rule-vs-judge split as `case.rule_checks` / `case.judge_checks`.
- Alternatives considered: YAML suites; a separate checks file keyed by case id; a generic
  `assertions` blob interpreted later.
- Why: JSON needs no extra dependency and is the same format runs/scores persist in (SQLite JSON),
  so there's one mental model. Putting typed checks *inline on the case* makes the check-vs-judge
  split (Decision 003) visible in the data and lets validation fail loudly at load time rather than
  mid-run. The `type`-prefix convention keeps routing declarative — no per-case "is this a judge
  thing?" flag to keep in sync.
- Tradeoff accepted: JSON has no comments (worked around with a `_note` field); a very large suite
  in one file is less ergonomic than many small files — fine at portfolio scale, revisit with
  dataset versioning.
- Revisit if: suites grow past a few hundred cases or need composition/imports.

## 010. Phase-1 case-shape calls: plain-string input, deterministic abstention, synthetic PII plant
- Phase: 1
- Decision: (a) `input` is a plain string question by default (an object `{question, context}` is
  permitted for hermetic judge cases); (b) abstention/refusal correctness is a **deterministic**
  check, not a judge call; (c) the PII-leak probe plants **synthetic** identifiers in the input and
  fails if the target echoes them back — even though the user supplied them.
- Alternatives considered: (a) always pin context in the suite; (b) ask the judge whether the
  target abstained; (c) only probe PII that the model might fabricate unprompted.
- Why: (a) A plain string keeps the adapter thin and tests the copilot's *own* retrieval, which is
  what we want to regress on; pinned context stays available for cases that must be judged
  hermetically. (b) Abstention is a property you can check by markers/phrasing — cheaper and more
  reproducible than a judge, and it keeps the judge scoped to the genuinely qualitative
  (Decision 003). (c) Echo-back is the realistic leak path and is trivially, deterministically
  checkable; using synthetic identifiers honors the no-real-data rule (Decision 007).
- Tradeoff accepted: deterministic abstention detection can be fooled by unusual phrasing — the
  marker set is part of the harness and itself fixture-tested in `selfcheck` (Phase 7).
- Revisit if: targets phrase abstention in ways the marker check misses → add a judged fallback.

## 011. Adapter interface: errors-as-results, name registry, two transports, synthetic stand-in
- Phase: 2
- Decision: The adapter contract is `run(case_input) -> TargetResult(output, trace, target_version,
  latency_ms, error)`. Adapters **never raise into the runner** — transport/target failures come
  back as a `TargetResult` with `error` set. Targets are resolved by name through a registry
  (`get_adapter(suite.target)`), so the runner never imports a concrete adapter. The RAG-copilot
  adapter supports two transports chosen in `.env`: `http` (POST JSON) and `command` (subprocess,
  JSON on stdin). A bundled **synthetic stand-in** (`tools/fake_rag_copilot.py`) is the out-of-box
  target so the demo runs cold.
- Alternatives considered: let adapters raise and have the runner catch; resolve adapters by
  importing a module path; support only HTTP; require the real copilot to be running for any demo.
- Why: Errors-as-results means one unreachable target or one malformed case degrades to a recorded
  failure instead of aborting a whole suite — exactly what an eval harness needs. A name registry
  keeps the runner target-agnostic (Decision 002) and makes "write an adapter, the harness doesn't
  change" literally true. Two transports cover the common ways a copilot is deployed (a service vs.
  a CLI/script) without assuming either. The synthetic stand-in keeps the seam demoable and the
  tests hermetic without real data or a live service (Decision 007); a degraded variant of it later
  drives the regression demo.
- Tradeoff accepted: the stand-in is not the real copilot — it proves the *harness* works, not the
  copilot's quality. The trace shape is loosely typed (a dict) rather than a strict schema.
- Revisit if: a target needs streaming, auth, or a richer typed trace → extend TargetResult.

## 012. Runner persists raw results first; scoring is a separate, re-runnable pass
- Phase: 3
- Decision: The runner calls the target and writes the **raw** result (output, trace, latency,
  error) plus the run metadata (suite, target, target_version, rubric_version) to SQLite. It does
  **no scoring**. Checks (Phase 4) and the judge (Phase 5) read these stored rows in a later pass.
  Storage lives in `app/store.py`; runs have integer ids so any two compare directly (Decision 006).
- Alternatives considered: score inline while running (one pass, target call + checks + judge
  together); store only pass/fail, not raw output.
- Why: Separating capture from scoring means a run can be **re-scored without re-calling the
  target** — re-run the judge after a rubric change, fix a buggy check and re-grade, or diff two
  stored runs — all from persisted raw output. It also keeps each module single-purpose and makes
  target latency independent of (potentially slow) judge calls. Keeping raw output is what makes
  the regression story auditable rather than just a number.
- Tradeoff accepted: raw outputs take more space than a verdict, and scoring is a second step
  rather than free during the run. Both are fine at portfolio scale.
- Revisit if: suites get large enough that storing full outputs is costly → store hashes/excerpts
  with opt-in full capture.

## 013. Rule checks: pure functions returning pass/fail + reason; markers are harness-owned
- Phase: 4
- Decision: Each deterministic check is a pure function of `(output, light context, params)` that
  returns a `CheckResult(passed, reason)` — always a boolean with an explanation, never a maybe.
  Abstention/refusal are decided by **marker phrase sets** and PII by a small **pattern set**, all
  defined in `app/checks.py` and exported so `selfcheck` fixtures (Phase 7) test the checks
  themselves. `citation_present` looks in the output text and falls back to the trace's citations.
  Verdicts persist to a `check_results` table (`layer='rule'`) so the judge (Phase 5) and reporting
  (Phase 6) share one shape.
- Alternatives considered: a judge/LLM call for abstention/refusal; returning only a boolean with no
  reason; an NER library for PII; storing rule verdicts in a separate schema from judge verdicts.
- Why: Markers/patterns are cheap, deterministic, and inspectable — and because they're the part
  most likely to be subtly wrong, they're fixture-tested in selfcheck (closes the "who checks the
  checker" gap for the deterministic layer). Carrying a `reason` makes a failing check actionable in
  the dashboard and the demo, not just a red dot. One `check_results` table with a `layer` column
  lets Phase 6 aggregate rule + judge uniformly.
- Tradeoff accepted: marker/pattern matching misses creative phrasings and exotic PII formats — by
  design the judge is *not* used here (Decision 003); the marker sets are versioned with the harness
  and grown as gaps surface. PII patterns are tuned for synthetic identifiers, not exhaustive.
- Revisit if: real targets phrase refusals/abstentions in ways markers miss, or a target handles
  real PII formats → add a judged fallback and/or an NER-backed PII check.

## 014. Judge: provider-abstracted, strict-JSON, versioned; offline `mock` stand-in
- Phase: 5
- Decision: `app/judge.py` scores only the qualitative criteria (groundedness, correctness) via a
  `JudgeProvider` abstraction (`anthropic` | `ollama` | `mock`). The model must return strict JSON
  `{"score", "reason"}` — a non-JSON reply is a *recorded error*, never a guessed score. Every score
  carries the judge model id + `RUBRIC_VERSION`, and the model id is stamped on the run. Verdicts go
  to the shared `check_results` table with `layer='judge'`. A `mock` heuristic provider lets the
  pipeline run offline (tests/CI/demo) and is explicitly labeled "not calibrated".
- Alternatives considered: hard-wire the Anthropic SDK; accept free-text judge output and regex a
  number out; let a parse/transport failure raise and abort scoring; reuse the synthetic target as
  the offline judge.
- Why: The provider seam is what makes "judge runs locally for sensitive targets" real (Decision 007)
  and keeps tests network-free. Strict JSON + recorded parse errors means a flaky judge surfaces as
  an error rather than a silently-wrong score — essential for a tool whose credibility is the point
  (Decision 004). Stamping model + rubric on every score makes calibration (Phase 7) and run-to-run
  comparison meaningful. One `check_results` table for both layers lets Phase 6 aggregate uniformly.
- Tradeoff accepted: the `mock` judge is a heuristic, useful only for plumbing/demo — real
  calibration requires a real provider. Strict-JSON parsing can reject an otherwise-fine answer that
  formats badly; one embedded-object fallback softens this, nothing looser.
- Revisit if: judges need multi-criterion single-call scoring, ensembling, or token/cost budgeting.

## 015. Reporting: case passes only if all checks pass; regression = pass→fail or score drop
- Phase: 6
- Decision: A *case* passes only if **all** its checks pass (rule + judge); a judge error counts as a
  fail, never a silent pass. The headline metric is **case pass rate** vs the suite's
  `suite_pass_rate` threshold. `compare` matches checks across two runs by `(case, check, layer)`;
  a **regression** is a check going pass→fail OR a judge score dropping ≥ `REGRESSION_EPS` (0.05),
  an **improvement** is fail→pass. A run is **flagged** if anything regressed, the case pass rate
  fell more than EPS, or the candidate dropped below threshold while the baseline met it. Scoring is
  applied lazily on first report (`ensure_scored`) so a captured run is viewable without a manual
  scoring step.
- Alternatives considered: average score as the headline; per-check pass rate as the gate; compare
  only aggregate numbers (not per-check); require an explicit scoring step before reporting.
- Why: "All checks must pass" matches how guardrails actually work — a case that leaks PII but nails
  format is not a pass. Matching per-check across runs is what turns "it got worse" into "*this* got
  worse, here," which is the whole point (Decision 006). Counting a judge error as a fail keeps a
  flaky judge from masking problems. Lazy scoring keeps the demo one-command without re-calling the
  target (Decision 012).
- Tradeoff accepted: an all-or-nothing case verdict hides partial progress (5/6 checks fixed still
  reads FAIL) — the per-check view and per-layer tallies expose that detail. A fixed EPS is a blunt
  threshold; per-check thresholds are a later refinement.
- Revisit if: suites need weighted/criticality-tiered checks or per-metric regression thresholds.

## 016. Self-check has hard gates (fixtures, regression) and a soft, honest judge gate
- Phase: 7
- Decision: `selfcheck` runs three validations and exits non-zero only on a *hard* gate failure:
  rule-check fixtures must be 100% and the injected regression must be flagged. Judge calibration is
  reported against a human-labeled gold set and compared to a 0.90 threshold, but a sub-threshold
  result fails the run **only when a real provider is configured** — with the offline `mock` it
  prints PASS-with-a-loud-caveat. If no real provider can be built, selfcheck falls back to the mock
  and says so explicitly rather than skipping calibration silently.
- Alternatives considered: make every gate hard (mock calibration would always fail the run); skip
  calibration when offline (hides the mechanism); trust the judge without a gold set.
- Why: The deterministic layer carries the load (Decision 003), so its fixtures and the
  regression-detection logic are the parts that *must* be correct — those are hard gates. Judge
  calibration is inherently provider-dependent: gating the whole harness on a stand-in's number
  would be theater, but hiding the number would defeat the "who evaluates the evaluator" point
  (Decision 004). The mock-vs-real split lets `make selfcheck` run cold and honestly, while still
  failing a *real* judge that isn't trustworthy. The gold set deliberately includes one subtle
  contradiction (cal-012, 30-vs-60 days) a lexical judge misses, so the disagreement-reporting path
  is exercised and the small-set limitation stays visible.
- Tradeoff accepted: a green offline selfcheck does not prove the shippable judge is calibrated —
  only a run with a real provider does; the report states this plainly. The gold set is small and
  directional (EVAL.md), not a statistical guarantee.
- Revisit if: calibration needs a larger/stratified gold set, per-criterion thresholds, or
  precision/recall on the judge's "fail" call rather than raw agreement.

## 017. Grade the real copilot over HTTP (`/answer`); the command shim is the fallback
- Decision: With the copilot's new `POST /answer` (copilot DEC 014), the harness's existing **http**
  transport grades the real copilot directly — `RAG_COPILOT_ADAPTER=http`,
  `RAG_COPILOT_URL=http://localhost:8000/answer`. The endpoint returns `{text, citations, ...}`, which
  `_parse_payload` already maps to `output=text` / `trace=citations`, so **no adapter code changed** — only
  the default URL (`/ask` → `/answer`) and docs. The `tools/real_rag_copilot.py` command shim stays as the
  **no-server fallback** (it reloads the copilot's model per case). Two regression tests lock the
  `/answer` response shape into the http transport.
- Why: The http transport was built first (Decision 002) for a JSON endpoint that didn't exist yet, so the
  subprocess shim was the stopgap — 8–23 s/case reloading the model. A warm server removes the reload and
  makes Run #N fast, which matters once the suite grows. Keeping the shim means the harness still grades the
  copilot with no server running (CI, a cold demo).
- Rejected: Deleting the command shim (loses the no-server path); adding HTTP inside the shim (redundant —
  the adapter already has a tested http transport); changing `_parse_payload` (the copilot's `text`/
  `citations` already fit the existing keys). Verified live: http adapter vs a warm copilot returned the
  grounded answer with a full citation trace (score 0.7529) and an abstention in 240 ms.
