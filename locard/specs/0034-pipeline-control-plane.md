# Spec 0034: Pipeline Control Plane & Org Provenance

## Problem Statement

The pipeline grew organically from a single-host crawl tool into a multi-host, multi-phase national ingest system. Each phase was engineered independently, and the "control plane" — job lifecycle, org status tracking, logging — was bolted on after the fact. The result:

1. **Jobs silently fail to start.** Dependency resolution is a single FK check with no visibility into *why* a job is blocked. No scheduler log, no "waiting on job #X" feedback.

2. **Exit codes require detective work.** A failed job shows "Exit 1: partial failure" — but understanding what actually happened requires reading raw log files. There's no structured job summary.

3. **Org pipeline status is fragmented.** Knowing "where is EIN 12-3456789 in the pipeline?" requires joining `nonprofits_seed.resolver_status`, `crawled_orgs.status`, `corpus.classification`, `filing_index.status`, and `people` — each with its own conventions.

4. **New stages can't plug in cleanly.** The coming vocabulary extraction, aggregation, and report generation stages will each need their own status tracking, job types, and logging. The current pattern of adding a new COMMAND_MAP entry and hoping everything fits is not sustainable.

## Goals

### Must Have

1. **Job lifecycle with clear state transitions and audit trail.** Every state change (pending → scheduled → running → completed/failed/cancelled) is logged with timestamp, reason, and actor. "Scheduled" is a new state: the job has been picked up by the scheduler but the subprocess hasn't started yet — this is where "silently didn't start" becomes visible.

2. **Structured job summaries.** When a job finishes, the orchestrator writes a machine-readable summary (JSON) alongside the log: duration, records processed, records failed, exit code, error classification, last meaningful error line. The dashboard reads the summary, not the log tail.

3. **Unified org provenance view.** A single materialized table (or view) that answers "where is this org in the pipeline?" across all stages. One row per org, one column per stage, value = status enum (not_started | in_progress | completed | failed | skipped). Updated as each stage completes.

4. **Stage registry.** Pipeline stages declared as configuration (not hardcoded in COMMAND_MAP). Each stage declares: name, command template, required parameters, valid predecessor stages, phase conflict rules, and the status column it writes. Adding a new stage = adding a config entry + the actual code.

5. **Dependency resolution with diagnostics.** When a job can't start, the system records *why* (dependency not met, phase conflict, host unavailable, predecessor failed). Visible on the dashboard job detail page.

6. **Forward-compatible for extract/aggregate/report.** The design must accommodate stages that don't exist yet without requiring schema changes to the control plane itself. A new `extract-vocab` stage should only need: (a) a stage registry entry, (b) a management command or script, (c) an org_provenance column.

### Should Have

7. **Job event log.** A time-series table of job events (created, scheduled, started, heartbeat, progress, warning, error, completed). Replaces log-tail scraping for dashboard display. Events are structured (JSON payload) not free-text.

8. **Retry policy per stage.** Configurable: how many times to retry a failed job, backoff strategy, whether to auto-retry or require manual intervention. Currently only exit-code-3 (lock busy) retries — everything else is terminal.

9. **Org provenance history.** Not just current status but when each stage completed for each org. Enables "how long does crawl take per org?" and "which orgs have been stuck in resolve for 7+ days?"

### Nice to Have

10. **Stage DAG visualization.** Dashboard page showing the pipeline as a directed graph with per-stage org counts (how many orgs are at each stage).

11. **Alerting hooks.** When a job fails or an org is stuck, emit a webhook/notification. Not critical for single-operator but useful as complexity grows.

## Non-Goals

- **Workflow engine replacement** (Airflow, Prefect, Dagster). This is a lightweight control plane for a single-operator system, not an enterprise orchestrator. The complexity budget is "Django models + management commands," not "install and maintain a workflow platform."
- **Real-time streaming logs.** The dashboard shows job summaries and event logs, not live log tails. Operators who need live logs use `tail -f` on the host.
- **Multi-tenant isolation.** Single operator (ronp), single RDS instance. No role-based access, no team permissions.

## Current State

