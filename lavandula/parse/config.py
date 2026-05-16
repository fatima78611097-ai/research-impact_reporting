"""Configuration constants and validation for the Docling parse pipeline."""
from __future__ import annotations

import re


PARSE_VERSION_PREFIX = "docling"
BATCH_SIZE = 500
STATS_UPDATE_INTERVAL = 50
MAX_ERROR_LEN = 500
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
PRIORITY_VALUE_RE = re.compile(r"^[a-z_]+$")
METADATA_ALLOWED_KEYS = frozenset(["title", "year"])
S3_BUCKET = "lavandula-nonprofit-collaterals"
S3_PREFIX = "pdfs/"
THUMBNAIL_PREFIX = "thumbnails/"
THUMBNAIL_WIDTH = 200
THUMBNAIL_QUALITY = 60

TRANSIENT_RETRY_COUNT = 3
TRANSIENT_RETRY_BASE_SECONDS = 2.0

LARGE_PDF_PAGE_THRESHOLD = 500
DOWNLOAD_WORKERS = 8


def validate_sha256(sha: str) -> bool:
    return bool(SHA256_RE.match(sha))


def validate_run_tag(tag: str) -> bool:
    return bool(RUN_TAG_RE.match(tag))


def validate_priority_values(values: list[str]) -> bool:
    return all(PRIORITY_VALUE_RE.match(v) for v in values)


def sanitize_error(exc: Exception) -> str:
    """Return error class + truncated message, max 500 chars. No stack traces or internal paths."""
    class_name = type(exc).__name__
    msg = str(exc)
    msg = re.sub(r"(/[^\s:]+)", "<path>", msg)
    msg = re.sub(r"(Traceback.*)", "", msg, flags=re.DOTALL)
    combined = f"{class_name}: {msg}".strip()
    return combined[:MAX_ERROR_LEN]


def filter_metadata(raw_metadata: dict | None) -> dict | None:
    """Keep only allowed keys (title, year). Discard everything else."""
    if not raw_metadata:
        return None
    filtered = {k: v for k, v in raw_metadata.items() if k in METADATA_ALLOWED_KEYS}
    return filtered or None
