"""Phase-2 tests: prose-internal de-dup (Spec 0069, §5.4 / AC7) + the decision chain
policies (AC3 unmarked, AC6 mispair-never-relabel, AC12 stale-coords)."""
from __future__ import annotations

from lavandula.nlp import gate_policy as gp


def _cell(text, page=1, table=0, row=0, col=0, idx=0):
    return {"kind": "cell", "text": text, "page": page, "table": table,
            "row": row, "col": col, "idx": idx, "bbox": None, "row_text": text}


def _txt(text, page=1, idx=0):
    return {"kind": "text", "text": text, "page": page, "row": None, "col": None,
            "table": None, "idx": idx, "bbox": None, "row_text": None}


# ============================================================
# de-dup (§5.4 / AC7)
# ============================================================

class TestDedup:
    def test_merges_colocated_identical(self):
        idmap = {"t0": _txt("served 500 shelter families", idx=0),
                 "t1": _txt("served 500 shelter families", idx=1)}
        # same page, both text, proximity weak-accept -> co-located; "shelter" is distinctive
        metrics = [
            {"metric_value": 500, "label": "shelter families served", "value_ref": "t0"},
            {"metric_value": 500, "label": "shelter families served", "value_ref": "t1"},
        ]
        dropped = gp.dedup_indices(metrics, idmap)
        # one of them is dropped, the kept one is lower reading order (idx 0)
        assert len(dropped) == 1
        assert list(dropped.values()) == [0]

    def test_keeps_spatially_distinct(self):
        # AC7: same value+label but on different pages -> NOT merged
        idmap = {"t0": _txt("500 families", page=1, idx=0),
                 "t1": _txt("500 families", page=5, idx=1)}
        metrics = [
            {"metric_value": 500, "label": "families housing served", "value_ref": "t0"},
            {"metric_value": 500, "label": "families housing served", "value_ref": "t1"},
        ]
        assert gp.dedup_indices(metrics, idmap) == {}

    def test_keeps_when_labels_disagree(self):
        idmap = {"t0": _txt("500", idx=0), "t1": _txt("500", idx=1)}
        metrics = [
            {"metric_value": 500, "label": "meals delivered", "value_ref": "t0"},
            {"metric_value": 500, "label": "volunteers recruited", "value_ref": "t1"},
        ]
        assert gp.dedup_indices(metrics, idmap) == {}

    def test_keeps_when_value_differs(self):
        idmap = {"t0": _txt("500", idx=0), "t1": _txt("501", idx=1)}
        metrics = [
            {"metric_value": 500, "label": "families housed", "value_ref": "t0"},
            {"metric_value": 501, "label": "families housed", "value_ref": "t1"},
        ]
        assert gp.dedup_indices(metrics, idmap) == {}

    def test_keeps_both_on_missing_value(self):
        idmap = {"t0": _txt("x", idx=0), "t1": _txt("x", idx=1)}
        metrics = [
            {"metric_value": "qualitative", "label": "impact area", "value_ref": "t0"},
            {"metric_value": "qualitative", "label": "impact area", "value_ref": "t1"},
        ]
        assert gp.dedup_indices(metrics, idmap) == {}


class TestReadingOrder:
    def test_text_before_table(self):
        idmap = {"t0": _txt("x", page=1, idx=0), "c0": _cell("y", page=1, idx=0)}
        assert gp.reading_order_key("t0", idmap) < gp.reading_order_key("c0", idmap)

    def test_lower_page_first(self):
        idmap = {"t0": _txt("x", page=1), "t1": _txt("y", page=3)}
        assert gp.reading_order_key("t0", idmap) < gp.reading_order_key("t1", idmap)


# ============================================================
# decision chain policies
# ============================================================

class TestDecideChain:
    def test_unmarked_quarantine(self):
        dec, reason = gp.decide({"metric_value": 5000, "label": "x"}, None, None, {},
                                marker_resolved=False)
        assert (dec, reason) == ("quarantine", "unmarked")

    def test_stale_coords_quarantine(self):
        dec, reason = gp.decide({"metric_value": 5000, "label": "x"}, "t0", "t0",
                                {"t0": _txt("5000")}, stale=True)
        assert (dec, reason) == ("quarantine", "stale_coords")

    def test_stale_precedes_everything(self):
        # even an unresolved marker: stale wins (precedence)
        dec, reason = gp.decide({"metric_value": 5000, "label": "x"}, None, None, {},
                                marker_resolved=False, stale=True)
        assert reason == "stale_coords"

    def test_publish_passthrough(self):
        idmap = {"t0": _txt("served 5,000 families in shelter")}
        dec, reason = gp.decide({"metric_value": 5000, "label": "families shelter served",
                                 "source_text": "served 5,000 families in shelter"},
                                "t0", "t0", idmap)
        assert (dec, reason) == ("publish", "ok")

    def test_nonmetric_reject_rule(self):
        idmap = {"t0": _txt("celebrating our 45 years of service")}
        dec, reason = gp.decide({"metric_value": 45, "label": "years service",
                                 "source_text": "celebrating our 45 years of service"},
                                "t0", "t0", idmap, measured=True, nonmetric_reject=True)
        assert (dec, reason) == ("quarantine", "not_a_metric_rule")

    def test_mispair_detect_quarantines_not_relabels(self):
        # AC6: value duplicated across two non-total columns of its row -> mispair, the
        # label is never changed (decide returns a decision, not a rewritten metric)
        idmap = {
            "c_h1": _cell("Program A", row=0, col=0),
            "c_h2": _cell("Program B", row=0, col=1),
            "c0": _cell("500", row=1, col=0, idx=2),
            "c1": _cell("500", row=1, col=1, idx=3),
        }
        m = {"metric_value": 500, "label": "people served program a", "source_text": "500 served"}
        dec, reason = gp.decide(m, "c0", "c0", idmap, mispair_detect=True)
        assert (dec, reason) == ("quarantine", "mispair")
        assert m["label"] == "people served program a"   # untouched

    def test_mispair_detect_off_publishes(self):
        idmap = {
            "c_h1": _cell("Program A", row=0, col=0),
            "c_h2": _cell("Program B", row=0, col=1),
            "c0": _cell("500", row=1, col=0, idx=2),
            "c1": _cell("500", row=1, col=1, idx=3),
        }
        m = {"metric_value": 500, "label": "people served program a", "source_text": "500 served"}
        dec, _ = gp.decide(m, "c0", "c0", idmap, mispair_detect=False)
        assert dec == "publish"

    def test_gate_timeout_maps_to_quarantine(self):
        # a clock that jumps past the budget mid-verdict -> decide maps GateTimeout to
        # quarantine/gate_timeout (S4 end-to-end)
        ticks = iter([0.0, 1e9, 1e9, 1e9, 1e9])
        idmap = {"t0": _txt("served 5000 families")}
        dec, reason = gp.decide({"metric_value": 5000, "label": "families served",
                                 "source_text": "served 5000 families"},
                                "t0", "t0", idmap, budget_s=1.0, clock=lambda: next(ticks))
        assert (dec, reason) == ("quarantine", "gate_timeout")
