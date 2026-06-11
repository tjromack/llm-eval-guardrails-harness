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
