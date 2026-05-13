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

**Query 2** — ORM query for job status:

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
job_map = {(j["phase"], j["state_code"]): j["status"] for j in jobs}
```

**Merge logic** — For each state row from Query 1:

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
        # Check state-specific job first, then nationwide fallback
        job_status = job_map.get((phase, state_row["state"]))
        nationwide_status = job_map.get((phase, None))
        effective_status = job_status or nationwide_status

        if effective_status == "running":
            cells.append({"status": "running"})
        elif job_status == "failed" or (job_status is None and nationwide_status == "failed"):
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
            # Job-based step
            if effective_status == "completed":
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

Use Django's `TestCase` with the test database. Tests need to create `NonprofitSeed` and `Job` model instances. For raw SQL tables (`classification_context`, `classification_results`, `classification_runs`, `corpus`), use Django's cursor to insert test rows directly.

### Test cases (from spec):

1. `test_zero_doc_state` — Seed state with no corpus → all cells `none`
2. `test_partial_extraction` — 10 PDFs, 5 context rows → extract `50%`
3. `test_complete_extraction` — All PDFs have context → extract `100%`
4. `test_running_job_overlay` — Running job overrides percentage → `running`
5. `test_failed_job_overlay` — Failed job → `failed`
6. `test_nationwide_fallback` — Running nationwide job, no state-specific → `running`
7. `test_state_specific_overrides_nationwide` — State completed + nationwide running → state shows `complete`
8. `test_job_based_steps_no_partial` — Compare/Resolve never show `partial`
9. `test_sorting_actionability` — Failed states sort first
10. `test_htmx_partial_response` — GET returns HTML fragment, not full page

Each test uses Django's test client to GET `/dashboard/classifier-v3/state-grid/` and inspects the response HTML. For data setup, tests insert directly into `lava_corpus.*` tables via cursor.

## File Summary

| File | Action | Lines |
|------|--------|-------|
| `pipeline/views.py` | Modify | +~80 (helper + view class) |
| `pipeline/urls.py` | Modify | +1 (URL pattern) |
| `pipeline/templates/pipeline/classifier_v3.html` | Modify | +2 (HTMX div) |
| `pipeline/templates/pipeline/partials/classifier_v3_state_grid.html` | New | ~40 |
| `pipeline/tests/test_v3_state_grid.py` | New | ~150 |

Total: ~270 lines, 3 modified files, 2 new files.

## Acceptance Criteria Mapping

| AC | Phase | How verified |
|----|-------|-------------|
| AC1 (grid with all states + 5 cols) | Phase 1+2 | Test 1 (zero-doc state appears) |
| AC2 (percentage vs icon) | Phase 2 | Tests 2-3 (percentage), Test 8 (icon) |
| AC3 (precedence) | Phase 1 | Tests 4-5 (running/failed override) |
| AC4 (state-specific > nationwide) | Phase 1 | Tests 6-7 |
| AC5 (HTMX auto-refresh) | Phase 2 | Test 10 |
| AC6 (actionability sort) | Phase 1 | Test 9 |
| AC7 (no new tables) | Phase 1 | Code review |
| AC8 (PDF-only denominator) | Phase 1 | Test 2 (verify non-PDF excluded) |
| AC9 (latest finished run) | Phase 1 | Implicit in SQL |
| AC10 (extract includes failed:*) | Phase 1 | Test 3 variant |
| AC11 (< 3s query) | Phase 1 | Manual verification |
| AC12 (no regression) | Phase 2+3 | Manual + existing tests pass |
