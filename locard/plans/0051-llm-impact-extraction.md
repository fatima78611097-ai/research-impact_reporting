# Plan 0051: LLM Impact Extraction (Metrics + Stories)

**Spec:** `locard/specs/0051-llm-impact-extraction.md`
**Author:** Architect
**Created:** 2026-05-26

## Overview

Four phases, each independently testable. Phase 1 (schema + extraction core) is the critical path — it produces a working CLI tool. Phases 2-3 add dashboard UI. Phase 4 is validation on real data.

## Phase 1: Schema + Extraction Core

**Goal:** `python3 manage.py llm_extract p20-test --ein 760305357` works end-to-end.

### Step 1.1: Database Migration

Create RDS migration file `lavandula/migrations/rds/017_llm_extraction_tables.sql`:

```sql
CREATE TABLE IF NOT EXISTS lava_vocab.llm_metrics ( ... );  -- per spec
CREATE TABLE IF NOT EXISTS lava_vocab.llm_stories ( ... );  -- per spec
-- Indexes and grants per spec
```

**AC:** Tables exist in RDS, `research_app` can INSERT/SELECT/DELETE.

### Step 1.2: Extraction Client Module

Create `lavandula/nlp/llm_extract.py` — the core extraction logic, independent of Django:

```python
def get_document_text(engine, sha: str, s3_client, tmp_dir: Path) -> str | None:
    """Text source precedence: Docling sections → pdftotext → None."""

def call_deepseek(api_key: str, text: str, http_client: httpx.Client) -> dict:
    """Single API call, returns parsed JSON dict with 'metrics' and 'stories'."""

def validate_response(result: dict, text_length: int) -> dict | None:
    """Validate JSON structure, apply hallucination guard."""

def extract_document(engine, sha: str, ein: str, run_id: int,
                     api_key: str, s3_client, http_client, tmp_dir) -> dict:
    """Full pipeline for one document. Returns stats dict."""
```

Key implementation details:
- `get_document_text`: Query `lava_parse.sections` first. If total `LENGTH(body_text) >= 100`, concatenate with `[heading]` prefixes. Otherwise download PDF from S3 to a secure temp directory created via `tempfile.mkdtemp()` (mode 0o700, auto-cleaned in `finally` block). Run `subprocess.run(["pdftotext", path, "-"], capture_output=True, timeout=30)`. Return None if < 100 chars.
- `call_deepseek`: Use `httpx.Client` (not `httpx.post` — reuse connection pool) with `verify=True` (TLS certificate validation — httpx default, stated explicitly). System prompt from spec. Temperature 0.0, max_tokens 6000, timeout 90s. Strip markdown fences from response. Retry on 429/500/503 with backoff (1s, 2s, 4s), max 3 retries. On JSON parse failure, log only the first 200 characters of the response (truncated to avoid logging full PII from stories) plus the SHA and error type — never the full raw response.
- `validate_response`: Check top-level dict has `metrics` (list) and `stories` (list). Each metric must have `metric_text` (str). Each story must have `story_title` (str). Hallucination guard: if `text_length < 100`, discard entire response even if LLM returned data.
- `extract_document`: Orchestrates the above. Returns `{"metrics": int, "stories": int, "skipped": bool, "cost_usd": float, "error": str|None}`.

**AC:** `extract_document()` can be called standalone in a Python script. No Django dependency in this module.

### Step 1.3: Management Command

Create `lavandula/dashboard/pipeline/management/commands/llm_extract.py`:

Follow the pattern from `extract_terms.py` / `extract_metrics.py`:
- `add_arguments()`: All args from spec with validation regex checks.
- `handle()`: Validate inputs → create/resume `extraction_runs` row → query eligible docs → dry-run or execute → update run stats.

Document query:
```sql
SELECT c.content_sha256, c.source_org_ein
FROM lava_corpus.corpus c
JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
WHERE c.material_type IN ('annual_report', 'impact_report')
  AND ns.ntee_code LIKE :ntee
  -- AND ns.state = :state  (if --state provided)
  -- AND c.source_org_ein = :ein  (if --ein provided)
ORDER BY c.source_org_ein, c.report_year DESC NULLS LAST
```

