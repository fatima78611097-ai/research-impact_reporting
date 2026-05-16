"""Pass 1 recovery: re-fetch cross_origin_blocked PDF URLs from fetch_log.

Queries fetch_log for rows where status='cross_origin_blocked' and the URL
ends in .pdf, then re-fetches them with the relaxed redirect policy
(is_pdf_candidate=True). Successfully fetched PDFs are archived to S3 and
recorded in corpus.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from lavandula.common.db import make_app_engine

from .. import config
from .. import fetch_pdf
from .. import s3_archive as _s3a
from ..http_client import ReportsHTTPClient
from ..redirect_policy import etld1
from ..url_redact import redact_url


log = logging.getLogger("lavandula.reports.recovery_pass1")

_STATE_RE = re.compile(r"^[A-Z]{2}$")


@dataclass
class UrlRecord:
    id: int
    url: str
    ein: str
    seed_etld1: str


def _validate_state_filter(raw: str) -> list[str]:
    states = [s.strip().upper() for s in raw.split(",") if s.strip()]
    for s in states:
        if not _STATE_RE.match(s):
            raise ValueError(f"Invalid state code: {s!r}")
    return states


def _query_blocked_urls(
    engine: Engine,
    *,
    state_filter: list[str] | None = None,
    resume_from: int = 0,
    max_urls: int | None = None,
) -> list[UrlRecord]:
    clauses = [
        "fl.fetch_status = 'cross_origin_blocked'",
        "fl.url_redacted LIKE '%.pdf'",
        "fl.id > :resume_from",
    ]
    params: dict = {"resume_from": resume_from}

    if state_filter:
        placeholders = ", ".join(f":st{i}" for i in range(len(state_filter)))
        clauses.append(f"ns.state IN ({placeholders})")
        for i, st in enumerate(state_filter):
            params[f"st{i}"] = st

    where = " AND ".join(clauses)
    limit_clause = f"LIMIT :max_urls" if max_urls else ""
    if max_urls:
        params["max_urls"] = max_urls

    sql = f"""
        SELECT fl.id, fl.url_redacted, fl.ein, ns.website
        FROM lava_corpus.fetch_log fl
        JOIN lava_corpus.nonprofits_seed ns ON fl.ein = ns.ein
        WHERE {where}
        ORDER BY fl.id
        {limit_clause}
    """

    records = []
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        for row in rows:
            website = row[3] or ""
            from urllib.parse import urlsplit
            host = urlsplit(website).hostname or ""
            records.append(UrlRecord(
                id=row[0],
                url=row[1],
                ein=row[2],
                seed_etld1=etld1(host),
            ))
    return records


def _update_fetch_log(engine: Engine, record_id: int, status: str, note: str = "") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE lava_corpus.fetch_log "
                "SET fetch_status = :status, notes = :note "
                "WHERE id = :id"
            ),
            {"status": status, "note": note, "id": record_id},
        )


def _archive_and_record(
    engine: Engine,
    s3_client,
    bucket: str,
    outcome: "fetch_pdf.DownloadOutcome",
    record: UrlRecord,
    run_id: str,
) -> None:
    import datetime
    import hashlib

    now = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    key = f"{config.DEFAULT_S3_PREFIX}/{outcome.content_sha256}.pdf"
    metadata = {
        "source-url": (outcome.final_url or record.url)[:config.MAX_S3_METADATA_URL_LEN],
        "ein": record.ein,
        "crawl-run-id": run_id,
        "fetched-at": now,
        "attribution-confidence": "cross_origin_pdf",
        "discovered-via": "recovery-pass1",
    }

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=outcome.body,
        ContentType="application/pdf",
        Metadata=metadata,
        ServerSideEncryption="AES256",
    )

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO lava_corpus.corpus (
                    ein, content_sha256, source_url, source_url_redacted,
                    file_size_bytes, attribution_confidence, discovered_via,
                    fetched_at
                ) VALUES (
                    :ein, :sha, :url, :url_redacted,
                    :size, :attribution, :discovered_via,
                    :fetched_at
                )
                ON CONFLICT (content_sha256) DO NOTHING
            """),
            {
                "ein": record.ein,
                "sha": outcome.content_sha256,
                "url": outcome.final_url or record.url,
                "url_redacted": redact_url(outcome.final_url or record.url),
                "size": outcome.bytes_read,
                "attribution": "cross_origin_pdf",
                "discovered_via": "recovery-pass1",
                "fetched_at": now,
            },
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pass 1: re-fetch cross_origin_blocked PDF URLs"
    )
    parser.add_argument("--max-urls", type=int, default=None)
    parser.add_argument("--no-limit", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state-filter", type=str, default=None)
    parser.add_argument("--resume-from", type=int, default=0)

    args = parser.parse_args(argv)

    if args.max_urls is None and not args.no_limit:
        parser.error("--max-urls is required (use --no-limit to bypass)")
        return 1

    state_filter = None
    if args.state_filter:
        state_filter = _validate_state_filter(args.state_filter)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    engine = make_app_engine()
    max_urls = args.max_urls if not args.no_limit else None

    records = _query_blocked_urls(
        engine,
        state_filter=state_filter,
        resume_from=args.resume_from,
        max_urls=max_urls,
    )

    log.info("Found %d cross_origin_blocked PDF URLs to process", len(records))

    if args.dry_run:
        for rec in records:
            log.info("would fetch: id=%d url=%s ein=%s", rec.id, rec.url[:100], rec.ein)
        log.info("DRY RUN complete — %d URLs would be fetched", len(records))
        return 0

    import boto3
    import uuid

    s3_client = boto3.client("s3")
    bucket = _s3a.default_bucket()
    run_id = f"recovery-pass1-{uuid.uuid4().hex[:8]}"
    client = ReportsHTTPClient()

    success_count = 0
    fail_count = 0

    for i, rec in enumerate(records):
        if fetch_pdf.is_domain_throttled(rec.url):
            _update_fetch_log(engine, rec.id, "domain_throttled",
                              "mismatch_threshold_exceeded")
            fail_count += 1
            continue

        outcome = fetch_pdf.download(
            rec.url, client, seed_etld1=rec.seed_etld1,
            validate_structure=True, is_pdf_candidate=True,
        )

        if outcome.status == "ok" and outcome.body:
            _archive_and_record(engine, s3_client, bucket, outcome, rec, run_id)
            _update_fetch_log(engine, rec.id, "success")
            success_count += 1
        else:
            _update_fetch_log(engine, rec.id, outcome.status, outcome.note or "")
            fail_count += 1

        if (i + 1) % 100 == 0:
            log.info(
                "Progress: %d/%d (success=%d, fail=%d)",
                i + 1, len(records), success_count, fail_count,
            )

    log.info(
        "Complete: %d total, %d success, %d failed",
        len(records), success_count, fail_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
