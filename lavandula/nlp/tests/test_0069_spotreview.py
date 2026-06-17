"""Phase-6 tests: human spot-review + SLA gate (Spec 0069).

AC5 — protocol produces a measured precision; shippable iff >= PUBLISH_PRECISION_SLA.
S5  — sample frozen+hashed; reviewer id+timestamp; outputs append-only.
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from lavandula.nlp import spot_review as sr


# ============================================================
# sample selection / freeze (S5)
# ============================================================

def _pub(i, org, table=False, conf=0.5):
    return {"id": i, "source_org_ein": org,
            "value_row": 1 if table else None, "value_col": 1 if table else None,
            "gate_confidence": conf}


class TestSelectSample:
    def test_deterministic(self):
        rows = [_pub(i, f"org{i%3}", table=(i % 2 == 0), conf=(i % 5) / 5) for i in range(40)]
        a = sr.select_sample(rows, size=12)
        b = sr.select_sample(rows, size=12)
        assert a == b
        assert a["size"] == 12

    def test_stratified_across_prose_and_table(self):
        rows = [_pub(i, "org1", table=(i % 2 == 0)) for i in range(20)]
        s = sr.select_sample(rows, size=8)
        kinds = {k.split("|")[0] for k in s["strata"]}
        assert "table" in kinds and "prose" in kinds

    def test_content_hash_binds_ids(self):
        rows = [_pub(i, "org1") for i in range(10)]
        s = sr.select_sample(rows, size=5)
        assert sr.verify_manifest(s) is True
        # tamper: swap an id -> hash no longer verifies
        s["sampled_ids"][0] = 9999
        assert sr.verify_manifest(s) is False

    def test_empty(self):
        s = sr.select_sample([], size=10)
        assert s["size"] == 0 and s["sampled_ids"] == []

    def test_lowest_confidence_prioritized(self):
        # within one stratum, the least-confident rows are sampled first
        rows = [_pub(1, "o", conf=0.9), _pub(2, "o", conf=0.5), _pub(3, "o", conf=0.99)]
        s = sr.select_sample(rows, size=1)
        assert s["sampled_ids"] == [2]


class TestManifest:
    def test_write_and_reload(self, tmp_path):
        rows = [_pub(i, "org1") for i in range(6)]
        m = sr.select_sample(rows, size=3)
        path = sr.write_manifest(m, gate_run_id=7, created_at="2026-06-17T00:00:00Z",
                                 base_dir=str(tmp_path))
        assert os.path.exists(path)
        import json
        loaded = json.load(open(path))
        assert loaded["gate_run_id"] == 7
        assert sr.verify_manifest(loaded)


# ============================================================
# reviewer identity (Gemini MEDIUM)
# ============================================================

class TestReviewerIdentity:
    def test_from_session(self):
        assert sr.reviewer_identity({"USER": "ronp"}) == "ronp"
        assert sr.reviewer_identity({"LOCARD_REVIEWER": "ann", "USER": "ronp"}) == "ann"

    def test_missing_raises(self):
        with pytest.raises(RuntimeError):
            sr.reviewer_identity({})


# ============================================================
# precision + SLA (AC5)
# ============================================================

class TestPrecision:
    def _r(self, mid, org, rn, rl, ord_=0):
        return {"metric_id": mid, "source_org_ein": org, "right_number": rn,
                "right_label": rl, "reviewed_at_ord": ord_}

    def test_all_correct_shippable(self):
        reviews = [self._r(i, "o1", True, True) for i in range(100)]
        res = sr.compute_precision(reviews)
        assert res.per_metric == 1.0 and res.shippable is True

    def test_below_sla_not_shippable(self):
        reviews = [self._r(i, f"o{i}", True, True) for i in range(95)]
        reviews += [self._r(100 + i, f"x{i}", False, True) for i in range(5)]  # 95% < 98%
        res = sr.compute_precision(reviews)
        assert res.per_metric == 0.95 and res.shippable is False

    def test_per_org_cap_blocks(self):
        # 98%+ overall but one org has 2 wrong -> per-org cap fails
        reviews = [self._r(i, "o1", True, True) for i in range(98)]
        reviews += [self._r(200, "bad", False, True), self._r(201, "bad", True, False)]
        res = sr.compute_precision(reviews)
        assert res.per_metric >= 0.98
        assert res.per_org_wrong["bad"] == 2
        assert res.shippable is False

    def test_latest_grade_wins(self):
        # append-only: a later re-grade supersedes an earlier one for the same metric
        reviews = [self._r(1, "o1", False, False, ord_=1), self._r(1, "o1", True, True, ord_=2)]
        res = sr.compute_precision(reviews)
        assert res.n == 1 and res.correct == 1


# ============================================================
# append-only record_review (S5) — integration
# ============================================================

def _have_pg() -> bool:
    try:
        return subprocess.run(["createdb", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


_MIG_DIR = os.path.join(os.path.dirname(__file__), "../../migrations/lava_vocab")


def _apply(conn, fname):
    with open(os.path.join(_MIG_DIR, fname)) as f:
        conn.execute(text(f.read().replace("BEGIN;", "").replace("COMMIT;", "")))


@pytest.fixture(scope="module")
def pg():
    if not _have_pg():
        pytest.skip("local postgres unavailable")
    dbname = f"scratch_0069sr_{uuid.uuid4().hex[:8]}"
    sock = "/var/run/postgresql"
    if subprocess.run(["createdb", "-h", sock, dbname]).returncode != 0:
        pytest.skip("cannot create scratch db")
    eng = create_engine(f"postgresql+psycopg2://@/{dbname}?host={sock}")
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        try:
            c.execute(text("CREATE ROLE research_app"))
        except Exception:
            pass
    base = """
        CREATE SCHEMA lava_vocab;
        CREATE TABLE lava_vocab.extraction_runs(id serial primary key, run_tag text unique);
        CREATE TABLE lava_vocab.llm_metrics(id bigserial primary key, run_id int not null,
          content_sha256 text not null, source_org_ein text not null, metric_text text not null,
          metric_type text, metric_value real);
        INSERT INTO lava_vocab.extraction_runs(run_tag) VALUES ('src');
    """
    with eng.begin() as c:
        for stmt in base.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        _apply(c, "0068_metric_markers.sql")
        _apply(c, "0069_gate_decision.sql")
        _apply(c, "0069_gate_review.sql")
        gid = c.execute(text("INSERT INTO lava_vocab.gate_runs(source_run_id, gate_version) "
                             "VALUES (1,'v') RETURNING id")).scalar()
        c.execute(text("INSERT INTO lava_vocab.llm_metrics(id, run_id, content_sha256, "
                       "source_org_ein, metric_text) VALUES (1,1,'d','11-1','x')"))
    eng._gid = gid
    yield eng
    eng.dispose()
    subprocess.run(["dropdb", "-h", sock, dbname])


class TestRecordReview:
    def test_append_only_new_row_per_grade(self, pg):
        sr.record_review(pg, pg._gid, 1, right_number=False, right_label=True,
                         sample_hash="h", reviewer="ronp")
        sr.record_review(pg, pg._gid, 1, right_number=True, right_label=True,
                         sample_hash="h", reviewer="ronp")
        with pg.connect() as c:
            n = c.execute(text("SELECT count(*) FROM lava_vocab.gate_review WHERE metric_id=1")).scalar()
        assert n == 2   # both grades preserved, append-only

    def test_update_blocked(self, pg):
        with pg.begin() as c, pytest.raises(Exception):
            c.execute(text("UPDATE lava_vocab.gate_review SET right_number=true WHERE metric_id=1"))
