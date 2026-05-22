"""Tests for the slot pollution fix.

The original 30-candidate cap was applied during collection, so HTML
garbage (press releases, sitemap noise) could fill all 30 slots before
any PDFs were found. The fix separates collection (up to 200) from
output (30 after PDF-first sort), ensuring PDFs aren't crowded out.
"""
from __future__ import annotations

import pytest

from lavandula.reports.candidate_filter import (
    CANDIDATE_CAP_PER_ORG,
    Candidate,
    extract_candidates,
)
from lavandula.reports.config import CANDIDATE_COLLECTION_CAP
from lavandula.reports.discover import per_org_candidates


# ---------------------------------------------------------------------------
# Helper: build HTML with a mix of HTML pages and PDFs
# ---------------------------------------------------------------------------

def _html_with_mixed_links(n_html: int, n_pdf: int, pdf_start: int = 0) -> str:
    """Build an HTML page with n_html report-anchor HTML links followed by n_pdf PDF links."""
    lines = []
    for i in range(n_html):
        lines.append(f'<a href="/annual-report-{i}/">Annual Report {i}</a>')
    for i in range(n_pdf):
        lines.append(f'<a href="/reports/report-{pdf_start + i}.pdf">Report {pdf_start + i}</a>')
    return f"<html><body>{''.join(lines)}</body></html>"


def _html_with_only_html_links(n: int) -> str:
    lines = []
    for i in range(n):
        lines.append(f'<a href="/annual-report-{i}/">Annual Report {i}</a>')
    return f"<html><body>{''.join(lines)}</body></html>"


def _html_with_only_pdf_links(n: int) -> str:
    lines = []
    for i in range(n):
        lines.append(f'<a href="/reports/annual-{i}.pdf">Annual Report {i}</a>')
    return f"<html><body>{''.join(lines)}</body></html>"


# ---------------------------------------------------------------------------
# extract_candidates: collection cap, not output cap
# ---------------------------------------------------------------------------

class TestExtractCandidatesCollectionCap:
    def test_collects_beyond_30(self):
        """extract_candidates no longer breaks at 30 — collects all matches up to COLLECTION_CAP."""
        html = _html_with_only_pdf_links(50)
        candidates = extract_candidates(
            html=html,
            base_url="https://example.org/",
            seed_etld1="example.org",
            referring_page_url="https://example.org/",
        )
        assert len(candidates) == 50

    def test_collection_cap_enforced(self):
        """extract_candidates stops at CANDIDATE_COLLECTION_CAP."""
        html = _html_with_only_pdf_links(300)
        candidates = extract_candidates(
            html=html,
            base_url="https://example.org/",
            seed_etld1="example.org",
            referring_page_url="https://example.org/",
        )
        assert len(candidates) == CANDIDATE_COLLECTION_CAP


# ---------------------------------------------------------------------------
# per_org_candidates: PDF-first sort + final cap at 30
# ---------------------------------------------------------------------------

def _stub_fetcher(pages: dict[str, str]):
    """Return a fetcher that serves pre-built pages by URL pattern."""
    def fetcher(url: str, kind: str) -> tuple[bytes, str]:
        for pattern, body in pages.items():
            if pattern in url:
                raw = body.encode("utf-8") if isinstance(body, str) else body
                return raw, "ok"
        return b"", "not_found"
    return fetcher


class TestPerOrgPdfPriority:
    def test_pdfs_survive_html_flood(self):
        """When homepage has 40 HTML links and 10 PDFs, all 10 PDFs
        appear in the final 30-candidate output."""
        html = _html_with_mixed_links(n_html=40, n_pdf=10)
        fetcher = _stub_fetcher({"example.org": html})

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-ein",
        )

        assert len(result) <= CANDIDATE_CAP_PER_ORG
        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 10

    def test_output_capped_at_30(self):
        """Even with 200 collected, final output is <= 30."""
        html = _html_with_only_pdf_links(80)
        fetcher = _stub_fetcher({"example.org": html})

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-ein",
        )

        assert len(result) <= CANDIDATE_CAP_PER_ORG

    def test_html_only_still_works(self):
        """If an org has only HTML candidates, they still come through."""
        html = _html_with_only_html_links(15)
        fetcher = _stub_fetcher({"example.org": html})

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-ein",
        )

        assert len(result) == 15
        assert all(not c.url.endswith(".pdf") for c in result)


# ---------------------------------------------------------------------------
# Sitemap: no early-break from HTML candidates
# ---------------------------------------------------------------------------

class TestSitemapNoEarlyBreak:
    def test_sitemap_html_does_not_block_pdfs(self):
        """Sitemap with 35 HTML pages followed by 5 PDFs — previously
        the loop broke at 30 HTML candidates and never saw the PDFs."""
        sitemap_urls = []
        for i in range(35):
            sitemap_urls.append(f"https://example.org/annual-report-{i}/")
        for i in range(5):
            sitemap_urls.append(f"https://example.org/reports/doc-{i}.pdf")

        sitemap_xml = (
            '<?xml version="1.0"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc></url>" for u in sitemap_urls)
            + "</urlset>"
        )

        def fetcher(url: str, kind: str) -> tuple[bytes, str]:
            if kind == "sitemap" or "sitemap" in url:
                return sitemap_xml.encode(), "ok"
            if kind == "homepage":
                return b"<html><body></body></html>", "ok"
            return b"", "not_found"

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-ein",
        )

        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 5, f"Expected 5 PDFs but got {len(pdf_urls)}"


# ---------------------------------------------------------------------------
# Gates Foundation scenario: sitemap fills slots with press releases
# ---------------------------------------------------------------------------

class TestGatesFoundationScenario:
    def test_press_releases_dont_crowd_pdfs(self):
        """Simulates the Gates Foundation failure: sitemap has 50 press
        release HTML pages and 3 PDFs buried at the end. Previously
        the 30-cap broke before reaching the PDFs."""
        sitemap_urls = []
        for i in range(50):
            sitemap_urls.append(f"https://example.org/ideas/articles/press-release-{i}/")
        for i in range(3):
            sitemap_urls.append(f"https://example.org/about/financials/annual-report-{2022+i}.pdf")

        sitemap_xml = (
            '<?xml version="1.0"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc></url>" for u in sitemap_urls)
            + "</urlset>"
        )

        homepage_html = b"<html><body><a href='/about'>About</a></body></html>"

        def fetcher(url: str, kind: str) -> tuple[bytes, str]:
            if kind == "sitemap" or "sitemap" in url:
                return sitemap_xml.encode(), "ok"
            if kind == "homepage":
                return homepage_html, "ok"
            return b"", "not_found"

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-ein",
        )

        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 3, (
            f"Expected all 3 annual report PDFs but got {len(pdf_urls)}: "
            f"{pdf_urls}"
        )
        assert len(result) <= CANDIDATE_CAP_PER_ORG
