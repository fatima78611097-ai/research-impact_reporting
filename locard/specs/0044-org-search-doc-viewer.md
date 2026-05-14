# Spec 0044: Org Search, Document Listing & PDF Viewer

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-14
**Dependencies:** 0019 (Pipeline Dashboard)

## Problem Statement

The dashboard's org list page can only filter by EIN, state, resolver status, and resolver method — there is no way to search by organization name. The org detail page shows 990 filings and pipeline provenance but does not list the corpus documents associated with the org. When a user wants to view a document, the existing report detail page shows metadata and first-page text but does not render the PDF. Users must download the file to read it.

Before moving to Layer 2 (vocabulary extraction and analysis), operators need to browse the corpus — see what documents each org has, what they're classified as, and read them in-browser without downloading.

## Goals

1. **Org name search** — users can search for organizations by name (partial match) on the org list page
2. **Org document listing** — the org detail page shows all corpus documents for that org with human-readable display names, material type, year, page count, and links to the viewer
3. **PDF viewer** — a standalone page that renders the PDF in-browser with toolbar controls (download, print, page navigation, zoom) and a metadata sidebar

## Non-Goals

- Full-text search across PDF content
- Document annotation or editing
- Batch document operations
- Document upload or manual classification
- Thumbnail generation

## Technical Context

### Current State

**Org list** (`OrgListView`, views.py:1075-1103):
- Filters: EIN (exact), state, resolver_status, resolver_method
- Template: `orgs.html`
- Model: `NonprofitSeed` (unmanaged, db_table="nonprofits_seed")
- NonprofitSeed has a `name` TextField — searchable but not currently exposed

**Org detail** (`OrgDetailView`, views.py:1106-1171):
- Shows: org info, 990 filings, officers, contractors, schedule J, provenance
- Template: `org_detail.html`
- No corpus document listing

**Report detail** (`ReportDetailView`, views.py:1485-1490):
- Shows: metadata + first_page_text
- Download: presigned S3 URL (5-min expiry)
- No in-browser PDF rendering

**Report model** (`Report`, unmanaged, db_table="corpus"):
- `content_sha256` (PK), `source_org_ein`, `source_url_redacted`
- `material_type`, `material_group`, `classification`, `classification_confidence`
- `report_year`, `page_count`, `file_size_bytes`, `archived_at`
- `first_page_text`

**S3 layout**: `s3://lavandula-nonprofit-collaterals/pdfs/{sha256}.pdf`

### Document Display Name

Documents currently have no title field. The human-readable name should be derived from available metadata:

```
{Org Name} — {Material Type Label} ({Year})
e.g., "American Red Cross — Annual Report (2023)"
```

When year is unknown: `"American Red Cross — Annual Report"`
When material_type is unknown: `"American Red Cross — Document (2023)"`

The `source_url_redacted` basename can serve as a secondary identifier (e.g., `2023-Annual-Report.pdf`).

## Technical Implementation

### Part 1: Org Name Search

Add a `filter_name` parameter to `OrgListView.get_queryset()`:

```python
name = self.request.GET.get("name", "").strip()
if name:
    qs = qs.filter(name__icontains=name)
```

Add a name search input to `orgs.html` alongside the existing EIN filter. The name search should be the first/most prominent filter since it's the most common lookup pattern.

**Database consideration:** `nonprofits_seed.name` has no index. For ~112K rows, `icontains` (SQL `LIKE '%term%'`) is fast enough without an index. If performance becomes an issue, add a GIN trigram index later.

### Part 2: Org Document Listing

Add corpus documents to `OrgDetailView.get_context_data()`:

```python
documents = Report.objects.filter(
    source_org_ein=self.object.ein
).order_by("-report_year", "material_type")
ctx["documents"] = documents
ctx["document_count"] = documents.count()
```

Add a "Documents" section to `org_detail.html`:
- Table with columns: Name (derived), Material Type, Year, Pages, Size, Archived
- Each row links to the document viewer page
- Show document count in section header
- If no documents: "No documents in corpus."

### Part 3: PDF Viewer Page

**URL:** `/dashboard/reports/<sha>/view/` → `DocumentViewerView` (name: `report_view`)

**View:** Generate a presigned S3 URL for the PDF and pass it to the template along with the report metadata.

```python
class DocumentViewerView(LoginRequiredMixin, DetailView):
    model = Report
    template_name = "pipeline/document_viewer.html"
    context_object_name = "report"
    slug_field = "content_sha256"
    slug_url_kwarg = "sha"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        import boto3
        from django.conf import settings
        s3 = boto3.client("s3")
        key = f"pdfs/{self.object.content_sha256}.pdf"
        ctx["pdf_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_COLLATERAL_BUCKET, "Key": key},
            ExpiresIn=900,
        )
        # Org name for display
        try:
            ctx["org"] = NonprofitSeed.objects.get(ein=self.object.source_org_ein)
        except NonprofitSeed.DoesNotExist:
            ctx["org"] = None
        return ctx
```

**Template layout:**

```
┌─────────────────────────────────────────────────────┐
│ Toolbar: [← Back] [Download] [Print] [Zoom -/+]    │
├────────────────────────────────┬────────────────────┤
│                                │  Metadata Sidebar  │
│                                │  ─────────────     │
│   PDF Rendered via             │  Org: Name (EIN)   │
│   <iframe> or PDF.js           │  Type: Annual Rpt  │
│                                │  Year: 2023        │
│                                │  Pages: 24         │
│                                │  Size: 2.4 MB      │
│                                │  Source URL         │
│                                │  Confidence: 0.95  │
│                                │  Archived: date    │
│                                │  SHA: abc123...    │
│                                │                    │
│                                │  [View Org →]      │
│                                │  [Next Doc →]      │
│                                │  [← Prev Doc]      │
└────────────────────────────────┴────────────────────┘
```

