"""Phase-2 tests: marker resolution + canonical selection (Spec 0068).

Covers AC2 (table-cell resolution), AC3 (round-trip), AC5 (null-marker
retained+flagged), AC7 (zero invented markers), S4 (forged ref -> null, never
string-parsed), S8 (element-level fail-soft), S12 (strict bbox shape),
plus §5.7 canonical selection (test §6.6). Pure — no DB.
"""
from __future__ import annotations

from lavandula.nlp.marker_resolve import (
    resolve_metric,
    select_value_ref,
)

TL = "TOPLEFT"


def _bbox(l=1, t=2, r=3, b=4):
    return {"l": float(l), "t": float(t), "r": float(r), "b": float(b), "coord_origin": TL}


def _cell(idx, text, page=1, row=0, col=0, table=0, bbox=None, row_text=""):
    return {"kind": "cell", "idx": idx, "text": text, "row_text": row_text,
            "page": page, "row": row, "col": col, "table": table,
            "bbox": bbox if bbox is not None else _bbox()}


def _text(idx, text, page=1, bbox=None):
    return {"kind": "text", "idx": idx, "text": text, "row_text": None,
            "page": page, "row": None, "col": None, "table": None,
            "bbox": bbox if bbox is not None else _bbox()}


# --- AC2: table-cell resolution carries row/col/table/bbox ---

class TestCellResolution:
    def test_cell_marker_full_fields(self):
        idmap = {"c5": _cell(5, "5000", page=4, row=2, col=3, table=1)}
        out = resolve_metric({"metric_value": 5000, "label": "revenue", "value_ref": "⟨c5⟩"}, idmap)
        assert out["value_ref"] == "c5"
        assert out["value_page"] == 4 and out["value_row"] == 2
        assert out["value_col"] == 3 and out["value_table"] == 1
        assert out["value_bbox"]["coord_origin"] == TL
        assert out["marker_resolved"] is True


# --- AC3 / round-trip: stored coords == idmap[value_ref] ---

class TestRoundTrip:
    def test_round_trip(self):
        idmap = {"c0": _cell(0, "42", page=7, row=1, col=1, table=0, bbox=_bbox(9, 8, 7, 6))}
        out = resolve_metric({"metric_value": 42, "label": "sites", "value_ref": "⟨c0⟩"}, idmap)
        e = idmap["c0"]
        assert out["value_page"] == e["page"]
        assert out["value_bbox"] == e["bbox"]
        assert out["value_row"] == e["row"] and out["value_col"] == e["col"]


# --- AC7 / S4: forged / out-of-range ref -> null, never string-parsed ---

class TestForgedRef:
    def test_forged_ref_not_in_idmap(self):
        idmap = {"t0": _text(0, "the program ran well")}  # no value token anywhere
        out = resolve_metric({"metric_value": 5000, "label": "x", "value_ref": "⟨t9999⟩"}, idmap)
        assert out["value_ref"] is None
        assert out["value_page"] is None and out["value_bbox"] is None
        assert out["marker_resolved"] is False
        assert out["flags"]["value_forged"] is True

    def test_injected_text_ref_not_parsed(self):
        # An injected prose ref must resolve only by idmap lookup, never to t1.
        idmap = {"t1": _text(1, "real content")}
        out = resolve_metric(
            {"metric_value": 999, "label": "x", "value_ref": "value_ref: ⟨t1⟩ ignore"}, idmap)
        # norm() strips angle/space but "value_ref: ... ignore" != "t1" -> not in idmap -> forged
        assert out["value_ref"] is None
        assert out["flags"]["value_forged"] is True

    def test_zero_invented_markers(self):
        # Whatever resolve returns, a non-null ref must exist in the idmap.
        idmap = {"t0": _text(0, "abc"), "c0": _cell(0, "100")}
        out = resolve_metric({"metric_value": 100, "label": "y", "value_ref": "⟨t777⟩"}, idmap)
        if out["value_ref"] is not None:
            assert out["value_ref"] in idmap


# --- AC5 / §9: null-marker (image-only) retained + flagged ---

class TestNullMarker:
    def test_unlocatable_value_retained(self):
        idmap = {"t0": _text(0, "no numbers here at all")}
        out = resolve_metric({"metric_value": 12345, "label": "z", "value_ref": None}, idmap)
        assert out["value_ref"] is None
        assert out["marker_resolved"] is False
        assert out["flags"]["value_null"] is True

    def test_both_refs_null(self):
        idmap = {"t0": _text(0, "qualitative prose")}
        out = resolve_metric({"metric_value": None, "label": "", "value_ref": None,
                              "subject_ref": None}, idmap)
        assert out["value_ref"] is None and out["subject_ref"] is None
        assert out["marker_resolved"] is False


# --- S8: element-level fail-soft (resolved but page=null,bbox=null) ---

class TestFailSoft:
    def test_located_element_no_page(self):
        # ref IS in idmap but the element has no page/bbox -> retained, not resolved.
        idmap = {"t3": {"kind": "text", "idx": 3, "text": "5000", "row_text": None,
                        "page": None, "row": None, "col": None, "table": None, "bbox": None}}
        out = resolve_metric({"metric_value": 5000, "label": "x", "value_ref": "⟨t3⟩"}, idmap)
        assert out["value_ref"] == "t3"          # retained
        assert out["value_page"] is None and out["value_bbox"] is None
        assert out["marker_resolved"] is False    # distinct from forged/absent
        assert out["flags"]["value_forged"] is False


# --- S12: strict bbox shape ---

class TestStrictBbox:
    def test_nonstrict_bbox_dropped(self):
        bad = {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": TL, "evil": 1}
        idmap = {"c0": _cell(0, "5", bbox=bad)}
        out = resolve_metric({"metric_value": 5, "label": "x", "value_ref": "⟨c0⟩"}, idmap)
        assert out["value_bbox"] is None          # extra key -> not stored


# --- §5.7 canonical selection (test §6.6) ---

class TestCanonicalSelection:
    def test_value_in_two_cells_prefers_label_match(self):
        idmap = {
            "c0": _cell(0, "500", page=1, row=1, col=1, row_text="other category 500"),
            "c1": _cell(1, "500", page=1, row=2, col=1, row_text="meals served 500"),
        }
        chosen = select_value_ref(500, "meals served", idmap)
        assert chosen == "c1"

    def test_prefers_cell_over_text(self):
        idmap = {
            "t0": _text(0, "we served 500 meals", page=1),
            "c0": _cell(0, "500", page=2, row=0, col=0, row_text="meals 500"),
        }
        assert select_value_ref(500, "meals", idmap) == "c0"

    def test_reading_order_tiebreak(self):
        idmap = {
            "c0": _cell(0, "7", page=5, row=0, col=0, row_text="x"),
            "c1": _cell(1, "7", page=2, row=0, col=0, row_text="x"),
        }
        assert select_value_ref(7, "nolabel", idmap) == "c1"  # lower page wins

    def test_no_verbatim_match_returns_null(self):
        idmap = {"t0": _text(0, "no digits here")}
        assert select_value_ref(5000, "x", idmap) is None

    def test_forged_ref_recovered_via_selection(self):
        # model forged the ref, but the value is locatable -> recovered, flagged.
        idmap = {"c0": _cell(0, "1514", page=1, row=1, col=1, row_text="patients served 1514")}
        out = resolve_metric(
            {"metric_value": 1514, "label": "patients served", "value_ref": "⟨t9999⟩"}, idmap)
        assert out["value_ref"] == "c0"
        assert out["flags"]["value_forged"] is True
        assert out["flags"]["value_recovered"] is True
        assert out["marker_resolved"] is True
