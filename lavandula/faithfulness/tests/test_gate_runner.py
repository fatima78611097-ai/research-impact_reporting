"""Tests for the faithfulness gate runner (Spec 0057 Phase 3).

Tests the tier assignment logic, story-specific rules, 0060 trust boundary,
context_window exclusion, and determinism — without a real database.
"""
from __future__ import annotations

import pytest

from lavandula.faithfulness.gate_runner import (
    TIER_QUARANTINE,
    TIER_UNVERIFIED_OCR,
    TIER_UNVERIFIED_PENDING,
    TIER_VERIFIED,
    _assign_tier,
    _extract_context_window,
)
from lavandula.faithfulness.grounding import Verdict, check, check_story
from lavandula.faithfulness.source_provider import SourceText, TableRow


# ============================================================
# Tier assignment
# ============================================================

class TestAssignTier:
    def test_grounded_docling_source_is_verified(self):
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        source = SourceText(section_text="x", tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_grounded_pdftotext_certified_is_verified(self):
        """0060: certified pdftotext source (no tier_hint) → Tier A."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        source = SourceText(section_text="x", tables=[], source="pdftotext-repaired")
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_grounded_pdftotext_scanned_is_ocr(self):
        """0060: scanned fallback (tier_hint=unverified_ocr) → Tier B."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        source = SourceText(section_text="x", tables=[], source="docling", tier_hint=TIER_UNVERIFIED_OCR)
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_OCR

    def test_ungrounded_is_quarantine(self):
        verdict = Verdict(grounded=False, rule="none")
        source = SourceText(section_text="x", tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_QUARANTINE

    def test_no_source_is_quarantine(self):
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, None) == TIER_QUARANTINE

    def test_failsafe_no_source_never_verified(self):
        """Fail-safe: absence of source never yields 'verified'."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        tier = _assign_tier(verdict, None)
        assert tier != TIER_VERIFIED


# ============================================================
# Story-specific rules
# ============================================================

class TestStoryTierAssignment:
    def test_story_summary_prose_not_rejected(self):
        """Summary may be abstractive — do NOT apply R1/R2 to summary."""
        source_text = "John received 50 meals through our shelter program last winter."
        verdict = check_story(
            source_snippet="John received 50 meals through our shelter program",
            story_summary="John's life was transformed by comprehensive services.",
            source_text=source_text,
            tables=[],
        )
        source = SourceText(section_text=source_text, tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_story_source_snippet_grounded_plus_quote_grounded(self):
        source_text = 'Maria said "this program saved my life" during the interview.'
        verdict = check_story(
            source_snippet='Maria said "this program saved my life"',
            story_summary='Maria testified: "this program saved my life"',
            source_text=source_text,
            tables=[],
        )
        source = SourceText(section_text=source_text, tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_story_no_snippet_is_quarantine(self):
        verdict = check_story(
            source_snippet=None,
            story_summary="A great story.",
            source_text="Some source.",
            tables=[],
        )
        source = SourceText(section_text="Some source.", tables=[], source="docling")
        assert _assign_tier(verdict, source) == TIER_QUARANTINE


# ============================================================
# 0060 trust boundary
# ============================================================

class TestTrustBoundary:
    def test_tier_hint_unverified_pending(self):
        """0060: provider-set unverified_pending → never 'verified'."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        source = SourceText(section_text="x", tables=[], source="docling", tier_hint=TIER_UNVERIFIED_PENDING)
        tier = _assign_tier(verdict, source)
        assert tier != TIER_VERIFIED
        assert tier == TIER_UNVERIFIED_PENDING

    def test_tier_hint_quarantine(self):
        """0060: provider-set quarantine is respected."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        source = SourceText(section_text="x", tables=[], source="docling", tier_hint=TIER_QUARANTINE)
        assert _assign_tier(verdict, source) == TIER_QUARANTINE


# ============================================================
# Context window exclusion
# ============================================================

class TestContextWindowExclusion:
    def test_context_window_not_used_for_grounding(self):
        """Context window content must NOT affect grounding verdict."""
        source_text = "We served 1,514 cancer patients."
        snippet = "completely fabricated text not in source"

        verdict = check(snippet, source_text, [])
        assert verdict.grounded is False

        context = _extract_context_window(source_text, [(0, 10)])
        assert context is not None

    def test_context_window_bounded(self):
        long_source = "x" * 10000
        context = _extract_context_window(long_source, [(5000, 5050)])
        assert context is not None
        assert len(context) <= 500

    def test_context_window_none_for_empty_offsets(self):
        assert _extract_context_window("some text", []) is None
        assert _extract_context_window("", [(0, 5)]) is None


# ============================================================
# Mixed grounded/defect/OCR fixture
# ============================================================

class TestMixedFixture:
    """Integration-style tests using the verifier + tier assignment together."""

    def test_grounded_metric(self):
        source_text = "We served 1,514 cancer patients and caregivers last year."
        source = SourceText(section_text=source_text, tables=[], source="docling")
        verdict = check("1,514 cancer patients and caregivers", source_text, [])
        tier = _assign_tier(verdict, source)
        assert tier == TIER_VERIFIED
        assert verdict.rule == "R1"

    def test_defect_metric(self):
        source_text = "We invested $2.8 million in community programs."
        source = SourceText(section_text=source_text, tables=[], source="docling")
        verdict = check("$2.8M", source_text, [])
        tier = _assign_tier(verdict, source)
        assert tier == TIER_QUARANTINE

    def test_pdftotext_certified_metric(self):
        """0060: certified pdftotext grounded metric → Tier A."""
        source_text = "Total revenue was $5,000,000 for the fiscal year."
        source = SourceText(section_text=source_text, tables=[], source="pdftotext-repaired")
        verdict = check("Total revenue was $5,000,000", source_text, [])
        tier = _assign_tier(verdict, source)
        assert tier == TIER_VERIFIED

    def test_scanned_fallback_metric(self):
        """0060: scanned doc falls back to Docling → Tier B."""
        source_text = "Total revenue was $5,000,000 for the fiscal year."
        source = SourceText(section_text=source_text, tables=[], source="docling", tier_hint=TIER_UNVERIFIED_OCR)
        verdict = check("Total revenue was $5,000,000", source_text, [])
        tier = _assign_tier(verdict, source)
        assert tier == TIER_UNVERIFIED_OCR

    def test_table_metric(self):
        tables = [[TableRow(cells=["Total expenses", "$26,122,404"])]]
        source = SourceText(section_text="", tables=tables, source="docling")
        verdict = check("Total expenses: $26,122,404", "", tables)
        tier = _assign_tier(verdict, source)
        assert tier == TIER_VERIFIED
        assert verdict.rule == "R2"

    def test_missing_source(self):
        verdict = Verdict(grounded=False, rule="none")
        tier = _assign_tier(verdict, None)
        assert tier == TIER_QUARANTINE


# ============================================================
# Determinism (re-run = identical)
# ============================================================

class TestRunDeterminism:
    def test_same_verdicts_on_rerun(self):
        source_text = "We served 1,514 cancer patients and caregivers."
        snippet = "1,514 cancer patients and caregivers"
        tables = [[TableRow(cells=["Programs", "1514"])]]

        results = []
        for _ in range(5):
            verdict = check(snippet, source_text, tables)
            source = SourceText(section_text=source_text, tables=tables, source="docling")
            tier = _assign_tier(verdict, source)
            results.append((tier, verdict.rule, verdict.offsets))

        for r in results:
            assert r == results[0]