Resume: LEFT JOIN `llm_metrics` and `llm_stories` on `(run_id, content_sha256)` to exclude already-processed docs. Also exclude SHAs in `stats_json.skipped_shas`.

Parallelism: `ThreadPoolExecutor(max_workers=parallel)`. Each worker calls `extract_document()`. Main thread collects futures, updates `stats_json` after each batch, checks cost limit.

SIGINT handler: Set `_shutdown` flag, wait for in-flight workers, update run status to `aborted`, write final stats.

Run-state detection at startup:
- Query `extraction_runs` for `run_tag`. If no row exists: create with `stats_json = {"status": "running"}`.
- If row exists and `stats_json.status = "running"`: refuse to start (another process may be active). Print "Run is already active. Use --resume if the previous process crashed."
- If row exists and `stats_json.status` in `("completed", "aborted", "failed")` AND `--resume` is set: reuse the run, skip already-processed docs.
- If row exists and status is terminal but `--resume` is NOT set: refuse ("Run already exists. Use --resume to continue or pick a new run_tag.").

State transitions:
- `running` → `completed`: all eligible docs processed (including skips/failures).
- `running` → `aborted`: SIGINT received or `--cost-limit` exceeded. Sets status before exiting.
- `running` → `failed`: unhandled exception in main thread. Wrapped in try/finally to always write final status.

Skipped SHAs bookkeeping:
- After processing each document, if the document had < 100 chars of text OR returned zero metrics AND zero stories from the LLM, append its SHA to `stats_json.skipped_shas`.
- `stats_json.skipped_shas` is persisted to DB every batch (same as other stats).
- On `--resume`, the eligible docs query excludes SHAs in `skipped_shas` in addition to SHAs with existing `llm_metrics`/`llm_stories` rows.

DB writes per document:
```sql
DELETE FROM lava_vocab.llm_metrics WHERE run_id = :run_id AND content_sha256 = :sha;
DELETE FROM lava_vocab.llm_stories WHERE run_id = :run_id AND content_sha256 = :sha;
INSERT INTO lava_vocab.llm_metrics (run_id, content_sha256, source_org_ein,
    metric_text, metric_type, metric_value, unit, geo_impact, source_snippet)
VALUES (:run_id, :sha, :ein, :metric_text, :metric_type, :metric_value,
    :unit, :geo_impact, :source_snippet);
-- Same pattern for llm_stories with array columns
```

For Postgres array columns (`people_mentioned`, `themes`), use SQLAlchemy's `ARRAY` type or cast: `CAST(:val AS TEXT[])`.

**AC:** AC1 (single EIN), AC2 (dry-run), AC3 (resume), AC4 (cost-limit), AC5 (parallel), AC6 (metrics FK), AC7 (stories FK), AC8 (skip < 100 chars), AC9 (hallucination guard).

### Step 1.4: Fixture Validation

Run on the two frozen fixture orgs:
```bash
python3 manage.py llm_extract fixture-test --ein 760305357
python3 manage.py llm_extract fixture-test --ein 010211543
```

Verify output against spec AC14 (CanCare >= 20 metrics, includes "1,514 cancer patients" and "9,611 visits") and AC15 (BGCSM >= 10 metrics, includes "1,492 youth served" and "66,108 meals").

**AC:** AC14, AC15.

## Phase 2: Dashboard — LLM Extraction Page

**Goal:** Operator can view run history, start new runs, and monitor progress from the dashboard.

### Step 2.1: URL Routes

Add to `lavandula/dashboard/pipeline/urls.py`:
```python
path("llm-extract/", views.LlmExtractView.as_view(), name="llm_extract"),
path("llm-extract/queue/", views.LlmExtractJobCreateView.as_view(), name="llm_extract_job_create"),
path("llm-extract/status/", views.LlmExtractStatusPartial.as_view(), name="llm_extract_status"),
path("llm-extract/stop/", views.LlmExtractStopView.as_view(), name="llm_extract_stop"),
```

### Step 2.2: Views

Add to `lavandula/dashboard/pipeline/views.py`:

