"""Phase-5 tests: the published_metrics view + boundary (Spec 0069).

AC4  — view returns only publish rows, slot-shaped (no metric_text).
AC10 — re-gate updates the view to the new current decision; never a stale/quarantine row.
S3   — gate_run_id is server-assigned monotonic immutable; a backdated run can't become active.
S6   — product role REVOKEd from raw llm_metrics; reads ONLY published_metrics.

Integration only (real Postgres; skipped if unavailable).
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

_MIG_DIR = os.path.join(os.path.dirname(__file__), "../../migrations/lava_vocab")


def _have_pg() -> bool:
    try:
        return subprocess.run(["createdb", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _apply(conn, fname):
    with open(os.path.join(_MIG_DIR, fname)) as f:
        sql = f.read().replace("BEGIN;", "").replace("COMMIT;", "")
    conn.execute(text(sql))


@pytest.fixture(scope="module")
def pg():
    if not _have_pg():
        pytest.skip("local postgres unavailable")
    dbname = f"scratch_0069v_{uuid.uuid4().hex[:8]}"
    sock = "/var/run/postgresql"
    if subprocess.run(["createdb", "-h", sock, dbname]).returncode != 0:
        pytest.skip("cannot create scratch db")
    eng = create_engine(f"postgresql+psycopg2://@/{dbname}?host={sock}")
    # roles are cluster-global (shared across scratch DBs) — create idempotently
    for role in ("research_app", "dashboard_user1"):
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            try:
                c.execute(text(f"CREATE ROLE {role}"))
            except Exception:
                pass
    base = """
        CREATE SCHEMA lava_vocab;
        CREATE TABLE lava_vocab.extraction_runs(id serial primary key, run_tag text unique);
        CREATE TABLE lava_vocab.llm_metrics(
          id bigserial primary key, run_id integer not null, content_sha256 text not null,
          source_org_ein text not null, metric_text text not null, metric_type text,
          metric_value real, unit text, geo_impact text, source_snippet text);
        INSERT INTO lava_vocab.extraction_runs(run_tag) VALUES ('src');
        GRANT SELECT ON lava_vocab.llm_metrics TO dashboard_user1;
    """
    with eng.begin() as c:
        for stmt in base.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        _apply(c, "0068_metric_markers.sql")
        _apply(c, "0069_gate_decision.sql")
        _apply(c, "0069_published_view.sql")
    yield eng
    eng.dispose()
    subprocess.run(["dropdb", "-h", sock, dbname])


def _new_gate_run(c, src=1):
    return c.execute(text(
        "INSERT INTO lava_vocab.gate_runs(source_run_id, gate_version) VALUES (:s,'v1') RETURNING id"),
        {"s": src}).scalar()


def _insert_metric(c, sha, label, value, decision, gid, vref="t0", page=1):
    return c.execute(text("""
        INSERT INTO lava_vocab.llm_metrics
          (run_id, content_sha256, source_org_ein, metric_text, metric_type, metric_value,
           unit, geo_impact, value_ref, value_page, marker_resolved, parse_version, render_version,
           gate_decision, gate_reason, gate_run_id)
        VALUES (1,:sha,'11-1',:mt,:lbl,:val,'people','LOCAL',:vref,:pg,true,'pv','rv',:dec,'ok',:gid)
        RETURNING id
    """), {"sha": sha, "mt": "composed sentence text", "lbl": label, "val": value,
           "vref": vref, "pg": page, "dec": decision, "gid": gid})


class TestPublishedView:
    def test_only_publish_slot_shaped(self, pg):
        with pg.begin() as c:
            gid = _new_gate_run(c)
            _insert_metric(c, "d1", "families served", 5000, "publish", gid)
            _insert_metric(c, "d1", "junk metric", 7777, "quarantine", gid)
        with pg.connect() as c:
            rows = c.execute(text(
                "SELECT * FROM lava_vocab.published_metrics WHERE content_sha256='d1'")).fetchall()
            cols = set(c.execute(text(
                "SELECT * FROM lava_vocab.published_metrics WHERE false")).keys())
        # AC4: only the publish row
        assert len(rows) == 1
        # slot-shaped: no metric_text / source_snippet leak
        assert "metric_text" not in cols and "source_snippet" not in cols
        assert {"metric_value", "label", "value_ref", "value_page", "gate_run_id"} <= cols

    def test_regate_updates_view(self, pg):
        # AC10: a publish row re-gated to quarantine drops out of the view (in-place update)
        with pg.begin() as c:
            gid = _new_gate_run(c)
            mid = _insert_metric(c, "d2", "regate me", 1234, "publish", gid).scalar()
        with pg.connect() as c:
            assert c.execute(text("SELECT count(*) FROM lava_vocab.published_metrics WHERE content_sha256='d2'")).scalar() == 1
        with pg.begin() as c:
            gid2 = _new_gate_run(c)
            c.execute(text("UPDATE lava_vocab.llm_metrics SET gate_decision='quarantine', "
                           "gate_reason='mispair', gate_run_id=:g WHERE id=:m"), {"g": gid2, "m": mid})
        with pg.connect() as c:
            assert c.execute(text("SELECT count(*) FROM lava_vocab.published_metrics WHERE content_sha256='d2'")).scalar() == 0

    def test_gate_run_id_monotonic_server_assigned(self, pg):
        # S3: ids are server-assigned and strictly increasing; a client cannot pick one
        with pg.begin() as c:
            a = _new_gate_run(c)
            b = _new_gate_run(c)
        assert b > a
        with pg.connect() as c, pytest.raises(Exception):
            c.execute(text("INSERT INTO lava_vocab.gate_runs(id, source_run_id, gate_version) "
                           "VALUES (1, 1, 'v')"))


class TestBoundary:
    def test_product_role_revoked_from_raw_can_read_view(self, pg):
        # S6: dashboard_user1 has NO privilege on raw llm_metrics, SELECT on the view
        with pg.connect() as c:
            raw = c.execute(text(
                "SELECT has_table_privilege('dashboard_user1','lava_vocab.llm_metrics','SELECT')")).scalar()
            view = c.execute(text(
                "SELECT has_table_privilege('dashboard_user1','lava_vocab.published_metrics','SELECT')")).scalar()
        assert raw is False
        assert view is True
