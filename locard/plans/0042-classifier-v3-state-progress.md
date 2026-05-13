# Plan 0042: Classifier V3 State Progress Grid

**Spec**: `locard/specs/0042-classifier-v3-state-progress.md`
**Date**: 2026-05-13

## Overview

Add a state × step progress grid to the classifier v3 dashboard page. The grid shows 51 states × 5 pipeline steps, with color-coded cells indicating progress (percentage for extract/reclassify/promote, job status for compare/resolve). Auto-refreshes via HTMX.

## Phase 1: Backend — View and Helper Function

**File**: `lavandula/dashboard/pipeline/views.py`

### Step 1.1: Add `_v3_state_grid()` helper

Add after the existing `_scan_v3_logs()` function (~line 733). This function runs two queries and merges results.

**Query 1** — Raw SQL via Django's `connections["default"]` cursor (matching existing pattern in `_dashboard_stats()`):

```sql
SELECT
    s.state,
    COUNT(DISTINCT c.content_sha256) AS total_docs,
    COUNT(DISTINCT cc.content_sha256) AS extracted,
    COUNT(DISTINCT cr.content_sha256) AS reclassified,
    COUNT(DISTINCT CASE WHEN c.v3_material_type IS NOT NULL
          THEN c.content_sha256 END) AS promoted
FROM lava_corpus.nonprofits_seed s
LEFT JOIN lava_corpus.corpus c
    ON s.ein = c.source_org_ein AND c.content_type = 'application/pdf'
LEFT JOIN lava_corpus.classification_context cc
    ON c.content_sha256 = cc.content_sha256
LEFT JOIN lava_corpus.classification_results cr
    ON c.content_sha256 = cr.content_sha256
    AND cr.run_id = (SELECT MAX(id) FROM lava_corpus.classification_runs
                     WHERE finished_at IS NOT NULL)
GROUP BY s.state
ORDER BY s.state
```

Use `connections["default"]` (not `connections["pipeline"]`) — this query hits `lava_corpus` schema tables directly, same as `_dashboard_stats()`.

**Query 2** — ORM query for job status.

The authoritative job per `(phase, state_code)` is the one with the highest `id`. Django's auto-incrementing PK is monotonically increasing and always matches `created_at` ordering (both set at INSERT time), so `Max("id")` is equivalent to "most recent by created_at" but avoids ties. The spec says `created_at DESC`; `Max("id")` is the correct implementation.

Build two maps: one for state-specific jobs, one for nationwide jobs:

```python
from django.db.models import Max

latest_per_phase_state = (
    Job.objects
    .filter(phase__in=_V3_PHASES)
    .exclude(status__in=["pending", "cancelled"])
    .values("phase", "state_code")
    .annotate(latest_id=Max("id"))
)
job_ids = [e["latest_id"] for e in latest_per_phase_state]
jobs = Job.objects.filter(pk__in=job_ids).values("id", "phase", "state_code", "status")

# Separate state-specific and nationwide
state_job_map = {}   # (phase, state_code) -> status, where state_code is NOT None
nationwide_job_map = {}  # phase -> status
for j in jobs:
    if j["state_code"] is None:
        nationwide_job_map[j["phase"]] = j["status"]
    else:
        state_job_map[(j["phase"], j["state_code"])] = j["status"]
```

**Merge logic** — Explicit precedence: state-specific job always wins over nationwide.

For each state row, resolve each cell in two steps:
1. **Select authoritative status**: Use state-specific if it exists, else nationwide, else None.
2. **Map to cell status** using the spec's precedence rules.

```python
PHASE_LIST = [
    ("extract-context", "extracted", True),      # (phase, doc_count_key, is_percentage)
    ("reclassify", "reclassified", True),
    ("compare-classify", None, False),
    ("resolve-disagree", None, False),
    ("promote-classify", "promoted", True),
]

for state_row in state_rows:
    cells = []
    for phase, doc_key, is_pct in PHASE_LIST:
        # Step 1: Select authoritative job status
        # State-specific ALWAYS overrides nationwide, regardless of creation time
        state_specific = state_job_map.get((phase, state_row["state"]))
        nationwide = nationwide_job_map.get(phase)
        job_status = state_specific if state_specific is not None else nationwide

        # Step 2: Map to cell status (precedence: running > failed > complete > partial > none)
        if job_status == "running":
            cells.append({"status": "running"})
        elif job_status == "failed":
            cells.append({"status": "failed"})
        elif is_pct:
            total = state_row["total_docs"]
            done = state_row.get(doc_key, 0)
            if total == 0:
                cells.append({"status": "none"})
            else:
                pct = round(done / total * 100)
                if pct >= 100:
                    cells.append({"status": "complete", "pct": 100})
                elif pct > 0:
                    cells.append({"status": "partial", "pct": pct})
                else:
                    cells.append({"status": "none"})
        else:
            # Job-based step: only complete or none
            if job_status == "completed":
                cells.append({"status": "complete"})
            else:
                cells.append({"status": "none"})
    state_row["cells"] = cells
```

