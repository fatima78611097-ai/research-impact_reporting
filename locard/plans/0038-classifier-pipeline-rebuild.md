# Plan 0038: Classifier Pipeline Rebuild

**Spec**: locard/specs/0038-classifier-pipeline-rebuild.md
**Date**: 2026-05-12

## Pre-Implementation Notes

### Schema Discovery: Simpler Join Path

The spec assumed doc-to-org linkage via a many-to-many `org_provenance` table. In reality, the join is direct:

```
corpus.source_org_ein → nonprofits_seed.ein (indexed: idx_corpus_ein)
```

Each document has exactly one `source_org_ein`. No deduplication needed. This simplifies the query construction and eliminates the `SELECT DISTINCT` subquery the spec described.

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

#### 2c: Startup Summary

Before processing, run a count query with the same filters to get:
- Total eligible docs (with context or with fallback)
- Docs excluded (no context, when not using fallback)
- Extraction coverage percentage

Print the configuration block per spec.

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
                    eval_text = pages_text or first_page_text
                    
                    # Quality gate (for fallback docs not caught by SQL)
                    if len(eval_text.strip()) < min_text_len:
                        stats["skipped_short"] += 1
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

#### 2e: Progress Reporting

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
    
    self.stdout.write(
        f"[{now}] Batch {batch_num} | "
        f"{processed:,}/{total:,} ({pct:.1f}%) | "
        f"{throughput:.1f} docs/sec | ETA {eta} | "
        f"rules:{stats.get('rule_matched',0)} "
        f"llm:{stats.get('llm_classified',0)} "
        f"skip:{stats.get('skipped_short',0)} "
        f"err:{stats.get('llm_errors',0)}"
    )
```

#### 2f: Resume Validation

On `--resume`, read `config_json` from the existing run. Compare stored filters against provided filters:

```python
if resume:
    stored = config.get("filters", {})
    provided = {"state": state, "ein": ein, "where": where_clause}
    if stored != provided:
        self.stderr.write(f"ERROR: Filters changed since original run.\n"
                         f"  Original: {stored}\n  Provided: {provided}\n"
                         f"Resume uses the original filters. Remove conflicting flags.")
        return
```

Store filters in `config_json` at run creation:

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

#### 2g: Init Run with Sample Guard

```python
if resume and sample:
    self.stderr.write("ERROR: --sample runs cannot be resumed.")
    return
```

### Step 3: Update `compare_classifications.py`

**File**: `lavandula/dashboard/pipeline/management/commands/compare_classifications.py`

Modifications to existing command:

1. **Remove confidence comparison metrics** — drop the avg confidence, confidence distribution, and confidence threshold sections
2. **Add tiebreaker candidate count** — at the end of comparison output, print: `Tiebreaker candidates: N (use resolve_disagreements --run-tag TAG to resolve)`
3. **Add `--state` filter** — join through `corpus.source_org_ein → nonprofits_seed.ein` for scoped comparison
4. **Keep `--show-reasoning`** — already exists, just ensure it works with the new flow
5. **Add breakdown by `classified_by` source** — rule-matched vs LLM disagreements

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

#### 4e: Store Results

Write tiebreaker results to `classification_results` with a new run_tag (e.g., `{original_tag}-tiebreaker`):

```python
config_json = {
    "parent_run_tag": run_tag,
    "backend": backend,
    "mode": "tiebreaker",
}
```

`classified_by = f"tiebreaker:{model_name}"`

#### 4f: Output

Print winner distribution and resolved classification distribution per spec.

### Step 5: Tests

**File**: `lavandula/dashboard/pipeline/tests/test_reclassify_corpus.py` (new or append to existing)

Test cases per spec's Testing Strategy section:

1. **Concurrency test**: Mock LLM client with `time.sleep(0.5)`, submit 4 docs with `--workers 4`, assert all 4 are in-flight simultaneously
2. **Quality gate test**: Create doc with 50-char pages_text, verify skipped (not classified as other_collateral)
3. **State filter test**: Create docs for TX and NY orgs, run with `--state TX`, verify only TX docs classified
4. **EIN filter test**: Single EIN, verify only that org's docs
5. **Resume test**: Run 2 batches, interrupt, resume, verify no duplicates
6. **Resume filter mismatch test**: Start with `--state TX`, resume with `--state NY`, verify error
7. **Sample + resume test**: Verify error on `--sample --resume`
8. **INNER JOIN test**: Default mode excludes docs without classification_context
9. **Fallback test**: `--allow-fallback` includes docs without context, uses first_page_text
10. **SIGINT test**: Send SIGINT during batch, verify checkpoint written
11. **Mixed failure test**: Some futures succeed, some fail, verify stats consistent
12. **Tiebreaker test**: Create disagreement data, run resolve_disagreements, verify results

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
