# Spec 0038: Classifier Pipeline Rebuild

**Status**: Draft (with Codex review + red team)
**Author**: Architect
**Date**: 2026-05-11
**Dependencies**: 0035 (multi-page extraction), 0025 (definition-driven classifier)

## Problem Statement

The Spec 0035 classifier pipeline (`reclassify_corpus`) has fundamental design and implementation failures that make it untrustworthy for production use:

1. **Silent fallback to bad data**: LEFT JOINs to `classification_context` and silently falls back to `first_page_text` when extraction hasn't run. The entire point of Spec 0035 was that first-page-only classification produces poor results. The fallback defeats the purpose.

2. **Zero operational visibility**: No progress logging during execution. The only output is a summary printed after completion — which is never seen if the process crashes. A multi-hour job on 186K documents gives no indication it's running, how far along it is, or what its throughput is.

3. **No targeting capability**: The only filtering is `--where` (raw SQL injection) and `--sample` (random). No structured way to classify by state, EIN, organization, or cohort. Testing requires running the full corpus and waiting days.

4. **Fake concurrency**: Accepts `--workers 4` but never uses it. The entire pipeline is single-threaded. LLM calls (the bottleneck) run sequentially.

5. **No quality gates**: Classifies documents with empty/inadequate text by writing `other_collateral` at confidence 0.1. No minimum quality threshold, no skip-and-report option.

6. **Heuristic prefilter on wrong data**: The `insufficient_text` rule evaluates `eval_text` which has already fallen back to `first_page_text`. Documents declared "insufficient" may have rich multi-page content that was never loaded.

7. **No validation workflow**: No built-in way to compare v3 results against the existing 161K Haiku v2 classifications in `corpus.material_type`. The comparison command from Spec 0035 was never implemented.

### What the existing 133-row run actually produced

- **101 docs** classified by DeepSeek Flash using `first_page_text` only (zero had `classification_context` rows)
- **24 docs** declared "insufficient text" by a heuristic looking at first-page text
- **8 docs** caught by regex rules (IRS 990 patterns)
- **0 docs** used multi-page context from `classification_context`

The multi-page extraction infrastructure (Spec 0035 Phase 1) works correctly. The classifier pipeline simply ignores it.

## Goals

1. **Require extraction context**: Classification refuses to process documents without `classification_context` rows. No silent fallback. Docs without extraction are skipped with a clear log message and count.

2. **Real-time progress reporting**: Continuous stdout output showing documents processed, throughput (docs/sec, docs/min), elapsed time, ETA, batch number, error counts, and classification distribution as it builds.

3. **Structured filtering**: First-class `--state`, `--ein`, `--org-id` flags that filter via joins to `org_seed`/`org_provenance`. The `--where` raw SQL option remains for power users but the common cases have proper flags.

4. **Actual concurrent workers**: LLM calls run in parallel via ThreadPoolExecutor. The `--workers` flag controls concurrency and defaults to 4. Each worker processes one document at a time.

5. **Quality gates**: Minimum `pages_text` length threshold (configurable, default 100 chars). Documents below threshold are logged and skipped — not silently classified as `other_collateral`.

6. **Validation comparison**: Built-in `--compare` mode that compares v3 results against the existing `corpus.material_type` Haiku v2 classifications.

## Non-Goals

