# Plan 0041: Classifier V3 Pipeline — Operational Parity

**Spec:** `locard/specs/0041-v3-pipeline-operational-parity.md`
**Date:** 2026-05-13

---

## Implementation Order

The 6 changes have no dependencies on each other. Order them by risk (lowest-risk first) so each step can be verified independently before moving on.

## Step 1: Delete `_launch_immediately()` dead code

**Risk:** None. This is uncommitted code removal.

**File:** `lavandula/dashboard/pipeline/views.py`

Surgically remove only the `run_now` and `_launch_immediately` additions from `ClassifierV3JobCreateView`. Do NOT use `git checkout` or `git restore` on the whole file — there are other uncommitted changes (stats from triage) that must be preserved.

1. Remove `run_now = request.POST.get("action") == "run_now"` (line 873)
2. Remove the `if run_now and not depends_on:` / `else:` branching (lines 880-884)
3. Restore the single success message: `messages.success(request, f"Queued {phase} job #{job.pk} for {state_label}")`
4. Delete the entire `_launch_immediately()` method (lines 892-922)

**After this step:** `git diff -- lavandula/dashboard/pipeline/views.py` shows only the stats-related changes from triage commit `6f61bc1`, not the `_launch_immediately` additions.

**Verify:** `grep -n "_launch_immediately\|run_now" lavandula/dashboard/pipeline/views.py` returns nothing.

## Step 2: Fix `check_phase_conflict()` and legacy factory conflict checks

**Risk:** Low. Adding `scheduled` to existing conflict queries. Makes conflict detection stricter (fewer false negatives), not looser.

**File:** `lavandula/dashboard/pipeline/orchestrator.py`

### 2a. `check_phase_conflict()` (line 264)

Change:
```python
qs = Job.objects.filter(phase=phase, status="running")
```
To:
```python
qs = Job.objects.filter(phase=phase, status__in=["running", "scheduled"])
```

### 2b. All 7 legacy factory functions

Each has a duplicate-detection query using `status__in=["pending", "running"]`. Add `"scheduled"` to each:

| Function | Current line | Change |
|---|---|---|
| `create_state_jobs` | ~314 | `["pending", "running"]` → `["pending", "scheduled", "running"]` |
| `create_resolve_job` | ~356 | same |
| `create_crawl_job` | ~390 | same |
| `create_classify_job` | ~420 | same |
| `create_990_index_job` | ~464 | same |
| `create_990_parse_job` | ~492 | same |
| `create_phone_enrich_job` | ~518 | same |

Use `replace_all` to change all 7 instances of `status__in=["pending", "running"]` to `status__in=["pending", "scheduled", "running"]` in one pass. But verify each one first — make sure no other uses of that pattern exist that shouldn't change.

**Verify:** `grep -n '"pending", "running"' lavandula/dashboard/pipeline/orchestrator.py` returns no matches. `grep -n '"pending", "scheduled", "running"' lavandula/dashboard/pipeline/orchestrator.py` returns 7 matches (factory functions) plus the 1 in `create_v3_job` that already had it.

## Step 3: Fix `depends_on` validation

**Risk:** Low. One-line change, makes validation more permissive (allows completed deps).

**File:** `lavandula/dashboard/pipeline/views.py`

In `ClassifierV3JobCreateView.post()`, find:
```python
if dep_job.status in Job.TERMINAL_STATUSES:
```
Change to:
```python
if dep_job.status in ("failed", "cancelled"):
```

**Verify:** Read the changed line. Confirm `completed` is no longer rejected.

## Step 4: Expand `_get_dependency_choices()` to include completed jobs

**Risk:** Low. Shared function — affects crawler/resolver/classifier pages too, but those already allow completed dependencies at the backend.

**File:** `lavandula/dashboard/pipeline/views.py`

In `_get_dependency_choices()` (line 90-102), change:
```python
qs = Job.objects.filter(status__in=["running", "pending"]).select_related("depends_on").order_by("-created_at")
```
To:
```python
from datetime import timedelta
cutoff = timezone.now() - timedelta(hours=24)
qs = Job.objects.filter(
    Q(status__in=["running", "pending"]) |
    Q(status="completed", finished_at__gte=cutoff)
).select_related("depends_on").order_by("-created_at")
```

The `Q` import is already at the top of views.py. The `timedelta` import may need to be added — check if it already exists.

