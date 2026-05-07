"""
Provenance writer for the pipeline control plane.

Updates org_provenance table per-EIN as each stage completes.
Enforces column ownership: a stage can only write to its own column.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from django.db import connection
from django.utils import timezone

from .stages import STAGE_REGISTRY

logger = logging.getLogger("pipeline.provenance")

VALID_STATUSES = frozenset({"not_started", "in_progress", "completed", "failed", "not_applicable"})

PROVENANCE_BASE_DIR = os.environ.get("PROVENANCE_BASE_DIR", "/var/lib/lava/provenance")


class ProvenanceSecurityError(Exception):
    pass


def update_org_provenance(stage_name: str, outcomes: list[tuple[str, str]]) -> int:
    """Update provenance for a batch of EINs after a stage completes.

    Args:
        stage_name: The stage that just completed (must be in STAGE_REGISTRY)
        outcomes: List of (ein, status) tuples

    Returns:
        Count of rows updated/inserted.

    Raises:
        ProvenanceSecurityError: If stage tries to write to wrong column.
        ValueError: If invalid status values are provided.
    """
    if stage_name not in STAGE_REGISTRY:
        raise ValueError(f"Unknown stage: '{stage_name}'")

    stage = STAGE_REGISTRY[stage_name]
    column = stage.provenance_column
    if not column:
        return 0

    # Enforce column ownership
    if not column.startswith(stage_name.replace("-", "_")):
        logger.error(
            "SECURITY: stage '%s' provenance_column '%s' doesn't match expected pattern",
            stage_name, column,
        )
        raise ProvenanceSecurityError(
            f"Stage '{stage_name}' cannot write to column '{column}'"
        )

    # Validate all status values
    for ein, status in outcomes:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}' for EIN {ein}")

    if not outcomes:
        return 0

    completed_column = column.replace("_status", "_completed_at")
    now = timezone.now()
    updated = 0

    # Batch update using raw SQL for performance
    with connection.cursor() as cursor:
        for ein, status in outcomes:
            completed_at = now if status == "completed" else None
            cursor.execute(
                f"""
                INSERT INTO lava_pipeline.org_provenance (ein, {column}, {completed_column}, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ein) DO UPDATE SET
                    {column} = EXCLUDED.{column},
                    {completed_column} = EXCLUDED.{completed_column},
                    updated_at = EXCLUDED.updated_at
                """,
                [ein, status, completed_at, now],
            )
            updated += 1

    logger.info("Updated provenance for %d EINs (stage=%s)", updated, stage_name)
    return updated


def read_provenance_file(job_id: int) -> list[tuple[str, str]]:
    """Read provenance outcomes from the orchestrator-assigned JSONL file.

    Validates path (realpath + prefix check) and parses each line.
    Returns list of (ein, status) tuples.

    Raises:
        ProvenanceSecurityError: If path traversal is detected.
        FileNotFoundError: If file doesn't exist.
    """
    file_path = os.path.join(PROVENANCE_BASE_DIR, f"{job_id}.jsonl")
    real_path = os.path.realpath(file_path)

    if not real_path.startswith(os.path.realpath(PROVENANCE_BASE_DIR)):
        raise ProvenanceSecurityError(
            f"Path traversal detected: {file_path} resolves to {real_path}"
        )

    outcomes = []
    with open(real_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line %d in %s", line_num, real_path)
                continue

            ein = record.get("ein")
            status = record.get("status")

            if not ein or not isinstance(ein, str):
                logger.warning("Skipping line %d: missing/invalid ein", line_num)
                continue
            if status not in VALID_STATUSES:
                logger.warning("Skipping line %d: invalid status '%s'", line_num, status)
                continue

            outcomes.append((ein, status))

    return outcomes
