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
- Bucket configured with SSE-S3 encryption at rest, versioning enabled, private ACL (Spec 0007)

### Document Display Name

Documents currently have no title field. The human-readable name should be derived from available metadata:

```
{Org Name} — {Material Type Label} ({Year})
e.g., "American Red Cross — Annual Report (2023)"
```

**Precedence rules (apply in order):**
1. Full: `"{Org Name} — {Material Type Label} ({Year})"`
2. Missing year: `"{Org Name} — {Material Type Label}"`
3. Missing material_type: `"{Org Name} �� Document ({Year})"` or `"{Org Name} — Document"` if year also null
4. Missing org name: `"[EIN] — {Material Type Label} ({Year})"` (fall through same year/type rules)
5. All null (no org, no type, no year): `"Document"` — show `source_url_redacted` basename as subtitle below

These rules apply uniformly in document listing tables, viewer headings, and sidebar titles.

The `source_url_redacted` basename can serve as a secondary identifier (e.g., `2023-Annual-Report.pdf`).

**Material type label mapping:** Use `material_type.replace("_", " ").title()` for display (e.g., `annual_report` → `Annual Report`). This is a presentation-only transform — no lookup table needed.

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
).order_by("-report_year", "material_type", "content_sha256")
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

**Error handling:**
- **Presign-time failure:** If `generate_presigned_url` raises `ClientError` (credentials issue, bucket misconfiguration): catch, set `ctx["pdf_error"] = True`, show error message with link to report detail page.
- **Load-time failure (missing S3 object):** `generate_presigned_url` does NOT verify object existence — it signs the URL regardless. If the PDF was deleted from S3, the browser will receive a 404/403 XML error in the iframe. Handle this with an `iframe.onerror` listener or an `onload` check, and show the "PDF not available" message. Additionally, the "Can't see the PDF?" link below the iframe provides manual fallback.
- If org record is missing (`NonprofitSeed.DoesNotExist`): `ctx["org"] = None`. Sidebar shows EIN only (no org name link). Display name falls back to `"[EIN] — Type (Year)"`.
- Null metadata fields: each sidebar field shows "-" when null. Display name omits null components gracefully (see Document Display Name section).
- Browser cannot render PDF inline (rare): the `<iframe>` fallback is browser-dependent. Below the iframe, show a small "Can't see the PDF?" link to the download URL.

**Back navigation:** The viewer accepts an optional `return_to` query parameter. Navigation links from org detail, reports list, and report detail pass their URL as `return_to`. The Back button uses this value if present; otherwise falls back to the org detail page (if org exists) or the reports list.

**`return_to` validation** (open-redirect prevention):
```python
def _safe_return_url(request):
    """Validate return_to param: must be a relative dashboard path, no scheme."""
    url = request.GET.get("return_to", "")
    if url and url.startswith("/dashboard/") and "://" not in url and not url.startswith("//"):
        return url
    return None
```
The view calls `_safe_return_url()` in `get_context_data()` and passes the result to the template. The template uses it for the Back link, with a fallback to `{% url 'org_detail' report.source_org_ein %}` or `{% url 'report_list' %}`. Absolute URLs, protocol-relative URLs (`//evil.com`), non-dashboard paths, and empty values are all rejected. Query strings within the path are allowed (e.g., `/dashboard/orgs/?state=NY`). Next/prev and search-result links propagate the same validated `return_to` value unchanged.

**Next/Previous navigation:** Query the corpus for documents belonging to the same org, ordered by `-report_year, material_type, content_sha256` (deterministic tie-breaker), and provide links to adjacent documents. This enables browsing through an org's full document collection without returning to the org detail page. Next/prev links preserve the `return_to` parameter.

**Null year ordering:** Documents with `report_year IS NULL` sort last (after all year-having documents). Use `COALESCE(report_year, 0)` or Django's `F('report_year').asc(nulls_last=True)` for deterministic ordering.

**Single-document orgs:** When there is no next or previous document, the corresponding link is hidden (not disabled/greyed — just absent). The template uses `{% if next_doc %}` guards.

**Document search panel:** The sidebar includes a collapsible "Browse Corpus" section below the metadata. This lets the user search across the full corpus without leaving the viewer. This is bundled with the viewer (not a separate spec) because the core use case is comparative browsing — reading one report, then jumping to a similar one in another vertical. Without in-viewer search, the operator must navigate back to the org list, find another org, open their documents, then open the viewer — 4 clicks vs 1.

**URL:** `/dashboard/reports/search/` → `DocumentSearchPartial` (HTMX partial, name: `report_search`)

**Filters:**
- `q` (text): search by org name via `NonprofitSeed.objects.filter(name__icontains=q)`, then `Report.objects.filter(source_org_ein__in=matching_eins)`. Minimum 2 characters required; empty or single-char `q` returns no results (avoids full-table scan). Maximum 100 chars.
- `material_type` (dropdown): filter by `Report.material_type` exact match. Dropdown values sourced from `Report.objects.values_list('material_type', flat=True).distinct().order_by('material_type')` — cached for 5 minutes.
- `state` (dropdown): filter by org state — `Report.objects.filter(source_org_ein__in=NonprofitSeed.objects.filter(state=state).values("ein"))`. Dropdown values: hardcoded US state list (50 + DC), alphabetical.
- `page` (int, default 1): pagination offset