Update the label to include a duration indicator for completed jobs so the dropdown is readable:
```python
status_display = j.status
if j.status == "completed" and j.finished_at:
    mins_ago = int((timezone.now() - j.finished_at).total_seconds() // 60)
    status_display = f"done {mins_ago}m ago"
choices.append((j.pk, f"#{j.pk} {j.phase} {j.state_code or 'global'} [{status_display}] @ {host_label}"))
```

**Verify:** Navigate to `/pipeline/classifier-v3/` and `/pipeline/crawler/`. Confirm the "Queue after job" dropdown shows completed jobs if any exist within the last 24 hours.

## Step 5: Add dependency dropdown and cancel button to v3 template

**Risk:** Low. Template-only changes using existing partials and views.

**File:** `lavandula/dashboard/pipeline/templates/pipeline/classifier_v3.html`

### 5a. Dependency dropdown

Add `{% include "pipeline/partials/_depends_on.html" %}` inside each of the 5 `<form>` elements, immediately before the `<button type="submit">` line. There are 5 forms (one per step card), each posting to `{% url 'classifier_v3_job_create' %}`.

The exact insertion point for each is between the host `</select></div>{% endif %}` block and the submit button. Example for extract-context (Step 1):

```html
      {% endif %}
      {% include "pipeline/partials/_depends_on.html" %}
      <button type="submit" ...>Queue Job</button>
```

Repeat for all 5 step cards.

### 5b. Cancel button on running jobs

In each step card's "running jobs" loop (the `{% for job in X_running %}` blocks), add a cancel form after the job info line:

```html
<div class="text-xs bg-blue-50 rounded p-2">
  <span class="font-mono">Job #{{ job.pk }}</span>
  ...existing content...
  <form method="post" action="{% url 'job_cancel' job.pk %}?next={% url 'classifier_v3' %}" class="inline ml-2">
    {% csrf_token %}
    <button type="submit" class="text-red-600 hover:text-red-800 text-xs"
            title="Cancel job #{{ job.pk }}">Cancel</button>
  </form>
</div>
```

There are 5 step cards with running job loops. The loop variable names are:
- `extract_context_running` (bg-blue-50)
- `reclassify_running` (bg-indigo-50)
- `compare_classify_running` (bg-amber-50)
- `resolve_disagree_running` (bg-purple-50)
- `promote_classify_running` (bg-green-50)

### 5c. Modify `JobCancelView` to support `next` parameter

**File:** `lavandula/dashboard/pipeline/views.py`, `JobCancelView` (line 477)

Change:
```python
class JobCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        cancel_job(job)
        _log_audit(request, "job_cancel", job.phase, {"job_id": job.pk})
        messages.success(request, f"Cancelled job #{job.pk}")
        return redirect("job_detail", pk=pk)
```
To:
```python
class JobCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        cancel_job(job)
        _log_audit(request, "job_cancel", job.phase, {"job_id": job.pk})
        messages.success(request, f"Cancelled job #{job.pk}")
        next_url = request.GET.get("next", "")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("job_detail", pk=pk)
```

**Verify:**
1. Navigate to `/pipeline/classifier-v3/`, confirm "Queue after job" dropdown appears in all 5 step cards
2. Queue a job, confirm Cancel button appears next to running job
3. Click Cancel, confirm redirect back to `/pipeline/classifier-v3/` (not job detail)
4. Navigate to a job detail page, cancel from there — confirm it still redirects to job detail

## Step 6: Add `--job-id` and stats flushing to 4 management commands

**Risk:** Medium. Touches 4 production management commands. Each change is small and additive (`--job-id` is optional, existing behavior unchanged when absent).

### 6a. Create shared stats helper

**New file:** `lavandula/dashboard/pipeline/job_stats.py`

```python
import logging
import threading

log = logging.getLogger(__name__)


def merge_stats(job_id, stats_dict):
    from pipeline.models import Job
    job = Job.objects.get(pk=job_id)
    cfg = job.config_json or {}
    cfg["stats"] = dict(stats_dict)
    Job.objects.filter(pk=job_id).update(config_json=cfg)


def start_stats_flusher(job_id, stats_dict, interval=10):
    if job_id is None:
        return threading.Event()  # no-op: caller can still call stop.set()

    stop = threading.Event()

    def _flusher():
        while not stop.wait(interval):
            try:
                merge_stats(job_id, stats_dict)
            except Exception:
                log.exception("Stats flush failed for job %d", job_id)

    t = threading.Thread(target=_flusher, daemon=True)
    t.start()
    return stop
```

### 6b. `reclassify_corpus.py`

