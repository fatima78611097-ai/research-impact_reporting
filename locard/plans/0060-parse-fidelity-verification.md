# Plan 0060 — Parse Fidelity Verification & pdftotext Repair

- **Project:** 0060   **Spec:** `locard/specs/0060-parse-fidelity-verification.md` (specified)
- **Status:** conceived (initial draft)
- **Author:** Architect, 2026-05-30

> Builder-executable plan. pdftotext extraction + fidelity scoring + PdftextSourceProvider + crawler inline hook + backfill batch runner + dashboard integration.

## Key decisions

1. **pdftotext reads from stdin** — `subprocess.run(["/usr/bin/pdftotext", "-layout", "-", "-"], input=pdf_bytes, ...)`. No temp file needed for the inline crawl path. Backfill path uses `NamedTemporaryFile` for S3 downloads.
2. **Inline crawl hook location:** inject in both `crawler.py` (sync) and `async_crawler.py` (async) right after `fetch_pdf.download()` succeeds, before `archive.put()`. The PDF bytes are already in `outcome.body`.
3. **Fidelity scoring is a separate step** from extraction — it requires both pdftotext AND Docling sections to exist. The batch runner computes it for docs that have both; the inline path stores pdftotext text only (Docling hasn't run yet at crawl time).
4. **Word-set tokenizer** reuses 0057's `normalize()` (lowercase → NFC → collapse whitespace) then splits on whitespace, discarding tokens ≤ 1 char. Pure function, shared module.
5. **Dashboard integration** goes on the existing Faithfulness Gate page — a "pdftotext Backfill" section with a run button and progress, plus a "Re-verify with pdftotext" option on the verify form.
6. **DDL is operator-run.** Same pattern as 0057.

## Phase 1 — pdftotext extractor module (pure, no DB)

`lavandula/faithfulness/pdftotext_extract.py`:
- `extract_text(pdf_bytes: bytes) -> ExtractResult` — runs `/usr/bin/pdftotext -layout - -` via subprocess, timeout 30s, stdout cap 10MB, strips NUL bytes, returns `ExtractResult(text, version, char_count, is_scanned)`.
- `is_scanned` = True when output is empty or <50 chars after whitespace strip.
- `get_pdftotext_version() -> str` — runs `pdftotext -v`, caches result.
- Fails fast if `/usr/bin/pdftotext` doesn't exist.

**Tests:** empty PDF → scanned; text-native PDF → non-empty text; timeout on hung subprocess; NUL byte stripped; version string parsed.

**Acceptance:** pure module, no DB deps, handles all error cases from spec §8.

## Phase 2 — Fidelity scoring module (pure, no DB)

`lavandula/faithfulness/fidelity.py`:
- `word_set(text: str) -> set[str]` — normalize (lowercase → NFC → collapse ws) → split → discard ≤1 char tokens.
- `coverage(source: set[str], target: set[str]) -> float` — |source ∩ target| / |source|, returns 0.0 if source is empty.
- `score_fidelity(docling_text: str, pdftotext_text: str) -> FidelityScore` — returns `FidelityScore(forward, reverse)`.
- `classify_text_source(extract_result, fidelity_score) -> str` — returns `text_native | scanned | pdftotext_failed` per spec §4.3 rules.

**Tests:** known fixture with pre-computed coverage; empty input → 0.0; identical input → 1.0; scanned classification; pdftotext_failed on low coverage (<0.50 both directions).

**Acceptance:** pure, deterministic, no DB deps.

## Phase 3 — Data model migration (operator-run)

`lavandula/migrations/lava_parse/0060_pdftotext.sql`:
- CREATE TABLE `lava_parse.pdftotext` (content_sha256 TEXT PK, pdftotext_version TEXT NOT NULL, full_text TEXT NOT NULL, char_count INTEGER NOT NULL, extracted_at TIMESTAMPTZ DEFAULT now())
- ALTER TABLE `lava_parse.documents` ADD COLUMN pdftotext_coverage NUMERIC, ADD COLUMN pdftotext_reverse NUMERIC, ADD COLUMN text_source TEXT
- GRANT SELECT, INSERT, UPDATE on new table/columns to `research_app`
- Rollback script included.

**Acceptance:** columns present, existing rows unaffected, backward compatible.

## Phase 4 — PdftextSourceProvider

`lavandula/faithfulness/source_provider.py` — add `PdftextSourceProvider` class alongside existing `DoclingSourceProvider`:
- Implements `SourceTextProvider` protocol (same interface).
- Per-document fallback per the spec's decision table (§4.3).
- Returns pdftotext `full_text` as `section_text`, Docling tables from `lava_parse.tables`, `source = "pdftotext-repaired"`.
- Falls back to `DoclingSourceProvider` when no pdftotext row or not text_native.

**Tests:** all 10 decision-table rows from spec §4.3; certified doc returns pdftotext text; scanned doc falls back to Docling with Tier B; missing pdftotext falls back to Docling; borderline coverage falls back.

**Acceptance:** provider passes the spec's decision table exhaustively.

## Phase 5 — Backfill batch runner

`lavandula/dashboard/pipeline/management/commands/extract_pdftotext.py`:
- Args: `[--run-tag TAG] [--limit N] [--sha SHA] [--force] [--force --version-mismatch]`
- Query docs in `lava_parse.documents` not yet in `lava_parse.pdftotext` (or all with `--force`).
- For each: download PDF from S3 → `extract_text()` → INSERT into `lava_parse.pdftotext` → if Docling sections exist, `score_fidelity()` → UPDATE `lava_parse.documents` with coverage + text_source.
- Atomic per-document (single transaction per doc).
- Progress every 100 docs. Resumable. Failures logged and skipped.
- `--force` overwrites existing rows. `--version-mismatch` only re-extracts where stored version ≠ current.
- Isolated TMPDIR (`/tmp/pdftotext-0060/`) for subprocess temp files.

**Tests:** resumable (skip existing); force overwrites; version-mismatch filter; S3 failure skipped; progress callback fires.

**Acceptance:** backfill runner processes all target docs, atomic per-doc, handles all failure modes.

## Phase 6 — Crawler inline hook

Inject pdftotext extraction into both crawlers, right after `fetch_pdf.download()` succeeds:

**`lavandula/reports/crawler.py`** (sync, ~line 350):
```python
# After outcome = fetch_pdf.download(...)
if outcome.status == "ok" and outcome.body:
    from lavandula.faithfulness.pdftotext_extract import extract_text
    extract_result = extract_text(outcome.body)
    # Store in lava_parse.pdftotext (fire-and-forget, don't block crawl on DB failure)
```

**`lavandula/reports/async_crawler.py`** (async, ~line 440):
```python
# Same, wrapped in loop.run_in_executor() for the subprocess call
```

pdftotext stdin accepts bytes directly — no temp file. The DB write is best-effort (log and continue if it fails, backfill catches it later).

**Tests:** mock subprocess, verify extract called during crawl; DB failure doesn't block crawl.

**Acceptance:** new crawled docs get pdftotext text stored automatically.

## Phase 7 — Dashboard integration

Add to the existing Faithfulness Gate page (`faithfulness.html` + `views.py`):
- **Backfill section:** "pdftotext Extraction" card with doc count (extracted / total parsed), "Run Backfill" button, progress display.
- **Re-verify option:** dropdown or toggle on the verify form to select `PdftextSourceProvider` instead of `DoclingSourceProvider`.
- Status partial endpoint for backfill progress polling.

**Tests:** view renders, backfill button triggers runner, re-verify with pdftotext option works.

**Acceptance:** operator can trigger backfill and re-verify from the dashboard without CLI.

## Phase 8 — End-to-end validation on p20-v1

1. Run backfill over the 3,242 p20-v1 docs.
2. Run `verify_faithfulness` with `PdftextSourceProvider`.
3. Compare results to the Docling-source run (82% Tier A).
4. Verify ≥ 95% Tier A. Verify no regressions (Docling-verified metrics stay verified).
5. Document results in `locard/operations/0060-validation.md`.

**Acceptance:** spec AC #5 (≥ 95% Tier A) and AC #6 (no regressions) met.

## Build sequence

Phase 1 (extractor) → Phase 2 (fidelity) → Phase 3 (migration, hand to operator) → Phase 4 (provider) → Phase 5 (backfill runner) → Phase 6 (crawler hook) → Phase 7 (dashboard) → Phase 8 (validation). Phases 1 and 2 can proceed in parallel.

## Dependencies & handoffs

- **0057 (faithfulness gate):** already shipped. 0060 plugs in via `SourceTextProvider`.
- **Operator:** applies Phase 3 migration SQL.
- **Crawler modification (Phase 6):** touches `crawler.py` and `async_crawler.py` — coordinate if a crawl is running.

## Risks / traps

- Do NOT use sequence alignment for fidelity scoring.
- Do NOT replace Docling tables — always use Docling tables for R2.
- The inline crawl hook must be fire-and-forget — never block the crawl on a pdftotext failure.
- pdftotext stdin mode (`-layout - -`) must be verified to work with poppler 24.02.0 on cloud2.