### Job Model (pipeline/models.py)
- Status: pending → running → completed | failed | cancelled
- `depends_on` FK for simple chaining
- `log_tail` (last 16KB of log file, unstructured text)
- `error_message` (single line extracted by regex from log)
- `config_json` (phase parameters, validated by COMMAND_MAP)

### Orchestrator (run_orchestrator.py)
- Polls every 30s for eligible jobs
- Spawns subprocess, tracks PID
- On completion: reads log tail, extracts error line, sets status
- Exit code hints: hardcoded dict mapping codes to human-readable strings
- Phase conflict: prevents two jobs of same phase+state from running simultaneously

### Org Status (fragmented)
- `nonprofits_seed.resolver_status`: null | resolved | rejected | error
- `crawled_orgs.status`: ok | transient | permanent_skip (+ attempts counter)
- `corpus.classification`: null | annual | impact | newsletter | ... (per-document, not per-org)
- `filing_index.status`: indexed | downloaded | parsed (per-filing, not per-org)
- `people`: exists or doesn't (per-filing)
- No unified view. No way to answer "show me all orgs that are resolved but not yet crawled" without a multi-table join.

### Logging
- `logging_utils.py`: RotatingFileHandler, sanitized, format string based
- `decisions_log.py`: Structured JSONL for crawler decisions (per-candidate)
- Job logs: raw subprocess stdout/stderr captured to files
- `PipelineAuditLog` model: exists but unused

## Technical Design

### 1. Stage Registry

Replace `COMMAND_MAP` with a declarative stage registry. Each stage is a Python dataclass (not YAML — it needs to be importable for validation and IDE support).

```python
@dataclass
class StageDefinition:
    name: str                          # "resolve", "crawl", "classify", "extract-vocab"
    display_name: str                  # "URL Resolver", "Site Crawler"
    command: list[str]                 # ["python3", "-m", "lavandula.nonprofits.tools.pipeline_resolve"]
    parameters: dict[str, ParamSpec]   # {"state": ParamSpec(required=True, type="state_code"), ...}
    predecessors: list[str]            # ["seed"] — stages that must be complete before this one
    conflict_group: str | None         # "per-state" or "global" or "990-family"
    provenance_column: str | None      # "resolve_status" — column in org_provenance this stage writes
    retry_policy: RetryPolicy          # max_attempts, backoff, auto_retry
    progress_estimator: str | None     # "count_unresolved" — function name for progress_total
```

The registry is a module-level dict in `lavandula/dashboard/pipeline/stages.py`. Adding a new stage = adding an entry. The orchestrator, dashboard views, and job creation forms all read from the registry.

### 2. Job Lifecycle Enhancement

**New status: `scheduled`**

```
pending → scheduled → running → completed
                              → failed → (auto-retry) → pending
                              → cancelled
```

- `pending`: Created, waiting for dependencies and eligibility
- `scheduled`: Picked up by scheduler, pre-launch checks passed. This is the window where "silently didn't start" currently hides.
- `running`: Subprocess spawned, PID recorded
- `completed` / `failed` / `cancelled`: Terminal states

**Blocked reason tracking:**

New field `blocked_reason` (nullable text) on Job, set by the scheduler when a job can't transition from pending → scheduled:
- "Waiting on job #231 (crawl CA) to complete"
- "Phase conflict: resolve WA already running (job #245)"
- "Host cloud1 not responding (last heartbeat 15m ago)"
- "Predecessor stage 'resolve' not complete for state WA"

Cleared when the job becomes eligible.

### 3. Job Event Log

New model `JobEvent`:

```python
class JobEvent(models.Model):
    job = models.ForeignKey(Job, related_name="events")
    timestamp = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(choices=[
        ("created", "Created"),
        ("scheduled", "Scheduled"),
        ("started", "Started"),
        ("progress", "Progress"),
        ("warning", "Warning"),
        ("error", "Error"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("retried", "Retried"),
    ])
    payload = models.JSONField(default=dict)
    # payload examples:
    # {"blocked_reason": "Waiting on job #231"}
    # {"progress_current": 500, "progress_total": 3589, "rate": "437 orgs/hr"}
    # {"exit_code": 1, "error_class": "flush_failure", "error_detail": "3 unresolved flush failures"}
    # {"duration_s": 29376, "records_processed": 3537, "records_failed": 50}
```