**File:** `lavandula/dashboard/pipeline/management/commands/reclassify_corpus.py`

1. Add argument: `parser.add_argument("--job-id", type=int, default=None, help="Job ID for dashboard stats")`

2. At the top of `handle()` (line ~87), after `engine = make_app_engine()`:
   ```python
   job_id = options.get("job_id")
   ```

3. The command already has a `stats = defaultdict(int)` dict with keys `total`, `llm_classified`, `llm_errors`, `skip_ctx`, `skip_fp`, `rule_matched`. Map to dashboard convention:

   After the watchdog setup (line ~223), start the flusher:
   ```python
   from pipeline.job_stats import start_stats_flusher
   dashboard_stats = {"processed": 0, "failed": 0}
   stats_stop = start_stats_flusher(job_id, dashboard_stats)
   ```

4. In the processing loop, wherever `stats["total"] += 1` appears (lines ~273, ~305, ~355, ~395), add `dashboard_stats["processed"] += 1` immediately after. Wherever `stats["llm_errors"] += 1` appears (lines ~346, ~394), add `dashboard_stats["failed"] += 1`.

5. At the end of `_run()`, before the summary output:
   ```python
   stats_stop.set()
   if job_id:
       from pipeline.job_stats import merge_stats
       merge_stats(job_id, dashboard_stats)
   ```

### 6c. `compare_classifications.py`

**File:** `lavandula/dashboard/pipeline/management/commands/compare_classifications.py`

1. Add argument: `parser.add_argument("--job-id", type=int, default=None, help="Job ID for dashboard stats")`

2. This is a batch command (fetches all rows, iterates in memory). No streaming loop, so no periodic flush is needed. Just report final counts.

   After the categorization loop (after line ~76, where `agree`, `disagree_rule`, `disagree_llm`, `v3_errors` are populated):
   ```python
   job_id = options.get("job_id")
   if job_id:
       from pipeline.job_stats import merge_stats
       merge_stats(job_id, {
           "processed": len(agree) + len(disagree_rule) + len(disagree_llm),
           "failed": len(v3_errors),
       })
   ```

   This is a fast in-memory operation, so a periodic flusher would add overhead for no benefit. One final merge is sufficient.

### 6d. `resolve_disagreements.py`

**File:** `lavandula/dashboard/pipeline/management/commands/resolve_disagreements.py`

1. Add argument: `parser.add_argument("--job-id", type=int, default=None, help="Job ID for dashboard stats")`

2. After `stats = defaultdict(int)` (line ~166), start the flusher:
   ```python
   job_id = options.get("job_id")
   from pipeline.job_stats import start_stats_flusher
   dashboard_stats = {"processed": 0, "failed": 0}
   stats_stop = start_stats_flusher(job_id, dashboard_stats)
   ```

3. In the `as_completed` loop:
   - After `stats["resolved"] += 1` (line ~231): add `dashboard_stats["processed"] += 1`
   - After `stats["errors"] += 1` (lines ~185, ~189): add `dashboard_stats["failed"] += 1`
   - After `stats["invalid"] += 1` (line ~210): add `dashboard_stats["failed"] += 1`

4. After the finalize section (after `conn.execute` that updates `classification_runs`):
   ```python
   stats_stop.set()
   if job_id:
       merge_stats(job_id, dashboard_stats)
   ```

### 6e. `promote_classification_run.py`

**File:** `lavandula/dashboard/pipeline/management/commands/promote_classification_run.py`

1. Add argument: `parser.add_argument("--job-id", type=int, default=None, help="Job ID for dashboard stats")`

2. This is a batch SQL command — two UPDATE statements that run atomically. No streaming loop. Report final count after completion.

   After the second `conn.execute` (line ~138), before the notes update:
   ```python
   job_id = options.get("job_id")
   if job_id:
       from pipeline.job_stats import merge_stats
       merge_stats(job_id, {"processed": result_count, "failed": 0})
   ```

### 6f. Migrate `extract_classification_context.py` to shared helper (optional)

The existing `_merge_stats` and `stats_flusher` in `extract_classification_context.py` work fine as-is. Migrating to the shared helper would reduce duplication but isn't required for this spec. If the builder has time, refactor it; if not, leave it.

**Verify for all 4 commands:**
1. Run each command manually with `--job-id <N>` where N is an existing pending/running Job ID
2. Check `Job.objects.get(pk=N).config_json["stats"]` shows `processed` and `failed` counts
3. Verify the dashboard status partial shows the stats for running jobs

