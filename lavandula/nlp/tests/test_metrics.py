"""Unit tests for metric extraction (Spec 0050)."""
from __future__ import annotations

import pytest

from lavandula.nlp.metrics import (
    NumberMatch,
    SentenceFragment,
    build_snippet,
    build_table_snippet,
    classify_heading,
    detect_numbers,
    extract_table_metrics,
    match_terms,
    split_sentences,
)


# --- T1: Number Detection ---

class TestDetectNumbers:
    def test_percentage(self):
        results = detect_numbers("achieved 92% of goals")
        assert len(results) == 1
        assert results[0].raw == "92%"
        assert results[0].parsed == 92.0
        assert results[0].unit_hint == "percent"

    def test_percentage_decimal(self):
        results = detect_numbers("a rate of 15.3% increase")
        assert len(results) == 1
        assert results[0].raw == "15.3%"
        assert results[0].parsed == 15.3
        assert results[0].unit_hint == "percent"

    def test_currency_millions(self):
        results = detect_numbers("raised $1.2M this year")
        assert len(results) == 1
        assert results[0].raw == "$1.2M"
        assert results[0].parsed == 1200000.0
        assert results[0].unit_hint == "currency"

    def test_currency_with_commas(self):
        results = detect_numbers("a budget of $50,000 for programs")
        assert len(results) == 1
        assert results[0].raw == "$50,000"
        assert results[0].parsed == 50000.0
        assert results[0].unit_hint == "currency"

    def test_comma_integer(self):
        results = detect_numbers("served 12,000 meals")
        assert len(results) == 1
        assert results[0].raw == "12,000"
        assert results[0].parsed == 12000.0
        assert results[0].unit_hint == "count"

    def test_plain_integer(self):
        results = detect_numbers("our 500 volunteers")
        assert len(results) == 1
        assert results[0].raw == "500"
        assert results[0].parsed == 500.0
        assert results[0].unit_hint == "count"

    def test_excludes_year(self):
        results = detect_numbers("In 2024 we expanded our reach")
        assert len(results) == 0

    def test_excludes_phone(self):
        results = detect_numbers("Call (555) 555-5555 for info")
        assert len(results) == 0

    def test_excludes_date(self):
        results = detect_numbers("filed on 05/25/2026")
        assert len(results) == 0

    def test_excludes_page_reference(self):
        results = detect_numbers("see page 12 for details")
        assert len(results) == 0

    def test_excludes_zip_code(self):
        results = detect_numbers("located in NY 10001")
        assert len(results) == 0

    def test_multiple_numbers(self):
        results = detect_numbers("served 12,000 meals and 500 families")
        assert len(results) == 2
        raws = [r.raw for r in results]
        assert "12,000" in raws
        assert "500" in raws

    def test_year_with_dollar_not_excluded(self):
        results = detect_numbers("received $2024 in donations")
        assert len(results) == 1
        assert results[0].unit_hint == "currency"

    def test_two_digit_excluded(self):
        results = detect_numbers("we have 12 staff members")
        assert len(results) == 0

    def test_currency_k_suffix(self):
        results = detect_numbers("awarded $50k in grants")
        assert len(results) == 1
        assert results[0].parsed == 50000.0

    def test_currency_b_suffix(self):
        results = detect_numbers("a $1.5B industry")
        assert len(results) == 1
        assert results[0].parsed == 1500000000.0


# --- T2: Snippet Construction ---

