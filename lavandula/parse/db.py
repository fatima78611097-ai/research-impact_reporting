"""Database operations for the Docling parse pipeline.

Uses raw psycopg2 (not SQLAlchemy) — the worker runs outside Django on
a GPU instance. All queries use parameterized statements exclusively.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

from lavandula.common.lock_keys import (
    DOCLING_PARSE_ORCHESTRATOR,
    DOCLING_PARSE_WORKER,
)
from lavandula.parse.config import validate_sha256

logger = logging.getLogger(__name__)


def get_connection(
    *,
    host: str,
    port: int,
    database: str,
    user: str = "docling_writer",
    password: str | None = None,
    iam_token_fn=None,
) -> psycopg2.extensions.connection:
    """Connect to RDS. Uses IAM token if iam_token_fn provided, else password."""
    pw = iam_token_fn() if iam_token_fn else password
    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=database,
        user=user,
        password=pw,
        sslmode="require",
    )
    conn.autocommit = False
    return conn


def fetch_work_batch(
    conn,
    priority_filter: list[str],
    batch_size: int,
    retry_errors: bool = False,
    reparse: bool = False,
    min_version: str | None = None,
) -> list[dict]:
    """Fetch next batch of unparsed documents matching priority filter.

    Returns list of {content_sha256, source_org_ein}.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if reparse and min_version:
            cur.execute(
                """
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                JOIN lava_parse.documents d ON d.content_sha256 = c.content_sha256
                WHERE d.parse_version < %(min_version)s
                  AND c.classification = ANY(%(priority)s)
                ORDER BY c.source_org_ein, c.content_sha256
                LIMIT %(limit)s
                """,
                {
                    "min_version": min_version,
                    "priority": priority_filter,
                    "limit": batch_size,
                },
            )
        elif retry_errors:
            cur.execute(
                """
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                JOIN lava_parse.documents d ON d.content_sha256 = c.content_sha256
                WHERE d.error IS NOT NULL
                  AND c.classification = ANY(%(priority)s)
                ORDER BY c.source_org_ein, c.content_sha256
                LIMIT %(limit)s
                """,
                {"priority": priority_filter, "limit": batch_size},
            )
        else:
            cur.execute(
                """
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                WHERE c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_parse.documents
                )
                  AND c.classification = ANY(%(priority)s)
                ORDER BY c.source_org_ein, c.content_sha256
                LIMIT %(limit)s
                """,
                {"priority": priority_filter, "limit": batch_size},
            )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def insert_document(conn, doc: dict) -> None:
    """Atomic per-document transaction: INSERT document + sections + tables."""
    sha = doc["sha"]
    if not validate_sha256(sha):
        raise ValueError(f"invalid sha256: {sha!r}")

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lava_parse.documents (
                    content_sha256, source_org_ein, parse_version,
                    page_count, section_count, table_count, figure_count,
                    total_text_chars, parse_duration_ms, error, metadata_json
                ) VALUES (
                    %(sha)s, %(org_ein)s, %(parse_version)s,
                    %(page_count)s, %(section_count)s, %(table_count)s, %(figure_count)s,
                    %(total_text_chars)s, %(parse_duration_ms)s, %(error)s, %(metadata_json)s
                )
                """,
                {
                    "sha": sha,
                    "org_ein": doc["org_ein"],
                    "parse_version": doc["parse_version"],
                    "page_count": doc["page_count"],
                    "section_count": doc["section_count"],
                    "table_count": doc["table_count"],
                    "figure_count": doc["figure_count"],
                    "total_text_chars": doc["total_text_chars"],
                    "parse_duration_ms": doc["parse_duration_ms"],
                    "error": doc["error"],
                    "metadata_json": json.dumps(doc["metadata_json"])
                    if doc["metadata_json"]
                    else None,
                },
            )

            section_ids: dict[int, int] = {}
            if doc.get("sections"):
                section_rows = [
                    (
                        sha,
                        s["section_index"],
                        s["heading"],
                        s["heading_level"],
                        s["body_text"],
                        s["char_count"],
                        s["page_start"],
                        s["page_end"],
                        s["parent_headings"],
                    )
                    for s in doc["sections"]
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO lava_parse.sections (
                        content_sha256, section_index, heading, heading_level,
                        body_text, char_count, page_start, page_end, parent_headings
                    ) VALUES %s
                    RETURNING id, section_index
                    """,
                    section_rows,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                )
                for row in cur.fetchall():
                    section_ids[row[1]] = row[0]

            if doc.get("tables"):
                table_rows = [
                    (
                        sha,
                        t["table_index"],
                        section_ids.get(t.get("section_index")),
                        t["page_number"],
                        t["caption"],
                        t["row_count"],
                        t["col_count"],
                        json.dumps(t["data_json"]),
                        t["markdown"],
                    )
                    for t in doc["tables"]
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO lava_parse.tables (
                        content_sha256, table_index, section_id, page_number,
                        caption, row_count, col_count, data_json, markdown
                    ) VALUES %s
                    """,
                    table_rows,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                )


def delete_document_data(conn, sha: str) -> None:
    """Delete tables, sections, document row for a SHA (for retry/reparse)."""
    if not validate_sha256(sha):
        raise ValueError(f"invalid sha256: {sha!r}")

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lava_parse.tables WHERE content_sha256 = %s", (sha,)
            )
            cur.execute(
                "DELETE FROM lava_parse.sections WHERE content_sha256 = %s", (sha,)
            )
            cur.execute(
                "DELETE FROM lava_parse.documents WHERE content_sha256 = %s", (sha,)
            )


class RunTagConflict(Exception):
    """Raised when a run_tag already exists and is finished (cannot resume)."""


def create_parse_run(conn, run_tag: str, config_dict: dict) -> int:
    """Create or resume a parse_runs record. Returns the run ID.

    - If no row exists: INSERT and return new ID.
    - If row exists with finished_at IS NULL: resume (return existing ID).
    - If row exists with finished_at IS NOT NULL: raise RunTagConflict.
    """
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, finished_at FROM lava_parse.parse_runs WHERE run_tag = %s",
                (run_tag,),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute(
                    "INSERT INTO lava_parse.parse_runs (run_tag, config_json) VALUES (%s, %s) RETURNING id",
                    (run_tag, json.dumps(config_dict)),
                )
                return cur.fetchone()[0]

            run_id, finished_at = row
            if finished_at is not None:
                raise RunTagConflict(
                    f"Run tag '{run_tag}' already completed at {finished_at}. "
                    f"Use a new tag (e.g., '{run_tag}-v2') for reruns."
                )
            return run_id


def update_run_stats(conn, run_id: int, stats: dict) -> None:
    """Update parse_runs with current progress stats."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lava_parse.parse_runs SET stats_json = %s WHERE id = %s",
                (json.dumps(stats), run_id),
            )


