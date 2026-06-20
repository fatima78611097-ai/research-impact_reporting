"""Mispairing — FLAG a split metric whose value and label sit physically far apart.

Geometry only; ported from nlp/mispairing_check thresholds (dy<60 = same row; dx<160 & dy<160
= same card; else far-apart suspect; different pages = suspect). FLAGS, never quarantines —
far-apart-but-correct pairs burned us before (the model's authoring is usually right; this is
a "worth a look", not a verdict). Inline metrics (same marker) have no pairing risk.
"""
from __future__ import annotations

from ..types import Metric, CheckResult


def _mid(b: dict) -> tuple[float, float]:
    return ((b["l"] + b["r"]) / 2, (b["t"] + b["b"]) / 2)


def mispairing(m: Metric) -> CheckResult:
    p = m.prov
    if p.same_marker:
        return CheckResult()                                   # inline — no pairing
    if p.value_page and p.subject_page and p.value_page != p.subject_page:
        return CheckResult("flag", "value & label on different pages", flag="mispair_suspect")
    if not (p.value_bbox and p.subject_bbox):
        return CheckResult()                                   # can't judge
    (vx, vy), (sx, sy) = _mid(p.value_bbox), _mid(p.subject_bbox)
    dx, dy = abs(vx - sx), abs(vy - sy)
    if dy < 60:
        return CheckResult()                                   # same row
    if dx < 160 and dy < 160:
        return CheckResult()                                   # same card
    return CheckResult("flag", f"value & label far apart (dx={int(dx)} dy={int(dy)})", flag="mispair_suspect")
