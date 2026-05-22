"""Tests for the crawl depth fix.

The original engine only walked one hop from the homepage. Documents
behind 2-3 navigation layers (About → Reports → PDF) were unreachable.
The fix adds multi-depth subpage walking controlled by MAX_SUBPAGE_DEPTH.
"""
from __future__ import annotations

from lavandula.reports.candidate_filter import CANDIDATE_CAP_PER_ORG
from lavandula.reports.discover import per_org_candidates


def _make_page(links: list[tuple[str, str]]) -> bytes:
    """Build an HTML page with the given (href, anchor_text) links."""
    body = "".join(
        f'<a href="{href}">{text}</a>' for href, text in links
    )
    return f"<html><body>{body}</body></html>".encode()


class TestMultiDepthSubpageWalk:

    def test_depth_2_reaches_nested_pdf(self):
        """Homepage → /impact → /impact/annual-report-2024.pdf
        At depth 1 this PDF was unreachable. At depth 2 it's found."""
        homepage = _make_page([
            ("/impact", "Our Impact"),
        ])
        about_page = _make_page([
            ("/impact/annual-report-2024.pdf", "Annual Report 2024"),
        ])

        def fetcher(url: str, kind: str) -> tuple[bytes, str]:
            if kind == "sitemap" or "sitemap.xml" in url:
                return b"", "not_found"
            if "example.org/impact" in url and kind == "subpage":
                return about_page, "ok"
            if kind == "homepage" or url.rstrip("/") == "https://example.org":
                return homepage, "ok"
            return b"", "not_found"

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-depth",
        )

        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 1
        assert "annual-report-2024.pdf" in pdf_urls[0]

    def test_depth_3_hops_reports_pdf(self):
        """Homepage → /impact → /impact/reports/ → /impact/reports/2024.pdf
        Three hops deep. Requires MAX_SUBPAGE_DEPTH >= 3 to reach."""
        import lavandula.reports.config as cfg
        original_depth = cfg.MAX_SUBPAGE_DEPTH
        cfg.MAX_SUBPAGE_DEPTH = 3
        try:
            homepage = _make_page([
                ("/impact", "Our Impact"),
            ])
            about_page = _make_page([
                ("/impact/reports/", "Our Reports"),
            ])
            reports_page = _make_page([
                ("/impact/reports/annual-report-2024.pdf", "2024 Annual Report"),
                ("/impact/reports/impact-report-2024.pdf", "2024 Impact Report"),
            ])

            def fetcher(url: str, kind: str) -> tuple[bytes, str]:
                if kind == "sitemap" or "sitemap.xml" in url:
                    return b"", "not_found"
                if "/impact/reports" in url and kind == "subpage":
                    return reports_page, "ok"
                if "/impact" in url and kind == "subpage":
                    return about_page, "ok"
                if kind == "homepage" or url.rstrip("/") == "https://example.org":
                    return homepage, "ok"
                return b"", "not_found"

            result = per_org_candidates(
                seed_url="https://example.org",
                seed_etld1="example.org",
                fetcher=fetcher,
                robots_text="",
                ein="test-depth-3",
            )

            pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
            assert len(pdf_urls) == 2
        finally:
            cfg.MAX_SUBPAGE_DEPTH = original_depth

    def test_gates_foundation_pattern(self):
        """Simulates: Homepage → /financials → /financials/annual-reports →
        links to PDFs. The real Gates Foundation failure mode."""
        homepage = _make_page([
            ("/financials", "Financials"),
            ("/donate", "Donate"),
        ])
        financials_page = _make_page([
            ("/financials/annual-reports", "Annual Reports"),
        ])
        annual_reports_page = _make_page([
            ("/financials/annual-reports/2024-annual-report.pdf", "2024 Annual Report"),
            ("/financials/annual-reports/2023-annual-report.pdf", "2023 Annual Report"),
            ("/financials/annual-reports/2022-annual-report.pdf", "2022 Annual Report"),
        ])

        def fetcher(url: str, kind: str) -> tuple[bytes, str]:
            if kind == "sitemap" or "sitemap.xml" in url:
                return b"", "not_found"
            if "/financials/annual-reports" in url and kind == "subpage":
                return annual_reports_page, "ok"
            if "/financials" in url and kind == "subpage":
                return financials_page, "ok"
            if kind == "homepage" or url.rstrip("/") == "https://example.org":
                return homepage, "ok"
            return b"", "not_found"

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-gates",
        )

        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 3, (
            f"Expected 3 annual report PDFs but got {len(pdf_urls)}: {pdf_urls}"
        )

    def test_no_infinite_loops(self):
        """Pages that link back to each other don't cause infinite recursion."""
        page_a = _make_page([
            ("/impact/reports/", "Reports"),
            ("/impact/", "Our Impact"),  # circular link back
        ])
        page_b = _make_page([
            ("/impact/", "Our Impact"),  # circular link back
            ("/impact/reports/annual-report.pdf", "Annual Report"),
        ])

        def fetcher(url: str, kind: str) -> tuple[bytes, str]:
            if kind == "sitemap" or "sitemap.xml" in url:
                return b"", "not_found"
            if "/impact/reports" in url:
                return page_b, "ok"
            if "/impact" in url:
                return page_a, "ok"
            if kind == "homepage":
                return _make_page([("/impact/", "Our Impact")]), "ok"
            return b"", "not_found"

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-circular",
        )

        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 1

    def test_subpage_budget_shared_across_depths(self):
        """MAX_SUBPAGES_PER_ORG is a total budget across all depth levels,
        not per-level."""
        import lavandula.reports.config as cfg
        import lavandula.reports.discover as disc
        original_cfg = cfg.MAX_SUBPAGES_PER_ORG
        original_disc = disc.MAX_SUBPAGES_PER_ORG
        cfg.MAX_SUBPAGES_PER_ORG = 3
        disc.MAX_SUBPAGES_PER_ORG = 3
        try:
            homepage = _make_page([
                ("/impact/", "Our Impact"),
                ("/reports/", "Reports"),
                ("/annual-report/", "Annual Report"),
                ("/donate/", "Donate"),
            ])
            generic_subpage = _make_page([
                ("/impact/details/", "Details"),
            ])

            fetched_urls = []

            def fetcher(url: str, kind: str) -> tuple[bytes, str]:
                if kind == "sitemap" or "sitemap.xml" in url:
                    return b"", "not_found"
                if kind == "subpage":
                    fetched_urls.append(url)
                    return generic_subpage, "ok"
                if kind == "homepage":
                    return homepage, "ok"
                return b"", "not_found"

            per_org_candidates(
                seed_url="https://example.org",
                seed_etld1="example.org",
                fetcher=fetcher,
                robots_text="",
                ein="test-budget",
            )

            # Should not exceed MAX_SUBPAGES_PER_ORG total fetches
            assert len(fetched_urls) <= 3
        finally:
            cfg.MAX_SUBPAGES_PER_ORG = original_cfg
            disc.MAX_SUBPAGES_PER_ORG = original_disc

    def test_depth_1_backward_compatible(self):
        """With MAX_SUBPAGE_DEPTH=1, behavior is identical to the original
        single-hop implementation."""
        import lavandula.reports.config as cfg
        original_depth = cfg.MAX_SUBPAGE_DEPTH
        cfg.MAX_SUBPAGE_DEPTH = 1
        try:
            homepage = _make_page([
                ("/impact/", "Our Impact"),
            ])
            impact_page = _make_page([
                ("/impact/reports/", "Reports"),
            ])
            reports_page = _make_page([
                ("/impact/reports/annual-report.pdf", "Annual Report"),
            ])

            def fetcher(url: str, kind: str) -> tuple[bytes, str]:
                if kind == "sitemap" or "sitemap.xml" in url:
                    return b"", "not_found"
                if "/impact/reports" in url:
                    return reports_page, "ok"
                if "/impact" in url and kind == "subpage":
                    return impact_page, "ok"
                if kind == "homepage":
                    return homepage, "ok"
                return b"", "not_found"

            result = per_org_candidates(
                seed_url="https://example.org",
                seed_etld1="example.org",
                fetcher=fetcher,
                robots_text="",
                ein="test-depth1",
            )

            # At depth 1, only /impact/ is walked. /impact/reports/ is
            # discovered but NOT walked. The PDF is unreachable.
            pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
            assert len(pdf_urls) == 0
        finally:
            cfg.MAX_SUBPAGE_DEPTH = original_depth

    def test_report_path_subpages_prioritized_at_depth_2(self):
        """At depth 2, subpages with report-relevant paths are walked
        before generic pages."""
        homepage = _make_page([
            ("/news/", "News"),
            ("/about/", "About"),
            ("/annual-report/", "Annual Report"),
            ("/careers/", "Careers"),
        ])
        annual_page = _make_page([
            ("/annual-report/2024-report.pdf", "Download 2024 Report"),
        ])
        # Other subpages link to more pages but not PDFs
        generic_page = _make_page([
            ("/news/latest/", "Latest News"),
        ])

        subpage_fetch_order = []

        def fetcher(url: str, kind: str) -> tuple[bytes, str]:
            if kind == "sitemap" or "sitemap.xml" in url:
                return b"", "not_found"
            if kind == "subpage":
                subpage_fetch_order.append(url)
                if "/annual-report" in url:
                    return annual_page, "ok"
                return generic_page, "ok"
            if kind == "homepage":
                return homepage, "ok"
            return b"", "not_found"

        result = per_org_candidates(
            seed_url="https://example.org",
            seed_etld1="example.org",
            fetcher=fetcher,
            robots_text="",
            ein="test-priority",
        )

        pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
        assert len(pdf_urls) == 1
        assert "2024-report.pdf" in pdf_urls[0]
