# Plan 0040: Classifier V3 Job Queue Integration

**Spec:** `locard/specs/0040-classifier-v3-job-queue.md`
**Date:** 2026-05-13
**Builder Protocol:** SPIDER (Implement → Defend → Evaluate → Review)

---

## Overview

Wire the 5-phase classifier v3 pipeline into the Job queue system. All changes are additive — new registry entries, a new factory function, view updates. No schema migrations needed. One prerequisite code change: add `--state` to `promote_classification_run.py`.

## File Inventory

Files to modify (8):

| # | File | Change |
|---|------|--------|
| 1 | `lavandula/dashboard/pipeline/management/commands/promote_classification_run.py` | Add `--state` argument + JOIN filter |
| 2 | `lavandula/dashboard/pipeline/models.py` | Add 5 entries to `Job.PHASE_CHOICES` |
| 3 | `lavandula/dashboard/pipeline/stages.py` | Add 5 `StageDefinition` entries to `STAGE_REGISTRY` |
| 4 | `lavandula/dashboard/pipeline/orchestrator.py` | Add v3 phases to `_PER_STATE_PHASES`, add `create_v3_job()`, update `COMMAND_MAP` entries, remove `--where` from reclassify |
| 5 | `lavandula/dashboard/pipeline/views.py` | New `ClassifierV3JobCreateView`, update `ClassifierV3View` + `ClassifierV3StatusPartial` to use Job queries, import `create_v3_job`, remove v3 from `ProcessStartView`/`ProcessStopView` |
| 6 | `lavandula/dashboard/pipeline/urls.py` | Add `classifier-v3/queue/` URL |
| 7 | `lavandula/dashboard/pipeline/templates/pipeline/classifier_v3.html` | Replace PipelineProcess forms with job queue forms, add host selector, show running/pending/recent jobs |
| 8 | `lavandula/dashboard/pipeline/templates/pipeline/partials/classifier_v3_status.html` | Replace `check_process()` status with Job-based status per phase |

Files to modify for forms (1):

| # | File | Change |
|---|------|--------|
| 9 | `lavandula/dashboard/pipeline/forms.py` | Add `state` field to `CompareClassifyForm` and `PromoteClassifyForm`, remove `where` from `ReclassifyForm` if present |

Files to create (1):

| # | File | Purpose |
|---|------|---------|
| 10 | `lavandula/dashboard/pipeline/management/commands/cleanup_v3_pipeline_processes.py` | One-time PipelineProcess cleanup command |

Test files to create (1):

| # | File | Purpose |
|---|------|---------|
| 10 | `lavandula/dashboard/pipeline/tests/test_v3_job_queue.py` | Unit + integration tests for all v3 job queue functionality |

No Django migrations required — `PHASE_CHOICES` is Python-level validation only.

---

## Implementation Steps

### Step 1: Add `--state` to `promote_classification_run.py`

**File:** `lavandula/dashboard/pipeline/management/commands/promote_classification_run.py`

**Why first:** This is a prerequisite — the StageDefinition for `promote-classify` declares a `state` parameter that must correspond to a real CLI flag.

**Changes:**

1. Add `--state` argument to `add_arguments()` (line 29-32):
```python
parser.add_argument("--state", help="Limit promotion to a single state (2-letter code)")
```

2. In `handle()`, after getting `run_id`, add state filter to both UPDATE queries using a JOIN, matching the pattern from `compare_classifications.py`. The join path is: `corpus.source_org_ein` → `nonprofits_seed.ein`:

```sql
-- v3 columns update (add JOIN on nonprofits_seed):
UPDATE lava_corpus.corpus c SET
    v3_material_type = cr.material_type, ...
FROM lava_corpus.classification_results cr
JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
WHERE cr.run_id = :run_id
  AND cr.content_sha256 = c.content_sha256
  AND (:state::text IS NULL OR ns.state = :state)
```

When `--state` is NULL, the JOIN still executes but the WHERE clause passes all rows — behavior is unchanged. The JOIN is preferred over a subquery to stay consistent with the proven pattern in `compare_classifications.py`.

Apply the same JOIN pattern to both UPDATE statements (v3 columns and canonical columns).

