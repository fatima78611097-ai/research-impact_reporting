"""Tagged render for the coordinate-handoff marker contract (Spec 0068).

Productionizes the research `comp-metric-regression/render.py::render_tagged`.
Reads a document's parse rows (``lava_parse.sections.source_locations`` +
``lava_parse.tables.cell_locations``) and emits:

    - ``section_text``  — plain section body text (the SELECT prompt input)
    - ``tagged_text``   — the same content with a per-element source marker
                          appended (``⟨t42⟩`` text element, ``⟨c17⟩`` table cell)
    - ``idmap``         — marker id -> resolved location
                          ``{kind, idx, text, page, bbox, row, col, table, row_text}``

IDs are **document-local and render-deterministic**: the same parse rows always
produce the same ids (fixed traversal order: sections by ``section_index``, then
tables by ``table_index``, cells by ``row`` then ``col``).

Hardening over the research code (spec §5.8):
  - ``MAX_IDMAP_ITEMS`` ceiling — a table-bomb doc is skipped (``idmap_too_large``)
    before any heavy string building, bounding CPU/memory.
  - ID-uniqueness assertion — a key collision fails safe (``idmap_id_collision``),
    never a silent overwrite.
  - bbox sanitization + clamp — every stored bbox is numeric, finite, has a
    Docling-declared ``coord_origin``, and is clamped to the page dimensions; a
    malformed / out-of-range bbox resolves ``bbox=null`` (fail-soft).
  - truncation prioritizing table cells — when the char cap is exceeded, table
    cells are retained and low-value narrative is dropped first; ``truncated`` is
    set and the dropped counts recorded so an unlocated tail metric is not
    mistaken for an image-only metric.

This module is pure and DB-read-only: ``build_render`` takes already-fetched
rows and has no DB/network dependency (so the bulk of the tests need no DB);
``render_tagged`` is the thin DB wrapper.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

# Render-module version stamped on every metric row so an idmap regeneration is
# auditable (plan §3 / Codex LOW). Bump when render/idmap semantics change.
RENDER_VERSION = "marker-render-0068.v1"

# --- Tunable bounds (spec §5.1/§5.8; plan §4 working defaults, Phase-0 confirmed) ---
MAX_TAGGED_CHARS = 60_000           # LLM input char cap (matches Spec 0051)
MAX_IDMAP_ITEMS = 8_000             # hard ceiling on text elements + cells (table-bomb guard)
COORD_ORIGINS = ("TOPLEFT", "BOTTOMLEFT")  # the Docling-declared coord_origin set
_CLAMP_TOL = 2.0                    # points of rounding overshoot we clamp (vs reject)


class SkipDocument(Exception):
    """A document that must not be rendered/extracted at all (fail-safe skip).

    ``reason`` is a stable machine token surfaced in the run report
    (``idmap_too_large`` | ``idmap_id_collision``).
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class RenderResult:
    section_text: str
    tagged_text: str
    idmap: dict[str, dict]
    truncated: bool = False
    dropped_narrative: int = 0          # narrative text items evicted by truncation
    dropped_chars: int = 0              # chars of content evicted
    item_count: int = 0                 # text elements + cells assigned an id
    meta: dict[str, Any] = field(default_factory=dict)


def norm(x: Any) -> str | None:
    """Normalize a model-emitted ref to a bare id token (strip ⟨⟩/whitespace).

    Returns ``None`` for non-strings. Does NOT validate that the result is a
    real marker — resolution is by idmap lookup only (never by parsing the id).
    """
    return x.strip().strip("⟨⟩").strip() if isinstance(x, str) else None


