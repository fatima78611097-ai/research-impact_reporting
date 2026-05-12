# Plan 0038: Classifier Pipeline Rebuild

**Spec**: locard/specs/0038-classifier-pipeline-rebuild.md
**Date**: 2026-05-12

## Pre-Implementation Notes

### Schema: Direct Join Path

The `corpus` table has a direct `source_org_ein` column — each document belongs to exactly one org. Filtering uses `corpus.source_org_ein → nonprofits_seed.ein` (indexed as `idx_corpus_ein`). No many-to-many join, no deduplication needed. The spec has been amended to reflect this.

### Existing Code to Preserve/Modify

| File | Action |
|------|--------|
| `dashboard/pipeline/management/commands/reclassify_corpus.py` | Full rewrite (489 lines → ~600 lines) |
| `dashboard/pipeline/management/commands/compare_classifications.py` | Modify existing (181 lines) — drop confidence metrics, add tiebreaker candidate count |
| `reports/classify.py` line 281 | Remove `confidence` from V2 `required` list |
| `reports/classify.py` lines 691-702 | Make confidence validation non-fatal (accept but ignore) |
| New: `dashboard/pipeline/management/commands/resolve_disagreements.py` | New command (~250 lines) |

### Client Instantiation

`select_classifier_client()` does SSM lookups for DeepSeek. Create clients once at startup (one per worker), not per-document. Store in a list, index by worker.

## Implementation Steps

### Step 1: Drop Confidence from Tool Schema

**Files**: `lavandula/reports/classify.py`

1. Line 281: Change `"required": ["material_type", "confidence", "reasoning"]` to `"required": ["material_type", "reasoning"]`
2. Lines 691-702: Make confidence handling non-fatal. If `confidence` is present, accept it. If absent, set to `None`. Remove the error-raising path for missing confidence.
3. Line 55 (V1 schema): Same change — remove `confidence` from required. V1 is legacy but keep it consistent.

**Test**: Run existing classifier unit tests — they should still pass since confidence is now optional, not removed.

### Step 2: Rewrite `reclassify_corpus.py`

**File**: `lavandula/dashboard/pipeline/management/commands/reclassify_corpus.py`

This is the bulk of the work. Structure the rewrite as:

#### 2a: CLI Arguments

Keep all existing args. Add new ones:

```python
parser.add_argument("--state", type=str, default=None, help="Two-letter state code")
parser.add_argument("--ein", type=str, default=None, help="EIN (e.g., 13-1234567)")
parser.add_argument("--org-id", type=int, default=None, help="nonprofits_seed row ID")  
parser.add_argument("--batch-size", type=int, default=200, help="Docs per batch")
parser.add_argument("--allow-fallback", action="store_true", help="Allow first_page_text fallback")
parser.add_argument("--min-text-len", type=int, default=100, help="Minimum pages_text length")
parser.add_argument("--quiet", action="store_true", help="Suppress per-batch progress")
```

Note: `--org-id` maps to `nonprofits_seed` internal ID (if one exists). Check if `nonprofits_seed` has a serial PK. If not, drop `--org-id` and use `--ein` only, which is the natural key.

#### 2b: Query Construction

Build the fetch query dynamically:

