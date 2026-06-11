# DEMO.md — Live Demo Script

A tight, repeatable walkthrough. The moments that land hardest: a **guardrail case scoring
correctly**, and a **regression getting flagged** after a prompt/model change. The first target
is the RAG copilot — so this also doubles as proof your projects share one quality bar.

## Before the demo
```bash
cp .env.example .env          # points at the bundled synthetic target (command transport)
make reset                    # clear runs, re-seed the sample suite (clean state -> run #1)

# Offline path (no API key): use the local mock judge. For a REAL judge, set
# ANTHROPIC_API_KEY in .env and drop the MODEL_PROVIDER override.
export MODEL_PROVIDER=mock     # Windows PowerShell: $env:MODEL_PROVIDER="mock"

make eval-run                 # baseline run (#1) against the RAG-copilot adapter
make selfcheck                # judge calibration + rule fixtures + injected-regression check
make run                      # start the dashboard -> http://localhost:8000
```
(The target is reached only through an adapter, configured in `.env`. Out of the box it's a
synthetic stand-in so the demo runs cold; point `.env` at the real copilot to grade it.)

## The ~90-second happy path

1. **Frame it.** *"This is the tool that grades my other projects. Right now it's pointed at the
   RAG copilot, through a thin adapter — synthetic/public suite only."*
   → Proves: the capstone framing + generality.

2. **Run the suite.** Show per-case results: rule checks (citation present, format) and judge
   scores (groundedness) side by side. *"Deterministic checks do most of the work; the LLM judge
   only handles the qualitative stuff — and it's calibrated."*
   → Proves: two-layer scoring, sound design.

3. **(Guardrail moment) Show an adversarial case.** An out-of-scope question the copilot must
   **abstain** on, and a **PII-leak** probe. *"Guardrail behavior is core test coverage here, not
   an afterthought — refusals, abstentions, and leak checks are part of every suite."*
   → Proves: evaluation *and* guardrails.

4. **(The one that lands) Introduce a regression.** Swap the copilot to a worse variant and re-run:
   ```bash
   # a worse "prompt/model": drops citations and stops abstaining (a quiet break)
   RAG_COPILOT_VERSION=degraded FAKE_RAG_MODE=degraded make eval-run    # run #2
   ```
   On the dashboard, pick baseline vs. degraded in **Compare** → the diff shows
   **⚠ REGRESSION FLAGGED** (case pass rate 100% → 33%, 11 checks regressed: citations dropped,
   out-of-scope answers no longer abstain, judge groundedness fell). *"A prompt tweak that drops
   citations is exactly what this catches between versions — and notice the adversarial refusal and
   the PII check still hold, so it's not a false alarm."*
   → Proves: regression detection, the CI mindset.

5. **Validate the evaluator.** Show `make selfcheck`: judge-vs-human agreement, rule-check
   fixtures, and the injected-regression confirmation. *"And because it judges other systems, I
   check the judge itself against human labels — who evaluates the evaluator."*
   → Proves: the rare, senior signal — calibrated, trustworthy evaluation.

## The capstone line (say it)
*"Anyone can build demos. This is the thing that measures them — and it's wired to grade the rest
of the portfolio, not just this one target."*

## Transfer targets to mention
Prompt-regression in CI, model A/B comparison, output-quality gates, red-teaming — for any LLM
product, in or out of healthcare. *"Write an adapter; the harness doesn't change."*

## Anticipated questions (answers in DECISIONS.md / EVAL.md)
- *Can you trust an LLM judge?* → only if calibrated; agreement is measured in selfcheck (004 / EVAL).
- *Why not judge everything with an LLM?* → deterministic checks where possible; judge for the
  qualitative only (003).
- *How does it grade your other projects?* → adapters; RAG copilot wired first (002).
- *What counts as a regression?* → run-to-run delta past a threshold (006).
- *Does test data leave the machine?* → judge can run locally; data is synthetic/public (007).

## If something breaks
- Don't debug live. *"Let me reset to a clean state"* → `make reset` → reload.
- Keep a screenshot/recording of the happy path, a saved comparison showing the regression, and a
  selfcheck report in `docs/` as a fallback.