def sanitize_bbox(raw: Any, page_dim: tuple[float, float] | None) -> dict | None:
    """Return a strict ``{l,t,r,b,coord_origin}`` scalar bbox, or ``None``.

    Rejects (``None``) anything non-finite / non-numeric, an unknown
    ``coord_origin``, or coordinates grossly out of the page bounds. Minor
    rounding overshoot (``<= _CLAMP_TOL`` points) is clamped into ``[0, dim]``.
    Output is exactly the five expected keys with float scalars — no nested
    objects or extra keys ever reach storage (jsonb-DoS guard, spec §5.8/S12).
    """
    if not isinstance(raw, dict):
        return None
    co = raw.get("coord_origin")
    if co not in COORD_ORIGINS:
        return None
    out: dict[str, Any] = {}
    for k in ("l", "t", "r", "b"):
        v = raw.get(k)
        # bool is an int subclass — exclude it explicitly.
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        out[k] = float(v)
    if page_dim:
        w, h = page_dim
        for k, maxd in (("l", w), ("r", w), ("t", h), ("b", h)):
            v = out[k]
            if v < -_CLAMP_TOL or (maxd and v > maxd + _CLAMP_TOL):
                return None  # out-of-range -> reject (fail-soft to bbox=null)
            if maxd:
                out[k] = min(max(v, 0.0), float(maxd))
            else:
                out[k] = max(v, 0.0)
    out["coord_origin"] = co
    return out


def _assign(idmap: dict, key: str, entry: dict) -> None:
    """Insert an idmap entry, asserting uniqueness (collision -> fail-safe skip)."""
    if key in idmap:
        raise SkipDocument("idmap_id_collision", key)
    idmap[key] = entry


def build_render(
    sections: list[tuple],
    tables: list[tuple],
    page_dims: dict[int, tuple[float, float]] | None = None,
    *,
    max_idmap_items: int = MAX_IDMAP_ITEMS,
    max_chars: int = MAX_TAGGED_CHARS,
) -> RenderResult:
    """Pure render. No DB, no network.

    ``sections``  : list of ``(body_text, source_locations)`` ordered by section_index.
    ``tables``    : list of ``(page_number, cell_locations)`` ordered by table_index.
    ``page_dims`` : ``{page_no: (width, height)}`` for bbox clamping (optional).
    """
    page_dims = page_dims or {}
    idmap: dict[str, dict] = {}
    # Each item: (priority, page, order, rendered_str, ids_in_item)
    #   priority 0 = table block (retain first), 1 = narrative (drop first).
    items: list[tuple[int, int, int, str, list[str]]] = []
    sectext_parts: list[str] = []
    t_n = 0
    c_n = 0
    n_items = 0
    order = 0

    def _bbox_for(raw, page):
        return sanitize_bbox(raw, page_dims.get(page) if page is not None else None)

    # --- sections / text elements ---
    for sec in sections:
        body, sl = sec[0], sec[1]
        heading = sec[2] if len(sec) > 2 else None    # optional 3rd element (back-compatible)
        if body:
            sectext_parts.append(body)
        if sl:
            for it in sl:
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                n_items += 1
                if n_items > max_idmap_items:
                    raise SkipDocument("idmap_too_large", f">{max_idmap_items} items")
                loc = (it.get("locations") or [{}])[0]
                page = loc.get("page_no")
                tid = f"t{t_n}"; t_n += 1
                _assign(idmap, tid, {
                    "kind": "text", "idx": int(tid[1:]), "text": txt,
                    "page": page, "bbox": _bbox_for(loc.get("bbox"), page),
                    "row": None, "col": None, "table": None, "row_text": None,
                    "heading": heading,
                })
                items.append((1, page or 0, order, f"{txt} ⟨{tid}⟩", [tid]))
                order += 1
        elif body:
            # No per-element locations: one fail-soft text element for the whole body.
            n_items += 1
            if n_items > max_idmap_items:
                raise SkipDocument("idmap_too_large", f">{max_idmap_items} items")
            tid = f"t{t_n}"; t_n += 1
            _assign(idmap, tid, {
                "kind": "text", "idx": int(tid[1:]), "text": body.strip(),
                "page": None, "bbox": None,
                "row": None, "col": None, "table": None, "row_text": None,
                "heading": heading,
            })
            items.append((1, 0, order, f"{body.strip()} ⟨{tid}⟩", [tid]))
            order += 1

    # --- tables / cells ---
    for ti, (page, cl) in enumerate(tables):
        if not cl:
            continue
        rows: dict[Any, list] = {}
        for cell in cl:
            rows.setdefault(cell.get("row"), []).append(cell)
        lines = [f"[table on page {page}]"]
        ids_here: list[str] = []
        for r in sorted(rows, key=lambda x: (x is None, x)):
            rowtext = " ".join((cc.get("text") or "") for cc in rows[r])
            parts = []
            for cell in sorted(rows[r], key=lambda cc: (cc.get("col") is None, cc.get("col"))):
                n_items += 1
                if n_items > max_idmap_items:
                    raise SkipDocument("idmap_too_large", f">{max_idmap_items} items")
                cid = f"c{c_n}"; c_n += 1
                _assign(idmap, cid, {
                    "kind": "cell", "idx": int(cid[1:]),
                    "text": (cell.get("text") or "").strip(), "row_text": rowtext,
                    "page": page, "row": cell.get("row"), "col": cell.get("col"),
                    "table": ti, "bbox": _bbox_for(cell.get("bbox"), page),
                })
                parts.append(f"{(cell.get('text') or '').strip()} ⟨{cid}⟩")
                ids_here.append(cid)
            lines.append(f"row {r}: " + " | ".join(parts))
        items.append((0, page or 0, order, "\n".join(lines), ids_here))
        order += 1

    item_count = t_n + c_n
    section_text = "\n".join(sectext_parts)[:max_chars]

    # --- assemble tagged_text under the char cap, table cells first (spec §5.1) ---
    # Greedy fill by (priority, reading order); emit selected items in reading order.
    selected: set[int] = set()
    used = 0
    for prio, page, ordr, rendered, ids in sorted(items, key=lambda x: (x[0], x[1], x[2])):
        add = len(rendered) + 1  # +1 for the join newline
        if used + add > max_chars:
            continue
        selected.add(ordr)
        used += add

    truncated = len(selected) < len(items)
    dropped_narrative = 0
    dropped_chars = 0
    kept_ids: set[str] = set()
    out_lines: list[str] = []
    for prio, page, ordr, rendered, ids in sorted(items, key=lambda x: (x[1], x[2])):
        if ordr in selected:
            out_lines.append(rendered)
            kept_ids.update(ids)
        else:
            dropped_chars += len(rendered)
            if prio == 1:
                dropped_narrative += 1

    # idmap holds only what survived into the tagged input — a dropped marker can
    # never be cited (and a forged id matching a dropped one cannot resolve).
    if truncated:
        idmap = {k: v for k, v in idmap.items() if k in kept_ids}

    tagged_text = "\n".join(out_lines)
    return RenderResult(
        section_text=section_text,
        tagged_text=tagged_text,
        idmap=idmap,
        truncated=truncated,
        dropped_narrative=dropped_narrative,
        dropped_chars=dropped_chars,
        item_count=item_count,
        meta={"kept_items": len(kept_ids), "total_items": item_count},
    )