`LlmExtractView` (GET): Query `extraction_runs` for runs where `extractor_version = '0051-v1'`. Display run history table (run_tag, status from stats_json, docs processed, metrics found, stories found, cost, duration). Show start form with fields: NTEE filter, state filter, max docs, parallel workers, cost limit.

`LlmExtractJobCreateView` (POST): Validate form inputs (same regex as CLI). Launch management command via `subprocess.Popen` (background, non-blocking — same pattern as other pipeline job creates). The command itself creates the `extraction_runs` row. Redirect to LLM extract page.

`LlmExtractStopView` (POST): Store a stop-requested flag in `stats_json` (e.g., `stats_json.stop_requested = true`). The management command checks this flag each batch and gracefully shuts down if set — same as SIGINT handling but poll-based. This avoids sending OS signals from the web process, which would require PID tracking and cross-process signal authorization. If the run is not in `running` state, update status to `aborted` directly (handles crashed process cleanup).

`LlmExtractStatusPartial` (GET): Return JSON with current run stats for AJAX polling. Query `stats_json` from the active run (where `stats_json->>'status' = 'running'`).

### Step 2.3: Template

Create `lavandula/dashboard/pipeline/templates/pipeline/llm_extract.html`:

Follow existing pipeline page patterns (e.g., classifier_v3.html):
- Run history table at top
- Start form with input fields
- Progress section with AJAX polling (htmx or vanilla JS, match existing pattern)

**AC:** AC11 (run history), AC12 (start form).

## Phase 3: Dashboard — Org Detail Integration

**Goal:** Org detail page shows LLM-extracted metrics and stories for each org.

### Step 3.1: View Changes

Update `OrgDetailView.get_context_data()` in `views.py`:

```python
# LLM-extracted metrics and stories
with engine.connect() as conn:
    ctx["llm_metrics"] = conn.execute(text("""
        SELECT metric_text, metric_type, metric_value, unit, geo_impact,
               source_snippet, created_at
        FROM lava_vocab.llm_metrics
        WHERE source_org_ein = :ein
        ORDER BY metric_value DESC NULLS LAST
    """), {"ein": ein}).fetchall()

    ctx["llm_stories"] = conn.execute(text("""
        SELECT story_title, story_summary, people_mentioned, program,
               themes, source_snippet, created_at
        FROM lava_vocab.llm_stories
        WHERE source_org_ein = :ein
        ORDER BY created_at DESC
    """), {"ein": ein}).fetchall()
```

### Step 3.2: Template Updates

Add two sections to `org_detail.html` (after existing 990 sections):

**LLM Metrics section:**
- Table: metric_text | metric_type | value | unit | geo_impact
- Collapsible source_snippet per row

**Impact Stories section:**
- Card layout per story: title, summary, people, program, themes
- Source snippet in muted text below

Only show sections when data exists (`{% if llm_metrics %}`, `{% if llm_stories %}`).

**AC:** AC13 (org detail shows metrics and stories).

## Phase 4: P20 Corpus Validation

**Goal:** Run on full P20 corpus, validate cost and time bounds.

### Step 4.1: Dry Run

```bash
python3 manage.py llm_extract p20-v1 --ntee P2% --dry-run
```

Verify doc count matches expectations (~3,335). Check estimated cost is within $20.

### Step 4.2: Full Run

```bash
python3 manage.py llm_extract p20-v1 --ntee P2% --parallel 10 --cost-limit 20
```

Monitor via dashboard. Verify:
- Completes within 90 minutes
- Total cost under $20
- No persistent API errors (transient retries OK)

### Step 4.3: Quality Spot-Check

After run completes, manually verify 5-10 orgs on the org detail page:
- Metrics look reasonable (not financial junk, not hallucinated)
- Stories reference real people/programs from the reports
- metric_type labels are coherent (will vary, but should be descriptive)
- geo_impact assignments make sense

**AC:** AC16 (cost and time bounds), AC10 (Docling fallback — verified by checking which text source was used per doc in run stats).

## Files to Create/Modify

