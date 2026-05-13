# Spec 0040: Classifier V3 Job Queue Integration

**Status:** Draft
**Author:** Architect
**Date:** 2026-05-13
**Dependencies:** Spec 0034 (Pipeline Control Plane), Spec 0038 (Classifier Pipeline Rebuild)

---

## Problem Statement

The classifier v3 pipeline (extract-context, reclassify, compare-classify, resolve-disagree, promote-classify) runs via the ad-hoc `PipelineProcess` model — a simple PID tracker with start/stop/check semantics. Every other pipeline stage (seed, resolve, crawl, classify, 990-index, 990-parse, enrich-phone) uses the `Job` queue system built in Spec 0034, which provides:

- **State-isolated jobs**: Multiple states can run simultaneously without conflict
- **Run vs queue**: Jobs can be queued and dispatched by the scheduler, not just started immediately
- **Queue visibility**: Dashboard shows pending, running, completed, and failed jobs with progress, log tails, and structured events
- **Multi-host dispatch**: Jobs target a specific worker host, enabling distribution across machines
- **Dependency chains**: Jobs can depend on other jobs (e.g., resolve depends on seed)
- **Retry with lineage**: Failed jobs can be retried with attempt tracking
- **Conflict detection**: Per-state and global conflict groups prevent double-runs

The classifier v3 pipeline has none of this. Running a national-scale reclassification requires manually starting one process at a time, with no way to queue 50 states for overnight processing, no multi-host placement, and no visibility into what's running or pending.

## Goals

1. **Migrate classifier v3 from PipelineProcess to Job**: All 5 phases become first-class Job phases with full lifecycle tracking
2. **State-isolated concurrent execution**: Run extract-context and reclassify for multiple states simultaneously (e.g., TX on cloud2, CA on cloud1)
3. **Run-vs-queue choice**: Queue jobs for later dispatch or run immediately, matching existing seed/resolve/crawl UX
4. **Job queue visibility**: Pending, running, completed, and failed v3 jobs visible in dashboard job list and on the classifier v3 page
5. **Multi-host placement**: Target v3 jobs at specific workers, same as other pipeline phases
6. **StageDefinition registration**: All 5 phases registered in `STAGE_REGISTRY` so the control plane treats them uniformly

## Non-Goals

- Removing the `PipelineProcess` model entirely (other future uses may exist)
- Changing the classifier v3 management commands themselves (they already work correctly via Spec 0038)
- Adding automated pipeline orchestration (e.g., auto-chain extract-context → reclassify → compare → promote)
- Changing the classifier v3 dashboard page layout beyond what's needed for job queue integration

## Current State

### What exists today

**COMMAND_MAP** (orchestrator.py) already has entries for all 5 v3 phases with full parameter validation. This is the command-building infrastructure — it knows how to construct `argv` arrays for each phase.

**Forms** (forms.py) exist for all 5 phases: `ExtractContextForm`, `ReclassifyForm`, `CompareClassifyForm`, `ResolveDisagreementsForm`, `PromoteClassifyForm`.

**Dashboard views** exist at `/classifier-v3/` with a status partial that polls `PipelineProcess` rows. The `ProcessStartView` and `ProcessStopView` handle start/stop via `start_process()` / `stop_process()`.

**_CONFIG_ALLOWLIST** (views.py) already has entries for v3 phases for job config display.

### What's missing

1. **Job.PHASE_CHOICES** doesn't include v3 phases
2. **STAGE_REGISTRY** (stages.py) has no v3 entries
3. **No `create_*_job()` functions** in orchestrator.py for v3 phases
4. **No job queue URLs** — v3 phases use `/process/<phase>/start/` and `/process/<phase>/stop/` instead of `/classifier-v3/queue/`
5. **Dashboard classifier v3 page** shows PipelineProcess status, not Job queue
6. **`_PER_STATE_PHASES`** set in orchestrator.py doesn't include v3 phases
7. **`get_eligible_jobs()`** doesn't know about v3 conflict groups

## Technical Implementation

### Phase 1: Model & Registry Changes

#### 1a. Add v3 phases to Job.PHASE_CHOICES

```python
# models.py — add to PHASE_CHOICES
("extract-context", "Extract Context"),
("reclassify", "Reclassify"),
("compare-classify", "Compare Classify"),
("resolve-disagree", "Resolve Disagreements"),
("promote-classify", "Promote Classification"),
```

