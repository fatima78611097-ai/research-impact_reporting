# Spec 0052: Extraction QA Viewer (PDF + Metrics/Stories)

**Status:** Approved
**Author:** Architect
**Created:** 2026-05-26
**Dependencies:** 0044 (Org Search, Document Listing & PDF Viewer), 0051 (LLM Impact Extraction)

## Problem Statement

The LLM extraction pipeline (Spec 0051) produces structured metrics and stories from nonprofit reports, but there is no way to visually verify extraction quality without manually cross-referencing the database against the source PDF. At 3,335+ documents in P20 alone, manual spot-checking requires a dedicated tool.

Operators need to see the source PDF and its extracted data together, with interactive linking between each extracted item and its location in the document. This enables:
- Rapid quality audit (are metrics real? are stories fabricated?)
- Prompt tuning feedback (which metrics did the model miss? what did it hallucinate?)
- Competitive validation (compare our extractions against known-good data)

## Goals

1. **Side-by-side layout** — PDF rendered on the left, extracted metrics and stories listed on the right
2. **Hover-to-highlight** — mouse over a metric/story on the right, PDF scrolls to and highlights the `source_snippet` in the document
3. **Click-to-lock** — click a metric/story to lock the highlight so the user can read surrounding context without holding the mouse
4. **Locate affordance** — each metric/story row has a visible locate icon making the interactive behavior discoverable
5. **Navigation** — browse by org (next/prev document), filter by extraction run, jump to specific EIN

## Non-Goals

- Editing or correcting extractions from this view (read-only QA)
- Annotation or manual tagging of documents
- Comparison view between different extraction runs (future work)
- PDF text editing or redaction
- Mobile-responsive layout (desktop QA tool)

## Technical Context

### Data Sources

**PDF source:** `s3://lavandula-nonprofit-collaterals/pdfs/{sha256}.pdf`
- Presigned URL generated server-side (15-min expiry, matching Spec 0044)

**Extraction data:** `lava_vocab.llm_metrics` and `lava_vocab.llm_stories`
- Keyed by `(run_id, content_sha256)`
- Both tables have `source_snippet` column — the exact text from the document where the item was found
- Metrics have: `metric_text`, `metric_type`, `metric_value`, `unit`, `geo_impact`, `source_snippet`
- Stories have: `story_title`, `story_summary`, `people_mentioned`, `program`, `themes`, `source_snippet`

**Text source (informational):** The extraction pipeline (Spec 0051) uses `lava_parse.sections` and pdftotext as text sources for the LLM. The QA viewer does NOT use these for search — it searches exclusively via PDF.js's text layer. For scanned/degraded PDFs where the text layer is empty, the viewer degrades gracefully (see AC 29).

### PDF Rendering

Spec 0044 uses browser-native `<iframe>` PDF rendering. This viewer requires **PDF.js** instead because:
- Programmatic control of scroll position (jump to a page/location)
- Text layer access for search and highlight
- Custom highlight overlay rendering

**PDF.js version:** Use PDF.js **4.x** (latest stable). Vendor the library into `static/vendor/pdfjs/` rather than using CDN — avoids external dependency for an internal tool. Required files:
- `pdf.min.mjs` (core library, ES module)
- `pdf.worker.min.mjs` (web worker)
- `pdf_viewer.mjs` (viewer component)
- `pdf_viewer.css` (text layer styles)

**Embedding approach:** Use the PDF.js **viewer components** (not the pre-built viewer application). Initialize `PDFViewer` with an `EventBus`, `PDFLinkService`, and `PDFFindController` inside a `<div>` container. This gives programmatic control without the full Firefox viewer UI chrome.

```javascript
const eventBus = new pdfjsViewer.EventBus();
const linkService = new pdfjsViewer.PDFLinkService({ eventBus });
const findController = new pdfjsViewer.PDFFindController({ eventBus, linkService });
const viewer = new pdfjsViewer.PDFViewer({
    container: document.getElementById('pdf-container'),
    eventBus,
    linkService,
    findController,
    textLayerMode: 2,  // ENABLE (renders text layer for search)
});
linkService.setViewer(viewer);
```

**Page lifecycle:** PDF.js renders pages lazily — only visible pages get canvas + text layer. When `findController` locates text on an unrendered page, the viewer automatically scrolls to and renders that page before highlighting. No manual page management needed.

### Highlight Mechanism — Snippet Matching

The `source_snippet` from the extraction may not exactly match PDF.js's text layer output due to: whitespace normalization, hyphenation, ligatures (fi→fi), Unicode equivalence, or OCR artifacts. The matching strategy uses a **fallback chain:**

**Max query length:** Search queries are capped at 80 characters to avoid PDF.js performance issues on pathological inputs. Snippets longer than 80 chars are always substrung.