3. Pass `state` parameter to both `conn.execute()` calls:
```python
state = options.get("state")
# ... in both execute calls:
{"run_id": run_id, "run_tag": run_tag, "state": state}
```

4. Update result count query to also filter by state (for accurate reporting):
```python
count_sql = """
    SELECT COUNT(*) FROM lava_corpus.classification_results cr
    JOIN lava_corpus.corpus c ON cr.content_sha256 = c.content_sha256
    JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
    WHERE cr.run_id = :rid AND (:state::text IS NULL OR ns.state = :state)
"""
```

5. Update the log output to include state when provided.

**AC:** `promote_classification_run --run-tag X --state TX --confirm` promotes only TX orgs' results. Without `--state`, behavior is unchanged.

---

### Step 2: Model & Registry Changes

#### 2a. Add v3 phases to `Job.PHASE_CHOICES`

**File:** `lavandula/dashboard/pipeline/models.py` (line 154-163)

Add 5 entries after the existing `("enrich-phone", "Phone Enrich")`:

```python
("extract-context", "Extract Context"),
("reclassify", "Reclassify"),
("compare-classify", "Compare Classify"),
("resolve-disagree", "Resolve Disagreements"),
("promote-classify", "Promote Classification"),
```

No Django migration needed.

#### 2b. Register StageDefinitions in `STAGE_REGISTRY`

**File:** `lavandula/dashboard/pipeline/stages.py` (after line 178, before closing `}`)

Add all 5 entries exactly as written in the spec (Section 1b). Copy from spec verbatim — the dataclass fields, parameter specs, conflict groups, and resource classes have all been reviewed and approved.

Key details to double-check during implementation:
- All use `conflict_group="per-state"`
- `reclassify` parameters MUST NOT include `where`
- `compare-classify` and `promote-classify` include `state` ParamSpec
- `compare-classify` resource_class is `"light"` (read-only, no LLM)
- `promote-classify` resource_class is `"light"` (single SQL UPDATE)

#### 2c. Validate registry passes

After adding entries, run `validate_registry()` in a Django shell or add to test suite. Expected: no errors.

---

### Step 3: Orchestrator Changes

**File:** `lavandula/dashboard/pipeline/orchestrator.py`

#### 3a. Add v3 phases to `_PER_STATE_PHASES` (line 252)

```python
_PER_STATE_PHASES = {"resolve", "classify", "enrich-phone", "seed", "crawl",
                     "extract-context", "reclassify", "compare-classify",
                     "resolve-disagree", "promote-classify"}
```

#### 3b. Update `COMMAND_MAP` entries

Two changes:

1. **Remove `where` from reclassify entry** (line 128):
   Delete the line `"where": {"type": "text", "flag": "--where"},`

2. **Add `state` to `compare-classify` entry** (line 152-158):
   Add `"state": {"type": "text", "pattern": r"^[A-Z]{2}$", "flag": "--state"},` to the `params` dict

3. **Add `state` to `promote-classify` entry** (line 159-165):
   Add `"state": {"type": "text", "pattern": r"^[A-Z]{2}$", "flag": "--state"},` to the `params` dict

#### 3c. Add `create_v3_job()` factory function

