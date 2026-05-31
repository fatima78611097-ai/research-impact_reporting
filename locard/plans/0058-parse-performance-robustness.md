# Plan 0058 — Parse Performance & Robustness

- **Project:** 0058   **Spec:** `locard/specs/0058-parse-performance-robustness.md` (specified)
- **Status:** conceived (initial draft)
- **Author:** Architect, 2026-05-31

> Builder-executable plan. Phase 0 is a SPIKE that gates the architecture (native timeout vs subprocess). Everything else is sequenced behind it. Worker-side changes require a tarball rebuild (bundles the already-committed NUL `_scrub` fix) + operator deploy. Quality A/B + integration tests are operator-run before the tarball ships to a full run.

## Key decisions (frozen from spec + research)
1. **Phase 0 spike decides §3.2 vs §3.3.** Do not build the subprocess machinery until the spike shows native `document_timeout` is insufficient OR peak memory shows OOM risk. The spike is throwaway code with a written PASS/FAIL note.
2. **Absolute ceilings are always-on**, independent of the spike and the triage heuristic: `max_num_pages` cap on every `convert()`, `images_scale` ceiling, and the `file_size>50MB` / `page_count>200` downgrade triggers. These ship regardless.
3. **Conditional-OCR detector reuses 0060's `pdftotext` signal** (already in `lava_parse.pdftotext` + `lava_parse.documents.text_source`) as primary; pdfminer/`first_page_text` fallback; default keep-OCR-on when uncertain.
4. **Timeout/OOM map to the existing PermanentError contract** (`parse_timeout` / `parse_oom`) — no new error-routing, reuse `_record_error` + `complete_work_item`.
5. **Quality A/B calls the real shipped 0057 gate** (not a mock) — 0057 is committed, so the harness imports `lavandula.faithfulness`.
6. **DDL is operator-run** (parse_outcome/docling_convert_ms columns + quarantine flag).
7. **NUL `_scrub` fix already committed** in chunking.py — it rides this spec's tarball; no separate deploy.

## Phase 0 — SPIKE: timeout mechanism + memory profile (REQUIRED FIRST, gates design)

`locard/spikes/0058/` (throwaway). On a g6 GPU instance:
1. Pull the poison doc `e038a9e75317ff86` + the §1.3 extremes (`de8fecc5…` 646s, `658d9b89…` 46.9 MB/page) from S3.
2. Build a `DocumentConverter` with `PdfPipelineOptions(document_timeout=T)` (T candidate 60s) and run the poison doc **≥3 times**. Record: wall-clock vs T, exception type, whether control returns within `T+30s`, whether the **next** doc parses cleanly after (GPU state intact), and whether any threads/GPU memory leak.
3. **Measure peak host + GPU memory** (`nvidia-smi`, `resource.getrusage`/`/proc`) on the poison doc and the 46–49 MB/page extremes.
4. Document the timeout's **nature**: wall-clock vs CPU-time, raises-vs-partial, interrupts-native-code, synchronous-cleanup.

**Output / gate (`locard/spikes/0058/RESULTS.md`):**
- `document_timeout` meets all §3.0 PASS criteria → **Phase 2 = native timeout (§3.2)**.
- Fails any → **Phase 2 = subprocess isolation (§3.3)**.
- Peak memory near instance limit → **subprocess + RLIMIT_AS is mandatory regardless** of the timeout verdict.

**Check in with the architect/operator on the spike result before building Phase 2.**

## Phase 1 — Pipeline options + triage + absolute ceilings (chunking.py)

Rewrite `chunking.parse_pdf()` to accept explicit options and build a configured `DocumentConverter`:
- `PdfPipelineOptions`: `do_ocr` (from detector), `table_structure_options=TableStructureOptions(mode=TableFormerMode.FAST, do_cell_matching=True)`, `images_scale` (config ceiling), `generate_page_images=False`, `document_timeout` (set in Phase 2 if native path).
- **First confirm** whether 2.93 already defaults TableFormer to FAST or ACCURATE (spec open question) — measure before claiming a speedup.
- `convert(..., max_num_pages=N)` absolute page ceiling on every doc.