No Django migration needed — `PHASE_CHOICES` is a validation-only constraint, not a DB-level enum.

#### 1b. Register StageDefinitions in STAGE_REGISTRY

```python
# stages.py
"extract-context": StageDefinition(
    name="extract-context",
    display_name="Context Extractor",
    command=["python3", "lavandula/dashboard/manage.py", "extract_classification_context"],
    parameters={
        "state": ParamSpec(type="state_code", cli_flag="--state"),
        "ein": ParamSpec(type="string", cli_flag="--ein", pattern=r"^\d{2}-\d{7}$"),
        "limit": ParamSpec(type="integer", cli_flag="--limit", min_value=1, max_value=999999),
        "reextract": ParamSpec(type="boolean", cli_flag="--reextract"),
        "download_workers": ParamSpec(type="integer", cli_flag="--download-workers", min_value=1, max_value=32),
        "extract_workers": ParamSpec(type="integer", cli_flag="--extract-workers", min_value=1, max_value=32),
    },
    predecessors=["crawl"],
    conflict_group="per-state",
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
    resource_class="heavy",
),
"reclassify": StageDefinition(
    name="reclassify",
    display_name="Reclassifier (V3)",
    command=["python3", "lavandula/dashboard/manage.py", "reclassify_corpus"],
    parameters={
        "run_tag": ParamSpec(required=True, type="string", cli_flag="--run-tag",
                             pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
        "state": ParamSpec(type="state_code", cli_flag="--state"),
        "ein": ParamSpec(type="string", cli_flag="--ein", pattern=r"^\d{2}-\d{7}$"),
        "sample": ParamSpec(type="integer", cli_flag="--sample", min_value=1, max_value=999999),
        "where": ParamSpec(type="string", cli_flag="--where"),
        "dry_run": ParamSpec(type="boolean", cli_flag="--dry-run"),
        "backend": ParamSpec(type="choice", cli_flag="--backend",
                             choices=["deepseek", "claude", "gemini", "codex"]),
        "workers": ParamSpec(type="integer", cli_flag="--workers", min_value=1, max_value=32),
        "resume": ParamSpec(type="boolean", cli_flag="--resume"),
        "definition": ParamSpec(type="string", cli_flag="--definition",
                                pattern=r"^[a-z][a-z0-9_]*$"),
        "allow_fallback": ParamSpec(type="boolean", cli_flag="--allow-fallback"),
        "min_text_len": ParamSpec(type="integer", cli_flag="--min-text-len",
                                  min_value=0, max_value=10000),
        "batch_size": ParamSpec(type="integer", cli_flag="--batch-size",
                                min_value=10, max_value=5000),
        "quiet": ParamSpec(type="boolean", cli_flag="--quiet"),
    },
    predecessors=["extract-context"],
    conflict_group="per-state",
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
    resource_class="heavy",
),
"compare-classify": StageDefinition(
    name="compare-classify",
    display_name="Classification Comparator",
    command=["python3", "lavandula/dashboard/manage.py", "compare_classifications"],
    parameters={
        "run_tag": ParamSpec(required=True, type="string", cli_flag="--run-tag",
                             pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
        "show_reasoning": ParamSpec(type="boolean", cli_flag="--show-reasoning"),
    },
    predecessors=["reclassify"],
    conflict_group="global",
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=1),
    resource_class="light",
),
"resolve-disagree": StageDefinition(
    name="resolve-disagree",
    display_name="Disagreement Resolver",
    command=["python3", "lavandula/dashboard/manage.py", "resolve_disagreements"],
    parameters={
        "run_tag": ParamSpec(required=True, type="string", cli_flag="--run-tag",
                             pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
        "backend": ParamSpec(type="choice", cli_flag="--backend",
                             choices=["deepseek", "claude", "gemini", "codex"]),
        "state": ParamSpec(type="state_code", cli_flag="--state"),
        "sample": ParamSpec(type="integer", cli_flag="--sample", min_value=1, max_value=999999),
        "workers": ParamSpec(type="integer", cli_flag="--workers", min_value=1, max_value=32),
        "dry_run": ParamSpec(type="boolean", cli_flag="--dry-run"),
        "definition": ParamSpec(type="string", cli_flag="--definition",
                                pattern=r"^[a-z][a-z0-9_]*$"),
    },
    predecessors=["compare-classify"],
    conflict_group="global",
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
    resource_class="medium",
),
"promote-classify": StageDefinition(
    name="promote-classify",
    display_name="Classification Promoter",
    command=["python3", "lavandula/dashboard/manage.py", "promote_classification_run"],
    parameters={
        "run_tag": ParamSpec(required=True, type="string", cli_flag="--run-tag",
                             pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
        "confirm": ParamSpec(type="boolean", cli_flag="--confirm"),
    },
    predecessors=["resolve-disagree"],
    conflict_group="global",
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=1),
    resource_class="light",
),
```

