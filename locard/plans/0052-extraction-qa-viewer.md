# Plan 0052: Extraction QA Viewer (PDF + Metrics/Stories)

**Spec:** `locard/specs/0052-extraction-qa-viewer.md`
**Created:** 2026-05-26

## Overview

Build an interactive QA viewer that displays a PDF alongside its LLM-extracted metrics and stories, with hover-to-highlight and click-to-lock linking between extraction items and their source locations in the document.

## Prerequisites

- RDS tables `lava_vocab.llm_metrics` and `lava_vocab.llm_stories` exist (Spec 0051, already applied)
- At least one extraction run with data (run 9 or 10, currently in progress)
- S3 bucket `lavandula-nonprofit-collaterals` accessible from cloud2

## Phase 1: PDF.js Vendor Setup

**Goal:** Get PDF.js 4.x vendored into the project and serving correctly.

### Steps

1. Download PDF.js 4.x pre-built release from GitHub releases (`pdfjs-4.x.x-dist.zip`)
2. Extract into `lavandula/dashboard/pipeline/static/vendor/pdfjs/`:
   - `pdf.min.mjs`
   - `pdf.worker.min.mjs`
   - `pdf_viewer.mjs`
   - `pdf_viewer.css`
3. Create a minimal test page that loads a presigned PDF URL and renders it via PDF.js viewer components
4. Verify text layer renders (text is selectable over the canvas)
5. Verify `findController` can search and highlight text

### Acceptance Criteria
- PDF renders in browser via PDF.js (not browser native viewer)
- Text layer is present (text can be selected)
- `findController.executeCommand('find', { query: 'some text' })` highlights matches

### Files Created/Modified
- `lavandula/dashboard/pipeline/static/vendor/pdfjs/` (new directory, vendored files)

---

## Phase 2: Django View & URL Routing

**Goal:** Backend view that generates presigned URL and queries extraction data.

### Steps

1. Add URL patterns to `lavandula/dashboard/pipeline/urls.py`:
   - `reports/<str:sha>/qa/` → `ExtractionQAView`
   - `orgs/<str:ein>/qa/` → `OrgExtractionQARedirectView` (redirects to first extracted doc)
2. Create `ExtractionQAView` in `views.py`:
   - `LoginRequiredMixin` + `DetailView` on Report model
   - Generate presigned S3 URL (15-min expiry)
   - Query `llm_metrics` and `llm_stories` for (sha, run_id)
   - Query available runs for this document
   - Compute prev/next docs for the org (ordered by `report_year DESC, content_sha256 ASC`, filtered to docs with extraction data)
   - Get org name from `NonprofitSeed`
3. Create `OrgExtractionQARedirectView`:
   - Look up the first document for this EIN that has extraction data
   - Redirect to `/dashboard/reports/<sha>/qa/`
   - 404 if org has no extracted documents
4. Add `Cache-Control: no-store` header to the response

### Acceptance Criteria
- Authenticated GET to `/dashboard/reports/<sha>/qa/` returns 200
- Context contains: `pdf_url`, `metrics`, `stories`, `available_runs`, `org`, `org_docs`
- Unauthenticated request redirects to login
- Response has `Cache-Control: no-store`
- `/dashboard/orgs/<ein>/qa/` redirects to first extracted doc

### Files Modified
- `lavandula/dashboard/pipeline/urls.py`
- `lavandula/dashboard/pipeline/views.py`

---

## Phase 3: Template & Layout

**Goal:** HTML template with side-by-side layout, metric/story lists, and toolbar.

### Steps

1. Create `lavandula/dashboard/pipeline/templates/pipeline/extraction_qa.html`
2. Layout structure:
   - Top toolbar: Back button, prev/next nav, run dropdown, org name + doc position
   - Left panel (60%): PDF container div (`#pdf-container` with `#pdf-viewer` inside)
   - Right panel (40%): scrollable, contains metrics section and stories section
3. Metrics section:
   - Header: "METRICS ({count})"
   - Each row: locate icon (⊕), `metric_text` (primary line), `metric_type` · `metric_value` `unit` · `geo_impact` badge (secondary line)
   - Empty state: "No metrics extracted"
4. Stories section:
   - Header: "STORIES ({count})"
   - Each row: locate icon (⊕), `story_title` (primary), first sentence of `story_summary` (secondary), theme tags as pills
   - Empty state: "No stories extracted"
5. Serialize extraction data for JavaScript via `json_script` template tag:
   - `{{ metrics_json|json_script:"metrics-data" }}`
   - `{{ stories_json|json_script:"stories-data" }}`
6. Include PDF.js CSS and JS files
7. Include `extraction-qa.css` and `extraction-qa.js`
8. Run dropdown: `<select>` with available runs, triggers page reload with `?run_id=X`
9. Prev/Next: standard `<a>` links to adjacent doc QA pages (hidden if single doc)

