"""Configuration constants and validation for the Docling parse pipeline."""
from __future__ import annotations

import re


PARSE_VERSION_PREFIX = "docling"
# Parse OUTPUT-schema version, independent of the Docling library version. Appended to
# parse_version as "+<schema>" so `reparse --min-version` can target docs parsed before a
# schema change. Lexically "docling-X.Y.Z" < "docling-X.Y.Z+s2" (prefix), so bumping this
# makes every prior doc sort below the new version -> a clean, idempotent reparse.
# History: (no suffix) = source_locations/bbox/charspan era; s2 = + page_dimensions (lava_parse.pages);
# s3 = + figure/picture regions (lava_parse.figures) — the infographic / vision-verify router.
PARSE_SCHEMA_VERSION = "s3"
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

# ---------------------------------------------------------------------------
# Spec 0058 Phase 2 — subprocess isolation (§3.3). The Phase-0 spike REJECTED
# the native document_timeout path: the poison doc segfaults Docling's native
# pdf_parsers.so (exit 139), uncatchable in-process; document_timeout is also a
# soft between-stages check (75s convert overran a 60s limit). A persistent
# child process is the ONLY thing that contains segfault + hang + OOM.
# ---------------------------------------------------------------------------
# Hard wall-clock bound the PARENT enforces by killing the child. This is the
# real per-doc timeout (not the native option). 180s covers the observed 75.5s
# and corpus p99 69.5s with margin, well under the 0056 5-min heartbeat.
PARSE_TIMEOUT_SECONDS = 180

# Optional soft, in-child document_timeout. NOT wired by default: the Phase-0
# spike found Docling's native document_timeout unreliable (overran a 60s limit
# by ~25%, and cannot interrupt a native segfault), so the parent hard kill is
# the authoritative bound. Kept here only so an operator could opt a doc into a
# soft self-abort via build_parse_options(document_timeout=...) if ever useful.
PARSE_CHILD_SOFT_TIMEOUT_SECONDS = 150

# Address-space cap on the child (resource.RLIMIT_AS), so an OOM / image bomb is
# DISABLED (None) — the Phase-6 smoke test (run 34) proved RLIMIT_AS is the WRONG
# tool for a GPU worker: it caps VIRTUAL address space, and CUDA/cuDNN reserve a
# very large virtual footprint + load sublibraries on demand. A 14 GB cap starved
# that init -> every convert failed with std::bad_alloc / "Cannot load symbol
# cudnnCreateTensorDescriptor / CUDNN_STATUS_NOT_SUPPORTED_SUBLIBRARY_UNAVAILABLE".
# Isolation test on an L4: RLIMIT_AS 14 GB -> FAIL, 28 GB -> OK, None -> OK.
# The OOM defense does NOT depend on this: a memory bomb in the child is killed by
# the OS OOM-killer (the bloated child, not the parent worker, is the target) ->
# the parent sees the dead child -> parse_crash -> worker survives + circuit
# breaker. Subprocess isolation + per-doc timeout are the real bounds. A proper
# RESIDENT-memory cap (cgroup memory.max, not virtual RLIMIT_AS) is the right
# future enhancement if a hard in-child bound is wanted.
PARSE_CHILD_RLIMIT_AS_BYTES = None

# How often the parent polls child liveness while awaiting a result — bounds how
# fast a segfault (child dies without sending) is detected (vs waiting full T).
PARSE_CHILD_POLL_SECONDS = 1.0
# Grace between terminate() and kill() when reaping a hung child.
PARSE_CHILD_KILL_GRACE_SECONDS = 5.0

# Circuit breaker — abort the run on repeated CHILD DEATHS (crash/timeout/oom),
# NOT on per-doc data errors (parse_failed/parse_malformed leave the child alive).
# Consecutive catches a sustained outage; the windowed rate catches an
# interleaved [poison, valid, poison, ...] stream that resets a consecutive-only
# counter forever (red-team HIGH — Gemini). Mirrors 0056's consecutive logic.
PARSE_BREAKER_CONSECUTIVE = 5
PARSE_BREAKER_WINDOW = 100
PARSE_BREAKER_WINDOW_MAX = 10


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