```python
def _build_query(self, run_id, cursor, sample, where_clause, 
                 state, ein, allow_fallback, min_text_len, batch_size):
    join_type = "LEFT" if allow_fallback else "INNER"
    
    sql = f"""
        SELECT c.content_sha256, c.first_page_text, c.source_url_redacted,
               c.file_size_bytes, c.pdf_creator, c.source_org_ein,
               cc.pages_text, cc.pages_extracted, cc.total_pages
        FROM {_SCHEMA}.corpus c
        {join_type} JOIN {_SCHEMA}.classification_context cc
            ON cc.content_sha256 = c.content_sha256
        LEFT JOIN {_SCHEMA}.classification_results cr
            ON cr.content_sha256 = c.content_sha256 AND cr.run_id = :run_id
        WHERE c.content_type = 'application/pdf'
          AND cr.content_sha256 IS NULL
    """
    params = {"run_id": run_id, "batch_size": batch_size}
    
    # Extraction quality gate (in SQL for INNER JOIN path)
    if not allow_fallback:
        sql += " AND cc.text_length >= :min_text_len"
        params["min_text_len"] = min_text_len
    
    # State filter (direct join, no many-to-many)
    if state:
        sql += f"""
          AND c.source_org_ein IN (
              SELECT ein FROM {_SCHEMA}.nonprofits_seed WHERE state = :state
          )"""
        params["state"] = state
    
    # EIN filter
    if ein:
        sql += " AND c.source_org_ein = :ein"
        params["ein"] = ein
    
    # Raw WHERE
    if where_clause:
        sql += f" AND ({where_clause})"
    
    # Cursor for resume
    if not sample and cursor:
        sql += " AND c.content_sha256 > :cursor"
        params["cursor"] = cursor
    
    # Ordering
    if sample:
        sql += f" ORDER BY random() LIMIT :batch_size"
    else:
        sql += f" ORDER BY c.content_sha256 LIMIT :batch_size"
    
    return sql, params
```

#### 2c: Startup Summary and Dry Run

Before processing, run count queries with the same filters:

```python
def _count_eligible(self, engine, state, ein, org_id, where_clause, 
                    allow_fallback, min_text_len, run_id):
    # Count 1: Total PDFs matching filters (regardless of context)
    total_pdfs = self._count_query(engine, join_type="LEFT", 
                                    min_text_len=None, ...)
    
    # Count 2: PDFs with extraction context + quality gate
    with_context = self._count_query(engine, join_type="INNER",
                                      min_text_len=min_text_len, ...)
    
    # Count 3: PDFs with context but below quality gate  
    below_gate = self._count_query(engine, join_type="INNER",
                                    min_text_len=None, ...) - with_context
    
    without_context = total_pdfs - (with_context + below_gate)
    coverage = (with_context + below_gate) / total_pdfs * 100 if total_pdfs else 0
    
    return {
        "total_pdfs": total_pdfs,
        "with_context": with_context,
        "below_gate": below_gate,
        "without_context": without_context,
        "coverage": coverage,
    }
```

Print the configuration block per spec. If `--dry-run`, print the extended breakdown and exit:

```python
if dry_run:
    already_classified = self._count_already_classified(engine, run_id)
    would_process = counts["with_context"] - already_classified
    
    self.stdout.write(f"Dry run ({run_tag}" + filter_desc + "):")
    self.stdout.write(f"  Total eligible PDFs:           {counts['total_pdfs']:,}")
    self.stdout.write(f"  With extraction context:       {counts['with_context'] + counts['below_gate']:,} ({counts['coverage']:.1f}%)")
    self.stdout.write(f"  Without context (excluded):    {counts['without_context']:,}")
    self.stdout.write(f"  Context text >= {min_text_len} chars:     {counts['with_context']:,}")
    self.stdout.write(f"  Context text < {min_text_len} chars:        {counts['below_gate']:,}")
    self.stdout.write(f"")
    self.stdout.write(f"  Already classified in this run: {already_classified:,}")
    self.stdout.write(f"  Would process: {would_process:,}")
    
    # Cost/time estimates (heuristic, labeled as estimates)
    cost_per_doc = {"deepseek": 0.00019, "haiku": 0.00025, "claude": 0.003}
    if backend in cost_per_doc:
        est_cost = would_process * cost_per_doc[backend]
        docs_per_sec_per_worker = 3.0
        est_secs = would_process / (docs_per_sec_per_worker * workers)
        self.stdout.write(f"")
        self.stdout.write(f"  Estimated cost ({backend}): ~${est_cost:.2f}")
        self.stdout.write(f"  Estimated time ({workers} workers): ~{_format_duration(est_secs)}")
    else:
        self.stdout.write(f"  Estimated cost/time: unavailable for {backend}")
    return
```

