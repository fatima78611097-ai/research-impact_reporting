# Plan 0050: Metric Extraction with Context Snippets

**Spec**: `locard/specs/0050-metric-extraction-context.md`
**Protocol**: SPIDER
**Estimated phases**: 4 — Schema (A), Core Library (B), Management Command (C), Tests (D)

## Overview

Build a Django management command `extract_metrics` that scans `lava_parse.sections` and `lava_parse.tables` for numeric values co-occurring with archetype signature vocabulary, then writes (term, number, snippet) tuples to a new `lava_vocab.metric_observations` table. The command follows the same patterns as `extract_terms`: advisory lock, cursor-based resume, batch writes, progress logging. No spaCy model load — sentence splitting and number detection are regex-only. Memory budget: < 300MB peak RSS.

## Dependencies (pip)

No new dependencies. Uses only:
- `sqlalchemy` (already installed)
- `re` (stdlib)
- Standard Django management command infrastructure

## Phase A: Schema Migration

### A1: Migration file

**File**: `lavandula/migrations/lava_vocab/003_metric_observations.sql`

Create the `metric_observations` table exactly as specified:

```sql
CREATE TABLE IF NOT EXISTS lava_vocab.metric_observations (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    term TEXT NOT NULL,
    numeric_value TEXT NOT NULL,
    numeric_parsed REAL,
    unit_hint TEXT,
    snippet TEXT NOT NULL,
    snippet_heading TEXT,
    section_index INT,
    source_type TEXT NOT NULL DEFAULT 'narrative',
    archetype_id INT REFERENCES lava_vocab.archetypes(id),
    confidence TEXT NOT NULL DEFAULT 'medium',
    UNIQUE(run_id, content_sha256, term, numeric_value, section_index)
);
```

Indexes (exact DDL):

```sql
CREATE INDEX IF NOT EXISTS idx_metric_ein ON lava_vocab.metric_observations(source_org_ein);
CREATE INDEX IF NOT EXISTS idx_metric_term ON lava_vocab.metric_observations(term);
CREATE INDEX IF NOT EXISTS idx_metric_run ON lava_vocab.metric_observations(run_id);
CREATE INDEX IF NOT EXISTS idx_metric_archetype ON lava_vocab.metric_observations(archetype_id);
```

