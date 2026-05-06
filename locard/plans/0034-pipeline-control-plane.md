# Plan 0034: Pipeline Control Plane & Org Provenance

## Overview

Implementation plan for Spec 0034. Four phases, each independently shippable. Phases 1-2 don't change pipeline stage code. Phase 3 adds data model. Phase 4 migrates each stage to the structured protocol.

**Estimated effort:** ~3-4 days agentic time across all phases.

## Phase 1: Stage Registry + Job Lifecycle (Foundation)

### 1.1 Stage Registry Module

**File:** `lavandula/dashboard/pipeline/stages.py` (NEW)

Create the stage registry with dataclasses and register all existing stages:

```python
from dataclasses import dataclass, field

@dataclass
class ParamSpec:
    required: bool = False
    type: str = "string"
    choices: list[str] | None = None
    cli_flag: str = ""
    min_value: int | None = None
    max_value: int | None = None

@dataclass
class RetryPolicy:
    max_attempts: int = 1
    auto_retry: bool = False
    backoff_seconds: int = 60
    retryable_exit_codes: list[int] = field(default_factory=lambda: [1, 3])

@dataclass
class StageDefinition:
    name: str
    display_name: str
    command: list[str]
    parameters: dict[str, ParamSpec]
    predecessors: list[str]
    conflict_group: str | None
    provenance_column: str | None
    retry_policy: RetryPolicy
    resource_class: str  # "heavy" | "medium" | "light"
    progress_estimator: str | None = None
    protocol_version: int = 0  # 0 = legacy, 1 = fd 3
    provenance_query: str | None = None  # SQL template for fallback provenance

STAGE_REGISTRY: dict[str, StageDefinition] = { ... }
```

Register existing stages: seed, resolve, crawl, classify, 990-index, 990-parse, enrich-phone.

**Validation function:** `validate_registry()` checks:
- No circular predecessors
- All predecessor names exist in registry
- No duplicate provenance_column values
- All conflict_groups are valid
- RetryPolicy.max_attempts ≤ 3

### 1.2 Parameter Validation

**File:** `lavandula/dashboard/pipeline/param_validators.py` (NEW)

```python
US_STATES = {"AL", "AK", "AZ", ...}  # All 50 + DC + territories

def validate_param(value: str, spec: ParamSpec) -> str:
    """Validate and return cleaned value. Raises ValueError on invalid."""
    ...

def build_argv(stage: StageDefinition, config_json: dict) -> list[str]:
    """Build subprocess argv from stage command + validated params."""
    argv = list(stage.command)
    for name, spec in stage.parameters.items():
        if name in config_json:
            clean = validate_param(config_json[name], spec)
            if spec.type == "boolean" and clean == "true":
                argv.append(spec.cli_flag)
            elif spec.type != "boolean":
                argv.extend([spec.cli_flag, clean])
    return argv
```

### 1.3 Job Model Migration

**Migration:** `lavandula/dashboard/pipeline/migrations/XXXX_job_lifecycle_v2.py`

Add fields to Job model:
- `status` choices: add `"scheduled"` 
- `blocked_reason` (TextField, nullable)
- `retry_of` (ForeignKey to Job, nullable, related_name="retries")
- `attempt_number` (IntegerField, default=1)

### 1.4 Orchestrator Refactor

**File:** `lavandula/dashboard/pipeline/management/commands/run_orchestrator.py`

Refactor to use stage registry:
1. Replace `COMMAND_MAP` imports with `from pipeline.stages import STAGE_REGISTRY`
2. Replace `build_argv()` calls with registry-based version
3. Add advisory lock on startup: `SELECT pg_try_advisory_lock(34001)` — exit if lock not acquired
4. Add `scheduled` state: job moves to `scheduled` before spawn attempt
5. Add crash recovery on startup:
   - `scheduled` jobs → reset to `pending`
   - `running` jobs → verify PID+start-time, mark orphaned if dead
6. Add `blocked_reason` updates in eligibility check loop
7. Add retry logic: on retryable failure, clone job + rebound dependents

### 1.5 Scheduler Scoring Function