- Changing the taxonomy or definition file (that's independent)
- Modifying the extraction pipeline (`extract_classification_context` is fine)
- Replacing the rule-based prefilter concept (it's sound; it just needs correct data)
- Real-time/streaming classification of newly crawled docs
- Modifying the `classify_first_page_v3` function or prompt assembly (Spec 0025/0035 — those work correctly when given the right data)

## Technical Design

### Command Interface

```
# Full corpus (with extraction context required)
python3 manage.py reclassify_corpus --run-tag v3.2

# Filter by state
python3 manage.py reclassify_corpus --run-tag v3.2-TX --state TX

# Filter by EIN (single org, ad-hoc testing)
python3 manage.py reclassify_corpus --run-tag test-ein --ein 13-1234567

# Filter by org ID
python3 manage.py reclassify_corpus --run-tag test-org --org-id 42

# Sample (random N docs that HAVE extraction context)
python3 manage.py reclassify_corpus --run-tag v3.2-sample --sample 500

# Control concurrency
python3 manage.py reclassify_corpus --run-tag v3.2 --workers 8

# Control batch size
python3 manage.py reclassify_corpus --run-tag v3.2 --batch-size 200

# Dry run (show what would be classified)
python3 manage.py reclassify_corpus --run-tag v3.2 --state TX --dry-run

# Allow fallback to first_page_text (explicit opt-in, not default)
python3 manage.py reclassify_corpus --run-tag v3.2-legacy --allow-fallback

# Resume interrupted run
python3 manage.py reclassify_corpus --run-tag v3.2 --resume

# Compare results against existing corpus classifications
python3 manage.py compare_classifications --run-tag v3.2
python3 manage.py compare_classifications --run-tag v3.2 --state TX
```

### CLI Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--run-tag` | str | required | Unique tag for this classification run |
| `--state` | str | None | Two-letter state code (joins to `org_seed.state`) |
| `--ein` | str | None | EIN (e.g., "13-1234567") |
| `--org-id` | int | None | org_seed.id |
| `--where` | str | None | Additional SQL WHERE predicate (operator-only, see Safety below) |
| `--sample` | int | None | Random sample size |
| `--workers` | int | 4 | Concurrent LLM workers |
| `--batch-size` | int | 200 | Docs per database fetch |
| `--backend` | choice | deepseek | LLM backend: deepseek, haiku, gemini, claude |
| `--definition` | str | corpus_reports | Classifier definition name |
| `--allow-fallback` | flag | False | Allow first_page_text when no extraction context |
| `--min-text-len` | int | 100 | Minimum pages_text length to classify |
| `--dry-run` | flag | False | Show counts without classifying |
| `--resume` | flag | False | Resume from checkpoint |
| `--quiet` | flag | False | Suppress per-batch progress (keep start/end summary) |

### Data Flow

```
                    ┌─────────────────────┐
                    │  classification_     │
                    │  context             │
                    │  (pages_text, etc.)  │
                    └─────────┬───────────┘
                              │ INNER JOIN (default)
                              │ LEFT JOIN  (--allow-fallback)
┌──────────┐     ┌────────────▼────────────┐
│ org_seed │────▶│   _fetch_batch()         │
│ (state,  │     │   Filters: state/ein/org │
│  ein)    │     └────────────┬─────────────┘
└──────────┘                  │
                              ▼
                    ┌─────────────────────┐
                    │  Quality Gate        │
                    │  len(pages_text)     │
                    │  >= min_text_len?    │
                    └────┬──────────┬──────┘
                    PASS │          │ FAIL
                         ▼          ▼
              ┌──────────────┐  Log + skip
              │ Rule Engine  │  (counted)
              │ (prefilter)  │
              └──┬───────┬───┘
            match│       │no match
                 ▼       ▼
              Write    ┌──────────────────┐
              result   │ ThreadPoolExecutor│
                       │ --workers N       │
                       │ LLM classify      │
                       └────────┬──────────┘
                                ▼
                          Write result
                          Log progress
```

### Query Construction

The `_fetch_batch` query changes from LEFT JOIN to INNER JOIN by default:

```sql
-- Default: require extraction context
SELECT c.content_sha256, c.first_page_text, c.source_url_redacted,
       c.file_size_bytes, c.pdf_creator,
       cc.pages_text, cc.pages_extracted, cc.total_pages
FROM lava_corpus.corpus c
INNER JOIN lava_corpus.classification_context cc
    ON cc.content_sha256 = c.content_sha256
LEFT JOIN lava_corpus.classification_results cr
    ON cr.content_sha256 = c.content_sha256 AND cr.run_id = :run_id
WHERE c.content_type = 'application/pdf'
  AND cr.content_sha256 IS NULL
  AND cc.text_length >= :min_text_len
```

When `--allow-fallback` is passed, revert to LEFT JOIN (the current behavior) with a startup warning:

```
WARNING: --allow-fallback enabled. Documents without extraction context
will be classified using first_page_text only. Results may be poor.
```

#### State/EIN/Org Filtering

State and EIN filters join through `org_provenance` to reach `org_seed`:

```sql
-- --state TX
INNER JOIN lava_corpus.org_provenance op
    ON op.content_sha256 = c.content_sha256
INNER JOIN lava_corpus.org_seed os
    ON os.id = op.org_id
WHERE os.state = :state

-- --ein 13-1234567
INNER JOIN lava_corpus.org_provenance op
    ON op.content_sha256 = c.content_sha256
INNER JOIN lava_corpus.org_seed os
    ON os.id = op.org_id
WHERE os.ein = :ein

-- --org-id 42
INNER JOIN lava_corpus.org_provenance op
    ON op.content_sha256 = c.content_sha256
WHERE op.org_id = :org_id
```

Note: one document can belong to multiple orgs (many-to-many via `org_provenance`). The query wraps the filtered result in a `SELECT DISTINCT c.content_sha256` subquery before the main fetch, so all counts (eligible, dry-run, progress total, ETA denominator) reflect deduplicated document counts, not row counts. The deduplication happens in SQL, not Python.

### Progress Reporting

Every batch prints a progress line to stdout:

```
[12:34:56] Batch 15 | 3,000/42,458 (7.1%) | 12.3 docs/sec | ETA 53m | rules:412 llm:2,501 skip:87 err:0
[12:35:12] Batch 16 | 3,200/42,458 (7.5%) | 12.1 docs/sec | ETA 54m | rules:438 llm:2,668 skip:94 err:0
```

Fields:
- Timestamp
- Batch number
- Docs processed / total eligible (percentage)
- Throughput (docs/sec, averaged over last 5 batches)
- ETA (based on rolling throughput)
- Running totals: rule-matched, LLM-classified, skipped (quality gate), errors

At startup, print a configuration summary:

```
Reclassify corpus (run tag: v3.2-TX)
  Backend: deepseek | Workers: 4 | Batch size: 200
  Definition: corpus_reports v2 (context_mode: multipage)
  Filter: state=TX
  Eligible docs: 8,432 (with extraction context)
  Docs without context (excluded): 1,204
  Extraction coverage: 87.5%
  Require context: YES (use --allow-fallback to override)
```

At completion, print the full summary (same as current, plus classification distribution):

```
Reclassification complete (v3.2-TX)
  Total: 8,432 | Elapsed: 11m 24s | Avg: 12.3 docs/sec
  Rule-matched:    1,204 (14.3%)
  LLM-classified:  6,891 (81.7%)
  Skipped (short):   298 (3.5%)
  LLM errors:        39 (0.5%)

  Distribution:
    annual_report:     2,104 (24.9%)
    not_relevant:      1,876 (22.2%)
    financial_report:    943 (11.2%)
    donor_newsletter:    812 (9.6%)
    ...
```

### Concurrent LLM Workers

Replace the single-threaded loop with a producer-consumer pattern:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {}
    for row in batch:
        # Quality gate
        if len(row["pages_text"]) < min_text_len:
            stats["skipped_short"] += 1
            continue

        # Rule prefilter (fast, run in main thread)
        rule_match = rule_engine.evaluate(...)
        if rule_match:
            write_result(...)
            stats["rule_matched"] += 1
            continue

        # Submit LLM call to thread pool
        fut = pool.submit(_classify_one, client, definition, row)
        futures[fut] = row

    # Collect results
    for fut in as_completed(futures):
        row = futures[fut]
        result = fut.result()
        if result:
            write_result(...)
            stats["llm_classified"] += 1
        else:
            stats["llm_errors"] += 1
```

Rule evaluation stays in the main thread (it's fast — microseconds per doc). Only LLM calls go to the thread pool.

**Thread safety**: Each worker gets its own LLM client instance, created at pool initialization (not shared). The `select_classifier_client()` function returns a new client per call. This avoids any thread-safety assumptions about the underlying SDK.

**Rate limiting**: Per-document backoff handles transient 429s. If the pool experiences 3+ concurrent 429 responses within a 10-second window, the main thread pauses new submissions for 30 seconds (global throttle). This prevents synchronized retry storms. Backoff delays include random jitter (±25%) to desynchronize workers.

### Dry Run Enhancement

Current dry run shows a single count. New dry run shows a useful breakdown:

```
Dry run (v3.2-TX, state=TX):
  Total eligible PDFs:           9,636
  With extraction context:       8,432 (87.5%)
  Without context (excluded):    1,204 (12.5%)
  Context text >= 100 chars:     8,134
  Context text < 100 chars:        298

  Already classified in this run: 0
  Would process: 8,134

  Estimated cost (DeepSeek): ~$1.60  (heuristic: doc_count × $0.00019)
  Estimated time (4 workers):  ~11 minutes  (heuristic: doc_count / 12 docs/sec)
```

Cost and time estimates are rough heuristics based on observed averages (~1,200 input tokens/doc at DeepSeek pricing, ~3 docs/sec/worker). They are labeled as estimates. If no historical data exists for the selected backend, estimates are omitted with "estimate unavailable."

### Comparison Command

New management command `compare_classifications`:

```
python3 manage.py compare_classifications --run-tag v3.2
python3 manage.py compare_classifications --run-tag v3.2 --state TX
python3 manage.py compare_classifications --run-tag v3.2 --show-reasoning
python3 manage.py compare_classifications --run-tag v3.2 --confidence-threshold 0.8
```

Compares `classification_results` (for the given run_tag) against `corpus.material_type` (the Haiku v2 crawl-time classifications):

```
Classification Comparison: v3.2 vs corpus (Haiku v2)
  Docs compared: 8,432

  Agreement: 6,291 (74.6%)
  Disagreement: 2,141 (25.4%)

  Confidence comparison:
    Avg confidence (Haiku v2):  0.87
    Avg confidence (v3.2):      0.94
    v3 confidence >= 0.9:       7,102 (84.2%)
    v2 confidence >= 0.9:       5,841 (69.3%)

  Top disagreements (v2 → v3):
    other_collateral → annual_report:        412
    other_collateral → program_brochure:     287
    financial_report → not_relevant:         198 (990s caught by rules)
    annual_report → impact_report:           156
    impact_report → annual_report:           134
    not_relevant → donor_newsletter:         112
    ...

  Docs where v3 is NOT confident (< 0.8):   412
    These should be manually reviewed.

  By classification source:
    Rule-matched disagreements:  198 (all financial_report → not_relevant)
    LLM disagreements:         1,943
```

The comparison output breaks down disagreements by `classified_by` source (rule vs LLM) so the operator can distinguish rule overrides (expected, deterministic) from classifier drift (needs investigation).

### Watchdog Integration

The stall watchdog (Spec 0036) is wired but the `get_active` lambda is broken (see Spec 0036 analysis). For this rebuild:

- `get_active` returns the number of in-flight LLM futures (not a 0/1 toggle)
- `get_progress` returns `stats["total"]` (same as current)
- This means the watchdog fires when LLM calls are active but progress has stopped — which is the actual stall condition

```python
active_futures = []  # maintained by the executor loop

watchdog = StallWatchdog(
    get_progress=lambda: stats["total"],
    get_active=lambda: len([f for f in active_futures if not f.done()]),
    stall_threshold_sec=600,
)
```

### Error Handling

- **LLM transient errors**: Exponential backoff per doc (2s, 4s, 8s, 16s), max 4 attempts. After 4 failures, write `classified_by='llm:error'` and continue.
- **20 consecutive failures**: Checkpoint and halt. Print clear message with resume instructions.
- **Database errors**: Checkpoint what's done, log error, halt. Don't silently continue with partial writes.
- **Keyboard interrupt (Ctrl+C)**: SIGINT handler sets a shutdown flag. Main thread stops submitting new work to the pool. Completed futures are drained and their results written. The cursor is checkpointed to the highest `content_sha256` that was durably written to `classification_results`. Remaining in-flight futures are cancelled. A partial summary prints showing what was completed. Then exit.

### What This Spec Does NOT Change

- **`classify_first_page_v3` function**: The LLM classification function works correctly when given the right data. No changes needed.
- **`extract_classification_context` command**: The extraction pipeline works correctly. No changes needed.
- **`classification_context` table schema**: No changes.
- **`classification_runs` / `classification_results` table schemas**: No changes.
- **Prefilter rules YAML**: No changes to the rules themselves. The `RuleEngine` code is unchanged — it just receives `pages_text` instead of a fallback value.
- **Definition files**: No changes.

### What This Spec Replaces

The entire `reclassify_corpus.py` management command is rewritten. The replacement keeps the same file path and command name for continuity. The command signature is backward-compatible (all existing flags still work) with new flags added.

A new `compare_classifications.py` management command is added.

## Migration Plan

No schema changes required. All tables from Spec 0035 are already created and correct. The existing 133 rows in `classification_results` (run tag `test-deepseek-20260509`) can remain as historical data.

## Acceptance Criteria

### Extraction Requirement
- AC1: Default mode uses INNER JOIN to `classification_context`. Docs without extraction context are excluded.
- AC2: Startup summary shows count of excluded docs and extraction coverage percentage.
- AC3: `--allow-fallback` reverts to LEFT JOIN with a visible WARNING.
- AC4: Quality gate skips docs where `pages_text` length < `--min-text-len` (default 100). Skipped docs are counted and reported.

### Filtering
- AC5: `--state TX` filters to docs belonging to Texas orgs via `org_provenance` → `org_seed`.
- AC6: `--ein 13-1234567` filters to docs belonging to a specific org.
- AC7: `--org-id 42` filters to docs linked to a specific org_seed row.
- AC8: Filters are combinable (e.g., `--state TX --sample 100` = random 100 from Texas).
- AC9: Duplicate SHA256s from many-to-many org relationships are deduplicated.

### Progress Reporting
- AC10: Startup prints configuration summary including eligible doc count, extraction coverage, filter details.
- AC11: Each batch prints a progress line with timestamp, count/total, percentage, throughput, ETA, and running category totals.
- AC12: Completion prints full summary with elapsed time, throughput, category distribution.
- AC13: `--quiet` suppresses per-batch output but keeps startup and completion summaries.

### Concurrency
- AC14: `--workers N` runs N concurrent LLM calls via ThreadPoolExecutor.
- AC15: Rule evaluation runs in the main thread (not submitted to the pool).
- AC16: Database writes are serialized (not concurrent) to avoid connection pool exhaustion.

### Dry Run
- AC17: `--dry-run` shows eligible count, extraction coverage, quality gate breakdown, estimated cost and time.

### Comparison
- AC18: `compare_classifications` command compares run results against `corpus.material_type`.
- AC19: Shows agreement/disagreement rate, top category changes, confidence comparison.
- AC20: Supports `--state` filter for scoped comparison.

### Resumability
- AC21: `--resume` reads cursor from the most recent incomplete run with the same tag and continues.
- AC22: Ctrl+C triggers a clean checkpoint before exit.

### Watchdog
- AC23: `get_active` returns count of in-flight LLM futures, not a 0/1 flag.

### Backward Compatibility
- AC24: All existing CLI flags (`--run-tag`, `--sample`, `--where`, `--backend`, `--dry-run`, `--resume`, `--definition`) continue to work.

## Cost Analysis

No change from Spec 0035 estimates. The work is a code rewrite, not a schema or infrastructure change.

| Operation | Cost | Time (est.) |
|---|---|---|
| Full corpus classify (130K × DeepSeek, 4 workers) | ~$24 | ~45 min |
| Single state (e.g., TX, ~8K docs, 4 workers) | ~$1.50 | ~6 min |
| Single EIN (1-50 docs) | < $0.01 | < 10 sec |
| Comparison query | $0 | < 5 sec |

The 4-worker concurrency should reduce wall-clock time by roughly 3-4x vs single-threaded.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Extraction not yet run for full corpus | Startup summary reports extraction coverage %. If coverage is 0% (no `classification_context` rows match the filter), the command prints an error and exits — there's nothing to classify. Otherwise it proceeds with whatever coverage exists and reports excluded doc count. |
| Thread pool overwhelms DeepSeek rate limits | Exponential backoff handles 429s. Default 4 workers is conservative. |
| org_provenance join is slow on 186K docs | Query uses existing indexes. State filter narrows the scan. |
| Comparison misleading if Haiku v2 was also wrong | Comparison is informational — human spot-check is the real validation. |

## Resumability & Checkpointing

Checkpoint state lives in `classification_runs.config_json.cursor` (the last processed `content_sha256`). An incomplete run is identified by `finished_at IS NULL`.

**Resume rules:**
- `--resume` finds the most recent run with matching `run_tag` where `finished_at IS NULL`
- The cursor is a `content_sha256` value; batches are ordered by `content_sha256 ASC` (deterministic, resumable)
- Already-classified rows (in `classification_results` for this `run_id`) are skipped via the existing `LEFT JOIN ... IS NULL` on `cr`
- Filters (`--state`, `--ein`, `--where`) are stored in `config_json` at run creation. On `--resume`, the command reads stored filters and uses them — it does NOT accept new filter flags. If the operator passes different filters on resume, the command prints an error and exits.
- `--sample` runs are NOT resumable. `--resume` with a `--sample` run prints an error. Random sampling is inherently non-deterministic and intended for quick tests, not long production runs.
- **Cursor and dedup interaction**: The `WHERE c.content_sha256 > :cursor` filter applies INSIDE the deduplicated subquery, not outside it. The query shape for resume with filters is: `SELECT DISTINCT c.content_sha256 FROM corpus c INNER JOIN ... WHERE c.content_sha256 > :cursor AND <filters> ORDER BY c.content_sha256 LIMIT :batch_size`. This ensures no duplicates or skips regardless of the many-to-many join cardinality.

**Checkpoint frequency:** After every batch (same as current). On Ctrl+C (SIGINT), a signal handler triggers one final checkpoint before exit.

## `--where` Safety

This is a single-operator system (only `ronp` has access). The `--where` flag exists for ad-hoc queries that don't justify a named flag.

**Explicit scope**: `--where` is an operator convenience with NO safety guarantees. It is equivalent to running ad-hoc SQL in psql. The operator is responsible for the predicate they write. The flag is NOT suitable for automation, scripting, or any context where the value comes from untrusted input.

Guardrails (defense-in-depth, not security boundaries):
- The value is interpolated into a `WHERE ... AND ({where_clause})` position
- The query uses `engine.connect()` (autocommit off) — DML without explicit `BEGIN` is rejected by PostgreSQL
- Parenthetical wrapping prevents some forms of clause escape

If structured filtering (`--state`, `--ein`, `--org-id`) covers the use case, prefer those over `--where`.

## Fallback Quality Gate Semantics

When `--allow-fallback` is active:
- Documents WITH `classification_context` rows: quality gate checks `cc.text_length` (same as default mode)
- Documents WITHOUT `classification_context` rows: quality gate checks `LENGTH(c.first_page_text)` against `--min-text-len`
- Skipped docs from either path are counted separately in progress output: `skip(ctx):N skip(fp):M` so the operator can see how many fallbacks had inadequate text
- The startup summary shows: `Fallback docs (no context): 1,204 — will use first_page_text`

## Comparison Command Details

The comparison joins `classification_results` (for the given `run_tag`) against `corpus` on `content_sha256`:

- **v2 material_type**: `corpus.material_type` (populated by crawler's Haiku v2 classifier)
- **v2 confidence**: `corpus.classification_confidence` (same source)
- **v2 reasoning**: `corpus.reasoning` (same source)
- Documents where `corpus.material_type IS NULL` (never classified by v2) are reported as a separate count ("v2 unclassified: N") and excluded from agreement/disagreement calculations
- Documents where the v3 run has `classified_by = 'llm:error'` are excluded from comparison (reported separately as "v3 errors: N")
- `--show-reasoning` prints v2 and v3 reasoning side-by-side for disagreements only (capped at `--limit 50` by default)
- `--confidence-threshold 0.8` filters the comparison to only show docs where EITHER classifier had confidence below the threshold (the uncertain cases worth reviewing)

## Error Row Semantics

When a document fails LLM classification after 4 retries:
- Written to `classification_results` with: `material_type=NULL`, `confidence=0.0`, `reasoning='LLM classification failed after 4 attempts'`, `classified_by='llm:error'`
- These rows ARE counted toward progress (they're "processed", just not successfully classified)
- On `--resume`, error rows are NOT retried (they exist in `classification_results` and the `LEFT JOIN ... IS NULL` skips them)
- To retry errors from a prior run, start a new run with a new `--run-tag`. Or delete the error rows manually (`DELETE FROM classification_results WHERE run_id = X AND classified_by = 'llm:error'`) and `--resume`.

## Sampling Semantics

- `--sample N` uses `ORDER BY random() LIMIT N` after all other filters and deduplication
- Sampling is NOT reproducible across runs (no seed). It's for quick spot-checks, not controlled experiments. For reproducible subsets, use `--state` or `--ein`.
- Sampling applies after extraction context filtering — all sampled docs have `pages_text` (unless `--allow-fallback`)
- `--sample` is incompatible with `--resume` (error on combination)

## Progress Metric Definitions

- **Throughput (docs/sec)**: Rolling average over the last 5 batches. Includes ALL docs processed in those batches (rule-matched + LLM-classified + skipped + errors). This is "documents evaluated per second", not "LLM calls per second."
- **ETA**: `(total_eligible - total_processed) / rolling_throughput`. Displayed as "ETA Xm" or "ETA Xh Ym" for longer runs. Shows "ETA --" if fewer than 2 batches have completed (insufficient data for estimate).
- **Elapsed**: Wall-clock time from first batch start (excludes startup count query time).

## Testing Strategy

### Required Tests

**Concurrency:**
- Verify that `--workers 4` results in 4 concurrent LLM calls (mock the LLM client with a delayed response, assert 4 calls are in-flight simultaneously)
- Verify that database writes are serialized (no concurrent write conflicts)

**Deduplication:**
- Create test data with one document linked to 3 orgs in the same state. Verify `--state` filter classifies it exactly once.

**Resume:**
- Start a run, interrupt after 2 batches, resume. Verify no duplicate classifications, correct final count.
- Verify `--resume` with different filters than the original run produces an error.
- Verify `--resume` with `--sample` produces an error.

**Fallback:**
- Default mode: verify docs without `classification_context` are excluded (count matches expected)
- `--allow-fallback`: verify docs without context use `first_page_text`, quality gate checks `first_page_text` length, separate skip counts reported

**Interrupt handling:**
- Send SIGINT during a batch. Verify checkpoint is written, partial results are persisted, no corrupt state.

**Quality gate:**
- Docs with `pages_text` below threshold are skipped and counted, not classified as `other_collateral`

**Mixed-failure batches:**
- Submit a batch where some LLM futures succeed and others fail. Verify: successful results are written, failed results get `llm:error` rows, stats are internally consistent (total = success + fail + skip + rule), checkpoint cursor advances to the highest durably written SHA.

## Traps to Avoid

1. **Don't silently fall back to first_page_text.** That's the entire reason this spec exists. The fallback must be explicitly opted into.
2. **Don't accept `--workers` and ignore it.** If you accept the flag, use it.
3. **Don't run silent.** Every long-running process must report progress continuously.
4. **Don't make testing require the full corpus.** `--state`, `--ein`, and `--sample` exist so you can validate on small targeted runs.
5. **Don't write `other_collateral` for docs you couldn't classify.** Skip them and say why. A low-confidence guess is worse than an honest gap.
