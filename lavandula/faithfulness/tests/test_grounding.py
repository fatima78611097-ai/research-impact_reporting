"""Tests for the faithfulness grounding verifier (Spec 0057).

TDD'd against the spec §4.2 counterexample table and required test cases:
- normalization edge cases (whitespace / NFC / quotes / dashes)
- table R2 (label+value across cells; multi-row tables)
- multi-span facts
- numeric-reformat defect detection
- Tier-B/OCR fallback path (tested at gate runner level)
- quarantine behavior
- story rule (§4.5)
- regression fixtures derived from the spec's counterexample table
"""
from __future__ import annotations

import pytest

from lavandula.faithfulness.grounding import (
    MAX_MULTI_SPAN,
    MAX_SNIPPET_CHARS,
    Verdict,
    _extract_direct_quotes,
    check,
    check_story,
    normalize,
    value_present,
)
from lavandula.faithfulness.source_provider import TableRow


# ============================================================
# Normalization (§4.2)
# ============================================================

class TestNormalize:
    def test_lowercase(self):
        assert normalize("Hello WORLD") == "hello world"

    def test_collapse_whitespace(self):
        assert normalize("hello   world\t\nfoo") == "hello world foo"

    def test_unicode_nfc(self):
        # e + combining accent vs precomposed é
        decomposed = "café"
        composed = "café"
        assert normalize(decomposed) == normalize(composed)

    def test_curly_quotes_to_straight(self):
        assert normalize("“Hello”") == normalize('"Hello"')
        assert normalize("‘it’s’") == normalize("'it's'")

    def test_dash_folding(self):
        assert normalize("2020–2021") == normalize("2020-2021")
        assert normalize("long—dash") == normalize("long-dash")
        assert normalize("figure‒dash") == normalize("figure-dash")

    def test_control_char_rejection(self):
        result = normalize("hello\x00world\x07test")
        assert result == "hello world test"
        assert "\x00" not in result
        assert "\x07" not in result

    def test_tabs_and_newlines(self):
        assert normalize("line1\n\tline2") == "line1 line2"

    def test_empty(self):
        assert normalize("") == ""

    def test_only_whitespace(self):
        assert normalize("   \t\n  ") == ""

    def test_mixed_unicode_dashes_and_quotes(self):
        text = "“Revenue” — $2.8M (2020–2021)"
        expected = normalize('"Revenue" - $2.8m (2020-2021)')
        assert normalize(text) == expected


# ============================================================
# R1: Contiguous span (§4.2)
# ============================================================

class TestR1ContiguousSpan:
    def test_exact_match(self):
        source = "We served 1,514 cancer patients and caregivers."
        snippet = "1,514 cancer patients and caregivers"
        v = check(snippet, source, [])
        assert v.grounded is True
        assert v.rule == "R1"
        assert len(v.offsets) == 1

    def test_whitespace_normalization_match(self):
        """Snippet matches source after whitespace/quote/NFC fold → GROUNDED (R1)."""
        source = 'We  achieved   "92%"  of  goals.'
        snippet = 'We achieved "92%" of goals.'
        v = check(snippet, source, [])
        assert v.grounded is True
        assert v.rule == "R1"

    def test_curly_quote_normalization(self):
        source = "“Hello world” is a greeting."
        snippet = '"Hello world" is a greeting.'
        v = check(snippet, source, [])
        assert v.grounded is True
        assert v.rule == "R1"

    def test_dash_normalization(self):
        source = "Fiscal year 2020–2021 results."
        snippet = "Fiscal year 2020-2021 results."
        v = check(snippet, source, [])
        assert v.grounded is True
        assert v.rule == "R1"

    def test_case_insensitive(self):
        source = "Total Revenue was $5,000,000"
        snippet = "total revenue was $5,000,000"
        v = check(snippet, source, [])
        assert v.grounded is True
        assert v.rule == "R1"

    def test_not_found(self):
        source = "We served 1,514 cancer patients."
        snippet = "completely different text not in source"
        v = check(snippet, source, [])
        assert v.grounded is False
        assert v.rule == "none"


# ============================================================
# R2: Same-table-row (§4.2)
# ============================================================

class TestR2SameTableRow:
    def test_label_value_across_cells(self):
        """'Total expenses: $26,122,404', source has them in one table row → GROUNDED (R2)."""
        tables = [[
            TableRow(cells=["Total expenses", "$26,122,404"]),
            TableRow(cells=["Total revenue", "$30,000,000"]),
        ]]
        snippet = "Total expenses: $26,122,404"
        v = check(snippet, "", tables)
        assert v.grounded is True
        assert v.rule == "R2"

    def test_label_value_partial_no_match(self):
        """Value from one row, label from another → DEFECT."""
        tables = [[
            TableRow(cells=["Total expenses", "$26,122,404"]),
            TableRow(cells=["Total revenue", "$30,000,000"]),
        ]]
        snippet = "Total revenue: $26,122,404"
        v = check(snippet, "", tables)
        assert v.grounded is False

    def test_multi_row_table(self):
        tables = [[
            TableRow(cells=["Category", "Amount"]),
            TableRow(cells=["Salaries", "$1,500,000"]),
            TableRow(cells=["Benefits", "$350,000"]),
            TableRow(cells=["Travel", "$75,000"]),
        ]]
        snippet = "Benefits $350,000"
        v = check(snippet, "", tables)
        assert v.grounded is True
        assert v.rule == "R2"

    def test_multiple_tables(self):
        tables = [
            [TableRow(cells=["Revenue", "$10M"])],
            [TableRow(cells=["Staff Count", "250"])],
        ]
        snippet = "Staff Count 250"
        v = check(snippet, "", tables)
        assert v.grounded is True
        assert v.rule == "R2"


