# Metric Review — Comprehensive Chronicle of Discoveries & Changes

Single source of truth for the metric-extraction / grounding / review effort. This is the **composite** — it
incorporates the content of the scattered docs (`enhancements.md`, `gate-issues.md`, `mispairing-eval-set.md`,
`grounding-repair-eval-set.md`, `side-by-side.md`, `metric-schema.md`, `low-value-reports.md`) so the discoveries
and changes live in one place, not seven. Those files remain the working detail; this is the record.

**Last updated:** 2026-06-10

## ▶ RESUME HERE (start here next session)

- **Where we are (2026-06-10):** full corpus **vision-verified** — 1,605/1,606 (Opus, boxed page renders, independent; DEV-ONLY, not a production gate). Definition + scorecard **LOCKED** (§1, §1a). `construct_metrics.py` **shelved** (M3) — the validated comprehensive prompt `b51dc96a` is canonical; harvest only its deterministic financial roll-up downstream.
- **Decisions locked:** ship = **grounding + is-a-metric + standalone**. **Tier = HELD** (keep the logic, accumulate data, do NOT publish until accurate & consistent). **Activity demoted tier-4 → reject.**
- **TRANSFER MANIFEST:** `research-pipeline-manifest.md` — the curated component list (init → end, incl. the doc gate) that production must carry; pairs with the REQ registry + suite.
- **HARDENED PROD OBLIGATIONS:** `production-requirements.md` (REQ-PROD-001..023 — confidence classes, RAG/vector schema, pgvector, render fix, config lock, verify stage). Printed as PENDING-PROD by every `test_suite.py` run; the convergence spec must adopt them as acceptance criteria.
- **Do next (queue, 2026-06-11):** (1) **P20 out-of-sample test running** — 600-doc stratified re-parse (config A in progress, ~63s/doc; B/C on the 120 subset after) → `regroup_p20.py` flags → Opus adjudication → flag precision + fix accuracy + bucket-E recovery + **Docling config lock → LOCKED: CONFIG B (pypdfium2), operator-confirmed 2026-06-11 (REQ-PROD-021 DONE)** (`locard/operations/p20-regroup-test/`). (2) **THEN Qwen residual eval** — Qwen2.5-VL-7B on g6.2xlarge against the dense-mosaic abstain class, plan in `p20-regroup-test/qwen-eval-plan.md` (needs operator g6 approval). End-state pipeline: Docling(locked config) → regroup layer (built-in) → extractor → gate+validity → dense residue → small-VLM queue.
- ~~Milestone M0/M1 (done):~~ M0 fixture+harness, M1 integrated pipeline — see §1b statuses.
- **STORY TRACK (2026-06-11, one day, full apparatus):** definition LOCKED (3 reviews: Codex+Gemini consults convergent + external; DP1-5 confirmed) → stage-1 prompt v1 (one page, marker spans) → 200-doc run (310 impact candidates) → span gate 91% → **100-story page-truth audit = story fixture** (verbatim 95%, conf instrumentation validated: 76% high vs 13% medium) → **stage-2 qualifier built+validated: precision 60%→87%, 94% retention** → applied to all candidates: **199 publishable impact stories** + routed classes (34 testimonial, 70 service_episode, 7 reject reclassed). PII reality: 44% named, 46% sensitive, 39% minors. All in `locard/operations/story-definition/`.
- **Then (NOT yet):** converge into production `lavandula/faithfulness/` — a *different, older* gate (R1/R2 snippet grounding, last touched 2026-05-31, wired into the live pipeline). Benchmark its grounding vs the vision fixture *before* deciding replace-vs-augment.

---

## 0. The pipeline (what exists)

1. **Extraction** — a model reads each report and selects ~5–15 comp-metrics (value + subject). Locked "virgin" prompt, sha `b51dc96a`.
2. **Grounding gate** (`gate.py`) — verifies each metric's value and subject are *real and located* at the cited Docling markers; decides `publish` / `quarantine`. It checks location, never whether the thing is well-formed or actually a metric.
3. **Review** — operator audits the gate's decisions in a web tool (`metric-review.html` + `review.db`), recording verdict / cause-stage / failure-mode per metric.

