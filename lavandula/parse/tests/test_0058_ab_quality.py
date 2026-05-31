"""Unit tests for Spec 0058 §4 A/B metrics — pure, set-based (the 0060 lesson:
NOT counts, NOT sequence alignment). Uses the real 0057 grounding.normalize/check.
"""
from __future__ import annotations

from lavandula.parse.ab_quality import (
    cell_set,
    compare_variants,
    lost_rows,
    new_tier_c,
    row_jaccard,
    row_set,
    set_coverage,
    word_coverage,
)


def _table(rows):
    return {"data_json": rows, "markdown": "", "table_index": 0}


class TestCellSet:
    def test_normalizes_and_dedupes(self):
        # "Total" / "TOTAL " collapse to one normalized cell; blanks dropped.
        s = cell_set([_table([["Total", "100"], ["TOTAL ", ""]])])
        assert s == {"total", "100"}

    def test_reorder_does_not_change_set(self):
        a = cell_set([_table([["a", "b"], ["c", "d"]])])
        b = cell_set([_table([["d", "c"], ["b", "a"]])])  # reordered
        assert a == b  # set coverage is reorder-insensitive (unlike edit distance)


class TestSetCoverage:
    def test_identical_is_perfect(self):
        a = {"x", "y", "z"}
        cov, inv = set_coverage(a, set(a))
        assert cov == 1.0 and inv == 0.0

    def test_b_drops_a_cell(self):
        cov, inv = set_coverage({"x", "y", "z"}, {"x", "y"})
        assert round(cov, 4) == 0.6667
        assert inv == 0.0

    def test_b_invents_a_cell(self):
        cov, inv = set_coverage({"x", "y"}, {"x", "y", "zzz"})
        assert cov == 1.0
        assert round(inv, 4) == 0.3333

    def test_empty_a_coverage_one(self):
        assert set_coverage(set(), {"x"}) == (1.0, 1.0)


class TestRows:
    def test_lost_rows_counts_missing(self):
        a = row_set([_table([["a", "1"], ["b", "2"]])])
        b = row_set([_table([["a", "1"]])])
        assert lost_rows(a, b) == 1

    def test_row_jaccard(self):
        a = row_set([_table([["a", "1"], ["b", "2"]])])
        b = row_set([_table([["a", "1"], ["c", "3"]])])
        assert round(row_jaccard(a, b), 4) == 0.3333  # 1 shared / 3 union


class TestWordCoverage:
    def test_full_retention(self):
        assert word_coverage("hello world", "world hello again") == 1.0

    def test_drops_words(self):
        assert round(word_coverage("a b c d", "a b"), 4) == 0.5


class TestNewTierC:
    def test_b_cell_grounded_in_source_is_not_tier_c(self):
        a = {"revenue"}
        b = {"revenue", "1,234,567"}
        source = "Total revenue for the year was 1,234,567 dollars."
        assert new_tier_c(a, b, source) == 0  # the new B cell IS in source

    def test_b_cell_absent_from_source_is_tier_c(self):
        a = {"revenue"}
        b = {"revenue", "9,999,999"}  # invented, not in source
        source = "Total revenue for the year was 1,234,567 dollars."
        assert new_tier_c(a, b, source) == 1

    def test_cell_already_in_a_never_counts(self):
        a = {"ungrounded-thing"}
        b = {"ungrounded-thing"}
        assert new_tier_c(a, b, "unrelated source") == 0


class TestCompareVariantsVerdict:
    def _doc(self, rows, convert_ms=100, sections=None):
        return {"tables": [_table(rows)] if rows else [],
                "sections": sections or [], "convert_ms": convert_ms}

    def test_text_native_identical_passes(self):
        rows = [["Revenue", "100"], ["Expenses", "80"]]
        src = "Revenue 100 Expenses 80"
        cmp = compare_variants("a" * 64, "text_native",
                               self._doc(rows, 200), self._doc(rows, 100), src)
        assert cmp.verdict == "PASS"
        assert cmp.cell_coverage == 1.0
        assert cmp.lost_row_count == 0
        assert cmp.speedup == 2.0

    def test_text_native_lost_row_fails(self):
        a = self._doc([["Revenue", "100"], ["Expenses", "80"]])
        b = self._doc([["Revenue", "100"]])  # dropped a row
        cmp = compare_variants("a" * 64, "text_native", a, b, "Revenue 100 Expenses 80")
        assert cmp.verdict == "FAIL"
        assert cmp.lost_row_count == 1

    def test_text_native_invented_ungrounded_cell_fails(self):
        a = self._doc([["Revenue", "100"]])
        b = self._doc([["Revenue", "100"], ["Bogus", "424242"]])  # not in source
        cmp = compare_variants("a" * 64, "text_native", a, b, "Revenue 100")
        assert cmp.verdict == "FAIL"
        assert cmp.new_tier_c >= 1

    def test_designed_stratum_is_report_only(self):
        a = self._doc([["a", "1"]])
        b = self._doc([["a", "1"], ["b", "2"]])  # extra grounded content
        cmp = compare_variants("a" * 64, "designed_image_heavy", a, b, "a 1 b 2")
        assert cmp.verdict == "PASS"  # not hard-gated