Add to `chunking.py` (pure, unit-testable):
- `compute_triage(file_size_bytes, page_count, text_signal) -> {downgrade: bool, reasons: [...]}` implementing §3.1: `mb_per_page>1.0 AND text<200`, plus absolute `file_size>50MB` / `page_count>200`.
- `should_skip_ocr(sha, engine) -> bool` implementing §3.4 detector precedence (0060 pdftotext `text_source`/`char_count` → pdfminer/`first_page_text` → default keep-OCR).

`config.py`: add `PARSE_TIMEOUT_SECONDS=180`, `IMAGES_SCALE_CAP`, `MAX_NUM_PAGES`, `POISON_MB_PER_PAGE=1.0`, `POISON_TEXT_FLOOR=200`, `OCR_TEXT_FLOOR=200`, `ABS_FILE_SIZE_CAP=50_000_000`, `ABS_PAGE_CAP=200`.

**Tests (unit, CI):** triage math on the §1.3 example shas; `should_skip_ocr` returns skip for text-native signal, keep for thin/empty/disagree; options builder produces FAST + capped scale + correct OCR flag.

**Acceptance:** pure functions deterministic; converter built with explicit bounded options; no behavior depends on the spike yet.

## Phase 2 — Per-doc hang/OOM guard (BRANCH on Phase 0)

**If spike PASS (native):** set `document_timeout` in the Phase-1 options. `chunking.parse_pdf` catches Docling's timeout exception → raises `DoclingParseError`. Done — minimal code.

**If spike FAIL (subprocess):** build `chunking.PersistentParseChild`:
- `multiprocessing.get_context("spawn")`, one child per worker, model loaded once.
- Child loop: receive `(pdf_path, options)` → run `convert` + `extract_sections`/`extract_tables`/`get_document_metadata` **inside the child** → return the small JSON-serializable dicts (NOT the DoclingDocument).
- Child has `resource.setrlimit(RLIMIT_AS, cap)` so an OOM kills the child → parent sees exit → `parse_oom`.
- Parent `result_queue.get(timeout=T)`; on `queue.Empty` → `terminate()`→`kill()`→respawn → raise `DoclingParseTimeout`.
- Circuit breaker: abort run on N consecutive OR M-in-W windowed child deaths (config).

**Tests:** native path — timeout option set + exception mapped (mock); subprocess path — timeout→respawn, OOM→parse_oom, child returns dicts, windowed+consecutive breaker (mocked multiprocessing).

**Acceptance:** the poison doc cannot wedge the worker beyond `T + kill margin` (proven in Phase 6 integration test).

## Phase 3 — Error contract + observability (worker.py, db.py, migration)

- `worker._process_one`: catch `DoclingParseError`/`DoclingParseTimeout`/OOM → `PermanentError('parse_timeout'|'parse_oom')`; thread the triage `downgrade` flag and `parse_outcome` through to the document row. Record `docling_convert_ms` separately from total `parse_duration_ms`.
- `db.py`: extend the document insert to write `docling_convert_ms`, `parse_outcome` (`ok|timeout|downgraded|error`). Reuse existing `_record_error`/`complete_work_item` flow (no new claim logic).
- **Migration** `lavandula/migrations/parse/0058_parse_outcome.sql` (operator-run): add nullable `docling_convert_ms INTEGER`, `parse_outcome TEXT` to `lava_parse.documents`; grants. Rollback included.

**Tests:** `parse_timeout`/`parse_oom` route through `_record_error`+`complete_work_item` (no stranded claim); `parse_outcome` set correctly for ok/downgraded/timeout.

**Acceptance:** every doc gets a `parse_outcome`; timed-out/OOM docs errored cleanly, worker continues.

## Phase 4 — Quarantine (DB-persisted, governed)

- Migration (same file or sibling): quarantine flag — plan chooses `lava_parse.parse_blocklist(content_sha256 PK, reason, quarantined_at, quarantined_by, evidence_json)` (cleaner than a corpus column; corpus is shared).
- `populate_work_queue` excludes blocklisted shas at enqueue.
- `db.quarantine_doc(sha, reason, by, evidence)` / `db.unquarantine_doc(sha)` — audited, reversible.
- Flag `e038a9e75317ff86` now (operator inserts the row, or a one-shot management command).
- Auto-quarantine is OFF by default; if enabled, requires the configured threshold (≥3 timeouts/sha across runs) and writes the full audit row.

