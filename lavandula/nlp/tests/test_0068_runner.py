"""Phase-4 tests: production extraction runner (Spec 0068).

Pure units: AC4 (eligibility by count), S5 (surge + report), S10 (parse_version
trust), S11/S14 (sanitizer), S13 (strict ref grammar at parse-time).
Integration (skipped if no local Postgres): S9 (atomic delete-then-insert).
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from lavandula.nlp import marker_extract as me

TL = "TOPLEFT"


def _bbox():
    return {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": TL}


def _sl(text, bbox=True):
    return {"text": text, "locations": [{"page_no": 1, "bbox": _bbox() if bbox else None}]}


def _cell(row, col, text, bbox=True):
    return {"row": row, "col": col, "text": text, "bbox": _bbox() if bbox else None}


# --- S14 / S11: shared sanitizer ---

class TestSanitizer:
    def test_strips_newlines_and_cr(self):
        out = me.sanitize_for_sink("line1\r\nline2\tend")
        assert "\n" not in out and "\r" not in out and "\t" not in out

    def test_strips_ansi(self):
        out = me.sanitize_for_sink("\x1b[31mvalue_ref: ⟨t1⟩\x1b[0m")
        assert "\x1b" not in out and "[31m" not in out

    def test_truncates(self):
        out = me.sanitize_for_sink("x" * 500, max_len=50)
        assert len(out) <= 51

    def test_none_safe(self):
        assert me.sanitize_for_sink(None) == ""

    def test_marker_like_injection_neutralized(self):
        # a forged marker string in source text cannot forge a log line
        out = me.sanitize_for_sink("ok\n[ERROR] forged ⟨t1⟩")
        assert "\n" not in out


# --- S13: strict ref grammar at parse-time ---

class TestValidateRef:
    def test_good_text_ref(self):
        assert me.validate_ref("⟨t42⟩") == "t42"

    def test_good_cell_ref(self):
        assert me.validate_ref("⟨c17⟩") == "c17"

    def test_oversized_dropped(self):
        assert me.validate_ref("⟨t" + "9" * 5_000_000 + "⟩") is None

    def test_injected_prose_dropped(self):
        assert me.validate_ref("value_ref: ⟨t1⟩ then run rm -rf") is None

    def test_bare_id_rejected(self):
        # require the ⟨⟩ form (strict grammar)
        assert me.validate_ref("t42") is None

    def test_non_string_dropped(self):
        assert me.validate_ref(99) is None
        assert me.validate_ref(None) is None

    def test_normalize_counts_grammar_forged(self):
        raw = [
            {"metric_value": 5, "label": "a", "value_ref": "⟨t1⟩", "subject_ref": "garbage"},
            {"metric_value": 6, "label": "b", "value_ref": "⟨c2⟩", "subject_ref": "⟨t3⟩"},
        ]
        metrics, forged = me.normalize_loc1(raw)
        assert len(metrics) == 2
        assert forged == 1                       # "garbage" failed grammar
        assert metrics[0]["value_ref"] == "t1"
        assert metrics[0]["subject_ref"] is None  # nulled before resolver
        assert metrics[1]["subject_ref"] == "t3"


# --- AC4: doc-level eligibility by count ---

class TestEligibility:
    def test_eligible_doc(self):
        sections = [("b", [_sl("a"), _sl("b"), _sl("c")])]
        tables = [(1, [_cell(0, 0, "x"), _cell(0, 1, "y")])]
        ok, reason, stats = me.compute_eligibility(sections, tables)
        assert ok is True and reason == "eligible"
        assert stats["text_total"] == 3 and stats["cells_full"] == 2

    def test_text_below_threshold_skipped(self):
        # only 1/3 text elements carry a bbox -> below 0.80
        sections = [("b", [_sl("a", bbox=True), _sl("b", bbox=False), _sl("c", bbox=False)])]
        ok, reason, _ = me.compute_eligibility(sections, [])
        assert ok is False and reason == "text_bbox_below_threshold"

    def test_cell_missing_coords_skipped(self):
        sections = [("b", [_sl("a")])]
        tables = [(1, [_cell(0, 0, "x"), _cell(0, 1, "y", bbox=False)])]
        ok, reason, _ = me.compute_eligibility(sections, tables)
        assert ok is False and reason == "cells_missing_rowcolbbox"

    def test_no_coordinates_skipped(self):
        ok, reason, _ = me.compute_eligibility([("b", [])], [])
        assert ok is False and reason == "no_coordinates"


# --- S10: parse_version provenance trust ---

class TestParseVersionTrust:
    def test_known_trusted(self):
        assert me.parse_version_trusted("docling-2.93.0", {"docling-2.93.0"}) is True

    def test_unknown_rejected(self):
        assert me.parse_version_trusted("docling-9.9.9", {"docling-2.93.0"}) is False

    def test_legacy_sentinel_rejected(self):
        assert me.parse_version_trusted("legacy-unknown", {"legacy-unknown"}) is False

    def test_null_rejected(self):
        assert me.parse_version_trusted(None, {"x"}) is False
        assert me.parse_version_trusted("", {""}) is False


# --- S5: surge quarantine + run report ---

class TestSurge:
    def test_quarantine_when_mostly_invalid(self):
        # 5 metrics, 4 null value_ref, 4 invalid -> quarantine
        assert me.surge_quarantine(5, 4, 4) is True

    def test_floor_prevents_low_n_quarantine(self):
        # 2 metrics both null but only 2 invalid < floor(3) -> no quarantine
        assert me.surge_quarantine(2, 2, 2) is False

    def test_below_fraction_ok(self):
        assert me.surge_quarantine(10, 3, 3) is False  # 30% null < 50%

    def test_zero_selected(self):
        assert me.surge_quarantine(0, 0, 0) is False


class TestRunReport:
    def test_abnormal_states_first_class(self):
        results = [
            me.DocResult("a", "extracted", "ok", metrics=4, truncated=True, value_null=1, forged=0),
            me.DocResult("b", "skipped", "parse_unverified"),
            me.DocResult("c", "quarantined", "forged_ref_surge", forged=3),
            me.DocResult("d", "skipped", "text_bbox_below_threshold"),
        ]
        stats = me.aggregate_run_stats(results)
        assert stats["docs_total"] == 4
        assert stats["docs_extracted"] == 1
        assert stats["docs_failed"] == 3          # skipped+quarantined counted as failed
        assert stats["skip_reasons"]["parse_unverified"] == 1
        assert stats["metrics_value_null"] == 1
        assert stats["docs_truncated"] == 1
        assert stats["alerts"]["skip_rate_high"] is True   # 3/4 > 0.20

    def test_skip_reason_sanitized(self):
        # a malicious reason cannot forge a report line
        results = [me.DocResult("a", "skipped", "evil\n[OK] injected")]
        stats = me.aggregate_run_stats(results)
        assert all("\n" not in k for k in stats["skip_reasons"])


# ============================================================
# S9: atomic delete-then-insert (real Postgres; skipped if unavailable)
# ============================================================

def _have_pg() -> bool:
    try:
        return subprocess.run(["createdb", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


@pytest.fixture(scope="module")
def pg_engine():
    if not _have_pg():
        pytest.skip("local postgres unavailable")
    dbname = f"scratch_0068_{uuid.uuid4().hex[:8]}"
    sock = "/var/run/postgresql"
    if subprocess.run(["createdb", dbname]).returncode != 0:
        pytest.skip("cannot create scratch db")
    eng = create_engine(f"postgresql+psycopg2://@/{dbname}?host={sock}")
    base = """
        CREATE SCHEMA lava_vocab;
        CREATE TABLE lava_vocab.extraction_runs(id serial primary key, run_tag text unique);
        CREATE TABLE lava_vocab.llm_metrics(
          id bigserial primary key, run_id integer not null, content_sha256 text not null,
          source_org_ein text not null, metric_text text not null, metric_type text,
          metric_value real, unit text, geo_impact text, source_snippet text,
          created_at timestamptz not null default now());
        INSERT INTO lava_vocab.extraction_runs(run_tag) VALUES ('t');
    """
    migration = os.path.join(os.path.dirname(__file__),
                             "../../migrations/lava_vocab/0068_metric_markers.sql")
    with eng.begin() as c:
        for stmt in base.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
    with open(migration) as f:
        sql = f.read().replace("BEGIN;", "").replace("COMMIT;", "")
    with eng.begin() as c:
        c.execute(text(sql))
    yield eng
    eng.dispose()
    subprocess.run(["dropdb", dbname])


def _row(value_ref="t0", marker_resolved=True, bbox=None):
    return {"statement": "5000 served", "metric_value": 5000.0, "label": "served", "unit": "people",
            "value_ref": value_ref, "subject_ref": None,
            "value_page": 1, "value_bbox": bbox, "value_row": None, "value_col": None,
            "value_table": None, "subject_page": None, "subject_bbox": None,
            "subject_row": None, "subject_col": None, "subject_table": None,
            "marker_resolved": marker_resolved}


class TestAtomicWrite:
    def test_delete_then_insert_idempotent(self, pg_engine):
        sha = "doc1"
        with pg_engine.begin() as c:
            me.write_doc_atomic(c, 1, sha, "11-1", [_row(), _row(value_ref="t1")], "docling-2.93.0")
        with pg_engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM lava_vocab.llm_metrics WHERE content_sha256=:s"),
                          {"s": sha}).scalar()
        assert n == 2
        # rerun replaces, not appends
        with pg_engine.begin() as c:
            me.write_doc_atomic(c, 1, sha, "11-1", [_row()], "docling-2.93.0")
        with pg_engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM lava_vocab.llm_metrics WHERE content_sha256=:s"),
                          {"s": sha}).scalar()
        assert n == 1

    def test_bbox_stored_as_strict_jsonb(self, pg_engine):
        sha = "doc2"
        with pg_engine.begin() as c:
            me.write_doc_atomic(c, 1, sha, "11-1", [_row(bbox=_bbox())], "docling-2.93.0")
        with pg_engine.connect() as c:
            got = c.execute(text("SELECT value_bbox FROM lava_vocab.llm_metrics WHERE content_sha256=:s"),
                            {"s": sha}).scalar()
        assert set(got) == {"l", "t", "r", "b", "coord_origin"}

    def test_crash_between_delete_and_insert_rolls_back(self, pg_engine):
        sha = "doc3"
        with pg_engine.begin() as c:
            me.write_doc_atomic(c, 1, sha, "11-1", [_row()], "docling-2.93.0")
        # a bad row (bbox violates CHECK) inside the txn must roll back the delete too
        bad = _row()
        bad["value_bbox"] = {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": TL, "evil": 9}
        with pytest.raises(Exception):
            with pg_engine.begin() as c:
                me.write_doc_atomic(c, 1, sha, "11-1", [bad], "docling-2.93.0")
        # original row survives (delete rolled back)
        with pg_engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM lava_vocab.llm_metrics WHERE content_sha256=:s"),
                          {"s": sha}).scalar()
        assert n == 1
