# Spec 0042: Classifier V3 State Progress Grid

**Status**: Draft
**Author**: Architect
**Date**: 2026-05-13
**Dependencies**: Spec 0040 (V3 Job Queue), Spec 0041 (Operational Parity)

## Problem

The classifier v3 pipeline has 5 sequential steps per state — extract, reclassify, compare, resolve, promote — but the dashboard only shows **global** step status (running/pending/done). An operator managing national-scale classification has no way to see which states have completed which steps without querying the database directly.

The crawler and resolver pages already have per-state progress grids (colored badge chips showing completion percentages), but those are single-axis: one process per state. The v3 pipeline is a **matrix**: 51 states × 5 steps.

## Goals

1. **State × step visibility**: Show a compact grid where rows are states and columns are the 5 v3 pipeline steps. At a glance, the operator sees exactly where each state stands.

2. **Live data, no tracking column**: Use the same live-query pattern as the existing dashboard — join source-of-truth tables at render time. No new JSONB column or status tracking table.

3. **Color-coded progress signals**: Each cell shows a visual status: not started, in progress, complete, or failed. The operator should be able to scan 51 rows and immediately spot which states need attention.

4. **Auto-refresh**: The grid auto-refreshes via HTMX (every 5 seconds), matching the existing v3 status partial behavior.

5. **Fit the existing page**: The grid appears on the existing classifier v3 page, below the step status bar and above the step forms. No new page or URL.

## Non-Goals

- **Global operations dashboard**: A cross-pipeline "ops cockpit" for maintenance workflows (new 990s, followup crawls, new seed orgs) is out of scope. This spec adds state progress to the v3 page only.
- **Historical run comparison**: No side-by-side comparison of different classification runs. The grid shows current state only.
- **Per-org drill-down**: Clicking a cell does not show individual org/document status. The grid is state-level.
- **New database tables or columns**: All data is derived from existing tables.

## Technical Implementation

### Step Semantics: Two Categories

Steps fall into two categories with different progress models:

**Percentage-based steps** (Extract, Reclassify, Promote): Progress is a ratio of docs processed ÷ total PDF docs in the state. These steps write per-doc results to tables, so completion is measurable at the document level.

**Job-based steps** (Compare, Resolve): These steps operate on aggregates (comparing runs, resolving disagreements) and don't write per-doc completion markers. Their status is binary: a completed Job for the state means "done," otherwise "not done." The `partial` status does NOT apply to these steps — they show only `none`, `running`, `complete`, or `failed`.

### Denominator: PDF Documents Only

The denominator for percentage-based steps is **PDF documents in the state**: rows in `corpus` where `content_type = 'application/pdf'` joined to `nonprofits_seed` by `source_org_ein = ein`. This matches the extraction pipeline, which only processes PDFs.

### Data Sources

| Step | Category | "Done" signal |
|------|----------|---------------|
| **Extract** | Percentage | Row exists in `classification_context` (any `extraction_method`, including `failed:*` and `skipped:*`) |
| **Reclassify** | Percentage | Row exists in `classification_results` for the latest **finished** run (`MAX(id) WHERE finished_at IS NOT NULL`) |
| **Compare** | Job-based | Most recent `compare-classify` Job for this state has `status = 'completed'` |
| **Resolve** | Job-based | Most recent `resolve-disagree` Job for this state has `status = 'completed'` |
| **Promote** | Percentage | `corpus.v3_material_type IS NOT NULL` |

**Latest run rule**: Reclassify uses the most recent **finished** classification run globally. If a newer run is in progress, it is ignored — the grid shows what's been completed. States absent from the latest run show 0% reclassified.

### Per-Cell Status Values and Precedence

Each cell resolves to exactly one status. The precedence order (highest priority first):

```
running > failed > complete > partial > none
```

**Resolution rules for percentage-based steps** (Extract, Reclassify, Promote):
1. If a Job is `running` for this state+step → `running` (blue pulse)
2. Else if the most recent Job for this state+step is `failed` → `failed` (red)
3. Else if percentage = 100% → `complete` (green, show "100%")
4. Else if percentage > 0% → `partial` (yellow, show percentage)
5. Else → `none` (gray dash)

**Resolution rules for job-based steps** (Compare, Resolve):
1. If a Job is `running` for this state+step → `running` (blue pulse)
2. Else if the most recent Job for this state+step is `failed` → `failed` (red)
3. Else if a completed Job exists for this state+step → `complete` (green check)
4. Else → `none` (gray dash)

### Nationwide Job Handling

Jobs with `state_code = NULL` (nationwide) apply to all states. Precedence:

- A **state-specific** Job always takes priority over a **nationwide** Job for the same step, regardless of creation time.
- If no state-specific Job exists, the nationwide Job's status applies.

Example: A nationwide extract job is `running`, but TX also has a state-specific extract job that `completed` → TX shows `complete`, all other states show `running`.

