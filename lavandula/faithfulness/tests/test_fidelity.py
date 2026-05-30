"""Tests for fidelity scoring module (Spec 0060 Phase 2)."""
from __future__ import annotations

import pytest

from lavandula.faithfulness.fidelity import (
    COVERAGE_FLOOR,
    FidelityScore,
    classify_text_source,
    coverage,
    score_fidelity,
    word_set,
)
from lavandula.faithfulness.pdftotext_extract import ExtractResult


# ============================================================
# word_set
# ============================================================

class TestWordSet:
    def test_basic_tokenization(self):
        ws = word_set("Hello World from Python")
        assert ws == {"hello", "world", "from", "python"}

    def test_single_char_tokens_discarded(self):
        ws = word_set("I am a data scientist")
        assert "i" not in ws
        assert "a" not in ws
        assert "am" in ws
        assert "data" in ws
        assert "scientist" in ws

    def test_unicode_nfc(self):
        ws1 = word_set("café")
        ws2 = word_set("café")
        assert ws1 == ws2

    def test_empty_input(self):
        assert word_set("") == set()
        assert word_set("   ") == set()

    def test_punctuation_kept_with_words(self):
        ws = word_set("$5,000 served 100%")
        assert "$5,000" in ws
        assert "served" in ws
        assert "100%" in ws

    def test_whitespace_collapse(self):
        ws = word_set("hello    world\n\ttab")
        assert ws == {"hello", "world", "tab"}


# ============================================================
# coverage
# ============================================================

class TestCoverage:
    def test_identical_sets(self):
        s = {"hello", "world"}
        assert coverage(s, s) == 1.0

    def test_disjoint_sets(self):
        assert coverage({"hello", "world"}, {"foo", "bar"}) == 0.0

    def test_partial_overlap(self):
        assert coverage({"hello", "world"}, {"hello", "bar"}) == 0.5

    def test_empty_source(self):
        assert coverage(set(), {"hello"}) == 0.0

    def test_empty_target(self):
        assert coverage({"hello"}, set()) == 0.0

    def test_superset_target(self):
        assert coverage({"hello"}, {"hello", "world"}) == 1.0


# ============================================================
# score_fidelity
# ============================================================

class TestScoreFidelity:
    def test_identical_texts(self):
        text = "We served 1,514 cancer patients and caregivers last year."
        score = score_fidelity(text, text)
        assert score.forward == 1.0
        assert score.reverse == 1.0

    def test_docling_drops_content(self):
        docling = "We served patients last year."
        pdftotext = "We served 1,514 cancer patients and caregivers last year."
        score = score_fidelity(docling, pdftotext)
        assert score.forward == 1.0  # all docling words in pdftotext
        assert score.reverse < 1.0   # pdftotext has words docling dropped

    def test_docling_invents_content(self):
        docling = "We served 1,514 cancer patients and caregivers last year. Extra invented words here."
        pdftotext = "We served 1,514 cancer patients and caregivers last year."
        score = score_fidelity(docling, pdftotext)
        assert score.forward < 1.0   # docling has words not in pdftotext
        assert score.reverse == 1.0  # all pdftotext words in docling

    def test_empty_texts(self):
        score = score_fidelity("", "")
        assert score.forward == 0.0
        assert score.reverse == 0.0

    def test_known_fixture(self):
        docling = "Annual Report 2023 Total revenue $5,000,000 Programs served 1,200 families"
        pdftotext = "Annual Report 2023 Total revenue $5,000,000 Programs served 1,200 families in the community"
        score = score_fidelity(docling, pdftotext)
        docling_ws = word_set(docling)
        pdftotext_ws = word_set(pdftotext)
        expected_fwd = len(docling_ws & pdftotext_ws) / len(docling_ws)
        expected_rev = len(pdftotext_ws & docling_ws) / len(pdftotext_ws)
        assert abs(score.forward - expected_fwd) < 0.001
        assert abs(score.reverse - expected_rev) < 0.001


# ============================================================
# classify_text_source
# ============================================================

class TestClassifyTextSource:
    def _make_extract(self, *, failed=False, is_scanned=False, char_count=1000):
        return ExtractResult(
            text="x" * char_count, version="test", char_count=char_count,
            is_scanned=is_scanned, failed=failed,
        )

    def test_failed_extraction(self):
        result = self._make_extract(failed=True)
        assert classify_text_source(result, None) == "pdftotext_failed"

    def test_scanned_document(self):
        result = self._make_extract(is_scanned=True, char_count=10)
        assert classify_text_source(result, None) == "scanned"

    def test_text_native_no_fidelity(self):
        result = self._make_extract()
        assert classify_text_source(result, None) == "text_native"

    def test_text_native_good_fidelity(self):
        result = self._make_extract()
        fidelity = FidelityScore(forward=0.95, reverse=0.98)
        assert classify_text_source(result, fidelity) == "text_native"

    def test_low_fidelity_both_directions(self):
        result = self._make_extract()
        fidelity = FidelityScore(forward=0.30, reverse=0.40)
        assert classify_text_source(result, fidelity) == "pdftotext_failed"

    def test_low_forward_high_reverse(self):
        result = self._make_extract()
        fidelity = FidelityScore(forward=0.30, reverse=0.90)
        assert classify_text_source(result, fidelity) == "text_native"

    def test_boundary_coverage(self):
        result = self._make_extract()
        fidelity = FidelityScore(forward=COVERAGE_FLOOR, reverse=COVERAGE_FLOOR)
        assert classify_text_source(result, fidelity) == "text_native"

    def test_just_below_boundary(self):
        result = self._make_extract()
        fidelity = FidelityScore(forward=COVERAGE_FLOOR - 0.01, reverse=COVERAGE_FLOOR - 0.01)
        assert classify_text_source(result, fidelity) == "pdftotext_failed"
