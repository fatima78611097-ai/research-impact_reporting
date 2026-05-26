# Spec 0052: Extraction QA Viewer (PDF + Metrics/Stories)

**Status:** Draft
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

**Text source for search:** `lava_parse.sections` (Docling-parsed full text by section)
- Used for text-layer search when PDF.js text layer is insufficient
- Fallback: pdftotext extraction

### PDF Rendering

Spec 0044 uses browser-native `<iframe>` PDF rendering. This viewer requires **PDF.js** instead because:
- Programmatic control of scroll position (jump to a page/location)
- Text layer access for search and highlight
- Custom highlight overlay rendering

PDF.js is Mozilla's open-source PDF renderer — renders PDFs to canvas with an invisible text layer for selection and search. The text layer enables `findController` for programmatic text search and highlight.

### Highlight Mechanism

PDF.js exposes a text layer (`<div class="textLayer">`) containing `<span>` elements for each text run. To highlight a `source_snippet`:

1. Use PDF.js `findController.executeCommand('find', { query: snippet })` to search for the text
2. PDF.js automatically scrolls to and highlights matches
3. Custom CSS styles the highlight (yellow background, smooth scroll)

If the snippet is too long for exact match (PDF.js find works best on short phrases), extract a distinctive 40-60 character substring from the snippet as the search query.

**Edge cases:**
- Snippet spans multiple pages → highlight on first occurrence
- Snippet not found (OCR differences, formatting) → show "Source not located" indicator on the metric row, no scroll
- Multiple occurrences of same text → highlight first occurrence (usually correct for reports)

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

### JavaScript Architecture

```javascript
// extraction-qa.js
class ExtractionQAViewer {
    constructor(pdfUrl, containerEl) {
        this.pdfViewer = null;
        this.lockedRow = null;
        this.init(pdfUrl, containerEl);
    }

    async init(pdfUrl, containerEl) {
        // Initialize PDF.js viewer
        const loadingTask = pdfjsLib.getDocument(pdfUrl);
        const pdf = await loadingTask.promise;
        // ... render pages into container with text layers
    }

    highlightSnippet(snippet, lock = false) {
        const query = this.extractSearchPhrase(snippet);
        this.pdfViewer.findController.executeCommand('findagain', {
            query: query,
            highlightAll: true,
            findPrevious: false,
        });
        if (lock) this.lockedRow = /* current row */;
    }

    extractSearchPhrase(snippet) {
        // Pick a distinctive substring (40-60 chars)
        // Prefer numeric content (more unique in reports)
        // Avoid line-break boundaries
        if (snippet.length <= 60) return snippet;
        // Find a substring with numbers (more unique)
        const numMatch = snippet.match(/\d[\d,.]* [a-zA-Z ]{10,40}/);
        if (numMatch) return numMatch[0];
        return snippet.substring(0, 60);
    }

    clearHighlight() {
        if (!this.lockedRow) {
            this.pdfViewer.findController.executeCommand('findagain', {
                query: '',
                highlightAll: false,
            });
        }
    }
}
```

### PDF.js Integration

**Library source:** Use PDF.js from CDN (`mozilla.github.io/pdf.js/`) or vendor a copy.

**Viewer setup:** Use the "viewer components" approach (not the full viewer application) to embed just the rendering and find functionality without the full Firefox PDF viewer UI chrome.

Required PDF.js components:
- `pdf.js` (core library)
- `pdf.worker.js` (web worker for parsing)
- `pdf_viewer.js` (viewer component with text layers)
- `pdf_viewer.css` (text layer styling)

**CORS:** Presigned S3 URLs are same-origin-neutral (the URL itself authenticates). PDF.js can fetch them directly. No CORS headers needed on S3 bucket.

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
20. Prev/Next buttons navigate between org's documents that have extraction data
21. Run dropdown switches between extraction runs for the same document
22. Back button returns to org detail page

### Error Handling
23. Missing PDF (S3 404) shows clear error message, extraction panel still visible
24. Document with no extractions shows "No extraction data for this document" message
25. Expired presigned URL shows reload prompt

## Security Considerations

- Presigned URLs expose the document to anyone with the link for 15 minutes — acceptable for internal QA tool behind auth
- No user-supplied content rendered as HTML (all metric/story text is escaped)
- PDF.js sandboxes PDF rendering (no JS execution from malicious PDFs)
- `source_snippet` values are stored server-side and never come from user input in this context

## Dependencies

- **Spec 0044** — provides the base PDF viewer pattern, presigned URL generation, org document listing. The QA viewer extends this with PDF.js (replacing `<iframe>`) and the extraction panel.
- **Spec 0051** — provides the extraction data (`llm_metrics`, `llm_stories` tables with `source_snippet` column)
- **PDF.js** — Mozilla's PDF rendering library (MIT license)

## Open Questions

1. **Spec 0044 not yet implemented** — should the QA viewer depend on 0044 being done first, or should it be self-contained (duplicate the presigned URL + view patterns)? Recommendation: make it self-contained since the viewer logic is sufficiently different (PDF.js vs iframe).
2. **Text search accuracy** — PDF.js text extraction may not exactly match the LLM's `source_snippet` (hyphenation, ligatures, Unicode normalization). May need fuzzy matching fallback.
3. **Split-path documents** — documents that went through the dual-path extraction (metrics and stories from separate API calls) may have overlapping source_snippets. Not a problem for display but worth noting.