#### 2d: Worker Pool and Main Loop

```python
def _run(self, engine, **options):
    workers = options["workers"]
    
    # Create one client per worker
    clients = [select_classifier_client(backend=backend) for _ in range(workers)]
    
    # Stats
    stats = defaultdict(int)
    distribution = defaultdict(int)
    batch_times = deque(maxlen=5)  # For rolling throughput
    shutdown = threading.Event()
    
    # SIGINT handler
    original_handler = signal.getsignal(signal.SIGINT)
    def _handle_sigint(signum, frame):
        shutdown.set()
    signal.signal(signal.SIGINT, _handle_sigint)
    
    # Watchdog
    active_futures = []
    watchdog = StallWatchdog(
        get_progress=lambda: stats["total"],
        get_active=lambda: len([f for f in active_futures if not f.done()]),
        stall_threshold_sec=600,
        progress_total=total_eligible,
    )
    watchdog.start_thread()
    
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while not shutdown.is_set():
                batch_start = time.monotonic()
                rows = self._fetch_batch(engine, ...)
                if not rows:
                    break
                
                futures = {}
                for row in rows:
                    if shutdown.is_set():
                        break
                    
                    pages_text = row["pages_text"] or ""
                    first_page_text = row["first_page_text"] or ""
                    has_context = bool(pages_text)
                    eval_text = pages_text if has_context else first_page_text
                    
                    # Quality gate with separate skip counters
                    if len(eval_text.strip()) < min_text_len:
                        if has_context:
                            stats["skip_ctx"] += 1
                        else:
                            stats["skip_fp"] += 1
                        stats["total"] += 1
                        continue
                    
                    # Rule prefilter
                    rule_match = rule_engine.evaluate(
                        text=eval_text, url=row["source_url"],
                        pdf_creator=row["pdf_creator"],
                        page_count=row["total_pages"],
                        file_size=row["file_size"],
                    )
                    if rule_match:
                        self._write_result(engine, run_id, row["content_sha256"], ...)
                        stats["rule_matched"] += 1
                        stats["total"] += 1
                        distribution[rule_match.material_type] += 1
                        continue
                    
                    # Submit to thread pool
                    client = clients[len(futures) % workers]
                    fut = pool.submit(
                        self._classify_one, client, definition, row
                    )
                    futures[fut] = row
                    active_futures.append(fut)
                
                # Collect results
                for fut in as_completed(futures):
                    row = futures[fut]
                    try:
                        result = fut.result()
                    except Exception:
                        result = None
                    
                    if result and not result.error:
                        self._write_result(engine, run_id, row["content_sha256"], ...)
                        stats["llm_classified"] += 1
                        distribution[result.material_type] += 1
                    else:
                        self._write_error(engine, run_id, row["content_sha256"])
                        stats["llm_errors"] += 1
                    stats["total"] += 1
                
                active_futures = [f for f in active_futures if not f.done()]
                
                # Checkpoint
                batch_elapsed = time.monotonic() - batch_start
                batch_times.append((len(rows), batch_elapsed))
                self._checkpoint(engine, run_id, last_cursor, stats)
                
                # Progress line
                if not quiet:
                    self._print_progress(batch_num, stats, total_eligible, batch_times)
                
                batch_num += 1
                
                if sample and stats["total"] >= sample:
                    break
    finally:
        watchdog.stop()
        signal.signal(signal.SIGINT, original_handler)
        self._checkpoint(engine, run_id, last_cursor, stats)
        self._print_summary(stats, distribution, start_time)
```

#### 2e: Rate Limiting, Backoff, and Failure Halt

The `_classify_one` method handles per-doc retries. A global throttle and consecutive-failure halt are managed in the main loop.