class TestBuildSnippet:
    def test_long_sentence_no_padding(self):
        sentence = "This year, 94% of enrolled children met or exceeded school readiness goals in the area of physical development."
        snippet = build_snippet(sentence, "Program Outcomes", "Next sentence here.")
        assert snippet == sentence
        assert "[" not in snippet

    def test_short_sentence_with_heading_and_next(self):
        sentence = "Served 500 families."
        heading = "Impact"
        next_sent = "We expanded our reach to new communities this year with additional funding."
        snippet = build_snippet(sentence, heading, next_sent)
        assert snippet.startswith("[Impact] — ")
        assert "Served 500 families." in snippet
        assert "We expanded" in snippet

    def test_table_snippet(self):
        snippet = build_table_snippet("Program Outcomes", "Meals Served", "12,000")
        assert snippet == "[Program Outcomes] — Meals Served: 12,000"

    def test_truncation_at_500_chars(self):
        long_sentence = "A" * 600
        snippet = build_snippet(long_sentence, None, None)
        assert len(snippet) == 500
        assert snippet.endswith("…")

    def test_short_with_no_heading(self):
        snippet = build_snippet("Served 500.", None, "More context here.")
        assert not snippet.startswith("[")
        assert "Served 500." in snippet
        assert "More context" in snippet

    def test_table_snippet_truncation(self):
        long_label = "A" * 600
        snippet = build_table_snippet("Heading", long_label, "123")
        assert len(snippet) == 500
        assert snippet.endswith("…")


# --- T3: Term Matching ---

class TestMatchTerms:
    def test_simple_match(self):
        term_set = {"school readiness goal", "food bank", "meal"}
        result = match_terms("our school readiness goal metrics improved", term_set)
        assert "school readiness goal" in result

    def test_no_match(self):
        term_set = {"school readiness goal", "food bank"}
        result = match_terms("we delivered meals to families", term_set)
        assert result == []

    def test_multiple_terms_match(self):
        term_set = {"school readiness", "enrolled children", "physical development"}
        text = "94% of enrolled children met school readiness goals in physical development"
        result = match_terms(text, term_set)
        assert "school readiness" in result
        assert "enrolled children" in result
        assert "physical development" in result

    def test_substring_match_exact(self):
        term_set = {"food bank"}
        assert match_terms("our food bank served", term_set) == ["food bank"]

    def test_substring_includes_partial_word(self):
        # Per spec: exact substring match — "food bank" IS in "food banking"
        term_set = {"food bank"}
        result = match_terms("food banking regulations apply", term_set)
        assert result == ["food bank"]


# --- T5: Heading Filter ---

class TestClassifyHeading:
    def test_block_auditor(self):
        assert classify_heading("Independent Auditor's Report") == "skip"

    def test_block_financial_statement(self):
        assert classify_heading("Financial Statement") == "skip"

    def test_block_form_990(self):
        assert classify_heading("Form 990 Information") == "skip"

    def test_block_board(self):
        assert classify_heading("Board of Directors") == "skip"

    def test_priority_program(self):
        assert classify_heading("Program Outcomes") == "priority"

    def test_priority_impact(self):
        assert classify_heading("Our Impact") == "priority"

    def test_priority_service(self):
        assert classify_heading("Services Provided") == "priority"

    def test_neutral(self):
        assert classify_heading("About Our Organization") == "neutral"

    def test_none_heading(self):
        assert classify_heading(None) == "neutral"

    def test_empty_heading(self):
        assert classify_heading("") == "neutral"

    def test_case_insensitive_block(self):
        assert classify_heading("FINANCIAL STATEMENT NOTES") == "skip"

    def test_case_insensitive_priority(self):
        assert classify_heading("PROGRAM ACHIEVEMENTS") == "priority"


# --- Sentence Splitting ---

class TestSplitSentences:
    def test_basic_split(self):
        text = "First sentence. Second sentence. Third sentence."
        frags = split_sentences(text)
        assert len(frags) >= 2
        assert frags[0].text.startswith("First")

    def test_bullet_detection(self):
        text = "Introduction.\n- First bullet point\n- Second bullet point"
        frags = split_sentences(text)
        bullets = [f for f in frags if f.is_bullet]
        assert len(bullets) >= 1

    def test_empty_text(self):
        assert split_sentences("") == []
        assert split_sentences("   ") == []

    def test_single_sentence(self):
        frags = split_sentences("Just one sentence without a period")
        assert len(frags) == 1
        assert frags[0].is_bullet is False

    def test_numbered_list(self):
        text = "Goals:\n1) Serve 500 families\n2) Deliver 12,000 meals"
        frags = split_sentences(text)
        bullets = [f for f in frags if f.is_bullet]
        assert len(bullets) >= 1


