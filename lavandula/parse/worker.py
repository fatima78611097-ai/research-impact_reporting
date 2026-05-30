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
import atexit
import json
import logging
import re
import sys
import tempfile
import threading
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


class HeartbeatThread(threading.Thread):
    """Daemon thread that writes heartbeat to DB every `interval` seconds.

    Shares docs_completed and current_doc_sha with the main processing loop
    via a simple lock. Never crashes the worker — all DB errors are swallowed.
    """

    def __init__(self, conn_factory, instance_id: str, run_id: int, interval: int = 60):
        super().__init__(daemon=True, name="heartbeat")
        self._conn_factory = conn_factory
        self._instance_id = instance_id
        self._run_id = run_id
        self._interval = interval
        self._lock = threading.Lock()
        self._docs_completed = 0
        self._current_doc_sha: str | None = None
        self._stop_event = threading.Event()

    def update(self, docs_completed: int, current_doc_sha: str | None) -> None:
        with self._lock:
            self._docs_completed = docs_completed
            self._current_doc_sha = current_doc_sha

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        conn = None
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            with self._lock:
                docs = self._docs_completed
                sha = self._current_doc_sha
            try:
                if conn is None:
                    conn = self._conn_factory()
                db.upsert_heartbeat(conn, self._instance_id, self._run_id, docs, sha)
            except Exception:
                logger.warning("heartbeat write failed, will retry next cycle")
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _parse_version() -> str:
    """Resolve the installed docling version string. Never raises.

    Used by BOTH the success path (_process_one) and the error path
    (_record_error) so they cannot diverge — a divergence here previously
    crashed the whole worker on the first PermanentError doc.
    """
    from importlib.metadata import version as _pkg_version

    try:
        return f"docling-{_pkg_version('docling')}"
    except Exception:
        return "docling-unknown"


def _ship_log_on_exit(run_tag: str | None, instance_id: str | None) -> None:
    """Best-effort atexit: upload worker log to S3."""
    if not run_tag or not instance_id:
        return
    log_path = Path("/var/log/docling-worker.log")
    if not log_path.exists():
        return
    try:
        import boto3

        s3 = boto3.client("s3")
        s3_key = f"logs/parse/{run_tag}/{instance_id}/worker.log"
        s3.upload_file(str(log_path), config.S3_BUCKET, s3_key)
        logger.info("shipped worker log to S3", extra={"s3_key": s3_key})
    except Exception:
        pass


def main() -> None:
    args = parse_args()
    _setup_logging()

    if args.worker_id and not re.match(r"^i-[0-9a-f]+$", args.worker_id):
        logger.error("invalid worker-id format", extra={"worker_id": args.worker_id})
        sys.exit(1)

    if not (isinstance(args.run_id, int) and args.run_id > 0):
        logger.error("invalid run-id: must be a positive integer")
        sys.exit(1)

    logger.info("worker starting", extra={"run_id": args.run_id, "priority": args.priority})

    conn = _connect(args)

    run_tag = _get_run_tag(conn, args.run_id)
    if args.worker_id and run_tag:
        atexit.register(_ship_log_on_exit, run_tag, args.worker_id)

    if args.worker_id:
        with conn.cursor() as cur:
            cur.execute("SET app.worker_id = %s", (args.worker_id,))

        status = db.get_run_status_by_id(conn, args.run_id)
        if status is None:
            logger.error("run_id does not exist in parse_runs", extra={"run_id": args.run_id})
            conn.close()
            sys.exit(1)

        heartbeat = HeartbeatThread(
            conn_factory=lambda: _connect(args),
            instance_id=args.worker_id,
            run_id=args.run_id,
        )
        heartbeat.start()

        try:
            _run_loop_queue(conn, args, heartbeat=heartbeat)
        except Exception:
            logger.exception("worker fatal error in queue loop")
            try:
                db.set_exit_reason(conn, args.run_id, "error")
            except Exception:
                pass
            try:
                db.reclaim_stale_claims(conn, args.run_id, args.worker_id)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            sys.exit(1)
        finally:
            heartbeat.stop()
    else:
        if not db.acquire_worker_lock(conn):
            logger.error("another worker is already running (advisory lock held)")
            conn.close()
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

    max_docs = args.max_docs

    while True:
        if max_docs is not None and stats["total"] >= max_docs:
            logger.info("reached max-docs limit", extra={"max_docs": max_docs})
            break

        batch = db.fetch_work_batch_legacy(conn, priority, args.batch_size, ntee_filter=args.ntee)
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
                    _generate_thumbnail(s3, pdf_path, sha)
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


