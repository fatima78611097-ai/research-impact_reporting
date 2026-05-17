"""Unit tests for reconcile_s3 fetch_log sha-index reduction.

Guards the perf fix that replaced a per-orphan `notes LIKE '%sha=...%'`
scan (one full scan of the multi-million-row fetch_log per orphan) with a
single-pass {sha: (ein, url_redacted)} index. The reducer MUST preserve
the old per-sha query's semantics: parse sha from notes, skip empty-ein
rows, highest-id row wins.
"""
from __future__ import annotations


def test_index_parses_sha_from_notes():
    from lavandula.reports.tools.reconcile_s3 import _index_from_fetch_log_rows

    sha = "a" * 64
    idx = _index_from_fetch_log_rows([("123456789", "https://x/y", f"sha={sha}")])
    assert idx == {sha: ("123456789", "https://x/y")}


def test_index_skips_empty_ein():
    from lavandula.reports.tools.reconcile_s3 import _index_from_fetch_log_rows

    sha = "b" * 64
    idx = _index_from_fetch_log_rows([("", "https://x", f"sha={sha}")])
    assert idx == {}


def test_index_skips_notes_without_sha():
    from lavandula.reports.tools.reconcile_s3 import _index_from_fetch_log_rows

    idx = _index_from_fetch_log_rows([("123456789", "https://x", "no sha here")])
    assert idx == {}


def test_index_handles_none_notes():
    from lavandula.reports.tools.reconcile_s3 import _index_from_fetch_log_rows

    idx = _index_from_fetch_log_rows([("123456789", "https://x", None)])
    assert idx == {}


def test_index_highest_id_wins():
    """Rows arrive id-ascending; the later row for a sha overwrites,
    matching the old query's ORDER BY id DESC LIMIT 1."""
    from lavandula.reports.tools.reconcile_s3 import _index_from_fetch_log_rows

    sha = "c" * 64
    idx = _index_from_fetch_log_rows([
        ("111111111", "https://old", f"sha={sha}"),
        ("222222222", "https://new", f"sha={sha}"),
    ])
    assert idx == {sha: ("222222222", "https://new")}
