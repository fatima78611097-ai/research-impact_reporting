# Spec 0047: Cross-Origin PDF Recovery

**Status**: Review
**Priority**: High
**Dependencies**: None (standalone fix to crawler engine + recovery passes)
**Estimated effort**: Medium (engine fix is small; recovery passes are operationally heavy)

---

## Problem Statement

The Lavandula crawler silently drops PDF links hosted on CDN domains that differ from the org's own eTLD+1. Two independent blocking points compound the problem:

1. **`candidate_filter.py` (line 241)**: During link discovery, any cross-origin link that doesn't match the narrow CMS-subdomain heuristic (`is_cms_match`) is silently `return None`'d — never logged, never fetched, never recorded in `fetch_log`.

2. **`redirect_policy.py` (`_allowed()`)**: During fetch, if a redirect chain passes through a host whose eTLD+1 is not in the seed's domain or the static `HOSTING_PLATFORMS` frozenset, the fetch is blocked with `cross_origin_blocked`.

The `HOSTING_PLATFORMS` allowlist (currently 11 entries) cannot scale to cover the long tail of CDN/CMS hosting domains used by nonprofits. Key missing domains include:
- `squarespace.com` (static1.squarespace.com — 22K+ URLs)
- `finalsite.net` (resources.finalsite.net — 483 URLs)
- `wildapricot.com` (cdn.wildapricot.com — 311 URLs)
- `filesusr.com` (Wix user files — 500+ URLs)
- `sharpschool.com`, `speakcdn.com`, `ymaws.com`, and many more

### Impact

- **31,040 PDF URLs** already discovered but blocked at fetch time — sitting in `fetch_log` with `status = 'cross_origin_blocked'`
- **~40,278 orgs** whose candidate pages were scanned but all PDF links were silently dropped by `candidate_filter.py` — these need re-scanning with the fixed logic
- Estimated **50K–80K PDFs** missing from the corpus due to this issue

### Root Cause

The current design assumes "same-origin = trustworthy" and uses a static allowlist for exceptions. In practice, the majority of nonprofits host their PDFs on third-party CDNs (Squarespace, Wix, WordPress.com, Finalsite, etc.). The allowlist approach is fundamentally unscalable.

---

## Goals

1. **Permanent engine fix**: Eliminate the static allowlist as the sole cross-origin gate. Replace with a content-type validation approach: if a link is found on a page belonging to the org's own domain and the target responds with `Content-Type: application/pdf`, accept it.
2. **Pass 1 recovery**: Re-fetch the 31K PDF URLs already known in `fetch_log` with `cross_origin_blocked` status.
3. **Pass 2 recovery**: Re-scan HTML pages for ~40K orgs to discover CDN-hosted links that were previously dropped silently by `candidate_filter.py`.

---

## Non-Goals

- Accepting non-PDF cross-origin resources (HTML pages, images, JS bundles)
- Removing the HOSTING_PLATFORMS allowlist entirely (it still provides fast-path acceptance without a HEAD request)
- Changing the redirect chain length limit (MAX_REDIRECTS = 5 is fine)
- Re-crawling orgs that already have PDFs in the corpus (only target orgs with gaps)

---

## Technical Implementation

### Part 1: Engine Fix — `candidate_filter.py`

**Current behavior (line 241)**:
```python
if is_cross_origin and not is_cms_match:
    return None  # silently dropped
```

**New behavior**:
```python
if is_cross_origin and not is_cms_match:
    if _pdf_like(href):
        # Cross-origin PDF found on org's own page — accept as candidate
        # with reduced score (will be validated at fetch time via Content-Type)
        pass  # fall through to normal scoring
    else:
        # Log the drop — DEBUG normally, escalates to WARNING if volume
        # exceeds CROSS_ORIGIN_DROP_ALERT_THRESHOLD per org per run
        _log_cross_origin_drop(href, seed_etld1)
        return None
```

The key insight: **the referring page context IS the authorization**. If an org's own page links to a PDF on a CDN, that's the org choosing to host there. We trust the org's page, not the CDN domain.

