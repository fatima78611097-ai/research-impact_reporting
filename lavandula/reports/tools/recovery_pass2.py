"""Pass 2 recovery: re-scan HTML pages for orgs with missing PDF candidates.

Identifies orgs that have HTML pages fetched but zero (or only
cross_origin_blocked) PDFs in the corpus, then re-extracts candidates
from cached HTML using the fixed candidate_filter (which now accepts
cross-origin PDFs). Discovered candidates are queued in fetch_log for
the normal pipeline.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Engine

from lavandula.common.db import make_app_engine

from .. import config
from ..candidate_filter import extract_candidates, Candidate
from ..redirect_policy import etld1
from ..url_redact import redact_url


log = logging.getLogger("lavandula.reports.recovery_pass2")

_STATE_RE = re.compile(r"^[A-Z]{2}$")


@dataclass
class OrgRecord:
    ein: str
    website: str
    seed_etld1: str


@dataclass
class HtmlPage:
    url: str
    body: str


def _validate_state_filter(raw: str) -> list[str]:
    states = [s.strip().upper() for s in raw.split(",") if s.strip()]
    for s in states:
        if not _STATE_RE.match(s):
            raise ValueError(f"Invalid state code: {s!r}")
    return states


def _identify_target_orgs(
    engine: Engine,
    *,
    state_filter: list[str] | None = None,
    max_orgs: int | None = None,
) -> list[OrgRecord]:
    """Find orgs with HTML pages but no successful PDF fetches."""
    clauses = []
    params: dict = {}

    if state_filter:
        placeholders = ", ".join(f":st{i}" for i in range(len(state_filter)))
        clauses.append(f"ns.state IN ({placeholders})")
        for i, st in enumerate(state_filter):
            params[f"st{i}"] = st

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit_clause = "LIMIT :max_orgs" if max_orgs else ""
    if max_orgs:
        params["max_orgs"] = max_orgs

    sql = f"""
        SELECT DISTINCT ns.ein, ns.website
        FROM lava_corpus.nonprofits_seed ns
        JOIN lava_corpus.fetch_log fl ON fl.ein = ns.ein
        WHERE fl.fetch_status = 'ok'
          AND fl.kind IN ('homepage', 'subpage')
          AND ns.ein NOT IN (
              SELECT DISTINCT c.ein FROM lava_corpus.corpus c
          )
          {" AND " + " AND ".join(clauses) if clauses else ""}
        ORDER BY ns.ein
        {limit_clause}
    """

    records = []
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        for row in rows:
            website = row[1] or ""
            host = urlsplit(website).hostname or ""
            records.append(OrgRecord(
                ein=row[0],
                website=website,
                seed_etld1=etld1(host),
            ))
    return records


def _load_cached_html(engine: Engine, ein: str) -> list[HtmlPage]:
    """Load HTML pages from fetch_log body cache (if available) or S3."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT url_redacted, body
                FROM lava_corpus.fetch_log
                WHERE ein = :ein
                  AND fetch_status = 'ok'
                  AND kind IN ('homepage', 'subpage')
                  AND body IS NOT NULL
                ORDER BY id
            """),
            {"ein": ein},
        ).fetchall()

    pages = []
    for row in rows:
        url = row[0] or ""
        body = row[1]
        if body and isinstance(body, (bytes, memoryview)):
            body = bytes(body).decode("utf-8", errors="replace")
        elif body and isinstance(body, str):
            pass
        else:
            continue
        if body:
            pages.append(HtmlPage(url=url, body=body))
    return pages


def _recrawl_org_pages(
    org: OrgRecord, client
) -> list[HtmlPage]:
    """Re-fetch org homepage + subpages to get fresh HTML."""
    from ..http_client import ReportsHTTPClient

    pages = []
    r = client.get(org.website, kind="homepage", seed_etld1=org.seed_etld1)
    if r.status == "ok" and r.body:
        html = r.body.decode("utf-8", errors="replace")
        pages.append(HtmlPage(url=r.final_url or org.website, body=html))
    return pages


def _extract_new_candidates(
    pages: list[HtmlPage], org: OrgRecord
) -> list[Candidate]:
    """Run fixed candidate_filter on HTML pages to find cross-origin PDFs."""
    all_candidates: list[Candidate] = []
    seen_urls: set[str] = set()

    for page in pages:
        candidates = extract_candidates(
            html=page.body,
            base_url=page.url,
            seed_etld1=org.seed_etld1,
            referring_page_url=page.url,
            ein=org.ein,
        )
        for c in candidates:
            if c.cross_origin_candidate and c.url not in seen_urls:
                seen_urls.add(c.url)
                all_candidates.append(c)

    return all_candidates


def _queue_candidates(engine: Engine, candidates: list[Candidate], ein: str) -> int:
    """Insert discovered candidates into fetch_log for the pipeline to process."""
    queued = 0
    with engine.begin() as conn:
        for cand in candidates:
            conn.execute(
                text("""
                    INSERT INTO lava_corpus.fetch_log (
                        ein, url_redacted, kind, fetch_status, notes
                    ) VALUES (
                        :ein, :url, 'pdf-get', 'queued', :notes
                    )
                    ON CONFLICT DO NOTHING
                """),
                {
                    "ein": ein,
                    "url": redact_url(cand.url),
                    "notes": f"recovery_pass2:cross_origin_candidate",
                },
            )
            queued += 1
    return queued


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pass 2: re-scan HTML pages for cross-origin PDF candidates"
    )
    parser.add_argument("--max-orgs", type=int, default=None)
    parser.add_argument("--no-limit", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state-filter", type=str, default=None)
    parser.add_argument("--source", choices=["cached", "recrawl"], default="cached")

    args = parser.parse_args(argv)

    if args.max_orgs is None and not args.no_limit:
        parser.error("--max-orgs is required (use --no-limit to bypass)")
        return 1

    state_filter = None
    if args.state_filter:
        state_filter = _validate_state_filter(args.state_filter)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    engine = make_app_engine()
    max_orgs = args.max_orgs if not args.no_limit else None

    orgs = _identify_target_orgs(engine, state_filter=state_filter, max_orgs=max_orgs)
    log.info("Found %d target orgs with missing PDFs", len(orgs))

    if not orgs:
        return 0

    client = None
    if args.source == "recrawl":
        from ..http_client import ReportsHTTPClient
        client = ReportsHTTPClient()

    total_candidates = 0
    orgs_with_candidates = 0

    for i, org in enumerate(orgs):
        if args.source == "cached":
            pages = _load_cached_html(engine, org.ein)
        else:
            pages = _recrawl_org_pages(org, client)

        if not pages:
            continue

        candidates = _extract_new_candidates(pages, org)

        if candidates:
            orgs_with_candidates += 1
            total_candidates += len(candidates)

            if args.dry_run:
                log.info(
                    "org %s: %d new cross-origin PDF candidates",
                    org.ein, len(candidates),
                )
                for c in candidates[:5]:
                    log.info("  -> %s", c.url[:100])
            else:
                queued = _queue_candidates(engine, candidates, org.ein)
                log.info("org %s: queued %d candidates", org.ein, queued)

        if (i + 1) % 100 == 0:
            log.info(
                "Progress: %d/%d orgs scanned, %d with candidates, %d total candidates",
                i + 1, len(orgs), orgs_with_candidates, total_candidates,
            )

    log.info(
        "Complete: %d orgs scanned, %d with candidates, %d total candidates discovered",
        len(orgs), orgs_with_candidates, total_candidates,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
