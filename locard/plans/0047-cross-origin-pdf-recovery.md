# Plan 0047: Cross-Origin PDF Recovery

**Spec**: `locard/specs/0047-cross-origin-pdf-recovery.md`
**Protocol**: SPIDER
**Estimated phases**: 4 (engine fix, Content-Type gate, Pass 1, Pass 2)

---

## Overview

This plan implements the permanent engine fix to accept cross-origin PDFs found on org pages, adds Content-Type validation as the security gate, and provides two recovery commands for the backlog.

The changes touch: `candidate_filter.py`, `redirect_policy.py`, `fetch_pdf.py`, `http_client.py`, `async_http_client.py`, `config.py`, and two new tool scripts.

---

## Phase A: Config Constants & Candidate Dataclass Extension

### A1. Add config constants to `lavandula/reports/config.py`

```python
MAX_UNKNOWN_HOPS = 2
MISMATCH_SLOW_THRESHOLD = 3       # add delay after this many mismatches per domain
MISMATCH_BLOCK_THRESHOLD = 10     # skip domain entirely after this many
CROSS_ORIGIN_DROP_ALERT_THRESHOLD = 50
```

### A2. Extend `Candidate` dataclass in `candidate_filter.py`

The `Candidate` is `frozen=True`, so add a new field with default:

```python
@dataclass(frozen=True)
class Candidate:
    url: str
    anchor_text: str
    referring_page_url: str
    discovered_via: str
    hosting_platform: str | None
    attribution_confidence: str
    original_source_url: str | None = None
    wayback_digest: str | None = None
    cross_origin_candidate: bool = False    # NEW
```

### A3. Tests for Phase A

- Unit test: Candidate can be constructed with `cross_origin_candidate=True`
- Unit test: New config constants exist and have expected defaults

---

## Phase B: Engine Fix — candidate_filter.py

### B1. Modify cross-origin gate (line 241)

Replace the silent `return None` with PDF-aware logic:

```python
if is_cross_origin and not is_cms_match:
    if _pdf_like(href):
        # Fall through to normal scoring — will be flagged cross_origin_candidate
        pass
    else:
        _log_cross_origin_drop(href, seed_etld1, ein)
        return None
```

### B2. Flag accepted cross-origin PDFs

After the scoring logic produces a `Candidate`, set `cross_origin_candidate=True` for cross-origin PDFs. Since Candidate is frozen, construct it with the flag:

At the point where `Candidate(...)` is returned (around line 280+), thread through:

```python
return Candidate(
    url=href,
    anchor_text=anchor,
    referring_page_url=referring_page_url,
    discovered_via="subpage-link",
    hosting_platform=None,
    attribution_confidence="cross_origin_pdf",
    cross_origin_candidate=is_cross_origin,  # True for newly-accepted PDFs
)
```

### B3. Implement `_log_cross_origin_drop()`

A helper that tracks per-org drop count and escalates from DEBUG to WARNING when `CROSS_ORIGIN_DROP_ALERT_THRESHOLD` is exceeded:

```python
_cross_origin_drop_counts: dict[str, int] = {}

def _log_cross_origin_drop(href: str, seed_etld1: str, ein: str) -> None:
    _cross_origin_drop_counts[ein] = _cross_origin_drop_counts.get(ein, 0) + 1
    count = _cross_origin_drop_counts[ein]
    level = logging.WARNING if count >= config.CROSS_ORIGIN_DROP_ALERT_THRESHOLD else logging.DEBUG
    logger.log(level, "cross_origin_non_pdf_dropped",
               extra={"href": href[:120], "seed": seed_etld1, "ein": ein, "drop_count": count})
```

### B4. Tests for Phase B

- Unit: cross-origin `.pdf` link on org page → returns Candidate with `cross_origin_candidate=True`
- Unit: cross-origin non-PDF link → returns None (dropped)
- Unit: same-origin PDF → unchanged behavior, `cross_origin_candidate=False`
- Unit: CMS-match logic still works (no regression)
- Unit: logging escalation after threshold hits

---

## Phase C: Engine Fix — redirect_policy.py

### C1. Add `is_pdf_candidate` parameter to `check_redirect_chain`

```python
def check_redirect_chain(
    chain: Iterable[str],
    *,
    seed_etld1: str,
    is_pdf_candidate: bool = False,
) -> RedirectCheckResult:
```

### C2. Implement bounded unknown-hop logic