```python
def _classify_one(self, client, definition, row):
    """Called in worker thread. Retries with exponential backoff + jitter."""
    max_retries = 4
    for attempt in range(max_retries):
        try:
            result = classify_first_page_v3(client, definition, row)
            return result
        except RateLimitError:
            if attempt == max_retries - 1:
                return None  # will become llm:error
            base_delay = 2 ** (attempt + 1)  # 2, 4, 8, 16
            jitter = base_delay * 0.25 * (2 * random.random() - 1)  # ±25%
            time.sleep(base_delay + jitter)
        except Exception:
            if attempt == max_retries - 1:
                return None
            time.sleep(2 ** (attempt + 1))

# In the main loop, after collecting results from a batch:
consecutive_failures = 0
for fut in as_completed(futures):
    ...
    if result and not result.error:
        consecutive_failures = 0
        ...
    else:
        consecutive_failures += 1
        stats["llm_errors"] += 1
        if consecutive_failures >= 20:
            self.stderr.write(
                f"HALT: 20 consecutive LLM failures. "
                f"Last error: {last_error}\n"
                f"Run --resume to continue after fixing the issue."
            )
            shutdown.set()
            break

# Global throttle: track 429 timestamps across workers
rate_limit_times = deque(maxlen=20)  # thread-safe via GIL for append/len

# Inside _classify_one, on 429:
rate_limit_times.append(time.monotonic())

# In main loop, before submitting next batch:
recent_429s = sum(1 for t in rate_limit_times 
                  if time.monotonic() - t < 10)
if recent_429s >= 3:
    self.stdout.write("[throttle] 3+ rate limits in 10s, pausing 30s")
    time.sleep(30)
```

#### 2f: Progress Reporting

```python
def _print_progress(self, batch_num, stats, total, batch_times):
    now = time.strftime("%H:%M:%S")
    processed = stats["total"]
    pct = (processed / total * 100) if total else 0
    
    # Rolling throughput
    if batch_times:
        recent_docs = sum(d for d, _ in batch_times)
        recent_time = sum(t for _, t in batch_times)
        throughput = recent_docs / recent_time if recent_time > 0 else 0
    else:
        throughput = 0
    
    # ETA
    remaining = total - processed
    if throughput > 0 and len(batch_times) >= 2:
        eta_secs = remaining / throughput
        eta = f"{int(eta_secs // 60)}m" if eta_secs < 3600 else f"{int(eta_secs // 3600)}h {int((eta_secs % 3600) // 60)}m"
    else:
        eta = "--"
    
    skip_str = f"skip(ctx):{stats.get('skip_ctx',0)} skip(fp):{stats.get('skip_fp',0)}" if allow_fallback else f"skip:{stats.get('skip_ctx',0)}"
    self.stdout.write(
        f"[{now}] Batch {batch_num} | "
        f"{processed:,}/{total:,} ({pct:.1f}%) | "
        f"{throughput:.1f} docs/sec | ETA {eta} | "
        f"rules:{stats.get('rule_matched',0)} "
        f"llm:{stats.get('llm_classified',0)} "
        f"{skip_str} "
        f"err:{stats.get('llm_errors',0)}"
    )
```

#### 2g: Resume Validation and Checkpoint Semantics

**Resume validation:**

```python
if resume and sample:
    self.stderr.write("ERROR: --sample runs cannot be resumed.")
    return

if resume:
    stored = config.get("filters", {})
    provided = {"state": state, "ein": ein, "where": where_clause}
    if stored != provided:
        self.stderr.write(f"ERROR: Filters changed since original run.\n"
                         f"  Original: {stored}\n  Provided: {provided}\n"
                         f"Resume uses the original filters. Remove conflicting flags.")
        return
    # Restore filters from config
    state = stored.get("state")
    ein = stored.get("ein")
    where_clause = stored.get("where")
```

**Run config stored at creation:**

```python
config_json = {
    "backend": backend,
    "definition": definition.name,
    "definition_version": definition.version,
    "rules_sha256": rule_engine.rules_sha256,
    "filters": {"state": state, "ein": ein, "where": where_clause},
    "allow_fallback": allow_fallback,
    "min_text_len": min_text_len,
}
```

