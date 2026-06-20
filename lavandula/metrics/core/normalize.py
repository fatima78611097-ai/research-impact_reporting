"""Normalize: the model's raw metrics + the marker map -> Metric records.

Resolves each metric's value_ref / subject_ref to its page, bbox, and text, and keeps the
model's `metric_text` as the `statement` (never rewritten). This is the only place raw model
output becomes the locked Metric contract — both harnesses normalize the same way.

Lifted from the spike's regen_2_model_text (the record-building half, minus the image render,
which is presentation = the dev output adapter's job).
"""
from __future__ import annotations

from .types import Metric, Provenance


def _resolve(idmap: dict, ref: str) -> dict:
    ref = str(ref or "").strip("⟨⟩ ")
    return idmap.get(ref) or idmap.get(f"⟨{ref}⟩") or {}


def normalize(raw_metrics: list[dict], idmap: dict, *, content_sha256: str,
              org: str | None = None, report_year: int | None = None,
              url: str | None = None) -> list[Metric]:
    """raw model metrics -> list[Metric] with provenance resolved. statement = the model's text."""
    out: list[Metric] = []
    for idx, m in enumerate(raw_metrics):
        vr = str(m.get("value_ref", "")).strip("⟨⟩ ")
        sr = str(m.get("subject_ref", "")).strip("⟨⟩ ")
        vc = _resolve(idmap, vr)
        sc = _resolve(idmap, sr)
        prov = Provenance(
            value_page=vc.get("page"), subject_page=sc.get("page"),
            value_ref=vr or None, subject_ref=sr or None,
            value_bbox=vc.get("bbox"), subject_bbox=sc.get("bbox"),
            value_text=vc.get("text"), subject_text=sc.get("text"),
            source_snippet=m.get("source_snippet"),
            same_marker=(vr == sr),
        )
        out.append(Metric(
            content_sha256=content_sha256, idx=idx, org=org, report_year=report_year, url=url,
            statement=m.get("metric_text") or "",
            value=m.get("metric_value"),
            value_text=(str(m.get("metric_value")) if m.get("metric_value") is not None else None),
            unit=m.get("unit"), tier=m.get("tier"),
            subject=sc.get("text") or "",
            prov=prov,
        ))
    return out
