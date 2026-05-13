# Spec 0041: Classifier V3 Pipeline — Operational Parity

**Status:** Draft
**Author:** Architect
**Date:** 2026-05-13
**Dependencies:** Spec 0040 (Classifier V3 Job Queue Integration)

---

## Problem Statement

Spec 0040 migrated the classifier v3 pipeline (extract-context, reclassify, compare-classify, resolve-disagree, promote-classify) from PipelineProcess to the Job queue. The data model migration is correct: `create_v3_job()`, STAGE_REGISTRY entries, conflict detection, and orchestrator dispatch all work.

But the operator experience regressed. The pre-0040 PipelineProcess UI had a "Run" button that launched a process and a "Stop" button that killed it. Post-0040, the v3 dashboard page can queue jobs but:

1. **No dependency chaining UI** — the "Queue after job" dropdown (`_depends_on.html` partial) is used by crawler, resolver, and classifier pages but was never added to the v3 page. The backend supports it (`ClassifierV3JobCreateView` parses `depends_on`, `create_v3_job()` validates it), but no form element exposes it. An operator cannot queue reclassify to run after extract-context completes.

2. **No stop/cancel affordance** — the builder removed v3 phases from `ProcessStopView` (correctly — they're no longer PipelineProcess) but didn't add a Job-based cancel control. `JobCancelView` exists at `jobs/<pk>/cancel/` and works, but the operator has to navigate to the job detail page to use it. The v3 pipeline page has no stop button.

3. **No progress visibility for 4/5 stages** — only `extract_classification_context.py` accepts `--job-id` and flushes stats (`processed`, `failed`) into `config_json` every 10 seconds. The other four commands (`reclassify_corpus`, `compare_classifications`, `resolve_disagreements`, `promote_classification_run`) don't accept `--job-id` at all — they don't know they're running as a Job. Their step cards show only "Nm ago" with no progress counters.

4. **`depends_on` rejects completed jobs** — `ClassifierV3JobCreateView` (views.py:864) rejects any dependency where `dep_job.status in Job.TERMINAL_STATUSES`, which includes `completed`. But queuing work after an already-completed job is a valid workflow (e.g., queue reclassify after a completed extract-context). The orchestrator's `get_eligible_jobs()` already handles this correctly.

5. **`check_phase_conflict()` misses `scheduled` status** — only checks `status="running"`, not `scheduled`. The v3 code (`create_v3_job()`) got this right with `status__in=["pending", "scheduled", "running"]`, but `check_phase_conflict()` is still called by `_start_eligible_jobs()` as a double-check before launch. All 7 legacy factory functions have the same bug.

6. **Uncommitted `_launch_immediately()` dead code** — broken imports (`from pipeline.stages import ...`), leaks file handle, bypasses orchestrator audit trail. No template element triggers it. Must be deleted.

These are not new features. They are gaps between what 0040 was supposed to deliver and what it actually delivered, measured against what the other pipeline pages already provide.

## Goals

1. **Dependency chaining on the v3 page** — every step card gets the "Queue after job" dropdown, matching crawler/resolver/classifier UX
2. **Cancel button on running v3 jobs** — inline on the v3 pipeline page, not requiring navigation to job detail
3. **Progress stats for all 5 stages** — every v3 management command accepts `--job-id` and flushes `processed`/`failed` counters into `config_json`
4. **Allow completed dependencies** — `depends_on` validation only rejects `failed` and `cancelled`, not `completed`
5. **Fix `check_phase_conflict()` to include `scheduled`** — backport to all legacy factory functions too
6. **Remove `_launch_immediately()` dead code** — delete the uncommitted changes

## Non-Goals

- No new pipeline phases or stages
- No STAGE_REGISTRY or COMMAND_MAP unification (that's a separate spec)
- No PipelineProcess retirement (deferred)
- No fd 3 protocol upgrade for v3 stages (the `config_json` stats pattern is sufficient for now)
- No changes to the orchestrator daemon, scheduler, or Job model
- No new tests beyond what's needed to validate these fixes (the 45 existing tests still can't run due to DB infrastructure — that's a separate issue)

## Technical Implementation

### 1. Template: Add dependency dropdown to all 5 step cards

**File:** `lavandula/dashboard/pipeline/templates/pipeline/classifier_v3.html`

Add `{% include "pipeline/partials/_depends_on.html" %}` inside each of the 5 form elements, before the submit button. The partial already exists and renders a `<select name="depends_on">` dropdown populated from `dependency_choices` in the template context.

The context variable `dependency_choices` is already passed by `ClassifierV3View.get_context_data()` (views.py:757). No view changes needed.

### 2. Template: Add cancel button to running v3 jobs

**File:** `lavandula/dashboard/pipeline/templates/pipeline/classifier_v3.html`

For each running job displayed in a step card, add a small cancel form that posts to the existing `job_cancel` URL:

```html
<form method="post" action="{% url 'job_cancel' job.pk %}" class="inline">
  {% csrf_token %}
  <button type="submit" class="text-red-600 hover:text-red-800 text-xs ml-2"
          title="Cancel job #{{ job.pk }}">Cancel</button>
</form>
```

The `JobCancelView` (views.py:477) already handles this — it calls `cancel_job()` which sends SIGTERM, waits, escalates to SIGKILL, and cascades to dependents. The view currently redirects to `job_detail`; it should redirect back to the referrer or `classifier_v3` when the job is a v3 phase. Modify `JobCancelView` to check `request.META.get("HTTP_REFERER")` and redirect to `classifier_v3` if the cancelled job's phase is in `_V3_PHASES`.

### 3. Management commands: Add `--job-id` and stats flushing

**Pattern to replicate** (from `extract_classification_context.py`):

```python
# In add_arguments():
parser.add_argument("--job-id", type=int, default=None, help="Job ID for stats reporting")

# In handle():
job_id = options.get("job_id")
stats = {"processed": 0, "failed": 0}
stats_stop = threading.Event()

def stats_flusher():
    while not stats_stop.wait(10):
        try:
            _merge_stats(job_id, stats)
        except Exception:
            pass

if job_id:
    stats_thread = threading.Thread(target=stats_flusher, daemon=True)
    stats_thread.start()

# At completion:
stats_stop.set()
if job_id:
    _merge_stats(job_id, stats)
```

**Files to modify:**

| Command | File | What to count |
|---|---|---|
| `reclassify_corpus` | `management/commands/reclassify_corpus.py` | orgs classified / failed |
| `compare_classifications` | `management/commands/compare_classifications.py` | results compared |
| `resolve_disagreements` | `management/commands/resolve_disagreements.py` | disagreements resolved / failed |
| `promote_classification_run` | `management/commands/promote_classification_run.py` | rows promoted |

Each command already has an inner loop that processes items. Add `stats["processed"] += 1` and `stats["failed"] += 1` at the appropriate points. The `_merge_stats` helper reads the current `config_json` from the Job, merges the stats dict, and writes it back — identical to `extract_classification_context.py:396-401`.

Factor the shared stats flusher into a small helper (e.g., `pipeline/job_stats.py`) to avoid duplicating the threading boilerplate in 4 files. The helper should provide:
- `start_stats_flusher(job_id, stats_dict, interval=10)` → returns a stop event
- `merge_stats(job_id, stats_dict)` — the atomic read-merge-write

### 4. Fix `depends_on` validation

**File:** `lavandula/dashboard/pipeline/views.py`, line 864

Change:
```python
if dep_job.status in Job.TERMINAL_STATUSES:
```

To:
```python
if dep_job.status in ("failed", "cancelled"):
```

This allows `completed` as a valid dependency status. The orchestrator's `get_eligible_jobs()` already handles this: `Q(depends_on__status="completed")` is in the filter, so a job depending on a completed job is immediately eligible.

### 5. Fix `check_phase_conflict()` to include `scheduled`

**File:** `lavandula/dashboard/pipeline/orchestrator.py`, line 264

Change:
```python
qs = Job.objects.filter(phase=phase, status="running")
```

To:
```python
qs = Job.objects.filter(phase=phase, status__in=["running", "scheduled"])
```

**Also fix all 7 legacy factory functions** (same file) that use `status__in=["pending", "running"]` — add `"scheduled"` to each:

| Function | Line | Current | Fixed |
|---|---|---|---|
| `create_state_jobs` | 314 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |
| `create_resolve_job` | 356 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |
| `create_crawl_job` | 390 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |
| `create_classify_job` | 420 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |
| `create_990_index_job` | 464 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |
| `create_990_parse_job` | 492 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |
| `create_phone_enrich_job` | 518 | `["pending", "running"]` | `["pending", "scheduled", "running"]` |

### 6. Delete `_launch_immediately()` and `run_now` logic

**File:** `lavandula/dashboard/pipeline/views.py`

Revert the uncommitted changes that added:
- `run_now = request.POST.get("action") == "run_now"` (line 873)
- The `if run_now and not depends_on:` branch (lines 880-884)
- The entire `_launch_immediately()` method (lines 892-922)

Restore the original builder code which unconditionally shows "Queued {phase} job".

## Acceptance Criteria

1. Each of the 5 v3 step cards on `/pipeline/classifier-v3/` has a "Queue after job" dropdown showing running and pending jobs
2. Each running v3 job on the pipeline page has a "Cancel" button that stops the process
3. All 5 v3 management commands accept `--job-id` and report `processed`/`failed` counts visible on the dashboard within 10 seconds of progress
4. An operator can queue reclassify with `depends_on` set to a completed extract-context job without error
5. `check_phase_conflict()` returns True for phases that are `scheduled` (not yet running)
6. No `_launch_immediately` method exists in `views.py`
7. No regressions to existing pipeline pages (crawler, resolver, classifier, 990, phone enrich)

## Traps to Avoid

1. **Don't modify the `_depends_on.html` partial** — it's shared by 3 other pages. If you need v3-specific behavior, add a new partial or conditionally render.

2. **Don't change `JobCancelView` signature** — it's already used by the job detail page. Only change where it redirects, and only for v3 phases.

3. **Stats merge is not atomic** — `_merge_stats()` does read-then-write on `config_json`. Two concurrent flushers for the same job could clobber each other. This is fine because each job has exactly one process, so exactly one flusher. Don't "fix" this with a lock.

4. **`--job-id` is passed by the orchestrator** — the triage fix (`91b9f4a`) already adds `--job-id` to argv for all launched jobs. The management commands just need to accept the argument. Don't add `--job-id` passing logic — it already exists.

5. **Don't add stats flushing to the view layer** — the view reads stats from `config_json`, the management command writes them. The 5-second HTMX poll and 10-second flush interval mean stats lag by at most 15 seconds. That's fine.

6. **Keep `reclassify_corpus.py`'s `--where` parameter** — the CLI still accepts it for direct command-line use. The STAGE_REGISTRY and web form correctly exclude it. Don't remove `--where` from the management command itself.

## Verification Plan

For each item, the builder should manually verify on the running dashboard:

1. Navigate to `/pipeline/classifier-v3/`, confirm "Queue after job" dropdown appears in each step card
2. Queue an extract-context job, confirm it appears as running, confirm Cancel button appears next to it, click Cancel, confirm it transitions to cancelled
3. Queue an extract-context job with `--state=RI` (small state), watch stats counters update on the dashboard while it runs
4. Let an extract-context complete, then queue reclassify with `depends_on` set to the completed job — confirm it's accepted
5. Run the management commands directly with `--job-id` and verify stats appear in the Job's `config_json`