**Join strategy:** Filters that reference org metadata (name, state) use subqueries via `source_org_ein__in=NonprofitSeed.objects.filter(...).values("ein")`. This avoids cross-model joins on unmanaged tables and lets PostgreSQL optimize the subquery.

**Results:** Ordered by `-report_year, material_type, content_sha256`. Each result shows org name (truncated to 30 chars), material type label, and year. Clicking navigates to that document in the viewer (full page load, preserving `return_to`).

**Pagination:** 25 results per page. Show "Load more" link at bottom that fetches page N+1 via HTMX append. No total count displayed (avoids expensive COUNT on filtered corpus).

**Empty state:** "No documents match your filters."

**Auth:** `LoginRequiredMixin` on the partial view. Returns 403 for unauthenticated HTMX requests.

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
- AC8: Documents are ordered by year (descending), then material type, then SHA (deterministic)
- AC9: If no documents exist for the org, show "No documents in corpus."

### Part 3: PDF Viewer
- AC10: `/dashboard/reports/<sha>/view/` renders the PDF inline via `<iframe>` with presigned S3 URL
- AC11: Toolbar has Download button (links to existing `report_download` URL)
- AC12: Toolbar has Print button (triggers `window.print()` or iframe print)
- AC13: Metadata sidebar shows: org name + EIN link, material type, year, page count, file size, source URL, confidence, archived date, SHA-256
- AC14: "View Org" link navigates to the org detail page
- AC15: Next/Previous document links navigate to adjacent docs for the same org (deterministic order with SHA tie-breaker)
- AC16: Back button uses `return_to` query param if present; falls back to org detail or reports list
- AC17: Presigned URL has 15-minute expiry
- AC18: Page requires authentication (LoginRequiredMixin)

### Viewer Search Panel
- AC19: Sidebar has a collapsible "Browse Corpus" search section
- AC20: Search panel has text input for org name (partial match)
- AC21: Search panel has material_type dropdown filter
- AC22: Search panel has state dropdown filter
- AC23: Search results load via HTMX without full page reload
- AC24: Results show as compact list (org name, material type, year) — max 25 per page, "Load more" pagination
- AC25: Clicking a search result navigates to that document in the viewer
- AC26: Empty search results show "No documents match your filters."
- AC27: Search endpoint requires authentication (unauthenticated requests redirect to login; HTMX requests receive 302 which the browser follows)

### Error Handling
- AC28: If S3 presign fails, viewer shows error message with link to report detail page
- AC29: If org record is missing, sidebar shows EIN only (no broken link)
- AC30: Null metadata fields display as "-" in sidebar
- AC31: Below iframe, "Can't see the PDF?" fallback link to download URL

### Navigation
- AC32: Report detail page has a "View PDF" button linking to the viewer
- AC33: Reports list page has a view link per row linking to the viewer
- AC34: Org detail document listing links to the viewer

## Security Considerations

- All views require authentication (LoginRequiredMixin), including the HTMX search partial
- S3 presigned URLs are time-limited (15 min) and scoped to a single object
- Name search uses Django ORM `icontains` (parameterized query, no SQL injection)
- PDF rendered in `<iframe>` with S3 presigned URL — browser isolates the cross-origin content. No explicit CSP changes needed since the iframe src is a signed AWS URL, not user-controlled
- No user-supplied content rendered as HTML (XSS-safe)
- `return_to` parameter: must be validated as a relative URL (starts with `/`) to prevent open-redirect attacks. Reject absolute URLs or URLs with `://`
- `source_url_redacted` is displayed as plain text (not a clickable link) — safe for display; URL tokens are already stripped by the crawler

## Testing Requirements

- Unit tests for name search filter (exact, partial, case-insensitive, combined with other filters, empty query)
- Unit tests for document listing context (org with docs, org without docs, ordering with tie-breaker)
- Unit tests for viewer view (presigned URL generation, org lookup, next/prev navigation, missing org, S3 error)
- Unit tests for viewer search partial (name filter, material_type filter, state filter, combined filters, empty results, pagination, auth enforcement)
- Unit tests for `return_to` validation (relative URL accepted, absolute URL rejected, missing param falls back)
- Unit tests for null metadata display (null year, null material_type, null page_count)
- Unit tests for authentication enforcement on all new views including HTMX partial
- Integration: verify document listing links to viewer, viewer links back to org, search results navigate correctly

## Traps to Avoid

1. **Don't build a custom PDF renderer** — browser built-in PDF viewers via `<iframe>` are sufficient and avoid JS library maintenance
2. **Don't add a title column to the corpus table** — derive display names from existing metadata (org name + material_type + year)
3. **Don't assume all orgs have documents** — handle the zero-document case gracefully
4. **Don't break the existing report detail page** — the viewer is additive, not a replacement
5. **iframe src must be the full presigned URL** — don't proxy the PDF through Django (memory/bandwidth waste)
