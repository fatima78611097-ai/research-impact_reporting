# Plan 0044: Org Search, Document Listing & PDF Viewer

**Spec:** locard/specs/0044-org-search-doc-viewer.md
**Created:** 2026-05-16

## Overview

Implement org name search, corpus document listing on org detail pages, an in-browser PDF viewer with metadata sidebar, a parsed-content viewer (sections/tables from Docling output), and a corpus search panel. All within the existing Django dashboard using Tailwind CSS and HTMX.

## Scope Addition: Parse Viewer (Part 4)

The spec covers Parts 1-3. **Part 4 (Parse Content Viewer) was explicitly requested by the operator** post-spec-approval to support evaluation of Docling extraction quality. It renders structured parse output (sections, headings, tables) without requiring SQL access.

## Phase Structure

Each phase is independently committable and testable:

1. **Phase A** — Models + template helpers (foundation)
2. **Phase B** — Org name search (Part 1)
3. **Phase C** — Org document listing (Part 2)
4. **Phase D** — PDF viewer (Part 3)
5. **Phase E** — Parse content viewer (Part 4)
6. **Phase F** — Corpus search panel
7. **Phase G** — Navigation links + integration

---

## Phase A: Models & Template Helpers

### Models for Parse Data

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
        db_table = 'lava_parse"."documents'

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

**Before coding:** Verify exact column names via `information_schema.columns` query on the running DB.

### Template Tags / Helpers

**File:** `pipeline/templatetags/pipeline_tags.py`

```python
@register.simple_tag
def display_name(report, org=None):
    """Derive display name per spec precedence rules."""
    org_name = org.name if org else None
    mat_label = report.material_type.replace("_", " ").title() if report.material_type else None
    year = report.report_year

    name_part = org_name or f"[{report.source_org_ein}]"
    type_part = mat_label or "Document"
    year_part = f" ({year})" if year else ""

    return f"{name_part} — {type_part}{year_part}"


@register.filter
def material_type_label(value):
    """annual_report → Annual Report"""
    if not value:
        return "-"
    return value.replace("_", " ").title()
```

**Thumbnail approach (addresses Codex concern about per-row presigning):**
- Use a deterministic URL pattern: `/dashboard/reports/<sha>/thumbnail/`
- This view generates a short-lived presigned URL and returns a 302 redirect
- Template uses `<img src="..." onerror="this.src='/static/img/doc-placeholder.svg'">`
- This avoids N presign calls during template render — the browser fetches only visible thumbnails
- Alternative: for the document listing table, pre-compute thumbnail URLs in the view for the page's documents (max 50 per page) since the S3 signing is CPU-only (no network), ~1ms each

**Decision: Use view-level presigning** for the document listing (bounded to page size). The thumbnail `<img>` tag uses `onerror` to swap in a placeholder SVG if the S3 object doesn't exist (presigned URL returns 403/404 → browser fires onerror).

---

## Phase B: Org Name Search (Part 1)

**File:** `pipeline/views.py` — `OrgListView`

```python
def get_queryset(self):
    qs = NonprofitSeed.objects.all().order_by("ein")
    name = self.request.GET.get("name", "").strip()[:100]
    if len(name) >= 2:
        qs = qs.filter(name__icontains=name)
    # ... existing filters unchanged ...
    return qs

def get_context_data(self, **kwargs):
    ctx = super().get_context_data(**kwargs)
    ctx["filter_name"] = self.request.GET.get("name", "")
    # ... existing context unchanged ...
    return ctx
```

**File:** `pipeline/templates/pipeline/orgs.html`

Add "Name" text input as the **first** filter field (most prominent). Wire to GET param like existing filters. Persist all filter values in pagination links via query string.

**Tests:**
- Name search: exact, partial, case-insensitive, 1-char rejected, empty returns all
- Composition: name + state combined
- Pagination preserves filter

---

## Phase C: Org Document Listing (Part 2)

**File:** `pipeline/views.py` — `OrgDetailView.get_context_data()`

```python
from django.db.models import F

documents = Report.objects.filter(
    source_org_ein=self.object.ein
).order_by(F('report_year').desc(nulls_last=True), 'material_type', 'content_sha256')

ctx["documents"] = documents
ctx["document_count"] = documents.count()

# Parse status lookup
parsed_shas = set(
    ParsedDocument.objects.filter(
        content_sha256__in=documents.values_list('content_sha256', flat=True)
    ).values_list('content_sha256', flat=True)
)
ctx["parsed_shas"] = parsed_shas

# Thumbnail presigned URLs (bounded by page — max 50 docs shown)
import boto3
from django.conf import settings
s3 = boto3.client("s3")
thumb_urls = {}
for doc in documents[:50]:
    key = f"thumbnails/{doc.content_sha256}.jpg"
    thumb_urls[doc.content_sha256] = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_COLLATERAL_BUCKET, "Key": key},
        ExpiresIn=300,
    )
ctx["thumb_urls"] = thumb_urls
```