**Conflict group rationale:**
- `extract-context` and `reclassify`: `per-state` — these are the long-running, state-scoped operations. Multiple states can run simultaneously; same state cannot.
- `compare-classify`, `resolve-disagree`, `promote-classify`: `global` — these are **globally exclusive** (not merely run_tag-scoped). Reason: `compare_classifications` reads all `classification_results` for a given `run_id`, `promote_classification_run` writes directly to the `corpus` table's `material_type`/`material_group`/`classification` columns. Concurrent promotes or a promote during a compare would produce inconsistent reads or lost writes. Global singleton is the correct model even if they technically operate on a single run_tag — the underlying tables are shared. Only one of these three phases may be active (pending, scheduled, or running) at a time.

### Phase 2: Orchestrator Job Creation

#### 2a. Add v3 phases to `_PER_STATE_PHASES`

```python
_PER_STATE_PHASES = {"resolve", "classify", "enrich-phone", "seed", "crawl",
                     "extract-context", "reclassify"}
```

#### 2b. Classifier v3 conflict group for global phases

Add a new conflict group set for the run-tag-scoped phases:

```python
_CLASSIFY_V3_GLOBAL_PHASES = {"compare-classify", "resolve-disagree", "promote-classify"}
```

These need their own conflict check: only one of these global phases can run at a time (they share the same classification_runs/classification_results tables).

#### 2c. Add `create_v3_job()` factory function

A single generic function handles all 5 v3 phases, since the pattern is identical:

```python
def create_v3_job(phase: str, config_overrides: dict, host: str,
                  depends_on: Job | None = None) -> Job:
    """Create a classifier v3 job.

    Per-state phases (extract-context, reclassify) use state isolation.
    Global phases (compare-classify, resolve-disagree, promote-classify) are singleton.
    """
    _maybe_validate_host(host)
    V3_PHASES = {"extract-context", "reclassify", "compare-classify",
                 "resolve-disagree", "promote-classify"}
    if phase not in V3_PHASES:
        raise InvalidParameterError(f"Not a v3 phase: {phase}")

    state = config_overrides.get("state") or None

    with transaction.atomic():
        if phase in _PER_STATE_PHASES:
            # Per-state conflict: block same phase + same state
            qs = Job.objects.select_for_update().filter(
                phase=phase, status__in=["pending", "scheduled", "running"],
            )
            if state:
                qs = qs.filter(state_code=state)
            else:
                qs = qs.filter(state_code__isnull=True)
        else:
            # Global conflict: block any active job in the global v3 group
            qs = Job.objects.select_for_update().filter(
                phase__in=_CLASSIFY_V3_GLOBAL_PHASES,
                status__in=["pending", "scheduled", "running"],
            )

        existing = qs.first()
        if existing:
            label = existing.state_code or "global"
            raise DuplicateJobError(
                f"Active {existing.phase} job already exists for {label}: "
                f"Job #{existing.pk}"
            )

        try:
            return Job.objects.create(
                state_code=state,
                phase=phase,
                status="pending",
                host=host,
                config_json=config_overrides,
                depends_on=depends_on,
            )
        except IntegrityError:
            raise DuplicateJobError(f"Duplicate {phase} job (constraint violation)")
```

#### 2d. Update `get_eligible_jobs()` conflict logic

The existing `get_eligible_jobs()` already handles `_PER_STATE_PHASES` and globally-blocked phases. Adding v3 per-state phases to `_PER_STATE_PHASES` covers extract-context and reclassify. The global v3 phases (compare-classify, resolve-disagree, promote-classify) are already handled by the generic "not in `_PER_STATE_PHASES` → global block" logic.