The orchestrator writes events instead of updating `log_tail`. The dashboard reads events for the job detail page. Progress updates are events (replacing log-line parsing).

### 4. Structured Job Summary

When a job completes or fails, the orchestrator writes a `JobEvent` with `event_type="completed"` or `"failed"` and a payload containing:

```json
{
    "duration_s": 29376,
    "exit_code": 0,
    "records_processed": 3537,
    "records_failed": 50,
    "records_skipped": 2,
    "error_class": null,
    "error_detail": null,
    "peak_rss_kb": 894032,
    "artifacts": {
        "pdfs_fetched": 9235,
        "bytes_downloaded": 32443967082,
        "wayback_recoveries": 47
    }
}
```

The `artifacts` dict is stage-specific (each stage reports its own metrics). The fixed fields (duration, exit_code, records_*) are common across all stages. The summary is parsed from the final log line of each stage (which already emits structured `===DONE===` lines) and from the exit code.

### 5. Org Provenance Table

New table `org_provenance` in `lava_pipeline` schema:

```sql
CREATE TABLE lava_pipeline.org_provenance (
    ein TEXT PRIMARY KEY,
    -- Stage statuses (enum: not_started, in_progress, completed, failed, skipped)
    seed_status       TEXT NOT NULL DEFAULT 'not_started',
    seed_completed_at TIMESTAMPTZ,
    resolve_status       TEXT NOT NULL DEFAULT 'not_started',
    resolve_completed_at TIMESTAMPTZ,
    crawl_status       TEXT NOT NULL DEFAULT 'not_started',
    crawl_completed_at TIMESTAMPTZ,
    classify_status       TEXT NOT NULL DEFAULT 'not_started',
    classify_completed_at TIMESTAMPTZ,
    filing_990_status       TEXT NOT NULL DEFAULT 'not_started',
    filing_990_completed_at TIMESTAMPTZ,
    -- Future stages — added via ALTER TABLE when stage is registered
    -- extract_vocab_status, aggregate_status, report_status, etc.
    
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Population strategy:** Each pipeline stage, on completion, updates its corresponding column. The orchestrator's `_finish_job()` handler calls `update_org_provenance(stage, ein_list, status)` with the list of EINs processed.

**Backfill:** One-time migration reads existing `nonprofits_seed`, `crawled_orgs`, and `corpus` tables to populate initial provenance state.

**Dashboard view:** New "Org Pipeline Status" page shows a filterable table: one row per org, columns for each stage, color-coded by status. Filter by state, NTEE code, or "stuck" (any stage failed or in_progress for > N days).

### 6. Forward Compatibility

When a new stage (e.g., `extract-vocab`) is added:

1. **Add to stage registry** in `stages.py`
2. **Add migration** `ALTER TABLE org_provenance ADD COLUMN extract_vocab_status TEXT DEFAULT 'not_started', ADD COLUMN extract_vocab_completed_at TIMESTAMPTZ;`
3. **Write the stage code** (management command or script) that does the work and emits structured `===DONE===` lines
4. **The rest is automatic:** The orchestrator knows how to dispatch it, the dashboard knows how to show it, the provenance table knows how to track it.

No changes to the orchestrator, job model, event log, or dashboard templates. The stage registry drives everything.

### 7. Logging Rationalization

**Eliminate log-tail scraping.** The orchestrator no longer reads log files to extract error information. Instead:

- Progress: pipeline stages emit structured progress to stdout in a known format (e.g., `PROGRESS: current=500 total=3589 rate=437`). The orchestrator's heartbeat loop parses this and writes `JobEvent(event_type="progress")`.
- Errors: stages emit `ERROR: class=flush_failure detail=3 unresolved flush failures` to stderr. The orchestrator captures these as `JobEvent(event_type="error")`.
- Summary: stages emit `SUMMARY: records_processed=3537 records_failed=50 ...` as their final line. The orchestrator parses this into the completion event.

The raw log file still exists (for debugging), but the dashboard never reads it directly.

**Structured progress protocol:** A simple line-based protocol that all stages follow:

```
PROGRESS: current=N total=M [key=value ...]
ERROR: class=NAME detail=TEXT
WARNING: class=NAME detail=TEXT  
SUMMARY: duration_s=N records_processed=N records_failed=N [key=value ...]
```

This is intentionally simple — not JSON, not protobuf. It's `key=value` pairs on tagged lines that can be parsed with a regex and are still human-readable in raw logs.

### 8. Retry Policy

Per-stage retry configuration:

```python
@dataclass
class RetryPolicy:
    max_attempts: int = 1         # 1 = no retry
    auto_retry: bool = False      # True = orchestrator retries automatically
    backoff_seconds: int = 60     # Wait between retries
    retryable_exit_codes: list[int] = field(default_factory=lambda: [1, 3])