**PDF rendering approach:** Use a simple `<iframe>` pointing to the presigned S3 URL. Modern browsers (Chrome, Firefox, Edge) have built-in PDF viewers with page navigation, zoom, download, and print. This avoids shipping a JavaScript PDF library and works with the S3 presigned URL directly.

The toolbar provides redundant download/print buttons for discoverability, plus navigation to the next/previous document for the same org (for corpus browsing).

**Presigned URL expiry:** 15 minutes (up from the current 5 minutes for downloads) to allow reading time. The URL is embedded in the page at load time — if it expires, the user reloads the page.

**Next/Previous navigation:** Query the corpus for documents belonging to the same org, ordered by `-report_year, material_type`, and provide links to adjacent documents. This enables browsing through an org's full document collection without returning to the org detail page.

**Document search panel:** The sidebar includes a collapsible search/filter section below the metadata. This lets the user search across the full corpus without leaving the viewer:

- Text input: search by org name (icontains)
- Dropdown: filter by material_type
- Dropdown: filter by state (derived from org's state)
- Results appear as a compact list in the sidebar (org name, type, year) — clicking a result navigates to that document in the viewer

This is loaded via HTMX partial (`/dashboard/reports/search/?q=...&material_type=...&state=...`) so it doesn't require a full page reload. The search partial returns up to 25 results, paginated.

The search panel makes the viewer a self-contained browsing tool — operators can jump between documents across orgs without leaving the page.

### Navigation Updates

- **Report detail page** (`report_detail.html`): Add a "View PDF" button alongside the existing "Download PDF" button
- **Reports list page** (`reports.html`): Add a view icon/link per row that goes to the viewer
- **Org detail documents table**: Each row links to the viewer

## Acceptance Criteria

### Part 1: Org Name Search
- AC1: Org list page has a "Name" text input that filters by partial name match (case-insensitive)
- AC2: Name filter composes with existing filters (state, EIN, status, method)
- AC3: Clearing the name field shows all orgs (no filter)
- AC4: Filter values persist across pagination

### Part 2: Org Document Listing
- AC5: Org detail page shows a "Documents (N)" section listing all corpus documents for the org
- AC6: Each document row shows: derived display name, material type badge, year, page count, file size
- AC7: Each document row links to the PDF viewer page
- AC8: Documents are ordered by year (descending), then material type
- AC9: If no documents exist for the org, show "No documents in corpus."

### Part 3: PDF Viewer
- AC10: `/dashboard/reports/<sha>/view/` renders the PDF inline via `<iframe>` with presigned S3 URL
- AC11: Toolbar has Download button (links to existing `report_download` URL)
- AC12: Toolbar has Print button (triggers `window.print()` or iframe print)
- AC13: Metadata sidebar shows: org name + EIN link, material type, year, page count, file size, source URL, confidence, archived date, SHA-256
- AC14: "View Org" link navigates to the org detail page
- AC15: Next/Previous document links navigate to adjacent docs for the same org
- AC16: Back button returns to the referring page (org detail or reports list)
- AC17: Presigned URL has 15-minute expiry
- AC18: Page requires authentication (LoginRequiredMixin)

### Viewer Search Panel
- AC19: Sidebar has a collapsible "Browse Corpus" search section
- AC20: Search panel has text input for org name (partial match)
- AC21: Search panel has material_type dropdown filter
- AC22: Search panel has state dropdown filter
- AC23: Search results load via HTMX without full page reload
- AC24: Results show as compact list (org name, material type, year) — max 25 per page
- AC25: Clicking a search result navigates to that document in the viewer

### Navigation
- AC26: Report detail page has a "View PDF" button linking to the viewer
- AC27: Reports list page has a view link per row linking to the viewer
- AC28: Org detail document listing links to the viewer

## Security Considerations

- All views require authentication (LoginRequiredMixin)
- S3 presigned URLs are time-limited (15 min) and scoped to a single object
- Name search uses Django ORM `icontains` (parameterized query, no SQL injection)
- PDF rendered in `<iframe>` with S3 origin — sandboxed by browser same-origin policy
- No user-supplied content rendered as HTML (XSS-safe)

## Testing Requirements

- Unit tests for name search filter (exact, partial, case-insensitive, combined with other filters)
- Unit tests for document listing context (org with docs, org without docs, ordering)
- Unit tests for viewer view (presigned URL generation, org lookup, next/prev navigation)
- Unit tests for authentication enforcement on all new views
- Integration: verify document listing links to viewer, viewer links back to org

## Traps to Avoid

1. **Don't build a custom PDF renderer** — browser built-in PDF viewers via `<iframe>` are sufficient and avoid JS library maintenance
2. **Don't add a title column to the corpus table** — derive display names from existing metadata (org name + material_type + year)
3. **Don't assume all orgs have documents** — handle the zero-document case gracefully
4. **Don't break the existing report detail page** — the viewer is additive, not a replacement
5. **iframe src must be the full presigned URL** — don't proxy the PDF through Django (memory/bandwidth waste)