# ============================================================
# R1 before R2 tie-break (determinism)
# ============================================================

class TestTieBreak:
    def test_r1_preferred_over_r2(self):
        """When snippet matches both source text (R1) and table (R2), R1 wins."""
        source = "Total expenses: $26,122,404 for the year."
        tables = [[TableRow(cells=["Total expenses", "$26,122,404"])]]
        snippet = "Total expenses: $26,122,404"
        v = check(snippet, source, tables)
        assert v.grounded is True
        assert v.rule == "R1"


# ============================================================
# Spec §4.2 counterexample table (frozen acceptance tests)
# ============================================================

class TestSpecCounterexamples:
    def test_whitespace_quote_nfc_fold_grounded(self):
        """'snippet matches source after whitespace/quote/NFC fold' → GROUNDED (R1)."""
        source = 'We   achieved  “92%”  of  goals.'
        snippet = 'We achieved "92%" of goals.'
        v = check(snippet, source, [])
        assert v.grounded is True
        assert v.rule == "R1"

    def test_table_row_grounded(self):
        """'Total expenses: $26,122,404', in one table row → GROUNDED (R2)."""
        tables = [[TableRow(cells=["Total expenses", "$26,122,404"])]]
        snippet = "Total expenses: $26,122,404"
        v = check(snippet, "", tables)
        assert v.grounded is True
        assert v.rule == "R2"

    def test_composed_prose_defect(self):
        """'420 Naturalizations is a 65% increase from last year!' — composed → DEFECT."""
        source = (
            "In 2023, we processed 420 naturalizations. "
            "Last year we processed 255 naturalizations."
        )
        snippet = "420 Naturalizations is a 65% increase from last year!"
        v = check(snippet, source, [])
        assert v.grounded is False
        assert v.rule == "none"

    def test_numeric_reformat_defect(self):
        """'$2.8M' where source says '$2.8 million' → DEFECT (default)."""
        source = "We invested $2.8 million in community programs."
        snippet = "$2.8M"
        v = check(snippet, source, [])
        assert v.grounded is False

    def test_scattered_tokens_defect(self):
        """Tokens present but scattered across doc → DEFECT."""
        source = (
            "Section 1: We have 500 volunteers. "
            "Section 5: Our budget is $2 million. "
            "Section 9: We operate in 12 states."
        )
        snippet = "500 volunteers with $2 million budget across 12 states"
        v = check(snippet, source, [])
        assert v.grounded is False


# ============================================================
# Value presence
# ============================================================

class TestValuePresent:
    def test_integer_value_present(self):
        v, d = value_present(1514, None, "served 1,514 patients")
        assert v is True
        assert d is True

    def test_integer_not_present(self):
        v, d = value_present(999, None, "served 1,514 patients")
        assert v is False

    def test_float_value(self):
        v, d = value_present(2.8, None, "invested $2.8 million")
        assert v is True

    def test_with_denominator(self):
        v, d = value_present(46, 89, "46 of 89 applications approved")
        assert v is True
        assert d is True

    def test_denominator_missing(self):
        v, d = value_present(46, 89, "46 applications approved")
        assert v is True
        assert d is False

    def test_none_value(self):
        v, d = value_present(None, None, "some snippet")
        assert v is True
        assert d is True


# ============================================================
# Edge cases & guards
# ============================================================

class TestEdgeCases:
    def test_empty_snippet(self):
        v = check("", "some source", [])
        assert v.grounded is False

    def test_whitespace_only_snippet(self):
        v = check("   \t\n  ", "some source", [])
        assert v.grounded is False

    def test_oversize_snippet(self):
        snippet = "x" * (MAX_SNIPPET_CHARS + 1)
        v = check(snippet, snippet, [])
        assert v.grounded is False

    def test_empty_source_with_tables(self):
        tables = [[TableRow(cells=["Revenue", "$10M"])]]
        v = check("Revenue $10M", "", tables)
        assert v.grounded is True
        assert v.rule == "R2"

    def test_no_source_no_tables(self):
        v = check("some snippet", "", [])
        assert v.grounded is False


# ============================================================
# Determinism
# ============================================================

