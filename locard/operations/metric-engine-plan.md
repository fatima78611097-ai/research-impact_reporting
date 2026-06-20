# Metric engine — implementation plan (2026-06-20)

Not a spec. A structured plan + approach for building the metric pipeline the way we agreed:
**one core, two thin I/O adapters, model-text not construction, every check written once and
measured, single source of truth with branch→merge discipline.**

---

## 1. The target shape (one core, two harnesses)

```
   INPUT adapter            CORE  (one source of truth)              OUTPUT adapter
 ┌──────────────┐   ┌──────────────────────────────────────┐   ┌────────────────────┐
 │ dev: sample  │ → │ extract → normalize → gate(checks)    │ → │ dev:  JSON + viewer │
 │ prod: corpus │ → │   (model's text kept; checks flag/    │ → │ prod: RDS commit    │
 └──────────────┘   │    quarantine; NO rewriting)          │   └────────────────────┘
                    └──────────────────────────────────────┘
                       ▲ new checks/methods land HERE, once
```

**The rule that keeps this honest:** adapters do **I/O only — zero logic.** Every decision (extract,
normalize, gate) lives in the core. The moment an adapter filters or grades, we've re-forked. Dev and
prod must differ **only** at the two ends.

**Proposed home:** a clean `lavandula/metrics/` package — *not* the `nlp/` grab-bag (which also holds the
deprecated spaCy term path). This physically separates the metric engine and makes a second gate harder
to create than to avoid.

```
lavandula/metrics/
  core/
    types.py         # the normalized Metric record — THE contract both adapters must produce
    extract.py       # prompt + DeepSeek -> raw metrics   (lifted from nlp/llm_extract)
    gate.py          # the check registry + run order -> decisions + flags
    checks/          # one pure, testable function per check (the whole gate lives here)
  adapters/
    dev_input.py     prod_input.py      # pool/corpus -> Metric records
    dev_output.py    prod_output.py     # decisions -> JSON+viewer / RDS
  harness/
    run_dev.py       run_prod.py        # wire input -> core -> output. Nothing else.
  tests/fixtures/    # known-bad / known-good per check
  viewer/review.html
```

---

## 2. The core contract (lock this first)

One normalized `Metric` record, derived from `comp-metric-regression/metric-schema.md`, that **both**
adapters emit and the core consumes. Minimum fields the checks need:

`statement` (model text) · `value` · `unit` · `tier` · `source_snippet` · `value_ref` · `subject_ref` ·
`page` · `v_bbox` · `s_bbox` · `v_text` · `s_text` · `same_marker` · provenance (sha8, page).

The core never rewrites `statement`. It only attaches: `decision` (publish/quarantine), `reason`, and
non-blocking `flags` (e.g. `incomplete`, `mispair_suspect`).

---

## 3. The gate = a registry of small checks (the heart of the work)

Each check is one pure function `check(metric) -> (verdict, reason)`, written **once**, with a fixture of
known-bad/known-good and a measured recall + false-positive rate. The metric **definition document**
(`nonprofit_metric_extraction_guidance_theory_of_change_aligned.md`) is the spec for these — each clause
becomes one check.

| check | what it rejects/flags | tier | status |
|---|---|---|---|
| `dedup` | near-duplicate metrics | rule | **port** from `slot_render.dedup` |
| `quality_text` | tenure/anniversary, financial-statement fragments | rule | **port** from `slot_render.metric_quality` |
| `quality_subject` | gratitude/thank-you, empty, contentless header (NOT label-length) | rule | **port** from `slot_render.subject_quality` (drop length rules) |
| `incompleteness` | bare generic people-noun, no action ("838 individuals") | rule (spaCy) | **port** from `incomplete_gate.py` |
| `is_a_metric` | dates, durations, rankings, forecasts, awards | rule | **build** (doc reject list) |
| `vague_quantity` | "thousands/many" rendered as a precise number | rule | **build** (new — found this session) |
| `measurable` | bare-1 / event-as-count, unverifiable small ints | rule + signal | **port concept** from `measure_check.py` |
| `mispairing` | value & label physically far apart on the page | geometry (bbox) | **port** `mispairing_check.geometry_pairing_check` as a **flag** |
| *(later)* `drift` / `subtle-incompleteness` | meaning changed vs source; dropped timeframe/recipient | model-judge | **defer** — semantic tier, hardest, build last |