Grants (exact DDL):

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON lava_vocab.metric_observations TO research_app;
GRANT USAGE, SELECT ON lava_vocab.metric_observations_id_seq TO research_app;
```

Check existing migration numbering — `001_create_schema.sql` and `002_*.sql` may exist.

### A2: Apply migration

Run the migration manually via `psql` (same as prior migrations). Verify table exists and grants are correct by querying `\dp lava_vocab.metric_observations`.

## Phase B: Core Library — `lavandula/nlp/metrics.py`

This is the extraction logic, separated from the management command for testability. All functions are pure or take an engine parameter — no Django dependencies.

### B1: Number detection — `detect_numbers(text) -> list[NumberMatch]`

**File**: `lavandula/nlp/metrics.py`

Returns a list of `NumberMatch` namedtuples: `(raw: str, parsed: float|None, unit_hint: str|None, start: int, end: int)`

Regex patterns applied in order (first match wins per position):
1. Percentages: `(\d+(?:\.\d+)?)%`
2. Currency: `\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s?([KMBkmb])?`
3. Integers with commas: `\b(\d{1,3}(?:,\d{3})+)\b`
4. Plain integers 3+ digits: `\b(\d{3,})\b`

Exclusion patterns applied before number detection:
- Year filter: 4-digit numbers 1900-2099 not preceded by `$` or followed by `%` → skip
- Phone: `\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}` → mask out matched spans
- Date: `\d{1,2}/\d{1,2}/\d{2,4}` → mask out matched spans
- Page ref: `(?:page|section|fig(?:ure)?)\s+\d+` (case-insensitive) → mask out matched spans
- ZIP: `[A-Z]{2}\s+\d{5}` → mask out matched spans

Implementation approach: first find all exclusion spans, then find all number matches, then remove any number match whose span overlaps an exclusion span.

`numeric_parsed` rules:
- Strip commas, parse float
- `%` suffix: parse number before it (92% → 92.0)
- `$` prefix with K/M/B: multiply accordingly ($1.2M → 1200000.0)
- Any ambiguity (ranges, ordinals, ratios): return None

`unit_hint` rules:
- `%` → `"percent"`
- `$` → `"currency"`
- Otherwise → `"count"`
- If parsed is None → None

### B2: Sentence splitting — `split_sentences(text) -> list[str]`

**File**: `lavandula/nlp/metrics.py`

Regex-based sentence splitting — NO spaCy model load. Split on:
- `. ` followed by uppercase letter: `(?<=[.!?])\s+(?=[A-Z])`
- Bullet/list markers: `\n\s*[-•]\s+` or `\n\s*\d+[.)]\s+`

Return list of sentence/fragment tuples with metadata for source_type classification.

Signature: `split_sentences(text: str) -> list[SentenceFragment]`

`SentenceFragment` is a namedtuple: `(text: str, start: int, end: int, is_bullet: bool)`

Detection of `is_bullet`:
- A fragment is classified as a bullet if it was split on a bullet/list marker (`\n\s*[-•]\s+` or `\n\s*\d+[.)]\s+`)
- This drives `source_type='bullet'` in the observation (vs `'narrative'` for normal sentences)

### B3: Snippet construction — `build_snippet(sentence, heading, next_sentence) -> str`

**File**: `lavandula/nlp/metrics.py`

Rules from spec:
1. If `len(sentence) >= 80`: return `sentence[:500]` (truncate with `…` if > 500)
2. If `len(sentence) < 80` and heading: prepend `f"[{heading}] — "`, append up to 150 chars of next_sentence
3. Hard cap at 500 chars, truncate with `…`

For table cells: separate function `build_table_snippet(heading, row_label, cell_value) -> str`
- Returns `f"[{heading}] — {row_label}: {cell_value}"`[:500]

### B4: Term matching — `match_terms(sentence_lower, term_set) -> list[str]`

**File**: `lavandula/nlp/metrics.py`

For each term in `term_set`, check if `term in sentence_lower`. Return list of matching terms.

Terms are already canonicalized (lowercase, lemmatized, space-joined) from Spec 0049. Sentence text is lowercased before matching. This is a simple `O(n*m)` substring check where n=number of terms (~2000) and m=avg term length. For ~2000 terms this is fast enough per sentence.

### B5: Heading classifier — `classify_heading(heading) -> str`

**File**: `lavandula/nlp/metrics.py`

Returns one of: `"skip"`, `"priority"`, `"neutral"`

Block list (case-insensitive substring match → `"skip"`):
- `"auditor"`, `"financial statement"`, `"balance sheet"`, `"form 990"`
- `"board of directors"`, `"staff list"`, `"acknowledgment"`
- `"table of contents"`, `"notes to financial"`, `"independent auditor"`
- `"statement of activities"`, `"statement of position"`

Priority list (case-insensitive substring match → `"priority"`):
- `"program"`, `"impact"`, `"outcome"`, `"achievement"`, `"result"`
- `"service"`, `"community"`, `"client"`, `"participant"`, `"success"`

If heading is None or empty → `"neutral"`

### B6: Table extraction — `extract_table_metrics(data_json, heading, term_set) -> list[dict]`

**File**: `lavandula/nlp/metrics.py`

Process a single table's `data_json` (JSONB from `lava_parse.tables`):

1. Parse `data_json` — it's a list of row arrays. First row is typically headers (column headers).
2. Count numeric cells across all rows (excluding header). If > 50% of all data cells are numeric → skip (financial statement).
3. Extract column headers from first row.
4. For each data row (not header):
   - Row label = first column value (may be empty)
   - For each cell after the first:
     - Run `detect_numbers(cell_value)`
     - If numbers found:
       - **Term source**: use row_label if non-empty, otherwise fall back to the column header for that cell's column index (per spec: "row label or column header if row label is empty")
       - Match the term source (lowercased) against term_set using same substring rule
       - If matched: build observation dict with `source_type='table'`, `confidence='high'`
       - Snippet: `build_table_snippet(heading, term_source, cell_value)`

Returns list of observation dicts ready for DB insert.

### B7: Document processor — `process_document(engine, run_id, sha, ein, term_set, archetype_id) -> list[dict]`

**File**: `lavandula/nlp/metrics.py`

Orchestrates extraction for one document:

1. Fetch sections from `lava_parse.sections` for this SHA
2. Fetch tables from `lava_parse.tables` for this SHA (joined to sections via `section_id`)
3. For each section:
   a. `classify_heading(heading)` → skip if `"skip"`
   b. Process narrative text:
      - `split_sentences(body_text)` → list of SentenceFragment
      - For each fragment: `detect_numbers(fragment.text)` → if any numbers found:
        - `matched_terms = match_terms(fragment.text.lower(), term_set)`
        - Determine `source_type`: `'bullet'` if `fragment.is_bullet`, else `'narrative'`
        - **Confidence assignment** (deterministic branching):
          1. If `matched_terms` is non-empty → `confidence='high'` (term + number in same sentence)
          2. If `matched_terms` is empty BUT `match_terms(heading.lower(), term_set)` is non-empty → `confidence='medium'` (number in sentence, term in section heading). Use heading-matched terms as the `term` value.
          3. If both empty BUT the heading itself matches the term set → `confidence='low'` (heading matches but term not in body). Use heading-matched terms. **Skip this level by default** — only emit if a `--include-low-confidence` flag is set.
        - For each (term, number) pair from the above: build snippet, emit observation dict
   c. Process tables belonging to this section:
      - `extract_table_metrics(data_json, heading, term_set)`
4. Return all observation dicts (do NOT write to DB here — caller handles that)

Memory note: sections are fetched per-document, processed, then released. No accumulation across documents.

## Phase C: Management Command — `extract_metrics`

### C1: Command skeleton

**File**: `lavandula/dashboard/pipeline/management/commands/extract_metrics.py`

Follow `extract_terms.py` patterns:
- `add_arguments`: `run_tag`, `--ntee` (default P2%), `--batch-size` (default 50), `--resume`, `--dry-run`
- Advisory lock via `pg_try_advisory_lock(hashtext(:key))`
- SIGINT handler for graceful shutdown
- Progress logging per batch

Key differences from `extract_terms`:
- **No ProcessPoolExecutor** — no spaCy model to parallelize. Single-threaded is fine for regex-only work.
- **No workers argument** — single process
- **Reuses existing extraction_runs** — uses the same `run_tag` from the 0049 extract_terms run (metric extraction is a follow-on step, not a separate run). Look up the run by tag, verify it exists.
- **Extractor version**: `"0050-v1"`

### C2: Term set loading

At startup (once per run, not per document):

1. Query `lava_vocab.archetypes` + `lava_vocab.archetype_members` for the target NTEE vertical
2. For each archetype: compute lift per term via existing `compute_lift_per_term()` → collect terms with lift > 2.0
3. Query top 500 TF-IDF terms for the vertical from `lava_vocab.tfidf_scores` as fallback
4. Union and deduplicate into a Python `set[str]` (~2000 terms, negligible memory)
5. Build archetype-to-EIN mapping for `archetype_id` lookups

### C3: Document iteration

Cursor-based pagination through `lava_parse.documents`:

```sql
SELECT d.content_sha256, d.source_org_ein
FROM lava_parse.documents d
JOIN lava_corpus.corpus c ON d.content_sha256 = c.content_sha256
JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
WHERE ns.ntee_code LIKE :ntee
  AND c.material_type = ANY(:materials)
  AND d.error IS NULL
  AND d.content_sha256 > :cursor