### Acceptance Criteria
- Page renders with correct layout proportions
- Metrics show metric_text, metric_type, value+unit, geo_impact
- Stories show title, summary excerpt, themes
- Empty states display correctly
- Run dropdown shows available runs
- Prev/Next navigation works (or hidden for single-doc orgs)
- Extraction data available in DOM via `json_script` (inspectable in devtools)

### Files Created
- `lavandula/dashboard/pipeline/templates/pipeline/extraction_qa.html`

---

## Phase 4: CSS Styling

**Goal:** Visual polish for the QA viewer layout and interaction states.

### Steps

1. Create `lavandula/dashboard/pipeline/static/pipeline/css/extraction-qa.css`
2. Layout:
   - Flexbox container: left panel 60%, right panel 40%, full viewport height minus toolbar
   - Right panel: `overflow-y: auto` for independent scroll
   - PDF container: `overflow: auto`, contains pdf.js rendered pages
3. Metric/story rows:
   - Default: white background, subtle bottom border
   - Hover: light gray background, cursor pointer
   - Locked (active): blue left border (3px), light blue background (`#e8f4fd`)
   - Not-located: orange dot indicator (::before pseudo-element)
4. Locate icon: muted gray, brightens on row hover
5. Geo badge: small pill (LOCAL=green, STATE=blue, NATIONAL=purple, GLOBAL=red)
6. Theme tags: small rounded pills, muted colors
7. Toolbar: fixed top, light background, standard dashboard styling
8. PDF.js text layer highlight override: `.highlight { background-color: rgba(255, 230, 0, 0.4); }`

### Acceptance Criteria
- Layout is visually clean and proportioned
- Hover/active/not-located states are visually distinct
- Locate icon is visible but not dominant
- PDF highlight is clearly visible yellow

### Files Created
- `lavandula/dashboard/pipeline/static/pipeline/css/extraction-qa.css`

---

## Phase 5: JavaScript — PDF.js Initialization & Find

**Goal:** Initialize PDF.js viewer and implement the snippet search/highlight functionality.

### Steps

1. Create `lavandula/dashboard/pipeline/static/pipeline/js/extraction-qa.js`
2. PDF initialization:
   - Load PDF from presigned URL via `pdfjsLib.getDocument(url)`
   - Initialize `PDFViewer` with `EventBus`, `PDFLinkService`, `PDFFindController`
   - Set `textLayerMode: 2` (enabled)
   - Do NOT set `enableScripting: true`
   - Render into `#pdf-container`
3. Snippet search function:
   - `extractSearchPhrase(snippet)` — returns best 40-60 char substring (prefer numeric content), max 80 chars
   - `highlightSnippet(snippet)` — executes find command, listens for `updatefindmatchescount` event
   - Fallback chain: exact substring → shortened → number-only → give up
   - Returns a Promise that resolves with `{ found: boolean }`
4. Clear highlight function:
   - Resets find controller (empty query)
5. Error handling:
   - PDF load failure: show "PDF not available" overlay in left panel
   - Expired URL detection: catch network error after 15+ min, show reload prompt

### Acceptance Criteria
- PDF loads and renders with text layer
- `highlightSnippet("some text from the report")` scrolls to and highlights the text
- Fallback chain works: when exact match fails, shorter/number attempts fire
- `clearHighlight()` removes all highlights
- PDF load failure shows error message

### Files Created
- `lavandula/dashboard/pipeline/static/pipeline/js/extraction-qa.js`

---

## Phase 6: JavaScript — Hover & Click Interaction

**Goal:** Wire up hover-to-highlight and click-to-lock on metric/story rows.

### Steps

1. Parse extraction data from `json_script` elements on page load
2. Attach event listeners to all metric/story rows:
   - `mouseenter` → if no locked row, call `highlightSnippet(snippet)`
   - `mouseleave` → if no locked row, call `clearHighlight()`
   - `click` → toggle lock state:
     - If clicking already-locked row: unlock, clear highlight
     - If clicking different row: lock new row, highlight new snippet
     - If clicking with no lock: lock this row
3. Visual state management:
   - Add/remove `.qa-row-active` class on locked row
   - Add `.qa-row-not-found` class if highlight returns `found: false`
4. Debounce hover (50ms) to avoid rapid-fire searches when mouse moves across rows quickly
5. Show brief loading state on row while find is in progress (disable hover for that row until result)

### Acceptance Criteria
- Hover over row → PDF highlights within 500ms
- Mouse out → highlight clears (unless locked)
- Click row → lock (blue border, persistent highlight)
- Click same row again → unlock
- Click different row → transfer lock
- Not-found rows get orange indicator
- Rapid mouse movement doesn't flood find requests (debounce)

