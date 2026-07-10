# Claude Code Prompt — Generate `MASTERY.md` for a Portfolio Project

**How to use:** open Claude Code in the root of one project repo and paste everything in
the `--- PROMPT ---` block below. Run it once per repo (all six projects). It inspects the
actual codebase and writes a `MASTERY.md` grounded in *that* repo — real filenames, real
commands, real metrics, real decisions. Repeat for each project; the structure stays
identical so the six docs read as one set.

> Tip: run it on a clean branch (`git checkout -b docs/mastery`) so you can review the diff
> before committing.

---

--- PROMPT ---

You are documenting THIS repository so its author can own it cold in a technical interview.
Produce a single file at the repo root named `MASTERY.md`.

## Step 1 — Investigate before you write

Read the repo first. Do not invent anything; everything in the doc must trace to a real
file, command, or recorded fact. Read at least:

- `README.md`, `USER_GUIDE.md`, `DECISIONS.md`, `EVAL.md`, `DEMO.md`, `TODO.md` (whichever exist)
- The application source (e.g. everything under `app/` or `src/`), the `Makefile`, and `.env.example`
- The test suite, and any eval/self-check entry point
- Any sample output or metrics already recorded in `EVAL.md` or the user guide

As you read, identify and note:
- The one-sentence purpose and the domain.
- The **core engine** modules and their input→output contract, in build order.
- The **swap layer**: what is domain-specific vs. domain-agnostic (this project follows a
  "reusable engine + thin swap layer" thesis — find where that seam is).
- The data flow end to end (so you can draw it).
- Every meaningful design decision and the alternative it rejected (mine `DECISIONS.md`).
- Real evaluation numbers and their honest caveats (don't fabricate; if a number requires
  running the eval, say which command produces it).
- The guardrails / failure handling (abstention, validation, human checkpoints, etc.).

## Step 2 — Write `MASTERY.md` in EXACTLY this structure

Match the voice: confident, first-person talking tracks, interrogation-ready, honest about
limitations, no marketing fluff. Use real `code`/file references throughout.

**Title + intro.** `# MASTERY.md — Owning This Project`, then a one-line framing: five
things to do without notes (explain, draw, rebuild, extend, defend), and a short blockquote
"Quick map of the codebase" listing the key modules with a one-clause description of each.

**## 1. Explain what it does and why, in plain English, in 60 seconds**
- A **talking track** as a blockquote, ~150 words, spoken aloud, naming the domain and the
  single most important property (e.g. grounded/abstains, validates, human-decides).
- **The one-sentence version.**
- **The three words to never lose** (the anchor concepts).
- **Self-check:** "you've got it when…".

**## 2. Draw the architecture from memory**
- A **fenced ASCII diagram** (inside triple backticks) of the real data flow, built from the
  actual modules. Keep it under ~80 characters wide so it prints cleanly.
- **Memory aids:** the ordered stages with a mnemonic, the offline/online (or local/remote)
  split, and where the external model call(s) happen.
- **Self-check.**

**## 3. Rebuild this core engine from scratch**
- **Build order & contracts:** number each core module; for each, state its input→output
  contract in one or two lines, and why it comes where it does.
- **The minimal happy path in pseudocode** (a fenced block) — the thing to write cold.
- Note the non-core add-ons (web layer, eval) briefly.
- **Self-check.**

**## 4. Extend it to a new domain by swapping the "swap layer"**
- A **table** with columns: *Swap this* | *File* | *What changes* — listing every
  domain-specific edit point.
- A short paragraph naming **the engine (what you DON'T touch)** and why.
- **The recipe:** a numbered list of the exact steps to port it to a new domain.
- **Why it's this clean:** one paragraph on why the engine is domain-blind.
- **Self-check.**

**## 5. Defend every design decision to a skeptic**
- A series of **"Question?" → defense** pairs (bold the question, answer in 2–4 sentences),
  one per real decision found in `DECISIONS.md` or the code. Cover at minimum: the core
  architecture choice and its rejected alternative; model/provider choices; storage choice;
  any custom algorithm; how hallucination/error is prevented AND measured; any threshold and
  how it was calibrated; the honest read on every weak-looking metric; and the
  data/compliance posture. Cite real numbers where they exist and state caveats without
  getting defensive. Reference the relevant `DECISIONS.md` IDs in parentheses.
- **Self-check.**

**### How to use this doc** — one short closing paragraph: read once, then drill the
self-checks; you own it when you can do all five cold.

**## Mastery checklist** — close with a checkbox list the author can tick over time:
```
- [ ] 1. Explain it in 60 seconds (domain + the anchor property), no notes
- [ ] 2. Draw the architecture from a blank board (stages, splits, external calls, guardrails)
- [ ] 3. Name the modules in build order, state each contract, write the happy path cold
- [ ] 4. List the swap-layer files, say what stays untouched and why
- [ ] 5. Defend any decision a skeptic names — alternative rejected + real numbers + caveats
```

## Step 3 — Constraints & finish

- Ground EVERYTHING in this repo. Use real module names, real `make`/CLI commands, real
  metric values. If you assert a number, it must come from `EVAL.md`/the user guide or be
  labeled as "run `<command>` to get this."
- Preserve the project's actual terminology and the "engine vs swap layer" framing.
- Keep the talking track ~150 words and genuinely speakable.
- Write only `MASTERY.md`. Do not modify other files.
- When done, print a short summary listing the files you read to ground the doc, and flag any
  section where the repo didn't give you enough to work with (so the author can fill the gap).

--- END PROMPT ---

---

## Notes for you (not part of the prompt)

- The six projects: RAG Copilot, Document → Structured Data Extractor, Payment-Integrity
  Claims Reviewer, AI Use-Case Intake & Prioritization Console, Agentic Workflow
  Orchestrator, LLM Evaluation & Guardrails Harness. The prompt is repo-agnostic — it adapts
  to whichever one you run it in.
- Each project's guardrail differs, and section 2/5 should reflect that: the RAG Copilot
  abstains, the Extractor validates + scores confidence, the Claims Reviewer keeps the LLM
  out of the decision, the Console flags "not a fit for AI," the Orchestrator gates
  consequential steps + audits, the Harness is itself the eval. Let Claude Code discover this
  per repo rather than pre-filling it.
- After it writes each `MASTERY.md`, you can run the same PDF styling we used for the user
  guides to produce a matching `<Project>_Mastery-Guide.pdf`.