Acceptance criteria for cross-origin PDFs in candidate_filter:
- Link must have a PDF-like extension (`.pdf` in path, or known PDF query patterns)
- Link must be discovered on a page belonging to the seed org's own domain
- The candidate is scored normally but flagged as `cross_origin_candidate = True` for logging

### Part 2: Engine Fix — `redirect_policy.py`

**Current behavior**: Every hop in a redirect chain must be in seed eTLD+1 OR `HOSTING_PLATFORMS`. Any other hop → `cross_origin_blocked`.

**New behavior**: Add a third acceptance path:

```python
def _allowed(host: str, seed_etld1: str, *, content_type_hint: str | None = None) -> bool:
    host_e = etld1(host)
    if host_e == seed_etld1:
        return True
    if host_e in config.HOSTING_PLATFORMS:
        return True
    # NEW: if this is a PDF fetch (known from candidate or HEAD response),
    # allow the redirect as long as final destination serves application/pdf
    if content_type_hint == "application/pdf":
        return True
    return False
```

However, the redirect policy is checked per-hop BEFORE the response body arrives. The cleaner approach:

**Option A (recommended)**: Make `check_redirect_chain` accept an `is_pdf_candidate` flag. When True, hops through known CDN infrastructure are allowed, and a single cross-origin hop to an unknown domain is tolerated (common pattern: org.com → CDN-specific shortener → CDN storage). Each cross-origin hop is logged at INFO for monitoring.

```python
def check_redirect_chain(
    chain: Iterable[str],
    *,
    seed_etld1: str,
    is_pdf_candidate: bool = False,
) -> RedirectCheckResult:
    urls = list(chain)
    if not urls:
        return RedirectCheckResult(ok=True)
    if len(urls) - 1 > config.MAX_REDIRECTS:
        return RedirectCheckResult(ok=False, reason="server_error", note="redirect_chain_too_long")

    unknown_hops = 0
    for url in urls:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if not _allowed(host, seed_etld1):
            if is_pdf_candidate:
                unknown_hops += 1
                logger.info("pdf_candidate_cross_hop", extra={"host": host, "seed": seed_etld1})
                if unknown_hops > config.MAX_UNKNOWN_HOPS:  # default: 2
                    return RedirectCheckResult(ok=False, reason="cross_origin_blocked",
                                               note=f"too many unknown hops ({unknown_hops})")
            else:
                return RedirectCheckResult(ok=False, reason="cross_origin_blocked",
                                           note=f"hop {host!r} not in seed eTLD+1 or platform allowlist")
    return RedirectCheckResult(ok=True)
```

This limits exposure: the crawler follows at most 2 unknown-domain hops (covering the common CDN redirect patterns like `bitly → cdn-shortener → storage`) while preventing arbitrary multi-hop traversal through unrelated infrastructure.

**Content-Type validation in the fetcher**: After following redirects, the fetcher MUST check that the final response has `Content-Type: application/pdf` (or `application/octet-stream` with a `.pdf` URL path). If not, discard the response and log `content_type_mismatch`.

### Part 3: Fetcher Content-Type Gate

Add a post-redirect validation step in the fetch pipeline:

```python
# In the fetcher, after following redirects for pdf candidates:
if candidate.cross_origin_candidate:
    ct = response.headers.get("content-type", "").split(";")[0].strip().lower()
    url_is_pdf = candidate.url.lower().rstrip("/").endswith(".pdf")
    allowed_types = {"application/pdf"}
    if url_is_pdf:
        allowed_types.add("application/octet-stream")
    if ct not in allowed_types:
        # Not actually a PDF — reject and update mismatch counter
        log_fetch(candidate, status="content_type_mismatch", note=f"got {ct}")
        _increment_mismatch_counter(candidate.host_etld1)
        return None
```

This ensures we never store non-PDF content even if the URL looked like a PDF.

**Per-domain mismatch throttling**: Track `content_type_mismatch` counts per eTLD+1. If a domain produces more than `config.MISMATCH_THROTTLE_THRESHOLD` (default: 5) mismatches within a crawl run, temporarily skip further cross-origin PDF candidates from that domain for the remainder of the run. This prevents resource exhaustion from domains that consistently serve non-PDF content at `.pdf` URLs.