**Null year ordering:** `F('report_year').desc(nulls_last=True)` ensures null-year docs sort last. This is PostgreSQL-native and works with Django's unmanaged models.

**File:** `pipeline/templates/pipeline/org_detail.html`

Add "Documents (N)" section. Table columns:
- Thumbnail: `<img src="{{ thumb_urls.sha }}" class="h-12" onerror="this.src='/static/pipeline/img/doc-placeholder.svg'">`
- Display name: `{% display_name doc org %}`
- Material Type: badge with `{{ doc.material_type|material_type_label }}`
- Year: `{{ doc.report_year|default:"-" }}`
- Pages: `{{ doc.page_count|default:"-" }}`
- Size: `{{ doc.file_size_bytes|filesizeformat }}`
- Parse: checkmark icon if `doc.content_sha256 in parsed_shas`
- Actions: [View PDF] [View Parse (if parsed)]

Each row links to viewer with `return_to` set to current org detail URL.

Zero-document state: "No documents in corpus."

**Tests:**
- Org with documents: correct count, ordering (year desc, nulls last, material_type, sha tiebreaker)
- Org without documents: empty state
- Parse status indicator: present for parsed docs, absent for unparsed
- Thumbnail URL generation (mock S3)

---

## Phase D: PDF Viewer (Part 3)

**URL:** `path("reports/<str:sha>/view/", views.DocumentViewerView.as_view(), name="report_view")`

**View:**

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
        from botocore.exceptions import ClientError

        # PDF presigned URL (15 min)
        try:
            s3 = boto3.client("s3")
            key = f"pdfs/{self.object.content_sha256}.pdf"
            ctx["pdf_url"] = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.S3_COLLATERAL_BUCKET, "Key": key},
                ExpiresIn=900,
            )
        except ClientError:
            ctx["pdf_error"] = True

        # Org lookup
        try:
            ctx["org"] = NonprofitSeed.objects.get(ein=self.object.source_org_ein)
        except NonprofitSeed.DoesNotExist:
            ctx["org"] = None

        # Next/prev for same org
        same_org = Report.objects.filter(
            source_org_ein=self.object.source_org_ein
        ).order_by(F('report_year').desc(nulls_last=True), 'material_type', 'content_sha256')
        doc_list = list(same_org.values_list('content_sha256', flat=True))
        idx = doc_list.index(self.object.content_sha256) if self.object.content_sha256 in doc_list else -1
        ctx["prev_doc"] = doc_list[idx - 1] if idx > 0 else None
        ctx["next_doc"] = doc_list[idx + 1] if 0 <= idx < len(doc_list) - 1 else None

        # Parse status
        ctx["is_parsed"] = ParsedDocument.objects.filter(
            content_sha256=self.object.content_sha256
        ).exists()

        # return_to validation
        ctx["return_to"] = _safe_return_url(self.request)

        # Audit log
        logger.info("document_view", extra={
            "user": self.request.user.username,
            "sha": self.object.content_sha256,
        })

        return ctx
```

**`return_to` validation and propagation:**

```python
def _safe_return_url(request):
    url = request.GET.get("return_to", "")
    if url and url.startswith("/dashboard/") and "://" not in url and not url.startswith("//"):
        return url
    return None
```

**Propagation contract:** Every link that enters the viewer passes `?return_to=<current_page_url>`:
- Org detail document listing → viewer: `return_to=/dashboard/orgs/{ein}/`
- Reports list → viewer: `return_to=/dashboard/reports/?page=N&...`
- Report detail → viewer: `return_to=/dashboard/reports/{sha}/`
- Next/prev links within viewer: preserve existing `return_to` unchanged
- Search result links within viewer: preserve existing `return_to` unchanged

Back button uses `return_to` if valid; falls back to org detail (if org exists) or reports list.

**Template:** `pipeline/templates/pipeline/document_viewer.html`

Sidebar metadata fields (all nullable → show "-" when null):
- Org: name (linked to org detail) + EIN. If no org record: show EIN only, no link.
- Material Type: `{{ report.material_type|material_type_label }}`
- Year: `{{ report.report_year|default:"-" }}`
- Pages: `{{ report.page_count|default:"-" }}`
- Size: `{{ report.file_size_bytes|filesizeformat }}`
- Source URL: plain text (NOT a link), truncated to 80 chars via `{{ report.source_url_redacted|truncatechars:80|default:"-" }}`
- Confidence: `{{ report.classification_confidence|floatformat:3|default:"-" }}`
- Archived: `{{ report.archived_at|default:"-" }}`
- SHA-256: `{{ report.content_sha256 }}` (monospace, break-all)

Navigation links:
- [View Org →] (if org exists)
- [View Parse →] (if is_parsed)
- [← Prev Doc] / [Next Doc →] (if they exist; hidden not greyed when absent)

Below iframe: `<a href="{% url 'report_download' report.content_sha256 %}">Can't see the PDF? Download it.</a>`

