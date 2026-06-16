# MASTERY.md — Owning This Project

Own this cold: **explain** it, **draw** it, **rebuild** it, **extend** it, and **defend** it — all
without notes. This is the capstone of the portfolio: the tool that *grades the other projects*, so
it has to be trustworthy itself.

> **Quick map of the codebase**
> - `app/suite.py` — the test-set format: cases, references, typed checks (load + validate).
> - `app/adapters/base.py` — the thin seam every target is reached through (`run → TargetResult`).
> - `app/adapters/rag_copilot.py` — first target adapter; `http` + `command` transports.
> - `app/runner.py` — runs a suite over a target, persists **raw** output/trace (no scoring).
> - `app/store.py` — SQLite schema + access: `runs`, `case_results`, `check_results`.
> - `app/checks.py` — deterministic rule checks (citation, abstention, refusal, PII, format).
> - `app/judge.py` — LLM-as-judge (groundedness, correctness); versioned rubric, strict JSON.
> - `app/report.py` — aggregation, thresholds, run-to-run comparison + regression flags.
> - `app/selfcheck.py` — validates the harness itself: calibration + fixtures + injected regression.
> - `app/main.py` — FastAPI dashboard: `/`, `/runs/{id}`, `/compare`, `/health`.
> - `tools/fake_rag_copilot.py` — synthetic stand-in target (baseline + `degraded` mode).

---

## 1. Explain what it does and why, in plain English, in 60 seconds

> This is an evaluation and guardrails harness for LLM systems. You give it a test suite — inputs,
> expected behavior, and the checks each output must pass — and it runs that suite against any LLM
> target through a thin adapter, then scores every output two ways. Deterministic rule checks carry
> the load: is a citation present, did it abstain on an out-of-scope question, refuse an adversarial
> prompt, leak a planted identifier. An LLM-as-judge handles only the qualitative stuff —
> groundedness, correctness — and the judge is versioned and calibrated against human labels, never
> trusted blindly. It stores every run, so when you tweak a prompt or swap a model it flags the
> regression. The whole point is moving from "it worked when I tried it" to "here are the numbers,
> and here's exactly what changed." It's the capstone — it grades my other projects.

**The one-sentence version:** Run an LLM system over a test suite through an adapter, score it with
deterministic checks plus a calibrated judge, and flag regressions across versions.

**The three words to never lose:** **adapter-decoupled · two-layer scoring · calibrated judge.**

**Self-check:** you've got it when you can say *why deterministic checks carry the load and the
judge is scoped to the qualitative* — and why the harness validates itself.

---

## 2. Draw the architecture from memory

```
  data/suites/rag_copilot.suite.json        tools/fake_rag_copilot.py
  (cases: input, reference, checks)            (or a REAL target)
            |                                          ^
            v                                          | adapter.run(input)
   suite.py  load+validate                             |  [EXTERNAL CALL #1]
            |                                           |
            v                                           |
   runner.py  run_suite  ---------------------> adapters/ (http | command)
            |  capture raw output+trace                 |
            v                                           v
   store.py  runs + case_results  <----------  TargetResult(output, trace, version)
            |
            |  (scoring is a SEPARATE, lazy pass)
            v
   +--------+--------------------------+
   |                                   |
   v                                   v
 checks.py  rule checks            judge.py  groundedness/correctness
 (citation, abstention,            versioned rubric, strict JSON
  refusal, PII, format)            [EXTERNAL CALL #2: anthropic|ollama|mock]
   |                                   |
   +------------------+----------------+
                      v
            store.py  check_results (layer = rule | judge)
                      |
                      v
            report.py  aggregate + threshold + COMPARE two runs -> regressions
                      |
                      v
            main.py  dashboard  /  ·  /runs/{id}  ·  /compare

  selfcheck.py  (validates the evaluator, off to the side):
     calibration vs judge_gold.json  +  rule fixtures  +  injected regression
```