ORDER BY d.content_sha256
LIMIT :batch_size
```

For each document:
1. Look up archetype_id from EIN (via archetype_members mapping loaded in C2)
2. Build document-specific term set (archetype terms + fallback TF-IDF terms)
3. Call `process_document()` from Phase B
4. Write observations to `lava_vocab.metric_observations` in a single transaction per document
5. Update cursor in `extraction_runs.config_json`

### C4: Observation writing

Per-document batch insert using `ON CONFLICT ... DO NOTHING` on the UNIQUE constraint:

```sql
INSERT INTO lava_vocab.metric_observations
    (run_id, content_sha256, source_org_ein, term, numeric_value,
     numeric_parsed, unit_hint, snippet, snippet_heading, section_index,
     source_type, archetype_id, confidence)
VALUES (:run_id, :sha, :ein, :term, :numeric_value,
        :numeric_parsed, :unit_hint, :snippet, :snippet_heading, :section_index,
        :source_type, :archetype_id, :confidence)
ON CONFLICT (run_id, content_sha256, term, numeric_value, section_index)
DO NOTHING
```

### C5: Progress and completion

- Log every batch: docs processed, observations extracted, rate
- On completion: update `extraction_runs.finished_at` and `stats_json`
- Track peak RSS via `/proc/self/status` VmHWM — log warning if > 400MB

### C6: Resume support

Resume uses a **dedicated cursor key** in `extraction_runs.config_json` — `"metrics_cursor"` — separate from the terms extraction cursor (`"cursor"`). This avoids conflicting with extract_terms state.

On `--resume`:
1. Look up existing run by tag, verify it exists
2. Read `metrics_cursor` from `config_json` (default: empty string if not set)
3. The document iteration query in C3 uses `AND d.content_sha256 > :cursor` — this is the sole resume mechanism
4. After each document is written to `metric_observations`, update `metrics_cursor` in `config_json`

No `doc_extractions` table is used by this command — that table belongs to extract_terms. Resume state is entirely cursor-based.

## Phase D: Tests

### D1: Unit tests — `lavandula/nlp/tests/test_metrics.py`

Test functions from Phase B:

1. **test_detect_numbers**: All fixtures from spec T1 (percentages, currency, commas, plain integers, exclusions for years, phones, dates, page refs, ZIPs)
2. **test_split_sentences**: Basic sentence splitting, bullet points (`is_bullet=True`), short fragments, numbered lists
3. **test_build_snippet**: All four cases from spec T2 (long, short, table, truncation at 500 chars)
4. **test_match_terms**: Cases from spec T3 (multi-word match, non-match, multiple terms/numbers)
5. **test_classify_heading**: Block list, priority list, neutral, None/empty
6. **test_extract_table_metrics**: Table with program data, financial table (>50% numeric) skipped, column-header fallback when row label is empty
7. **test_bullet_source_type**: Verify that bullet-detected fragments produce observations with `source_type='bullet'`, non-bullet fragments produce `source_type='narrative'`
8. **test_confidence_levels**: Explicit test for all three confidence branches:
   - Term + number in same sentence → `'high'`
   - Number in sentence, term only in heading → `'medium'`
   - Heading matches term, term not in body → `'low'`

### D2: Integration test — `lavandula/nlp/tests/test_metrics_integration.py`

Requires database access (run against real lava_parse data):

1. Select 5 Head Start EINs from `archetype_members`
2. Run `process_document()` on each
3. Verify spec T4 criteria (observations produced, term matches, snippet bounds, confidence values)

### D3: Memory monitoring

Add RSS tracking to the management command (not a separate test file). Log VmHWM at startup and every 100 documents. Assert < 500MB in integration test if run on full P20.

## File Summary

| File | Purpose |
|------|---------|
| `lavandula/migrations/lava_vocab/003_metric_observations.sql` | Schema migration |
| `lavandula/nlp/metrics.py` | Core extraction library (numbers, sentences, snippets, terms, tables) |
| `lavandula/dashboard/pipeline/management/commands/extract_metrics.py` | Django management command |
| `lavandula/nlp/tests/test_metrics.py` | Unit tests |
| `lavandula/nlp/tests/test_metrics_integration.py` | Integration tests |

## Build Order & Dependencies

```
A (Schema) ──→ B (Core Library) ──→ C (Management Command) ──→ D (Tests)
                                         ↑
                                    B can be built independently
                                    and unit-tested before C