| File | Action | Phase |
|------|--------|-------|
| `lavandula/migrations/rds/017_llm_extraction_tables.sql` | Create | 1.1 |
| `lavandula/nlp/llm_extract.py` | Create | 1.2 |
| `lavandula/dashboard/pipeline/management/commands/llm_extract.py` | Create | 1.3 |
| `lavandula/dashboard/pipeline/urls.py` | Modify (add 3 routes) | 2.1 |
| `lavandula/dashboard/pipeline/views.py` | Modify (add 3 views + update OrgDetailView) | 2.2, 3.1 |
| `lavandula/dashboard/pipeline/templates/pipeline/llm_extract.html` | Create | 2.3 |
| `lavandula/dashboard/pipeline/templates/pipeline/org_detail.html` | Modify (add 2 sections) | 3.2 |

## Testing Strategy

**Unit tests** (Phase 1):
- `validate_response()` with valid/invalid/empty/hallucinated inputs
- `get_document_text()` with Docling sections, pdftotext fallback, insufficient text
- Argument validation in management command (invalid run_tag, ntee, state, ein)
- Cost calculation from token counts
- Resume logic (skip already-extracted, skip already-skipped)

**Integration tests** (Phase 1):
- End-to-end on single EIN (requires DeepSeek API access)
- Dry-run mode (no API calls)
- Cost-limit abort (set to $0.001, verify abort after first doc)
- SIGINT handling (send signal during run, verify graceful shutdown)

**Dashboard tests** (Phases 2-3):
- LLM extract page renders with empty run history
- Start form validation rejects invalid inputs
- Org detail page renders with/without LLM data
- Status partial returns valid JSON

## Estimated Effort

- Phase 1: ~3-4 hours (migration + extraction module + CLI + fixture test)
- Phase 2: ~2 hours (dashboard page + views + template)
- Phase 3: ~1 hour (org detail additions)
- Phase 4: ~1 hour (full P20 run + spot-check)
- **Total: ~7-8 hours**

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| DeepSeek API rate limiting at parallel=10 | Run takes longer | Reduce parallel, add jitter to backoff |
| pdftotext produces garbage on some PDFs | Metrics quality degrades | Log text_length per doc; Docling fallback when available |
| LLM response format varies | Parse failures | Robust validation + graceful skip, not crash |
| `::jsonb` cast bug | Stats write fails | Use `CAST(:param AS jsonb)` per Trap #4 |
| ThreadPoolExecutor + DB connections | Connection pool exhaustion | Create engine per-worker or use connection-per-call pattern |

## Consultation Summary

### Spec Review - Gemini

**Verdict**: APPROVE
**Confidence**: HIGH
No key issues.

### Spec Review - Codex

**Verdict**: REQUEST_CHANGES
**Confidence**: HIGH
3 findings:
1. Missing stop/abort control for running jobs → **Fixed**: Added `LlmExtractStopView` with poll-based stop via `stats_json.stop_requested` flag.
2. Resume/skip bookkeeping underspecified → **Fixed**: Added explicit skipped_shas persistence per batch and exclusion in resume query.
3. Run-state transitions unclear → **Fixed**: Added run-state detection at startup (refuse if running, require --resume for terminal states).

### Red Team - Gemini

**Verdict**: REQUEST_CHANGES
2 CRITICAL, 2 HIGH, 1 MEDIUM.

1. **CRITICAL — PII logged on JSON parse failure**: Raw LLM response could contain names from stories.
   - **Disposition**: Fixed. Log only first 200 chars of response plus SHA and error type.

2. **CRITICAL — Insecure temp directory for PDF downloads**: Shared /tmp could expose data.
   - **Disposition**: Fixed. Use `tempfile.mkdtemp()` with mode 0o700, auto-cleaned in finally block.

3. **HIGH — No explicit TLS enforcement**: httpx and DB connections could be misconfigured.
   - **Disposition**: Fixed. Added `verify=True` explicitly on httpx client. RDS connections already use SSL via engine config.

4. **HIGH — Dependency version pinning missing**: Unpinned deps could introduce CVEs.
   - **Disposition**: Accepted. Single-operator system, deps managed manually. No CI/CD pipeline to integrate scanning into.

5. **MEDIUM — SIGINT cross-process signal authorization**: Web process sending OS signals is risky.
   - **Disposition**: Fixed. Replaced SIGINT-based stop with poll-based `stats_json.stop_requested` flag.