**File:** `lavandula/dashboard/pipeline/scheduler.py` (NEW)

```python
def score_placement(job: Job, worker: Worker, config: SchedulerConfig) -> float | None:
    """Score a job-host pairing. Returns None if hard-rule blocked."""
    # Hard rules (gates)
    if worker.cpu_pct and worker.cpu_pct > config.cpu_ceiling_pct:
        return None
    if worker.mem_pct and worker.mem_pct > config.memory_ceiling_pct:
        return None
    running_heavy = Job.objects.filter(host=worker.hostname, status="running", phase__in=heavy_phases).count()
    if running_heavy >= config.max_concurrent_heavy:
        return None

    # Soft scoring
    score = 1.0
    stage = STAGE_REGISTRY[job.phase]
    stage_config = config.stage_weights.get(job.phase, {})
    
    # Host affinity
    if worker.hostname in stage_config.get("prefer_hosts", []):
        score += 0.3
    
    # Memory headroom bonus
    if worker.mem_pct:
        score += (100 - worker.mem_pct) / 100 * 0.5
    
    # Starvation boost
    wait_minutes = (now() - job.created_at).total_seconds() / 60
    if wait_minutes > config.starvation_boost_after_minutes:
        score += 0.5
    
    return score

def select_next_job(pending_jobs, workers, config) -> tuple[Job, Worker] | None:
    """Pick the best job-host pair from all candidates."""
    ...
```

### 1.6 Scheduler Config

**File:** `lavandula/dashboard/pipeline/scheduler_config.py` (NEW)

Loads `scheduler_config.yaml` from a configurable path. Watches file mtime and reloads on change. Provides sensible defaults if file is missing.

**File:** `lavandula/dashboard/scheduler_config.yaml` (NEW)

Initial config with conservative defaults based on current operational knowledge.

### 1.7 Dashboard Updates (Minimal)

- Job list: show `blocked_reason` in a tooltip/column for pending jobs
- Job list: show `scheduled` status with distinct color
- Job creation forms: read stage parameters from registry (replaces hardcoded form fields)

### 1.8 Tests

- `tests/test_stages.py`: Registry validation, param validation, argv construction
- `tests/test_scheduler.py`: Scoring function unit tests, hard-rule gates, starvation boost
- `tests/test_job_lifecycle.py`: State transitions, retry+rebound, crash recovery

**Acceptance Criteria (Phase 1):**
- [ ] AC1: Stage registry contains all existing stages with correct commands
- [ ] AC2: `validate_registry()` passes; invalid registries rejected
- [ ] AC3: `build_argv()` produces correct argv for each stage
- [ ] AC4: Parameter validation rejects shell metacharacters, invalid states
- [ ] AC5: Job model has `scheduled` status, `blocked_reason`, `retry_of` fields
- [ ] AC6: Orchestrator acquires advisory lock; second instance exits cleanly
- [ ] AC7: Jobs transition through `pending → scheduled → running → completed`
- [ ] AC8: Blocked reason populated and visible on dashboard
- [ ] AC9: Crash recovery: `scheduled` reset to `pending` on restart
- [ ] AC10: Crash recovery: `running` jobs reconciled via PID + start time
- [ ] AC11: Retry: failed job with retryable code spawns retry, dependents rebound
- [ ] AC12: Scheduler scoring respects memory ceiling (hard rule)
- [ ] AC13: Scheduler scoring applies host affinity preference (soft weight)
- [ ] AC14: `scheduler_config.yaml` hot-reload on file change
- [ ] AC15: Dashboard shows blocked reason and scheduled status

---

## Phase 2: Event Log + Structured Summaries

### 2.1 JobEvent Model

**Migration:** `lavandula/dashboard/pipeline/migrations/XXXX_job_event.py`

```python
class JobEvent(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="events")
    timestamp = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, db_index=True)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["job", "timestamp"]),
            models.Index(fields=["event_type", "timestamp"]),
        ]
```

### 2.2 Event Emission in Orchestrator

