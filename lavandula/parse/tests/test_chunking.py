"""Unit tests for lavandula.parse.chunking — section and table extraction."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Pre-inject docling mocks so the module can be imported without docling installed
sys.modules.setdefault("docling", MagicMock())
sys.modules.setdefault("docling.document_converter", MagicMock())
sys.modules.setdefault("docling.chunking", MagicMock())

from lavandula.parse.chunking import (
    extract_sections,
    extract_tables,
    get_document_metadata,
    _find_containing_section,
    _extract_heading,
    _extract_heading_level,
    _get_parent_headings,
    _scrub,
)


class TestScrub:
    """Spec 0056 — NUL/control-char stripping for all Docling-derived text
    fields that reach PostgreSQL (regression for run-32 NUL-byte failure)."""

    def test_strips_nul(self):
        assert _scrub("hello\x00world") == "helloworld"

    def test_preserves_whitespace(self):
        assert _scrub("a\nb\tc\r\n") == "a\nb\tc\r\n"

    def test_strips_other_control_chars(self):
        assert _scrub("a\x07b\x08c\x1fd\x7fe") == "abcde"

    def test_recurses_into_list(self):
        assert _scrub([["cell\x00", "ok"], ["x\x1f"]]) == [["cell", "ok"], ["x"]]

    def test_recurses_into_dict(self):
        assert _scrub({"k": "v\x00", "n": 5, "d": {"a": "b\x00"}}) == {
            "k": "v", "n": 5, "d": {"a": "b"},
        }

    def test_passes_through_non_strings(self):
        assert _scrub(None) is None
        assert _scrub(42) == 42
        assert _scrub(3.14) == 3.14


def _make_chunk(text="Sample text", headings=None, pages=None):
    """Create a mock chunk with the expected Docling structure."""
    chunk = MagicMock()
    chunk.text = text
    meta = MagicMock()
    meta.headings = headings
    if pages:
        doc_items = []
        for page in pages:
            item = MagicMock()
            prov = MagicMock()
            prov.page_no = page
            item.prov = [prov]
            doc_items.append(item)
        meta.doc_items = doc_items
    else:
        meta.doc_items = []
    chunk.meta = meta
    return chunk


def _make_table(page_no=None, caption=None, data=None):
    """Create a mock Docling table."""
    table = MagicMock()
    if page_no:
        prov = MagicMock()
        prov.page_no = page_no
        prov.page = page_no
        table.prov = [prov]
    else:
        table.prov = None
    if caption:
        cap = MagicMock()
        cap.text = caption
        table.caption = cap
    else:
        table.caption = None

    # Mock the DataFrame that export_to_dataframe returns
    mock_df = MagicMock()
    raw = data or [["A", "B"], [1, 2]]
    mock_df.columns.tolist.return_value = raw[0] if raw else []
    mock_df.values.tolist.return_value = raw[1:] if len(raw) > 1 else []
    table.export_to_dataframe.return_value = mock_df
    table.export_to_markdown.return_value = "| A | B |\n|---|---|\n| 1 | 2 |"
    return table


class TestExtractHeading:
    def test_returns_last_heading(self):
        chunk = _make_chunk(headings=["Chapter 1", "Section A"])
        assert _extract_heading(chunk) == "Section A"

    def test_returns_none_when_no_headings(self):
        chunk = _make_chunk(headings=None)
        assert _extract_heading(chunk) is None

    def test_returns_none_when_empty_headings(self):
        chunk = _make_chunk(headings=[])
        assert _extract_heading(chunk) is None


class TestExtractHeadingLevel:
    def test_level_equals_depth(self):
        chunk = _make_chunk(headings=["Top", "Middle", "Bottom"])
        assert _extract_heading_level(chunk) == 3

    def test_single_heading_is_level_1(self):
        chunk = _make_chunk(headings=["Title"])
        assert _extract_heading_level(chunk) == 1

    def test_no_headings_returns_none(self):
        chunk = _make_chunk(headings=None)
        assert _extract_heading_level(chunk) is None


class TestGetParentHeadings:
    def test_returns_full_chain(self):
        chunk = _make_chunk(headings=["Annual Report", "Programs", "Youth Services"])
        assert _get_parent_headings(chunk) == ["Annual Report", "Programs", "Youth Services"]

    def test_empty_when_no_headings(self):
        chunk = _make_chunk(headings=None)
        assert _get_parent_headings(chunk) == []


class TestExtractSections:
    def test_extracts_sections_from_chunks(self):
        doc = MagicMock()
        chunks = [
            _make_chunk("Intro text", headings=["Introduction"], pages=[1, 2]),
            _make_chunk("Body text", headings=["Introduction", "Details"], pages=[3]),
        ]
        mock_chunker_cls = MagicMock()
        mock_chunker_cls.return_value.chunk.return_value = chunks
        sys.modules["docling.chunking"].HierarchicalChunker = mock_chunker_cls

        sections = extract_sections(doc)

        assert len(sections) == 2
        assert sections[0]["section_index"] == 0
        assert sections[0]["heading"] == "Introduction"
        assert sections[0]["heading_level"] == 1
        assert sections[0]["body_text"] == "Intro text"
        assert sections[0]["char_count"] == 10
        assert sections[0]["page_start"] == 1
        assert sections[0]["page_end"] == 2
        assert sections[0]["parent_headings"] == ["Introduction"]

        assert sections[1]["section_index"] == 1
        assert sections[1]["heading"] == "Details"
        assert sections[1]["heading_level"] == 2
        assert sections[1]["page_start"] == 3
        assert sections[1]["page_end"] == 3
        assert sections[1]["parent_headings"] == ["Introduction", "Details"]

    def test_empty_document_returns_empty(self):
        doc = MagicMock()
        mock_chunker_cls = MagicMock()
        mock_chunker_cls.return_value.chunk.return_value = []
        sys.modules["docling.chunking"].HierarchicalChunker = mock_chunker_cls

        sections = extract_sections(doc)
        assert sections == []

    def test_section_without_heading(self):
        doc = MagicMock()
        chunks = [_make_chunk("Orphan text", headings=None, pages=[1])]
        mock_chunker_cls = MagicMock()
        mock_chunker_cls.return_value.chunk.return_value = chunks
        sys.modules["docling.chunking"].HierarchicalChunker = mock_chunker_cls

        sections = extract_sections(doc)
        assert sections[0]["heading"] is None
        assert sections[0]["heading_level"] is None
        assert sections[0]["parent_headings"] == []


class TestExtractTables:
    def test_extracts_table_with_section_linkage(self):
        doc = MagicMock()
        doc.tables = [_make_table(page_no=4, caption="Revenue")]

        sections = [
            {"section_index": 0, "page_start": 1, "page_end": 3},
            {"section_index": 1, "page_start": 3, "page_end": 6},
        ]

        tables = extract_tables(doc, sections)

        assert len(tables) == 1
        assert tables[0]["table_index"] == 0
        assert tables[0]["section_index"] == 1
        assert tables[0]["page_number"] == 4
        assert tables[0]["caption"] == "Revenue"
        assert tables[0]["row_count"] == 2  # header + 1 data row (from mock)
        assert tables[0]["col_count"] == 2

    def test_no_tables_returns_empty(self):
        doc = MagicMock()
        doc.tables = []
        assert extract_tables(doc, []) == []

    def test_table_before_any_section(self):
        doc = MagicMock()
        doc.tables = [_make_table(page_no=1)]

        sections = [
            {"section_index": 0, "page_start": 3, "page_end": 5},
        ]

        tables = extract_tables(doc, sections)
        assert tables[0]["section_index"] is None


class TestFindContainingSection:
    def test_finds_section_by_page_range(self):
        sections = [
            {"section_index": 0, "page_start": 1, "page_end": 3},
            {"section_index": 1, "page_start": 4, "page_end": 8},
        ]
        assert _find_containing_section(5, None, sections) == 1

    def test_picks_latest_section_on_overlap(self):
        sections = [
            {"section_index": 0, "page_start": 1, "page_end": 5},
            {"section_index": 1, "page_start": 3, "page_end": 7},
        ]
        assert _find_containing_section(4, None, sections) == 1

    def test_returns_none_when_no_page(self):
        assert _find_containing_section(None, None, []) is None

    def test_returns_none_when_no_match(self):
        sections = [{"section_index": 0, "page_start": 10, "page_end": 15}]
        assert _find_containing_section(5, None, sections) is None


class TestGetDocumentMetadata:
    def test_extracts_page_and_figure_count(self):
        doc = MagicMock()
        doc.pages = {1: None, 2: None, 3: None}
        doc.pictures = [MagicMock(), MagicMock()]
        doc.metadata = None

        meta = get_document_metadata(doc)
        assert meta["page_count"] == 3
        assert meta["figure_count"] == 2

    def test_handles_missing_attributes(self):
        doc = MagicMock(spec=[])
        meta = get_document_metadata(doc)
        assert meta["page_count"] == 0
        assert meta["figure_count"] == 0
