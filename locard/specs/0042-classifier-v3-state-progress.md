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

### Data Sources

Each cell's status is computed by joining `nonprofits_seed` (state) → `corpus` (docs per state) against the relevant step's output table:

| Step | "Done" signal | SQL join |
|------|--------------|----------|
| **Extract** | Row exists in `classification_context` | `corpus c JOIN classification_context cc ON c.content_sha256 = cc.content_sha256` |
| **Reclassify** | Row exists in `classification_results` for the latest run | `corpus c JOIN classification_results cr ON c.content_sha256 = cr.content_sha256 WHERE cr.run_id = <latest>` |
| **Compare** | Compare job completed for this state | `Job.objects.filter(phase='compare-classify', state_code=state, status='completed')` |
| **Resolve** | Resolve job completed for this state | `Job.objects.filter(phase='resolve-disagree', state_code=state, status='completed')` |
| **Promote** | `corpus.v3_material_type IS NOT NULL` | Direct column check on corpus |

**Why Compare and Resolve use Job status instead of table data**: These steps produce analysis output (comparison reports, tiebreaker verdicts) but don't have a per-doc completion flag that maps cleanly to "state X is done." Job completion for the state is the correct signal — if the compare job for TX ran and completed, TX is compared.

### Per-Cell Status Values

Each cell has one of 5 states:

| Status | Visual | Meaning |
|--------|--------|---------|
| `none` | Gray empty | 0% — step not started for this state |
| `partial` | Yellow with percentage | 1-99% of docs processed |
| `complete` | Green check | 100% (or step's job completed) |
| `running` | Blue pulse | Job currently running for this state+step |
| `failed` | Red X | Most recent job for this state+step failed |

For steps 1 (Extract) and 5 (Promote), percentage = docs with context/promotion ÷ total docs in state. For steps 2-4, percentage is derived from classification_results count or job completion status.

### SQL Query Strategy

A single view function computes the full grid in **two queries** (same pattern as `_dashboard_stats()`):

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
JOIN lava_corpus.corpus c ON s.ein = c.source_org_ein
LEFT JOIN lava_corpus.classification_context cc
    ON c.content_sha256 = cc.content_sha256
LEFT JOIN lava_corpus.classification_results cr
    ON c.content_sha256 = cr.content_sha256
    AND cr.run_id = (SELECT MAX(id) FROM lava_corpus.classification_runs
                     WHERE finished_at IS NOT NULL)
GROUP BY s.state
```

**Query 2: Job-level status** (overlay running/failed/completed for all 5 steps)
```python
jobs = Job.objects.filter(
    phase__in=V3_PHASES,
    status__in=["running", "completed", "failed"],
).values("phase", "state_code", "status").order_by("-created_at")
```

Combine: for each state × step, the doc-level query gives the percentage, and the job query overlays running/failed status.

### Template Structure

The grid renders as an HTML table inside a new HTMX partial:

```
pipeline/partials/classifier_v3_state_grid.html
```

Layout:
```
┌───────┬──────────┬────────────┬─────────┬─────────┬─────────┐
│ State │ Extract  │ Reclassify │ Compare │ Resolve │ Promote │
├───────┼──────────┼────────────┼─────────┼─────────┼─────────┤
│ TX    │ ██ 100%  │ ░░ 45%     │ —       │ —       │ —       │
│ CA    │ ██ 100%  │ ●● running │ —       │ —       │ —       │
│ NY    │ ░░ 12%   │ —          │ —       │ —       │ —       │
│ FL    │ —        │ —          │ —       │ —       │ —       │
│ ...   │          │            │         │         │         │
└───────┴──────────┴────────────┴─────────┴─────────┴─────────┘
```

Each cell is a small colored badge:
- **Gray dash** `—`: not started (0%)
- **Yellow** `░░ 45%`: partial progress
- **Green** `██ 100%` or `✓`: complete
- **Blue pulse** `●● running`: job in progress
- **Red** `✗ failed`: last job failed

### Sorting

States sort by pipeline progress (most advanced first), then by doc count within the same progress level. This puts "states that need attention" at the top — states stuck partway through show before states not yet started.

Sort key: `(max_completed_step DESC, total_docs DESC)`

### Performance

The existing dashboard queries (112K seed rows + 186K corpus rows) run in ~1.5 seconds from buffer cache. This grid adds one LEFT JOIN to `classification_context` (6K rows currently, growing to ~186K) and one LEFT JOIN to `classification_results` (2K rows currently, growing to ~186K). At full scale these are still small enough for sequential scans.

The grid query runs inside the HTMX partial, polled every 5 seconds. Since the partial is its own endpoint, the grid refresh doesn't re-render the forms.

**Index note**: No new indexes needed at current scale. If `classification_context` grows past ~500K rows and the query exceeds 3 seconds, add `CREATE INDEX idx_cc_sha ON classification_context(content_sha256)` — but the PK already covers this.

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

- `pipeline/views.py` — add view class and helper function (~60 lines)
- `pipeline/templates/pipeline/classifier_v3.html` — add HTMX div (~3 lines)
- `dashboard/urls.py` (or `pipeline/urls.py` depending on routing) — add URL pattern (~1 line)

## Acceptance Criteria

1. The classifier v3 page shows a state × step grid with 51 rows (all US states + DC) and 5 columns (extract, reclassify, compare, resolve, promote)
2. Each cell displays one of: not started (gray), partial (yellow + percentage), complete (green), running (blue pulse), failed (red)
3. The grid auto-refreshes every 5 seconds via HTMX without re-rendering the rest of the page
4. States are sorted by pipeline progress (most advanced first), then by document count
5. The grid is computed via live SQL queries against existing tables — no new database columns or tables
6. Extract and Promote percentages are accurate: extracted docs ÷ total PDFs in state, promoted docs ÷ total PDFs in state
7. Running/failed status reflects the most recent Job for each state × step combination
8. The grid loads in under 3 seconds at current data scale (186K corpus, 112K seeds)
9. No regression to existing v3 page functionality (step status bar, forms, job display, cancel buttons)

## Traps to Avoid

1. **Don't add a tracking table**: The existing tables already encode the state. A separate `state_step_status` table would be a stale cache of what the source-of-truth tables already show.

2. **Don't query per-state in a loop**: Running 51 separate queries (one per state) would be slow. Use GROUP BY in a single query, just like `_dashboard_stats()` does.

3. **Don't count extraction failures as "not started"**: A row in `classification_context` with `extraction_method = 'failed:encrypted'` or `'failed:corrupt'` still means extraction has been attempted. Count these as extracted (the step is done for that doc — it just had no usable text).

4. **Don't assume step ordering from data alone**: A state could have promoted results from a previous run but no reclassify results from the current run. The grid should reflect the *latest* run's state, not historical completions.

5. **Don't make the grid too wide**: 5 columns + state name must fit comfortably on a 1280px screen. Keep cell content minimal — percentage or icon, not both.

6. **Don't forget nationwide jobs**: Jobs with `state_code = NULL` apply to all states. A running nationwide extract job means every state shows "running" in the extract column.

## Security Considerations

- No new user input handling — the grid is read-only
- No new database writes
- Uses existing `LoginRequiredMixin` authentication
- HTMX partial uses existing `HtmxLoginRequiredMixin` pattern
