"""Dev INPUT adapter — selects WHICH docs the dev run processes: the sample pool.

I/O only: reads the fixture, resolves each sha8 to its full content_sha256 + org name.
It does NOT extract, normalize, or gate — it just hands the harness the doc list. The
prod input adapter differs ONLY here (a corpus query instead of the fixture).
"""
from __future__ import annotations

import json
import os

from sqlalchemy import text

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "sample_pool.json")


def _pool_sha8s() -> list[str]:
    pool = json.load(open(os.path.abspath(FIXTURE)))
    sha8s = list(pool.get("base_25_ntee_p", []))
    sha8s += list(pool.get("coverage_financial_3", []))
    sha8s += list(pool.get("coverage_infographic", {}).get("docs", []))
    return sha8s


def load_sample_pool(conn) -> list[tuple[str, str | None]]:
    """-> [(content_sha256, org_name), ...] for every doc in the fixture pool."""
    out = []
    for sha8 in _pool_sha8s():
        full = conn.execute(
            text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
            {"p": sha8 + "%"}).scalar()
        if not full:
            continue
        org = conn.execute(text(
            "SELECT ns.name FROM lava_parse.documents d "
            "LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein = d.source_org_ein "
            "WHERE d.content_sha256 = :s"), {"s": full}).scalar()
        out.append((full, str(org) if org else None))
    return out
