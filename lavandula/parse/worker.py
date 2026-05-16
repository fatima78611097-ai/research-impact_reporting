"""Docling GPU worker — runs on G6.2xlarge spot instance.

Downloads PDFs from S3, parses with Docling, writes structured results
to lava_parse schema in RDS. Designed for single-worker operation with
advisory lock coordination.

Usage:
    python -m lavandula.parse.worker \
        --run-id <id> \
        --host <rds-host> \
        --database <db-name> \
        [--priority annual,impact] \
        [--batch-size 500]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from lavandula.parse import config
from lavandula.parse import chunking
from lavandula.parse import db
from lavandula.parse.chunking import DoclingParseError

logger = logging.getLogger("lavandula.parse.worker")


class TransientError(Exception):
    """Retriable error (network, throttle). Not recorded as document failure."""


class PermanentError(Exception):
    """Non-retriable error (corrupt PDF, parse crash). Recorded in documents.error."""


def main() -> None:
    args = parse_args()
    _setup_logging()

    logger.info("worker starting", extra={"run_id": args.run_id, "priority": args.priority})

    conn = _connect(args)

    if not db.acquire_worker_lock(conn):
        logger.error("another worker is already running (advisory lock held)")
        sys.exit(1)

    try:
        _run_loop(conn, args)
    finally:
        db.release_worker_lock(conn)
        conn.close()

    logger.info("worker finished")


def _run_loop(conn, args) -> None:
    import boto3

    s3 = boto3.client("s3")
    priority = args.priority.split(",") if args.priority else ["annual", "impact"]

    stats = {
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "start_time": time.time(),
    }

    while True:
        batch = db.fetch_work_batch(conn, priority, args.batch_size)
        if not batch:
            logger.info("no more eligible documents")
            break

        with tempfile.TemporaryDirectory(prefix="docling-work-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            pdf_paths = _download_batch(s3, batch, tmp_path)

            for item in batch:
                sha = item["content_sha256"]

                if not config.validate_sha256(sha):
                    logger.warning("invalid sha256 in work queue", extra={"sha": sha[:20]})
                    stats["skipped"] += 1
                    continue

                pdf_path = pdf_paths.get(sha)

                if pdf_path is None:
                    # Download failure after retries is transient — skip, don't
                    # record as permanent error. Doc remains eligible for next run.
                    logger.warning("download failed after retries, skipping", extra={"sha": sha[:16]})
                    stats["transient_skipped"] = stats.get("transient_skipped", 0) + 1
                    stats["total"] += 1
                    continue

                try:
                    result = _process_one(pdf_path, item)
                    db.insert_document(conn, result)
                    stats["succeeded"] += 1
                except TransientError as e:
                    # Transient errors (RDS connection loss, etc.) — don't record,
                    # doc remains eligible for next run.
                    logger.warning("transient error, skipping", extra={"sha": sha[:16], "err": str(e)[:100]})
                    stats["transient_skipped"] = stats.get("transient_skipped", 0) + 1
                except PermanentError as e:
                    # Permanent errors (corrupt PDF, Docling crash, empty parse) —
                    # record so doc is excluded from normal reruns.
                    _record_error(conn, item, e)
                    stats["failed"] += 1
                finally:
                    stats["total"] += 1
                    if pdf_path and pdf_path.exists():
                        pdf_path.unlink()

                if stats["total"] % config.STATS_UPDATE_INTERVAL == 0:
                    db.update_run_stats(conn, args.run_id, stats)

                if _spot_termination_pending():
                    logger.warning("spot termination notice received, exiting gracefully")
                    break

        if _spot_termination_pending():
            break

    stats["end_time"] = time.time()
    stats["duration_seconds"] = stats["end_time"] - stats["start_time"]
    db.finish_run(conn, args.run_id, stats)


def _process_one(pdf_path: Path, item: dict) -> dict:
    """Parse one PDF, return structured result for db.insert_document."""
    start = time.time()

    try:
        doc = chunking.parse_pdf(pdf_path)
    except Exception as exc:
        raise PermanentError(f"docling_parse_failed: {type(exc).__name__}") from exc

    sections = chunking.extract_sections(doc)
    tables = chunking.extract_tables(doc, sections)
    meta = chunking.get_document_metadata(doc)

    total_text_chars = sum(s["char_count"] for s in sections)

    if not sections and total_text_chars == 0:
        raise PermanentError("empty_parse")

    duration_ms = int((time.time() - start) * 1000)

    import docling

    return {
        "sha": item["content_sha256"],
        "org_ein": item["source_org_ein"],
        "parse_version": f"docling-{docling.__version__}",
        "page_count": meta["page_count"],
        "section_count": len(sections),
        "table_count": len(tables),
        "figure_count": meta["figure_count"],
        "total_text_chars": total_text_chars,
        "parse_duration_ms": duration_ms,
        "error": None,
        "metadata_json": config.filter_metadata(meta.get("metadata")),
        "sections": sections,
        "tables": tables,
    }


def _download_batch(s3, batch: list[dict], tmp_dir: Path) -> dict[str, Path]:
    """Download PDFs in parallel. Returns {sha: local_path} for successes."""
    results: dict[str, Path] = {}

    def _download_one(item: dict) -> tuple[str, Path | None]:
        sha = item["content_sha256"]
        if not config.validate_sha256(sha):
            return sha, None
        key = f"{config.S3_PREFIX}{sha}.pdf"
        local_path = tmp_dir / f"{sha}.pdf"
        for attempt in range(config.TRANSIENT_RETRY_COUNT):
            try:
                s3.download_file(config.S3_BUCKET, key, str(local_path))
                return sha, local_path
            except Exception:
                if attempt < config.TRANSIENT_RETRY_COUNT - 1:
                    time.sleep(config.TRANSIENT_RETRY_BASE_SECONDS * (2**attempt))
        return sha, None

    with ThreadPoolExecutor(max_workers=config.DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(_download_one, item): item for item in batch}
        for future in as_completed(futures):
            sha, path = future.result()
            if path is not None:
                results[sha] = path

    return results


def _record_error(conn, item: dict, error) -> None:
    """Insert document row with error field set."""
    import docling

    error_str = config.sanitize_error(error) if isinstance(error, Exception) else str(error)[:config.MAX_ERROR_LEN]

    doc = {
        "sha": item["content_sha256"],
        "org_ein": item["source_org_ein"],
        "parse_version": f"docling-{docling.__version__}",
        "page_count": 0,
        "section_count": 0,
        "table_count": 0,
        "figure_count": 0,
        "total_text_chars": 0,
        "parse_duration_ms": None,
        "error": error_str,
        "metadata_json": None,
        "sections": [],
        "tables": [],
    }
    try:
        db.insert_document(conn, doc)
    except Exception as exc:
        logger.error(
            "failed to record error row",
            extra={"sha": item["content_sha256"][:16], "err": str(exc)[:100]},
        )


def _spot_termination_pending() -> bool:
    """Check EC2 instance metadata for spot termination notice."""
    try:
        import httpx

        resp = httpx.get(
            "http://169.254.169.254/latest/meta-data/spot/instance-action",
            timeout=1.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _connect(args) -> "psycopg2.extensions.connection":
    """Build DB connection with IAM auth."""
    import boto3

    rds_client = boto3.client("rds", region_name="us-east-1")

    def _get_token():
        return rds_client.generate_db_auth_token(
            DBHostname=args.host,
            Port=args.port,
            DBUsername="docling_writer",
            Region="us-east-1",
        )

    return db.get_connection(
        host=args.host,
        port=args.port,
        database=args.database,
        user="docling_writer",
        iam_token_fn=_get_token,
    )


def _setup_logging() -> None:
    """Configure structured JSON logging to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger("lavandula.parse")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["error"] = config.sanitize_error(record.exc_info[1])
        if hasattr(record, "sha"):
            entry["sha"] = record.sha
        extra_keys = {"sha", "run_id", "priority", "err"}
        for k in extra_keys:
            v = getattr(record, k, None)
            if v is not None:
                entry[k] = v
        return json.dumps(entry)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Docling GPU parse worker")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", required=True)
    parser.add_argument("--priority", default="annual,impact")
    parser.add_argument("--batch-size", type=int, default=config.BATCH_SIZE)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