The baseline under review: a "clean-166" doc set → **1,606 metrics**.

---

## 1. The agreed definition of "metric" (settled 2026-06-08, operator's call)

The single most important decision, because everything keys off it:

- A metric is **a measurement of a quantity** — value + concrete subject + action/context enough to **stand alone** given mission + report year.
- **REFINED 2026-06-10 (operator's call) — is-a-metric vs tier are SEPARATE judgments:**
  - **is-a-metric = does it measure one of FOUR value classes — outcome/impact, reach/output, capacity, financial.** If it measures any of them it's a metric, *even if we can't say which*. This is the robust judgment we **gate and publish on**.
  - **Tier (which of the four) = HELD.** Logged, not published, until accurate & consistent. It's subjective — a knowledgeable reviewer agrees on metric-or-not but quibbles on tier (the design principle in the guidance doc).
- **Reject (not a metric) = the 8 non-measurements PLUS activity:** event-as-1 ("launched X"), rankings (#1, 3rd largest), awards/honors, dates/years, durations ("9-month program"), labels/ratings (4-star), unsupported multipliers ("doubled"), forecasts ("will rise 14% by 2045") — and **activity** (launched/hosted/opened), **demoted tier-4 → reject 2026-06-10** (lowest value, blurs into event-as-1; only ~0.4% of published). Service-delivery *volume* ("provided 800 sessions / 12,000 meals") stays **reach**, not activity.
- **Authoritative rubric:** `lavandula/docs/nonprofit_metric_extraction_guidance.md` (tiers, standalone requirement, rejection rules, schema, prompt guidance).

**Review fixes to fold into the construction** (from the guidance-doc review):
1. Rewrite-to-standalone may use **only facts present in the source** — never invent a subject/context (the highest-risk step).
2. **Financial is its own tier** (missing from the guidance §4 table).
3. **Capacity vs activity:** capacity = what *exists* ("16 centers"); activity = what was *done* ("opened 2 centers").
4. Define precedence so `validity` / `metric_tier=non_metric` / `standalone_quality=reject` can't contradict.
5. Keep **selection** as its own step; don't fold rewrite + classify into one prompt blindly (selection is the strong part).

---

## 1a. LOCKED scorecard (2026-06-10, vision-verified)

Full corpus vision-verified — **1,605 of 1,606** (Opus, boxed page renders, judged independently; DEV-ONLY, ~31M tokens, 3 batches incl. a 253-rerun of transient rate-limit failures). One denominator: **1,433 published**.

| Layer | Measured |
|---|---|
| **Mode 2 — is-a-metric** (measures one of the 4 value classes) | **91.4%** |
| **Mode 1 — grounding correct** (value on page + right subject) | 91.0% (fabricated 4.3%, mispaired 4.2%, other 0.6%) |
| Mode 1 — recall loss (real metrics wrongly quarantined) | 18.0% (31/172 of the quarantine pile; under the locked definition — one quarantined item is activity, now reject) |
| **SHIPPABLE** (grounded + is-a-metric + standalone) | **86.3%** |
| Standalone among shippable | 89% full · 11% partial · ~0% fail |
| Tier agreement vision-vs-code (**HELD**) | 88.2% |

Cross-check (rebuilds trust in the number): two independent methods agree on the non-metric rate — vision **7.7%** (over published) ≈ measurable-value classifier **7.5%** (over 1,606). Artifacts: `vision-corpus-merged.json` (merged verdicts + 104-item ship-error drop-list), `vision-sweep-*-result.json`. **This scorecard is the single source of truth — do not re-derive it inconsistently across different denominators.**

---

## 1b. Research-completion plan (milestones, 2026-06-10)

No full SPIDER spec — the logic is designed and vision-validated; the remaining risk is **integration/regression**, which tests catch, not specs. Lightweight: this plan + TICK-style execution, each milestone **measured against the §1a fixture**.

**Ship path = grounding + is-a-metric + standalone. Tier = held background track (not a ship gate).**

- **M0 — Freeze fixture + scoring harness.** `vision-corpus-merged.json` → frozen regression fixture; harness reproduces the §1a numbers. *Exit: baselines reproduced.*
- **M1 — Assemble the integrated run** (render → select [`b51dc96a`] → ground → gate [hardened] → is-a-metric [≤20 classifier] → twin-repair), tier **logged-not-published**. *Exit (corrected): one command scores ≥ §1a with expected deltas, 0 skips.*
  - **M1a DONE 2026-06-10** (`pipeline.py`): chain replayed over the corpus → published **1,433 → 1,375**; **shippable 86.3% → 90.1%**, is-a-metric → 95.4%, grounding → 93.5%, recall loss 18.0% → 12.2% (9 twin-repaired); 0 skips.
  - **M1b DONE 2026-06-10 — alignment rewrite REJECTED (validated against the fixture).** The rewritten prompt (`align_classifier.py`) over-rejected **percentages** (small-int stage-1 catches values ≤20, incl. `11%`/`12%`/`20%`): agreement with vision **89% → 79%**, false-rejects (real metrics dropped) **8 → 39** (33 newly-wrong, e.g. "staff retention increased 11%", "served the seven communities"). The OLD classifier is already **~89%-aligned** with the locked definition, so it is **RETAINED** — M1a's 90.1% stands. Negative result preserved (`align_classifier.py`, `measurable-value-quarantine-aligned.json`). **Lesson: validate-before-wire — the M0 harness caught it.** Accepted residual: old classifier wrongly drops 2 capacity ratios (`e43d7482:6`, `f4819890:8`); a surgical %-exclusion from stage-1 is the only safe tweak, low-value, deferred.
  - **M1c DONE 2026-06-10** (`run_doc.py`): live per-doc wrapper — render → SELECT [`b51dc96a`] → GROUND → `decide()` chain (hardened gate + live is-a-metric for small ints + twin-repair). Smoke-tested end-to-end on `752d2b78` → 10 metrics, 10 publish, clean standalone output (small-int %s correctly kept). **M1 COMPLETE.**
  - **PROMOTED 2026-06-10:** M1 decisions applied to the canonical set (`review-data.json`): 68 publish→quarantine, 11 quarantine→publish → **1,376 publish**. Snapshot `review-data.before-m1-promote.json`. **Process rule going forward: a validated fix is APPLIED to the set as part of done — no computed-but-not-applied state.**
  - **ITEM 3 CLOSED 2026-06-11 — accepted residual, no rule.** The 5 small-int slips (`d8f908ed:1`, `35494eab:9`, `36941d40:8`, `4ec0d342:2`, `1a70e980:0`) are **coincidence-grounded fabrications**: vision says the value is NOT on the page, but a stray standalone digit genuinely exists in the parsed text at the cited marker — undetectable by any text rule, visible only to page-truth. They belong to the wider **fabrication residue: 22 of 1,322 published (1.7%)** (5 small + 17 larger), owned by (a) the Docling config lock (B/C test running) and (b) the production vision-verify stage (the two-stage architecture validated by the P20 test). Do not build a text rule for these.
  - **ITEM 4 DONE 2026-06-11** (in `pipeline.py decide()`): **4a neighbor re-anchor** (value+subject both ground at the ±1 same-prefix marker; small-int + year-range excluded) — fixture-validated **2/2 vision-real recoveries** (`015893d4:0`, `e136ede4:4`), applied → set **1,323 publish / 283 quarantine**. **4b rounded-value REJECTED** (validated, regressed): "more than 1,300"≈1,324 matched a look-alike number from a DIFFERENT stat (`81265722:0`, ISSUE-003's near-duplicate trap) — 50% precision → the gate's strictness is correct; kept as documented negative result. **M4 suite extended + GREEN** (8 checks incl. item4-recovery).
  - **M4 DONE 2026-06-11** (`test_suite.py`): locked regression suite = the production-port acceptance contract. First run caught a latent item-1 year-as-value stage-1 regex bug ("in March 2017" / sentence-final "2017."); fixed via bare-token year matching (comma=count, bare=year) → 6 more quarantined, 0 false rejects.
  - **ITEM 2 (mispairing) BUILT + APPLIED 2026-06-10/11** (`regroup.py` + `validate_regroup.py`): **coordinate re-grouping** — the per-element bboxes were in `lava_parse.sections.source_locations` all along; pair each bare number with its caption (right-same-row / below-same-col / short-header-above), **mutual-nearest** both ways, abstain otherwise. Caption filters: digits, qualifier phrases ("of which"), finite verbs (sentences ≠ captions), financial line items. **No detection step needed** — engages from each page's own geometry (1,245/1,408 metrics untouched = prose no-op). Iterated v1→v6 against the fixture + a 13-case Opus page-truth adjudication (`regroup-adjudication-result.json`; 2 "regressions" were BOTH_SAME artifacts, 4 "misses" were regroup-right-model-wrong): final **1% regression / ~16 high-conf catches / Capstone 7/7**. **Deployed FLAG-MODE** (disagree → quarantine `mispairing_geometry`, never silent relabel, per "incomplete not wrong"): **15 publish→quarantine applied** → set **1,327 publish / 279 quarantine**; scorecard: **shippable 92.7%, grounded 94.6%**, mispaired-left 44 (regroup abstains on dense mosaics — vision-queue residual). Geometry spike history: naive nearest-centroid 22% precision → the failure was the METHOD, not the data.
  - **ITEM 1 DONE 2026-06-10** (`item1_nonmetric_rules.py`): rules for the remaining non-metric classes — tenure/duration, year-as-value, forecast, population factoid (factoids ruled NOT-a-metric per the guidance doc's core definition, operator-confirmed; doc outranks lenient vision on definition edges). Two-stage (regex → value-aware DeepSeek), fixture-validated with operator adjudication of 8 disagreements (2 judge errors kept: `a00adaa5:0` achieved-goal, `b60a49eb:8` real count). **34 publish→quarantine applied** → set **1,342 publish / 264 quarantine**; scorecard vs fixture: shippable **91.7%**, is-a-metric **97.1%**, grounded 93.6%. Snapshot `review-data.before-item1.json`.
- **M2 — Render single-namespace fix** + re-parse a sample. *Exit: prefix-swap class → 0, no new grounding regressions.*
- **M3 — RESOLVED:** `construct_metrics.py` shelved; comprehensive prompt `b51dc96a` canonical; harvest only `roll_up_financials` downstream.
- **M4 — Lock per-step regression tests.** Bars: grounding precision ≥91%, is-a-metric ≥91% (fab catch ≥88% / 0 deterministic loss), twin-repair ≥N recovered / 0 false, standalone Y+P ≥99%. **No tier ship bar (held).** *Exit: green suite = research done + acceptance contract for the production port.*
- **Tier track (background, parallel):** adjudicate the 3 reach-boundary rulings (`tier-disagreements.html`), accumulate labels, measure drift; publish only when accurate & consistent.

**"Bow" = M4 green + M3 resolved** → research is the validated, tested reference. *Then* production convergence (benchmark `lavandula/faithfulness/` R1/R2 grounding vs the §1a fixture first, to decide replace-vs-augment).

---

## 2. Gate / grounding fixes (BUILT)

- **Hyphen fix in `candidate_numbers`** — the parser couldn't read hyphenated scale-words ("$2.3-million"), so it missed the value at the cited marker and false-quarantined. Now recovers `$X-million`. Directly recovered `db75f054:7` ($2.3M fiber) and `8e1cf08b:0` ($9M renovation).
- **`column_guard` removed** — it over-flagged 56 benign financials (almost all on excluded compilation reports) and caught zero confirmed real errors; the table column-mispairing residual is handled by geometry (ENH-003), not this guard.

---

## 3. Recall losses — false-quarantines (real metrics the gate held)

### 3a. Operator-flagged gate issues (`gate-issues.md`) — collected before deciding any change
- **ISSUE-001 (`56688514`)** — derived/aggregate value. Model wrote value=**114** for "64 meetings … and 50 visits"; **114 = 64+50** appears nowhere in the doc. Gate correctly quarantined (the value is model-synthesized). Also cited the wrong neighboring marker.
- **ISSUE-002 (`db75f054`)** — value real, wrong marker. "$2.3-million fiber project … 68 miles" is real; grounding cited a *different* sentence (the repairs passage). Recovered by the hyphen fix (the value IS at the cited marker `t20`, just unparsed before).
- **ISSUE-003 (`81265722:0`)** — **right passage, wrong slot.** Value 1,300 is verbatim on p5 (`t20`); the model pointed `value_ref` at a look-alike on p12 (`t110`, "1,324"), and `subject_ref` at the real p5 sentence. Gate checked p12 (1,324 ≠ 1,300) → quarantine. Value/subject marker mis-assignment, made easy by near-duplicate numbers.

### 3b. Off-by-one-neighbor (`grounding-repair-eval-set.md`)
- `015893d4:2` (14 advisory board members) and `:3` (4 meetings) — both live in the same p2 sentence (`t2`); grounding pinned both to `t1` (the intro above). One ±1 neighbor re-anchor recovers both.
- Candidate rule (not built): if value isn't at the cited marker, check ±1 neighbors for value+subject; re-anchor. Local + unambiguous, beats a whole-doc value search.

### 3c. ENH-007 — Marker prefix-swap (ROOT-CAUSED + REPAIR BUILT, the dominant recall loss)
- Re-gated all **125 `value_not_at_marker` quarantines** with the current parser: **2 recovered** (the hyphen cases), 57 reground-strong, 4 reground-distinct, 34 reground-weak, 28 derived. The raw "reground" is coincidence-inflated (36 of 57 have value <20 — event/award `1`s matching by chance).
- Spot-verify of the 25 distinctive candidates (value ≥20): **16 genuinely recoverable, 9 the gate working** (derived/summed `370cd021:2` 600k−100k=500k, `7e9ebeba:2` 19+6=25; year-as-value `d58e3d5b:12`=2021; abstract `ebaed191:8` "tens of thousands"=10000).
- **Root cause (confirmed):** the model writes the **wrong marker prefix, right index** — cites `c91` (cell) when it means `t91` (text). The cited ID often doesn't even exist (9 of 125); the same-index opposite-prefix twin grounds the value. 11 of 125: `c09c96c2:0`, `015893d4:3`, `d58e3d5b:0/:1/:2/:3/:4/:5/:9/:10`, `128de607:0`. **Why:** `render_tagged` used two counters (`t`, `c`) with overlapping ranges, so `⟨t91⟩` and `⟨c91⟩` look interchangeable.
- **Twin-repair built** (`build_twin_repair.py`): re-grounds to the twin, re-runs the gate as its own guard. **10 publish** (9 solid `d58e3d5b`×8 + `128de607:0`; 1 marginal `015893d4:3`). 1 correctly held (`c09c96c2:0` val=2, twin matched by coincidence, gate's subject check rejected). Marked `false-quarantine / grounding / prefix-swap`.
- **Render prevention drafted:** single `m##` ID namespace so the prefix can't be swapped. Grep-confirmed safe (nothing infers kind from the prefix; the `kind` field does). Not applied — needs re-extraction; bake in before the national run; keep twin-repair as the residual net.
- **Verdict on the gate:** it is **NOT leaking real metrics at scale** — most `value_not_at_marker` quarantines are correct; the recoverable share is ~16–20 of 125 (~2%), concentrated in the prefix-swap.

---

## 4. Spatial mispairings — false-publishes (ENH-003 + `mispairing-eval-set.md`)

The dangerous false-publish class: a number paired to the **wrong label** on a designed page. The gate is structurally blind — both the value and the label are on the page, so every text check passes; the error is purely spatial.

**6 confirmed mispairings (vision-verified):**

| metric | doc / page | value | model labeled it | truth (vision) |
|---|---|---|---|---|
| `5b98b66f:4` | Capstone p2 | 967 | Head Start children | housing counseling |
| `5b98b66f:5` | Capstone p2 | 353 | entrepreneurs supported | Head Start |
| `5b98b66f:6` | Capstone p2 | 369 | housing counseling | financial literacy |
| `5b98b66f:9` | Capstone p2 | 2,971 | financial participants | heating assistance |
| `68e0e142:1` | Narrow Gate p9 | 95 | identity confidence | purpose in life |
| `2cf7c1e8:4` | Forward Stride p1 | 283 | clients via partnerships | Equine Assisted Learning (partnerships=155) |

- **Method (ENH-003):** re-derive number↔label pairing from **bboxes** (which preserve the 2-D layout the reading-order flattening destroyed). Geometry-vs-model disagreement = mispairing flag; geometry's pairing = the fix. Vision-free, scales to 300k.
- **Validated:** geometry caught **6/6**, corrected **6/6**. **1 false-flag** (`5b98b66f:7`, 32=CKA graduates — naive nearest-centroid grabbed a side-column label; fix = "label directly below, in-column").
- **Decision:** on infographic pages, **geometry IS the source of truth** — re-pair and publish (a bbox-adjacency fact is *more* provable than the model's flattened-text guess). Gated on a measured precision vs the <1% bar before auto-publish.
- **ROOT-CAUSE NOTE (corrected 2026-06-08):** mispairings come from **Docling 2-D→1-D layout flattening**, NOT from the model composing a sentence. (Earlier I wrongly lumped them with paraphrase-drift.) The fix is geometry re-pairing, not constraining the rewrite.

---

## 5. Quality checks — the two-stage harness (ENH-006, BUILT)

Reusable engine: cheap regex pre-filter over every metric → tiny candidate set → DeepSeek qualifies only the candidates. Cost scales with the ~1% sliver. A "check" = (stage-1 regex, stage-2 question).

- **Plug-in #1 — loose-OR-composite — SUPERSEDED / MIS-AIMED.** It asked "is the source a loose or-list?" — but a source or-list is faithful most of the time. Of its 6 "confirmed," **5 were actually faithful** (`d5cb6d0c:7/:8`, `68e0e142:3`, `93e81f02:9`, `2ec17b37:2` — the model kept the report's "or" verbatim) and were restored to published. Lesson: flag what the **model** did wrong, not what the source's wording looks like.
- **Plug-in #2 — measurable-value (BUILT)** (`measurable_value_check.py`) — does the number measure a real quantity? Stage 1 = small integers (≤20); Stage 2 = MEASURE/NOT. Keeps real small counts (16 centers, 9 vans, 6 counties); rejects event-1s, multipliers, identifiers. **Result: 121/1606 NOT = 7.5% non-metric (a FLOOR — see §6).**
- **Plug-in #3 — paraphrase-drift (BUILT)** (`paraphrase_drift_check.py`) — replacement for loose-OR. Does the model's STATEMENT change the SOURCE's meaning? The real defect: a source disjunction ("A, B, OR C" — any) rewritten as a conjunction ("A AND B" — all). **Precision lesson:** v1 over-fired (13/94, 12 false positives) on *incidental* "or"s elsewhere in the source ("deficit or loss", "at or below"); tightened to judge only the metric's own claim → **1 of 1,606 (`d5cb6d0c:6`), 0 false positives.** Paraphrase distortion is genuinely rare. Calibration on clean cases (5/5) did NOT survive contact with real sources — always run the full set and verify before trusting precision.

---

## 6. Non-metrics — the big quality question (definition fight + reframing)

- **measurable-value applied to the review:** of the 121 flagged, 56 were already gate-quarantined; of the 65 still published, **spot-verify by hand:** 57 are confirmed non-metrics → marked `false-publish / not-a-metric`; **8 the check got WRONG** and were held (it over-flags reach-counts and ratios — `5b8eae73:4` "15% sales", `d58e3d5b:11` "2 counties", `12a4557a:4` "4 catchment areas", `3e3ff326:10` "3 programs", the `e43d7482:6` 4:1 / `f4819890:8` 14:1 ratios, `81265722:9` 4 partners, `0793289a:2` 3 in Top-10).
- **The 7.5% is a FLOOR, not the number.** The check only inspects small integers, so it structurally misses non-metrics carrying a larger spurious value; and it over-flags real low-tier metrics. The true non-metric rate **has not been measured against the agreed definition.** At ~186K docs this is material (six figures of entries) — so it is NOT "handled," and the measurable-value filter is not yet wired or trustworthy as a live filter.
- **Tally reframe (review tool):** marking 57 non-metrics as `false-publish` spiked the displayed publish-error from 3.2% → 21.5% by blending two different things. Split the tally: **`err%` = wrong-data only = 2.6% (8/302)**; **`not-a-metric`** is its own line. The wrong-data rate never moved; the 57 are the known non-metric bucket surfacing.
- **Prompt change is back on the table.** The earlier "no prompt change, handle downstream" call was explicitly conditional ("unless downstream proves inadequate at 300k"). It proved inadequate → building the construction at the source is justified.

---

## 7. Selection-quality, parse-quality, rendering (queued ENHs)

- **ENH-005 — standalone-ness / metric-validity** — a metric can ground perfectly and still not be well-formed (bare-abstraction subject: "40% increase in *client engagement*" — engaged in what?; or a framing sentence: `81265722:8` "The Strategy is aligned with…"). Cheap word-list pre-filter + LLM micro-check. Folds into the construction.
- **ENH-004 — garble detector (upstream)** — `b9acbd60` parsed entirely as `/gid00001/...` (font glyph-ID leakage) on a clean PDF; the model then **hallucinated** round numbers ("1,000,000 across 50 communities") and the gate quarantined them. ~0.3% class. Detect by fraction of `/gidNNNNN` / PUA glyphs → re-OCR or exclude. A third corpus-inclusion reject reason after financial/compilation and plain-text.
- **ENH-002 — Docling bbox precision weak on designed pages** (offset ~1–2 lines on infographic callouts; pixel-perfect on clean tables/prose). Matters most for a **public-facing highlight** feature. Logged; no fix yet.
- **ENH-001 — boxed-review rendering** — when value & subject share a marker, the blue subject box paints over the red value box → "no box" appearance. Draw one purple "value+subject same passage" box instead.

---

## 8. Prompt experiment — virgin vs treatment (`side-by-side.md`)

Compared the locked "virgin" prompt to a "treatment" variant (stable = present in ≥2/3 runs; kept / dropped / added). Pattern across ~10 docs: **treatment pulls more granular financial line items** (total assets, cash, liabilities, reserves, per-program expenses) and occasionally **drops** narrative/context metrics. Confirms a prompt change shifts *what* gets selected — relevant input to the §6 prompt-vs-downstream decision (more financial granularity is mostly low-tier noise, exactly what `construct_metrics.py` also over-produced in §9).

---

## 9. The new construction (IMPLEMENTED 2026-06-08)

`construct_metrics.py` — implements the guidance-doc rubric + the §1 review fixes (rewrite limited to source facts; financial as its own tier; capacity-vs-activity; explicit auditable rejections) via DeepSeek. Doc text in → tiered standalone metric records out (per the `metric-schema.md` shape).

- **Ran on doc 1 (Dreams With Wings, `011c1177`):** 18 records; recovered what the old pipeline dropped (financial lines, total expenses, the van). **But a poor test doc** — finance-dense, impact-thin; it only exercised the financial side. Develop on impact-rich docs instead (see RESUME HERE).
- **Financial roll-up MOVED TO CODE** (`roll_up_financials`): the prompt couldn't suppress line items (it kept them, hedged as `borderline`), so a deterministic post-filter keeps top-line totals + narrative/event amounts and drops statement line items/balances. Doc 1: 18 → 8. Caveat: regex heuristic, one-doc validation only; consider a model financial-sub-kind tag for robustness.
- **Discard-rate insight:** dropping >50% of output is a symptom, not a fix — on doc 1 it reflects a finance-dense/impact-thin report and shows "extract-everything-then-dump" is the wrong shape. Healthier: tier-don't-drop (let display filter by tier, per the guidance doc) or keep the financial statement out of the narrative extractor. **Open operator design call.**
- **Rejections still not emitted** — the prompt won't reliably force `non_metric` records (even the in-text "September 2024" date wasn't flagged); some rubric-rejects (logo "25 years", "best year ever") aren't in the parsed text at all. Needs a separate pass / code, not more prompt wording.

---

## 10. Schema (`metric-schema.md`)

Canonical record: identity (metric_id, content_sha256, ein, year, url) · the metric (statement, subject, value, value_text, unit, direction, temporal, geo) · **classification tags** (`logic_tier` input/output/outcome/impact, `metric_type`, `measurable`) populated post-extraction · **provenance** (page, value_ref/subject_ref, bboxes, charspan, `paired_by` model|geometry, source_snippet) · **verification** (gate, grounded, review, cause_stage, failure_mode) · extraction audit (model, prompt_sha). The moat is the structured, bbox-backed provenance.

---

## 11. Corpus inclusion / low-value reports (`low-value-reports.md`)

Curation gate (not an extraction defect) — exclude reports that extract fine but shouldn't be in the corpus:
- **Flavor A — plain-text report** (`4afba9fd`): typed, undesigned, no imagery.
- **Flavor B — single-program/administrative** (`ee33d0e3`): a narrow grant compliance "final report," not a broad org annual report.
- **Flavor C — narrative/event report** (`12a4557a` legislative 12/13, `30259c4d` church 5/6): counts events (a bill passed = 1) not quantities; cheaply detected by a high non-metric fraction.

---

## 12. Key numbers

| Metric | Value |
|---|---|
| Baseline | 1,606 metrics / clean-166 docs |
| Wrong-data publish error (reviewed) | **2.6%** (8/302) |
| Non-metric rate (measurable-value) | **7.5% — a floor**, not measured vs the agreed definition |
| Paraphrase distortion | 1/1,606 (0.06%) |
| Confirmed spatial mispairings | 6/6 caught & fixed by geometry; 1 false-flag |
| Prefix-swap recall loss | 11/125 `value_not_at_marker`; twin-repair recovered 10 |
| New construction on doc 1 | 18 tiered metrics; +2 recall vs old; flaws noted |

---

## 13. Open items / next

- [ ] **NEXT:** run `construct_metrics.py` on `779d9438` (impact-rich) + **vision hand-review** the output (tier / standalone / rejections / faithfulness); then widen to more impact-rich docs. NOT the finance-heavy first pick (`011c1177`).
- [ ] **Operator design call:** how are financials handled — tier-don't-drop, or keep the financial statement out of the extractor? Is a P&L sub-line a metric? (Surfaced by the >50% discard on doc 1.)
- [ ] Make the construction emit `non_metric` rejections (separate pass / code — prompt pleas failed).
- [ ] Validate the roll-up heuristic across the batch, or move it to a model financial-sub-kind tag.
- [ ] **Measure the TRUE non-metric rate** against the agreed definition — the 7.5% is a floor and the check over-flags.
- [ ] Tighten measurable-value (stop over-flagging reach-counts/ratios) before wiring as a live filter.
- [ ] Operator decision on the **8 held** measurable-value metrics.
- [ ] Geometry (ENH-003): tighten "label directly below, in-column" to clear `5b98b66f:7`; grow the mispairing set to a real precision number; wire as pre-gate re-pair.
- [ ] Apply the `render` single-namespace ID fix before the national re-extraction.
- [ ] Decide prompt-vs-downstream for non-metrics using the construction results + the §8 experiment.

---

## 14. File map

| What | Where |
|---|---|
| Target rubric | `lavandula/docs/nonprofit_metric_extraction_guidance.md` |
| New construction | `construct_metrics.py` |
| Gate | `gate.py` · render `render.py` |
| Checks | `measurable_value_check.py`, `paraphrase_drift_check.py`, (superseded `loose_or_check.py`) |
| Recovery | `build_twin_repair.py`, `regate_value_quarantines.py`, `spot_verify_reground.py` |
| Schema | `metric-schema.md` |
| Eval sets / issues | `mispairing-eval-set.md`, `grounding-repair-eval-set.md`, `gate-issues.md`, `low-value-reports.md` |
| Prompt experiment | `side-by-side.md` |
| Enhancement backlog (full detail) | `enhancements.md` (ENH-001..007) |
| Review tool + verdicts | `metric-review.html`, `review.db` |

---

## 15. How we're working (process)

- **Show the artifact, not a summary of it** — claims come with the command/file/number behind them; the operator verifies the result, not my gloss.
- **Surface methodology before spending compute** — sample, thresholds, model, source — get a yes; no silent parameters.
- **The metric definition is the operator's call** (recorded in §1); I don't tune it to flatter the numbers.
- **Checks over-fire at scale** — every check this session needed full-run verification before its precision could be trusted; calibration on a handful does not transfer.
