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

# ---------------------------------------------------------------------------
# Spec 0058: parse performance & robustness (hang defense + triage + tuning)
# ---------------------------------------------------------------------------
# Per-doc timeout. 180s covers the measured p99 (69.5s) with margin and stays
# well under the 0056 5-min heartbeat. The *mechanism* (native document_timeout
# vs subprocess kill) is decided by the Phase-0 spike; this is the bound.
PARSE_TIMEOUT_SECONDS = 180

# Absolute, always-on ceilings (the floor of defense — independent of the
# mb/page heuristic, applied to EVERY doc so per-doc cost is bounded even when
# the triage heuristic misses; spec §3.1 / §7).
MAX_NUM_PAGES = 500          # hard convert(max_num_pages=N) ceiling on every doc
ABS_FILE_SIZE_CAP = 50_000_000  # 50 MB — corpus max observed ~49.6 MB (true outlier)
ABS_PAGE_CAP = 200           # page_count > this -> downgrade (NOT a page truncation)

# images_scale: the always-on ceiling for normal docs, and the tighter value
# for triaged/downgraded docs. CAP default 2.0 = no regression vs Docling's
# default; the §4 A/B may lower it toward 1.0-1.5 before shipping.
IMAGES_SCALE_CAP = 2.0
IMAGES_SCALE_DOWNGRADE = 1.0

# TableFormer FAST is a quality-affecting tuning knob: it stays OFF until the
# §4 cell-content A/B proves zero loss, then the operator flips this to True.
# Default False preserves current production behavior (Docling's own default).
TABLEFORMER_FAST = False

# Pre-parse poison triage (spec §3.1). Downgrade (never skip) when a doc is
# image-heavy AND has a thin text layer.
POISON_MB_PER_PAGE = 1.0     # mb_per_page strictly above this is "image-heavy"
POISON_TEXT_FLOOR = 200      # text-layer chars below this is "thin"

# Conditional OCR (spec §3.4). Skip OCR only when an embedded text layer is
# confidently present (>= this many chars). Default keep-OCR-on when uncertain.
OCR_TEXT_FLOOR = 200
# The 0060 pdftotext signal is the preferred detector, but only when the
# backfill is substantially complete — otherwise the OCR decision would flip
# run-to-run as the backfill fills in. Below this fraction, the run uses the
# first_page_text fallback UNIFORMLY so the decision is reproducible (Codex
# red-team). The chosen source + backfill % are recorded in parse_runs.stats_json.
OCR_DETECTOR_BACKFILL_MIN = 0.99


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
