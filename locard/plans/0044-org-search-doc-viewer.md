# Plan 0044: Org Search, Document Listing & PDF Viewer

**Spec:** locard/specs/0044-org-search-doc-viewer.md
**Created:** 2026-05-16

## Overview

Implement org name search, corpus document listing on org detail pages, an in-browser PDF viewer with metadata sidebar, a parsed-content viewer (sections/tables from Docling output), and a corpus search panel. All within the existing Django dashboard using Tailwind CSS and HTMX.

## Scope Addition: Parse Viewer (Part 4)

The spec covers Parts 1-3. This plan adds **Part 4: Parse Content Viewer** — a tab/section on the document viewer page that renders the structured Docling parse output (sections with headings, body text, tables). This lets the operator evaluate extraction quality without running SQL.

## Implementation Steps

### Step 1: Models for Parse Data

Add unmanaged Django models for `lava_parse` tables in `pipeline/models.py`:

```python
class ParsedDocument(models.Model):
    content_sha256 = models.TextField(primary_key=True)
    source_org_ein = models.TextField()
    parse_version = models.TextField()
    parsed_at = models.DateTimeField()
    page_count = models.IntegerField()
    section_count = models.IntegerField()
    table_count = models.IntegerField()
    figure_count = models.IntegerField()
    total_text_chars = models.IntegerField()
    parse_duration_ms = models.IntegerField()
    error = models.TextField(null=True)
    metadata_json = models.JSONField(null=True)

    class Meta:
        managed = False
        db_table = 'lava_parse"."documents'  # cross-schema reference

class ParsedSection(models.Model):
    content_sha256 = models.TextField()
    section_index = models.IntegerField()
    heading = models.TextField(null=True)
    heading_level = models.IntegerField(null=True)
    body_text = models.TextField()
    char_count = models.IntegerField()
    page_start = models.IntegerField(null=True)
    page_end = models.IntegerField(null=True)
    parent_headings = models.JSONField(null=True)

    class Meta:
        managed = False
        db_table = 'lava_parse"."sections'

class ParsedTable(models.Model):
    content_sha256 = models.TextField()
    table_index = models.IntegerField()
    caption = models.TextField(null=True)
    markdown = models.TextField(null=True)
    row_count = models.IntegerField(null=True)
    col_count = models.IntegerField(null=True)
    page_number = models.IntegerField(null=True)
    section_index = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = 'lava_parse"."tables'
```

**Note:** Verify exact column names match the DB schema before coding. Use `information_schema.columns` query.

### Step 2: Org Name Search (Part 1)

**File:** `pipeline/views.py` — `OrgListView.get_queryset()`

Add `name` filter parameter:
```python
name = self.request.GET.get("name", "").strip()[:100]
if len(name) >= 2:
    qs = qs.filter(name__icontains=name)
```

Pass `filter_name` to context. Persist filter in pagination links.

**File:** `pipeline/templates/pipeline/orgs.html`

Add a "Name" text input as the first/most prominent filter field. Wire to same GET-based filter mechanism as existing fields.

### Step 3: Org Document Listing (Part 2)

**File:** `pipeline/views.py` — `OrgDetailView.get_context_data()`

Query corpus documents for the org's EIN:
```python
from django.db.models import F

documents = Report.objects.filter(
    source_org_ein=self.object.ein
).order_by(F('report_year').desc(nulls_last=True), 'material_type', 'content_sha256')
ctx["documents"] = documents
ctx["document_count"] = documents.count()
```

Also check parse status for each document:
```python
parsed_shas = set(
    ParsedDocument.objects.filter(
        content_sha256__in=documents.values_list('content_sha256', flat=True)
    ).values_list('content_sha256', flat=True)
)
ctx["parsed_shas"] = parsed_shas
```

**File:** `pipeline/templates/pipeline/org_detail.html`

Add a "Documents (N)" section below existing content. Table columns:
- Thumbnail (48px-tall, from presigned S3 `thumbnails/{sha}.jpg`, placeholder icon if missing)
- Display name (derived per spec rules)
- Material Type (badge)
- Year
- Pages
- Size
- Parse status indicator (checkmark if parsed)
- Link to viewer

### Step 4: PDF Viewer Page (Part 3)

**URL:** `path("reports/<str:sha>/view/", views.DocumentViewerView.as_view(), name="report_view")`

**View:** `DocumentViewerView(LoginRequiredMixin, DetailView)`
- Generate 15-min presigned S3 URL for PDF
- Look up org name
- Compute next/prev documents for same org
- Validate `return_to` param
- Log document access (audit)

**Template:** `pipeline/templates/pipeline/document_viewer.html`
- Layout: toolbar top, iframe (75% width) + sidebar (25% width)
- Toolbar: Back, Download, Print buttons
- Sidebar: metadata fields, View Org link, Next/Prev links
- Below iframe: "Can't see the PDF?" fallback download link
- Error state if presign fails

### Step 5: Parse Content Viewer (Part 4 — NEW)

**URL:** `path("reports/<str:sha>/parse/", views.ParseViewerView.as_view(), name="report_parse")`

**View:** `ParseViewerView(LoginRequiredMixin, DetailView)`
- Model: `Report` (same slug lookup as viewer)
- Context: ParsedDocument metadata, all sections ordered by section_index, all tables

**Template:** `pipeline/templates/pipeline/parse_viewer.html`

