"""Prod INPUT adapter — selects WHICH docs the prod run processes: the corpus.

I/O only (like dev_input, just a different source): returns parsed docs that are eligible
for metric extraction. It does NOT extract, normalize, or gate. The core then processes
these exactly as it does the dev sample pool — same code, different input.

`already_done` lets the harness skip docs already processed under the current run (resumable).
"""
from __future__ import annotations

from sqlalchemy import text

# material types that carry an impact narrative (mirrors the corpus inclusion criteria).
_REPORT_TYPES = ("annual_report", "impact_report", "year_in_review",
                 "community_benefit_report", "donor_impact_report")


def load_corpus(conn, limit: int | None = None, shas: list[str] | None = None) -> list[tuple[str, str | None]]:
    """-> [(content_sha256, org_name), ...] of parsed report docs eligible for extraction.

    `shas` restricts to specific docs (a targeted re-run); otherwise all eligible parsed docs.
    """
    if shas:
        rows = conn.execute(text("""
            SELECT d.content_sha256, ns.name
            FROM lava_parse.documents d
            LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein = d.source_org_ein
            WHERE d.content_sha256 = ANY(:shas)"""), {"shas": shas}).fetchall()
        return [(r[0], str(r[1]) if r[1] else None) for r in rows]

    q = """
        SELECT d.content_sha256, ns.name
        FROM lava_parse.documents d
        JOIN lava_corpus.corpus co ON co.content_sha256 = d.content_sha256
        LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein = d.source_org_ein
        WHERE co.material_type = ANY(:types)
        ORDER BY d.content_sha256
    """
    params = {"types": list(_REPORT_TYPES)}
    if limit:
        q += " LIMIT :lim"
        params["lim"] = limit
    rows = conn.execute(text(q), params).fetchall()
    return [(r[0], str(r[1]) if r[1] else None) for r in rows]