When `is_pdf_candidate=True`, allow hops through non-allowlisted domains up to `MAX_UNKNOWN_HOPS` (2). Each unknown hop is logged at INFO.

```python
unknown_hops = 0
for url in urls:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if not _allowed(host, seed_etld1):
        if is_pdf_candidate:
            unknown_hops += 1
            logger.info("pdf_candidate_cross_hop", extra={"host": host, "seed": seed_etld1})
            if unknown_hops > config.MAX_UNKNOWN_HOPS:
                return RedirectCheckResult(ok=False, reason="cross_origin_blocked",
                                           note=f"too many unknown hops ({unknown_hops})")
        else:
            return RedirectCheckResult(ok=False, reason="cross_origin_blocked",
                                       note=f"hop {host!r} not in seed eTLD+1 or platform allowlist")
return RedirectCheckResult(ok=True)
```

### C3. Thread `is_pdf_candidate` through HTTP clients

**`http_client.py`** — `ReportsHTTPClient.get()`:
- Add `is_pdf_candidate: bool = False` parameter
- Pass to `check_redirect_chain(..., is_pdf_candidate=is_pdf_candidate)` at line ~317

**`async_http_client.py`** — `AsyncReportsHTTPClient.get()`:
- Same parameter addition
- Pass to `check_redirect_chain` at line ~348

**`fetch_pdf.py`** — `download()`:
- Add `is_pdf_candidate: bool = False` parameter
- Pass through to `client.get(..., is_pdf_candidate=is_pdf_candidate)`

### C4. Tests for Phase C

- Unit: `is_pdf_candidate=False` → existing behavior unchanged (regression test)
- Unit: `is_pdf_candidate=True`, 1 unknown hop → ALLOWED
- Unit: `is_pdf_candidate=True`, 2 unknown hops → ALLOWED
- Unit: `is_pdf_candidate=True`, 3 unknown hops → BLOCKED (exceeds MAX_UNKNOWN_HOPS)
- Unit: `is_pdf_candidate=True`, hops through allowlisted domain → don't count as unknown
- Unit: MAX_REDIRECTS still enforced even for PDF candidates

---

## Phase D: Content-Type Gate in Fetcher

### D1. Add Content-Type validation to `fetch_pdf.download()`

After the GET request succeeds (status == "ok"), check Content-Type for cross-origin candidates:

```python
if is_pdf_candidate and r.status == "ok":
    ct = (r.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
    url_has_pdf_ext = url.lower().rstrip("/").endswith(".pdf")
    allowed_types = {"application/pdf"}
    if url_has_pdf_ext:
        allowed_types.add("application/octet-stream")
    if ct not in allowed_types:
        return DownloadOutcome(
            status="content_type_mismatch",
            url=url,
            final_url=r.final_url,
            ...
            note=f"expected pdf, got {ct}",
        )
```

### D2. Per-domain mismatch throttling

Add a module-level counter in `fetch_pdf.py`. Uses a two-tier approach: after 3 mismatches from a domain, add a 10s delay between fetches; after 10 mismatches, skip entirely for the run. This prevents a single attacker-controlled domain from blocking legitimate CDN fetches via early poisoning.

```python
_mismatch_counts: dict[str, int] = {}

MISMATCH_SLOW_THRESHOLD = 3    # add delay after this many mismatches
MISMATCH_BLOCK_THRESHOLD = 10  # skip domain entirely after this many

def get_domain_mismatch_state(url: str) -> str:
    """Returns 'ok', 'slow', or 'blocked'."""
    from .redirect_policy import etld1
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname or ""
    domain = etld1(host)
    count = _mismatch_counts.get(domain, 0)
    if count >= MISMATCH_BLOCK_THRESHOLD:
        return "blocked"
    if count >= MISMATCH_SLOW_THRESHOLD:
        return "slow"
    return "ok"

def _increment_mismatch(url: str) -> None:
    from .redirect_policy import etld1
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname or ""
    domain = etld1(host)
    _mismatch_counts[domain] = _mismatch_counts.get(domain, 0) + 1
```

### D3. Integrate throttle check in crawler loop

In `crawler.py` (or the caller of `fetch_pdf.download`), before downloading a cross-origin candidate:

```python
if cand.cross_origin_candidate and fetch_pdf.is_domain_throttled(cand.url):
    _write_fetch(ein=ein, ..., fetch_status="domain_throttled", ...)
    continue
```

### D4. Tests for Phase D