Layout:
```
┌─────────────────────────────────────────────────────────┐
│ Toolbar: [← Back] [View PDF] [Org Detail]               │
├─────────────────────────────────────────────────────────┤
│ Parse Summary                                            │
│ Version: docling-2.93.0 | Pages: 30 | Sections: 176    │
│ Tables: 17 | Chars: 99,350 | Duration: 23.4s            │
├─────────────────────────────────────────────────────────┤
│ Sections                                                 │
│ ─────────                                                │
│ ## A Future We're Building Together          [p2]       │
│ For 128 years, Mount Desert Island Hospital...           │
│                                                          │
│ ### Guided by Purpose, Grounded in Community  [p3]      │
│ Mount Desert Island Hospital was established...          │
│ ...                                                      │
├─────────────────────────────────────────────────────────┤
│ Tables                                                   │
│ ─────────                                                │
│ Table 1 (p6): "December 31, 2023 and 2022"             │
│ | ASSETS | 2023 | 2022 |                                │
│ | Cash   | $5.3M| $4.1M|                                │
│ ...                                                      │
└─────────────────────────────────────────────────────────┘
```

- Sections rendered with heading hierarchy (indent by level)
- Body text shown in full (no truncation)
- Page numbers shown per section
- Tables rendered as HTML `<table>` from markdown (use `markdown` library or manual parse)
- If document not yet parsed: show "Not parsed" with link back

**Navigation integration:**
- Document viewer sidebar gets a "View Parse" link (if parsed)
- Org document listing gets a parse icon/link per row (if parsed)
- Report detail page gets a "View Parse" button

### Step 6: Corpus Search Panel (Spec AC19-27)

**URL:** `path("reports/search/", views.DocumentSearchPartial.as_view(), name="report_search")`

**View:** `DocumentSearchPartial` — returns HTML fragment (HTMX partial)
- Filters: `q` (org name, min 2 chars), `material_type` (dropdown), `state` (dropdown)
- Results: 25 per page, "Load more" pagination
- Auth required

**Integration:** Add to document viewer sidebar as collapsible "Browse Corpus" panel.

### Step 7: Navigation Links

- `report_detail.html`: Add "View PDF" and "View Parse" buttons
- `reports.html`: Add view icon per row
- `org_detail.html`: Document listing rows link to viewer

### Step 8: Template Tags / Helpers

**File:** `pipeline/templatetags/pipeline_tags.py`

Add helpers:
- `display_name(report, org)` — implements spec's display name precedence rules
- `material_type_label(material_type)` — `replace("_", " ").title()`
- `thumbnail_url(sha)` — returns presigned S3 URL for thumbnail (5-min expiry)

### Step 9: Tests

- Unit tests for name search filter
- Unit tests for document listing context
- Unit tests for viewer view (presigned URL, next/prev, return_to validation)
- Unit tests for parse viewer (sections/tables rendering, not-parsed state)
- Unit tests for search partial (filters, pagination, auth)
- Unit tests for display name derivation (all precedence cases)

## File Manifest

| File | Action | Purpose |
|------|--------|---------|
| `pipeline/models.py` | Edit | Add ParsedDocument, ParsedSection, ParsedTable models |
| `pipeline/views.py` | Edit | Add name filter, document listing, viewer, parse viewer, search partial |
| `pipeline/urls.py` | Edit | Add 3 new URL patterns |
| `pipeline/templatetags/pipeline_tags.py` | Edit | Add display_name, thumbnail_url helpers |
| `pipeline/templates/pipeline/orgs.html` | Edit | Add name search input |
| `pipeline/templates/pipeline/org_detail.html` | Edit | Add documents section |
| `pipeline/templates/pipeline/document_viewer.html` | Create | PDF viewer page |
| `pipeline/templates/pipeline/parse_viewer.html` | Create | Parse content viewer |
| `pipeline/templates/pipeline/partials/search_results.html` | Create | HTMX search results fragment |
| `pipeline/templates/pipeline/report_detail.html` | Edit | Add View PDF / View Parse buttons |
| `pipeline/templates/pipeline/reports.html` | Edit | Add view link per row |
| `pipeline/tests/test_org_search.py` | Create | Tests for Part 1 |
| `pipeline/tests/test_document_listing.py` | Create | Tests for Part 2 |
| `pipeline/tests/test_viewer.py` | Create | Tests for Parts 3-4 |
| `pipeline/tests/test_search_partial.py` | Create | Tests for search panel |

## Dependencies

- HTMX (already available in dashboard — used by classifier views)
- No new Python packages required
- `markdown` library for table rendering (check if already installed; if not, use manual HTML table generation from the stored markdown)

## Acceptance Criteria (from spec + Part 4)

All spec ACs (AC1-AC34) plus:
- AC35: `/dashboard/reports/<sha>/parse/` shows structured parse output (sections with headings, body text, page numbers)
- AC36: Tables rendered as formatted HTML tables
- AC37: Parse summary bar shows version, page count, section count, table count, total chars, duration
- AC38: "Not parsed" state handled gracefully with message
- AC39: Document viewer sidebar links to parse viewer (when parsed)
- AC40: Org document listing shows parse status indicator per row

## Estimated Effort

Moderate — ~4-6 hours for a builder. No architectural novelty; standard Django views/templates with existing patterns.
