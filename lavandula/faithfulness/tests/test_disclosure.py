"""Tests for the disclosure contract (Spec 0057 Phase 5)."""
from __future__ import annotations

from lavandula.faithfulness.disclosure import (
    api_fields,
    get_badge,
    is_publishable,
    tier_filter_sql,
)
from lavandula.faithfulness.gate_runner import (
    TIER_QUARANTINE,
    TIER_UNVERIFIED_LEGACY,
    TIER_UNVERIFIED_OCR,
    TIER_UNVERIFIED_PENDING,
    TIER_VERIFIED,
)


class TestIsPublishable:
    def test_verified_publishable(self):
        assert is_publishable(TIER_VERIFIED) is True

    def test_ocr_publishable(self):
        assert is_publishable(TIER_UNVERIFIED_OCR) is True

    def test_quarantine_not_publishable(self):
        assert is_publishable(TIER_QUARANTINE) is False

    def test_pending_not_publishable(self):
        assert is_publishable(TIER_UNVERIFIED_PENDING) is False

    def test_legacy_not_publishable(self):
        assert is_publishable(TIER_UNVERIFIED_LEGACY) is False


class TestGetBadge:
    def test_verified_badge(self):
        badge = get_badge(TIER_VERIFIED)
        assert badge["label"] == "sourced"
        assert badge["symbol"] == "✓"
        assert badge["publishable"] is True

    def test_ocr_badge(self):
        badge = get_badge(TIER_UNVERIFIED_OCR)
        assert badge["label"] == "OCR — unverified"
        assert badge["publishable"] is True

    def test_quarantine_badge(self):
        badge = get_badge(TIER_QUARANTINE)
        assert badge["publishable"] is False

    def test_unknown_tier_defaults_to_legacy(self):
        badge = get_badge("some_unknown_tier")
        assert badge["label"] == "legacy — unverified"
        assert badge["publishable"] is False


class TestTierFilterSql:
    def test_contains_publishable_tiers(self):
        sql = tier_filter_sql()
        assert "verified" in sql
        assert "unverified_ocr" in sql
        assert "quarantine" not in sql


class TestApiFields:
    def test_verified_fields(self):
        fields = api_fields(TIER_VERIFIED)
        assert fields["verification_tier"] == TIER_VERIFIED
        assert fields["badge_label"] == "sourced"
        assert fields["badge_symbol"] == "✓"
        assert "ocr_confidence" not in fields

    def test_ocr_with_confidence(self):
        fields = api_fields(TIER_UNVERIFIED_OCR, tier_b_confidence=0.87654)
        assert fields["verification_tier"] == TIER_UNVERIFIED_OCR
        assert fields["ocr_confidence"] == 0.877

    def test_ocr_without_confidence(self):
        fields = api_fields(TIER_UNVERIFIED_OCR)
        assert "ocr_confidence" not in fields