- Unit: cross-origin candidate + Content-Type `application/pdf` → succeeds
- Unit: cross-origin candidate + Content-Type `application/octet-stream` + `.pdf` URL → succeeds
- Unit: cross-origin candidate + Content-Type `text/html` → `content_type_mismatch`
- Unit: cross-origin candidate + Content-Type `application/octet-stream` + non-`.pdf` URL → rejected
- Unit: same-origin candidate → no Content-Type gate applied (existing behavior)
- Unit: mismatch counter reaches threshold → `is_domain_throttled` returns True
- Integration: full flow from candidate discovery through fetch with cross-origin PDF

---

## Phase E: Pass 1 Recovery Command

### E1. Create `lavandula/reports/tools/recovery_pass1.py`

Management command structure:

```python
"""Pass 1 recovery: re-fetch cross_origin_blocked PDF URLs from fetch_log."""

def main():
    args = parse_args()
    # Validate: --max-urls required unless --no-limit
    conn = get_connection(...)
    
    # Query: SELECT url, seed_url, ein FROM fetch_log
    #        WHERE status = 'cross_origin_blocked' AND url LIKE '%.pdf'
    #        ORDER BY id
    
    for batch in batched(urls, args.batch_size):
        for url_record in batch:
            if args.dry_run:
                log(f"would fetch: {url_record.url}")
                continue
            outcome = fetch_pdf.download(
                url_record.url, client,
                seed_etld1=url_record.seed_etld1,
                is_pdf_candidate=True,
            )
            if outcome.status == "ok":
                archive_to_s3(outcome)
                update_fetch_log(conn, url_record.id, status="success")
                insert_corpus(conn, outcome)
            else:
                update_fetch_log(conn, url_record.id, status=outcome.status)
```

### E2. Arguments

```
--max-urls INT       (required unless --no-limit)
--no-limit           explicitly bypass cap
--batch-size INT     (default 1000)
--dry-run            log without fetching
--state-filter STR   comma-separated state codes
--resume-from INT    fetch_log ID to resume from
```

### E3. Operational features

- Idempotent: skip rows already updated to non-`cross_origin_blocked` status
- Resume: `--resume-from` or auto-detect last processed ID from a state file
- Rate limiting: reuse existing `host_throttle` module
- Backoff: exponential on 429/5xx (base 2s, max 60s)
- Progress: log every 100 URLs with running counts
- **SQL safety**: All queries use psycopg2 parameterized queries (`%s` placeholders, never string interpolation). The `--state-filter` argument is validated against `config.PRIORITY_VALUE_RE` (alphanumeric + underscore only) before use in queries.
- **TLS**: Reuses existing `ReportsHTTPClient` which enforces HTTPS with certificate verification (requests library defaults: TLS 1.2+, system CA bundle, hostname verification enabled). The `allow_insecure_cleartext=False` default is never overridden for recovery fetches.

### E4. Tests for Phase E

- Unit: dry-run mode produces expected output without network calls
- Unit: already-fetched URLs are skipped (idempotent)
- Unit: --max-urls cap is respected
- Unit: --state-filter limits scope correctly
- Integration: mock HTTP + mock DB → full pass1 workflow

---

## Phase F: Pass 2 Recovery Command

### F1. Create `lavandula/reports/tools/recovery_pass2.py`

```python
"""Pass 2 recovery: re-scan HTML pages for orgs with missing PDF candidates."""

def main():
    args = parse_args()
    conn = get_connection(...)
    
    # Query: orgs with HTML pages in fetch_log (status='success', content_type LIKE 'text/html%')
    # but zero corpus entries or only cross_origin_blocked PDFs
    orgs = identify_target_orgs(conn, state_filter=args.state_filter)
    
    for batch in batched(orgs, args.batch_size):
        for org in batch:
            if args.source == "cached":
                html_pages = load_cached_html(org)
            else:
                html_pages = recrawl_org_pages(org, client)
            
            candidates = extract_candidates_with_fixed_filter(html_pages, org)
            
            if args.dry_run:
                log(f"org {org.ein}: {len(candidates)} new candidates")
                continue
            
            queue_candidates_for_fetch(conn, candidates)
```

### F2. Determine HTML caching status

Check if HTML bodies are stored:
- Look for an S3 prefix like `html/` or a `body` column in `fetch_log`
- If not cached, fall back to re-crawl mode

### F3. Arguments