One refinement: add `_CLASSIFY_V3_GLOBAL_PHASES` mutual exclusion to `check_phase_conflict()`:

```python
def check_phase_conflict(phase: str, state_code: str | None = None) -> bool:
    # existing per-state logic...
    # Add: if phase is in _CLASSIFY_V3_GLOBAL_PHASES, check all 3
    if phase in _CLASSIFY_V3_GLOBAL_PHASES:
        if Job.objects.filter(phase__in=_CLASSIFY_V3_GLOBAL_PHASES,
                              status="running").exists():
            return True
    # ...
```

### Phase 3: Dashboard Integration

#### 3a. Add job queue URL for v3 phases

```python
# urls.py
path("classifier-v3/queue/", views.ClassifierV3JobCreateView.as_view(),
     name="classifier_v3_job_create"),
```

#### 3b. New `ClassifierV3JobCreateView`

Handles POST from the classifier v3 dashboard forms. Instead of calling `start_process()`, creates a Job via `create_v3_job()`. The view:

1. **Validates `phase`** against a server-side allowlist (`_V3_PHASES`). Rejects any phase not in the set — prevents a crafted POST from targeting an unintended stage.
2. Validates the form (reusing existing form classes, looked up by phase from a strict mapping)
3. Extracts `host` from POST data (worker selector, defaulting to current hostname). Host is validated by `_maybe_validate_host()` inside `create_v3_job()`.
4. Extracts optional `depends_on` from POST data. Validated: must reference an existing pending/scheduled/running job, or is ignored.
5. Calls `create_v3_job(phase, config, host, depends_on)`
6. Redirects to classifier_v3 with success/error message

**Error messages returned to the user:**
- No active workers registered: "No workers available. Register a worker first."
- Selected host offline/stale: "Host {host} is {status}"
- Duplicate job conflict: "Active {phase} job already exists for {state}: Job #{id}"
- Invalid form data: Django form validation errors displayed
- `depends_on` references a terminal/nonexistent job: silently ignored (treated as no dependency)

#### 3c. Update ClassifierV3View context

Replace `PipelineProcess` status polling with Job query:

```python
# Instead of check_process(phase), query recent jobs per phase
for phase in _V3_PHASES:
    # Per-state phases can have MULTIPLE running jobs (one per state)
    running = list(Job.objects.filter(phase=phase, status="running")
                   .order_by("state_code"))
    pending_count = Job.objects.filter(phase=phase, status="pending").count()
    recent = Job.objects.filter(phase=phase).order_by("-created_at")[:5]
    ctx[phase.replace("-", "_") + "_running"] = running
    ctx[phase.replace("-", "_") + "_pending_count"] = pending_count
    ctx[phase.replace("-", "_") + "_recent"] = recent
```

**Multi-running display**: Per-state phases (extract-context, reclassify) can have multiple running jobs simultaneously — one per state. The dashboard must show **all** running jobs for these phases, not just `.first()`. Display as a list grouped by state code, each with its own progress bar, elapsed time, and host label. Global phases (compare, resolve-disagree, promote) show at most one running job.

#### 3d. Update ClassifierV3StatusPartial

Same migration: show Job-based status instead of PipelineProcess. For each v3 step, show:
- All currently running jobs (multiple for per-state phases) with progress, elapsed time, host, and state code
- Pending job count
- Most recent completed/failed job with exit code and duration

#### 3e. Update classifier_v3.html template

Each phase card gets:
- "Queue Job" button (submits to `/classifier-v3/queue/`) in addition to or replacing "Start" button
- Host selector dropdown (when multiple workers exist)
- Optional depends_on selector
- Running/pending/recent job indicators from Job model

#### 3f. Remove PipelineProcess dependency for v3 phases

The `ProcessStartView` and `ProcessStopView` should no longer handle v3 phases. Remove v3 entries from the `form_map` and `redirect_map` dicts in both views. The process start/stop URLs stay for any remaining ad-hoc phases, but v3 routes through the job queue exclusively.

### Phase 4: Job Scheduler Integration

The existing job scheduler (`scheduler.py` `score_placement()` and `get_eligible_jobs()`) already handles new phases automatically as long as:

1. The phase is in `COMMAND_MAP` (already true)
2. The phase has conflict group logic in `check_phase_conflict()` (Phase 2d)
3. The phase is in `_PER_STATE_PHASES` if per-state (Phase 2a)

The scheduler daemon picks up pending jobs and transitions them to `scheduled` → `running`. No scheduler changes needed beyond the conflict group updates.

### Phase 5: Cleanup & Backward Compatibility

#### 5a. Cancellation behavior

Job cancellation for v3 phases uses the existing `cancel_job()` function (orchestrator.py), which:
1. Sends SIGTERM to the process group
2. Waits up to 10 seconds for graceful shutdown
3. Sends SIGKILL if still alive
4. Marks job as `cancelled`, sets `finished_at`
5. Cascades cancellation to any dependent jobs

The v3 management commands already handle SIGINT/SIGTERM gracefully — `reclassify_corpus` has a `_SIGINT_DRAIN_TIMEOUT` that completes in-flight LLM calls before exiting. Partial outputs (classification_results rows) are safe: they're idempotent writes keyed by (run_id, content_sha256). A cancelled-and-restarted run with `--resume` picks up where it left off.

#### 5b. PipelineProcess cleanup

**Before migration**: Verify PID liveness for any `running` PipelineProcess rows before marking stopped. Use the existing `_is_pid_alive_and_matches()` from `process_manager.py`. Only mark stopped if the PID is dead or doesn't match the expected command. If a real process is still running, either wait for it to finish or stop it explicitly via `stop_process()` first.

```python
# Migration script (not blind SQL UPDATE):
for proc in PipelineProcess.objects.filter(
    name__in=_V3_PHASES, status="running"
):
    if not proc.pid or not _is_pid_alive_and_matches(proc.pid, proc.name):
        proc.status = "stopped"
        proc.save(update_fields=["status"])
    else:
        log.warning("PipelineProcess %s PID %s still alive — stop manually first",
                     proc.name, proc.pid)
```

#### 5c. Log history

After migration, the v3 page has two log sources:
- **Historical**: `_scan_v3_logs()` shows log files from PipelineProcess-era runs (pre-migration). These remain accessible via the existing `ProcessLogPartial` view. Displayed in a "Historical Logs" section at the bottom of the page.
- **Current**: `Job.log_file` shows logs from Job-era runs. Displayed via the standard `JobLogPartial` view (already used by other pipeline pages).

The `PipelineProcess` model is **not removed** but is no longer used by v3 views for status polling. It remains available for any future ad-hoc use cases. After this spec, no dashboard page creates v3 PipelineProcess rows.

## Acceptance Criteria

### Model & Registry (Phase 1)
1. All 5 v3 phases appear in `Job.PHASE_CHOICES`
2. All 5 v3 phases have `StageDefinition` entries in `STAGE_REGISTRY`
3. `validate_registry()` passes with the new entries
4. Each StageDefinition has correct `conflict_group`, `resource_class`, `retry_policy`, and `predecessors`

### Orchestrator (Phase 2)
5. `extract-context` and `reclassify` are in `_PER_STATE_PHASES`
6. `create_v3_job()` creates pending jobs for all 5 phases
7. `create_v3_job()` rejects duplicate per-state jobs (same phase + same state active)
8. `create_v3_job()` rejects when any `_CLASSIFY_V3_GLOBAL_PHASES` job is active (for global phases)
9. `create_v3_job()` validates host against active workers
10. `check_phase_conflict()` returns True when a global v3 phase conflicts with another global v3 phase
11. `get_eligible_jobs()` correctly handles v3 per-state and global conflict groups
12. `build_argv()` produces correct command lines for all 5 v3 phases (already works via existing COMMAND_MAP)

### Dashboard (Phase 3)
13. Classifier v3 page shows job queue status (running/pending/recent) per phase instead of PipelineProcess status
14. Each phase card has a "Queue Job" form with host selector
15. Host selector shows all active workers when multiple exist
16. Queued v3 jobs appear in the global job list (`/jobs/`)
17. V3 jobs support cancel and retry via existing job infrastructure
18. Running v3 jobs show progress, elapsed time, and log tail
19. `ProcessStartView` and `ProcessStopView` no longer accept v3 phase names