**Durable cursor rule:** The checkpoint cursor is the highest `content_sha256` for which a `classification_results` row has been durably written (committed). The cursor is NOT advanced speculatively for in-flight work. This means:

```python
def _checkpoint(self, engine, run_id, cursor, stats):
    """Write cursor = highest SHA that has a committed result row."""
    with engine.begin() as conn:
        conn.execute(text(f"""
            UPDATE {_SCHEMA}.classification_runs 
            SET config_json = jsonb_set(config_json, '{{cursor}}', to_jsonb(:cursor::text)),
                config_json = jsonb_set(config_json, '{{stats}}', :stats::jsonb)
            WHERE id = :run_id
        """), {"run_id": run_id, "cursor": cursor, "stats": json.dumps(dict(stats))})
```

`cursor` is updated after each successful `_write_result` call, tracking `max(content_sha256)` of written rows.

**SIGINT drain semantics:**

```python
def _handle_sigint(signum, frame):
    shutdown.set()  # (1) stop submitting new work

# In the main loop, after shutdown.set():
# (2) Drain completed futures (don't cancel them — their results are valid)
for fut in as_completed(futures, timeout=30):
    row = futures[fut]
    try:
        result = fut.result()
        if result and not result.error:
            self._write_result(...)
            # Update cursor to this SHA
    except Exception:
        pass

# (3) Cancel remaining in-flight futures
for fut in futures:
    if not fut.done():
        fut.cancel()

# (4) Checkpoint with the highest durably written cursor
self._checkpoint(engine, run_id, last_durable_cursor, stats)

# (5) Print partial summary
self.stdout.write(f"\nInterrupted. Checkpointed at {last_durable_cursor[:12]}...")
self._print_summary(stats, distribution, start_time)
```

### Step 3: Update `compare_classifications.py`

**File**: `lavandula/dashboard/pipeline/management/commands/compare_classifications.py`

Modifications to existing command:

1. **Remove confidence comparison metrics** — drop avg confidence, confidence distribution, and confidence threshold sections
2. **Add tiebreaker candidate count** — print: `Tiebreaker candidates: N (use resolve_disagreements --run-tag TAG to resolve)`
3. **Add `--state` filter** — join through `corpus.source_org_ein → nonprofits_seed.ein` for scoped comparison
4. **Add breakdown by `classified_by` source** — rule-matched vs LLM disagreements
5. **Exclude v3 error rows** — `classified_by = 'llm:error'` rows are excluded from comparison and reported separately: `v3 errors (excluded): N`
6. **Report v2 unclassified** — docs where `corpus.material_type IS NULL` are counted and excluded: `v2 unclassified (excluded): N`
7. **Cap `--show-reasoning`** — default limit 50 disagreements. Add `--limit N` flag to override:

```python
parser.add_argument("--show-reasoning", action="store_true")
parser.add_argument("--limit", type=int, default=50, help="Max disagreements to show with --show-reasoning")
```

**Comparison query structure:**

```sql
SELECT cr.content_sha256, cr.material_type AS v3_type, cr.classified_by,
       cr.reasoning AS v3_reasoning,
       c.material_type AS v2_type, c.reasoning AS v2_reasoning
FROM lava_corpus.classification_results cr
JOIN lava_corpus.classification_runs runs ON runs.id = cr.run_id
JOIN lava_corpus.corpus c ON c.content_sha256 = cr.content_sha256
WHERE runs.run_tag = :run_tag
  AND cr.classified_by != 'llm:error'     -- exclude errors
  -- Optional state filter:
  AND (:state IS NULL OR c.source_org_ein IN (
      SELECT ein FROM lava_corpus.nonprofits_seed WHERE state = :state
  ))
```

Then in Python:
- Partition into: v2_null (unclassified), agree (v2==v3), disagree (v2!=v3)
- Within disagree, partition by `classified_by LIKE 'rule:%'` vs LLM
- Tiebreaker candidates = LLM disagreements only

### Step 4: Create `resolve_disagreements.py`