**Step 1 — Exact substring match:** Extract a distinctive 40-60 character substring from `source_snippet` (prefer substrings containing numbers, which are more unique in reports). Pass to `findController.executeCommand('find', { query, highlightAll: true, caseSensitive: false })`.

**Step 2 — Shortened query:** If step 1 finds 0 matches (reported via `updatefindmatchescount` event), retry with a shorter 20-30 character substring (the most numeric-dense portion).

**Step 3 — Number-only fallback:** Extract just the key number from `source_snippet` (e.g., "670,017" from "670,017 meals prepared"). Search for the number. This almost always matches but may highlight the wrong occurrence if the number appears multiple times.

**Step 4 — Give up:** If all attempts find 0 matches, mark the row with an orange dot indicator and tooltip "Source text not located in PDF." Do not scroll the PDF.

**Success criteria:** A match is "found" when `findController` reports ≥1 match via the `updatefindmatchescount` event. The viewer scrolls to and highlights the first match.

**Multiple occurrences:** If the same text appears multiple times in the document, the first occurrence is highlighted. This is acceptable because reports rarely repeat exact metric phrasing.

**Snippet spans pages:** `findController` handles cross-page matches natively — it highlights the match on whichever page(s) it spans.

## Technical Implementation

### URL Structure

```
/dashboard/reports/<sha>/qa/                    → QA viewer for a specific document
/dashboard/reports/<sha>/qa/?run_id=10          → Specific extraction run
/dashboard/orgs/<ein>/qa/                       → First extracted document for this org
```

### View

```python
class ExtractionQAView(LoginRequiredMixin, DetailView):
    model = Report
    template_name = "pipeline/extraction_qa.html"
    slug_field = "content_sha256"
    slug_url_kwarg = "sha"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sha = self.object.content_sha256
        ein = self.object.source_org_ein
        run_id = self.request.GET.get("run_id")

        # PDF presigned URL
        s3 = boto3.client("s3")
        ctx["pdf_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_COLLATERAL_BUCKET, "Key": f"pdfs/{sha}.pdf"},
            ExpiresIn=900,
        )

        # Extraction data
        engine = make_app_engine()
        with engine.connect() as conn:
            # Get available runs for this doc
            runs = conn.execute(text("""
                SELECT DISTINCT r.id, r.run_tag, r.created_at
                FROM lava_vocab.extraction_runs r
                JOIN lava_vocab.llm_metrics m ON m.run_id = r.id
                WHERE m.content_sha256 = :sha
                ORDER BY r.id DESC
            """), {"sha": sha}).fetchall()
            ctx["available_runs"] = runs

            # Use specified run or latest
            if not run_id and runs:
                run_id = runs[0][0]

            if run_id:
                metrics = conn.execute(text("""
                    SELECT metric_text, metric_type, metric_value, unit,
                           geo_impact, source_snippet
                    FROM lava_vocab.llm_metrics
                    WHERE run_id = :run_id AND content_sha256 = :sha
                    ORDER BY metric_value DESC NULLS LAST
                """), {"run_id": run_id, "sha": sha}).fetchall()

                stories = conn.execute(text("""
                    SELECT story_title, story_summary, people_mentioned,
                           program, themes, source_snippet
                    FROM lava_vocab.llm_stories
                    WHERE run_id = :run_id AND content_sha256 = :sha
                    ORDER BY story_title
                """), {"run_id": run_id, "sha": sha}).fetchall()

                ctx["metrics"] = metrics
                ctx["stories"] = stories
                ctx["selected_run_id"] = run_id

        # Org context + nav
        try:
            ctx["org"] = NonprofitSeed.objects.get(ein=ein)
        except NonprofitSeed.DoesNotExist:
            ctx["org"] = None

        # Next/prev documents for this org that have extractions
        ctx["org_docs"] = self._get_org_docs(engine, ein, run_id)
        return ctx
```

### Template Layout

```
┌───────────────────────────────────────────────────────────────────────────┐
│ [← Org] [← Prev Doc] [Next Doc →]  |  Run: [dropdown]  |  Org: Name     │
├─────────────────────────────────┬─────────────────────────────────────────┤
│                                 │  METRICS (24)                           │
│                                 │  ┌──────────────────────────────────┐   │
│                                 │  │ ⊕ 670,017 meals prepared        │   │
│   PDF Viewer                    │  │   meals · LOCAL                  │   │
│   (PDF.js)                      │  ├──────────────────────────────────┤   │
│                                 │  │ ⊕ 329,349 nights of shelter     │   │
│   - Full document               │  │   shelter nights · STATE         │   │
│   - Text layer enabled          │  ├──────────────────────────────────┤   │
│   - Highlights shown            │  │ ⊕ $34,217,302 rental assistance │   │
│     in yellow overlay           │  │   USD · STATE                    │   │
│                                 │  └──────────────────────────────────┘   │
│                                 │                                         │
│                                 │  STORIES (5)                            │
│                                 │  ┌──────────────────────────────────┐   │
│                                 │  │ ⊕ Maria's Journey to Housing    │   │
│                                 │  │   "After 3 months in shelter..." │   │
│                                 │  │   Themes: housing, resilience    │   │
│                                 │  └──────────────────────────────────┘   │
└─────────────────────────────────┴─────────────────────────────────────────┘
```