### Part 4: Pass 1 Recovery — Re-fetch Known URLs

A management command that:
1. Queries `fetch_log` for rows where `status = 'cross_origin_blocked'` and the URL ends in `.pdf`
2. Groups by org (using `seed_url` → org mapping)
3. For each URL, fetches directly (no candidate_filter needed — URL is already known)
4. Follows redirects with the relaxed policy (`is_pdf_candidate=True`)
5. Validates Content-Type of final response
6. On success: stores PDF in S3, updates `fetch_log` status to `success`, inserts into `corpus`
7. On failure: updates `fetch_log` status to the appropriate error

**Command**: `python -m lavandula.reports.tools.recovery_pass1`

Arguments:
- `--batch-size` (default 1000): number of URLs to process per batch
- `--max-urls` (required): hard cap — must be set explicitly, or use `--no-limit` to bypass
- `--no-limit`: explicitly opt out of the cap (requires confirmation intent)
- `--dry-run`: log what would be fetched without actually fetching
- `--state-filter` (default all states): limit to specific states for phased rollout

**Operational characteristics**:
- Runs on existing t3.large (cloud2) — no GPU needed
- Rate limiting: respect existing per-domain delays (1s between requests to same host)
- Exponential backoff on HTTP 429/5xx (base 2s, max 60s) — reuses existing fetcher retry logic
- Idempotent: skip URLs already successfully fetched
- Resume-safe: processes in fetch_log ID order, can restart from last processed ID

### Part 5: Pass 2 Recovery — Re-scan for Dropped Links

A management command that:
1. Identifies orgs that have pages in `fetch_log` with `status = 'success'` and `content_type LIKE 'text/html%'` but zero PDF candidates (or only `cross_origin_blocked` PDFs)
2. For each such org, re-parses the already-stored HTML pages (from S3 or fetch_log body cache if available) using the **fixed** `candidate_filter.py`
3. Any newly-discovered PDF candidates are queued for fetch via the normal pipeline

**Two sub-approaches**:

**5a. Re-parse cached HTML** (preferred if HTML is cached):
- Check if HTML bodies are stored in S3 or locally
- Re-run candidate extraction with fixed filter
- Much faster — no network requests needed for discovery

**5b. Re-crawl org pages** (fallback if HTML not cached):
- Re-fetch the org's pages (homepage + report-anchor subpages)
- Run candidate extraction with fixed filter
- Slower but guaranteed to work

**Command**: `python -m lavandula.reports.tools.recovery_pass2`

Arguments:
- `--batch-size` (default 500): orgs per batch
- `--max-orgs` (required): hard cap — must be set explicitly, or use `--no-limit` to bypass
- `--no-limit`: explicitly opt out of the cap (requires confirmation intent)
- `--dry-run`: discover candidates without fetching
- `--state-filter`: limit to specific states
- `--source {cached,recrawl}`: whether to use cached HTML or re-crawl

**Note**: Pass 2 discovered candidates feed into the normal fetch pipeline (which now has the engine fix). They don't need special handling beyond discovery.

---

## New Config Constants

Add to `lavandula/reports/config.py`:
- `MAX_UNKNOWN_HOPS = 2` — max cross-origin redirect hops through non-allowlisted domains for PDF candidates
- `MISMATCH_SLOW_THRESHOLD = 3` — add inter-fetch delay for a domain after this many content_type_mismatches
- `MISMATCH_BLOCK_THRESHOLD = 10` — skip domain entirely after this many content_type_mismatches per run
- `CROSS_ORIGIN_DROP_ALERT_THRESHOLD = 50` — escalate logging from DEBUG to WARNING if an org exceeds this many non-PDF cross-origin drops in a single run

---

## Data Model Changes

### New columns on `fetch_log`

None required — existing `status` and `note` fields suffice:
- `status = 'cross_origin_blocked'` already marks Pass 1 targets
- `status = 'content_type_mismatch'` (new status value) for rejected non-PDFs

### New flag on candidate records (in-memory)

