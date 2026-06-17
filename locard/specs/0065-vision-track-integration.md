# Spec 0065: Vision Track Integration — VL2 Designed-Page Metric Extraction

- **Status:** conceived (draft)
- **Priority:** high
- **Author:** Architect
- **Created:** 2026-06-03
- **Dependencies:** 0051 (LLM Impact Extraction), 0057 (Faithfulness Verification), 0060 (Parse Fidelity / pdftotext)
- **Related:** 0052 (Extraction QA Viewer — proof-image overlap), 0063 (Multi-Source Union Grounding), 0058 (poison-cohort signals), 0064 (Docling parse optimization — sibling recovery path)
- **Tags:** layer-2, vision, extraction, vl2, gpu, designed-pages, faithfulness, deepseek

---

## 1. Problem Statement

Our flat-text extraction pipeline (0051 → 0057 gate) mislabels or drops the **highest-value numbers in the corpus** — the hero stats on designed "infographic" pages, where a number and its label are paired by 2D layout (a card, a callout, a column header) rather than by reading order. When Docling/pdftotext flatten such a page, "7,429" and "Global Programmatic Reach" land in different parts of the text stream, so the LLM either pairs the number with the wrong label or cannot ground it, and the 0057 gate quarantines it.

We have **validated** that a vision model reading the rendered page image fixes exactly this failure. DeepSeek-VL2-Small (int4 on a g6/L4 GPU) scored **7/8** on the ground-truth disambiguation cases flat text got wrong — pairing the right number with the right label by *seeing* the layout. This spec integrates that validated capability into the production extraction pipeline as a **third extraction source**, alongside Docling text and pdftotext (0060/0063).

**Core principle (carried from 0057):** a metric is a value plus its provenance. A vision-read metric has no verbatim text snippet by definition (text extraction failed on that page), so its provenance is the **rendered page-image citation plus a cross-confirmation of value and label against the document text**, surfaced as a **distinct, disclosed verification tier** — never silently mixed with Tier-A verbatim facts.

---

## 2. Goals

1. **Recover the quarantined designed-page metrics** — re-extract metrics from the rendered page image for the documents/pages whose metrics the 0057 gate quarantined as "Bucket E" (words present, no contiguous match) on figure-dense pages.
2. **Deterministic, gate-driven routing** — select vision candidates from *already-computed* 0057 gate output (quarantined metrics + grounding diagnostics), not from a standalone "is this an infographic?" classifier (the spike detector measured 28% precision — too weak to route on).
3. **Honest, disclosed provenance** — vision metrics carry `modality='vision'`, `grounding_source='vision'`, and a dedicated `verification_tier` value; the page-image citation is stored and renderable; value+label are cross-confirmed against the document text where possible.
4. **Reuse existing infrastructure** — the g6 GPU deploy (`parse_documents.py`), the `work_queue` claim model, the validated VL2 int4 harness, and the S3 weight cache. No new orchestration paradigm.
5. **No regression** — vision metrics are *additive*; the pipeline must never let a vision read overwrite or contradict a Tier-A verbatim-grounded text metric without flagging the conflict.

## 3. Non-Goals

- **Not** a font-garble fix. Mojibake hero stats (Make-A-Wish pattern, ~0.2% of metrics) are a **text-layer decode** problem owned by 0060 pdftotext-repair, not vision. Vision targets *correctly-decoded but spatially-scattered* numbers.
- **Not** a full-corpus re-parse or re-extraction. Vision runs only on the gate-flagged candidate pages.
- **Not** a replacement for the text track. The ~85% of metrics that ground verbatim stay on text; vision is the recovery path for the residual.
- **Not** the text-regroup gate. The spike's column-major "try-and-measure" regroup (`regroup_gate.py`, validated 6/6) is a *cheaper sibling* recovery path; see §7.1 for how the two relate. This spec ships vision; the cascade ordering is an open decision (§10).
- **Not** a vision-based *story* extractor (stories are narrative; scope is metrics). Stories stay on text.

---

## 4. Background & Validation

### 4.1 What the spike proved (spike 0064)