Refactor `run_orchestrator.py` to emit events at each state transition:
- Job created → `JobEvent(event_type="created")`
- Eligibility check fails → `JobEvent(event_type="blocked", payload={"reason": ...})`
- Job scheduled → `JobEvent(event_type="scheduled")`
- Job started → `JobEvent(event_type="started", payload={"pid": ..., "host": ...})`
- Job completed → `JobEvent(event_type="completed", payload={summary})`
- Job failed → `JobEvent(event_type="failed", payload={summary + error})`

### 2.3 Summary Parsing

**File:** `lavandula/dashboard/pipeline/summary_parser.py` (NEW)

Parses the final `=== ... DONE ===` line from each stage's log into the structured summary payload. Each stage has a slightly different format; the parser handles known formats and produces a normalized dict.

For protocol v1 stages (Phase 4), this is replaced by fd 3 SUMMARY parsing.

### 2.4 Payload Size Enforcement

All event payloads pass through `truncate_payload(payload, max_bytes=65536)` before save.

### 2.5 Dashboard: Job Detail Timeline

**Template:** `pipeline/templates/pipeline/job_detail.html` (MODIFY)

Replace the current `log_tail` display with a timeline of `JobEvent` records:
- Vertical timeline, most recent at top
- Each event shows: timestamp, type badge (color-coded), payload summary
- Progress events collapsed by default (expandable)
- Error events highlighted in red

**Security:** All payload values rendered via `{{ value }}` (autoescaped). No `|safe` filter anywhere in job detail templates.

### 2.6 Deprecate log_tail

- Stop writing `log_tail` on job completion (orchestrator no longer reads log files for dashboard display)
- Leave the field on the model for now (remove in a future cleanup)
- Dashboard no longer reads `log_tail`; reads `job.events.all()` instead

### 2.7 Tests

- `tests/test_job_events.py`: Event creation on each transition, payload structure, truncation
- `tests/test_summary_parser.py`: Parse each stage's DONE line format
- `tests/test_job_detail_view.py`: Timeline renders correctly, XSS safety

**Acceptance Criteria (Phase 2):**
- [ ] AC16: JobEvent model created with indexes
- [ ] AC17: Every state transition creates a corresponding event
- [ ] AC18: Completed/failed events contain structured summary payload
- [ ] AC19: Payloads exceeding 64KB are truncated with `_truncated` flag
- [ ] AC20: Dashboard job detail shows event timeline
- [ ] AC21: No `|safe` filter used on any event-derived content
- [ ] AC22: Progress events created from heartbeat polling
- [ ] AC23: log_tail field no longer written (backward compat: still readable if populated)

---

## Phase 3: Org Provenance

### 3.1 Provenance Table Migration

**Migration:** `lavandula/migrations/rds/0XX_org_provenance.sql`

```sql
CREATE SCHEMA IF NOT EXISTS lava_pipeline;

CREATE TABLE lava_pipeline.org_provenance (
    ein TEXT PRIMARY KEY,
    seed_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (seed_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    seed_completed_at TIMESTAMPTZ,
    resolve_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (resolve_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    resolve_completed_at TIMESTAMPTZ,
    crawl_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (crawl_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    crawl_completed_at TIMESTAMPTZ,
    classify_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (classify_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    classify_completed_at TIMESTAMPTZ,
    filing_990_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (filing_990_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    filing_990_completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_provenance_resolve ON lava_pipeline.org_provenance(resolve_status);
CREATE INDEX idx_provenance_crawl ON lava_pipeline.org_provenance(crawl_status);
CREATE INDEX idx_provenance_classify ON lava_pipeline.org_provenance(classify_status);
```

### 3.2 Django Model

**File:** `lavandula/dashboard/pipeline/models.py` (MODIFY)

Add `OrgProvenance` model pointing to the `lava_pipeline.org_provenance` table. Managed=False (migrations via raw SQL, not Django ORM).

### 3.3 Provenance Writer

**File:** `lavandula/dashboard/pipeline/provenance.py` (NEW)

