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


def fetch_work_batch_legacy(
    conn,
    priority_filter: list[str],
    batch_size: int,
    retry_errors: bool = False,
    reparse: bool = False,
    min_version: str | None = None,
    ntee_filter: str | None = None,
) -> list[dict]:
    """Fetch next batch of unparsed documents matching priority filter.

    Returns list of {content_sha256, source_org_ein}.
    """
    ntee_join = "JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein" if ntee_filter else ""
    ntee_where = "AND ns.ntee_code LIKE %(ntee)s" if ntee_filter else ""

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if reparse and min_version:
            cur.execute(
                f"""
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                JOIN lava_parse.documents d ON d.content_sha256 = c.content_sha256
                {ntee_join}
                WHERE d.parse_version < %(min_version)s
                  AND c.classification = ANY(%(priority)s)
                  {ntee_where}
                ORDER BY c.source_org_ein, c.content_sha256
                LIMIT %(limit)s
                """,
                {
                    "min_version": min_version,
                    "priority": priority_filter,
                    "limit": batch_size,
                    "ntee": ntee_filter,
                },
            )
        elif retry_errors:
            cur.execute(
                f"""
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                JOIN lava_parse.documents d ON d.content_sha256 = c.content_sha256
                {ntee_join}
                WHERE d.error IS NOT NULL
                  AND c.classification = ANY(%(priority)s)
                  {ntee_where}
                ORDER BY c.source_org_ein, c.content_sha256
                LIMIT %(limit)s
                """,
                {"priority": priority_filter, "limit": batch_size, "ntee": ntee_filter},
            )
        else:
            cur.execute(
                f"""
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                {ntee_join}
                WHERE c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_parse.documents
                )
                  AND c.classification = ANY(%(priority)s)
                  {ntee_where}
                ORDER BY c.source_org_ein, c.content_sha256
                LIMIT %(limit)s
                """,
                {"priority": priority_filter, "limit": batch_size, "ntee": ntee_filter},
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


def get_eligible_count(conn, priority_filter: list[str], ntee_filter: str | None = None) -> int:
    """Count documents eligible for parsing (not yet parsed)."""
    with conn.cursor() as cur:
        if ntee_filter:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM lava_corpus.corpus c
                JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
                WHERE c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_parse.documents
                )
                  AND c.classification = ANY(%(priority)s)
                  AND ns.ntee_code LIKE %(ntee)s
                """,
                {"priority": priority_filter, "ntee": ntee_filter},
            )
        else:
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


# ---------------------------------------------------------------------------
# Work queue functions (Spec 0055: multi-instance SKIP LOCKED)
# ---------------------------------------------------------------------------


def populate_work_queue(
    conn, run_id: int, priority_filter: list[str], ntee_filter: str | None = None
) -> int:
    """Materialize eligible docs into work_queue. Returns count inserted."""
    ntee_join = (
        "JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein"
        if ntee_filter
        else ""
    )
    ntee_where = "AND ns.ntee_code LIKE %(ntee)s" if ntee_filter else ""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO lava_parse.work_queue (run_id, content_sha256, source_org_ein)
                SELECT %(run_id)s, c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                {ntee_join}
                WHERE c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_parse.documents
                )
                  AND c.classification = ANY(%(priority)s)
                  {ntee_where}
                ON CONFLICT (run_id, content_sha256) DO NOTHING
                """,
                {"run_id": run_id, "priority": priority_filter, "ntee": ntee_filter},
            )
            return cur.rowcount


def fetch_work_batch(
    conn, run_id: int, batch_size: int, worker_id: str
) -> list[dict]:
    """Claim next batch of unclaimed work items using SKIP LOCKED."""
    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                WITH claimed AS (
                    SELECT id, content_sha256, source_org_ein
                    FROM lava_parse.work_queue
                    WHERE run_id = %(run_id)s AND claimed_by IS NULL
                    ORDER BY id
                    LIMIT %(limit)s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE lava_parse.work_queue wq
                SET claimed_by = %(worker_id)s, claimed_at = NOW()
                FROM claimed
                WHERE wq.id = claimed.id
                RETURNING wq.content_sha256, wq.source_org_ein
                """,
                {"run_id": run_id, "limit": batch_size, "worker_id": worker_id},
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def complete_work_item(
    conn, run_id: int, content_sha256: str, error: str | None = None
) -> None:
    """Mark a work queue item as completed (or errored)."""
    if error and len(error) > 200:
        error = error[:200]
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.work_queue
                SET completed_at = NOW(), error = %(error)s
                WHERE run_id = %(run_id)s AND content_sha256 = %(sha)s
                """,
                {"run_id": run_id, "sha": content_sha256, "error": error},
            )


def reclaim_stale_claims(conn, run_id: int, worker_id: str) -> int:
    """Reset claims for a terminated worker so they can be picked up again."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.work_queue
                SET claimed_by = NULL, claimed_at = NULL
                WHERE run_id = %(run_id)s AND claimed_by = %(worker)s AND completed_at IS NULL
                """,
                {"run_id": run_id, "worker": worker_id},
            )
            return cur.rowcount


def reclaim_all_stale_claims(conn, run_id: int) -> int:
    """For run resume: reclaim all incomplete claims from previous attempt."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.work_queue
                SET claimed_by = NULL, claimed_at = NULL
                WHERE run_id = %(run_id)s AND claimed_by IS NOT NULL AND completed_at IS NULL
                """,
                {"run_id": run_id},
            )
            return cur.rowcount


def get_queue_progress(conn, run_id: int) -> dict:
    """Returns aggregate progress for the orchestrator poll loop."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE claimed_by IS NOT NULL) as claimed,
                COUNT(*) FILTER (WHERE completed_at IS NOT NULL AND error IS NULL) as completed,
                COUNT(*) FILTER (WHERE completed_at IS NOT NULL AND error IS NOT NULL) as errored
            FROM lava_parse.work_queue
            WHERE run_id = %(run_id)s
            """,
            {"run_id": run_id},
        )
        row = cur.fetchone()
        return {
            "total": row[0],
            "claimed": row[1],
            "completed": row[2],
            "errored": row[3],
        }


def get_per_worker_stats(conn, run_id: int) -> list[dict]:
    """Returns per-instance throughput for dashboard display."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT claimed_by,
                   COUNT(*) FILTER (WHERE completed_at IS NOT NULL) as completed,
                   COUNT(*) FILTER (WHERE completed_at IS NULL) as in_progress,
                   MAX(completed_at) as last_activity,
                   EXTRACT(EPOCH FROM MAX(completed_at) - MIN(claimed_at)) as active_seconds
            FROM lava_parse.work_queue
            WHERE run_id = %(run_id)s AND claimed_by IS NOT NULL
            GROUP BY claimed_by
            """,
            {"run_id": run_id},
        )
        return [dict(r) for r in cur.fetchall()]


def cleanup_work_queue(conn, run_id: int) -> int:
    """Delete queue rows after run completion."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lava_parse.work_queue WHERE run_id = %(run_id)s",
                {"run_id": run_id},
            )
            return cur.rowcount


def get_run_status_by_id(conn, run_id: int) -> dict | None:
    """Get status of a parse run by ID."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT id, run_tag, started_at, finished_at, config_json,
                   stats_json, instance_id
            FROM lava_parse.parse_runs
            WHERE id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
