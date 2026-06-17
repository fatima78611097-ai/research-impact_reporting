"""Phase-4 tests: the marker-grounded gate runner (Spec 0069).

Pure units: gate_document (decide + de-dup), aggregate_report (histogram + triage).
Integration (skipped if no local Postgres): AC2 (every metric decided + report), AC9
(idempotent in-place UPDATE; 0068 payload untouched), AC11 (determinism), S7 (advisory
lock — concurrent runner busy), active-run guard.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from lavandula.nlp import gate_runner as gr
from lavandula.nlp.marker_render import RENDER_VERSION


def _txt(text_, idx=0, page=1):
    return {"kind": "text", "text": text_, "page": page, "row": None, "col": None,
            "table": None, "idx": idx, "bbox": None, "row_text": None}


# ============================================================
# pure: gate_document
# ============================================================

class TestGateDocument:
    def _row(self, mid, value, label, source, value_ref, subject_ref=None, marker_resolved=True):
        return {"id": mid, "metric_value": value, "metric_type": label,
                "source_snippet": source, "value_ref": value_ref,
                "subject_ref": subject_ref or value_ref, "marker_resolved": marker_resolved,
                "parse_version": "docling-x", "render_version": RENDER_VERSION,
                "metric_text": source}

    def test_publish_and_quarantine(self):
        idmap = {"t0": _txt("served 5,000 families in shelter", idx=0),
                 "t1": _txt("our mission statement", idx=1)}
        rows = [
            self._row(1, 5000, "shelter families served", "served 5,000 families in shelter", "t0"),
            self._row(2, 9999, "phantom", "our mission statement", "t1"),
        ]
        decs = gr.gate_document(rows, idmap, stale=False, measure_fn=None, mispair_detect=False)
        by_id = {d.metric_id: d for d in decs}
        assert by_id[1].decision == "publish"
        assert by_id[2].decision == "quarantine" and by_id[2].reason == "value_not_at_marker"

    def test_unmarked_quarantine(self):
        idmap = {}
        rows = [self._row(1, 5000, "x", "y", None, marker_resolved=False)]
        decs = gr.gate_document(rows, idmap, stale=False, measure_fn=None, mispair_detect=False)
        assert decs[0].reason == "unmarked"

    def test_stale_quarantines_all(self):
        idmap = {"t0": _txt("served 5,000 families", idx=0)}
        rows = [self._row(1, 5000, "families served", "served 5,000 families", "t0")]
        decs = gr.gate_document(rows, idmap, stale=True, measure_fn=None, mispair_detect=False)
        assert decs[0].reason == "stale_coords"

    def test_small_int_without_measure_fn_unchecked(self):
        idmap = {"t0": _txt("operated 9 vans", idx=0)}
        rows = [self._row(1, 9, "vans operated", "operated 9 vans", "t0")]
        decs = gr.gate_document(rows, idmap, stale=False, measure_fn=None, mispair_detect=False)
        assert decs[0].decision == "quarantine" and decs[0].reason == "measure_unchecked"

    def test_small_int_with_measure_fn_publishes(self):
        idmap = {"t0": _txt("operated 9 vans across the county", idx=0)}
        rows = [self._row(1, 9, "vans operated", "operated 9 vans across the county", "t0")]
        decs = gr.gate_document(rows, idmap, stale=False, measure_fn=lambda s, u: "MEASURE",
                                mispair_detect=False)
        assert decs[0].decision == "publish"

    def test_dedup_collapses_duplicate(self):
        idmap = {"t0": _txt("served 500 shelter families", idx=0),
                 "t1": _txt("served 500 shelter families", idx=1)}
        rows = [
            self._row(1, 500, "shelter families served", "served 500 shelter families", "t0"),
            self._row(2, 500, "shelter families served", "served 500 shelter families", "t1"),
        ]
        decs = gr.gate_document(rows, idmap, stale=False, measure_fn=None, mispair_detect=False)
        decisions = sorted((d.metric_id, d.decision, d.reason) for d in decs)
        # one publishes, the later one drops as duplicate
        assert decisions[0] == (1, "publish", "ok")
        assert decisions[1] == (2, "quarantine", "duplicate")


# ============================================================
# pure: aggregate_report
# ============================================================

class TestAggregateReport:
    def test_histogram_and_triage(self):
        results = [
            gr.DocResult("a", [
                gr.MetricDecision(1, "publish", "ok"),
                gr.MetricDecision(2, "quarantine", "mispair"),
                gr.MetricDecision(3, "quarantine", "unmarked"),
                gr.MetricDecision(4, "quarantine", "not_a_metric_rule"),
            ]),
        ]
        rep = gr.aggregate_report(results, gate_run_id=7, source_run_id=3)
        assert rep["published"] == 1 and rep["quarantined"] == 3
        assert rep["reason_histogram"] == {"mispair": 1, "not_a_metric_rule": 1, "unmarked": 1}
        assert rep["triage"]["recoverable_relabel"] == 1
        assert rep["triage"]["recoverable_vision"] == 1
        assert rep["triage"]["true_junk"] == 1


# ============================================================
# integration (real Postgres)
# ============================================================

def _have_pg() -> bool:
    try:
        return subprocess.run(["createdb", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


_MIG_DIR = os.path.join(os.path.dirname(__file__), "../../migrations/lava_vocab")


def _apply(conn, fname):
    with open(os.path.join(_MIG_DIR, fname)) as f:
        sql = f.read().replace("BEGIN;", "").replace("COMMIT;", "")
    conn.execute(text(sql))


@pytest.fixture(scope="module")
def pg_engine():
    if not _have_pg():
        pytest.skip("local postgres unavailable")
    dbname = f"scratch_0069_{uuid.uuid4().hex[:8]}"
    sock = "/var/run/postgresql"
    if subprocess.run(["createdb", "-h", sock, dbname]).returncode != 0:
        pytest.skip("cannot create scratch db")
    eng = create_engine(f"postgresql+psycopg2://@/{dbname}?host={sock}")
    base = """
        CREATE SCHEMA lava_vocab;
        CREATE SCHEMA lava_parse;
        CREATE TABLE lava_parse.documents(content_sha256 text, source_org_ein text, parse_version text);
        CREATE TABLE lava_parse.sections(content_sha256 text, section_index int, body_text text, source_locations jsonb);
        CREATE TABLE lava_parse.tables(content_sha256 text, table_index int, page_number int, cell_locations jsonb);
        CREATE TABLE lava_parse.pages(content_sha256 text, page_no int, width real, height real);
        CREATE TABLE lava_vocab.extraction_runs(
          id serial primary key, run_tag text unique,
          extractor_version text not null default 'x',
          started_at timestamptz not null default now(),
          finished_at timestamptz, stats_json jsonb);
        CREATE TABLE lava_vocab.llm_metrics(
          id bigserial primary key, run_id integer not null, content_sha256 text not null,
          source_org_ein text not null, metric_text text not null, metric_type text,
          metric_value real, unit text, geo_impact text, source_snippet text,
          created_at timestamptz not null default now());
    """
    with eng.begin() as c:
        for stmt in base.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        _apply(c, "0068_metric_markers.sql")
        _apply(c, "0069_gate_decision.sql")
    yield eng
    eng.dispose()
    subprocess.run(["dropdb", "-h", sock, dbname])


def _seed(eng, run_tag="0068-markers-test"):
    """One doc, one section with a grounding text element, two metrics (1 publish-able,
    1 ungrounded), under a 0068 source run."""
    sha = "docA"
    pv = "docling-2.93.0"
    sl = [{"text": "served 5,000 families in our shelter program",
           "locations": [{"page_no": 1, "bbox": {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"}}]},
          {"text": "a general note about our mission",
           "locations": [{"page_no": 1, "bbox": {"l": 1, "t": 50, "r": 3, "b": 52, "coord_origin": "TOPLEFT"}}]}]
    with eng.begin() as c:
        c.execute(text("INSERT INTO lava_parse.documents VALUES (:s,:e,:p)"),
                  {"s": sha, "e": "11-1", "p": pv})
        c.execute(text("INSERT INTO lava_parse.sections VALUES (:s,0,:b,CAST(:sl AS JSONB))"),
                  {"s": sha, "b": "body", "sl": json.dumps(sl)})
        c.execute(text("INSERT INTO lava_parse.pages VALUES (:s,1,600,800)"), {"s": sha})
        rid = c.execute(text("INSERT INTO lava_vocab.extraction_runs(run_tag) VALUES (:t) RETURNING id"),
                        {"t": run_tag}).scalar()
        def ins(label, value, source, vref):
            c.execute(text("""
                INSERT INTO lava_vocab.llm_metrics
                  (run_id, content_sha256, source_org_ein, metric_text, metric_type, metric_value,
                   source_snippet, value_ref, subject_ref, marker_resolved, parse_version, render_version)
                VALUES (:rid,:sha,'11-1',:mt,:lbl,:val,:src,:vref,:vref,true,:pv,:rv)
            """), {"rid": rid, "sha": sha, "mt": source, "lbl": label, "val": value,
                   "src": source, "vref": vref, "pv": pv, "rv": RENDER_VERSION})
        ins("shelter families served", 5000, "served 5,000 families in our shelter program", "t0")
        ins("phantom metric", 7777, "a general note about our mission", "t1")
    return rid, sha


class TestRunGateIntegration:
    def test_decides_every_metric_and_reports(self, pg_engine):
        rid, sha = _seed(pg_engine, "run-decide")
        rep = gr.run_gate(pg_engine, "run-decide", measure_fn=None, mispair_detect=False)
        assert rep["metrics_total"] == 2
        assert rep["published"] == 1 and rep["quarantined"] == 1
        with pg_engine.connect() as c:
            rows = c.execute(text(
                "SELECT metric_value, gate_decision, gate_reason, gate_run_id FROM lava_vocab.llm_metrics "
                "WHERE run_id=:r ORDER BY id"), {"r": rid}).fetchall()
        assert all(row[1] is not None for row in rows)         # AC2: every metric decided
        assert rows[0][1] == "publish" and rows[1][1] == "quarantine"
        assert rows[1][2] == "value_not_at_marker"

    def test_idempotent_in_place_update(self, pg_engine):
        # AC9: re-run -> same decisions, no duplicate/extra rows, 0068 payload untouched
        rid, sha = _seed(pg_engine, "run-idem")
        before = _snapshot(pg_engine, rid)
        gr.run_gate(pg_engine, "run-idem", measure_fn=None, mispair_detect=False)
        first = _decisions(pg_engine, rid)
        gr.run_gate(pg_engine, "run-idem", measure_fn=None, mispair_detect=False)
        second = _decisions(pg_engine, rid)
        after = _snapshot(pg_engine, rid)
        assert first == second                                 # determinism (AC11)
        assert before == after                                 # value_ref/marker payload untouched
        with pg_engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM lava_vocab.llm_metrics WHERE run_id=:r"),
                          {"r": rid}).scalar()
        assert n == 2                                          # no new rows

    def test_advisory_lock_blocks_concurrent(self, pg_engine):
        # S7: hold the lock in one connection; a second run_gate fails fast
        rid, sha = _seed(pg_engine, "run-lock")
        src = rid
        with pg_engine.begin() as held:
            got = held.execute(text("SELECT pg_try_advisory_xact_lock(:c,:o)"),
                               {"c": gr.GATE_RUNNER_BASE, "o": int(src)}).scalar()
            assert got
            with pytest.raises(gr.GateRunBusy):
                gr.run_gate(pg_engine, "run-lock", measure_fn=None, mispair_detect=False)

    def test_report_appended_to_source_stats(self, pg_engine):
        rid, sha = _seed(pg_engine, "run-stats")
        rep = gr.run_gate(pg_engine, "run-stats", measure_fn=None, mispair_detect=False)
        gid = rep["gate_run_id"]
        with pg_engine.connect() as c:
            stats = c.execute(text("SELECT stats_json FROM lava_vocab.extraction_runs WHERE id=:r"),
                              {"r": rid}).scalar()
        assert str(gid) in stats["gate_runs"]
        assert stats["gate_runs"][str(gid)]["published"] == 1


def _snapshot(eng, rid):
    with eng.connect() as c:
        return c.execute(text(
            "SELECT id, value_ref, subject_ref, marker_resolved, metric_value FROM lava_vocab.llm_metrics "
            "WHERE run_id=:r ORDER BY id"), {"r": rid}).fetchall()


def _decisions(eng, rid):
    with eng.connect() as c:
        return c.execute(text(
            "SELECT id, gate_decision, gate_reason FROM lava_vocab.llm_metrics "
            "WHERE run_id=:r ORDER BY id"), {"r": rid}).fetchall()