```python
def update_org_provenance(stage_name: str, outcomes: list[tuple[str, str]]) -> int:
    """Update provenance for a batch of EINs. Returns count updated."""
    stage = STAGE_REGISTRY[stage_name]
    column = stage.provenance_column
    if not column:
        return 0
    # Validate column ownership
    assert column == f"{stage_name}_status" or column in ALLOWED_COLUMNS[stage_name]
    # Batch UPDATE
    ...
```

### 3.4 Provenance File Handling

On job dispatch, orchestrator passes `--provenance-out /var/lib/lava/provenance/<job_id>.jsonl`.

On job completion:
1. Check if provenance file exists at the pre-assigned path
2. Validate: `os.path.realpath(path).startswith(PROVENANCE_BASE_DIR)`
3. Parse JSONL, validate each `{"ein": str, "status": str}`
4. Call `update_org_provenance(stage, outcomes)`
5. Delete provenance file after successful ingestion

If no file exists, call `stage.provenance_query(config_json)` to determine outcomes from source tables.

### 3.5 Backfill Migration

**File:** `lavandula/dashboard/pipeline/management/commands/backfill_provenance.py` (NEW)

One-time command that reads existing tables and populates `org_provenance`:

```sql
-- Seed: all EINs in nonprofits_seed
INSERT INTO lava_pipeline.org_provenance (ein, seed_status, seed_completed_at)
SELECT ein, 'completed', created_at FROM lava_impact.nonprofits_seed
ON CONFLICT (ein) DO NOTHING;

-- Resolve: from resolver_status
UPDATE lava_pipeline.org_provenance SET
    resolve_status = CASE
        WHEN s.resolver_status IN ('resolved', 'accepted') THEN 'completed'
        WHEN s.resolver_status IN ('rejected', 'error') THEN 'failed'
        ELSE 'not_started'
    END
FROM lava_impact.nonprofits_seed s WHERE org_provenance.ein = s.ein;

-- Crawl: from crawled_orgs
UPDATE lava_pipeline.org_provenance SET
    crawl_status = CASE
        WHEN co.status = 'ok' THEN 'completed'
        WHEN co.status = 'permanent_skip' THEN 'failed'
        WHEN co.status = 'transient' THEN 'in_progress'
        ELSE 'not_started'
    END,
    crawl_completed_at = co.last_crawled_at
FROM lava_impact.crawled_orgs co WHERE org_provenance.ein = co.ein;

-- Classify: completed if all documents for org are classified
UPDATE lava_pipeline.org_provenance SET
    classify_status = CASE
        WHEN NOT EXISTS (SELECT 1 FROM lava_corpus.corpus c
            WHERE c.source_org_ein = org_provenance.ein) THEN 'not_applicable'
        WHEN NOT EXISTS (SELECT 1 FROM lava_corpus.corpus c
            WHERE c.source_org_ein = org_provenance.ein AND c.material_type IS NULL) THEN 'completed'
        ELSE 'in_progress'
    END;
```

### 3.6 Dashboard: Provenance Page

**Template:** `pipeline/templates/pipeline/provenance.html` (NEW)

Table with columns: EIN, Org Name, State, NTEE, Seed, Resolve, Crawl, Classify, 990.
Each stage cell color-coded: green=completed, yellow=in_progress, red=failed, gray=not_started.
Filters: state dropdown, status dropdown per stage, "stuck" checkbox (failed or in_progress > 7 days).
Paginated (50 per page).

**View:** `lavandula/dashboard/pipeline/views.py` (MODIFY)

New view `provenance_list` with queryset filters.

### 3.7 Tests

- `tests/test_provenance.py`: Write outcomes, column ownership enforcement, partial success
- `tests/test_backfill.py`: Backfill produces correct state for test fixtures
- `tests/test_provenance_views.py`: Dashboard page renders, filters work, XSS safe

**Acceptance Criteria (Phase 3):**
- [ ] AC24: `org_provenance` table created with CHECK constraints
- [ ] AC25: Backfill populates correct status for orgs at each pipeline stage
- [ ] AC26: `update_org_provenance` writes correct per-EIN outcomes
- [ ] AC27: Column ownership enforced — stage can only write its own column
- [ ] AC28: Provenance file path validated (realpath + prefix check)
- [ ] AC29: Fallback to `provenance_query` when no file exists
- [ ] AC30: Dashboard provenance page renders with color-coded statuses
- [ ] AC31: Filters (state, status, stuck) produce correct results
- [ ] AC32: Performance: provenance queries < 100ms at 100K rows

