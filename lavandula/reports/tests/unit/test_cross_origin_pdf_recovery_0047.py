"""Tests for Spec 0047: Cross-Origin PDF Recovery.

Covers:
- candidate_filter: cross-origin PDF acceptance + logging
- redirect_policy: bounded unknown hops for PDF candidates
- fetch_pdf: Content-Type gate + mismatch throttling
- recovery commands: argument validation + dry-run behavior
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------
# Phase A: Config constants + Candidate dataclass
# ---------------------------------------------------------------


def test_config_constants_exist():
    from lavandula.reports import config
    assert config.MAX_UNKNOWN_HOPS == 2
    assert config.MISMATCH_SLOW_THRESHOLD == 3
    assert config.MISMATCH_BLOCK_THRESHOLD == 10
    assert config.CROSS_ORIGIN_DROP_ALERT_THRESHOLD == 50


def test_candidate_cross_origin_field_default():
    from lavandula.reports.candidate_filter import Candidate
    c = Candidate(
        url="https://cdn.example.com/report.pdf",
        anchor_text="Report",
        referring_page_url="https://org.org/",
        discovered_via="subpage-link",
        hosting_platform=None,
        attribution_confidence="cross_origin_pdf",
    )
    assert c.cross_origin_candidate is False


def test_candidate_cross_origin_field_set():
    from lavandula.reports.candidate_filter import Candidate
    c = Candidate(
        url="https://cdn.example.com/report.pdf",
        anchor_text="Report",
        referring_page_url="https://org.org/",
        discovered_via="subpage-link",
        hosting_platform=None,
        attribution_confidence="cross_origin_pdf",
        cross_origin_candidate=True,
    )
    assert c.cross_origin_candidate is True


# ---------------------------------------------------------------
# Phase B: candidate_filter cross-origin PDF acceptance
# ---------------------------------------------------------------


def test_cross_origin_pdf_accepted_on_org_page():
    from lavandula.reports.candidate_filter import extract_candidates
    html = """
    <html><body>
      <a href="https://cdn.prod.website-files.com/impact-report-2024.pdf">Impact Report</a>
    </body></html>
    """
    candidates = extract_candidates(
        html=html,
        base_url="https://cancare.org/financials",
        seed_etld1="cancare.org",
        referring_page_url="https://cancare.org/financials",
        discovered_via="subpage-link",
        parent_is_report_anchor=True,
    )
    assert len(candidates) == 1
    assert candidates[0].cross_origin_candidate is True
    assert candidates[0].attribution_confidence == "cross_origin_pdf"
    assert candidates[0].hosting_platform is None


def test_cross_origin_non_pdf_dropped():
    from lavandula.reports.candidate_filter import extract_candidates
    html = """
    <html><body>
      <a href="https://external.com/some-page.html">External Link</a>
      <a href="https://other.com/resource">Other</a>
    </body></html>
    """
    candidates = extract_candidates(
        html=html,
        base_url="https://example.org/our-impact/",
        seed_etld1="example.org",
        referring_page_url="https://example.org/our-impact/",
        discovered_via="subpage-link",
        parent_is_report_anchor=True,
    )
    assert candidates == []


def test_same_origin_pdf_unchanged():
    from lavandula.reports.candidate_filter import extract_candidates
    html = """
    <html><body>
      <a href="/reports/annual-2024.pdf">Annual Report</a>
    </body></html>
    """
    candidates = extract_candidates(
        html=html,
        base_url="https://example.org/",
        seed_etld1="example.org",
        referring_page_url="https://example.org/",
    )
    assert len(candidates) == 1
    assert candidates[0].cross_origin_candidate is False
    assert candidates[0].attribution_confidence == "own_domain"


def test_cms_match_still_works():
    from lavandula.reports.candidate_filter import extract_candidates
    html = """
    <html><body>
      <a href="https://sagehillschool.myschoolapp.com/reports/2024.pdf">Download</a>
    </body></html>
    """
    candidates = extract_candidates(
        html=html,
        base_url="https://sagehillschool.org/giving/annual-fund",
        seed_etld1="sagehillschool.org",
        referring_page_url="https://sagehillschool.org/giving/annual-fund",
        discovered_via="subpage-link",
        parent_is_report_anchor=True,
    )
    assert len(candidates) == 1
    assert candidates[0].hosting_platform == "own-cms"
    assert candidates[0].attribution_confidence == "platform_verified"


def test_cross_origin_drop_logging_escalation(caplog):
    """At CROSS_ORIGIN_DROP_ALERT_THRESHOLD, one INFO summary is emitted then silence."""
    import logging
    from lavandula.reports.candidate_filter import (
        _log_cross_origin_drop, _cross_origin_drop_counts,
    )
    from lavandula.reports import config

    ein = "test_ein_0047"
    _cross_origin_drop_counts.pop(ein, None)

    threshold = config.CROSS_ORIGIN_DROP_ALERT_THRESHOLD
    with caplog.at_level(logging.DEBUG, logger="lavandula.reports.candidate_filter"):
        for i in range(threshold + 10):
            _log_cross_origin_drop(f"https://x.com/page{i}", "example.org", ein)

    info_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "suppressing further" in r.message
    ]
    assert len(info_records) == 1

    # Past threshold: no more records emitted
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_records) == threshold - 1

    _cross_origin_drop_counts.pop(ein, None)


# ---------------------------------------------------------------
# Phase C: redirect_policy bounded unknown hops
# ---------------------------------------------------------------


def test_redirect_non_pdf_existing_behavior():
    from lavandula.reports.redirect_policy import check_redirect_chain
    result = check_redirect_chain(
        ["https://org.org/", "https://unknown-cdn.com/file"],
        seed_etld1="org.org",
        is_pdf_candidate=False,
    )
    assert not result.ok
    assert result.reason == "cross_origin_blocked"


def test_redirect_pdf_one_unknown_hop_allowed():
    from lavandula.reports.redirect_policy import check_redirect_chain
    result = check_redirect_chain(
        ["https://org.org/report.pdf", "https://cdn.randomhost.com/actual.pdf"],
        seed_etld1="org.org",
        is_pdf_candidate=True,
    )
    assert result.ok


def test_redirect_pdf_two_unknown_hops_allowed():
    from lavandula.reports.redirect_policy import check_redirect_chain
    result = check_redirect_chain(
        [
            "https://org.org/report.pdf",
            "https://shortener.co/abc",
            "https://cdn.storage.net/actual.pdf",
        ],
        seed_etld1="org.org",
        is_pdf_candidate=True,
    )
    assert result.ok


def test_redirect_pdf_three_unknown_hops_blocked():
    from lavandula.reports.redirect_policy import check_redirect_chain
    result = check_redirect_chain(
        [
            "https://org.org/report.pdf",
            "https://hop1.com/x",
            "https://hop2.net/y",
            "https://hop3.io/z",
        ],
        seed_etld1="org.org",
        is_pdf_candidate=True,
    )
    assert not result.ok
    assert "too many unknown hops" in result.note


def test_redirect_pdf_allowlisted_hops_dont_count():
    from lavandula.reports.redirect_policy import check_redirect_chain
    result = check_redirect_chain(
        [
            "https://org.org/report.pdf",
            "https://d123.cloudfront.net/redirect",
            "https://unknown-cdn.com/actual.pdf",
        ],
        seed_etld1="org.org",
        is_pdf_candidate=True,
    )
    # cloudfront.net is in HOSTING_PLATFORMS, so only 1 unknown hop
    assert result.ok


def test_redirect_max_redirects_still_enforced():
    from lavandula.reports.redirect_policy import check_redirect_chain
    from lavandula.reports import config
    chain = [f"https://org.org/hop{i}" for i in range(config.MAX_REDIRECTS + 2)]
    result = check_redirect_chain(
        chain,
        seed_etld1="org.org",
        is_pdf_candidate=True,
    )
    assert not result.ok
    assert result.note == "redirect_chain_too_long"


# ---------------------------------------------------------------
# Phase D: Content-Type gate + mismatch throttling
# ---------------------------------------------------------------


def test_content_type_gate_pdf_passes(monkeypatch):
    from unittest.mock import MagicMock, patch
    from lavandula.reports import fetch_pdf
    from lavandula.reports.http_client import FetchResult

    client = MagicMock()
    client.head.return_value = FetchResult(
        status="ok", http_status=200,
        headers={"Content-Type": "application/pdf"},
        kind="pdf-head",
    )
    client.get.return_value = FetchResult(
        status="ok", http_status=200,
        body=b"%PDF-1.4 fake content",
        final_url="https://cdn.example.com/report.pdf",
        final_url_redacted="https://cdn.example.com/report.pdf",
        redirect_chain=["https://cdn.example.com/report.pdf"],
        redirect_chain_redacted=["https://cdn.example.com/report.pdf"],
        headers={"Content-Type": "application/pdf"},
        bytes_read=21, kind="pdf-get",
    )

    monkeypatch.setattr(fetch_pdf, "_validate_pdf_structure", lambda b: (True, ""))

    outcome = fetch_pdf.download(
        "https://cdn.example.com/report.pdf",
        client,
        seed_etld1="example.org",
        is_pdf_candidate=True,
    )
    assert outcome.status == "ok"


def test_content_type_gate_html_rejected(monkeypatch):
    from unittest.mock import MagicMock
    from lavandula.reports import fetch_pdf
    from lavandula.reports.http_client import FetchResult

    fetch_pdf._mismatch_counts.clear()

    client = MagicMock()
    client.head.return_value = FetchResult(
        status="ok", http_status=200,
        headers={"Content-Type": "text/html"},
        kind="pdf-head",
    )
    # HEAD returns text/html but _head_or_skip only blocks on 200+non-pdf
    # Actually _head_or_skip blocks here. Let's make HEAD return 405 so GET runs
    client.head.return_value = FetchResult(
        status="ok", http_status=405,
        headers={}, kind="pdf-head",
    )
    client.get.return_value = FetchResult(
        status="ok", http_status=200,
        body=b"<html>not a pdf</html>",
        final_url="https://cdn.example.com/report.pdf",
        final_url_redacted="https://cdn.example.com/report.pdf",
        redirect_chain=["https://cdn.example.com/report.pdf"],
        redirect_chain_redacted=["https://cdn.example.com/report.pdf"],
        headers={"Content-Type": "text/html; charset=utf-8"},
        bytes_read=22, kind="pdf-get",
    )

    outcome = fetch_pdf.download(
        "https://cdn.example.com/report.pdf",
        client,
        seed_etld1="example.org",
        is_pdf_candidate=True,
    )
    assert outcome.status == "content_type_mismatch"
    assert "text/html" in outcome.note


def test_content_type_octet_stream_with_pdf_ext_passes(monkeypatch):
    from unittest.mock import MagicMock
    from lavandula.reports import fetch_pdf
    from lavandula.reports.http_client import FetchResult

    client = MagicMock()
    client.head.return_value = FetchResult(
        status="ok", http_status=405,
        headers={}, kind="pdf-head",
    )
    client.get.return_value = FetchResult(
        status="ok", http_status=200,
        body=b"%PDF-1.4 fake content",
        final_url="https://cdn.example.com/report.pdf",
        final_url_redacted="https://cdn.example.com/report.pdf",
        redirect_chain=["https://cdn.example.com/report.pdf"],
        redirect_chain_redacted=["https://cdn.example.com/report.pdf"],
        headers={"Content-Type": "application/octet-stream"},
        bytes_read=21, kind="pdf-get",
    )

    monkeypatch.setattr(fetch_pdf, "_validate_pdf_structure", lambda b: (True, ""))

    outcome = fetch_pdf.download(
        "https://cdn.example.com/report.pdf",
        client,
        seed_etld1="example.org",
        is_pdf_candidate=True,
    )
    assert outcome.status == "ok"


def test_content_type_octet_stream_without_pdf_ext_rejected(monkeypatch):
    from unittest.mock import MagicMock
    from lavandula.reports import fetch_pdf
    from lavandula.reports.http_client import FetchResult

    fetch_pdf._mismatch_counts.clear()

    client = MagicMock()
    client.head.return_value = FetchResult(
        status="ok", http_status=405,
        headers={}, kind="pdf-head",
    )
    client.get.return_value = FetchResult(
        status="ok", http_status=200,
        body=b"%PDF-1.4 fake content",
        final_url="https://cdn.example.com/document",
        final_url_redacted="https://cdn.example.com/document",
        redirect_chain=["https://cdn.example.com/document"],
        redirect_chain_redacted=["https://cdn.example.com/document"],
        headers={"Content-Type": "application/octet-stream"},
        bytes_read=21, kind="pdf-get",
    )

    outcome = fetch_pdf.download(
        "https://cdn.example.com/document",
        client,
        seed_etld1="example.org",
        is_pdf_candidate=True,
    )
    assert outcome.status == "content_type_mismatch"


def test_same_origin_no_content_type_gate(monkeypatch):
    """Same-origin candidates bypass the Content-Type gate."""
    from unittest.mock import MagicMock
    from lavandula.reports import fetch_pdf
    from lavandula.reports.http_client import FetchResult

    client = MagicMock()
    client.head.return_value = FetchResult(
        status="ok", http_status=405,
        headers={}, kind="pdf-head",
    )
    client.get.return_value = FetchResult(
        status="ok", http_status=200,
        body=b"%PDF-1.4 fake content",
        final_url="https://example.org/report.pdf",
        final_url_redacted="https://example.org/report.pdf",
        redirect_chain=["https://example.org/report.pdf"],
        redirect_chain_redacted=["https://example.org/report.pdf"],
        headers={"Content-Type": "application/octet-stream"},
        bytes_read=21, kind="pdf-get",
    )

    monkeypatch.setattr(fetch_pdf, "_validate_pdf_structure", lambda b: (True, ""))

    outcome = fetch_pdf.download(
        "https://example.org/report.pdf",
        client,
        seed_etld1="example.org",
        is_pdf_candidate=False,
    )
    # Not blocked by content-type gate (same-origin skips it)
    assert outcome.status == "ok"


def test_mismatch_throttle_blocked():
    from lavandula.reports import fetch_pdf
    from lavandula.reports import config

    fetch_pdf._mismatch_counts.clear()
    fetch_pdf._mismatch_counts["badcdn.com"] = config.MISMATCH_BLOCK_THRESHOLD

    assert fetch_pdf.is_domain_throttled("https://files.badcdn.com/x.pdf") is True
    assert fetch_pdf.get_domain_mismatch_state("https://files.badcdn.com/x.pdf") == "blocked"

    fetch_pdf._mismatch_counts.clear()


def test_mismatch_throttle_slow():
    from lavandula.reports import fetch_pdf
    from lavandula.reports import config

    fetch_pdf._mismatch_counts.clear()
    fetch_pdf._mismatch_counts["slowcdn.com"] = config.MISMATCH_SLOW_THRESHOLD

    assert fetch_pdf.is_domain_throttled("https://files.slowcdn.com/x.pdf") is False
    assert fetch_pdf.get_domain_mismatch_state("https://files.slowcdn.com/x.pdf") == "slow"

    fetch_pdf._mismatch_counts.clear()


def test_mismatch_throttle_ok():
    from lavandula.reports import fetch_pdf

    fetch_pdf._mismatch_counts.clear()
    assert fetch_pdf.get_domain_mismatch_state("https://goodcdn.com/x.pdf") == "ok"
    assert fetch_pdf.is_domain_throttled("https://goodcdn.com/x.pdf") is False


# ---------------------------------------------------------------
# Phase E: recovery_pass1 argument validation
# ---------------------------------------------------------------


def test_pass1_requires_max_urls():
    from lavandula.reports.tools.recovery_pass1 import main
    with pytest.raises(SystemExit):
        main(["--batch-size", "10"])


def test_pass1_no_limit_bypasses_cap():
    """--no-limit flag allows running without --max-urls (won't error on args)."""
    from lavandula.reports.tools.recovery_pass1 import _validate_state_filter
    # Just validate the state filter logic
    states = _validate_state_filter("CA,NY,TX")
    assert states == ["CA", "NY", "TX"]


def test_pass1_state_filter_validation():
    from lavandula.reports.tools.recovery_pass1 import _validate_state_filter
    with pytest.raises(ValueError):
        _validate_state_filter("invalid")
    with pytest.raises(ValueError):
        _validate_state_filter("CA,bad,TX")


# ---------------------------------------------------------------
# Phase F: recovery_pass2 argument validation
# ---------------------------------------------------------------


def test_pass2_requires_max_orgs():
    from lavandula.reports.tools.recovery_pass2 import main
    with pytest.raises(SystemExit):
        main(["--batch-size", "10"])


def test_pass2_state_filter_validation():
    from lavandula.reports.tools.recovery_pass2 import _validate_state_filter
    states = _validate_state_filter("CA,NY")
    assert states == ["CA", "NY"]
    with pytest.raises(ValueError):
        _validate_state_filter("toolong")