def finish_run(conn, run_id: int, stats: dict) -> None:
    """Mark a parse run as finished."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.parse_runs
                SET finished_at = NOW(), stats_json = %s
                WHERE id = %s
                """,
                (json.dumps(stats), run_id),
            )


def get_run_status(conn, run_tag: str) -> dict | None:
    """Get status of a parse run by tag."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT id, run_tag, started_at, finished_at, config_json,
                   stats_json, instance_id
            FROM lava_parse.parse_runs
            WHERE run_tag = %s
            """,
            (run_tag,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_eligible_count(conn, priority_filter: list[str]) -> int:
    """Count documents eligible for parsing (not yet parsed)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM lava_corpus.corpus c
            WHERE c.content_sha256 NOT IN (
                SELECT content_sha256 FROM lava_parse.documents
            )
              AND c.classification = ANY(%(priority)s)
            """,
            {"priority": priority_filter},
        )
        return cur.fetchone()[0]


def acquire_orchestrator_lock(conn) -> bool:
    """Acquire orchestrator advisory lock. Returns True if acquired."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(%s)", (DOCLING_PARSE_ORCHESTRATOR,)
        )
        return cur.fetchone()[0]


def release_orchestrator_lock(conn) -> None:
    """Release orchestrator advisory lock."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_unlock(%s)", (DOCLING_PARSE_ORCHESTRATOR,)
        )


def acquire_worker_lock(conn) -> bool:
    """Acquire worker advisory lock. Returns True if acquired."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(%s)", (DOCLING_PARSE_WORKER,)
        )
        return cur.fetchone()[0]


def release_worker_lock(conn) -> None:
    """Release worker advisory lock."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_unlock(%s)", (DOCLING_PARSE_WORKER,)
        )