---

## Phase 4: Structured Progress Protocol

### 4.1 Protocol Library

**File:** `lavandula/pipeline_protocol.py` (NEW)

```python
import os
import sys

_FD3 = None

def _get_fd3():
    global _FD3
    if _FD3 is None:
        try:
            _FD3 = os.fdopen(3, 'w', buffering=1)  # line-buffered
        except OSError:
            _FD3 = False  # fd 3 not available
    return _FD3 if _FD3 else None

def emit_progress(current: int, total: int | None = None, **kwargs):
    fd = _get_fd3()
    if fd:
        parts = [f"current={current}"]
        if total is not None:
            parts.append(f"total={total}")
        parts.extend(f"{k}={v}" for k, v in kwargs.items())
        fd.write(f"PROGRESS: {' '.join(parts)}\n")

def emit_error(error_class: str, detail: str):
    fd = _get_fd3()
    if fd:
        detail_safe = detail.replace('\n', ' ')[:1000]
        fd.write(f"ERROR: class={error_class} detail={detail_safe}\n")

def emit_summary(**kwargs):
    fd = _get_fd3()
    if fd:
        parts = [f"{k}={v}" for k, v in kwargs.items()]
        fd.write(f"SUMMARY: {' '.join(parts)}\n")
```

### 4.2 Orchestrator fd 3 Reader

**File:** `lavandula/dashboard/pipeline/protocol_reader.py` (NEW)

When spawning a protocol v1 stage, the orchestrator:
1. Creates a pipe: `r_fd, w_fd = os.pipe()`
2. Passes `w_fd` as fd 3 to the subprocess (via `pass_fds=(w_fd,)` + pre-exec dup2)
3. Reads from `r_fd` in the heartbeat loop, parsing structured lines
4. Each parsed line becomes a `JobEvent`

For protocol v0 stages: fd 3 is not opened. No structured event parsing. Exit code + log file size only.

### 4.3 Stage Migrations (one per stage)

Migrate each existing stage to protocol v1. For each:
1. Add `from lavandula.pipeline_protocol import emit_progress, emit_error, emit_summary` 
2. Replace periodic print statements with `emit_progress()`
3. Add `emit_error()` at exception handlers
4. Add `emit_summary()` as final action before exit
5. Write provenance JSONL file at `--provenance-out` path
6. Update stage registry: `protocol_version=1`

**Order of migration:**
1. `crawl` (most complex, highest value — eliminates "Exit 1" mystery)
2. `resolve` (second most frequent)
3. `classify` (simple, few progress points)
4. `seed` (rare, simple)
5. `990-index`, `990-parse` (group together)
6. `enrich-phone`

Each stage migration is an independent commit, independently testable.

### 4.4 Tests

- `tests/test_protocol.py`: emit_* functions write correct format to fd 3
- `tests/test_protocol_reader.py`: Reader parses well-formed lines, handles malformed
- `tests/test_protocol_integration.py`: Full cycle — subprocess emits, orchestrator captures events

**Acceptance Criteria (Phase 4):**
- [ ] AC33: `pipeline_protocol.py` library emits correct format on fd 3
- [ ] AC34: Library gracefully handles fd 3 unavailable (no-op)
- [ ] AC35: Orchestrator opens pipe, passes as fd 3, reads structured lines
- [ ] AC36: Parsed lines create JobEvents with correct payloads
- [ ] AC37: Malformed lines logged as warnings, don't crash orchestrator
- [ ] AC38: Crawler stage migrated to v1, emits progress/error/summary
- [ ] AC39: Resolver stage migrated to v1
- [ ] AC40: Classifier stage migrated to v1
- [ ] AC41: All stages write provenance JSONL at assigned path
- [ ] AC42: Legacy v0 stages still work (exit code only, no event parsing)

---

## File Summary