- **Model:** DeepSeek-VL2-Small, 4-bit NF4, on g6.2xlarge (L4, 24 GB). Validated recipe in memory `project_vision_track_vl2.md` and `locard/spikes/0064/launch_vl2.py` + `vl2_pkg/`.
- **Accuracy:** **7/8** on the ground-truth number↔label disambiguation cases that flat text got wrong — *identical* to bf16 on a 48 GB L40S, so 4-bit costs zero quality. The single miss was a digit-perception slip (read 706 vs ground-truth 709), not a reasoning or quantization error.
- **Throughput:** cold load 246 s (S3 weight cache); warm inference ~5.7 s/page (~626 pages/hr). Ample for a minority of pages.
- **Three packaging fixes** baked into the harness recipe: modern transformers stack (`.to` 4-bit dispatch), `incremental_prefilling(chunk_size=512)` (rotary IndexError, VL2 issue #82), `llm_int8_skip_modules=["kv_b_proj"]` (MLA raw-weight `baddbmm` Byte error).

### 4.2 The failure mode the data actually shows (corrects the "~15%" framing)

From the spike prevalence analysis (`prevalence_part2.py`, `prevalence-findings.md`) over run-10 (68,772 metrics):

| Quarantine bucket | Share of quarantines | Nature |
|---|---|---|
| **E: words present, runs OK, no exact match** | **89.2%** | hero number/label separated by 2D layout, reordered, or LLM-stitched → **vision target** |
| F: partial coverage (0.5–0.8) | 4.7% | fragmented |
| D: fragmented, low contiguity | 3.3% | fragmented |
| C: table-only | 1.4% | table-row-aware matching (0057 R2) |
| B: words absent (garble/fabrication) | 1.4% | 0060 repair / true defect |

Figure-density dose-response (quarantine rate rises with design density): `some_figures` 10.9% → `moderate (≥5)` 16.7% → `heavy_infographic (≥10 figs)` 21.5%; **94% of docs have ≥5 figures**, 34% are heavy-infographic. So the vision-recoverable population is **the Bucket-E quarantines concentrated in figure-dense docs** — large, valuable, and *deterministically identifiable from gate output*.

### 4.3 Why a deterministic gate signal, not a classifier

The spike's KPI-page detector (`kpi_detector.py`) measured **28% precision / 62% recall** on a 97-page labeled eval — too unreliable to gate GPU spend. The validated alternative (`SOLUTION-try-and-measure.md`) is to **never ask "is this a KPI page?"** Instead: a page is a candidate **because its metrics were already quarantined by the 0057 gate**, and the fix is **accepted only if it re-grounds metrics that the baseline could not** — a measurable outcome, not a guess. This spec applies that discipline: candidate selection reads `llm_metrics.verification_tier='quarantine'` + the grounding diagnostics (`word_coverage` high, `longest_run` low) + `lava_parse.documents.figure_count` density.

---

## 5. Architecture: vision as the third source

```
                 ┌─────────────────────────────────────────────────────┐
 baseline text   │ 0051 llm_extract → llm_metrics (modality default)    │
 extraction      │           │                                          │
                 │           ▼                                          │
 0057 gate ──────│  faithfulness gate → verification_tier per metric    │
                 │           │                                          │
                 │     ┌─────┴─────────────┐                            │
                 │  verified/Tier-A   QUARANTINE (Bucket-E, fig-dense)  │
                 │  (keep, text)            │                           │
                 └──────────────────────────┼───────────────────────────┘
                                            ▼  (deterministic candidate set)
                            ┌──────────────────────────────────┐
                            │ 0065 VISION TRACK                 │
                            │ 1. resolve candidate PAGES        │
                            │ 2. render page → PNG (corpus PDF) │
                            │ 3. VL2-Small int4 on g6/L4        │
                            │    comprehension-first per-page   │
                            │ 4. cross-confirm value+label vs   │
                            │    document text → vision tier    │
                            │ 5. write llm_metrics              │
                            │    (modality='vision', page img)  │
                            └──────────────────────────────────┘
```

### 5.1 Stage 1 — Candidate selection (deterministic, no classifier)

A quarantined metric becomes a vision candidate, and is localized to a specific **(content_sha256, page)**, by a pure-SQL + deterministic pre-pass — **no model inference**:

1. **Quarantine + Bucket-E filter:** the metric has `verification_tier='quarantine'` with grounding diagnostics matching **Bucket E** (`word_coverage ≥ 0.8` AND `longest_run < 0.5` — value/label words present but not contiguous).
2. **Figure-dense filter:** the doc has `lava_parse.documents.figure_count ≥ FIG_DENSE_MIN` (default 5). `figure_count` is populated by the parse worker (Spec 0046) and is authoritative.
3. **Not-already-recovered filter (cascade state):** the metric has not already been superseded by a successful text-regroup run (`superseded_by IS NULL`). If the text-regroup stage is not built, this filter is a no-op and vision takes all Bucket-E candidates. This is the explicit cascade state machine (§7.1).
4. **Digit pre-pass page localization (replaces "render all pages"):** search the document's per-page text (Docling section text by `page_start/page_end`, union pdftotext) for the metric's **value digits** (normalized — comma/scale variants per §5.4). The candidate page is the page where the digits appear. If the digits appear on multiple pages, each is a candidate page for that metric. **If the digits appear on NO page, the metric is dropped from the vision set** (grounding would fail anyway — §5.4 — so rendering is wasted). This eliminates the `MAX_VISION_PAGES_PER_DOC` blind-render fallback; the cap remains only as a per-doc safety ceiling on total rendered pages.

The candidate set operates on **one base extraction run** (`:base_run_id` — the run being recovered), not across runs, so results are reproducible. Distinct candidate **(sha, page)** pairs are enqueued into the vision work queue (§5.6); a page is rendered once even if it carries several quarantined metrics.

### 5.2 Stage 2 — Page rendering (proof image)

For each candidate page, render the page of the corpus PDF (from `s3://lavandula-nonprofit-collaterals/pdfs/{sha}.pdf`) to a PNG at a legibility-preserving DPI (default ~150–200; small designed numbers must stay readable). This reuses the validated `pypdfium2` render path from the spike (`build_arrow_demo.py`). The rendered PNG is **both** the vision model input **and** the stored citation artifact (§5.4, ties to the proof-image capture discussed for the 0052 viewer). Page images are content-addressed (`s3://…/vision_pages/{sha}/{page}.png`) and rendered once per page (dedup across multiple metrics on the same page).

### 5.3 Stage 3 — Vision extraction (VL2 on g6/L4)

Run DeepSeek-VL2-Small int4 (validated harness) on each candidate page image with a **comprehension-first per-page prompt** adapted from the spike V4 prompt (`cell_ground_docling.py`) — but reading an *image* and emitting the *metrics visible on the page*, each as `{statement, metric_value, label}` (plus, if reliable, a coarse visual location of the value for the proof box; see §10). Unlike the spike's per-question probing (`vl2_test.py` poses one disambiguation question per known-bad value), production emits a **list of metrics for the page**. The same int4 deploy recipe applies (S3 weight cache → 246 s cold load, ~5.7 s/page warm).

### 5.4 Stage 4 — Grounding & provenance tier for vision metrics

A vision metric has no verbatim contiguous snippet (that's why text failed). Its provenance is established by **cross-confirmation against the document text** plus the **page-image citation**. All matching uses the **0057 normalization** (lowercase → collapse whitespace → Unicode NFC → fold quotes/dashes); reuse `faithfulness/grounding.py` so the rules are identical to the text gate.

- **Value check:** the normalized `metric_value` appears in the candidate page's text in **Docling OR pdftotext** (0063 union), accepting the 0057 value-derivation variants (comma grouping `7,429`/`7429`, scale words `2.8M`/`2.8 million`, and the `numerator`/`denominator` legs for ratios). Confirms the model read the digits correctly (guards the 706/709 perception slip).
- **Label check:** the normalized `label` tokens have **word_coverage ≥ LABEL_COVER_MIN (default 0.8)** against the candidate page's text. Contiguity is **not** required (non-contiguity is the whole point) and proximity is **not** required (vision supplies the pairing; text only corroborates the label exists). **No synonym expansion** — the label words themselves must be present (keeps the claim defensible).
- **Citation:** the rendered page-image S3 key + page number. (Value bounding box is deferred — §11.3/Future; VLM grounding-token coordinates are unproven.)

Tier assignment (new values in 0057's ladder):

| Condition | Tier |
|---|---|
| value corroborated AND label word_coverage ≥ LABEL_COVER_MIN, page image stored | `vision_grounded` — disclosed, **publishable-with-disclosure** (value+label both corroborated; layout pairing supplied by vision) |
| value corroborated but **conflicts** with a *different* text value already paired to that label on the page | `conflict` — **human review required**, never auto-published |
| value OR label not corroborated in text | `quarantine` — vision could not be cross-confirmed; not published |

`vision_grounded` is a **distinct disclosed tier**, surfaced as a badge in the viewer/API with a methodology note whose wording must be **honest about what is and isn't verified**: *"the value and the label each appear in this document; their pairing was read from the page image by a vision model and is not independently verified from the text."* Co-occurrence proves both tokens *exist*, **not** that the model paired them correctly — the flattened text cannot confirm the pairing (that is precisely why text extraction failed here). This is the precise, per-fact disclosure 0057 mandates — not a blanket "AI may be wrong" hedge, and not an overclaim of "verified." It sits between Tier-A `verified` (verbatim-contiguous, pairing proven) and Tier-C `quarantine`.

Union-source semantics (0063): the value/label presence check succeeds if satisfied in **Docling OR pdftotext**; the two disagreeing on bulk prose is *not* a defect (different serializations) and does not by itself lower the tier. A `conflict` is specifically a value-vs-*different*-value contradiction on the page (§5.7), not a source serialization difference.

**Default product position:** `vision_grounded` IS publishable with its disclosure badge (consistent with 0057's truth-in-advertising stance). The operator may override to "review-first" globally — and may later refine the policy per report-type or confidence band rather than a single toggle (§11.5 is the one decision needing sign-off). `conflict` is review-first regardless.

### 5.5 Stage 5 — Write to schema

Vision metrics are written to `lava_vocab.llm_metrics` under a **new extraction run** (`run_tag = vision-<base_run_tag>`), using the existing columns + the 0057 faithfulness columns:

- `modality = 'vision'` (the 0057 column exists, default `'unspecified'` — purpose-built for this)
- `grounding_source = 'vision'` (extend the existing `docling` | `pdftotext-repaired` set)
- `verification_tier = 'vision_grounded' | 'quarantine'`
- `model_statement` (new column) = the model's abstractive sentence. `source_snippet` is reserved for **verbatim evidence only**: the value span + label span (as found in the corroborating text), with their offsets in `grounding_offsets`. The abstractive statement never occupies `source_snippet` (Trap §10.1) — this keeps `source_snippet` meaning the same thing across text and vision rows.
- `metric_text`, `metric_value`, `metric_type`/`label`, `unit`, `numerator`/`denominator` (structural fidelity per 0057) as usual
- a **page-image reference** (new column, §6) for the citation

Vision rows are *additive*. A reconciliation step (§5.7) handles overlap with the original quarantined text rows.

### 5.6 Deploy / orchestration (reuse `parse_documents.py`)

- Launch a g6.2xlarge (spot, AZ-rotating on `InsufficientInstanceCapacity`) via the existing `_launch_instance` pattern: SSM AMI param `/cloud2.lavandulagroup.com/docling-ami-id`, IAM profile `cloud2_lavandulagroup`, SG `sg-0d9a6217a104cfe35`, S3-tarball + `AWS-RunShellScript` deploy. Root volume 150 GB; TMPDIR/HF_HOME off `/tmp`.
- **S3 weight cache** (`s3://…/vl2_cache/hf/`) → 246 s cold load. **Stream the worker log to S3** every 30 s (no end-only upload — the hard-won observability lesson).
- A new `work_queue`-style claim model (mirror `lava_parse.work_queue` + `worker_heartbeats`) so multiple vision workers can self-coordinate via `SKIP LOCKED` (per 0055), with the 0056 heartbeat + relaunch-budget-resets-on-progress reliability already proven for parse.

### 5.7 Reconciliation with the original quarantined text rows

**Mapping key:** a vision row *corresponds to* a quarantined text row when they share `content_sha256`, the same candidate page, and the same normalized `metric_value`. Lifecycle:

- **Supersession:** when a `vision_grounded` row corresponds to a quarantined text row, set `superseded_by = <vision row id>` on the **text** row. Superseded text rows are **retained for audit** but **excluded from all published views/API** (the published view filters `superseded_by IS NULL AND verification_tier IN (publishable set)`). The vision row is the single published fact.
- **Uniqueness:** at most one vision row may supersede a given text row. If two vision rows map to the same quarantined text row (same page+value), keep the first written and mark the second a duplicate (`verification_tier='quarantine'`, note in `context_window`) rather than double-superseding — enforced by a partial unique index on `(superseded_by)` where not null.
- **No matching text row:** a `vision_grounded` row that localizes to a page but matches no *quarantined* text row (e.g. text never emitted the metric at all) is published on its own merits — it is still value+label corroborated against the page text.
- **Conflict:** if the vision value matches the page text but contradicts a *different* value already paired to that same label on the page, the vision row is `verification_tier='conflict'` (human review), and the text row is **not** superseded.

This is purely additive: no Tier-A `verified` text row is ever modified.

---

## 6. Schema changes (operator-run DDL)

> Per project constraints, Claude cannot apply RDS DDL; the operator runs these migrations in pgAdmin. Single-operator DB — no zero-downtime/dual-write needed.

1. **`grounding_source`** — extend accepted values to include `'vision'` (no DDL if free-text TEXT; document the new value).
2. **`verification_tier`** — add `'vision_grounded'` and `'conflict'` to the accepted set (no DDL if free-text TEXT; update `gate_runner` constants + any CHECK).
3. **Page-image citation, abstractive statement, and supersession columns** on `lava_vocab.llm_metrics`:
   ```sql
   ALTER TABLE lava_vocab.llm_metrics
     ADD COLUMN IF NOT EXISTS model_statement TEXT,         -- abstractive sentence (NOT verbatim; source_snippet stays verbatim-only)
     ADD COLUMN IF NOT EXISTS citation_image_key TEXT,      -- s3 key of rendered page PNG
     ADD COLUMN IF NOT EXISTS citation_page INT,            -- 1-based page number
     ADD COLUMN IF NOT EXISTS citation_bbox JSONB,          -- optional [x0,y0,x1,y1] of the value, page-pixel space (deferred, §11.3)
     ADD COLUMN IF NOT EXISTS superseded_by BIGINT;         -- on a TEXT row: the vision row that replaces it
   -- supersession is one-to-one in BOTH directions: a text row has one superseded_by (scalar
   -- column); a vision row supersedes at most one text row (unique index).
   CREATE UNIQUE INDEX IF NOT EXISTS llm_metrics_superseded_by_uniq
     ON lava_vocab.llm_metrics (superseded_by) WHERE superseded_by IS NOT NULL;
   ```
   The published view filters `superseded_by IS NULL AND verification_tier IN ('verified','vision_grounded', …publishable…)`; `conflict` and `quarantine` are excluded from publish, surfaced only in the review queue.
4. **Vision work queue + heartbeats** in a new **`lava_vision`** schema (clean separation from parse; §11.4 resolved), mirroring `lava_parse.work_queue` / `worker_heartbeats` (per-`(run_id, content_sha256, page)`, `claimed_by`/`claimed_at`/`completed_at`/`error`, `SKIP LOCKED` claiming, heartbeat row per worker).
5. **S3 lifecycle for rendered pages** — `s3://…/vision_pages/{sha}/{page}.png` is content-addressed by `(sha, page)` so re-runs reuse rather than orphan. A re-parse that changes a doc's `content_sha256` would orphan old keys; add an S3 lifecycle rule (expire `vision_pages/` objects with no referencing `citation_image_key` after N days) **or** a GC sweep keyed off live `citation_image_key` references. (Operational, not blocking.)

(The `modality`, `numerator`, `denominator`, `tier_b_confidence`, `grounding_offsets`, `context_window` columns from 0057 already exist and are reused.)

---

## 7. Technical Implementation

### 7.1 Relationship to the text-regroup gate

There are two recovery paths for designed pages: **text-regroup** (column-major reorder + KPI prompt, re-ground against original text — `regroup_gate.py`, validated 6/6, cheap: one DeepSeek text call) and **vision** (this spec, validated 7/8, costs GPU). They recover overlapping but not identical sets: text-regroup wins when the words just need reordering; vision wins when the pairing genuinely requires *seeing* the 2D layout. **Recommended cascade (open decision §10):** baseline → 0057 gate → text-regroup on quarantines (cheap) → **vision on the residual quarantines** (expensive). If the text-regroup stage is not built, vision takes all Bucket-E candidates. Either way the candidate signal (§5.1) is identical and the accept criterion is identical: *recover grounded metrics the prior stage could not.*

### 7.2 New modules

- `lavandula/vision/render.py` — page → PNG (pypdfium2), content-addressed S3 upload, render-once-per-page.
- `lavandula/vision/extract.py` — VL2 load (the validated `load()`/`ask()` from `vl2_test.py`, productionized), per-page comprehension-first prompt, JSON parse.
- `lavandula/vision/ground.py` — value+label cross-confirmation against Docling/pdftotext (reuse `faithfulness/grounding.py` normalization + R1/R2), tier assignment, write to `llm_metrics`.
- `lavandula/vision/worker.py` — claim candidate pages (`SKIP LOCKED`), heartbeat, render→extract→ground→write, mirror `lavandula/parse/worker.py`.
- `lavandula/dashboard/pipeline/management/commands/extract_vision.py` — orchestrator: build candidate set, launch g6 worker(s) via the `parse_documents` deploy pattern, monitor, terminate.

### 7.3 Candidate query (illustrative)

```sql
-- vision candidates: quarantined Bucket-E metrics on figure-dense docs,
-- localized to a page via the section that carried the snippet.
SELECT m.content_sha256, s.page_start AS page, count(*) AS n_quarantined
FROM lava_vocab.llm_metrics m
JOIN lava_parse.documents d  ON d.content_sha256 = m.content_sha256
LEFT JOIN lava_parse.sections s
       ON s.content_sha256 = m.content_sha256
      AND m.source_snippet IS NOT NULL  -- localize via section text match (impl detail)
WHERE m.run_id = :base_run_id
  AND m.verification_tier = 'quarantine'
  AND (m.grounding_offsets->>'word_coverage')::numeric >= 0.8   -- words present
  AND (m.grounding_offsets->>'longest_run')::numeric  <  0.5    -- not contiguous (Bucket E)
  AND d.figure_count >= :fig_dense_min
GROUP BY 1, 2;
```
(Exact diagnostic plumbing — whether `word_coverage`/`longest_run` live in `grounding_offsets` JSONB or dedicated columns — to be confirmed against `faithfulness/grounding.py`'s `Verdict` persistence during planning.)

### 7.4 Reused, validated assets

- VL2 int4 recipe & harness: `locard/spikes/0064/launch_vl2.py`, `vl2_pkg/run.sh`, `vl2_pkg/vl2_test.py`.
- g6 deploy: `parse_documents.py` `_launch_instance` / `_start_worker` / poll loop.
- Grounding: `lavandula/faithfulness/grounding.py` (R1/R2 + normalization), `source_provider.py` (Docling/pdftotext sources, extend with union per 0063).
- Render: `pypdfium2` (`build_arrow_demo.py`).

### 7.5 Error handling & idempotency

| Failure | Handling |
|---|---|
| Page render hangs / exceeds `RENDER_TIMEOUT_S` | killable child process is **terminated** (0058 `PersistentParseRunner` pattern); `error=render_timeout`/`render_crash`; page quarantined; worker survives. |
| Page render fails (corrupt/encrypted PDF page) | `error=render_failed`, skip the page, **do not** crash the worker; metric stays `quarantine`. |
| Page declares oversized dimensions | render capped at `MAX_RENDER_PIXELS` (downscale); never allocate an unbounded bitmap. |
| VL2 inference exceeds `VISION_TIMEOUT_S` | inference killed; `error=vision_timeout`; page quarantined; worker continues. |
| VL2 returns malformed / partially-valid JSON (missing a required field) | the **whole page output** is treated as malformed: retry once; on second failure `error=vision_parse_failed`; metrics stay `quarantine` (no partial-record guessing). |
| Duplicate page claim across workers | prevented by `SELECT … FOR UPDATE SKIP LOCKED` on the vision work queue (per 0055). |
| Two workers emit equivalent rows (race) | supersession is guarded by the `superseded_by` unique index; the second loser is marked duplicate, not double-published. |
| Worker dies mid-page (spot reclaim, OOM, segfault) | page write is a **single transaction, idempotent** per `(run_id, sha, page)` via delete-then-insert (the `llm_extract._write_metrics` pattern); the unclaimed/incomplete item is re-claimable; 0056 heartbeat detects the dead slot. |
| Docling vs pdftotext disagree | union grounding (0063): value/label found in **either** source suffices (a serialization difference is not a defect); neither → `quarantine`. |
| Value digits localize to no page (pre-pass empty) | metric dropped from vision set before any render (no GPU spent); stays `quarantine`. |
| Same value appears on multiple pages | each is a candidate page; the page whose text also satisfies the label check grounds the metric; if several do, keep the first and log the ambiguity. |
| Same label paired to multiple values on one page | each (value,label) is a separate metric; a value matching a *different* text value already bound to that label → `conflict`. |
| Corroboration relies on invisible/hidden text | the value/label span is found only in non-visible text → downgrade to `quarantine` + flag the doc (anti-steganography, §13). |

All vision writes are scoped to the vision `run_id`; a re-run with the same `run_tag` is idempotent (delete-then-insert per page), so partial runs are safely resumable.

---

## 8. Testing Strategy

**CI fixture (offline, no GPU):** package the 4 spike ground-truth pages (`locard/spikes/0064/vl2_pkg/images/*.png`) + `ground_truth.json` + a small synthetic `lava_parse`/`llm_metrics` fixture (a base run with: 2 quarantined Bucket-E metrics on a figure-dense doc, 1 verbatim-grounded metric, 1 garble/Bucket-B metric, 1 quarantined metric whose digits appear on no page). Expected outputs are asserted against this fixture. The VL2 model call is **mocked** in unit tests (fixed JSON responses keyed to the 4 images); the real-GPU end-to-end run (AC3/AC4) is a separate manual/integration test on a g6, not CI.

Required coverage:
1. **Candidate selection (pure SQL + pre-pass):** the fixture yields exactly the expected `(sha, page)` set — includes the 2 Bucket-E metrics, **excludes** the verbatim-grounded, the garble, and the digits-on-no-page metric; excludes any `superseded_by IS NOT NULL`. Asserts zero model inference.
2. **Digit pre-pass localization:** a metric whose value appears only on page 12 localizes to page 12; appears nowhere → dropped.
3. **Render dedup:** a page with 2 quarantined metrics is rendered once; the PNG key is content-addressed by `(sha, page)`.
4. **Grounding tiers (mocked VL2):** value+label corroborated → `vision_grounded`; value not in text → `quarantine`; value present but contradicts a different value paired to that label → `conflict`. Normalization (comma/scale/NFC) honored.
5. **Reconciliation/supersession:** a `vision_grounded` row sets `superseded_by` on the matching quarantined text row; the published view shows one fact; a second vision row mapping to the same text row is rejected by the unique index.
6. **Worker lease/heartbeat/idempotency:** `SKIP LOCKED` prevents double-claim; re-running the same `run_tag` is idempotent; a simulated mid-page death leaves the item re-claimable.
7. **Negative/no-regression:** no Tier-A `verified` text row is modified by any vision path; `conflict`/`quarantine` never appear in the published view.
8. **Adversarial fixtures (red-team):**
   - duplicate label with two different values on one page → two metrics / `conflict` where applicable;
   - the same value appearing on multiple pages → grounds on the label-satisfying page, ambiguity logged;
   - value+label both present but semantically unrelated co-occurrence → still `vision_grounded` *but* asserts the disclosure wording is the honest "pairing not independently verified" (not "verified");
   - **hidden-text injection:** a fixture page whose only corroboration is invisible text → `quarantine` + flag (anti-steganography);
   - oversized-page render → capped at `MAX_RENDER_PIXELS`;
   - render/inference hang → killed at the timeout, page quarantined, worker survives;
   - malformed-but-partially-valid model JSON → whole page treated malformed;
   - concurrency race on supersession → unique index rejects the duplicate.

**End-to-end (manual, g6):**
9. **AC3/AC4** — render → VL2 int4 → JSON on the 4 ground-truth pages reproduces the 7/8 disambiguation; cold load ≤ ~300 s (cached), warm ≥ ~400 pages/hr; log streams to S3 mid-run.

---

## 9. Acceptance Criteria

**Candidate selection**
1. The candidate query selects only docs with quarantined Bucket-E metrics on figure-dense pages; given a fixture run, it returns the expected page set and **excludes** verbatim-grounded and garble (Bucket-B) docs.
2. Candidate selection performs **zero model inference** (pure SQL over gate output).

**Vision extraction**
3. On the 4 spike ground-truth pages, the production per-page prompt recovers the correct number↔label pairings (matches the 7/8 disambiguation result) when run end-to-end (render → VL2 → JSON).
4. Cold load ≤ ~300 s via S3 weight cache; warm throughput ≥ ~400 pages/hr on g6.2xlarge.
5. Worker log streams to S3 during the run (partial log readable mid-flight); a killed/timed-out run still leaves a partial log.

**Grounding & provenance**
6. Every written vision metric has `modality='vision'`, `grounding_source='vision'`, a stored `citation_image_key` + `citation_page`, and a `verification_tier` of `vision_grounded`, `conflict`, or `quarantine`.
7. A vision metric whose value is **not** found in any text source is assigned `quarantine`, never `vision_grounded` (no publishing an uncorroborated vision read).
8. A vision value that **conflicts** with a different value paired to the same label on the page is assigned `conflict` (human review), not auto-published; the matching text row is not superseded.

**Integration & no-regression**
9. Vision rows are additive; no Tier-A `verified` text metric is overwritten. Where a `vision_grounded` row supersedes a quarantined text row, exactly one published fact is shown (the vision row), with `superseded_by` set on the text row.
10. The viewer/API exposes the `vision_grounded` tier as a distinct, disclosed badge (not collapsed into `verified`).

**Reliability**
11. Reuses the 0056 heartbeat + progress-resetting relaunch budget; a healthy-but-slow worker is not falsely relaunched; the run reaches a terminal status (`success`/`failed`) and is resumable from the work queue.

**Security & robustness (red-team)**
12. Render and inference each run under an enforced wall-clock timeout in a **killable** child process; a hanging/segfaulting page is quarantined and the worker survives (no GPU drain).
13. Rendered PNGs are capped at `MAX_RENDER_PIXELS` (downscaled), raster-only, with image metadata stripped; the viewer/API enforces source-PDF authorization on citation images.
14. A metric whose value/label corroboration is present **only in non-visible text** is `quarantine`d and flagged (hidden-text injection defense); the `vision_grounded` disclosure states the pairing is not independently verified.
15. The vision worker IAM role has no `s3:DeleteObject` on the `vision_pages/` or log prefixes; supersession/conflict/duplicate decisions are written to an audit log with reason codes.

---

## 10. Traps to Avoid

1. **Do not claim the model's `statement` is a verbatim snippet.** It is an abstractive sentence. `source_snippet` for a vision metric is the statement; the *verbatim* evidence is the value+label spans recorded in `grounding_offsets` against the corroborating text source, plus the page image. Mislabeling it verbatim would poison the 0057 chain of custody.
2. **Do not route on a classifier.** The detector is 28% precision. Route on quarantine + diagnostics (deterministic), and accept only on measured re-grounding.
3. **Do not publish an uncorroborated vision read.** Value not in text → quarantine. Vision can hallucinate a digit (the 706/709 slip); the value cross-check against text is the guard.
4. **Do not render at low DPI.** The designed numbers this targets are often small; render ~150–200 DPI so digits stay legible (and the citation image is readable).
5. **Do not re-download weights per run.** Pull from the S3 cache; HF download is ~50 min vs ~4 min cached. End-only logs = flying blind; stream every 30 s.
6. **Do not let vision touch the garble bucket.** Mojibake (Bucket B) is a 0060 text-layer decode job; vision on a garbled page wastes GPU on content pdftotext already recovers cleanly.
7. **Do not over-render.** Cap `MAX_VISION_PAGES_PER_DOC`; one render per page (dedup); skip pages with no quarantined metric.
8. **Reading order is not the model's job to invent silently.** Capture the value+label as the model pairs them visually, but always re-confirm both against text before publishing.

---

## 11. Open decisions

Resolved during spec-review (Codex/Gemini), recorded here for the record:

1. **Cascade vs standalone → RESOLVED: cascade.** Run the cheap text-regroup gate first; vision takes the residual. Enforced by candidate-filter §5.1.3 (`superseded_by IS NULL`); if text-regroup isn't built, the filter is a no-op and vision takes all Bucket-E candidates. The cascade state machine is explicit (§7.1).
2. **Page localization → RESOLVED: digit pre-pass (§5.1.4).** Search per-page text for the value digits; render only the page(s) where they appear; drop the metric (no render) when they appear nowhere. Replaces the blind render-all-pages fallback (Gemini's suggestion).
3. **Value bounding box → RESOLVED: deferred to V2 (§12).** Whole-page image is the citation now; VLM grounding-token coordinates are unproven (both reviewers concurred).
4. **Schema home → RESOLVED: new `lava_vision` schema** for the work queue/heartbeats (§6.4); metric rows stay in `lava_vocab.llm_metrics`.

**Still needs operator sign-off (the one genuine product call):**
5. **`vision_grounded` publishability.** Spec default: a value+label-corroborated vision read **is publishable with its disclosure badge** (consistent with 0057's truth-in-advertising stance). Confirm this default, or set it to "review-first" globally. (`conflict` is review-first regardless.)

## 12. Future / out of scope

- Value-bounding-box proof overlay in the 0052 viewer (depends on §11.3).
- Pre-quantized 4-bit weight cache (~10 GB) for ~2–3 min cold start (a deploy optimization, not blocking).
- Vision for non-metric content (chart-series extraction, table reconstruction).
- Extending vision recovery to the OCR/scanned Tier-B population (different problem: no text layer to cross-confirm against).

---

## 13. Security considerations

Addressed after red-team review (Codex + Gemini, both REQUEST_CHANGES → resolved below).

### Resource exhaustion / DoS on the GPU worker (red-team HIGH)
A crafted "PDF bomb" page (huge declared dimensions, deeply nested content, parser pathology) could hang the render or inference step and drain an expensive g6 spot instance. This is the **same failure class as the 0058 poison-doc hang** — apply the same defense:
- **Killable render in a child process with a wall-clock timeout** (default `RENDER_TIMEOUT_S=15`/page), reusing the 0058 `PersistentParseRunner` subprocess pattern (a hung/segfaulting render maps to `error=render_timeout`/`render_crash`, the page is quarantined, the worker survives). A render that exceeds the timeout is killed, not waited on.
- **Inference wall-clock timeout** (default `VISION_TIMEOUT_S=60`/page) around `model.generate`; on timeout the page is quarantined and the worker continues.
- **Max rendered-PNG pixel cap** (default `MAX_RENDER_PIXELS`, e.g. 4000×4000) regardless of the PDF's declared physical size/DPI — downscale to fit. Protects worker RAM, S3 size, and the downstream 0052 viewer from an oversized bitmap.
- The 0056 heartbeat + finite, resumable work queue bound total spend; `MAX_VISION_PAGES_PER_DOC` + the digit pre-pass (§5.1.4) cap pages rendered.

### Prompt injection (red-team HIGH)
- **Visual-only exposure:** the model is given **ONLY the page image + a fixed extraction prompt** — no document text, no tools, no instructions beyond "extract this page." Document text is consulted *afterward, in code*, to cross-confirm value+label. An injected visual "label" not present in the document text → `quarantine`. So a *visual-only* injection can at most cause a *dropped* metric, never a published fabrication.
- **Hidden-text bypass (residual, must be mitigated + disclosed):** an attacker who controls the PDF can plant **invisible text** (0 pt, white-on-white, text-render-mode 3) that carries the fake value+label, so the VLM reads it visually AND the text cross-check finds it — a false `vision_grounded`. Mitigation: **when building the corroborating text, exclude non-visible text** — prefer Docling/pdftotext renderings that drop invisible glyphs, and add an invisible-text detector (render-mode-3 / zero-area / color-equals-background) that, if a metric's corroboration depends on hidden text, downgrades it to `quarantine` and flags the doc. Residual risk (combined visual+textual steganography) is **explicitly disclosed** as a known limitation of the `vision_grounded` tier (it is corpus content we ingested, not attacker-supplied at request time, which bounds the realistic threat).

### Citation artifacts (red-team MEDIUM/LOW)
- **Raster-only, metadata-stripped:** only the rendered raster PNG is stored — no embedded PDF objects, scripts, or original metadata; strip/normalize image metadata before upload.
- **Authorization parity:** `vision_pages/*.png` is a pixel-perfect replica of a (potentially sensitive) document page; the viewer/API must enforce the **same authorization** as the source PDF (private S3, presigned/auth path), never public.

### Least-privilege IAM (red-team MEDIUM)
The vision worker role grants **read** on the PDF + weight-cache prefixes and **`s3:PutObject` only (no `s3:DeleteObject`)** on the `vision_pages/` and log prefixes — a compromised worker (e.g. a pypdfium2 RCE) cannot delete citations or rewrite logs to hide tracks; re-runs overwrite via `PutObject`/versioning. DB writes are scoped to `lava_vision` + the vision `run_id`. Concrete read/write matrix to be finalized in the plan.

### Audit (red-team LOW)
Every **supersession, duplicate rejection, and conflict** decision is logged with `(run_id, sha, page, text_row_id, vision_row_id, reason_code)` so the published-vs-superseded lineage is reconstructable.

### Integrity boundary (the honest claim — see §5.4)
`vision_grounded` certifies that the value and the label **each appear** in the document text; it does **not** independently certify the *pairing* (the flattened text cannot — that is why text extraction failed on this page). The pairing is supplied by the vision model. This residual is the tier's disclosed meaning, not a hidden gap.
