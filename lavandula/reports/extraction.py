"""Multi-page PDF text extraction with sandboxed subprocess (Spec 0035).

Extracts pages 1-5 from a PDF in a resource-limited subprocess.
Each extraction runs in its own multiprocessing.Process with:
  - 1 GB RLIMIT_AS (memory cap)
  - 30s RLIMIT_CPU (CPU time cap)
  - 64 RLIMIT_NOFILE (fd cap)
  - Credential env vars stripped

Communication uses a Pipe (not Queue) to avoid thread requirements
in the sandboxed child process.
"""
from __future__ import annotations

import io
import multiprocessing
import multiprocessing.connection
import os
import pickle
import resource
from dataclasses import dataclass

_MAX_PAGES = 5
_MAX_TEXT_LEN = 16_000
_PARENT_MAX_PAYLOAD = 32_000
_WALL_TIMEOUT_SEC = 30

_CREDENTIAL_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "DATABASE_URL",
)

_RLIMIT_AS_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB
_RLIMIT_CPU_SEC = 30
_RLIMIT_NOFILE = 64
_RLIMIT_FSIZE = 0  # no file writes
_RLIMIT_NPROC = 0  # no child processes or new threads


@dataclass
class ExtractionResult:
    pages_text: str
    pages_extracted: int
    total_pages: int | None
    extraction_method: str
    text_length: int


def _set_resource_limits():
    resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (_RLIMIT_CPU_SEC, _RLIMIT_CPU_SEC))
    resource.setrlimit(resource.RLIMIT_NOFILE, (_RLIMIT_NOFILE, _RLIMIT_NOFILE))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_RLIMIT_FSIZE, _RLIMIT_FSIZE))
    resource.setrlimit(resource.RLIMIT_NPROC, (_RLIMIT_NPROC, _RLIMIT_NPROC))


def _strip_credentials():
    for var in _CREDENTIAL_ENV_VARS:
        os.environ.pop(var, None)


def _make_result(pages_text="", pages_extracted=0, total_pages=None,
                 extraction_method="pypdf", text_length=0):
    return {
        "pages_text": pages_text,
        "pages_extracted": pages_extracted,
        "total_pages": total_pages,
        "extraction_method": extraction_method,
        "text_length": text_length,
    }


def _worker(pdf_bytes: bytes, max_pages: int, conn):
    """Runs inside the sandboxed subprocess. Sends result dict via Pipe."""
    try:
        _strip_credentials()
        _set_resource_limits()
    except Exception as exc:
        conn.send(_make_result(
            extraction_method=f"failed:sandbox_setup:{type(exc).__name__}",
        ))
        conn.close()
        return

    try:
        from pypdf import PdfReader
    except ImportError:
        conn.send(_make_result(extraction_method="failed:no_pypdf"))
        conn.close()
        return

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        exc_name = type(exc).__name__.lower()
        if "encrypt" in exc_name or "password" in str(exc).lower():
            method = "failed:encrypted"
        else:
            method = "failed:corrupt"
        conn.send(_make_result(extraction_method=method))
        conn.close()
        return

    if reader.is_encrypted:
        tp = len(reader.pages) if reader.pages else None
        conn.send(_make_result(total_pages=tp, extraction_method="failed:encrypted"))
        conn.close()
        return

    total_pages = len(reader.pages)
    pages_to_extract = min(max_pages, total_pages)
    page_texts = []
    pages_extracted = 0

    for i in range(pages_to_extract):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""
        page_texts.append(f"\n--- PAGE {i + 1} ---\n{text}")
        pages_extracted += 1

    combined = "".join(page_texts).strip()

    stripped = combined
    for marker_prefix in ("--- PAGE 1 ---", "--- PAGE 2 ---", "--- PAGE 3 ---",
                          "--- PAGE 4 ---", "--- PAGE 5 ---"):
        stripped = stripped.replace(marker_prefix, "")
    if not stripped.strip():
        conn.send(_make_result(
            pages_extracted=pages_extracted, total_pages=total_pages,
            extraction_method="pypdf:empty",
        ))
        conn.close()
        return

    if len(combined) > _MAX_TEXT_LEN:
        combined = combined[:_MAX_TEXT_LEN]

    conn.send(_make_result(
        pages_text=combined, pages_extracted=pages_extracted,
        total_pages=total_pages, extraction_method="pypdf",
        text_length=len(combined),
    ))
    conn.close()


def extract_pages(pdf_bytes: bytes, max_pages: int = _MAX_PAGES) -> ExtractionResult:
    """Extract text from up to max_pages of a PDF in a sandboxed subprocess.

    Returns ExtractionResult with extraction_method indicating success or
    failure mode (failed:timeout, failed:oom, failed:encrypted, etc.).
    """
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    proc = multiprocessing.Process(target=_worker, args=(pdf_bytes, max_pages, child_conn))
    proc.start()
    child_conn.close()  # parent doesn't write to this end

    proc.join(timeout=_WALL_TIMEOUT_SEC)

    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)
        parent_conn.close()
        return ExtractionResult(
            pages_text="", pages_extracted=0, total_pages=None,
            extraction_method="failed:timeout", text_length=0,
        )

    if proc.exitcode != 0:
        parent_conn.close()
        method = "failed:oom" if proc.exitcode == -9 else f"failed:exit_{proc.exitcode}"
        return ExtractionResult(
            pages_text="", pages_extracted=0, total_pages=None,
            extraction_method=method, text_length=0,
        )

    try:
        if not parent_conn.poll(timeout=1):
            parent_conn.close()
            return ExtractionResult(
                pages_text="", pages_extracted=0, total_pages=None,
                extraction_method="failed:no_result", text_length=0,
            )
        data = parent_conn.recv()
    except Exception:
        parent_conn.close()
        return ExtractionResult(
            pages_text="", pages_extracted=0, total_pages=None,
            extraction_method="failed:no_result", text_length=0,
        )
    finally:
        parent_conn.close()

    if not isinstance(data, dict):
        return ExtractionResult(
            pages_text="", pages_extracted=0, total_pages=None,
            extraction_method="failed:protocol_violation", text_length=0,
        )

    pages_text = data.get("pages_text", "")
    if len(pages_text) > _PARENT_MAX_PAYLOAD:
        return ExtractionResult(
            pages_text="", pages_extracted=0,
            total_pages=data.get("total_pages"),
            extraction_method="failed:protocol_violation", text_length=0,
        )

    return ExtractionResult(
        pages_text=pages_text,
        pages_extracted=data.get("pages_extracted", 0),
        total_pages=data.get("total_pages"),
        extraction_method=data.get("extraction_method", "pypdf"),
        text_length=data.get("text_length", len(pages_text)),
    )


__all__ = ["ExtractionResult", "extract_pages"]
