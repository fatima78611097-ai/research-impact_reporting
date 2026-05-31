# Spec 0058 — Parse Performance & Robustness (Hang Defense + TableFormer FAST + Conditional OCR)

- **Project:** 0058
- **Status:** conceived (multi-agent review incorporated; grounded by 4-thread research workflow)
- **Depends on:** 0055 (Multi-Instance Parse), 0056 (Parse Reliability — heartbeat backstop)
- **Author:** Architect, 2026-05-31

> **Scale gate (robustness half):** the per-doc hang defense must land before the next full-corpus / national parse run. Run 32 proved a single image-heavy PDF wedges a worker forever; the 0056 heartbeat only caught it after 5 minutes of dead time, and it recurs on every relaunch.

---

## 1. Problem & Motivation

Two coupled problems in the Docling parse pipeline, both rooted in `chunking.parse_pdf()` using a bare `DocumentConverter()` with default options and no per-doc bound.

### 1.1 Hang / poison docs (the blocker)

Run 32 (smoke test) wedged on `e038a9e75317ff86` — a 5.6 MB, 4-page Adobe Illustrator annual report with multiple 23–35 megapixel embedded images and a 19-char text layer. Docling rasterizes those images at full resolution with OCR on; `converter.convert()` hung indefinitely. No per-doc timeout, no subprocess isolation. The 0056 heartbeat eventually terminated the worker after 5 min — but the doc was re-claimed on relaunch and hung the next worker too, so the run made zero progress and failed.

**This is not an outlier — it is a cohort** (measured, §1.3): 12,570 corpus docs (3.77%) match the poison profile, and 55 of 16,238 already-parsed docs took >120s (1 hit 646s).

### 1.2 Slowness / GPU underutilization

Measured profiling (g6/L4, Docling 2.93.0): per-doc parse averages ~15.5s (p50 10.8, p90 27.4, p99 69.5, **max 646s**); GPU only ~12% utilized; ~all time inside `convert()`, split between OCR and TableFormer. Our chunking/extract/DB work is ~0s. The long tail of slow docs dominates total compute on a full-corpus run.

### 1.3 Corpus blast radius (measured, n=333,456 corpus / 16,238 parsed)

| Signal | Count | % corpus |
|---|---|---|
| MB/page > 1.0 | 31,579 | 9.47% |
| MB/page > 1.0 **AND** first_page_text < 200 chars (**poison profile**) | 12,570 | 3.77% |
| MB/page > 1.5 AND text < 200 | 6,818 | 2.04% |
| Adobe design tools (Illustrator/InDesign/Photoshop) | 95,621 | 28.68% |
| Canva | 29,512 | 8.85% |

Parse-duration tail (of 16,238 parsed): >30s = 1,343 (8.3%), >60s = 247 (1.5%), >120s = 55 (0.34%), >300s = 3, >600s = 1.

**Key insight:** slowness is NOT purely image-heavy — several of the slowest parsed docs were low-filesize, high-page-count scanned/OCR-heavy reports (e.g. a 46-page UNKNOWN-creator doc at 501s). So a **per-doc timeout is the universal guard**; image-cap + conditional-OCR target specific subsets.

## 2. Scope / Non-Goals

**0058 delivers:**
1. **Layered hang defense** — per-doc timeout that a hung `convert()` cannot escape, so no single doc can wedge a worker.
2. **Pre-parse poison triage** — cheap deterministic check (MB/page + text-layer signal) to cap or downgrade high-risk docs before they hit the GPU.
3. **Pipeline tuning** — TableFormer FAST mode + capped `images_scale` + conditional OCR, each gated by a cell-content A/B.
4. **Quality acceptance gate** — a reproducible cell-CONTENT A/B (not counts) that any pipeline change must pass, backed by the 0057 grounding gate.
5. **Observability** — distinguish "slow but completed" from "timed out / killed" in `lava_parse.documents`.