**Sorting** — Compute actionability score per state:

```python
def _action_score(row):
    statuses = [c["status"] for c in row["cells"]]
    if "failed" in statuses:
        return 0
    if "running" in statuses:
        return 1
    has_progress = any(s in ("complete", "partial") for s in statuses)
    has_none = any(s == "none" for s in statuses)
    if has_progress and has_none:
        return 2
    if has_progress:
        return 3
    return 4

state_rows.sort(key=lambda r: (_action_score(r), -r["total_docs"]))
```

Return `{"grid_rows": state_rows}`.

### Step 1.2: Add `ClassifierV3StateGridPartial` view class

```python
class ClassifierV3StateGridPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/classifier_v3_state_grid.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_v3_state_grid())
        return ctx
```

### Step 1.3: Add URL pattern

**File**: `lavandula/dashboard/pipeline/urls.py`

Add after the existing `classifier-v3/log/` pattern:

```python
path("classifier-v3/state-grid/", views.ClassifierV3StateGridPartial.as_view(), name="classifier_v3_state_grid"),
```

## Phase 2: Frontend — Templates

### Step 2.1: Create grid partial template

**New file**: `lavandula/dashboard/pipeline/templates/pipeline/partials/classifier_v3_state_grid.html`

```html
<div class="bg-white rounded-lg shadow p-4 mb-4">
  <h3 class="text-sm font-semibold text-gray-700 mb-2">State Progress</h3>
  <div class="overflow-x-auto">
    <table class="min-w-full text-xs">
      <thead class="bg-gray-100">
        <tr>
          <th class="px-2 py-1 text-left">State</th>
          <th class="px-2 py-1 text-center">Extract</th>
          <th class="px-2 py-1 text-center">Reclassify</th>
          <th class="px-2 py-1 text-center">Compare</th>
          <th class="px-2 py-1 text-center">Resolve</th>
          <th class="px-2 py-1 text-center">Promote</th>
        </tr>
      </thead>
      <tbody>
        {% for row in grid_rows %}
        <tr class="border-b">
          <td class="px-2 py-1 font-medium">{{ row.state }}</td>
          {% for cell in row.cells %}
          <td class="px-2 py-1 text-center font-mono">
            {% if cell.status == "running" %}
            <span class="text-blue-500 animate-pulse">●</span>
            {% elif cell.status == "failed" %}
            <span class="text-red-600 bg-red-50 px-1 rounded">✗</span>
            {% elif cell.status == "complete" %}
            {% if cell.pct %}
            <span class="text-green-600 bg-green-50 px-1 rounded">100%</span>
            {% else %}
            <span class="text-green-600 bg-green-50 px-1 rounded">✓</span>
            {% endif %}
            {% elif cell.status == "partial" %}
            <span class="text-yellow-600 bg-yellow-50 px-1 rounded">{{ cell.pct }}%</span>
            {% else %}
            <span class="text-gray-400">—</span>
            {% endif %}
          </td>
          {% endfor %}
        </tr>
        {% empty %}
        <tr><td colspan="6" class="px-2 py-4 text-center text-gray-400">No states found</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

### Step 2.2: Add HTMX div to classifier v3 page

**File**: `lavandula/dashboard/pipeline/templates/pipeline/classifier_v3.html`

Add immediately after the existing status auto-polling div (line 9):

```html
<!-- State progress grid -->
<div hx-get="{% url 'classifier_v3_state_grid' %}" hx-trigger="load, every 5s" hx-swap="innerHTML"></div>
```

## Phase 3: Tests

**File**: `lavandula/dashboard/pipeline/tests/test_v3_state_grid.py` (new)

### Test data strategy

The test database uses the same `lava_corpus` schema as production (created by Django migrations + RDS migration SQL). Tests follow the existing pattern in `test_v3_job_queue.py`:

- **ORM-managed tables** (`NonprofitSeed`, `Job`): Create via Django ORM
- **Raw SQL tables** (`corpus`, `classification_context`, `classification_results`, `classification_runs`): Insert via `connections["default"].cursor()` with explicit `lava_corpus.` schema prefix, cleaned up in `tearDown`

Each test calls `_v3_state_grid()` directly (unit-testing the helper) rather than hitting the HTTP endpoint, except test 13 which verifies the HTMX partial. This avoids auth boilerplate and focuses on logic.

### Test cases:

1. `test_zero_doc_state` — Seed a state with no corpus rows → all 5 cells `none`
2. `test_partial_extraction` — 10 PDFs, 5 context rows → extract shows `50%`, others `none`
3. `test_complete_extraction` — All PDFs have context rows → extract shows `100%`
4. `test_running_job_overlay` — Running job for extract-context + state → cell shows `running` regardless of percentage
5. `test_failed_job_overlay` — Failed job for reclassify + state → cell shows `failed`
6. `test_nationwide_fallback` — Running nationwide job, no state-specific job → all states show `running`
7. `test_state_specific_overrides_nationwide` — State completed + nationwide running → state shows `complete`
8. `test_job_based_steps_no_partial` — Compare/Resolve with completed job show `complete`, without show `none`, never `partial`
9. `test_sorting_actionability` — Create states: one with failed job, one running, one partial, one complete, one not started → verify sort order 0,1,2,3,4
10. `test_row_count_matches_states` — Seed 3 states (2 with docs, 1 without) → grid has exactly 3 rows, zero-doc state appears
11. `test_multiple_seeds_same_state` — 5 seed orgs in same state, each with 2 PDFs → total_docs = 10, not 5
12. `test_in_progress_run_ignored` — Create a finished run (id=1) and an unfinished run (id=2) → reclassify uses run 1 only
13. `test_htmx_partial_response` — Authenticated GET to `/dashboard/classifier-v3/state-grid/` returns 200, HTML fragment (no `<html>` tag), contains `<table>`
14. `test_pdf_only_denominator` — Seed state with 5 PDF + 3 non-PDF corpus rows → total_docs = 5

### Performance verification (AC11)

After deployment, manually time the grid query on production:

```python
import time
t0 = time.monotonic()
result = _v3_state_grid()
elapsed = time.monotonic() - t0
print(f"Grid query: {elapsed:.2f}s")
```

If elapsed exceeds 3 seconds, investigate the EXPLAIN ANALYZE output. Likely fix: the existing PKs and indexes are sufficient, but if `classification_context` or `classification_results` grow past ~500K rows, the Postgres planner may benefit from `ANALYZE` on those tables.

## File Summary

| File | Action | Lines |
|------|--------|-------|
| `pipeline/views.py` | Modify | +~80 (helper + view class) |
| `pipeline/urls.py` | Modify | +1 (URL pattern) |
| `pipeline/templates/pipeline/classifier_v3.html` | Modify | +2 (HTMX div) |
| `pipeline/templates/pipeline/partials/classifier_v3_state_grid.html` | New | ~40 |
| `pipeline/tests/test_v3_state_grid.py` | New | ~200 |

Total: ~320 lines, 3 modified files, 2 new files.

## Acceptance Criteria Mapping

| AC | Phase | How verified |
|----|-------|-------------|
| AC1 (grid with all states + 5 cols) | Phase 1+2 | Tests 1, 10 (zero-doc state + row count) |
| AC2 (percentage vs icon) | Phase 2 | Tests 2-3 (percentage), Test 8 (icon) |
| AC3 (precedence) | Phase 1 | Tests 4-5 (running/failed override) |
| AC4 (state-specific > nationwide) | Phase 1 | Tests 6-7 |
| AC5 (HTMX auto-refresh) | Phase 2 | Test 13 |
| AC6 (actionability sort) | Phase 1 | Test 9 |
| AC7 (no new tables) | Phase 1 | Code review |
| AC8 (PDF-only denominator) | Phase 1 | Test 14 (non-PDF excluded) |
| AC9 (latest finished run) | Phase 1 | Test 12 (in-progress run ignored) |
| AC10 (extract includes failed:*) | Phase 1 | Test 3 variant |
| AC11 (< 3s query) | Phase 1 | Manual timing on production |
| AC12 (no regression) | Phase 2+3 | Manual + existing tests pass |
