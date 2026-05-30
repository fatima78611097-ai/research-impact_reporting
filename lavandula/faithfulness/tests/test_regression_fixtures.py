"""Regression fixtures for the faithfulness verifier (Spec 0057 Phase 6).

Frozen test cases derived from the spec's counterexample table and edge
cases discovered during implementation. These lock the gate's behavior
as a regression guard — any change to the verifier that breaks these
tests is a signal that the gate's semantics have shifted.
"""
from __future__ import annotations

import json

import pytest

from lavandula.faithfulness.grounding import (
    Verdict,
    check,
    check_story,
    normalize,
    value_present,
)
from lavandula.faithfulness.gate_runner import (
    TIER_QUARANTINE,
    TIER_UNVERIFIED_OCR,
    TIER_VERIFIED,
    _assign_tier,
)
from lavandula.faithfulness.source_provider import SourceText, TableRow

# ============================================================
# Fixture data: each entry is a frozen regression case
# ============================================================

METRIC_FIXTURES = [
    {
        "id": "spec-r1-whitespace-fold",
        "description": "Whitespace/quote/NFC fold → GROUNDED R1",
        "source_text": 'We  achieved   "92%"  of  goals.',
        "snippet": 'We achieved "92%" of goals.',
        "tables": [],
        "expected_grounded": True,
        "expected_rule": "R1",
    },
    {
        "id": "spec-r2-table-row",
        "description": "Label+value across table cells → GROUNDED R2",
        "source_text": "",
        "snippet": "Total expenses: $26,122,404",
        "tables": [[["Total expenses", "$26,122,404"]]],
        "expected_grounded": True,
        "expected_rule": "R2",
    },
    {
        "id": "spec-composed-prose",
        "description": "Composed prose (420 Naturalizations...) → DEFECT",
        "source_text": "In 2023, we processed 420 naturalizations. Last year we processed 255 naturalizations.",
        "snippet": "420 Naturalizations is a 65% increase from last year!",
        "tables": [],
        "expected_grounded": False,
        "expected_rule": "none",
    },
    {
        "id": "spec-numeric-reformat",
        "description": "$2.8M vs $2.8 million → DEFECT (default)",
        "source_text": "We invested $2.8 million in community programs.",
        "snippet": "$2.8M",
        "tables": [],
        "expected_grounded": False,
        "expected_rule": "none",
    },
    {
        "id": "spec-scattered-tokens",
        "description": "Tokens scattered across doc → DEFECT",
        "source_text": "Section 1: We have 500 volunteers. Section 5: Our budget is $2 million. Section 9: We operate in 12 states.",
        "snippet": "500 volunteers with $2 million budget across 12 states",
        "tables": [],
        "expected_grounded": False,
        "expected_rule": "none",
    },
    {
        "id": "exact-substring",
        "description": "Exact substring match (no normalization needed)",
        "source_text": "In 2023, we served 1,514 cancer patients and caregivers through our programs.",
        "snippet": "served 1,514 cancer patients and caregivers",
        "tables": [],
        "expected_grounded": True,
        "expected_rule": "R1",
    },
    {
        "id": "curly-to-straight-quotes",
        "description": "Curly quotes normalized to straight → GROUNDED R1",
        "source_text": "Our “impact report” shows significant growth.",
        "snippet": 'Our "impact report" shows significant growth.',
        "tables": [],
        "expected_grounded": True,
        "expected_rule": "R1",
    },
    {
        "id": "em-dash-to-hyphen",
        "description": "Em-dash normalized to hyphen → GROUNDED R1",
        "source_text": "Revenue — $5M — exceeded expectations.",
        "snippet": "Revenue - $5M - exceeded expectations.",
        "tables": [],
        "expected_grounded": True,
        "expected_rule": "R1",
    },
    {
        "id": "r1-over-r2-tiebreak",
        "description": "When both R1 and R2 match, R1 wins (determinism)",
        "source_text": "Total expenses: $26,122,404 for the year.",
        "snippet": "Total expenses: $26,122,404",
        "tables": [[["Total expenses", "$26,122,404"]]],
        "expected_grounded": True,
        "expected_rule": "R1",
    },
    {
        "id": "table-multi-row",
        "description": "Value from correct row, not another → GROUNDED R2",
        "source_text": "",
        "snippet": "Benefits $350,000",
        "tables": [[["Salaries", "$1,500,000"], ["Benefits", "$350,000"], ["Travel", "$75,000"]]],
        "expected_grounded": True,
        "expected_rule": "R2",
    },
    {
        "id": "table-cross-row-defect",
        "description": "Value from row A, label from row B → DEFECT",
        "source_text": "",
        "snippet": "Travel: $1,500,000",
        "tables": [[["Salaries", "$1,500,000"], ["Travel", "$75,000"]]],
        "expected_grounded": False,
        "expected_rule": "none",
    },
]