class TestDeterminism:
    def test_same_input_same_verdict(self):
        source = "We served 1,514 cancer patients and caregivers."
        snippet = "1,514 cancer patients and caregivers"
        tables = [[TableRow(cells=["Programs", "1514"])]]
        verdicts = [check(snippet, source, tables) for _ in range(10)]
        for v in verdicts:
            assert v.grounded is True
            assert v.rule == "R1"
            assert v.offsets == verdicts[0].offsets


# ============================================================
# Story grounding rule (§4.5)
# ============================================================

class TestStoryRule:
    def test_grounded_source_snippet(self):
        source = "Maria found hope through our counseling program."
        v = check_story(
            source_snippet="Maria found hope through our counseling program",
            story_summary="Maria overcame adversity with program support.",
            source_text=source,
            tables=[],
        )
        assert v.grounded is True

    def test_no_source_snippet_tier_c(self):
        v = check_story(
            source_snippet=None,
            story_summary="A great story about impact.",
            source_text="Some source text.",
            tables=[],
        )
        assert v.grounded is False

    def test_empty_source_snippet_tier_c(self):
        v = check_story(
            source_snippet="",
            story_summary="A great story.",
            source_text="Some source.",
            tables=[],
        )
        assert v.grounded is False

    def test_summary_prose_not_rejected(self):
        """Summary may be abstractive — do NOT reject composed summary."""
        source = "John received 50 meals and housing support through our shelter program last winter."
        v = check_story(
            source_snippet="John received 50 meals and housing support through our shelter program",
            story_summary="John's life was transformed by comprehensive support services.",
            source_text=source,
            tables=[],
        )
        assert v.grounded is True

    def test_direct_quote_must_be_grounded(self):
        """Direct quotes in summary must be verbatim-grounded."""
        source = 'Maria said "this program saved my life" during the interview.'
        v = check_story(
            source_snippet='Maria said "this program saved my life"',
            story_summary='Maria testified: "this program saved my life"',
            source_text=source,
            tables=[],
        )
        assert v.grounded is True

    def test_ungrounded_direct_quote_fails(self):
        """A fabricated direct quote in the summary → defect."""
        source = "Maria participated in the program and found it helpful."
        v = check_story(
            source_snippet="Maria participated in the program",
            story_summary='Maria said "the program changed everything for me"',
            source_text=source,
            tables=[],
        )
        assert v.grounded is False

    def test_ungrounded_source_snippet_fails(self):
        source = "We provide housing and meals to families."
        v = check_story(
            source_snippet="Our comprehensive services helped 500 families escape homelessness",
            story_summary="Families found new hope.",
            source_text=source,
            tables=[],
        )
        assert v.grounded is False


# ============================================================
# Direct quote extraction
# ============================================================

class TestExtractDirectQuotes:
    def test_double_quotes(self):
        quotes = _extract_direct_quotes('She said "hello world" to everyone')
        assert quotes == ["hello world"]

    def test_curly_double_quotes(self):
        quotes = _extract_direct_quotes("She said “hello world” to everyone")
        assert quotes == ["hello world"]

    def test_single_quotes(self):
        quotes = _extract_direct_quotes("She said 'hello world' to everyone")
        assert quotes == ["hello world"]

    def test_multiple_quotes(self):
        quotes = _extract_direct_quotes('"first quote" and "second quote"')
        assert len(quotes) == 2

    def test_short_quotes_ignored(self):
        """Quotes shorter than 3 chars are not considered direct quotes."""
        quotes = _extract_direct_quotes('She said "hi" to everyone')
        assert quotes == []

    def test_no_quotes(self):
        quotes = _extract_direct_quotes("No quotes here at all")
        assert quotes == []


class TestDiagnostics:
    """Spec 0057 amendment — the WHY signals on every Verdict must separate the
    quarantine failure modes (fragmentation vs garble/fabrication vs missing)."""

    def test_clean_match_full_coverage_and_run(self):
        v = check("served 1,514 patients", "in 2023 we served 1,514 patients here", [])
        assert v.grounded and v.word_coverage == 1.0 and v.longest_run == 1.0

    def test_fragmentation_high_coverage_low_run(self):
        # all snippet words present in source, but not contiguous -> parser split
        v = check("5,330 served at the y",
                  "reach in numbers 5,330 various served at the y in programs", [])
        assert not v.grounded
        assert v.word_coverage == 1.0          # every word is there
        assert v.longest_run < 1.0             # but not as one run

    def test_garble_or_fabrication_low_coverage(self):
        v = check("420 naturalizations is a 65% increase",
                  "we processed many cases last year", [])
        assert not v.grounded
        assert v.word_coverage < 0.3           # words genuinely absent

    def test_missing_source_zero_chars(self):
        v = check("anything at all here", "", [])
        assert not v.grounded and v.source_chars == 0

    def test_table_artifact_has_table_coverage(self):
        from lavandula.faithfulness.source_provider import TableRow
        v = check("total expenses 26,122,404", "",
                  [[TableRow(cells=["Total expenses", "$26,122,404"])]])
        assert v.table_coverage > 0.5