### "Most Recent Job" Definition

For each `(phase, state_code)` pair, the authoritative Job is selected by:
1. Filter to `Job.objects.filter(phase=phase)` with matching `state_code` (exact match, not NULL)
2. Order by `created_at DESC`
3. Take the first row

For nationwide overlay, separately query `Job.objects.filter(phase=phase, state_code__isnull=True).order_by("-created_at").first()`.

### Cell Visual Contract

Each cell renders as a **single compact element** — either a percentage or an icon, not both:

| Status | Render | CSS classes |
|--------|--------|-------------|
| `none` | `—` | `text-gray-400` |
| `partial` | `45%` | `text-yellow-600 bg-yellow-50` |
| `complete` | `100%` (percentage steps) or `✓` (job steps) | `text-green-600 bg-green-50` |
| `running` | `●` (with pulse animation) | `text-blue-500 animate-pulse` |
| `failed` | `✗` | `text-red-600 bg-red-50` |

Table cells use `text-xs font-mono text-center` for compact rendering. The full grid (6 columns × 51 rows) fits within 1280px width.

### SQL Query Strategy

**Query 1: Doc-level progress** (steps 1, 2, 5)
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

Note: Uses `LEFT JOIN` from `nonprofits_seed` to `corpus` so states with zero PDF docs still appear (all columns = 0).

**Query 2: Job-level status** (all 5 steps)
```python
from django.db.models import Max

latest_jobs = (
    Job.objects
    .filter(phase__in=V3_PHASES)
    .exclude(status__in=["pending", "cancelled"])
    .values("phase", "state_code")
    .annotate(latest_id=Max("id"))
)
job_map = {}
for entry in latest_jobs:
    job = Job.objects.get(pk=entry["latest_id"])
    key = (job.phase, job.state_code)  # state_code may be None for nationwide
    job_map[key] = job.status
```

### Sorting

Sort goal: **states needing operator action first**, then fully complete states, then not-yet-started states.

Sort key per state:
1. **Primary**: Actionability score (states stuck mid-pipeline sort first)
   - Has any `failed` cell → score 0 (highest priority — needs fixing)
   - Has any `running` cell → score 1 (actively progressing)
   - Has mix of `complete`/`partial` and `none` → score 2 (partially done, may need next step kicked off)
   - All cells `complete` → score 3 (fully done)
   - All cells `none` → score 4 (not started)
2. **Secondary**: `total_docs DESC` (larger states first within same priority tier)

### States with Zero Documents

All 51 jurisdictions from `nonprofits_seed` appear in the grid, even if they have zero corpus documents. These states show `—` in every column and sort to the bottom (score 4). The `LEFT JOIN` from `nonprofits_seed` to `corpus` ensures they appear.

### Performance

**Measurement**: Server-side wall time for `_v3_state_grid()` (both queries combined), measured on the production RDS instance with warm buffer cache. Target: under 3 seconds.

The existing dashboard queries on the same tables run in ~1.5 seconds. This query adds two LEFT JOINs to smaller tables (`classification_context`: ~186K rows at full scale, `classification_results`: ~186K rows at full scale). Expected time: 2-3 seconds at full scale.

The grid runs inside its own HTMX partial endpoint, polled every 5 seconds. The partial response is small (~5KB of HTML for 51 rows).

**Index note**: No new indexes needed. The PK on `classification_context(content_sha256)` and `classification_results(run_id, content_sha256)` cover the join conditions. At full scale, Postgres will still prefer sequential scans for a GROUP BY across all rows.

### View Changes

**`views.py`**:
- Add `ClassifierV3StateGridPartial(HtmxLoginRequiredMixin, TemplateView)` — computes the grid data and returns the partial template
- Helper function `_v3_state_grid()` runs the two queries and merges into a list of row dicts

**`urls.py`**:
- Add URL: `classifier-v3/state-grid/` → `ClassifierV3StateGridPartial`

**`classifier_v3.html`**:
- Add `<div hx-get="{% url 'classifier_v3_state_grid' %}" hx-trigger="load, every 5s" hx-swap="innerHTML"></div>` below the existing status bar

### New Files

- `pipeline/templates/pipeline/partials/classifier_v3_state_grid.html` — the grid partial template

### Modified Files

- `pipeline/views.py` — add view class and helper function (~80 lines)
- `pipeline/templates/pipeline/classifier_v3.html` — add HTMX div (~3 lines)
- `dashboard/urls.py` (or `pipeline/urls.py` depending on routing) — add URL pattern (~1 line)

## Acceptance Criteria