### New Files (13)
| File | Phase | Purpose |
|------|-------|---------|
| `pipeline/stages.py` | 1 | Stage registry definitions |
| `pipeline/param_validators.py` | 1 | Parameter validation + argv construction |
| `pipeline/scheduler.py` | 1 | Scoring function for job-host placement |
| `pipeline/scheduler_config.py` | 1 | YAML config loader with hot-reload |
| `dashboard/scheduler_config.yaml` | 1 | Operator-tunable scheduling weights |
| `pipeline/summary_parser.py` | 2 | Parse stage DONE lines into summary payload |
| `pipeline/provenance.py` | 3 | Provenance writer with ownership enforcement |
| `pipeline/management/commands/backfill_provenance.py` | 3 | One-time backfill from existing tables |
| `pipeline/templates/pipeline/provenance.html` | 3 | Provenance dashboard page |
| `lavandula/pipeline_protocol.py` | 4 | Structured event emission library |
| `pipeline/protocol_reader.py` | 4 | fd 3 pipe reader for orchestrator |
| `migrations/rds/0XX_org_provenance.sql` | 3 | Provenance table DDL |
| `pipeline/migrations/XXXX_*.py` | 1,2 | Django model migrations |

### Modified Files (8)
| File | Phase | Changes |
|------|-------|---------|
| `pipeline/models.py` | 1,2,3 | Job fields, JobEvent model, OrgProvenance |
| `pipeline/management/commands/run_orchestrator.py` | 1,2,4 | Registry-based dispatch, events, fd 3 reader |
| `pipeline/orchestrator.py` | 1 | Remove COMMAND_MAP, delegate to stages.py |
| `pipeline/views.py` | 1,3 | Provenance page, registry-driven forms |
| `pipeline/templates/pipeline/job_list.html` | 1 | Blocked reason, scheduled status |
| `pipeline/templates/pipeline/job_detail.html` | 2 | Event timeline replaces log_tail |
| `reports/crawler.py` | 4 | Protocol v1 migration |
| `nonprofits/tools/pipeline_resolve.py` | 4 | Protocol v1 migration |

---

## Deployment Notes

### Phase 1 Deployment
1. Run Django migration for new Job fields
2. Deploy code
3. Restart orchestrator (will acquire advisory lock)
4. Verify: create a job, observe `pending → scheduled → running`

### Phase 2 Deployment
1. Run Django migration for JobEvent model
2. Deploy code
3. Restart orchestrator
4. Historical jobs won't have events (they retain `log_tail`)
5. New jobs will have event timelines on dashboard

### Phase 3 Deployment
1. Run RDS migration for `org_provenance` table
2. Deploy code
3. Run `python manage.py backfill_provenance`
4. Verify: spot-check 10 orgs across different pipeline stages
5. Create `/var/lib/lava/provenance/` directory on each worker

### Phase 4 Deployment
- Per-stage: update code, update registry entry (`protocol_version=0→1`), deploy, restart orchestrator
- Each stage can be migrated independently
- Verify: run a job for the migrated stage, confirm events appear in dashboard

---

## Risk Mitigation

1. **Phase 1 is the riskiest** (touches the orchestrator's core dispatch loop). Mitigate by keeping the existing `COMMAND_MAP` as a fallback for 1 week: if `STAGE_REGISTRY` is missing a stage, fall back to COMMAND_MAP + log a deprecation warning.

2. **Backfill accuracy** — run backfill on a staging DB first, spot-check against known orgs. The backfill is idempotent (INSERT ON CONFLICT DO NOTHING + UPDATE WHERE).

3. **fd 3 pipe exhaustion** — if a stage writes faster than the orchestrator reads, the pipe buffer fills (default 64KB on Linux). The stage's `emit_*` calls will block. Mitigate: orchestrator reads fd 3 in a non-blocking thread, not just on heartbeat ticks.

4. **Advisory lock prevents legitimate restart** — if the orchestrator crashes without releasing the lock, the lock persists until the DB connection is cleaned up (typically immediate on process death, but verify). Add a `--force` flag that issues `pg_advisory_unlock_all()` before acquiring.