**File**: `lavandula/dashboard/pipeline/management/commands/resolve_disagreements.py`

New management command (~250 lines). Structure:

#### 4a: CLI Arguments

```python
parser.add_argument("--run-tag", required=True, help="Run tag to resolve disagreements for")
parser.add_argument("--backend", default="haiku", help="Tiebreaker LLM backend")
parser.add_argument("--state", type=str, default=None, help="Filter by state")
parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--sample", type=int, default=None, help="Sample N disagreements")
```

#### 4b: Fetch Disagreements

```sql
SELECT cr.content_sha256, cr.material_type AS v3_type, cr.classified_by,
       c.material_type AS v2_type,
       cc.pages_text, c.first_page_text, c.source_url_redacted
FROM lava_corpus.classification_results cr
JOIN lava_corpus.classification_runs runs ON runs.id = cr.run_id
JOIN lava_corpus.corpus c ON c.content_sha256 = cr.content_sha256
LEFT JOIN lava_corpus.classification_context cc ON cc.content_sha256 = cr.content_sha256
WHERE runs.run_tag = :run_tag
  AND cr.material_type != c.material_type      -- disagreement
  AND cr.classified_by NOT LIKE 'rule:%'        -- exclude rule-matched (deterministic)
  AND cr.classified_by != 'llm:error'           -- exclude errors
  AND c.material_type IS NOT NULL               -- v2 must exist
```

#### 4c: Tiebreaker Prompt

Build a focused prompt that presents both candidates:

```python
def _build_tiebreaker_prompt(self, v2_type, v3_type, pages_text, first_page_text):
    doc_text = pages_text or first_page_text
    sanitized = sanitize_document_text(doc_text)
    
    return (
        f"Two classifiers disagree on this nonprofit document.\n\n"
        f"Classifier A says: {v2_type}\n"
        f"Classifier B says: {v3_type}\n\n"
        f"Read the document below and determine which classification is correct. "
        f"Pick one of the two options, or classify it yourself if both are wrong. "
        f"Explain your reasoning in under 200 characters.\n\n"
        f"<untrusted_document>\n{sanitized}\n</untrusted_document>"
    )
```

#### 4d: Tool Schema

```python
TIEBREAKER_TOOL = {
    "name": "resolve_disagreement",
    "description": "Record which classifier is correct",
    "input_schema": {
        "type": "object",
        "properties": {
            "winner": {
                "type": "string",
                "enum": ["A", "B", "neither"],
            },
            "material_type": {
                "type": "string",
                "description": "The correct material_type",
            },
            "reasoning": {
                "type": "string",
                "description": "Short rationale (<=200 chars)",
            },
        },
        "required": ["winner", "material_type", "reasoning"],
    },
}
```

#### 4e: Backend Enforcement (AC27)

Before processing, read the parent run's config to check the backend used:

```python
parent_run = self._get_run(engine, run_tag)
parent_backend = parent_run["config_json"].get("backend", "unknown")

if backend == parent_backend:
    self.stderr.write(
        f"ERROR: Tiebreaker backend '{backend}' is the same as the primary run.\n"
        f"  The tiebreaker must use a DIFFERENT model to provide an independent vote.\n"
        f"  Primary run used: {parent_backend}\n"
        f"  Suggestion: --backend haiku (if primary was deepseek)\n"
    )
    return
```

#### 4f: Store Results

Write tiebreaker results to `classification_results` with a new run_tag (e.g., `{original_tag}-tiebreaker`):

```python
config_json = {
    "parent_run_tag": run_tag,
    "backend": backend,
    "mode": "tiebreaker",
}
```

`classified_by = f"tiebreaker:{model_name}"`

#### 4g: Output

Print winner distribution and resolved classification distribution per spec.

### Step 5: Tests

**File**: `lavandula/dashboard/pipeline/tests/test_reclassify_corpus.py` (new or append to existing)

#### Core classification tests:

