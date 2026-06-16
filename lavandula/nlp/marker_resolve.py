"""Marker resolution + canonical ref selection (Spec 0068, Phase 2).

Given an LLM-emitted metric (``metric_value``, ``label``, ``value_ref``,
``subject_ref``) and the document's ``idmap`` (from ``marker_render``), resolve
each ref to its snapshotted coordinates and decide ``marker_resolved``.

Trust model (decision — resolves spec §5.7 vs §2 tension):
  - The model's cited ref is **advisory** and is validated by **idmap lookup
    only** (never by parsing the id string — spec §8 anti-forgery). A cited ref
    that exists in the idmap is trusted as-is and stored; 0068 does **not** check
    that the value is verbatim there (that is 0069's job — spec §2).
  - A cited ref that is null / forged / absent from the idmap triggers **§5.7
    canonical selection** over the idmap as a deterministic recovery: the value's
    numeric token (or, for the subject, the label terms) must appear in an
    element; cells are preferred over text; then label-matching row_text; then
    reading order. If nothing matches, the ref is ``null`` (retained + flagged,
    a vision/0069 candidate) — never invented.

This module is pure: no DB, no network. ``resolve_metric`` returns the exact
storage fields (refs + resolved coords + ``marker_resolved``) plus a ``flags``
dict feeding the §5.9 abuse counters (forged / null / unresolved).
"""
from __future__ import annotations

import re
from typing import Any

from lavandula.nlp.marker_render import norm

_BBOX_KEYS = {"l", "t", "r", "b", "coord_origin"}


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s if s is not None else ""))


def _words(s: str | None) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def _strict_bbox(bbox: Any) -> dict | None:
    """Defense-in-depth: only a strict ``{l,t,r,b,coord_origin}`` bbox is stored."""
    if not isinstance(bbox, dict) or set(bbox) != _BBOX_KEYS:
        return None
    return bbox


def _value_candidates(metric_value: Any, idmap: dict) -> list[str]:
    """Marker ids whose text contains the value's digit token (§5.7 rule 1)."""
    vd = _digits(metric_value)
    if not vd:
        return []
    out = []
    for mid, e in idmap.items():
        for tok in re.findall(r"\d[\d,.]*", e.get("text") or ""):
            if vd in _digits(tok):
                out.append(mid)
                break
    return out


def _subject_candidates(label: str | None, idmap: dict) -> list[str]:
    """Marker ids whose text/row_text shares a label word (§5.7 subject)."""
    lw = _words(label)
    if not lw:
        return []
    out = []
    for mid, e in idmap.items():
        etext = (e.get("text") or "") + " " + (e.get("row_text") or "")
        if lw & _words(etext):
            out.append(mid)
    return out


def _select(cands: list[str], idmap: dict, label: str | None) -> str | None:
    """Deterministic §5.7 ordering over candidate ids -> the canonical id.

    Order: prefer cell over text; then a cell whose row_text matches label terms;
    then reading order (page, table, row, col); final tie-break by id index.
    """
    if not cands:
        return None
    lw = _words(label)

    def key(mid: str):
        e = idmap[mid]
        is_text = 0 if e.get("kind") == "cell" else 1          # cell first
        ctx = _words((e.get("row_text") or "") + " " + (e.get("text") or ""))
        label_miss = 0 if (lw and (lw & ctx)) else 1           # label-matching first
        page = e.get("page") if e.get("page") is not None else 10 ** 9
        return (
            is_text, label_miss, page,
            e.get("table") if e.get("table") is not None else -1,
            e.get("row") if e.get("row") is not None else -1,
            e.get("col") if e.get("col") is not None else -1,
            e.get("idx", 0),
        )

    return sorted(cands, key=key)[0]


def select_value_ref(metric_value: Any, label: str | None, idmap: dict) -> str | None:
    """Canonical §5.7 value_ref over the idmap (recovery / disambiguation)."""
    return _select(_value_candidates(metric_value, idmap), idmap, label)


def select_subject_ref(label: str | None, idmap: dict) -> str | None:
    """Canonical §5.7 subject_ref over the idmap (recovery / disambiguation)."""
    return _select(_subject_candidates(label, idmap), idmap, label)


def _resolved_fields(prefix: str, ref: str | None, idmap: dict) -> dict:
    """Snapshot {page,bbox,row,col,table} for a resolved ref (all NULL if none)."""
    e = idmap.get(ref) if ref else None
    return {
        f"{prefix}_ref": ref,
        f"{prefix}_page": e.get("page") if e else None,
        f"{prefix}_bbox": _strict_bbox(e.get("bbox")) if e else None,
        f"{prefix}_row": e.get("row") if e else None,
        f"{prefix}_col": e.get("col") if e else None,
        f"{prefix}_table": e.get("table") if e else None,
    }


def resolve_metric(metric: dict, idmap: dict) -> dict:
    """Resolve one metric's refs. Returns storage fields + ``flags``.

    Never drops a metric: an unlocatable value yields ``value_ref=null`` and
    ``marker_resolved=false`` (retained, vision/0069 candidate).
    """
    flags = {"value_forged": False, "subject_forged": False,
             "value_recovered": False, "subject_recovered": False,
             "value_null": False, "subject_null": False}

    # --- value_ref: trust a valid model pick; else §5.7 recovery ---
    cited_v = norm(metric.get("value_ref"))
    value_ref = None
    if cited_v is not None:
        if cited_v in idmap:
            value_ref = cited_v
        else:
            flags["value_forged"] = True            # cited-but-absent (surge signal)
    if value_ref is None:
        rec = select_value_ref(metric.get("metric_value"), metric.get("label"), idmap)
        if rec is not None:
            value_ref = rec
            flags["value_recovered"] = True

    # --- subject_ref: same contract ---
    cited_s = norm(metric.get("subject_ref"))
    subject_ref = None
    if cited_s is not None:
        if cited_s in idmap:
            subject_ref = cited_s
        else:
            flags["subject_forged"] = True
    if subject_ref is None:
        rec = select_subject_ref(metric.get("label"), idmap)
        if rec is not None:
            subject_ref = rec
            flags["subject_recovered"] = True

    flags["value_null"] = value_ref is None
    flags["subject_null"] = subject_ref is None

    out = {}
    out.update(_resolved_fields("value", value_ref, idmap))
    out.update(_resolved_fields("subject", subject_ref, idmap))

    # marker_resolved: the value is the verifiable anchor — true only when its
    # ref resolves to an element with a known page (element-level fail-soft S8:
    # a ref resolving only to page=null,bbox=null is retained but NOT resolved).
    vloc = idmap.get(value_ref) if value_ref else None
    out["marker_resolved"] = bool(vloc and vloc.get("page") is not None)
    out["flags"] = flags
    return out