### Integration (Phase 4)
20. State-isolated jobs: Can queue extract-context for TX and CA simultaneously — both dispatch and run concurrently
21. Global conflict: Cannot queue compare-classify while resolve-disagree is running
22. Multi-host: Can target a reclassify job at cloud1 while extract-context runs on cloud2
23. Dependency chain: Can queue reclassify with depends_on pointing to an extract-context job — reclassify waits until extract-context completes

### Backward Compatibility (Phase 5)
24. Existing PipelineProcess rows remain in DB but are no longer polled by v3 views
25. Historical v3 log files remain accessible via log viewer
26. Job creation uses the same form fields and validation as the current PipelineProcess forms

## Traps to Avoid

1. **Don't change the management commands**: The underlying `reclassify_corpus`, `extract_classification_context`, etc. commands are already correct (Spec 0038). This spec only changes how they're dispatched.
2. **Don't add new conflict groups to `validate_registry()`**: The existing `per-state` and `global` values are sufficient. The `_CLASSIFY_V3_GLOBAL_PHASES` mutual exclusion is handled in the orchestrator, not the registry validator.
3. **Don't forget the `--run-tag` coupling**: When queuing reclassify → compare → resolve-disagree → promote as a dependency chain, all jobs must share the same `run_tag` config value. The UI should make this clear (perhaps pre-fill from the upstream job's config).
4. **Don't break the scheduler daemon**: The scheduler already calls `get_eligible_jobs()` and `build_argv()`. New phases just work if conflict groups are correct.
5. **PipelineProcess ghost processes**: Before switching the v3 page from PipelineProcess to Job queries, clean up stale PipelineProcess rows using the PID-liveness-checked script (Phase 5b). A running PipelineProcess would still be checked by `check_phase_conflict()` — clear them.
6. **Include `scheduled` in conflict checks**: The Job lifecycle has three active states: `pending`, `scheduled`, `running`. All conflict/duplicate checks must filter on all three. The existing `create_crawl_job()` etc. only check `pending`/`running` — this is a latent bug in the existing code, but v3 must get it right. Use `status__in=["pending", "scheduled", "running"]`.
7. **Don't remove PipelineProcess from `check_phase_conflict()`**: Even after migration, `check_phase_conflict()` still checks PipelineProcess rows. Leave this check in place — it's a safety net if a stale row somehow exists.

## Testing Requirements

- **Unit tests**: `create_v3_job()` conflict detection for per-state and global phases, parameter validation, host validation
- **Unit tests**: `create_v3_job()` rejects when `scheduled` job exists (not just `pending`/`running`)
- **Unit tests**: `create_v3_job()` concurrent enqueue race (two threads creating same phase+state simultaneously — one succeeds, one raises `DuplicateJobError`)
- **Unit tests**: `STAGE_REGISTRY` validation passes, all entries have correct types
- **Unit tests**: `get_eligible_jobs()` returns v3 jobs when eligible, excludes when conflicted
- **Unit tests**: `get_eligible_jobs()` excludes compare-classify when resolve-disagree is running (cross-phase global exclusion)
- **Unit tests**: `ClassifierV3JobCreateView` rejects POST with phase not in `_V3_PHASES`
- **Unit tests**: `ClassifierV3JobCreateView` rejects POST with invalid `depends_on` (nonexistent, terminal)
- **Integration tests**: Full form POST → job creation → job appears in list
- **Integration tests**: Cancel running v3 job → SIGTERM sent → job marked cancelled → dependent jobs cascaded
- **Integration tests**: Retry failed v3 job → new job created with correct `retry_of` and `attempt_number`
- **Migration tests**: PipelineProcess cleanup script only marks stopped when PID is dead (not blindly)
- **Manual verification**: Queue TX and CA extract-context jobs simultaneously, verify both dispatch on separate hosts

## Migration Path

1. **Pre-migration check**: Verify no v3 PipelineProcess rows are actively running. If any are alive, wait or stop them manually via `stop_process()`.
2. Deploy code changes
3. Run PipelineProcess cleanup (Phase 5b script — PID-liveness-checked, not blind SQL)
4. Run Django migration (if any — PHASE_CHOICES doesn't require one, but review for any template/static changes)
5. Restart gunicorn
6. Verify v3 job creation via dashboard: queue one extract-context job, confirm it appears in `/jobs/` list
7. Verify ProcessStartView rejects v3 phase names (POST to `/process/reclassify/start/` returns error)