1. **Concurrency test**: Mock LLM client with `time.sleep(0.5)`, submit 4 docs with `--workers 4`, assert all 4 are in-flight simultaneously (use a threading barrier or counter)
2. **Serialized DB writes test**: With `--workers 4`, verify all writes go through a single connection (mock the engine, assert no concurrent `_write_result` calls)
3. **Quality gate test**: Create doc with 50-char pages_text, verify skipped (not classified as `other_collateral`)
4. **INNER JOIN test**: Default mode excludes docs without `classification_context`
5. **Fallback test**: `--allow-fallback` includes docs without context, uses `first_page_text`, separate `skip(fp)` counter incremented

#### Filter tests:

6. **State filter test**: Create docs for TX and NY orgs via `source_org_ein`, run with `--state TX`, verify only TX docs classified
7. **EIN filter test**: Single EIN, verify only that org's docs
8. **Org-ID filter test**: Filter by `nonprofits_seed.id`, verify correct docs

#### Resume tests:

9. **Resume test**: Run 2 batches, interrupt, resume, verify no duplicates and correct final count
10. **Resume filter mismatch test**: Start with `--state TX`, resume with `--state NY`, verify error
11. **Sample + resume test**: Verify error on `--sample --resume`

#### Interrupt and failure tests:

12. **SIGINT test**: Send SIGINT during a batch, verify: checkpoint written, completed futures drained, cursor points to highest durable SHA
13. **Mixed failure test**: Some futures succeed, some fail. Verify: stats internally consistent (`total = llm_classified + llm_errors + skip_ctx + skip_fp + rule_matched`), error rows written with `classified_by='llm:error'`
14. **20 consecutive failures halt**: Mock LLM to fail 20 times in a row, verify command halts with clear error message and checkpoint
15. **Global throttle test**: Mock LLM to return 429 three times within 10s, verify main thread pauses before next batch submission

#### Dry-run test:

16. **Dry-run output test**: Create test data with known counts, run `--dry-run`, verify output includes: eligible count, extraction coverage, quality gate breakdown, estimated cost/time (or "unavailable" for unknown backends)

#### Tiebreaker tests:

17. **Tiebreaker test**: Create disagreement data, run `resolve_disagreements`, verify results stored with `classified_by='tiebreaker:{model}'`
18. **Tiebreaker backend enforcement test**: Set parent run backend to `haiku`, run tiebreaker with `--backend haiku`, verify error. Run with `--backend deepseek`, verify success.

#### Comparison tests:

19. **Comparison error exclusion test**: Create v3 error rows (`classified_by='llm:error'`), run comparison, verify they're excluded and reported separately
20. **Comparison v2 unclassified test**: Create docs where `corpus.material_type IS NULL`, verify reported as "v2 unclassified" and excluded from agreement/disagreement calculations

### Step 6: Commit and PR

1. Commit Step 1 (confidence schema change) separately — small, testable
2. Commit Step 2 (reclassify rewrite) — the main deliverable
3. Commit Step 3 (compare update) — depends on Step 2
4. Commit Step 4 (resolve_disagreements) — depends on Step 3
5. Commit Step 5 (tests) — covers all steps
6. Create PR against master

## Estimated Build Time

| Step | Effort |
|------|--------|
| Step 1: Confidence schema | ~15 min |
| Step 2: Reclassify rewrite | ~2 hours |
| Step 3: Compare update | ~30 min |
| Step 4: Resolve disagreements | ~1 hour |
| Step 5: Tests | ~1 hour |
| **Total** | **~5 hours** |

## Risks

| Risk | Mitigation |
|------|------------|
| DeepSeek client SSM lookup per instantiation | Create clients once at startup, one per worker |
| `nonprofits_seed` doesn't have a serial PK for `--org-id` | Use `--ein` as the natural key; drop `--org-id` from CLI if no PK exists |
| Existing compare_classifications.py logic may be tangled with confidence | Read carefully before modifying; may be simpler to rewrite comparison section |
| Tiebreaker prompt quality | Test on 10-20 known disagreements before full run |
