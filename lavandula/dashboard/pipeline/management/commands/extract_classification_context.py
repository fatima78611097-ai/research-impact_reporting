"""Extract multi-page text from S3-archived PDFs into classification_context (Spec 0035).

Usage:
    python3 manage.py extract_classification_context
    python3 manage.py extract_classification_context --limit 1000
    python3 manage.py extract_classification_context --reextract
    python3 manage.py extract_classification_context --download-workers 8 --extract-workers 4
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.reports.extraction import ExtractionResult, extract_pages
from lavandula.reports.s3_archive import S3Archive

log = logging.getLogger(__name__)

_SCHEMA = "lava_corpus"
_BUCKET = "lavandula-nonprofit-collaterals"
_PREFIX = "pdfs"
_PAGE_SIZE = 500
_MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB
_ADVISORY_LOCK_KEY = "extract-context"


class Command(BaseCommand):
    help = "Extract pages 1-5 from S3 PDFs into classification_context"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None,
                            help="Max PDFs to process")
        parser.add_argument("--reextract", action="store_true",
                            help="Force re-extraction of all rows")
        parser.add_argument("--download-workers", type=int, default=8,
                            help="Concurrent S3 download threads")
        parser.add_argument("--extract-workers", type=int, default=4,
                            help="Concurrent extraction workers")

    def handle(self, *args, **options):
        engine = make_app_engine()
        limit = options["limit"]
        reextract = options["reextract"]
        download_workers = options["download_workers"]
        extract_workers = options["extract_workers"]

        self._lock_conn = engine.connect()
        try:
            locked = self._lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": _ADVISORY_LOCK_KEY},
            ).scalar()
            self._lock_conn.commit()
            if not locked:
                self.stderr.write("Another extraction is running (advisory lock held). Exiting.")
                self._lock_conn.close()
                return

            self._run(engine, limit=limit, reextract=reextract,
                      download_workers=download_workers,
                      extract_workers=extract_workers)
        finally:
            try:
                self._lock_conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": _ADVISORY_LOCK_KEY},
                )
                self._lock_conn.commit()
            except Exception:
                log.exception("Failed to release advisory lock")
            finally:
                self._lock_conn.close()

    def _run(self, engine, *, limit, reextract, download_workers, extract_workers):
        archive = S3Archive(_BUCKET, _PREFIX)

        job_id = self._create_job(engine)
        stats = {
            "processed": 0, "extracted": 0, "skipped_oversized": 0,
            "skipped_missing": 0, "sha_mismatch": 0, "failed": 0,
        }

        work_queue: queue.Queue = queue.Queue(maxsize=extract_workers * 2)
        result_lock = threading.Lock()
        results_batch: list[dict] = []
        batch_size = 100
        active_workers = [0]

        from lavandula.reports.stall_watchdog import StallWatchdog

        watchdog = StallWatchdog(
            get_progress=lambda: stats["processed"],
            get_active=lambda: active_workers[0],
            stall_threshold_sec=300,
            notify_email=os.environ.get("WATCHDOG_NOTIFY_EMAIL"),
            job_id=job_id,
        )
        watchdog.start_thread()

        def extraction_consumer():
            while True:
                item = work_queue.get()
                if item is None:
                    work_queue.task_done()
                    break
                sha256, pdf_bytes = item
                active_workers[0] += 1
                try:
                    result = extract_pages(pdf_bytes)
                    row = {
                        "content_sha256": sha256,
                        "pages_text": result.pages_text,
                        "pages_extracted": result.pages_extracted,
                        "total_pages": result.total_pages,
                        "extraction_method": result.extraction_method,
                        "text_length": result.text_length,
                    }
                    with result_lock:
                        results_batch.append(row)
                        if result.extraction_method.startswith("failed:"):
                            stats["failed"] += 1
                        else:
                            stats["extracted"] += 1
                        stats["processed"] += 1
                except Exception:
                    log.exception("Extraction error for sha=%s", sha256[:12])
                    with result_lock:
                        stats["failed"] += 1
                        stats["processed"] += 1
                finally:
                    active_workers[0] -= 1
                    work_queue.task_done()

        extract_threads = []
        for _ in range(extract_workers):
            t = threading.Thread(target=extraction_consumer, daemon=True)
            t.start()
            extract_threads.append(t)

        last_cursor = ""
        remaining = limit
        total_queued = 0

        try:
            while True:
                page_limit = _PAGE_SIZE
                if remaining is not None:
                    page_limit = min(page_limit, remaining)
                    if page_limit <= 0:
                        break

                if reextract:
                    sql = (
                        f"SELECT c.content_sha256, c.file_size "
                        f"FROM {_SCHEMA}.corpus c "
                        f"WHERE c.content_type = 'application/pdf' "
                        f"  AND c.content_sha256 > :cursor "
                        f"ORDER BY c.content_sha256 LIMIT :page_limit"
                    )
                else:
                    sql = (
                        f"SELECT c.content_sha256, c.file_size "
                        f"FROM {_SCHEMA}.corpus c "
                        f"LEFT JOIN {_SCHEMA}.classification_context cc "
                        f"  ON cc.content_sha256 = c.content_sha256 "
                        f"WHERE c.content_type = 'application/pdf' "
                        f"  AND cc.content_sha256 IS NULL "
                        f"  AND c.content_sha256 > :cursor "
                        f"ORDER BY c.content_sha256 LIMIT :page_limit"
                    )

                with engine.connect() as conn:
                    rows = conn.execute(
                        text(sql), {"cursor": last_cursor, "page_limit": page_limit}
                    ).fetchall()

                if not rows:
                    break

                sha_list = []
                size_map = {}
                for content_sha256, file_size in rows:
                    last_cursor = content_sha256
                    sha_list.append(content_sha256)
                    size_map[content_sha256] = file_size

                oversized = []
                to_download = []
                for sha in sha_list:
                    fs = size_map.get(sha)
                    if fs is not None and fs > _MAX_PDF_BYTES:
                        oversized.append(sha)
                    else:
                        to_download.append(sha)

                if oversized:
                    self._write_oversized(engine, oversized)
                    with result_lock:
                        stats["skipped_oversized"] += len(oversized)
                        stats["processed"] += len(oversized)

                def download_and_queue(sha):
                    try:
                        pdf_bytes = archive.get(sha)
                    except Exception as exc:
                        exc_str = type(exc).__name__
                        if "NoSuchKey" in str(exc) or "404" in str(exc):
                            log.warning("S3 missing: sha=%s", sha[:12])
                            with result_lock:
                                stats["skipped_missing"] += 1
                                stats["processed"] += 1
                            return
                        log.warning("S3 error for sha=%s: %s", sha[:12], exc_str)
                        with result_lock:
                            stats["failed"] += 1
                            stats["processed"] += 1
                        return

                    if len(pdf_bytes) > _MAX_PDF_BYTES:
                        log.warning("S3 object oversized after download: sha=%s size=%d",
                                    sha[:12], len(pdf_bytes))
                        self._write_oversized_single(engine, sha)
                        with result_lock:
                            stats["skipped_oversized"] += 1
                            stats["processed"] += 1
                        return

                    actual_sha = hashlib.sha256(pdf_bytes).hexdigest()
                    if actual_sha != sha:
                        log.warning("SHA256 mismatch: expected=%s actual=%s",
                                    sha[:12], actual_sha[:12])
                        with result_lock:
                            stats["sha_mismatch"] += 1
                            stats["processed"] += 1
                        return

                    work_queue.put((sha, pdf_bytes))

                with ThreadPoolExecutor(max_workers=download_workers) as dl_pool:
                    list(dl_pool.map(download_and_queue, to_download))

                work_queue.join()

                if results_batch:
                    with result_lock:
                        to_flush = list(results_batch)
                        results_batch.clear()
                    self._bulk_insert(engine, to_flush)

                if remaining is not None:
                    remaining -= len(rows)

                self._update_job(engine, job_id, stats)
                total_queued += len(rows)

                if len(rows) < page_limit:
                    break

        finally:
            for _ in extract_threads:
                work_queue.put(None)
            for t in extract_threads:
                t.join(timeout=60)

            watchdog.stop()
            if watchdog._thread is not None:
                watchdog._thread.join(timeout=2)

            if results_batch:
                with result_lock:
                    to_flush = list(results_batch)
                    results_batch.clear()
                self._bulk_insert(engine, to_flush)

        self._finish_job(engine, job_id, stats)
        self.stdout.write(
            f"Extraction complete: {stats['processed']} processed, "
            f"{stats['extracted']} extracted, {stats['failed']} failed, "
            f"{stats['skipped_oversized']} oversized, "
            f"{stats['skipped_missing']} missing, "
            f"{stats['sha_mismatch']} sha mismatch"
        )

    def _write_oversized(self, engine, sha_list):
        with engine.begin() as conn:
            for sha in sha_list:
                conn.execute(text(
                    f"INSERT INTO {_SCHEMA}.classification_context "
                    f"(content_sha256, pages_text, pages_extracted, total_pages, "
                    f" extraction_method, text_length) "
                    f"VALUES (:sha, '', 0, NULL, 'skipped:oversized', 0) "
                    f"ON CONFLICT (content_sha256) DO NOTHING"
                ), {"sha": sha})

    def _write_oversized_single(self, engine, sha):
        self._write_oversized(engine, [sha])

    def _bulk_insert(self, engine, rows):
        if not rows:
            return
        with engine.begin() as conn:
            for row in rows:
                conn.execute(text(
                    f"INSERT INTO {_SCHEMA}.classification_context "
                    f"(content_sha256, pages_text, pages_extracted, total_pages, "
                    f" extraction_method, text_length) "
                    f"VALUES (:content_sha256, :pages_text, :pages_extracted, "
                    f" :total_pages, :extraction_method, :text_length) "
                    f"ON CONFLICT (content_sha256) DO NOTHING"
                ), row)

    def _create_job(self, engine):
        from pipeline.models import Job
        job = Job.objects.create(
            phase="extract-context",
            status="running",
            config={},
            started_at=timezone.now(),
        )
        return job.id

    def _update_job(self, engine, job_id, stats):
        try:
            from pipeline.models import Job
            Job.objects.filter(id=job_id).update(config={"stats": stats})
        except Exception:
            log.exception("Failed to update job %d", job_id)

    def _finish_job(self, engine, job_id, stats):
        try:
            from pipeline.models import Job
            Job.objects.filter(id=job_id).update(
                status="completed",
                config={"stats": stats},
                completed_at=timezone.now(),
            )
        except Exception:
            log.exception("Failed to finish job %d", job_id)