Error state (pdf_error=True): show "Unable to load PDF" message with link to report detail page.

**Tests:**
- Presigned URL generation (mock S3, verify 900s expiry)
- S3 ClientError → pdf_error in context
- Org lookup success and DoesNotExist
- Next/prev: middle doc, first doc (no prev), last doc (no next), single doc (no nav)
- return_to: valid relative URL accepted, absolute URL rejected, protocol-relative rejected, non-dashboard path rejected, empty → None
- Auth enforcement (anonymous → redirect to login)
- Null metadata fields render as "-"

---

## Phase E: Parse Content Viewer (Part 4)

**URL:** `path("reports/<str:sha>/parse/", views.ParseViewerView.as_view(), name="report_parse")`

**View:**

```python
class ParseViewerView(LoginRequiredMixin, DetailView):
    model = Report
    template_name = "pipeline/parse_viewer.html"
    context_object_name = "report"
    slug_field = "content_sha256"
    slug_url_kwarg = "sha"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sha = self.object.content_sha256

        try:
            ctx["parse_doc"] = ParsedDocument.objects.get(content_sha256=sha)
        except ParsedDocument.DoesNotExist:
            ctx["parse_doc"] = None
            return ctx

        ctx["sections"] = ParsedSection.objects.filter(
            content_sha256=sha
        ).order_by("section_index")

        ctx["tables"] = ParsedTable.objects.filter(
            content_sha256=sha
        ).order_by("table_index")

        try:
            ctx["org"] = NonprofitSeed.objects.get(ein=self.object.source_org_ein)
        except NonprofitSeed.DoesNotExist:
            ctx["org"] = None

        ctx["return_to"] = _safe_return_url(self.request)
        return ctx
```

**Template:** `pipeline/templates/pipeline/parse_viewer.html`

- Toolbar: [← Back] [View PDF] [Org Detail]
- Parse summary bar: version, pages, sections, tables, chars (formatted with commas), duration (ms → seconds)
- Sections: rendered with heading hierarchy. Heading level → indent/font-size. Body text in full. Page number badge per section.
- Tables: rendered as HTML `<table>` elements. Parse the stored markdown table format into HTML rows/cells. Caption shown above if present.
- Not-parsed state: "This document has not been parsed yet." with link back to org detail.

**Table markdown → HTML conversion:** The `markdown` column stores pipe-delimited markdown tables. Convert using a simple parser (split on `|`, strip, first row = headers). Do NOT use a markdown library — keep it dependency-free.

**XSS prevention (CRITICAL):** All cell content MUST be escaped before rendering in HTML. The parser produces a structured Python list-of-lists; the template iterates this structure and applies Django's `{{ cell|escape }}` filter (or uses autoescaping, which is on by default). Never use `|safe` or `mark_safe` on cell content. The table structure (headers, rows) is generated by our parser from the pipe-delimited format — only the cell TEXT comes from the parsed document and must be treated as untrusted.

**Tests:**
- Parsed document: sections and tables in context, correct ordering
- Not-parsed document: parse_doc=None, graceful template
- Auth enforcement
- Table markdown rendering

---

## Phase F: Corpus Search Panel (AC19-27)

**URL:** `path("reports/search/", views.DocumentSearchPartial.as_view(), name="report_search")`

**View:**

```python
class DocumentSearchPartial(LoginRequiredMixin, ListView):
    template_name = "pipeline/partials/search_results.html"
    context_object_name = "results"
    paginate_by = 25

    def get_queryset(self):
        qs = Report.objects.all()

        q = self.request.GET.get("q", "").strip()[:100]
        if len(q) >= 2:
            matching_eins = NonprofitSeed.objects.filter(
                name__icontains=q
            ).values("ein")
            qs = qs.filter(source_org_ein__in=matching_eins)
        elif q:
            return Report.objects.none()

        material_type = self.request.GET.get("material_type", "")
        if material_type:
            qs = qs.filter(material_type=material_type)

        state = self.request.GET.get("state", "")
        if state:
            state_eins = NonprofitSeed.objects.filter(state=state).values("ein")
            qs = qs.filter(source_org_ein__in=state_eins)

        return qs.order_by(
            F('report_year').desc(nulls_last=True), 'material_type', 'content_sha256'
        )
```