Cheapest tier first; a model-judge only for the semantic residue rules can't see. Each check flags with a
reason — review decides thresholds.

**Out of scope (separate track):** vision **recovery** of trapped infographic numbers (the reader). The
gate *detects/flags*; it does not recover. Recovery is its own effort, not part of this plan.

---

## 4. Phased build (each phase runnable + verifiable)

**Phase 0 — Decisions + scaffold.** Lock the `Metric` contract (§2). Create `lavandula/metrics/` skeleton.
Decide the dev sample pool (recommend: keep the 25 NTEE-P docs as the standing fixture, add a few
infographic-heavy + financial-only docs for coverage). *Deliverable:* empty package + contract + this plan.

**Phase 1 — Dev harness around the core.** Lift extraction into `core/extract.py`; build `dev_input`
(sample pool) + `dev_output` (JSON + viewer); `run_dev.py` wires them. *Deliverable:* reproduce today's
model-text run through the clean architecture. *Verify:* output matches the current `model-review-data.json`.

**Phase 2 — Consolidate the gate (the real metric-quality work).** Port the checks we have into
`core/checks/`, each with a fixture + measured numbers; build the missing ones (`is_a_metric`,
`vague_quantity`, `measurable`, `mispairing`-flag). Retire `slot_render` gating. *Deliverable:* one check
registry producing the reviewable gated output. *Verify:* every check passes its known-bad/known-good
fixture; no check over-flags the known-good set.

**Phase 3a — Prod harness (adapters) around the SAME core.** Build `prod_input` (corpus query: parsed
docs not yet metric-processed) + `prod_output` (RDS write), reusing the Spec 0069 runner's plumbing
(advisory lock, atomic/idempotent writes, per-run report) — the *body* we keep, with its
construction-specific *logic* dropped. *Verify:* a dev run and a prod **dry-run** (`write=False`) over the
same docs produce **identical decisions** — proof the core is shared and the adapters carry no logic.

**Phase 3b — Bake prod into the orchestrator** (the close-out of the "metric extraction is hand-run" gap).
Register the prod harness as orchestrated stage(s), **after `parse`**:
- **Candidate stages:** `extract-metrics` (core extract over parsed docs → `lava_vocab.llm_metrics`) and
  `gate-metrics` (core gate → decision columns) — or one combined `metrics` stage. *Recommend two* (finer
  re-run control: re-gate without re-extracting).
- **Wire:** add to `STAGE_REGISTRY` (`dashboard/pipeline/stages.py`) + the orchestrator `COMMAND_MAP`
  (`dashboard/pipeline/orchestrator.py`); add a `/queue` + status route to the dashboard (the `llm-extract`
  UI partly exists — formalize it into a registry stage).
- **Result:** metric production runs as a normal job — queued from the panel, under the same
  locking/progress/retry as crawl/classify/parse, terminating in RDS.
- *Timing:* only after the checks are stable (Phase 2 done) — don't orchestrate a gate you're still tuning,
  or you re-run the corpus on every check change.
- *Deliverable:* metric extraction is **wired into job control**, not hand-run — the gap the catalog flagged is closed.

**Phase 4 — Discipline made standing + archive v1.** Test suite on the core (all fixtures). Formalize the
duplication/consistency audit (`duplication-audit.md` method) as a repeatable script. Snapshot-tag and
**archive the v1 gate** (`slot_render` gating + the construction-specific 0069 pieces: `render_and_grade`,
`column_mispair`). Update the pipeline map. *Deliverable:* the fork is gone; one source of truth remains.