```

Phase B has no database dependency for unit testing. Phase C requires A (table must exist) and B (library functions). Phase D spans both unit (B only) and integration (A + B + C).

## Acceptance Criteria Mapping

| AC | Phase | Verification |
|----|-------|-------------|
| Metric observations extracted | C3-C4 | Integration test T4 |
| Context snippets preserved | B3 | Unit test T2 |
| Provenance recorded | C4 | Check all columns populated in T4 |
| Archetype vocabulary linked | C2 | Term set includes lift > 2.0 terms |
| Stored in lava_vocab | A1 | Migration creates table |
| No OOM on t3.large | C5 | Memory monitoring, T6 |
| Number detection accuracy | B1 | Unit test T1 |
| Heading filter | B5 | Unit test T5 |
| Table extraction | B6 | Unit test T8 |
| Resume works | C6 | Manual test T7 |

## Operational Notes

- **Host**: t3.large (7.6GB RAM). Peak RSS must stay under 500MB.
- **No spaCy model load**: This is the single most important memory decision. Sentence splitting is regex-only.
- **Single-threaded**: No ProcessPoolExecutor. Regex-based extraction is fast enough without parallelism and avoids memory multiplication.
- **Run time estimate**: ~3,300 P20 documents, regex processing ~50 docs/sec → ~1 minute total. Very fast compared to extract_terms (which loaded spaCy).
- **Prerequisite**: extract_terms and discover_archetypes must have completed for the same run_tag. The command should verify this at startup.

## Traps to Avoid

1. **DO NOT load spaCy.** The spec says "regex fallback" but we are making regex the primary and only approach. spaCy adds 800MB and is not needed for sentence splitting.
2. **DO NOT accumulate observations in memory across documents.** Write per-document, release.
3. **DO NOT parse numeric_value aggressively.** NULL is preferred over wrong. Store raw text always.
4. **DO NOT skip neutral headings.** Only block-listed headings are skipped. Everything else gets processed.
5. **DO NOT use ProcessPoolExecutor.** Single-threaded regex is fast and memory-safe. No parallelism needed.
6. **DO NOT create a new extraction_run.** Reuse the existing run_tag from extract_terms — this is a follow-on step in the same pipeline.

## Consultation Log

**Round 1 (2026-05-26)**:
- **Gemini**: APPROVE (HIGH confidence). No key issues.
- **Codex**: REQUEST_CHANGES (HIGH confidence). 6 issues:
  1. Schema DDL incomplete (indexes/grants not spelled out) → Fixed: added exact DDL
  2. `source_type='bullet'` never defined → Fixed: added `is_bullet` to SentenceFragment, drives source_type
  3. Confidence branching underspecified → Fixed: added deterministic 3-branch logic in B7
  4. Table term matching missing column-header fallback → Fixed: added fallback per spec
  5. Resume strategy muddled → Fixed: clarified `metrics_cursor` key, no doc_extractions
  6. Test gaps (bullet, confidence, column-header) → Fixed: added tests 7 and 8 in D1