### Files Modified
- `lavandula/dashboard/pipeline/static/pipeline/js/extraction-qa.js`

---

## Phase 7: Unit & Integration Tests

**Goal:** Test the Django view logic (no browser tests for JS).

### Steps

1. Create test file `lavandula/dashboard/pipeline/tests/test_extraction_qa.py`
2. Test cases:
   - `test_qa_view_authenticated` — 200 response with correct context
   - `test_qa_view_unauthenticated` — redirect to login
   - `test_qa_view_with_run_id` — respects `?run_id=X` parameter
   - `test_qa_view_default_latest_run` — uses latest run when no run_id
   - `test_qa_view_no_extractions` — empty metrics/stories in context
   - `test_qa_view_metrics_only` — metrics present, stories empty
   - `test_qa_view_stories_only` — stories present, metrics empty
   - `test_qa_view_nonexistent_doc` — 404
   - `test_qa_view_cache_control` — `Cache-Control: no-store` header present
   - `test_org_qa_redirect` — redirects to first extracted doc
   - `test_org_qa_redirect_no_docs` — 404 for org with no extractions
   - `test_prev_next_navigation` — context contains correct prev/next docs
   - `test_prev_next_single_doc` — no prev/next when org has one doc
3. Test fixtures: create test extraction run, metrics, and stories in test setup

### Acceptance Criteria
- All unit tests pass
- Coverage: view logic, query logic, edge cases
- Tests use Django test client with mocked S3 (no real AWS calls)

### Files Created
- `lavandula/dashboard/pipeline/tests/test_extraction_qa.py`

---

## Phase 8: Manual Validation & Polish

**Goal:** End-to-end manual testing with real data, fix edge cases.

### Steps

1. Run the QA viewer against a document from the P20 extraction run
2. Verify:
   - PDF loads and renders all pages
   - Metrics list matches database content
   - Hover on 5+ metrics → each highlights correctly in PDF
   - Click-to-lock works, clicking another row transfers
   - At least one "not located" case handled gracefully
   - Run dropdown switches data
   - Prev/Next navigates correctly
3. Test with edge cases:
   - Document with 50+ metrics (scrolling right panel)
   - Large PDF (50+ pages) — performance acceptable
   - Document with no text layer (scanned PDF) — graceful degradation
4. Fix any issues found
5. Add entry point links:
   - From org detail page (if extractions exist for a doc, show "QA" link)
   - From LLM extraction dashboard (link to QA view per doc)

### Acceptance Criteria
- All spec acceptance criteria verified manually
- No console errors during normal operation
- Performance acceptable on large documents (< 2s to first interaction)
- Entry point links work from org detail and extraction dashboard

### Files Modified
- `lavandula/dashboard/pipeline/templates/pipeline/org_detail.html` (add QA link)
- `lavandula/dashboard/pipeline/templates/pipeline/llm_extract.html` (add QA link)

---

## Implementation Notes

### Traps to Avoid

1. **Don't use PDF.js pre-built viewer app** — it brings a full UI we don't need and is hard to control programmatically. Use viewer components only.
2. **Don't put snippets in HTML attributes** — use `json_script` and read from JS. Attribute escaping is fragile for long text with quotes.
3. **Don't search for full snippets** — they can be 200+ chars and contain line breaks. Always substring to 40-80 chars.
4. **Don't block on find results** — PDF.js find is async. Use the `updatefindmatchescount` event, don't poll.
5. **Don't virtualize the right panel** — 50-100 DOM nodes is nothing. Virtualization adds complexity for no gain.
6. **Don't forget `enableScripting: false`** — PDF.js can execute PDF JavaScript if enabled. Security requirement.

### Key Files Summary

| File | Purpose |
|------|---------|
| `static/vendor/pdfjs/` | Vendored PDF.js library |
| `urls.py` | 2 new URL patterns |
| `views.py` | `ExtractionQAView`, `OrgExtractionQARedirectView` |
| `templates/pipeline/extraction_qa.html` | Main template |
| `static/pipeline/css/extraction-qa.css` | Styles |
| `static/pipeline/js/extraction-qa.js` | PDF.js init + interaction logic |
| `tests/test_extraction_qa.py` | Unit + integration tests |

### Estimated Effort

- Phase 1 (PDF.js setup): 30 min
- Phase 2 (Django view): 45 min
- Phase 3 (Template): 45 min
- Phase 4 (CSS): 30 min
- Phase 5 (JS — PDF.js): 1.5 hours
- Phase 6 (JS — interaction): 1 hour
- Phase 7 (Tests): 45 min
- Phase 8 (Validation): 1 hour

**Total: ~6.5 hours**