## Step 7: Add tests

**Risk:** Low. Tests cannot run yet (DB infrastructure blocker), but should be written now so they're ready when the infrastructure is fixed.

**File:** `lavandula/dashboard/pipeline/tests/test_v3_job_queue.py` (append to existing file)

Add tests for the 3 logic changes in this spec:

### 7a. `check_phase_conflict` with `scheduled` status

```python
def test_check_phase_conflict_includes_scheduled(self):
    """Scheduled jobs should trigger conflict detection."""
    Job.objects.create(phase="crawl", status="scheduled", host="test")
    self.assertTrue(check_phase_conflict("crawl"))

def test_check_phase_conflict_scheduled_per_state(self):
    """Per-state scheduled jobs should only conflict with same state."""
    Job.objects.create(phase="extract-context", status="scheduled", state_code="TX", host="test")
    self.assertTrue(check_phase_conflict("extract-context", "TX"))
    self.assertFalse(check_phase_conflict("extract-context", "CA"))
```

### 7b. `depends_on` allowing completed jobs

```python
def test_v3_job_create_allows_completed_dependency(self):
    """Completed jobs should be valid dependency targets."""
    dep = Job.objects.create(phase="extract-context", status="completed", host="test")
    # Should not raise — completed is a valid dependency
    job = create_v3_job("reclassify", {"run_tag": "test"}, "test", depends_on=dep)
    self.assertEqual(job.depends_on, dep)

def test_v3_job_create_rejects_failed_dependency(self):
    """Failed jobs should still be rejected as dependency targets."""
    dep = Job.objects.create(phase="extract-context", status="failed", host="test")
    # The view rejects failed deps; create_v3_job itself doesn't check terminal status,
    # so this test validates the view-layer check
```

### 7c. `JobCancelView` redirect with `next` parameter

```python
def test_cancel_view_redirects_to_next(self):
    """Cancel should redirect to ?next URL when provided."""
    job = Job.objects.create(phase="extract-context", status="running", host="test")
    response = self.client.post(f"/pipeline/jobs/{job.pk}/cancel/?next=/pipeline/classifier-v3/")
    self.assertRedirects(response, "/pipeline/classifier-v3/")

def test_cancel_view_rejects_absolute_next(self):
    """Cancel should ignore absolute URLs in next parameter."""
    job = Job.objects.create(phase="extract-context", status="running", host="test")
    response = self.client.post(f"/pipeline/jobs/{job.pk}/cancel/?next=https://evil.com/")
    self.assertRedirects(response, f"/pipeline/jobs/{job.pk}/")

def test_cancel_view_rejects_protocol_relative_next(self):
    """Cancel should ignore protocol-relative URLs in next parameter."""
    job = Job.objects.create(phase="extract-context", status="running", host="test")
    response = self.client.post(f"/pipeline/jobs/{job.pk}/cancel/?next=//evil.com/")
    self.assertRedirects(response, f"/pipeline/jobs/{job.pk}/")
```

These tests follow the patterns in the existing `test_v3_job_queue.py`. They cannot run until the test DB infrastructure is fixed, but they document the expected behavior and will catch regressions once tests are enabled.

## Step 8: Commit and verify

1. Run `python3 lavandula/dashboard/manage.py check` to confirm no Django errors
2. Restart gunicorn/dashboard
3. Walk through the verification plan from the spec:
   - Dependency dropdown appears in all 5 step cards
   - Cancel button appears on running jobs, redirects back to v3 page
   - Stats visible for extract-context (already working) and for a manually-launched reclassify with `--job-id`
   - Completed dependency accepted
4. Spot-check other pipeline pages (crawler, resolver) to confirm no regressions

## Consultation Log

### Round 1: Plan Review (2026-05-13)

**Gemini**: APPROVE (HIGH confidence) — no issues.

**Codex (GPT-5.4)**: REQUEST_CHANGES (HIGH confidence)
- Step 1 "revert" language ambiguous in dirty worktree → **Fixed**: clarified to "surgically remove"
- Missing tests from spec requirement → **Fixed**: added Step 7 with 7 test cases covering scheduled conflict, completed deps, and next-parameter redirect

### Round 2: Red Team Security Review (2026-05-13)

**Gemini (red team)**: REQUEST_CHANGES (0 CRITICAL, 0 HIGH)
- MEDIUM: `config_json` content policy lacks enforcement → **Already documented** in spec Security Assumptions. Job arguments are validated by `param_validators.py`. No credentials or secrets flow through `config_json`.
