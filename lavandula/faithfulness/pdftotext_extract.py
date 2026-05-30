"""pdftotext extraction module (Spec 0060 Phase 1).

Pure module — runs /usr/bin/pdftotext as a subprocess, captures output with
bounded memory, strips NUL bytes, classifies scanned vs text-native.
No DB dependency.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

PDFTOTEXT_BIN = "/usr/bin/pdftotext"
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB
TIMEOUT_SECONDS = 30
SCANNED_CHAR_THRESHOLD = 50

_cached_version: str | None = None


@dataclass(frozen=True)
class ExtractResult:
    text: str
    version: str
    char_count: int
    is_scanned: bool
    failed: bool = False
    error: str | None = None


def get_pdftotext_version() -> str:
    global _cached_version
    if _cached_version is not None:
        return _cached_version

    if not os.path.isfile(PDFTOTEXT_BIN):
        raise FileNotFoundError(
            f"{PDFTOTEXT_BIN} not found — install poppler-utils"
        )

    result = subprocess.run(
        [PDFTOTEXT_BIN, "-v"],
        capture_output=True, text=True, timeout=10,
    )
    version_text = (result.stderr or result.stdout).strip()
    for line in version_text.splitlines():
        if "version" in line.lower():
            _cached_version = line.strip()
            return _cached_version

    _cached_version = version_text.splitlines()[0] if version_text else "unknown"
    return _cached_version


def extract_text(pdf_input: bytes) -> ExtractResult:
    """Extract text from PDF bytes via pdftotext.

    Uses Popen with bounded stdout read to enforce the 10MB output cap.
    Input is fed via stdin (no temp file needed).
    """
    version = get_pdftotext_version()

    try:
        proc = subprocess.Popen(
            [PDFTOTEXT_BIN, "-layout", "-", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "TMPDIR": _ensure_tmpdir()},
        )
    except OSError as exc:
        return ExtractResult(
            text="", version=version, char_count=0,
            is_scanned=False, failed=True, error=str(exc),
        )

    try:
        raw_output, stderr_out = proc.communicate(
            input=pdf_input, timeout=TIMEOUT_SECONDS,
        )

        if stderr_out:
            log.debug("pdftotext stderr: %s", stderr_out[:500])

        if len(raw_output) > MAX_OUTPUT_BYTES:
            log.warning("pdftotext output exceeded %d bytes", MAX_OUTPUT_BYTES)
            return ExtractResult(
                text="", version=version, char_count=0,
                is_scanned=False, failed=True,
                error="output_exceeded_10mb",
            )

    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return ExtractResult(
            text="", version=version, char_count=0,
            is_scanned=False, failed=True, error="timeout",
        )
    except OSError as exc:
        proc.kill()
        proc.communicate()
        return ExtractResult(
            text="", version=version, char_count=0,
            is_scanned=False, failed=True, error=str(exc),
        )

    text = raw_output.decode("utf-8", errors="replace")
    text = text.replace("\x00", "")
    text_stripped = text.strip()

    is_scanned = len(text_stripped) < SCANNED_CHAR_THRESHOLD

    return ExtractResult(
        text=text,
        version=version,
        char_count=len(text_stripped),
        is_scanned=is_scanned,
    )


def _ensure_tmpdir() -> str:
    d = "/tmp/pdftotext-0060"
    os.makedirs(d, exist_ok=True)
    return d
