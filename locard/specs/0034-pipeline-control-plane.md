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

6. **Forward-compatible for extract/aggregate/report.** The control plane core (Job, JobEvent, orchestrator, dashboard templates) requires zero changes when adding a new stage. A new `extract-vocab` stage needs only: (a) a stage registry entry in `stages.py`, (b) a management command or script, (c) a migration adding its columns to `org_provenance`. The provenance migration is a lightweight `ALTER TABLE ADD COLUMN` — not a control-plane schema change, but a data-model extension that the stage itself owns.

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

## Architecture Decisions

### Orchestrator Topology: Single Central Orchestrator

One `run_orchestrator` process runs on the dashboard host, polling RDS for eligible jobs. It dispatches jobs to remote workers via SSH (already established in Spec 0033's multi-host model). Workers execute subprocesses locally; the orchestrator monitors via heartbeat rows in the `Worker` table.

This means:
- **"Scheduled" = the central orchestrator picked up the job and is about to SSH-dispatch it.** The gap between `scheduled` and `running` is the SSH + process spawn latency.
- **Locking is centralized** — only one process reads/writes job state, so no distributed lock needed. The orchestrator acquires a Postgres advisory lock on startup; a second instance attempting to start will detect the lock and exit. This prevents split-brain from accidental double-launch.
- **Worker liveness** is determined by the orchestrator polling `Worker.last_heartbeat`. Workers update this via a lightweight cron or the running subprocess itself.
- **If the orchestrator dies**, no new jobs are scheduled. Running subprocesses continue on their workers. On restart, the orchestrator reclaims `scheduled` jobs (moves them back to `pending`) and reconciles `running` jobs by checking PIDs on their target hosts.

**SSH dispatch security:**
- Workers are pre-registered in the `Worker` table with their SSH hostname and key fingerprint.
- The orchestrator connects via SSH key authentication (no passwords), using `ssh -o StrictHostKeyChecking=yes` with host keys pinned in `~/.ssh/known_hosts`.
- Remote commands are constructed as explicit argv arrays (`ssh host python3 -m module --arg value`) — never passed through a remote shell. The orchestrator uses `subprocess.Popen(["ssh", host, "--"] + argv)` with `shell=False`.
- Workers do not need inbound SSH access to the orchestrator — communication is one-directional (orchestrator → worker).

### org_provenance Schema: `lava_pipeline`

This is a pipeline control table, not a data table. It lives in `lava_pipeline` alongside `Job`, `JobEvent`, and `Worker`.

### PipelineAuditLog: Superseded by JobEvent

The unused `PipelineAuditLog` model is dropped. `JobEvent` covers its intended purpose with better structure.

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
    parameters: dict[str, ParamSpec]   # see ParamSpec types below
    predecessors: list[str]            # ["seed"] — stages that must be complete before this one
    conflict_group: str | None         # "per-state" or "global" or "990-family"
    provenance_column: str | None      # "resolve_status" — column in org_provenance this stage writes
    retry_policy: RetryPolicy          # max_attempts, backoff, auto_retry
    progress_estimator: str | None     # "count_unresolved" — function name for progress_total
```

**ParamSpec types** define the validation vocabulary for stage parameters:

```python
@dataclass
class ParamSpec:
    required: bool = False
    type: str = "string"  # one of: state_code, string, integer, boolean, choice
    choices: list[str] | None = None  # for type="choice"
    cli_flag: str = ""  # e.g., "--state" — how the param maps to argv
```

Type validators:
- `state_code`: 2-letter uppercase US state abbreviation, validated against a fixed list
- `string`: Non-empty, max 200 chars, alphanumeric + hyphens/underscores only (no shell metacharacters)
- `integer`: Parseable as int, within optional min/max bounds
- `boolean`: Emitted as a flag (`--re-classify`) when true, omitted when false
- `choice`: Value must be in the `choices` list

Parameters are validated BEFORE command construction. Invalid parameters reject the job with a `JobEvent(event_type="failed", payload={"error_class": "invalid_params", ...})`.

The `progress_estimator` is a callable `(config_json: dict) -> int | None` that returns the expected total record count for a job with the given config. For example, for `resolve`, it queries `SELECT COUNT(*) FROM nonprofits_seed WHERE state = :state AND resolver_status IS NULL`. Returns `None` if unknown. Called once at job creation to set `Job.progress_total`.

The registry is a module-level dict in `lavandula/dashboard/pipeline/stages.py`. Adding a new stage = adding an entry. The orchestrator, dashboard views, and job creation forms all read from the registry.

**Command dispatch safety:** All stage commands are executed via `subprocess.Popen(argv, ...)` with `shell=False`. Parameters from `config_json` are validated against the stage's `ParamSpec` definitions and passed as explicit argv elements — never interpolated into strings.

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

The field holds the *current* reason. Each change is also recorded as a `JobEvent(event_type="blocked", payload={"reason": "..."})` so the full blocking history is preserved in the event log. The field is cleared when the job becomes eligible.

**Crash recovery:**

On orchestrator restart:
- Jobs in `scheduled` status (picked up but never spawned) are moved back to `pending` with a `JobEvent(event_type="reset", payload={"reason": "orchestrator restart"})`.
- Jobs in `running` status are reconciled: the orchestrator checks the target host for the recorded PID AND verifies the process start time matches `Job.started_at` (within 5s tolerance) using `/proc/<pid>/stat` or `ps -o lstart`. This prevents PID-reuse false positives. If the PID is alive with matching start time, tracking resumes. If the PID is gone or start time mismatches, the job is marked `failed` with `error_class="orphaned"`.
- `Popen` spawn failures (binary missing, fork error, OS limit) transition directly from `scheduled` → `failed` with the exception recorded in the event log.

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
    -- Stage statuses (enum: not_started, in_progress, completed, failed, not_applicable)
    -- CHECK constraint enforces valid values per column
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

**Population strategy:** Each pipeline stage, on completion, reports per-EIN outcomes. The orchestrator's `_finish_job()` handler calls `update_org_provenance(stage, outcomes)` where `outcomes` is a list of `(ein, status)` tuples — not a single status for the whole batch. This handles partial success: a crawl job that processes 3,537 orgs successfully and 50 with transient failures writes `completed` for 3,537 and `failed` for 50.

The orchestrator assigns each job a provenance output path at dispatch time: `--provenance-out /var/lib/lava/provenance/<job_id>.jsonl`. The stage writes per-EIN outcomes to this file as JSONL: `{"ein": "...", "status": "completed|failed|not_applicable"}`. The orchestrator reads ONLY from this pre-assigned path (validated with `os.path.realpath` + prefix check against the configured base directory). Stages cannot specify an arbitrary provenance path — this prevents path traversal attacks. If the provenance file does not exist at job completion, the orchestrator queries the stage's source tables directly to determine per-EIN outcomes (stage-specific query defined in the stage registry's `provenance_query` callable).

**Seed stage special case:** Seed creates EINs — the `org_provenance` row is created by the seed stage itself (INSERT with all other columns defaulting to `not_started`). Orgs with zero documents after crawl have `classify_status = not_applicable`.

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

**Injection safety:** Structured lines (`PROGRESS:`, `ERROR:`, `SUMMARY:`) are emitted via a dedicated helper (`from lavandula.pipeline_protocol import emit_progress, emit_error, emit_summary`) that writes to a separate file descriptor (fd 3) rather than stdout/stderr. The orchestrator reads fd 3 for structured events and captures stdout/stderr only for the raw log file. This prevents untrusted content in stdout (org names, error traces from crawl targets) from being parsed as structured events.

**Legacy stage handling:** Stages are explicitly marked `protocol_version: 1` (fd 3) or `protocol_version: 0` (legacy) in the stage registry. Legacy stages (v0) do NOT get structured event parsing at all — the orchestrator reads only exit code and log file size for them. No log-tail parsing for structured data. This eliminates the injection vector entirely. All existing stages must be migrated to protocol v1 before the legacy fallback is removed; the stage registry tracks migration status. Target: all stages at v1 by end of Phase 4.

**Provenance column ownership:** Each stage's `provenance_column` in the registry is enforced at write time. The `update_org_provenance()` function validates that the calling stage is writing only to its own column. Attempts to write to another stage's column raise an error and log a security event.

**Payload size cap:** Event payloads are capped at 64KB. Payloads exceeding this are truncated with a `"_truncated": true` flag.

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
1. If `attempts < max_attempts`, create a new job (clone of the failed one) with status `pending`
2. Cloned fields: phase, state_code, host, config_json. Not cloned: depends_on, log_file, pid, events.
3. Write `JobEvent(event_type="retried", payload={"original_job_id": N, "attempt": M, "retry_job_id": R})` on the original job
4. The failed job stays in `failed` status (preserving its events/logs)
5. **Dependent rebinding:** Any `pending` jobs whose `depends_on` pointed to the failed job are updated to point to the retry job. Only pending dependents are rebound — running or completed dependents are not touched. The retry job's `retry_of` FK (new field) links back to the original, forming a chain. The eligibility check follows `retry_of` chains: a dependent waiting on job X is also satisfied if any retry of X completed. A `JobEvent(event_type="dependency_rebound")` is recorded on each affected dependent.
6. **Max retry cap:** `max_attempts` is capped at 3 in the `RetryPolicy` dataclass validation. This prevents infinite retry loops from misconfiguration.

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

**State machine & registry:**
- Unit tests for stage registry validation (missing predecessors, circular dependencies, invalid parameters)
- Unit tests for job lifecycle state machine (valid/invalid transitions, e.g. `completed` → `running` rejected)
- Unit tests for event log creation on each state transition
- Unit tests for command construction from stage registry (never `shell=True`, proper argv splatting)

**Job lifecycle integration:**
- Job creation → dependency block → dependency met → scheduled → running → completed, verifying events at each step
- Job failure → auto-retry → new job created with correct linkage → dependents rebound to retry
- Job cancellation from each non-terminal state

**Crash recovery:**
- Orchestrator restart with jobs in `scheduled` state (should reset to `pending`)
- Orchestrator restart with jobs in `running` state, PID alive (should resume tracking)
- Orchestrator restart with jobs in `running` state, PID gone (should mark `failed`/`orphaned`)
- Spawn failure (binary not found) from `scheduled` state (should mark `failed`)

**Progress protocol:**
- Well-formed PROGRESS/ERROR/SUMMARY lines parsed correctly
- Malformed lines (missing fields, partial writes, embedded newlines) handled gracefully
- Lines exceeding 64KB payload cap are truncated with `_truncated` flag
- Untrusted content on stdout does NOT produce spurious events (fd 3 isolation)
- Legacy stages (no fd 3) fall back to log-tail parsing

**Org provenance:**
- Backfill migration produces correct status for orgs at various pipeline stages
- Partial-success provenance: job completes with mixed EIN outcomes, each written correctly
- Seed stage creates provenance row; subsequent stages update it
- Orgs with zero documents get `classify_status = not_applicable`
- Concurrent provenance writes on different columns for same EIN (last-writer-wins, no conflict)

**Dashboard rendering:**
- Event payloads rendered safely (no XSS via `blocked_reason` or `error_detail`)
- Job detail page shows full event timeline including blocked/rebound events

**Performance smoke:**
- `org_provenance` filter queries at 100K rows < 100ms
- Event log queries for a long-running job (~3K events) < 200ms
- Dashboard job list with 500+ historical jobs renders < 2s

## Resolved Decisions

1. **`org_provenance` lives in `lava_pipeline`.** It's operationally focused — tracks where orgs are in the pipeline, not domain data about them.

2. **Classify provenance = "all known documents classified."** An org with 10 documents, 7 classified and 3 pending, has `classify_status = in_progress`. When all 10 are done, `completed`. An org with zero documents after crawl has `classify_status = not_applicable`.

3. **Event log: no retention policy initially.** Estimated volume: < 500K events/month at national scale. Reassess after Phase 4 (structured progress protocol) adds per-stage progress events — if volume exceeds 5M/month, add a 90-day TTL on `progress` events only, keeping lifecycle events indefinitely.

4. **Dashboard rendering security.** All event payloads and `blocked_reason` text must be rendered through Django's autoescaping. Plan must explicitly prohibit `|safe` filter on any user-facing field derived from job events or log content.