- Left panel: 60% width, PDF.js viewer with full page rendering
- Right panel: 40% width, scrollable list of metrics and stories
- ⊕ icon = locate button (click to lock highlight, hover to preview)

### Interaction Design

**Hover behavior:**
1. User hovers over a metric/story row
2. JavaScript extracts the `source_snippet` from a `data-snippet` attribute
3. Selects a distinctive 40-60 char substring from the snippet
4. Calls `pdfViewer.findController.executeCommand('findagain', { query, highlightAll: true })`
5. PDF scrolls to the match, text highlighted in yellow
6. On mouseout, clear the search highlight

**Click-to-lock behavior:**
1. User clicks the locate icon (⊕) or the row
2. Same search/scroll as hover, but highlight persists
3. Row gets an "active" visual state (blue left border, light blue background)
4. Clicking another row or clicking again unlocks
5. Locked state survives scrolling the right panel

**Not-found handling:**
- If `findController` reports 0 matches, show a subtle indicator on the row (orange dot or "not located" tooltip)
- Don't scroll the PDF — leave it where it is
- Log not-found events to console for debugging

### Interaction State Model

The viewer manages three pieces of UI state:

1. **Hovered row** — which metric/story row the mouse is over (null if none)
2. **Locked row** — which metric/story is "pinned" with a persistent highlight (null if none)
3. **Selected run** — which extraction run is active

**State transitions on run change:**
- Locked row resets to null (highlight cleared)
- Hovered row resets to null
- Metrics and stories lists replace entirely
- "Not located" indicators reset (re-evaluated lazily on next hover/click)
- PDF stays on current page (no scroll)

**State transitions on document navigation (next/prev):**
- Full page reload (new URL) — all state resets naturally

### CORS

Presigned S3 URLs are same-origin-neutral (the URL authenticates via query string). PDF.js can fetch them directly without CORS headers on the S3 bucket.

### Performance Considerations

- **Large PDFs (50+ pages):** PDF.js renders pages on-demand. Only visible pages are rendered. Scrolling to a highlight triggers rendering of that page first.
- **Many metrics (50+):** Right panel is a virtual scroll container. All items render (not virtualized) since 50-100 DOM nodes is trivial.
- **Slow find:** PDF.js `findController` searches the entire document text. For large documents this can take 100-500ms. Show a brief loading indicator on the row during search.
- **Presigned URL expiry:** 15-minute URL. If the page is left open longer, show a "Session expired, reload page" message rather than a broken viewer.

### Static Assets

- `extraction-qa.js` — main viewer logic
- `extraction-qa.css` — layout, hover states, highlight styles
- PDF.js library files (vendored or CDN)

### Entry Points

