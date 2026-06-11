# AI Engineering Portfolio

A body of work with one thesis: **engineer the reusable core, keep the frontend and the data
swappable.** Each project is a working prototype built the same disciplined way — front-loaded
design docs, a decision log, a demo script, and (where it counts) an evaluation step — on
**synthetic or public data only, no PHI and no internal systems.**

The six split into two groups: three built against a healthcare-payer problem space, and three
general-purpose engineering archetypes that transfer across domains.

---

## Payer-domain builds

| Project | What it shows |
|---------|---------------|
| **AI Use-Case Intake & Prioritization Console** | Product/strategy thinking: structured intake, five-dimension scoring, ROI hypotheses, a "when *not* to use AI" gate, and an executive decision brief. |
| **Regulatory RAG Copilot for Post-Payment** | Responsible RAG: grounded, cited answers over public CMS rules, abstention when unsure, and a real evaluation harness. |
| **Payment-Integrity Claims Reviewer** | The right division of labor: transparent rules detect, AI explains, a human decides — with a measured detector and an ROI dashboard. |

## Transferable engineering archetypes

| Project | What it shows | Transfers to |
|---------|---------------|--------------|
| **Agentic Workflow Orchestrator** | Tool registry → planner → guarded stepwise execution → human checkpoints → audit log. Plan-then-execute, not a black-box loop. | Onboarding automation, data reconciliation, report generation, research agents. |
| **LLM Document → Structured Data Extractor** | Schema-constrained extraction → deterministic validation → confidence anchored in validation → human review → structured load. | Invoices/AP, contracts, forms, resume parsing, KYC. |
| **LLM Evaluation & Guardrails Harness** | Test set → run target via adapter → rule checks + calibrated LLM-as-judge → regression dashboard. **The capstone — it grades the others.** | Prompt-regression CI, model comparison, output-quality gates, red-teaming. |

## The thesis in practice

Across all six, the valuable part is the engine, not the skin:

- **Reusable core, swappable edges.** Each project isolates a domain-agnostic engine (an
  orchestrator, an extractor, an evaluator, a scorer, a RAG pipeline) from its UI and its data.
  Change the schema, the corpus, the tools, or the domain — the core stands.
- **Built to be defended, not just demoed.** Every repo carries a decision log (each tradeoff and
  its rejected alternative) so any choice can be explained later.
- **Measured, not asserted.** Where it matters, projects ship an evaluation step — and the
  harness exists specifically to hold the others to a shared quality bar.
- **Responsible by default.** Synthetic/public data, human-in-the-loop on consequential outputs,
  AI used where it's strong and deliberately not where it isn't, and honest framing of limits.

## How the pieces connect

- The **evaluation harness** grades the **RAG copilot** first, with thin adapters to reach the
  rest — one shared quality bar across the portfolio.
- The **orchestrator** is the generalized pattern behind an internal workflow tool shipped at
  work (credited under Experience), shown here in a neutral domain.
- The **intake console**, **privacy methodology**, and **responsible-AI/regulatory** references
  describe *how* new work gets prioritized and governed — the operating model behind the builds.

## Repos

Payer-domain: `ai-usecase-intake-console/` · `postpay-regulatory-copilot/` ·
`payment-integrity-reviewer/`
Archetypes: `agentic-workflow-orchestrator/` · `document-structured-extractor/` ·
`llm-eval-guardrails-harness/`

Each contains its own README, decision log, demo script, and (where applicable) evaluation
harness. Shared stack: FastAPI · SQLite · HTMX · Claude (with a local-model option) ·
synthetic/public data only.