**Non-Goals:**
- Multi-worker GPU concurrency / FP16 (secondary; cut the work first — separate future project).
- Replacing Docling (its structure + tables are the product; we're tuning its pipeline).
- The NUL-byte fix (already committed in chunking.py `_scrub`; ships in this spec's worker tarball but is not 0058 scope).
- Corpus/S3 integrity reconcile for the 65 missing PDFs (separate corpus-integrity follow-up).

## 3. Requirements

### 3.0 Phase-0 spike (de-risk the timeout mechanism) — REQUIRED FIRST

Docling 2.93's `PdfPipelineOptions` exposes a native **`document_timeout`**. Before building anything, a 1–2h spike must answer: **does `document_timeout` actually interrupt a hard hang** (the `e038a9e75317ff86` case), or does it only check between pipeline stages and fail to break a C/CUDA-level block? Run the poison doc through `convert()` with `document_timeout` set and measure wall-clock + whether it returns/raises.

- **If `document_timeout` reliably bounds the poison doc** → it is the primary mechanism (cheap, no subprocess machinery). Ship it.
- **If it does NOT** (hang exceeds the timeout) → escalate to the subprocess-isolation design (§3.3), which is heavier.

The spike's PASS/FAIL determines which of §3.2/§3.3 the plan builds. Output: a result note, throwaway code.

**PASS criteria (all must hold) — `document_timeout` is the primary mechanism only if:**
1. On the poison doc with `document_timeout = T`, `convert()` returns control (raises or returns) within `T + 30s` wall-clock — measured across **≥ 3 runs** (deterministic, not a lucky single run).
2. After the timeout fires, the worker process is healthy: it can immediately parse a normal doc to completion (GPU context intact, no leaked memory that breaks the next convert).
3. No orphaned threads/processes left consuming GPU after the timeout.

**FAIL (→ build §3.3 subprocess isolation) if any:** the poison doc exceeds `T + 30s`; OR the timeout fires but the next doc fails/hangs (corrupted GPU state); OR cleanup leaks GPU memory across docs. A "late exception" (fires but well past T) counts as FAIL — partial bounding is not bounding.

### 3.1 Pre-parse poison triage (cheap, deterministic, always-on)

Before calling `convert()`, compute a risk signal from data already on hand (no GPU, no full download needed — corpus row + the local PDF bytes the worker already holds):
- `mb_per_page = file_size_bytes / max(page_count, 1)`
- text-layer signal: `first_page_text` length (corpus) and/or the 0060 `pdftotext` char_count if present.

For docs matching the **poison profile** (default: `mb_per_page > 1.0 AND text_signal < 200 chars`; threshold config-driven), parse with **downgraded options**: capped `images_scale`, conditional OCR per §3.4, and the per-doc timeout. This removes most hang risk before it reaches the GPU. The threshold is configurable; default chosen from §1.3 (3.77% of corpus).

**Triage is a DOWNGRADE, never a skip.** A poison-profile doc is still parsed — just with reduced `images_scale` and the timeout — so we never silently drop content (a designed report's hero stats matter; recall-first). Only the timeout (§3.2/§3.3) or an explicit quarantine flag (§3.5) prevents a parse. The triage decision is recorded as `parse_outcome='downgraded'` (§5) so its quality impact is measurable. The per-doc timeout applies to **every** doc regardless of triage — triage reduces the chance of hitting it, it is not the hang guard itself.

### 3.2 Per-doc timeout via native `document_timeout` (if spike PASSES)

Set `PdfPipelineOptions.document_timeout` to a configured bound (default **180s** — covers p99 69.5s with safety margin, well under the 0056 5-min heartbeat). On timeout, Docling raises; `chunking.parse_pdf` converts that to a `DoclingParseError`, and `worker._process_one` raises `PermanentError('parse_timeout')`. Page cap via `convert(max_num_pages=...)` as a secondary bound.

**Error / claim contract (precise):** a `parse_timeout` follows the **existing PermanentError path** (worker.py): `_record_error` inserts a `documents` row with `error='parse_timeout'`, then `complete_work_item()` marks the work_queue row complete-with-error in the same flow — so the row is **never left claimed-incomplete**, and the worker advances to the next doc. A timed-out doc is **NOT auto-retried** within the run (it is a PermanentError, not a TransientError — avoids re-hanging the same doc). Because a `documents` row now exists, `populate_work_queue` will **not** re-enqueue it on a future run unless the operator explicitly clears it or runs with a reprocess flag (so a one-time relaxed-timeout retry is an explicit operator action, not automatic). This is the same contract the NUL-byte and download-fail errors already use.

### 3.3 Subprocess isolation (ONLY if spike FAILS)

If `document_timeout` cannot break a hard hang, run `convert()` in a **persistent child process** (multiprocessing `spawn` context) that holds the warm model across docs and is killed + respawned only on timeout:
- One child per worker, model loaded once (~minutes). Per-doc requests routed via queue; normal docs incur zero reload cost.
- Parent `join(timeout)`; on expiry → `terminate()` then `kill()` (SIGKILL reclaims GPU memory) → respawn → raise `DoclingParseTimeout` → `PermanentError('parse_timeout')`.
- **IPC contract (Gemini review):** the child must NOT return the raw `DoclingDocument` across the process boundary — it is large and may not pickle cleanly. The child runs `extract_sections` / `extract_tables` / `get_document_metadata` **inside the child** and returns the small, already-flattened dicts (the same structures `db.insert_document` consumes). Only plain JSON-serializable data crosses the queue.
- **Circuit breaker keys on CONSECUTIVE failures (Gemini review):** respawn-then-success resets the counter; the run fails only after N *consecutive* child deaths (config, default 5) — so a scattered cluster of poison docs doesn't fail an otherwise healthy run. Mirrors 0056's per-slot consecutive-failure logic.
- **GPU cleanup:** SIGKILL reclaims the child's CUDA memory at the OS/driver level; the spike (§3.0 criterion 2/3) also validates this holds across respawns so repeated timeouts don't fragment GPU memory. If a respawn's model load itself fails, that counts toward the consecutive-failure breaker.
- Precedents in-repo: `reports/fetch_pdf.py` (multiprocessing spawn + terminate/kill), `faithfulness/pdftotext_extract.py` (subprocess timeout=30).

This is the heavier path; the spike exists specifically to avoid building it if the native timeout suffices. The 0056 heartbeat remains the final backstop in all cases.

### 3.4 Pipeline tuning (each gated by §4 A/B)

- **TableFormer FAST:** set `TableStructureOptions(mode=TableFormerMode.FAST, do_cell_matching=True)`. Measured 1.5× table-heavy / 2.6× on a 44pg doc with no cell-count loss — but must pass the cell-CONTENT A/B before shipping (the plan must first confirm whether 2.93 already defaults to FAST or ACCURATE).
- **`images_scale` cap:** default 2.0 → capped (candidate 1.0–1.5); reduces rasterization cost for image-heavy docs. A/B-gated.
- **Conditional OCR:** NEVER blanket-off (measured: destroyed 88% of table cells + 6% text on a scanned doc). OCR is enabled only when an embedded text layer is confidently present. **Detector precedence (deterministic, first available wins):**
  1. **0060 `pdftotext` result** (preferred — already computed corpus-wide): `text_source='text_native'` with a healthy `char_count` (≥ a configured floor, candidate 200) → **skip OCR**. `scanned`/`pdftotext_failed`/thin → **keep OCR**.
  2. **Fallback** when no 0060 row at parse time: inline pdfminer/`first_page_text` length ≥ floor → skip OCR.
  3. **Default when signals are absent or disagree: keep OCR ON** (fail toward completeness — a wrongly-OCR'd text-native doc is just slower; a wrongly-skipped scanned doc loses all content).
  **Mixed PDFs** (some pages text, some scanned): v1 decides **per-document** (keep OCR if ANY signal suggests a scanned page, i.e. err toward OCR-on); true per-page conditional OCR is a non-goal for v1 and noted as a future refinement. Docling's own auto-skip (it bypasses OCR on pages with a text layer) further limits the cost of a conservative document-level "keep OCR" decision.

### 3.5 Quarantine known poison docs

Quarantine is **DB-persisted, not dynamic** — a known-hang doc carries a durable flag so it is excluded from `populate_work_queue` enqueue (a column on the corpus/documents side, set by the operator or by the gate when a doc times out repeatedly). This is distinct from §3.1 triage (which is computed dynamically at parse time and only downgrades). Concretely: `e038a9e75317ff86` is flagged now; the plan defines whether the flag lives on `lava_corpus.corpus` or a small `lava_parse.parse_blocklist` table (operator-run DDL). A timed-out doc is NOT auto-quarantined (it already errors cleanly per §3.2); quarantine is for docs proven to hang the timeout mechanism itself or known-pathological inputs. Quarantine = a flag, never a delete.

## 4. Quality Acceptance Gate — Cell-Content A/B (the core safeguard)

No pipeline change (FAST, images_scale, conditional OCR) ships without passing this. **Measure cell CONTENT, not counts** — a cell can change "100"→"101" or get garbled while the count is unchanged.

**Sample (stratified to the real ~99% text-native / ~1% scanned skew, NOT 1:1:1):** table-heavy, scanned/OCR-dependent, and designed/image-heavy docs, plus the poison doc and the measured slow-doc examples (`de8fecc5…` 646s, `658d9b89…` 46.9 MB/page).

**Variant A** = current default pipeline. **Variant B** = proposed (FAST + capped images_scale + conditional OCR + timeout).

**Metrics (per the 0060 lesson — use set coverage, NOT sequence alignment/edit-distance, which conflates benign reorder with real loss):**
1. **Cell-content parity:** normalize every cell (0057 `normalize()`: lowercase + collapse ws + NFC + quote/dash fold), compare as sets. Bidirectional coverage `|A∩B|/|A|` and invention `|B∖A|/|B|`.
2. **Row preservation:** Jaccard per-row; count exact / partial(≥80%) / lost rows. A lost row = a grounding error.
3. **OCR quality** (scanned sample where B keeps OCR): normalized word-set coverage ≥ 0.95 vs A.
4. **Grounding compatibility:** run the **real 0057 gate** on both variants' table markdown; Variant B must produce **no new Tier-C** (no faithfulness regression).

**Acceptance thresholds (frozen):**
- Text-native: cell-content parity **≥ 0.9999**, **zero lost rows**, **zero** grounding-tier regression (exact equality).
- Scanned: cell parity ≥ 0.95, lost rows ≤ 1%.
- Poison doc: Variant B **completes within the timeout** (no hang); Variant A baseline hangs.
- Performance: TableFormer FAST ≥ 1.3× on the table-heavy sample (informational floor; below it, revisit).

## 5. Data Model & Observability

**New nullable columns on `lava_parse.documents`** (operator-run DDL, same pattern as 0056/0057/0060; existing rows default NULL = legacy):
- `docling_convert_ms INTEGER` — time inside `convert()` alone (vs total `parse_duration_ms` which includes chunking/extract/DB).
- `parse_outcome TEXT` — enum `ok | timeout | downgraded | error` (a downgraded doc that completes is `downgraded`; a timed-out doc is `timeout` and carries `error='parse_timeout'`). This is a per-document column, not a run aggregate; the dashboard rolls it up per run.

Per-doc timeout/respawn/downgrade events logged (INFO/WARNING) and counted into `parse_runs.stats_json` for the dashboard.

## 6. Acceptance Criteria

1. No single document can wedge a worker longer than the configured per-doc timeout (default 180s) + a bounded kill margin — proven on the poison doc `e038a9e75317ff86` (Variant A hangs; Variant B completes/errors cleanly).
2. A timed-out doc is recorded `errored` (`parse_timeout`), the worker continues, and the queue is not left with a stranded claimed-incomplete row.
3. Pre-parse triage flags the poison cohort and downgrades their pipeline options.
4. TableFormer FAST + images_scale cap + conditional OCR each pass the §4 cell-content A/B before shipping; **zero cell-content loss and zero grounding regression on text-native docs**.
5. Conditional OCR keeps OCR ON for scanned docs (no blanket-off regression).
6. Observability distinguishes timeout/downgrade/ok; per-doc convert time recorded.
7. The Phase-0 spike result is documented and dictates which timeout mechanism shipped.
8. **Required tests, split by tier:**
   - **Unit (deterministic fixtures, CI):** triage threshold math classifies the §1.3 example shas correctly; conditional-OCR decision function returns skip-OCR for a text-native signal and keep-OCR for a thin/empty signal; `parse_timeout` maps to `PermanentError('parse_timeout')` and routes through `_record_error` + `complete_work_item` (mocked); pipeline-options builder produces FAST + capped images_scale + correct OCR flag.
   - **Integration (real Docling, one instance, not CI):** poison doc `e038a9e75317ff86` completes within timeout (or errors cleanly) and the worker parses a normal doc immediately after; conditional OCR on a real scanned doc keeps its tables.
   - **End-to-end / acceptance (corpus-sample, manual):** the §4 cell-content A/B over the stratified sample, with frozen pass/fail thresholds, run via the A/B harness (which calls the real shipped 0057 gate).
   Unit tests gate CI; integration + A/B are operator-run before the tarball ships to a full run.

## 7. Security & Abuse Considerations

- PDFs are untrusted input to a GPU process. The per-doc timeout + (if needed) subprocess kill bound resource exhaustion from a malicious/pathological PDF (the 35-megapixel-image case is the natural attack shape).
- If subprocess isolation is used: `spawn` context, no shell, kill on timeout, GPU memory reclaimed by SIGKILL, circuit breaker on repeated child death.
- `max_num_pages` and `images_scale` caps bound per-doc compute/memory regardless of input.
- No new external access; no new IAM. Same single-operator GPU instances.

## 8. Failure & Error Scenarios

- **convert() hangs past timeout** → timeout fires (native or subprocess-kill) → `PermanentError('parse_timeout')` → doc errored, worker continues.
- **Native `document_timeout` doesn't break a hang** (spike FAIL) → subprocess design is mandatory; until shipped, the 0056 heartbeat is the backstop (worker relaunches, doc re-errors — degraded but not a dead run, provided the doc is quarantined so it doesn't re-poison).
- **Child process dies repeatedly** → circuit breaker fails the run with a clear exit_reason (ties into 0056's exit_reason).
- **Conditional-OCR misclassifies a scanned doc as text-native** → OCR wrongly skipped → thin extraction. Mitigation: the 0060 pdftotext signal is the primary detector (a scanned doc has near-empty pdftotext), and the A/B scanned-sample gate catches systematic misclassification.
- **A/B shows regression** → the change does not ship; default pipeline stays.

## 9. Traps to Avoid

- Do NOT trust `signal.alarm`/SIGALRM to interrupt a C/CUDA hang — it can't. (Native `document_timeout` or subprocess kill only.)
- Do NOT use a per-doc child process that reloads the model each doc — model load is minutes; use a persistent child if subprocess is needed.
- Do NOT blanket-disable OCR — it destroys scanned-doc tables. Conditional only.
- Do NOT measure A/B quality by cell/row COUNTS or sequence alignment — use normalized cell-content set coverage + the 0057 grounding gate.
- Do NOT remove the 0056 heartbeat — it's the final backstop even with a per-doc timeout.
- Quarantine ≠ delete.

## 10. Open Questions (plan phase)

- **`document_timeout` units & semantics** (ms vs s; raises vs partial result) — resolved by the Phase-0 spike.
- **Default timeout value** — 180s proposed; tune against the parse-duration tail and the heartbeat window.
- **Poison threshold** — `mb_per_page>1.0 AND text<200` (3.77%) proposed; pick after seeing downgrade impact.
- **Is TableFormer FAST already the 2.93 default?** — plan must confirm before claiming a speedup.
- **Conditional-OCR detector** — 0060 pdftotext signal (preferred) vs inline pdfminer; depends on backfill coverage at parse time.
- **A/B harness form** — standalone script vs management command; should it reuse the 0057 gate directly (0057 is shipped, so yes).