**Memory aids — the ordered stages (mnemonic "Same Run Captures, Checks Judge, Reports"):**
**S**uite → **R**unner → **C**apture (store raw) → **C**hecks + **J**udge → **R**eport. The
**offline/online split:** everything runs offline with the synthetic target + `mock` judge; you only
go online when an adapter points at a real target (call #1) or `MODEL_PROVIDER=anthropic` sends the
judge to the API (call #2). There are exactly **two** external-call sites — the adapter and the
judge provider — and both are abstracted.

**Self-check:** you can place the two external calls and explain why capture and scoring are
separate stages (re-score a stored run without re-calling the target).

---

## 3. Rebuild this core engine from scratch

**Build order & contracts** (this is also the git history, phases 1→7):

1. **`suite.py`** — *in:* a suite JSON path; *out:* a validated `Suite` of `Case`s, each with typed
   `Check`s. It owns the format and fails loudly on bad input. First because everything consumes it.
2. **`adapters/base.py`** — *in:* a case input (str/dict); *out:* `TargetResult(output, trace,
   target_version, latency_ms, error)`. Errors come back *as results*, never raised. A name registry
   (`get_adapter`) keeps the runner target-agnostic.
3. **`store.py`** — *in:* run metadata / per-case results / check verdicts; *out:* rows in `runs`,
   `case_results`, `check_results`. Integer run ids so any two runs compare directly.
4. **`runner.py`** — *in:* a `Suite` + an `Adapter`; *out:* a persisted run of **raw** outputs. No
   scoring here — that's deliberate so a run is re-scorable.
5. **`checks.py`** — *in:* a `Check` + a `CheckContext(output, reference, trace, ...)`; *out:*
   `CheckResult(passed, reason)`. Pure functions; markers for abstention/refusal, patterns for PII.
6. **`judge.py`** — *in:* criterion + output + context/reference; *out:* a clamped 0–1 `score` +
   `reason`, stamped with model id + `RUBRIC_VERSION`. Strict JSON; a bad reply is a recorded error.
7. **`report.py`** — *in:* a run id (or two); *out:* a `RunReport` (per-case/suite, thresholded) or a
   `Comparison` flagging regressions. Lazily scores on first view.

**The minimal happy path in pseudocode** (the thing to write cold):

```
suite   = load_suite("data/suites/rag_copilot.suite.json")
adapter = get_adapter(suite.target)            # resolved by name, not imported
run_id  = create_run(suite, adapter.target_version)

for case in suite.cases:
    result = adapter.run(case.input)           # external call #1
    store_case_result(run_id, case, result)    # RAW only

for case in suite.cases:                        # separate scoring pass
    out = stored_output(run_id, case.id)
    for chk in case.rule_checks:  store(run_check(chk, ctx(out)))      # deterministic
    for chk in case.judge_checks: store(judge.score(chk, out, ref))   # external call #2

report = build_run_report(run_id, suite)        # all-checks-pass => case passes
flagged = compare(baseline_id, run_id, suite).flagged
```

**Non-core add-ons:** the FastAPI/Jinja layer in `app/main.py` (read-only views) and the
self-validator `app/selfcheck.py` sit on top of this engine; neither is needed to score a run.

**Self-check:** you can write the loop above from memory and explain why scoring is a second pass.

---

## 4. Extend it to a new domain by swapping the "swap layer"

For this project a "new domain" = **a new LLM system to grade**. The engine doesn't change; you
write a little configuration around it.

| Swap this | File | What changes |
|---|---|---|
| The target adapter | `app/adapters/<new>.py` (+ `@register("<name>")`) | How you call the system (HTTP/CLI/SDK) and parse its output+trace. ~40 lines. |
| The suite | `data/suites/<new>.suite.json` | Domain cases: inputs, references, `category`, and which checks apply — incl. guardrail/PII cases. |
| The judge rubric | `app/judge.py` (`RUBRIC_VERSION`, criterion instructions) | Only if "grounded/correct" means something domain-specific; bump the version. |
| The calibration gold set | `data/calibration/judge_gold.json` | Human-labeled (output, context, label) pairs to re-validate the judge for the new domain. |
| Marker/PII sets *(optional)* | `app/checks.py` | Only if the new domain abstains/refuses in different language or has different identifiers. |

**The engine (what you DON'T touch):** `runner.py`, `store.py`, the check *mechanics* in
`checks.py`, the judge *mechanics* in `judge.py`, `report.py`, `selfcheck.py`, and `main.py`. That's
the reusable core — it never names a target.

**The recipe:**
1. Write an adapter implementing `run(input) -> TargetResult`; decorate it `@register("billing_bot")`.
2. Author `data/suites/billing_bot.suite.json` with `"target": "billing_bot"` and your cases/checks.
3. (If needed) extend the judge rubric and bump `RUBRIC_VERSION`; tune marker/PII sets.
4. Build a small `judge_gold.json` and run `make selfcheck` to confirm the judge agrees with humans.
5. `make eval-run`, then compare runs in the dashboard. **Write an adapter; the harness doesn't change.**

**Why it's this clean:** the runner resolves targets by *name* through a registry
(`get_adapter(suite.target)`) and never imports a concrete adapter (Decision 002/011); scoring reads
stored raw output (Decision 012), so it's blind to *how* the output was produced. The only
domain-specific knowledge lives in the adapter, the suite JSON, and the rubric/calibration data.

**Self-check:** you can name the five swap points and the one thing that makes the engine
domain-blind (name-based target resolution + scoring off stored output).

---

## 5. Defend every design decision to a skeptic

**Why an adapter seam instead of building eval into the target?** Decoupling is what turns this from
a one-off test script into a reusable harness that can grade *any* LLM system — including my other
projects. The runner resolves targets by name and never hard-codes one (002/011). Cost: an adapter
per target — but that's the extension point, by design.

**Why two scoring layers instead of judging everything with an LLM?** Rules are cheap, reproducible,
and trustworthy for anything you can decide deterministically — citation present, abstention,
refusal, PII leak, format. The judge is reserved for the genuinely qualitative — groundedness,
correctness (003). Leaning on rules keeps the harness fast and minimizes dependence on a fallible
model.

**Why is the judge call abstracted across three providers?** `anthropic` is the real judge,
`ollama` is a local/private option for sensitive targets, and `mock` is an offline heuristic so the
pipeline runs in CI/demo with no key (014/007). The provider seam is what makes "the judge can run
locally" real rather than a slogan.

**How do you stop the judge from silently making things up?** It must return strict JSON
`{"score","reason"}`; a non-JSON reply is a *recorded error*, never a guessed score, and every score
is stamped with the judge model id + `RUBRIC_VERSION` so no score is anonymous (014/004).

**How is hallucination/error prevented AND measured?** Prevented by scoping the judge to
groundedness/correctness and letting deterministic checks carry the load (003). Measured by
`selfcheck`'s **calibration**: the judge runs over a human-labeled gold set and reports agreement
(EVAL.md, Decision 004/016). Run `make selfcheck` to produce it.

**What are the thresholds and how are they set?** Suite gate `suite_pass_rate = 0.85` (in the suite
JSON); judge calibration gate `0.90`; a judge score drop `≥ 0.05` (`REGRESSION_EPS`) counts as a
regression; groundedness decision boundary `0.5` (016/015). They're explicit, documented constants —
starting points to be tuned with a real provider and a larger gold set, not laws.

**What counts as a regression?** A case passes only if *all* its checks pass; a judge error counts
as a fail, never a silent pass (015). `compare` matches checks across runs by `(case, check, layer)`
and flags pass→fail or a notable score drop. A run is flagged if anything regressed or it fell below
threshold versus the baseline.

**Why SQLite + raw-first storage?** SQLite stores suites/runs/scores and makes run-to-run comparison
trivial with no infra (001). The runner persists *raw* output before scoring (012) so a run can be
re-scored after a rubric change without re-calling the target — and the regression story stays
auditable, not just a number.

**Honest read on the weak-looking metric.** The headline `make selfcheck` number — judge agreement
**0.92 (11/12)** — is from the **offline `mock` heuristic, not a calibrated judge**; the report says
so loudly and the verdict gate is *soft* for mock, *hard* for a real provider (016). The gold set is
**12 cases** — directional, not statistical, and it deliberately includes one subtle contradiction
(`cal-012`, 30-vs-60 days) a lexical judge misses, so the disagreement path stays visible. The
trustworthy number requires `MODEL_PROVIDER=anthropic` + a key, then `make selfcheck`. Hard gates
that *don't* depend on a provider — **rule fixtures 22/22** and **injected regression flagged YES
(citation 1.00→0.00, case pass 100%→33%)** — pass every run.

**Data/compliance posture?** Synthetic/public data only — fabricated identifiers (a fake SSN in the
PII probe) and public CFR section numbers; no PHI, no internal systems (007). The default target is
a bundled stand-in, and the judge can run fully offline (`mock`/`ollama`), so test data need not
leave the machine. Pointing at a real system or using sensitive data is out of scope and would need
a BAA / in-boundary model and de-identification (README "Path to production").

**Self-check:** for any decision a skeptic names, you give the rejected alternative, the real number
or command, and the caveat — without getting defensive.

---

### How to use this doc

Read it once end to end, then **drill the self-checks** — one per section. Close the file and do each
from a blank page: the 60-second pitch, the diagram, the rebuild loop, the swap-layer table, and a
cold defense of a decision picked at random. You own it when you can do all five without notes.

## Mastery checklist
```
- [ ] 1. Explain it in 60 seconds (domain + the anchor property), no notes
- [ ] 2. Draw the architecture from a blank board (stages, splits, external calls, guardrails)
- [ ] 3. Name the modules in build order, state each contract, write the happy path cold
- [ ] 4. List the swap-layer files, say what stays untouched and why
- [ ] 5. Defend any decision a skeptic names — alternative rejected + real numbers + caveats
```