From the org detail page (Spec 0044's document listing), each document that has extraction data shows a "QA" button/link alongside the existing "View" link.

From the LLM extraction dashboard page (Spec 0051), extraction run results can link to the QA viewer for any document in the run.

## Acceptance Criteria

### Layout
1. Page renders with PDF on left (~60%), extraction panel on right (~40%)
2. PDF renders via PDF.js with text layer enabled
3. Right panel shows metrics count, stories count, and the extraction run tag
4. Right panel is independently scrollable from the PDF

### Metrics Display
5. Each metric shows: metric_text (primary), metric_value + unit (secondary), geo_impact badge
6. Metrics are ordered by metric_value descending (largest impact first)
7. Each metric row has a visible locate icon (⊕ or similar)

### Stories Display
8. Each story shows: story_title (primary), first sentence of story_summary (secondary), theme tags
9. Stories are ordered alphabetically by title
10. Each story row has a visible locate icon

### Hover-to-Highlight
11. Hovering a metric/story row triggers PDF scroll and text highlight within 500ms
12. Highlight uses yellow background on the text layer
13. Moving mouse away clears the highlight (unless locked)
14. If source_snippet not found in PDF text, row shows "not located" indicator

### Click-to-Lock
15. Clicking locate icon or row locks the highlight (persists after mouseout)
16. Locked row has distinct visual state (blue border, light background)
17. Clicking another row transfers the lock
18. Clicking the same row again unlocks (clears highlight)

### Navigation
19. Toolbar shows org name and current document position (e.g., "Doc 3 of 7")
20. Prev/Next buttons navigate between org's documents that have extraction data, ordered by `report_year DESC, content_sha256 ASC`
21. When org has only one extracted document, Prev/Next buttons are hidden (not disabled)
22. Run dropdown switches between extraction runs for the same document; changing run resets locked highlight and reloads extraction panel
23. Back button returns to org detail page

### Error Handling
24. Missing PDF (S3 404) shows "PDF not available" message in left panel; extraction panel still visible and fully functional
25. Document with no extractions shows "No extraction data for this document" in right panel; PDF viewer still renders normally
26. Expired presigned URL (PDF fails to load after page has been open >15 min) shows "Session expired — reload page" overlay on the PDF panel
27. Metrics-only extraction (stories list empty) shows metrics normally, stories section shows "No stories extracted"
28. Stories-only extraction (metrics list empty) shows stories normally, metrics section shows "No metrics extracted"
29. Degraded text layer (scanned PDF with no text layer) — hover/click attempts all reach step 4 (give up); all rows show "not located" indicators. PDF still renders visually. No special pre-detection needed.

## Security Considerations

**Authorization model:** This is a single-operator system (one authenticated user). All authenticated users have access to all reports and extraction data — there is no per-org, per-role, or per-tenant access control. `LoginRequiredMixin` is the only authorization gate. This is intentional and matches all other dashboard pages.

- **Authentication:** View requires `LoginRequiredMixin`. Unauthenticated requests redirect to login.
- **Presigned URL risk acknowledgment:** Presigned S3 URLs are bearer tokens — anyone who obtains the URL can access the PDF for 15 minutes regardless of session. This is an accepted risk for a single-operator internal tool. Mitigations: short expiry (15 min), URLs not persisted in logs or analytics, page served with `Cache-Control: no-store` header.
- **XSS prevention:** All extraction text (metric_text, story_title, source_snippet, etc.) is rendered via Django template auto-escaping (`{{ value }}`). For JavaScript access, extraction data is serialized via Django's `json_script` template tag (produces `<script type="application/json">` with safe escaping). No `|safe` filter, no `innerHTML`, no attribute interpolation of raw text. The `data-snippet` approach in the JavaScript section uses the pre-escaped JSON data from `json_script`, not direct attribute injection.
- **PDF sandbox:** PDF.js renders to `<canvas>` and does not execute JavaScript embedded in PDF documents. The implementation MUST NOT enable `enableScripting` in PDFViewer options. PDF annotations, embedded files, and form actions are not rendered.
- **No client-side persistence:** No extraction content or PDF URLs stored in localStorage, sessionStorage, or sent to analytics.
- **Console logging:** "Not found" debug events log the metric row index, not raw snippet text.

## Testing Requirements

### Unit Tests (Python)
- View returns correct metrics/stories for a given (sha, run_id)
- View defaults to latest run when no run_id specified
- View handles document with no extractions (empty metrics/stories lists)
- View handles document with metrics but no stories, and vice versa
- Prev/Next navigation query returns correct ordering and filters to docs with extractions
- Run dropdown query returns distinct runs for the document

### Integration Tests (Python)
- Authenticated user can load QA viewer page (200 response)
- Unauthenticated user is redirected to login
- Page context includes pdf_url, metrics, stories, available_runs
- Run switching via query parameter loads correct extraction data

### Browser/Manual Tests
- PDF renders in left panel with text layer visible
- Hovering a metric row scrolls PDF and highlights text within 500ms
- Mouse-out clears highlight
- Clicking locate icon locks highlight; clicking again unlocks
- Clicking a different row transfers lock
- "Not located" indicator appears for unmatched snippets
- Run dropdown switches data without page reload
- Expired presigned URL shows reload message

## Dependencies

- **Spec 0044** — provides the base PDF viewer pattern, presigned URL generation, org document listing. The QA viewer extends this with PDF.js (replacing `<iframe>`) and the extraction panel.
- **Spec 0051** — provides the extraction data (`llm_metrics`, `llm_stories` tables with `source_snippet` column)
- **PDF.js** — Mozilla's PDF rendering library (MIT license)

## Design Decisions

1. **Self-contained (no dependency on 0044 implementation)** — The QA viewer is self-contained. It uses PDF.js (not iframe) and its own view/template. If 0044 is implemented later, the QA viewer can link to/from it, but doesn't share rendering infrastructure.
2. **Text matching uses fallback chain** — Exact match → shortened query → number-only → give up. No fuzzy/approximate string matching library required. The fallback chain handles 95%+ of cases based on the nature of metric snippets (they contain distinctive numbers).
3. **Split-path extraction is transparent** — Whether metrics and stories came from one call or two is invisible to the viewer. Both are stored identically in the database.
