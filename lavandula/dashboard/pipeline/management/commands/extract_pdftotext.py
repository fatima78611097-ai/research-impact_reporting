"""Extract pdftotext from S3-archived PDFs and compute fidelity scores (Spec 0060).

Usage:
    python3 manage.py extract_pdftotext
    python3 manage.py extract_pdftotext --limit 100
    python3 manage.py extract_pdftotext --sha abc123...
    python3 manage.py extract_pdftotext --force
    python3 manage.py extract_pdftotext --force --version-mismatch
    python3 manage.py extract_pdftotext --run-tag p20-v1
"""
from __future__ import annotations

import logging
import re
import tempfile
import time

from django.core.management.base import BaseCommand
from sqlalchemy import text as sa_text

from lavandula.common.db import make_app_engine
from lavandula.faithfulness.fidelity import classify_text_source, score_fidelity
from lavandula.faithfulness.pdftotext_extract import (
    ExtractResult,
    extract_text,
    get_pdftotext_version,
)
from lavandula.reports.s3_archive import S3Archive

log = logging.getLogger(__name__)

_BUCKET = "lavandula-nonprofit-collaterals"
_PREFIX = "pdfs"
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")


class Command(BaseCommand):
    help = "Extract pdftotext text from S3-archived PDFs, compute fidelity scores (Spec 0060)"

    def add_arguments(self, parser):
        parser.add_argument("--run-tag", type=str, default=None,
                            help="Filter to documents from a specific extraction run")
        parser.add_argument("--limit", type=int, default=None,
                            help="Max documents to process")
        parser.add_argument("--sha", type=str, default=None,
                            help="Process a single document by SHA-256")
        parser.add_argument("--force", action="store_true",
                            help="Re-extract even if pdftotext row exists")
        parser.add_argument("--version-mismatch", action="store_true",
                            help="With --force: only re-extract where stored version differs")

    def handle(self, *args, **options):
        sha_filter = options["sha"]
        if sha_filter and not _SHA_RE.match(sha_filter):
            self.stderr.write("Invalid SHA-256: must be 64 hex characters")
            raise SystemExit(1)

        try:
            version = get_pdftotext_version()
        except FileNotFoundError as exc:
            self.stderr.write(str(exc))
            raise SystemExit(1)

        self.stdout.write(f"pdftotext version: {version}")

        engine = make_app_engine()
        archive = S3Archive(_BUCKET, _PREFIX)

        shas = self._get_target_shas(
            engine, options, sha_filter, version,
        )

        if not shas:
            self.stdout.write("No documents to process.")
            return

        self.stdout.write(f"Processing {len(shas)} documents...")

        stats = {"processed": 0, "extracted": 0, "scanned": 0, "failed": 0, "skipped": 0}
        t0 = time.monotonic()

        for i, sha in enumerate(shas):
            try:
                self._process_one(engine, archive, sha, version, stats)
            except Exception:
                log.exception("Unexpected error processing %s", sha[:16])
                stats["failed"] += 1

            if (i + 1) % 100 == 0:
                elapsed = time.monotonic() - t0
                self.stdout.write(
                    f"  Progress: {i + 1}/{len(shas)} docs, "
                    f"{stats['extracted']} extracted, {stats['scanned']} scanned, "
                    f"{stats['failed']} failed ({elapsed:.0f}s)"
                )

        elapsed = time.monotonic() - t0
        self.stdout.write(
            f"\nComplete ({elapsed:.1f}s): {stats['processed']} processed, "
            f"{stats['extracted']} text-native, {stats['scanned']} scanned, "
            f"{stats['failed']} failed, {stats['skipped']} skipped"
        )

    def _get_target_shas(self, engine, options, sha_filter, version):
        force = options["force"]
        version_mismatch = options["version_mismatch"]
        run_tag = options["run_tag"]
        limit = options["limit"]

        if sha_filter:
            return [sha_filter]

        with engine.connect() as conn:
            if force and version_mismatch:
                rows = conn.execute(sa_text("""
                    SELECT d.content_sha256
                    FROM lava_parse.documents d
                    JOIN lava_parse.pdftotext p ON d.content_sha256 = p.content_sha256
                    WHERE p.pdftotext_version != :version
                    ORDER BY d.content_sha256
                    LIMIT :lim
                """), {"version": version, "lim": limit or 999999999}).fetchall()
            elif force:
                if run_tag:
                    rows = conn.execute(sa_text("""
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_parse.parse_runs r ON r.run_tag = :tag
                        WHERE d.metadata_json->>'run_tag' = :tag
                           OR d.parsed_at >= r.started_at
                        ORDER BY d.content_sha256
                        LIMIT :lim
                    """), {"tag": run_tag, "lim": limit or 999999999}).fetchall()
                else:
                    rows = conn.execute(sa_text("""
                        SELECT content_sha256
                        FROM lava_parse.documents
                        ORDER BY content_sha256
                        LIMIT :lim
                    """), {"lim": limit or 999999999}).fetchall()
            else:
                if run_tag:
                    rows = conn.execute(sa_text("""
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        LEFT JOIN lava_parse.pdftotext p ON d.content_sha256 = p.content_sha256
                        JOIN lava_parse.parse_runs r ON r.run_tag = :tag
                        WHERE p.content_sha256 IS NULL
                          AND (d.metadata_json->>'run_tag' = :tag
                               OR d.parsed_at >= r.started_at)
                        ORDER BY d.content_sha256
                        LIMIT :lim
                    """), {"tag": run_tag, "lim": limit or 999999999}).fetchall()
                else:
                    rows = conn.execute(sa_text("""
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        LEFT JOIN lava_parse.pdftotext p ON d.content_sha256 = p.content_sha256
                        WHERE p.content_sha256 IS NULL
                        ORDER BY d.content_sha256
                        LIMIT :lim
                    """), {"lim": limit or 999999999}).fetchall()

        return [r[0] for r in rows]

    def _process_one(self, engine, archive, sha, version, stats):
        pdf_bytes = self._download_pdf(archive, sha)
        if pdf_bytes is None:
            stats["failed"] += 1
            return

        result = extract_text(pdf_bytes)

        docling_text = self._get_docling_text(engine, sha)
        fidelity = None
        if docling_text and not result.failed and not result.is_scanned:
            fidelity = score_fidelity(docling_text, result.text)

        text_source = classify_text_source(result, fidelity)

        with engine.begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO lava_parse.pdftotext
                    (content_sha256, pdftotext_version, full_text, char_count)
                VALUES (:sha, :version, :text, :chars)
                ON CONFLICT (content_sha256) DO UPDATE SET
                    pdftotext_version = EXCLUDED.pdftotext_version,
                    full_text = EXCLUDED.full_text,
                    char_count = EXCLUDED.char_count,
                    extracted_at = now()
            """), {
                "sha": sha,
                "version": version,
                "text": result.text,
                "chars": result.char_count,
            })

            conn.execute(sa_text("""
                UPDATE lava_parse.documents
                SET pdftotext_coverage = :fwd,
                    pdftotext_reverse = :rev,
                    text_source = :src
                WHERE content_sha256 = :sha
            """), {
                "sha": sha,
                "fwd": fidelity.forward if fidelity else None,
                "rev": fidelity.reverse if fidelity else None,
                "src": text_source,
            })

        stats["processed"] += 1
        if text_source == "text_native":
            stats["extracted"] += 1
        elif text_source == "scanned":
            stats["scanned"] += 1
        else:
            stats["failed"] += 1

    def _download_pdf(self, archive, sha):
        try:
            return archive.get(sha)
        except Exception:
            log.warning("S3 download failed for %s, retrying", sha[:16])
            try:
                return archive.get(sha)
            except Exception:
                log.warning("S3 download failed on retry for %s", sha[:16])
                return None

    def _get_docling_text(self, engine, sha):
        with engine.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT heading, body_text
                FROM lava_parse.sections
                WHERE content_sha256 = :sha
                ORDER BY section_index
            """), {"sha": sha}).fetchall()

        if not rows:
            return None

        parts = []
        for heading, body in rows:
            if heading:
                parts.append(heading)
            parts.append(body)
        return "\n\n".join(parts)