---

## 4b. How a change flows once both paths exist (the everyday loop)

Adding a new gate type (or changing any check) is **one path, not two** — this is the precision that keeps
the fork from coming back:

1. **Branch.** Add/edit the check in `core/checks/` — **one place.**
2. **Test via the DEV harness:** run on the sample pool → JSON → viewer. Add the check's known-bad /
   known-good fixture; confirm it catches the bad and doesn't over-flag the good.
3. **Merge** when satisfied.
4. **The PROD harness inherits it automatically** — it calls the same core. The only "update to prod" is
   **re-running the orchestrated stage**; you never re-implement the check on the prod side.

You touch the prod path *itself* only when the change is to a prod **adapter** (I/O — e.g. a new RDS
column), **never** for a gate/check. A gate type is *core*, so it's written once and both paths get it.
That is the whole reason for the one-core design: "test on research, then update prod" must mean *merge the
core and re-run prod* — not *edit a second copy of the gate.* The moment it means the second thing, we're
back to two gates.

---

## 5. Discipline guardrails (because no human reviews the code)

This project has no human code review — drift only surfaces when probed. So the guardrails are structural,
not "be careful":

1. **One core, I/O-only adapters.** A fork must be harder to make than to avoid.
2. **Every check written once, in the core.** New method = edit the core (on a branch), never a second copy.
3. **Every check has a fixture + a measured number** (recall on known-bad, false-positives on known-good).
   A check isn't trusted until it has them.
4. **Branch → validate via dev harness → merge.** Single source of truth in version control; prod picks it
   up on the next run. No standalone scripts that become permanent.
5. **Standing duplication audit**, not just the dead-code sweep — run deliberately, the way we just did.
6. **Docs stay honest** — "complete/clean" only with the check that proves it.

---

## 6. Decisions (LOCKED 2026-06-20)

- **Dev sample pool:** the **25 NTEE-P docs + ~8 for coverage** (~5 infographic-heavy, ~3 financial-only).
  This is the standing fixture every dev run is reviewed against.
- **v1 disposition: archive + start over.** Snapshot-tag the Spec 0069 gate and remove it from the tree;
  rebuild `prod_output` fresh against the new `Metric` contract. Keep 0069 **tagged as a reference**
  (read, do **not** import) for the concurrency/idempotency patterns — advisory lock, active-run guard,
  atomic upsert, per-run report — because that's the easy-to-get-wrong part. The plumbing is welded to the
  construction data model (consumes a 0068 marker run, re-renders via markers), so it can't be cleanly
  lifted; we re-implement the *patterns* against the new contract. **Port its two real checks** —
  `gate.py` (grounding) and `measure_check.py` (measurable / is-a-metric) — into `core/checks/`.
- **Sequencing: finish Phase 2 checks before Phase 3 (prod wiring).** The checks are the product; prod is
  plumbing — don't orchestrate a gate still being tuned.

## 6b. Deferred decisions (come due at Phase 2 — NOT blocking Phase 0)

- **Two co-occurring numbers — one metric or "multiple"?** e.g. *"Thirteen households received a total of
  $577,000."* Sets the `is_a_metric` / multiple-metric check threshold.
- **Is a percent-change a valid metric?** e.g. *"34% increase from the previous year."* Same check.
- ~~**Extraction model**~~ **RESOLVED — `deepseek-chat`** (operator-confirmed 2026-06-20; matches the
  pinned `_MODEL` in code). The "v4-flash" note was wrong.

*Vision models are NOT a decision for this plan* — the gate is text-only detection. They belong to the
out-of-scope **recovery** track: the local options are VL2 and Qwen (Qwen the better of the two), and both
are currently displaced by Gemini Flash-Lite. Which vision model to use is a recovery-track call, made when
that track starts — and note the operator's standing reservation that Flash-Lite is weak as a judge.