# --- Table Extraction ---

class TestExtractTableMetrics:
    def test_basic_table(self):
        data = [
            ["Metric", "Value", "Notes"],
            ["Meals served", "12,000", "Increase from prior year"],
            ["School readiness goal", "94%", "Exceeded target"],
            ["Volunteer hours", "5,000", "Community support"],
        ]
        term_set = {"meal", "school readiness goal", "volunteer"}
        results = extract_table_metrics(data, "Program Outcomes", term_set)
        assert len(results) > 0
        assert all(r["source_type"] == "table" for r in results)
        assert all(r["confidence"] == "high" for r in results)

    def test_financial_table_skipped(self):
        data = [
            ["Account", "Amount"],
            ["Revenue", "500,000"],
            ["Expenses", "450,000"],
            ["Assets", "1,200,000"],
            ["Liabilities", "300,000"],
        ]
        term_set = {"revenue", "expense", "asset"}
        results = extract_table_metrics(data, "Financial Summary", term_set)
        assert len(results) == 0

    def test_column_header_fallback(self):
        data = [
            ["", "Meals Served", "Status"],
            ["", "3,000", "On track"],
            ["", "4,000", "Exceeded"],
            ["", "3,500", "On track"],
        ]
        term_set = {"meal"}
        results = extract_table_metrics(data, "Quarterly Results", term_set)
        assert len(results) > 0

    def test_invalid_data_json(self):
        assert extract_table_metrics(None, "Heading", set()) == []
        assert extract_table_metrics("not a list", "Heading", set()) == []
        assert extract_table_metrics([["header only"]], "Heading", set()) == []

    def test_blocked_heading_skipped(self):
        data = [
            ["Item", "Amount"],
            ["Revenue", "500,000"],
        ]
        term_set = {"revenue"}
        results = extract_table_metrics(data, "Financial Statement", term_set)
        assert len(results) == 0


# --- Bullet Source Type ---

class TestBulletSourceType:
    def test_bullet_produces_bullet_source_type(self):
        text = "Overview.\n- Served 500 families last year\n- Delivered 12,000 meals"
        frags = split_sentences(text)
        bullets = [f for f in frags if f.is_bullet]
        assert len(bullets) >= 1
        for b in bullets:
            assert b.is_bullet is True

    def test_narrative_produces_narrative_source_type(self):
        text = "We served 500 families. We delivered 12,000 meals to communities."
        frags = split_sentences(text)
        for f in frags:
            assert f.is_bullet is False


# --- Confidence Levels ---

class TestConfidenceLevels:
    def test_high_confidence_term_and_number_same_sentence(self):
        from lavandula.nlp.metrics import _process_narrative
        term_set = {"school readiness"}
        obs = _process_narrative(
            "We achieved 94% on school readiness assessments this year.",
            "Program Outcomes",
            0,
            term_set,
        )
        assert len(obs) > 0
        assert obs[0]["confidence"] == "high"

    def test_medium_confidence_term_in_heading_only(self):
        from lavandula.nlp.metrics import _process_narrative
        term_set = {"school readiness"}
        obs = _process_narrative(
            "We achieved 94% on our key assessments this year for all enrolled children.",
            "School Readiness Results",
            0,
            term_set,
        )
        assert len(obs) > 0
        assert obs[0]["confidence"] == "medium"

    def test_no_observation_when_no_term_match(self):
        from lavandula.nlp.metrics import _process_narrative
        term_set = {"school readiness"}
        obs = _process_narrative(
            "We served 500 families this year in our community food program.",
            "About Us",
            0,
            term_set,
        )
        assert len(obs) == 0
