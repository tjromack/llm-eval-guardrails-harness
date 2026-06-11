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