1. The classifier v3 page shows a state × step grid with all states from `nonprofits_seed` (including states with zero docs) and 5 columns (extract, reclassify, compare, resolve, promote)
2. Percentage-based cells (extract, reclassify, promote) show percentage text; job-based cells (compare, resolve) show icon only (check or X)
3. Cell status precedence is `running > failed > complete > partial > none`
4. State-specific jobs take priority over nationwide jobs for the same step
5. The grid auto-refreshes every 5 seconds via HTMX without re-rendering the rest of the page
6. States sort by actionability: failed first, then running, then partially done, then fully complete, then not started
7. The grid is computed via live SQL queries against existing tables — no new database columns or tables
8. The denominator for percentages is PDF documents only (`content_type = 'application/pdf'`)
9. Reclassify percentage uses the latest **finished** classification run only; in-progress runs are ignored
10. Extract counts include all `classification_context` rows regardless of `extraction_method` (including `failed:*` and `skipped:*`)
11. Server-side query time for the grid is under 3 seconds on production with warm buffer cache
12. No regression to existing v3 page functionality (step status bar, forms, job display, cancel buttons)

## Testing Strategy

Tests use Django's test client and the existing test database pattern:

1. **Zero-doc state**: Seed a state with no corpus rows → all 5 columns show `none`
2. **Partial extraction**: Seed a state with 10 PDFs, add 5 `classification_context` rows → extract shows `50%`, others show `none`
3. **Complete extraction**: All PDFs in state have `classification_context` rows → extract shows `100%`
4. **Running job overlay**: Create a running Job for `extract-context` + state → cell shows `running` regardless of percentage
5. **Failed job overlay**: Create a failed Job for `reclassify` + state → cell shows `failed`
6. **Nationwide job fallback**: Create a running nationwide Job, no state-specific Job → all states show `running` for that step
7. **State-specific overrides nationwide**: State has a completed state-specific Job AND a running nationwide Job → state shows `complete`
8. **Job-based steps**: Compare/Resolve show `complete` only when a completed Job exists, never `partial`
9. **Sorting**: Create states with different progress levels → verify failed states sort first, not-started last
10. **HTMX partial**: GET request to the grid URL returns valid HTML fragment (not full page)

## Traps to Avoid

1. **Don't add a tracking table**: The existing tables already encode the state. A separate `state_step_status` table would be a stale cache of what the source-of-truth tables already show.

2. **Don't query per-state in a loop**: Running 51 separate queries (one per state) would be slow. Use GROUP BY in a single query, just like `_dashboard_stats()` does.

3. **Don't count extraction failures as "not started"**: A row in `classification_context` with `extraction_method = 'failed:encrypted'` or `'failed:corrupt'` still means extraction has been attempted. Count these as extracted (the step is done for that doc — it just had no usable text).

4. **Don't assume step ordering from data alone**: A state could have promoted results from a previous run but no reclassify results from the current run. The grid should reflect the *latest* run's state, not historical completions.

5. **Don't make the grid too wide**: 5 columns + state name must fit comfortably on a 1280px screen. Keep cell content minimal — percentage or icon, not both (never both).

6. **Don't forget nationwide jobs**: Jobs with `state_code = NULL` apply to all states. A running nationwide extract job means every state shows "running" in the extract column — unless that state has a more recent state-specific job.

7. **Don't use `partial` for Compare/Resolve**: These are job-based steps. They are either done or not done. There is no meaningful partial state.

8. **Don't INNER JOIN seed to corpus**: Use LEFT JOIN so states with zero corpus docs still appear in the grid. INNER JOIN would silently drop states.

## Security Considerations

- No new user input handling — the grid is read-only
- No new database writes
- Uses existing `LoginRequiredMixin` authentication
- HTMX partial uses existing `HtmxLoginRequiredMixin` pattern

## Consultation Log

### Round 1: Spec Review (2026-05-13)

**Gemini**: APPROVE (HIGH confidence). No key issues.

**Codex**: REQUEST_CHANGES (HIGH confidence). 10 findings:
1. Compare/Resolve `partial` status undefined → **Fixed**: Split into percentage-based vs job-based step categories. Compare/Resolve never show `partial`.
2. "Most recent Job" ambiguous → **Fixed**: Added explicit selection rules (filter by phase+state_code, order by created_at DESC, take first).
3. Nationwide job precedence undefined → **Fixed**: Added precedence rule: state-specific always overrides nationwide.
4. Denominator unclear (PDF only?) → **Fixed**: Explicitly defined as PDF documents only (`content_type = 'application/pdf'`).
5. Latest run rule incomplete → **Fixed**: Clarified: latest **finished** run only, in-progress runs ignored.
6. 51-row guarantee vs JOIN shape → **Fixed**: Changed to LEFT JOIN from nonprofits_seed to corpus; documented zero-doc state handling.
7. Sorting contradicts stated goal → **Fixed**: Replaced sort with actionability-based scoring (failed first, not-started last).
8. Performance claims loose → **Fixed**: Defined measurement boundary (server-side wall time, production, warm cache).
9. Visual contract contradicts examples → **Fixed**: Explicit table of what each status renders (percentage OR icon, never both).
10. Testing strategy missing → **Fixed**: Added 10 concrete test cases covering edge conditions.