def _run_loop_queue(conn, args, heartbeat: HeartbeatThread | None = None) -> None:
    """Queue mode: claim work via SKIP LOCKED, complete items individually."""
    import boto3

    s3 = boto3.client("s3")

    stats = {
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "start_time": time.time(),
    }

    max_docs = args.max_docs
    exit_reason = "unknown"

    transient_attempts: dict[str, int] = {}
    max_transient_attempts = getattr(config, "MAX_TRANSIENT_ATTEMPTS", 3)
    consecutive_fetch_errors = 0

    while True:
        if max_docs is not None and stats["total"] >= max_docs:
            logger.info("reached max-docs limit", extra={"max_docs": max_docs})
            exit_reason = "max_docs"
            break

        try:
            batch = db.fetch_work_batch(conn, args.run_id, args.batch_size, args.worker_id)
            consecutive_fetch_errors = 0
        except Exception as exc:
            consecutive_fetch_errors += 1
            logger.error(
                "fetch_work_batch failed",
                extra={"err": str(exc)[:150], "attempt": consecutive_fetch_errors},
            )
            _safe_rollback(conn)
            if consecutive_fetch_errors >= 5:
                logger.error("giving up after repeated fetch failures")
                exit_reason = "error"
                break
            time.sleep(config.TRANSIENT_RETRY_BASE_SECONDS * consecutive_fetch_errors)
            continue

        if not batch:
            logger.info("no more unclaimed work items")
            exit_reason = "empty_batch"
            break

        with tempfile.TemporaryDirectory(prefix="docling-work-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            pdf_paths = _download_batch(s3, batch, tmp_path)

            for item in batch:
                sha = item["content_sha256"]

                if heartbeat:
                    heartbeat.update(stats["succeeded"], sha)

                if not config.validate_sha256(sha):
                    logger.warning("invalid sha256 in work queue", extra={"sha": sha[:20]})
                    try:
                        db.complete_work_item(conn, args.run_id, sha, error="invalid_sha256")
                    except Exception:
                        _safe_rollback(conn)
                    stats["skipped"] += 1
                    stats["total"] += 1
                    continue

                pdf_path = pdf_paths.get(sha)

                if pdf_path is None:
                    logger.warning("download failed after retries", extra={"sha": sha[:16]})
                    _handle_transient(conn, args, sha, "download_failed",
                                      transient_attempts, max_transient_attempts, stats)
                    stats["total"] += 1
                    continue

                try:
                    result = _process_one(pdf_path, item)
                    db.insert_document(conn, result)
                    _generate_thumbnail(s3, pdf_path, sha)
                    db.complete_work_item(conn, args.run_id, sha)
                    stats["succeeded"] += 1
                except TransientError as e:
                    logger.warning("transient error", extra={"sha": sha[:16], "err": str(e)[:100]})
                    _safe_rollback(conn)
                    _handle_transient(conn, args, sha, str(e)[:100],
                                      transient_attempts, max_transient_attempts, stats)
                except PermanentError as e:
                    error_label = str(e)[:200] if str(e) else "unknown_error"
                    try:
                        _record_error(conn, item, e)
                    except Exception as rec_exc:
                        logger.error("record_error failed",
                                     extra={"sha": sha[:16], "err": str(rec_exc)[:120]})
                        _safe_rollback(conn)
                    try:
                        db.complete_work_item(conn, args.run_id, sha, error=error_label)
                    except Exception:
                        _safe_rollback(conn)
                    stats["failed"] += 1
                except Exception as exc:
                    logger.error("unexpected error, marking item failed",
                                 extra={"sha": sha[:16], "err": str(exc)[:200]})
                    _safe_rollback(conn)
                    try:
                        db.complete_work_item(conn, args.run_id, sha,
                                              error=f"unexpected:{type(exc).__name__}")
                    except Exception:
                        _safe_rollback(conn)
                    stats["failed"] += 1
                finally:
                    stats["total"] += 1
                    if pdf_path and pdf_path.exists():
                        pdf_path.unlink()

                try:
                    if stats["total"] % config.STATS_UPDATE_INTERVAL == 0:
                        db.update_run_stats(conn, args.run_id, stats)
                except Exception as exc:
                    logger.warning("update_run_stats failed", extra={"err": str(exc)[:100]})
                    _safe_rollback(conn)

                if _spot_termination_pending():
                    logger.warning("spot termination notice received, exiting gracefully")
                    exit_reason = "spot_termination"
                    break

        if _spot_termination_pending():
            exit_reason = "spot_termination"
            break

    stats["end_time"] = time.time()
    stats["duration_seconds"] = stats["end_time"] - stats["start_time"]
    try:
        db.update_run_stats(conn, args.run_id, stats)
    except Exception as exc:
        logger.warning("final update_run_stats failed", extra={"err": str(exc)[:100]})
        _safe_rollback(conn)

    try:
        db.set_exit_reason(conn, args.run_id, exit_reason)
    except Exception:
        logger.warning("failed to set exit_reason", extra={"exit_reason": exit_reason})
        _safe_rollback(conn)


def _safe_rollback(conn) -> None:
    """Roll back the current transaction, swallowing any error.

    Required after an aborted statement under autocommit=False so the shared
    connection is usable for the next item instead of stuck 'in failed txn'.
    """
    try:
        conn.rollback()
    except Exception:
        pass


def _handle_transient(conn, args, sha, reason, attempts, max_attempts, stats) -> None:
    """Bounded retry for transient / download failures.

    Below the attempt cap: unclaim the row so it returns to the pool for retry.
    At/above the cap: record it as errored so it never stays stranded as a
    claimed-but-incomplete row (which would silently wedge the run tail).
    """
    attempts[sha] = attempts.get(sha, 0) + 1
    if attempts[sha] >= max_attempts:
        logger.warning("transient retries exhausted, marking errored",
                       extra={"sha": sha[:16], "err": reason})
        try:
            db.complete_work_item(conn, args.run_id, sha,
                                  error=f"transient_exhausted:{reason}"[:200])
        except Exception:
            _safe_rollback(conn)
        stats["failed"] += 1
    else:
        try:
            db.unclaim_work_item(conn, args.run_id, sha)
        except Exception:
            _safe_rollback(conn)
        stats["transient_skipped"] = stats.get("transient_skipped", 0) + 1


def _process_one(pdf_path: Path, item: dict) -> dict:
    """Parse one PDF, return structured result for db.insert_document."""
    start = time.time()

    try:
        doc = chunking.parse_pdf(pdf_path)
    except Exception as exc:
        raise PermanentError(f"docling_parse_failed: {type(exc).__name__}") from exc

    # A3: post-parse extraction can also fault (CUDA/OOM from OCR, malformed
    # chunking output). Reclassify so these route through the handled error
    # paths instead of escaping as a raw exception that kills the worker.
    try:
        sections = chunking.extract_sections(doc)
        tables = chunking.extract_tables(doc, sections)
        meta = chunking.get_document_metadata(doc)
    except Exception as exc:
        msg = str(exc).lower()
        if any(k in msg for k in ("cuda", "device-side assert", "out of memory", "nvml", "cublas")):
            raise TransientError(f"gpu_fault: {type(exc).__name__}") from exc
        raise PermanentError(f"docling_postparse_failed: {type(exc).__name__}") from exc

    total_text_chars = sum(s["char_count"] for s in sections)

    if not sections and total_text_chars == 0:
        raise PermanentError("empty_parse")

    duration_ms = int((time.time() - start) * 1000)

    return {
        "sha": item["content_sha256"],
        "org_ein": item["source_org_ein"],
        "parse_version": _parse_version(),
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


def _generate_thumbnail(s3, pdf_path: Path, sha: str) -> None:
    """Render page 1 as JPEG thumbnail and upload to S3."""
    try:
        from pdf2image import convert_from_path
        from io import BytesIO

        pages = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=72)
        if not pages:
            return

        img = pages[0]
        ratio = config.THUMBNAIL_WIDTH / img.width
        img = img.resize((config.THUMBNAIL_WIDTH, int(img.height * ratio)))

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=config.THUMBNAIL_QUALITY)
        buf.seek(0)

        key = f"{config.THUMBNAIL_PREFIX}{sha}.jpg"
        s3.put_object(
            Bucket=config.S3_BUCKET,
            Key=key,
            Body=buf.getvalue(),
            ContentType="image/jpeg",
        )
    except Exception as exc:
        logger.warning("thumbnail generation failed", extra={"sha": sha[:16], "err": str(exc)[:80]})


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
    error_str = config.sanitize_error(error) if isinstance(error, Exception) else str(error)[:config.MAX_ERROR_LEN]

    doc = {
        "sha": item["content_sha256"],
        "org_ein": item["source_org_ein"],
        "parse_version": _parse_version(),
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


def _get_run_tag(conn, run_id: int) -> str | None:
    """Look up the run_tag for a run_id. Used for log shipping path."""
    try:
        status = db.get_run_status_by_id(conn, run_id)
        return status.get("run_tag") if status else None
    except Exception:
        return None


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
    parser.add_argument("--max-docs", type=int, default=None,
                        help="Stop after processing this many documents (required for safety)")
    parser.add_argument("--ntee", default=None,
                        help="NTEE prefix filter (e.g. 'P2%%' for Human Services)")
    parser.add_argument("--worker-id", default=None,
                        help="EC2 instance ID (enables SKIP LOCKED queue mode)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
