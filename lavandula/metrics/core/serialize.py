"""Serialize the pre-gate Metric so an extraction can be FROZEN and re-gated later.

The frozen cache holds the model's output + resolved provenance — everything EXCEPT the gate
result. Re-gating loads these, runs the gate, and rebuilds the viewer with the SAME metric ids
and order, so review scores (keyed by metric id) survive a check change. Only a deliberate
re-extract changes the set.
"""
from __future__ import annotations

from dataclasses import asdict

from .types import Metric, Provenance


def metric_to_dict(m: Metric) -> dict:
    """Pre-gate fields only (decision/reason/flags are recomputed on re-gate)."""
    return {
        "content_sha256": m.content_sha256, "idx": m.idx, "org": m.org,
        "report_year": m.report_year, "url": m.url,
        "statement": m.statement, "value": m.value, "value_text": m.value_text,
        "unit": m.unit, "tier": m.tier, "subject": m.subject, "program": m.program,
        "prov": asdict(m.prov),
    }


def metric_from_dict(d: dict) -> Metric:
    m = Metric(
        content_sha256=d["content_sha256"], idx=d["idx"], org=d.get("org"),
        report_year=d.get("report_year"), url=d.get("url"),
        statement=d.get("statement") or "", value=d.get("value"), value_text=d.get("value_text"),
        unit=d.get("unit"), tier=d.get("tier"), subject=d.get("subject"), program=d.get("program"),
    )
    m.prov = Provenance(**(d.get("prov") or {}))
    return m