Add `cross_origin_candidate: bool` to the candidate dataclass/dict. This flag:
- Is set by `candidate_filter.py` when accepting a cross-origin PDF
- Is read by the fetcher to enable relaxed redirect policy
- Is read by the fetcher to enforce Content-Type validation
- Does NOT need to be persisted — it's a pipeline-internal flag

---

## Security Considerations

### Threat: Open redirect exploitation
**Risk**: An attacker could craft a redirect chain that leads to a non-PDF resource if we blindly follow all redirects for PDF candidates.
**Mitigation**: Content-Type validation gate (Part 3). Even if redirects are followed, the response MUST be `application/pdf`. Non-PDF responses are discarded.

### Threat: PDF-extension URL serving malicious HTML
**Risk**: A URL ending in `.pdf` could serve HTML with embedded scripts.
**Mitigation**: Content-Type check rejects anything that isn't `application/pdf` (or `application/octet-stream` only when the URL path ends in `.pdf`). Even if stored, PDFs are processed by Docling on isolated ephemeral GPU instances — no browser execution context. S3 bucket uses server-side encryption (SSE-S3) at rest.

### Threat: Unbounded fetching from arbitrary domains
**Risk**: With relaxed cross-origin policy, the crawler could be directed to fetch from unintended targets via redirect chains.
**Mitigation**: 
- Only PDF-like URLs (`.pdf` extension) get relaxed treatment
- Only URLs found on the org's OWN pages qualify
- Rate limiting per domain remains in effect
- MAX_REDIRECTS (5) still caps chain length
- MAX_UNKNOWN_HOPS (2) limits traversal through non-allowlisted domains — prevents arbitrary multi-hop chains while supporting common CDN redirect patterns
- Each unknown-domain hop is logged at INFO for monitoring
- Content-Type gate ensures we only store actual PDFs
- Per-domain mismatch throttling backs off domains that consistently serve non-PDFs

### Threat: Recovery pass overwhelming target servers
**Risk**: Pass 1 re-fetches 31K URLs, potentially hammering CDN servers.
**Mitigation**: Existing per-domain rate limiting. CDNs (CloudFront, Squarespace, etc.) can easily handle 1 req/sec. `--batch-size` and `--max-urls` provide operational control.

---

## Acceptance Criteria

1. **Engine fix verified**: After deploying the fix, a test crawl of CanCare (cancare.org) discovers and successfully fetches the impact report from `cdn.prod.website-files.com`
2. **Pass 1 recovery**: Command exists, runs idempotently, successfully re-fetches previously-blocked PDFs with correct Content-Type validation
3. **Pass 2 recovery**: Command exists, discovers previously-dropped candidates from cached/re-crawled HTML pages
4. **No regression**: Existing crawl behavior for same-origin PDFs is unchanged
5. **Logging**: All cross-origin PDF decisions are logged (no more silent drops for PDFs; non-PDF cross-origin drops logged at DEBUG level)
6. **Safety caps**: Both recovery commands require `--max-urls`/`--max-orgs` or explicit `--no-limit` flag

---

## Rollout Plan

1. Deploy engine fix to crawler
2. Run Pass 1 with `--dry-run` to verify URL counts match expectations (~31K)
3. Run Pass 1 with `--max-urls 1000` on a single state to validate
4. Run Pass 1 fully
5. Run Pass 2 with `--dry-run` to quantify newly-discoverable candidates
6. Run Pass 2 in state-by-state batches
7. Monitor corpus growth and verify data quality on new PDFs

---

## Traps to Avoid

1. **Don't remove HOSTING_PLATFORMS**: Keep it as a fast-path — known CDNs skip the Content-Type check overhead. The new logic is additive.
2. **Don't trust URL extension alone**: A `.pdf` URL can serve anything. Always validate Content-Type on the response.
3. **Don't re-fetch already-successful URLs**: Pass 1 must check `fetch_log` for existing success rows before re-fetching.
4. **Don't process non-PDF orgs in Pass 2**: Only target orgs with zero PDFs or only cross_origin_blocked PDFs — don't re-scan orgs that already have a healthy corpus.
5. **Don't skip rate limiting for recovery passes**: CDN or not, respect per-domain delays.
