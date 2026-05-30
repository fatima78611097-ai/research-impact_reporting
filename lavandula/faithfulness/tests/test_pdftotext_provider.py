"""Tests for PdftextSourceProvider (Spec 0060 Phase 4).

Tests the provider's decision table from spec §4.3 — all 7 rows plus
borderline cases.
"""
from __future__ import annotations

import pytest

from lavandula.faithfulness.gate_runner import (
    TIER_QUARANTINE,
    TIER_UNVERIFIED_OCR,
    TIER_UNVERIFIED_PENDING,
    TIER_VERIFIED,
    _assign_tier,
)
from lavandula.faithfulness.grounding import Verdict
from lavandula.faithfulness.source_provider import (
    CERTIFICATION_COVERAGE_FLOOR,
    PdftextSourceProvider,
    SourceText,
)


# ============================================================
# Certification logic (pure, no DB)
# ============================================================

class TestCertification:
    def test_certified_text_native_above_floor(self):
        assert PdftextSourceProvider._is_certified("text_native", 0.80, 0.90)

    def test_certified_at_exact_floor(self):
        assert PdftextSourceProvider._is_certified(
            "text_native", CERTIFICATION_COVERAGE_FLOOR, CERTIFICATION_COVERAGE_FLOOR
        )

    def test_not_certified_below_floor(self):
        assert not PdftextSourceProvider._is_certified("text_native", 0.49, 0.99)

    def test_not_certified_scanned(self):
        assert not PdftextSourceProvider._is_certified("scanned", 0.99, 0.99)

    def test_not_certified_failed(self):
        assert not PdftextSourceProvider._is_certified("pdftotext_failed", 0.99, 0.99)

    def test_not_certified_null_source(self):
        assert not PdftextSourceProvider._is_certified(None, 0.99, 0.99)

    def test_not_certified_null_coverage(self):
        assert not PdftextSourceProvider._is_certified("text_native", None, 0.99)
        assert not PdftextSourceProvider._is_certified("text_native", 0.99, None)

    def test_decimal_coverage_from_db(self):
        from decimal import Decimal
        assert PdftextSourceProvider._is_certified(
            "text_native", Decimal("0.95"), Decimal("0.98")
        )

    def test_decimal_below_floor(self):
        from decimal import Decimal
        assert not PdftextSourceProvider._is_certified(
            "text_native", Decimal("0.49"), Decimal("0.49")
        )


# ============================================================
# Decision table (spec §4.3) — tier assignment end-to-end
# ============================================================

class TestDecisionTable:
    """Each test corresponds to one row in the spec's decision table."""

    def test_row1_certified_pdftotext(self):
        """pdftotext exists + text_native + coverage ≥ 0.50 → pdftotext-repaired, Tier A."""
        source = SourceText(
            section_text="pdftotext full text here",
            tables=[],
            source="pdftotext-repaired",
        )
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_row2_uncertified_low_coverage(self):
        """pdftotext exists + text_native + coverage < 0.50 → Docling, unverified_pending."""
        source = SourceText(
            section_text="docling fallback text",
            tables=[],
            source="docling",
            tier_hint=TIER_UNVERIFIED_PENDING,
        )
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_PENDING

    def test_row3_scanned(self):
        """pdftotext exists + scanned → Docling, Tier B (OCR label)."""
        source = SourceText(
            section_text="docling text from scanned doc",
            tables=[],
            source="docling",
            tier_hint=TIER_UNVERIFIED_OCR,
        )
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_OCR

    def test_row4_scanned_no_docling(self):
        """pdftotext exists + scanned + missing Docling → None → quarantine."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, None) == TIER_QUARANTINE

    def test_row5_pdftotext_failed(self):
        """pdftotext exists + pdftotext_failed → Docling, unverified_pending."""
        source = SourceText(
            section_text="docling fallback",
            tables=[],
            source="docling",
            tier_hint=TIER_UNVERIFIED_PENDING,
        )
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_PENDING

    def test_row6_no_pdftotext_row(self):
        """No pdftotext row + Docling exists → Docling, unverified_pending."""
        source = SourceText(
            section_text="docling text",
            tables=[],
            source="docling",
            tier_hint=TIER_UNVERIFIED_PENDING,
        )
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_PENDING

    def test_row7_no_pdftotext_no_docling(self):
        """No pdftotext row + no Docling → None → quarantine."""
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 10)])
        assert _assign_tier(verdict, None) == TIER_QUARANTINE


# ============================================================
# Tier hint mechanics
# ============================================================

class TestTierHint:
    def test_no_tier_hint_defaults_to_verified(self):
        source = SourceText(section_text="x", tables=[], source="docling")
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 5)])
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_tier_hint_overrides_default(self):
        source = SourceText(
            section_text="x", tables=[], source="docling",
            tier_hint=TIER_UNVERIFIED_OCR,
        )
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 5)])
        assert _assign_tier(verdict, source) == TIER_UNVERIFIED_OCR

    def test_ungrounded_overrides_tier_hint(self):
        source = SourceText(
            section_text="x", tables=[], source="docling",
            tier_hint=TIER_VERIFIED,
        )
        verdict = Verdict(grounded=False, rule="none")
        assert _assign_tier(verdict, source) == TIER_QUARANTINE

    def test_none_source_overrides_tier_hint(self):
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 5)])
        assert _assign_tier(verdict, None) == TIER_QUARANTINE


# ============================================================
# Backward compatibility
# ============================================================

class TestBackwardCompat:
    def test_docling_provider_unchanged(self):
        """DoclingSourceProvider returns source='docling', no tier_hint → VERIFIED."""
        source = SourceText(section_text="text", tables=[], source="docling")
        assert source.tier_hint is None
        verdict = Verdict(grounded=True, rule="R1", offsets=[(0, 5)])
        assert _assign_tier(verdict, source) == TIER_VERIFIED

    def test_source_text_default_tier_hint(self):
        source = SourceText(section_text="x", tables=[], source="docling")
        assert source.tier_hint is None