STORY_FIXTURES = [
    {
        "id": "story-grounded-snippet",
        "description": "Story with grounded source_snippet → verified",
        "source_text": "Maria found hope through our counseling program and rebuilt her life.",
        "source_snippet": "Maria found hope through our counseling program",
        "story_summary": "Maria overcame adversity with support.",
        "expected_grounded": True,
    },
    {
        "id": "story-no-snippet",
        "description": "Story with no source_snippet → Tier C",
        "source_text": "Some text about the program.",
        "source_snippet": None,
        "story_summary": "A compelling impact story.",
        "expected_grounded": False,
    },
    {
        "id": "story-grounded-quote",
        "description": "Story with grounded direct quote → verified",
        "source_text": 'Maria said "this program saved my life" during her interview.',
        "source_snippet": 'Maria said "this program saved my life"',
        "story_summary": 'Maria testified: "this program saved my life"',
        "expected_grounded": True,
    },
    {
        "id": "story-ungrounded-quote",
        "description": "Story with fabricated direct quote → defect",
        "source_text": "Maria participated in the program and found it helpful.",
        "source_snippet": "Maria participated in the program",
        "story_summary": 'Maria said "the program changed everything for me"',
        "expected_grounded": False,
    },
    {
        "id": "story-abstractive-summary-ok",
        "description": "Abstractive summary prose is allowed (not rejected)",
        "source_text": "John received 50 meals and housing support through our shelter program last winter.",
        "source_snippet": "John received 50 meals and housing support through our shelter program",
        "story_summary": "John's life was transformed by comprehensive support services.",
        "expected_grounded": True,
    },
]


def _build_tables(raw: list) -> list[list[TableRow]]:
    """Convert fixture table data to TableRow objects."""
    return [
        [TableRow(cells=row) for row in table]
        for table in raw
    ]


class TestMetricRegressionFixtures:
    @pytest.mark.parametrize(
        "fixture",
        METRIC_FIXTURES,
        ids=[f["id"] for f in METRIC_FIXTURES],
    )
    def test_metric_fixture(self, fixture):
        tables = _build_tables(fixture["tables"])
        verdict = check(
            fixture["snippet"],
            fixture["source_text"],
            tables,
        )
        assert verdict.grounded is fixture["expected_grounded"], (
            f"Fixture {fixture['id']}: expected grounded={fixture['expected_grounded']}, "
            f"got grounded={verdict.grounded}"
        )
        assert verdict.rule == fixture["expected_rule"], (
            f"Fixture {fixture['id']}: expected rule={fixture['expected_rule']}, "
            f"got rule={verdict.rule}"
        )


class TestStoryRegressionFixtures:
    @pytest.mark.parametrize(
        "fixture",
        STORY_FIXTURES,
        ids=[f["id"] for f in STORY_FIXTURES],
    )
    def test_story_fixture(self, fixture):
        verdict = check_story(
            source_snippet=fixture["source_snippet"],
            story_summary=fixture["story_summary"],
            source_text=fixture["source_text"],
            tables=[],
        )
        assert verdict.grounded is fixture["expected_grounded"], (
            f"Fixture {fixture['id']}: expected grounded={fixture['expected_grounded']}, "
            f"got grounded={verdict.grounded}"
        )


class TestTierRegressionFixtures:
    """End-to-end: verify that fixture verdicts produce the correct tier."""

    def test_verified_metric_gets_tier_a(self):
        source_text = "We served 1,514 cancer patients and caregivers."
        verdict = check("1,514 cancer patients and caregivers", source_text, [])
        source = SourceText(section_text=source_text, tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_defect_metric_gets_tier_c(self):
        source_text = "We invested $2.8 million in community programs."
        verdict = check("$2.8M", source_text, [])
        source = SourceText(section_text=source_text, tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_QUARANTINE

    def test_certified_pdftotext_gets_tier_a(self):
        """0060: certified pdftotext-repaired → Tier A (verified)."""
        source_text = "Total revenue was $5,000,000."
        verdict = check("Total revenue was $5,000,000", source_text, [])
        source = SourceText(section_text=source_text, tables=[], source="pdftotext-repaired")
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_scanned_fallback_gets_tier_b(self):
        """0060: scanned doc with tier_hint → Tier B."""
        source_text = "Total revenue was $5,000,000."
        verdict = check("Total revenue was $5,000,000", source_text, [])
        source = SourceText(section_text=source_text, tables=[], source="docling", tier_hint=TIER_UNVERIFIED_OCR)
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_OCR

    def test_missing_source_gets_tier_c(self):
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, None) == TIER_QUARANTINE