def _fetch_page_dims(conn, sha: str) -> dict[int, tuple[float, float]]:
    rows = conn.execute(text(
        "SELECT page_no, width, height FROM lava_parse.pages WHERE content_sha256=:s"
    ), {"s": sha}).fetchall()
    out = {}
    for page_no, w, h in rows:
        if page_no is not None and w is not None and h is not None:
            out[int(page_no)] = (float(w), float(h))
    return out


def render_tagged(conn, sha: str, **kwargs) -> RenderResult:
    """Fetch a doc's parse rows and render. DB read-only; no network.

    Raises ``SkipDocument`` on ``idmap_too_large`` / ``idmap_id_collision``.
    """
    secs = conn.execute(text(
        "SELECT body_text, source_locations, heading FROM lava_parse.sections "
        "WHERE content_sha256=:s ORDER BY section_index"
    ), {"s": sha}).fetchall()
    tabs = conn.execute(text(
        "SELECT page_number, cell_locations FROM lava_parse.tables "
        "WHERE content_sha256=:s ORDER BY table_index"
    ), {"s": sha}).fetchall()
    page_dims = _fetch_page_dims(conn, sha)
    sections = [(body, sl, heading) for body, sl, heading in secs]
    tables = [(page, cl) for page, cl in tabs]
    return build_render(sections, tables, page_dims, **kwargs)