```
--max-orgs INT       (required unless --no-limit)
--no-limit           explicitly bypass cap
--batch-size INT     (default 500)
--dry-run            discover candidates without queuing
--state-filter STR   comma-separated state codes (validated: alpha only)
--source {cached,recrawl}  (default: auto-detect)
```

### F3a. Security constraints (same as Pass 1)

- All DB queries use parameterized placeholders — no string interpolation
- `--state-filter` validated against `[A-Z]{2}` regex before use
- TLS enforced for all HTTP fetches (existing client defaults)
- No new dependencies introduced — uses existing `host_throttle`, `fetch_pdf`, `db_writer`

### F4. Tests for Phase F

- Unit: target org identification query returns correct orgs
- Unit: candidate extraction with fixed filter finds cross-origin PDFs
- Unit: --max-orgs cap respected
- Unit: dry-run logs candidates without side effects
- Integration: mock HTML + mock DB → full pass2 discovery workflow

---

## Phase G: Integration Testing & Acceptance

### G1. End-to-end smoke test

Test the full flow with CanCare (cancare.org):
1. Run candidate_filter on CanCare's financials page HTML
2. Verify the `cdn.prod.website-files.com` PDF link is accepted
3. Verify `cross_origin_candidate=True` is set
4. Verify redirect chain is allowed (cdn.prod.website-files.com eTLD+1 = `website-files.com` — not in allowlist, counts as 1 unknown hop)
5. Verify Content-Type gate passes (actual PDF)

### G2. Regression test

- Run existing test suite — all must pass unchanged
- Verify same-origin flow is completely unaffected
- Verify HOSTING_PLATFORMS fast-path still works

### G3. Acceptance criteria verification

| Criterion | How to verify |
|-----------|---------------|
| Engine fix verified | CanCare test crawl succeeds |
| Pass 1 recovery | `--dry-run` shows ~31K URLs |
| Pass 2 recovery | `--dry-run` shows candidates for target orgs |
| No regression | Existing tests pass |
| Logging | Cross-origin decisions visible in logs |
| Safety caps | Commands fail without --max-urls/--max-orgs |

---

## File Change Summary

| File | Change |
|------|--------|
| `lavandula/reports/config.py` | Add 3 constants |
| `lavandula/reports/candidate_filter.py` | Accept cross-origin PDFs, add logging, extend Candidate |
| `lavandula/reports/redirect_policy.py` | Add `is_pdf_candidate` param, bounded unknown hops |
| `lavandula/reports/http_client.py` | Thread `is_pdf_candidate` to redirect check |
| `lavandula/reports/async_http_client.py` | Thread `is_pdf_candidate` to redirect check |
| `lavandula/reports/fetch_pdf.py` | Content-Type gate, mismatch throttling |
| `lavandula/reports/crawler.py` | Check domain throttle before cross-origin fetch |
| `lavandula/reports/tools/recovery_pass1.py` | NEW — Pass 1 command |
| `lavandula/reports/tools/recovery_pass2.py` | NEW — Pass 2 command |

---

## Ordering & Dependencies

```
Phase A (config + dataclass) → no deps
Phase B (candidate_filter) → depends on A
Phase C (redirect_policy) → depends on A
Phase D (Content-Type gate) → depends on B + C
Phase E (Pass 1) → depends on C + D
Phase F (Pass 2) → depends on B + D
Phase G (integration) → depends on all
```

Phases B and C can be implemented in parallel.
Phases E and F can be implemented in parallel (both depend on D).

---

## Traps to Avoid (from spec)

1. Don't remove HOSTING_PLATFORMS — it's a fast-path, not being replaced
2. Don't trust URL extension alone — Content-Type gate is mandatory
3. Don't re-fetch already-successful URLs in Pass 1
4. Don't process orgs that already have healthy corpus in Pass 2
5. Don't skip rate limiting for recovery passes
6. Don't forget to thread `is_pdf_candidate` through BOTH sync and async HTTP clients

---

## Consultation Log

| Date | Type | Model | Verdict | Key Feedback |
|------|------|-------|---------|-------------|
| 2026-05-16 | plan-review | gemini | APPROVE | No issues found |
| 2026-05-16 | red-team-plan | gemini | REQUEST_CHANGES | CRITICAL: SQL injection risk; HIGH: TLS config |

All findings addressed: explicit parameterized queries, input validation on state-filter, TLS enforcement documented, two-tier mismatch thresholds.