```

When a job fails with a retryable exit code:
1. If `attempts < max_attempts`, create a new job (clone of the failed one) with `depends_on=None`, status `pending`
2. Write `JobEvent(event_type="retried", payload={"original_job_id": N, "attempt": M})`
3. The failed job stays in `failed` status (preserving its events/logs)

This replaces the current exit-code-3 special case in the orchestrator.

## Migration Path

This is a significant refactor touching the orchestrator, job model, and dashboard. Phase it:

**Phase 1: Stage registry + job lifecycle** — Replace COMMAND_MAP with registry, add `scheduled` status, add `blocked_reason`. Minimal dashboard changes (show blocked reason on job list).

**Phase 2: Event log + structured summaries** — Add `JobEvent` model, refactor orchestrator to write events, update dashboard job detail to show events. Remove log-tail scraping.

**Phase 3: Org provenance** — New table, backfill migration, dashboard provenance page. Pipeline stages updated to write provenance on completion.

**Phase 4: Structured progress protocol** — Update each pipeline stage to emit PROGRESS/ERROR/SUMMARY lines. Orchestrator heartbeat parses them into events.

Phases 1-2 can ship without changing any pipeline stage code. Phase 3 changes the data model. Phase 4 changes every pipeline stage (but each stage can be migrated independently).

## Traps to Avoid

1. **Don't over-engineer the stage registry.** It should be a Python dict of dataclasses, not a plugin system with dynamic loading. We have ~10 stages, not 100.

2. **Don't migrate all stages to the progress protocol at once.** The orchestrator should gracefully handle stages that don't emit structured lines (fall back to log-tail parsing for legacy stages).

3. **Don't make org_provenance a view.** It needs to be a table (materialized) because the source tables have different schemas and the join is expensive at 100K+ orgs. Write-through on stage completion, not computed on read.

4. **Don't add real-time log streaming.** The temptation will be strong. Resist it. Structured events + raw log files cover all use cases without the WebSocket complexity.

5. **The provenance table will need new columns as stages are added.** This is fine — `ALTER TABLE ADD COLUMN` is cheap in Postgres. Don't try to make it "schema-free" with a JSON column; explicit columns are queryable and type-safe.

## Testing Requirements

- Unit tests for stage registry validation (missing predecessors, circular dependencies, invalid parameters)
- Unit tests for job lifecycle state machine (valid/invalid transitions)
- Unit tests for event log creation on each state transition
- Integration test: job creation → dependency block → dependency met → scheduled → running → completed, verifying events at each step
- Integration test: job failure → auto-retry → new job created with correct linkage
- Migration test: backfill org_provenance from existing tables produces correct status for sampled orgs

## Open Questions

1. **Should `org_provenance` live in `lava_pipeline` or `lava_impact`?** It's a pipeline control table, but it's keyed by EIN which is a data concept. Recommend `lava_pipeline` since it's operationally focused.

2. **How granular should classify provenance be?** Currently classification is per-document, not per-org. An org might have 10 documents, 7 classified and 3 pending. Should provenance track "all documents classified" or "at least one classified"? Recommend: `classify_status = completed` when all known documents for that org are classified.

3. **Should the event log have a retention policy?** At 1 event per heartbeat (30s) per running job, a 24-hour crawl generates ~2,880 events. Across 50 states with parallel jobs, that's manageable (< 500K events/month). Probably fine to keep indefinitely for the first year, then add TTL if needed.
