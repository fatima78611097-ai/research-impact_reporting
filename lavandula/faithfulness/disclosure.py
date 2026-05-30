"""Disclosure contract for faithfulness verification tiers (Spec 0057 §4.3).

Defines the tier→badge mapping and query filters for downstream consumers.
Published surfaces (API, viewer, AI interviewer) use these functions to
determine what to show and how to label it.
"""
from __future__ import annotations

from lavandula.faithfulness.gate_runner import (
    TIER_QUARANTINE,
    TIER_UNVERIFIED_LEGACY,
    TIER_UNVERIFIED_OCR,
    TIER_UNVERIFIED_PENDING,
    TIER_VERIFIED,
)

PUBLISHABLE_TIERS = frozenset({TIER_VERIFIED, TIER_UNVERIFIED_OCR})

TIER_BADGES = {
    TIER_VERIFIED: {"label": "sourced", "symbol": "✓", "publishable": True},
    TIER_UNVERIFIED_OCR: {"label": "OCR — unverified", "symbol": None, "publishable": True},
    TIER_UNVERIFIED_PENDING: {"label": "pending verification", "symbol": None, "publishable": False},
    TIER_QUARANTINE: {"label": "quarantined", "symbol": None, "publishable": False},
    TIER_UNVERIFIED_LEGACY: {"label": "legacy — unverified", "symbol": None, "publishable": False},
}


def is_publishable(tier: str) -> bool:
    """Whether a fact with this tier may appear in published surfaces."""
    return tier in PUBLISHABLE_TIERS


def get_badge(tier: str) -> dict:
    """Get the display badge for a verification tier."""
    return TIER_BADGES.get(tier, TIER_BADGES[TIER_UNVERIFIED_LEGACY])


def tier_filter_sql() -> str:
    """SQL WHERE clause fragment excluding unpublishable tiers.

    Use in queries that feed published surfaces (API, viewer, reports).
    """
    return "verification_tier IN ('verified', 'unverified_ocr')"


def api_fields(
    tier: str,
    tier_b_confidence: float | None = None,
) -> dict:
    """Fields to include in API responses for a fact.

    Exposes verification_tier and badge. For Tier B, includes confidence.
    Tier C facts should never reach this function (filtered by query).
    """
    badge = get_badge(tier)
    result = {
        "verification_tier": tier,
        "badge_label": badge["label"],
        "badge_symbol": badge["symbol"],
    }
    if tier == TIER_UNVERIFIED_OCR and tier_b_confidence is not None:
        result["ocr_confidence"] = round(tier_b_confidence, 3)
    return result