**Auth:** Uses standard `LoginRequiredMixin`. Unauthenticated requests get redirected to login (302). HTMX follows the redirect (browser handles it). No 403 — follow spec exactly.

**Template:** `pipeline/templates/pipeline/partials/search_results.html`
- Compact list: org name (truncated 30 chars), material type label, year
- Each result links to that document's viewer (preserving `return_to`)
- "Load more" link at bottom ��� HTMX append of page N+1
- Empty state: "No documents match your filters."

**Integration in viewer sidebar:** Collapsible "Browse Corpus" section with text input, material_type dropdown, state dropdown. HTMX `hx-get` triggers search, results replace a target div.

Material type dropdown values: queried once in `DocumentViewerView.get_context_data()` and cached (Django cache, 5 min TTL).

**Tests:**
- Name filter: min 2 chars, partial match, case-insensitive
- Material type filter: exact match
- State filter: via org subquery
- Combined filters
- Pagination: 25 per page, "Load more" link
- Empty results message
- Auth: anonymous request → 302 redirect (not 403)

---

## Phase G: Navigation Links

Update existing templates to link into the new views:

**`report_detail.html`:**
- Add "View PDF" button → `{% url 'report_view' report.content_sha256 %}?return_to={% url 'report_detail' report.content_sha256 %}`
- Add "View Parse" button (if parsed) → `{% url 'report_parse' report.content_sha256 %}?return_to={% url 'report_detail' report.content_sha256 %}`

**`reports.html`:**
- Add view icon per row → viewer with `return_to` set to current reports list URL (including pagination/filters)

**`org_detail.html`:** (already done in Phase C — document listing rows link to viewer)

**Integration tests:**
- Org detail → viewer → back returns to org detail
- Reports list → viewer → back returns to reports list (with filters preserved)
- Viewer → next/prev → return_to unchanged
- Viewer → parse → back works
- Search result → viewer → return_to unchanged

---

## File Manifest

| File | Phase | Action | Purpose |
|------|-------|--------|---------|
| `pipeline/models.py` | A | Edit | Add ParsedDocument, ParsedSection, ParsedTable |
| `pipeline/templatetags/pipeline_tags.py` | A | Edit | Add display_name, material_type_label |
| `pipeline/views.py` | B-F | Edit | All new views and filter logic |
| `pipeline/urls.py` | D-F | Edit | 4 new URL patterns (view, parse, search, thumbnail) |
| `pipeline/templates/pipeline/orgs.html` | B | Edit | Name search input |
| `pipeline/templates/pipeline/org_detail.html` | C | Edit | Documents section |
| `pipeline/templates/pipeline/document_viewer.html` | D | Create | PDF viewer page |
| `pipeline/templates/pipeline/parse_viewer.html` | E | Create | Parse content viewer |
| `pipeline/templates/pipeline/partials/search_results.html` | F | Create | HTMX search fragment |
| `pipeline/templates/pipeline/report_detail.html` | G | Edit | View PDF/Parse buttons |
| `pipeline/templates/pipeline/reports.html` | G | Edit | View link per row |
| `pipeline/static/pipeline/img/doc-placeholder.svg` | A | Create | Thumbnail fallback |
| `pipeline/tests/test_org_search.py` | B | Create | Part 1 tests |
| `pipeline/tests/test_document_listing.py` | C | Create | Part 2 tests |
| `pipeline/tests/test_viewer.py` | D | Create | Part 3 tests |
| `pipeline/tests/test_parse_viewer.py` | E | Create | Part 4 tests |
| `pipeline/tests/test_search_partial.py` | F | Create | Search panel tests |

## Dependencies

- HTMX (already in dashboard base template)
- No new Python packages
- Table markdown → HTML: manual pipe-split parser (no `markdown` library needed)

## Security

- All views: `LoginRequiredMixin` (single-operator model, no object-level perms)
- `return_to`: validated via `_safe_return_url()` — rejects absolute URLs, protocol-relative, non-dashboard paths
- `source_url_redacted`: displayed as plain text, truncated 80 chars, never rendered as clickable link
- Presigned URLs: scoped to single object, time-limited (15 min PDF, 5 min thumbnails)
- Audit: document views logged with user + SHA
- Search inputs: parameterized queries via ORM (no raw SQL)
- **Table cell content XSS:** All parsed table cell text rendered with Django autoescaping (never `|safe`). Parser outputs Python data structures; template escapes on render.
- **Section body text:** Rendered via `{{ section.body_text }}` with autoescaping — no raw HTML.
- **Rate limiting:** Out of scope for this spec (single-operator dashboard behind VPN). If multi-user access is added later, rate limiting should be implemented on login.