**Tests:** blocklisted sha excluded from enqueue; quarantine/unquarantine round-trip; audit fields populated.

**Acceptance:** known poison doc never enters a work queue; quarantine is reversible + audited; default operator-confirmed.

## Phase 5 — Quality A/B harness + run it

`lavandula/parse/ab_quality.py` (or a management command `parse_ab_quality`):
- Input: a stratified sample (table-heavy, scanned, designed/image-heavy, plus poison + slow examples), reflecting the ~99% text-native / ~1% scanned skew.
- For each doc: parse under Variant A (current defaults) and Variant B (FAST + capped images_scale + conditional OCR + timeout); record `docling_convert_ms`, table cell sets, markdown.
- Compare with the spec §4 metrics: normalized cell-content **set coverage** (bidirectional, NOT sequence alignment), row preservation (Jaccard), OCR word-set coverage on scanned, and **run the real shipped 0057 gate** on both variants' markdown → compare Tier-A vs Tier-C.
- Emit a results table + PASS/CONDITIONAL/FAIL against the frozen thresholds (text-native cell parity ≥0.9999, zero lost rows, zero grounding regression; scanned ≥0.95; poison completes within timeout).

**Deliverable:** `locard/operations/0058-ab-results.md`. **Acceptance:** Variant B passes the frozen gates on the sample, OR the failing knob is reverted and re-tested.

## Phase 6 — Worker tarball rebuild + deploy + smoke test

1. Rebuild `worker-code.tar.gz` (bundles the committed NUL `_scrub` fix + all Phase 1-4 worker changes). Back up the current tarball to `deploy/backups/`.
2. **Operator applies the Phase 3/4 migrations FIRST** (parse_outcome columns + blocklist table), then deploys the tarball — migration-before-tarball gate, same as 0056.
3. Re-run the run-32 smoke test (283 P-cleanup docs) on the new tarball. Expect: poison doc errors cleanly (no hang), workers make progress, run completes or ends with honest per-doc outcomes.

**Acceptance:** smoke test completes without a hang; `e038a9e75317ff86` recorded `parse_timeout`/`parse_oom`/`downgraded`, not a wedge; the NUL doc `359f55959df4e637` parses successfully (NUL fix confirmed in production).

## Phase 7 — Dashboard surface

- Parse progress partial: per-run `parse_outcome` rollup (ok/downgraded/timeout/error counts).
- Quarantine review surface: list blocklisted docs with reason/who/when + an un-quarantine action.

**Acceptance:** operator can see downgrade/timeout rates and manage quarantine from the dashboard.

## Build sequence

**Phase 0 (spike) → check in → Phase 1 (options/triage, parallel-safe) → Phase 2 (branch on spike) → Phase 3 (error/observability) + Phase 4 (quarantine) → Phase 5 (A/B, operator-run) → Phase 6 (tarball + migration + smoke) → Phase 7 (dashboard).**

Phase 1 can start before the spike finishes (the always-on caps + triage + detector don't depend on the timeout mechanism). Phase 2 is the only spike-gated phase.

## Dependencies & handoffs

- **0060 pdftotext signal** feeds the conditional-OCR detector — backfill should be substantially complete (currently ~78%; finish it before the A/B so the detector has data).
- **0057 gate** is the A/B quality oracle (shipped).
- **Operator:** runs the Phase 0 spike on a GPU instance, applies migrations, deploys the tarball (migration-first), runs the A/B + smoke test.
- **0056 heartbeat** remains the final backstop.

## Risks / traps
- Don't build the subprocess machinery before the spike says you must.
- Absolute ceilings ship regardless of the spike — they're the floor of defense.
- Conditional OCR: never blanket-off; default keep-on when the signal is ambiguous.
- A/B uses set coverage, not edit-distance (0060 lesson).
- Migration before tarball, never deploy mid-run.
- Quarantine is audited + reversible — never silent.