Add after `create_phone_enrich_job()` (after line 532). Implementation from the spec (Section 2b), including:
- Phase allowlist validation
- `run_tag` mismatch validation for `depends_on`
- Per-state conflict detection with `select_for_update()`
- `status__in=["pending", "scheduled", "running"]` (includes `scheduled` — Trap #6)
- `IntegrityError` fallback

Copy the function from the spec, with one addition: **predecessor phase and state compatibility validation** for `depends_on`.

Add after the `run_tag` mismatch check:

```python
# Validate predecessor phase is allowed
if depends_on:
    _V3_PREDECESSORS = {
        "extract-context": set(),  # no v3 predecessor (depends on crawl, which is not v3)
        "reclassify": {"extract-context"},
        "compare-classify": {"reclassify"},
        "resolve-disagree": {"compare-classify"},
        "promote-classify": {"resolve-disagree"},
    }
    allowed = _V3_PREDECESSORS.get(phase, set())
    if depends_on.phase not in allowed:
        raise InvalidParameterError(
            f"{phase} cannot depend on {depends_on.phase} "
            f"(allowed predecessors: {allowed or 'none'})"
        )
    # State compatibility: downstream state must match upstream state,
    # OR upstream is nationwide (None) and downstream narrows to a state.
    # Reject: downstream nationwide depending on upstream per-state.
    upstream_state = depends_on.state_code
    if upstream_state and not state:
        raise InvalidParameterError(
            f"Nationwide {phase} cannot depend on state-scoped "
            f"{depends_on.phase} (state={upstream_state})"
        )
    if upstream_state and state and upstream_state != state:
        raise InvalidParameterError(
            f"State mismatch: {phase} targets {state} but depends on "
            f"{depends_on.phase} which targets {upstream_state}"
        )
```

**State compatibility rules:**
- Same state → OK (TX depends on TX)
- Downstream per-state depends on upstream nationwide → OK (TX reclassify depends on nationwide extract-context)
- Downstream nationwide depends on upstream per-state → REJECTED (nationwide compare depends on TX reclassify)
- Downstream state differs from upstream state → REJECTED (CA reclassify depends on TX extract-context)

---

### Step 4: Form Updates

**File:** `lavandula/dashboard/pipeline/forms.py`

#### 4a. Add `state` to `CompareClassifyForm` (line 338)

Currently has only `run_tag` and `show_reasoning`. Add:
```python
state = forms.ChoiceField(required=False, choices=[("", "All states")] + STATE_CHOICES)
```
(Use the same `STATE_CHOICES` pattern used by other forms in this file.)

#### 4b. Add `state` to `PromoteClassifyForm` (line 345)

Currently has only `run_tag` and `confirm`. Add:
```python
state = forms.ChoiceField(required=False, choices=[("", "All states")] + STATE_CHOICES)
```

#### 4c. Remove `where` from `ReclassifyForm` (if present)

Check if the form has a `where` field. If so, remove it. The `--where` parameter is CLI-only per the security section.

---

### Step 5: Dashboard View Changes

**File:** `lavandula/dashboard/pipeline/views.py`

#### 5a. Add `create_v3_job` import (line 39-51)

Add to the import block:
```python
from .orchestrator import create_v3_job
```

#### 5b. New `ClassifierV3JobCreateView`

Add after `ClassifierV3StatusPartial` (after line ~821). This is a `LoginRequiredMixin, View` that handles POST:

```python
class ClassifierV3JobCreateView(LoginRequiredMixin, View):
    _FORM_MAP = {
        "extract-context": "ExtractContextForm",
        "reclassify": "ReclassifyForm",
        "compare-classify": "CompareClassifyForm",
        "resolve-disagree": "ResolveDisagreementsForm",
        "promote-classify": "PromoteClassifyForm",
    }

    def post(self, request):
        phase = request.POST.get("phase", "")
        if phase not in _V3_PHASES:
            messages.error(request, f"Invalid phase: {phase}")
            return redirect("classifier_v3")

        from . import forms
        form_cls = getattr(forms, self._FORM_MAP[phase])
        form = form_cls(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("classifier_v3")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        host = request.POST.get("host") or _get_hostname()

        # Resolve depends_on with strict validation
        depends_on = None
        depends_on_raw = request.POST.get("depends_on", "").strip()
        if depends_on_raw:
            try:
                dep_id = int(depends_on_raw)
                dep_job = Job.objects.get(pk=dep_id)
            except (ValueError, Job.DoesNotExist):
                messages.error(request, f"Dependency job #{depends_on_raw} not found")
                return redirect("classifier_v3")
            if dep_job.status in Job.TERMINAL_STATUSES:
                messages.error(request,
                    f"Dependency job #{dep_id} is {dep_job.status} — cannot depend on a terminal job")
                return redirect("classifier_v3")
            depends_on = dep_job

        try:
            job = create_v3_job(phase, config, host, depends_on=depends_on)
            _log_audit(request, "queue_v3", phase, config)
            state_label = config.get("state") or "nationwide"
            messages.success(request, f"Queued {phase} job #{job.pk} for {state_label}")
        except DuplicateJobError as e:
            messages.error(request, str(e))
        except InvalidParameterError as e:
            messages.error(request, str(e))

        return redirect("classifier_v3")
```

#### 5c. Update `ClassifierV3View.get_context_data()` (line 719-742)

Replace the `check_process(phase)` loop with Job queries:

```python
for phase in _V3_PHASES:
    key = phase.replace("-", "_")
    running = list(Job.objects.filter(phase=phase, status="running").order_by("state_code"))
    _annotate_running_jobs(running)
    _annotate_host_display(running)
    pending_count = Job.objects.filter(phase=phase, status="pending").count()
    recent = _annotate_recent_jobs(
        Job.objects.filter(phase=phase).order_by("-created_at")[:5]
    )
    _annotate_host_display(recent)
    ctx[f"{key}_running"] = running
    ctx[f"{key}_pending_count"] = pending_count
    ctx[f"{key}_recent"] = recent
```

Keep existing form instantiation. Add:
```python
ctx["worker_choices"] = _get_worker_choices()
ctx["dependency_choices"] = _get_dependency_choices()
```

Keep `ctx["recent_logs"] = _scan_v3_logs()` for historical log display.

**Query count note:** This issues ~15 queries (3 per phase × 5 phases). Acceptable for 5 phases on a single-operator dashboard. The status partial is polled every 5s via HTMX — if performance becomes an issue, consolidate to 3 aggregate queries (all running, all pending counts, all recent) with Python-side grouping. Not worth the complexity now.

#### 5d. Update `ClassifierV3StatusPartial.get_context_data()` (line 763-821)

Replace the `check_process(phase)` loop (lines 770-801) with Job-based status:

```python
for phase, label in [...]:
    running = list(Job.objects.filter(phase=phase, status="running").order_by("state_code"))
    _annotate_running_jobs(running)
    _annotate_host_display(running)
    pending_count = Job.objects.filter(phase=phase, status="pending").count()
    last_completed = Job.objects.filter(
        phase=phase, status__in=["completed", "failed"]
    ).order_by("-finished_at").first()
    steps.append({
        "phase": phase, "label": label,
        "running": running,
        "pending_count": pending_count,
        "last_completed": last_completed,
        "last_duration": _job_duration(last_completed) if last_completed else None,
    })
```

Keep the `classification_runs` query (lines 803-820) — it's independent metadata.

#### 5e. Remove v3 phases from `ProcessStartView` (line 824-865)

**Rejection behavior:** After removing v3 entries from `form_map`, a POST to `/process/reclassify/start/` hits the existing `if phase not in form_map` guard (line 846), which returns `messages.error(request, f"Unknown phase: {phase}")` and redirects to `dashboard`. This is the standard error path already coded in the view — no new code needed.

Remove from `form_map` (line 826-835):
```
"extract-context": "ExtractContextForm",
"reclassify": "ReclassifyForm",
"compare-classify": "CompareClassifyForm",
"resolve-disagree": "ResolveDisagreementsForm",
"promote-classify": "PromoteClassifyForm",
```

Remove from `redirect_map` (line 836-845):
```
"extract-context": "classifier_v3",
"reclassify": "classifier_v3",
"compare-classify": "classifier_v3",
"resolve-disagree": "classifier_v3",
"promote-classify": "classifier_v3",
```

#### 5f. Remove v3 phases from `ProcessStopView` (line 868-887)

Remove from `redirect_map` (line 870-879):
```
"extract-context": "classifier_v3",
"reclassify": "classifier_v3",
"compare-classify": "classifier_v3",
"resolve-disagree": "classifier_v3",
"promote-classify": "classifier_v3",
```

#### 5g. Update `_CONFIG_ALLOWLIST` (line 108-121)

Add `state` to `compare-classify` and `promote-classify` entries:
```python
"compare-classify": ["run_tag", "state"],
"promote-classify": ["run_tag", "state", "confirm"],
```

---

### Step 6: URL Configuration

**File:** `lavandula/dashboard/pipeline/urls.py`

Add after line 32 (after the classifier-v3 status URL):
```python
path("classifier-v3/queue/", views.ClassifierV3JobCreateView.as_view(), name="classifier_v3_job_create"),
```

---

### Step 7: Template Updates

#### 7a. Update `classifier_v3.html`

**File:** `lavandula/dashboard/pipeline/templates/pipeline/classifier_v3.html`

For each phase card:
- Replace the `start_process` form action with POST to `{% url 'classifier_v3_job_create' %}`
- Add hidden `<input name="phase" value="extract-context">` (etc.)
- Add host selector dropdown using `worker_choices` context variable
- Add optional `depends_on` selector — when a dependency is selected, JavaScript pre-fills `run_tag` from the selected job's config (read from a `data-config` attribute on the option element) to enforce consistency. Server-side `create_v3_job()` validates the match regardless.
- Change button text from "Start" to "Queue Job"
- Show running jobs list (with state code, elapsed, host, progress) instead of PipelineProcess status
- Show pending count badge
- Show recent jobs list with status/duration

Keep historical logs section at the bottom.

#### 7b. Update `classifier_v3_status.html`

**File:** `lavandula/dashboard/pipeline/templates/pipeline/partials/classifier_v3_status.html`

Update the status partial to render the new `steps` format:
- Each step shows running job list (multiple possible), pending count, and last completed job
- Replace `status == "running"` checks with `running|length > 0`
- Show state code per running job

---

### Step 8: PipelineProcess Cleanup Management Command

**File:** `lavandula/dashboard/pipeline/management/commands/cleanup_v3_pipeline_processes.py`

Ship as a Django management command (not a PR-description snippet) so it's repeatable and testable:

```python
class Command(BaseCommand):
    help = "Clean up stale PipelineProcess rows for v3 phases migrated to Job queue"

    def handle(self, *args, **options):
        from lavandula.dashboard.pipeline.process_manager import _is_pid_alive_and_matches
        _V3_PHASES = ("extract-context", "reclassify", "compare-classify",
                      "resolve-disagree", "promote-classify")
        for proc in PipelineProcess.objects.filter(name__in=_V3_PHASES, status="running"):
            if not proc.pid or not _is_pid_alive_and_matches(proc.pid, proc.name):
                proc.status = "stopped"
                proc.save(update_fields=["status"])
                self.stdout.write(f"Marked {proc.name} as stopped (PID {proc.pid} dead)")
            else:
                self.stderr.write(
                    f"WARNING: {proc.name} PID {proc.pid} still alive — stop manually"
                )
```

**Deployment:** Run `python3 lavandula/dashboard/manage.py cleanup_v3_pipeline_processes` after deploying the code changes and restarting gunicorn. Safe to run multiple times (idempotent).

---

### Step 9: Tests

**File:** `lavandula/dashboard/pipeline/tests/test_v3_job_queue.py`

#### Unit tests:

1. **`test_stage_registry_v3_entries`** — All 5 v3 phases in `STAGE_REGISTRY`, `validate_registry()` returns empty
2. **`test_phase_choices_v3`** — All 5 v3 phases in `Job.PHASE_CHOICES`
3. **`test_create_v3_job_all_phases`** — `create_v3_job()` creates pending jobs for each of the 5 phases
4. **`test_create_v3_job_per_state_conflict`** — Rejects duplicate same-phase + same-state (pending, scheduled, running)
5. **`test_create_v3_job_scheduled_conflict`** — Rejects when a `scheduled` job exists for the same phase+state
6. **`test_create_v3_job_different_states_ok`** — TX and CA for the same phase can coexist
7. **`test_create_v3_job_nationwide_and_per_state_ok`** — Nationwide (no state) and TX for the same phase coexist
8. **`test_create_v3_job_run_tag_mismatch`** — Rejects when `depends_on` has different `run_tag`
9. **`test_create_v3_job_run_tag_match_ok`** — Succeeds when `depends_on` has matching `run_tag`
10. **`test_create_v3_job_host_validation`** — Rejects invalid/offline host when active workers exist
11. **`test_create_v3_job_invalid_phase`** — Rejects phase not in V3_PHASES
12. **`test_get_eligible_jobs_v3`** — v3 jobs appear when eligible, excluded when same-phase+state is running
13. **`test_per_state_phases_includes_v3`** — All 5 v3 phases in `_PER_STATE_PHASES`
14. **`test_command_map_reclassify_no_where`** — `COMMAND_MAP["reclassify"]["params"]` does not contain `where`
15. **`test_command_map_compare_has_state`** — `COMMAND_MAP["compare-classify"]["params"]` contains `state`
16. **`test_command_map_promote_has_state`** — `COMMAND_MAP["promote-classify"]["params"]` contains `state`

#### View tests:

17. **`test_classifier_v3_job_create_valid`** — POST creates a pending job, redirects with success message
18. **`test_classifier_v3_job_create_invalid_phase`** — POST with bad phase rejected
19. **`test_classifier_v3_job_create_invalid_depends_on`** — POST with nonexistent `depends_on` rejected
20. **`test_classifier_v3_job_create_terminal_depends_on`** — POST with completed/failed `depends_on` rejected
21. **`test_classifier_v3_job_create_duplicate`** — POST returns error when duplicate exists
22. **`test_process_start_rejects_v3`** — POST to `/process/reclassify/start/` returns error (phase removed from form_map)

#### Dependency validation tests:

23. **`test_create_v3_job_invalid_predecessor_phase`** — reclassify cannot depend on compare-classify (wrong predecessor)
24. **`test_create_v3_job_valid_predecessor_phase`** — reclassify can depend on extract-context (correct predecessor)
25. **`test_create_v3_job_nationwide_depends_on_per_state_rejected`** — Nationwide compare cannot depend on TX-only reclassify
26. **`test_create_v3_job_per_state_depends_on_nationwide_ok`** — TX reclassify can depend on nationwide extract-context
27. **`test_create_v3_job_state_mismatch_rejected`** — CA reclassify cannot depend on TX extract-context

#### Management command tests:

28. **`test_promote_with_state_filter`** — `promote_classification_run --state TX` only updates TX orgs
29. **`test_promote_without_state_unchanged`** — `promote_classification_run` without `--state` promotes all orgs (backward compat)

#### Concurrent / race tests:

30. **`test_create_v3_job_concurrent_race`** — Two threads call `create_v3_job()` for same phase+state simultaneously. One succeeds, one raises `DuplicateJobError`. Uses `threading.Thread` + `transaction.atomic()` + `select_for_update()` serialization.

#### Migration tests:

31. **`test_pipeline_process_cleanup_dead_pid`** — Create a `PipelineProcess` row with a dead PID → cleanup command marks stopped
32. **`test_pipeline_process_cleanup_alive_pid`** — Create a `PipelineProcess` row with the current process PID → cleanup command leaves it alone and warns

#### Integration tests:

33. **`test_cancel_v3_job_cascades`** — Cancel extract-context job → dependent reclassify also cancelled
34. **`test_retry_v3_job`** — Retry failed v3 job → new job with `retry_of` and incremented `attempt_number`

---

## Deployment Checklist

1. Deploy code (no Django migrations)
2. Restart gunicorn: `sudo systemctl restart gunicorn`
3. Run PipelineProcess cleanup script (Step 8) via `manage.py shell`
4. Verify: open `/classifier-v3/`, confirm job queue forms visible
5. Smoke test: queue one `extract-context` job for a single state, verify it appears in `/jobs/`
6. Verify: POST to `/process/reclassify/start/` returns error (phase removed)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Template changes break layout | Keep existing card structure; only replace form action and status display |
| `promote_classification_run --state` JOIN is slow | `nonprofits_seed.state` is indexed; subquery runs once per UPDATE |
| Stale PipelineProcess rows confuse `check_phase_conflict()` | Cleanup script (Step 8) runs post-deploy; `check_phase_conflict()` still checks PipelineProcess as safety net |
| Existing v3 bookmarks to `/process/<phase>/start/` break | Single operator, no external callers — removed phases return error with clear message |
| Invalid dependency chains (wrong predecessor, cross-state) | `create_v3_job()` validates predecessor phase and state compatibility server-side |
| Cleanup command skipped during deployment | Command is idempotent and repeatable; included in deployment checklist with explicit step number |
