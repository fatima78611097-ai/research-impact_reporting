"""Phase-1 tests for the tagged render module (Spec 0068).

Covers S1 (item ceiling), S2 (id-collision fail-safe), S3 (bbox sanitization),
S6 (deterministic ids), S7 (truncation retains table cells). Pure — no DB.
"""
from __future__ import annotations

import math

import pytest

from lavandula.nlp.marker_render import (
    MAX_TAGGED_CHARS,
    RenderResult,
    SkipDocument,
    _assign,
    build_render,
    sanitize_bbox,
)

TL = "TOPLEFT"


def _bbox(l=10, t=20, r=30, b=40, co=TL):
    return {"l": l, "t": t, "r": r, "b": b, "coord_origin": co}


def _section(text, page=1, bbox=None):
    return ("body " + text, [{"text": text, "locations": [{"page_no": page, "bbox": bbox or _bbox()}]}])


def _table(page, cells):
    return (page, cells)


def _cell(row, col, text, bbox=None):
    return {"row": row, "col": col, "text": text, "bbox": bbox or _bbox()}


# --- happy path / structure ---

class TestStructure:
    def test_text_and_table_markers(self):
        sections = [_section("100 people served", page=2)]
        tables = [_table(3, [_cell(1, 1, "Revenue"), _cell(1, 2, "5000")])]
        r = build_render(sections, tables, {2: (600.0, 800.0), 3: (600.0, 800.0)})
        # one text marker, two cell markers
        assert sorted(k[0] for k in r.idmap) == ["c", "c", "t"]
        t0 = r.idmap["t0"]
        assert t0["kind"] == "text" and t0["page"] == 2 and t0["text"] == "100 people served"
        c1 = r.idmap["c1"]
        assert c1["kind"] == "cell" and c1["row"] == 1 and c1["col"] == 2
        assert c1["table"] == 0 and c1["text"] == "5000"
        assert "⟨t0⟩" in r.tagged_text and "⟨c0⟩" in r.tagged_text
        assert "[table on page 3]" in r.tagged_text

    def test_cell_bbox_resolves(self):
        bb = _bbox(1, 2, 3, 4)
        tables = [_table(1, [_cell(0, 0, "x", bbox=bb)])]
        r = build_render([], tables, {1: (600.0, 800.0)})
        assert r.idmap["c0"]["bbox"] == {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": TL}


# --- S6: deterministic ids ---

class TestDeterministicIds:
    def test_same_rows_same_idmap(self):
        sections = [_section("a", page=1), _section("b", page=1)]
        tables = [_table(1, [_cell(1, 1, "x"), _cell(1, 2, "y")])]
        r1 = build_render(sections, tables, {1: (600.0, 800.0)})
        r2 = build_render(sections, tables, {1: (600.0, 800.0)})
        assert r1.idmap == r2.idmap
        assert r1.tagged_text == r2.tagged_text

    def test_cells_ordered_row_then_col(self):
        # Out-of-order cells must still get ids in (row, col) order.
        cells = [_cell(2, 1, "d"), _cell(1, 2, "b"), _cell(1, 1, "a"), _cell(2, 0, "c")]
        r = build_render([], [_table(1, cells)], {1: (600.0, 800.0)})
        texts = [r.idmap[f"c{i}"]["text"] for i in range(4)]
        assert texts == ["a", "b", "c", "d"]


# --- S1: item ceiling (table bomb) ---

class TestItemCeiling:
    def test_over_cap_skips(self):
        cells = [_cell(i // 10, i % 10, str(i)) for i in range(50)]
        with pytest.raises(SkipDocument) as ei:
            build_render([], [_table(1, cells)], {1: (600.0, 800.0)}, max_idmap_items=20)
        assert ei.value.reason == "idmap_too_large"

    def test_text_items_count_toward_ceiling(self):
        sections = [_section(f"item {i}", page=1) for i in range(30)]
        with pytest.raises(SkipDocument) as ei:
            build_render(sections, [], {1: (600.0, 800.0)}, max_idmap_items=10)
        assert ei.value.reason == "idmap_too_large"

    def test_under_cap_ok(self):
        cells = [_cell(0, i, str(i)) for i in range(5)]
        r = build_render([], [_table(1, cells)], {1: (600.0, 800.0)}, max_idmap_items=20)
        assert r.item_count == 5


# --- S2: id-collision fail-safe ---

class TestIdCollision:
    def test_assign_duplicate_raises(self):
        idmap = {}
        _assign(idmap, "t0", {"kind": "text"})
        with pytest.raises(SkipDocument) as ei:
            _assign(idmap, "t0", {"kind": "text"})
        assert ei.value.reason == "idmap_id_collision"


# --- S3: bbox sanitization ---

class TestBboxSanitization:
    def test_good_bbox(self):
        assert sanitize_bbox(_bbox(1, 2, 3, 4), (600, 800)) == {
            "l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": TL}

    def test_nan_rejected(self):
        assert sanitize_bbox(_bbox(l=float("nan")), (600, 800)) is None

    def test_inf_rejected(self):
        assert sanitize_bbox(_bbox(t=float("inf")), (600, 800)) is None

    def test_negative_rejected(self):
        assert sanitize_bbox(_bbox(l=-50), (600, 800)) is None

    def test_out_of_range_rejected(self):
        assert sanitize_bbox(_bbox(r=5000), (600, 800)) is None

    def test_minor_overshoot_clamped(self):
        out = sanitize_bbox(_bbox(r=601), (600, 800))  # 1pt over, within tol
        assert out["r"] == 600.0

    def test_unknown_coord_origin_rejected(self):
        assert sanitize_bbox(_bbox(co="SPIRAL"), (600, 800)) is None

    def test_non_numeric_rejected(self):
        assert sanitize_bbox(_bbox(l="10"), (600, 800)) is None

    def test_bool_rejected(self):
        assert sanitize_bbox(_bbox(l=True), (600, 800)) is None

    def test_strict_shape_no_extra_keys(self):
        raw = _bbox(1, 2, 3, 4)
        raw["evil"] = {"nested": [1, 2, 3]}
        raw["page_no"] = 7
        out = sanitize_bbox(raw, (600, 800))
        assert set(out) == {"l", "t", "r", "b", "coord_origin"}

    def test_not_a_dict_rejected(self):
        assert sanitize_bbox("⟨t1⟩", (600, 800)) is None
        assert sanitize_bbox(None, (600, 800)) is None

    def test_no_page_dims_keeps_finite(self):
        # No page dims -> no range check, but still numeric/finite validated.
        out = sanitize_bbox(_bbox(1, 2, 3, 4), None)
        assert out["l"] == 1.0
        assert sanitize_bbox(_bbox(l=float("inf")), None) is None

    def test_malformed_bbox_resolves_null_in_idmap(self):
        # Fail-soft: a malformed cell bbox -> bbox=null, element retained.
        tables = [_table(1, [_cell(0, 0, "x", bbox=_bbox(l=float("nan")))])]
        r = build_render([], tables, {1: (600.0, 800.0)})
        assert r.idmap["c0"]["bbox"] is None
        assert r.idmap["c0"]["text"] == "x"


# --- S7: truncation retains table cells, drops narrative first ---

class TestTruncation:
    def test_narrative_dropped_tables_kept(self):
        # Lots of narrative + one table; tiny cap so not everything fits.
        sections = [_section("narrative line number %d here padding" % i, page=1) for i in range(40)]
        tables = [_table(1, [_cell(0, 0, "KEEPCELL"), _cell(0, 1, "9999")])]
        r = build_render(sections, tables, {1: (600.0, 800.0)}, max_chars=200)
        assert r.truncated is True
        assert r.dropped_narrative > 0
        # The table cells survive into the tagged input + idmap.
        assert "⟨c0⟩" in r.tagged_text and "KEEPCELL" in r.tagged_text
        assert "c0" in r.idmap and "c1" in r.idmap
        # Dropped narrative ids are pruned from idmap (can't be cited).
        kept_text_ids = [k for k in r.idmap if k.startswith("t")]
        assert len(kept_text_ids) < 40

    def test_no_truncation_when_under_cap(self):
        sections = [_section("short", page=1)]
        r = build_render(sections, [], {1: (600.0, 800.0)}, max_chars=MAX_TAGGED_CHARS)
        assert r.truncated is False
        assert r.dropped_narrative == 0
