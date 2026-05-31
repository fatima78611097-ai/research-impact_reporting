"""Spec 0058 §3.3 — subprocess isolation for Docling parsing.

The Phase-0 spike (locard/spikes/0058/RESULTS.md) REJECTED the native
``document_timeout`` path: the poison doc ``e038a9e75317ff86`` **segfaults**
Docling's native ``pdf_parsers.so`` (exit 139), which no in-process mechanism
(``document_timeout``, signal, or Python exception) can catch — it takes the
whole worker down. ``document_timeout`` is also only a soft between-stages check
(a 24-page doc ran 75.5s under a 60s limit). A persistent child process is the
ONLY thing that contains all three failure modes:

  * **segfault / native abort** → child dies, parent sees a non-zero exit →
    ``parse_crash``; the worker survives.
  * **hang** → parent's hard ``join(timeout)`` → ``terminate()`` → ``kill()`` →
    ``parse_timeout``.
  * **OOM / image bomb** → child ``RLIMIT_AS`` makes the allocation fail →
    ``parse_oom``.

Design (mirrors precedents: ``reports/fetch_pdf.py`` spawn+terminate+kill,
``faithfulness/pdftotext_extract.py`` subprocess-timeout):

  * One **persistent** child per worker (``spawn`` context), model loaded once
    and reused (a per-doc child reloading the model — minutes — is forbidden by
    the spec). The child caches a converter per distinct options tuple, so the
    common case (normal docs, one config) stays warm; the rare downgraded /
    OCR-skip configs build their own converter once.
  * The child runs ``convert`` AND ``extract_sections``/``extract_tables``/
    ``get_document_metadata`` INSIDE itself and returns only small, already
    flattened, **JSON**-encoded dicts — the raw ``DoclingDocument`` never crosses
    the process boundary (it is large and may not pickle cleanly), and JSON
    removes pickle-opcode attack surface on PDF-derived data (red-team — Gemini).
  * The parent validates the decoded payload against an explicit schema BEFORE
    DB insert; a malformed/partial payload is ``parse_malformed`` (red-team —
    Codex), not a silent field loss.
  * The PARENT owns a dedicated ``TMPDIR`` and wipes it on every child exit/kill
    (SIGKILL skips the child's own cleanup, so child temp files would otherwise
    leak — red-team — Gemini).
  * A windowed + consecutive ``CircuitBreaker`` (used by the worker) aborts the
    run on repeated child deaths.

Only ``convert``/``extract`` and the spawn machinery touch docling/GPU, so the
pure pieces (``validate_payload``, ``CircuitBreaker``) and the parent control
flow (with an injected fake context) are unit-testable off-GPU.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import queue
import resource
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from lavandula.parse import config
from lavandula.parse import chunking
from lavandula.parse.chunking import ParseOptions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed errors — each maps to a distinct PermanentError label in the worker.
# ---------------------------------------------------------------------------
class ParseChildError(Exception):
    """Base for subprocess-parse failures. ``code`` is the parse_outcome error."""

    code = "parse_error"


class DoclingParseTimeout(ParseChildError):
    """Child did not return within the hard timeout and was killed."""

    code = "parse_timeout"


class DoclingParseCrash(ParseChildError):
    """Child died (segfault / native abort) before returning a result."""

    code = "parse_crash"


class DoclingParseOOM(ParseChildError):
    """Child hit its RLIMIT_AS address-space cap (OOM / image bomb)."""

    code = "parse_oom"


class DoclingParseMalformed(ParseChildError):
    """Child returned a payload that failed schema validation."""

    code = "parse_malformed"


# Result-marker strings the child sends; mapped to the typed errors above.
_CHILD_OOM = "parse_oom"
_CHILD_FAILED = "parse_failed"


# ---------------------------------------------------------------------------
# Payload schema validation (pure — red-team — Codex)
# ---------------------------------------------------------------------------
def validate_payload(payload: Any) -> dict:
    """Validate the child's decoded payload before it reaches db.insert_document.

    Required shape: ``{"sections": [..], "tables": [..], "metadata": {..}}`` with
    the per-row keys db.insert_document consumes. Raises DoclingParseMalformed on
    any structural problem so a partial/garbled payload becomes a clean error,
    not a silently mis-inserted row.
    """
    if not isinstance(payload, dict):
        raise DoclingParseMalformed(f"payload is {type(payload).__name__}, not dict")

    for key, typ in (("sections", list), ("tables", list), ("metadata", dict)):
        if key not in payload:
            raise DoclingParseMalformed(f"missing key {key!r}")
        if not isinstance(payload[key], typ):
            raise DoclingParseMalformed(f"{key} is {type(payload[key]).__name__}, not {typ.__name__}")

    _SECTION_KEYS = {
        "section_index", "heading", "heading_level", "body_text",
        "char_count", "page_start", "page_end", "parent_headings",
    }
    _TABLE_KEYS = {
        "table_index", "section_index", "page_number", "caption",
        "row_count", "col_count", "data_json", "markdown",
    }
    for i, sec in enumerate(payload["sections"]):
        if not isinstance(sec, dict):
            raise DoclingParseMalformed(f"sections[{i}] is not a dict")
        missing = _SECTION_KEYS - sec.keys()
        if missing:
            raise DoclingParseMalformed(f"sections[{i}] missing {sorted(missing)}")
    for i, tab in enumerate(payload["tables"]):
        if not isinstance(tab, dict):
            raise DoclingParseMalformed(f"tables[{i}] is not a dict")
        missing = _TABLE_KEYS - tab.keys()
        if missing:
            raise DoclingParseMalformed(f"tables[{i}] missing {sorted(missing)}")

    meta = payload["metadata"]
    if "page_count" not in meta or "figure_count" not in meta:
        raise DoclingParseMalformed("metadata missing page_count/figure_count")
    return payload


# ---------------------------------------------------------------------------
# Circuit breaker (pure — consecutive OR windowed; red-team HIGH — Gemini)
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """Trips on N CONSECUTIVE child deaths OR > M deaths within the last W docs.

    A respawn-then-success resets the consecutive counter, so a scattered cluster
    of poison docs does not fail an otherwise healthy run. The windowed rate
    catches an interleaved ``[poison, valid, poison, valid, ...]`` stream that
    would reset a consecutive-only counter forever and tie up the GPU. Only hard
    child deaths (crash/timeout/oom) count — per-doc data errors do not.
    """

    def __init__(
        self,
        *,
        consecutive_max: int = config.PARSE_BREAKER_CONSECUTIVE,
        window: int = config.PARSE_BREAKER_WINDOW,
        window_max: int = config.PARSE_BREAKER_WINDOW_MAX,
    ):
        self.consecutive_max = consecutive_max
        self.window = window
        self.window_max = window_max
        self.consecutive = 0
        self._recent: deque[int] = deque(maxlen=window)  # 1=death, 0=success
        self._reason: str | None = None

    def record_success(self) -> None:
        self.consecutive = 0
        self._recent.append(0)

    def record_failure(self) -> None:
        self.consecutive += 1
        self._recent.append(1)
        if self.consecutive >= self.consecutive_max:
            self._reason = (
                f"parse_breaker_consecutive:{self.consecutive}"
                f">={self.consecutive_max}"
            )
        elif sum(self._recent) > self.window_max:
            self._reason = (
                f"parse_breaker_windowed:{sum(self._recent)}"
                f">{self.window_max}_in_{len(self._recent)}"
            )

    def tripped(self) -> bool:
        return self._reason is not None

    def reason(self) -> str | None:
        return self._reason


# ---------------------------------------------------------------------------
# Child process — entry point + per-task work
# ---------------------------------------------------------------------------
def _apply_rlimit(rlimit_as: int | None) -> None:
    """Cap the child's address space so an OOM bomb is bounded at the OS level.

    CAVEAT: CUDA/torch reserve large *virtual* address space (not resident); an
    RLIMIT_AS that is too low can prevent CUDA from initialising at all. The
    Phase-6 smoke test MUST confirm the child inits CUDA and parses a normal doc
    under this cap; if not, raise the cap or set PARSE_CHILD_RLIMIT_AS_BYTES=None.
    """
    if rlimit_as is None:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        new_hard = rlimit_as if (hard == resource.RLIM_INFINITY or rlimit_as < hard) else hard
        resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, new_hard))
    except (ValueError, OSError) as exc:  # pragma: no cover - platform dependent
        logger.warning("could not set RLIMIT_AS=%s: %s", rlimit_as, exc)


def _options_key(opts: ParseOptions) -> tuple:
    """Hashable key for the child's converter cache (model-affecting fields)."""
    return (
        opts.do_ocr,
        opts.images_scale,
        opts.table_mode_fast,
        opts.do_cell_matching,
        opts.generate_page_images,
        opts.document_timeout,
    )


def _run_one_task(converters: dict, pdf_path: str, opts: ParseOptions) -> dict:
    """Convert + extract INSIDE the child; return JSON-safe flattened dicts.

    Reuses a cached converter for the same options tuple so warm docs incur no
    reload. Imports docling lazily via chunking.build_converter.
    """
    key = _options_key(opts)
    converter = converters.get(key)
    if converter is None:
        converter = chunking.build_converter(opts)
        converters[key] = converter

    result = converter.convert(pdf_path, max_num_pages=opts.max_num_pages)
    doc = result.document
    sections = chunking.extract_sections(doc)
    tables = chunking.extract_tables(doc, sections)
    metadata = chunking.get_document_metadata(doc)
    return {"sections": sections, "tables": tables, "metadata": metadata}


def _child_main(task_q, result_q, tmpdir: str, rlimit_as: int | None) -> None:  # pragma: no cover - runs in subprocess on GPU
    """Persistent child loop. One task at a time; model(s) loaded once and reused.

    Protocol: receives ``(req_id, pdf_path, opts_dict)`` (None = shutdown), replies
    ``(req_id, json_str)`` where the JSON is ``{"ok": True, "sections": ...}`` or
    ``{"ok": False, "error": "...", "detail": "..."}``. Result is JSON-encoded so
    the raw DoclingDocument never crosses the boundary and PDF-derived data
    carries no pickle opcodes.
    """
    _apply_rlimit(rlimit_as)
    if tmpdir:
        os.environ["TMPDIR"] = tmpdir
        tempfile.tempdir = tmpdir

    converters: dict = {}
    while True:
        msg = task_q.get()
        if msg is None:
            break
        req_id, pdf_path, opts_dict = msg
        try:
            opts = ParseOptions(**opts_dict)
            payload = _run_one_task(converters, pdf_path, opts)
            result_q.put((req_id, json.dumps({"ok": True, **payload})))
        except MemoryError:
            # RLIMIT_AS hit at the Python level — report cleanly; the parent will
            # kill+respawn for a clean GPU state.
            result_q.put((req_id, json.dumps({"ok": False, "error": _CHILD_OOM})))
        except BaseException as exc:  # noqa: BLE001 — never let the loop die silently
            result_q.put((req_id, json.dumps({
                "ok": False, "error": _CHILD_FAILED,
                "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
            })))


# ---------------------------------------------------------------------------
# Parent — persistent runner
# ---------------------------------------------------------------------------
class PersistentParseRunner:
    """Owns the persistent child, the per-doc timeout, and respawn-on-death.

    ``parse(pdf_path, options)`` returns the validated ``{sections, tables,
    metadata}`` dicts or raises one of the typed ParseChildError subclasses. The
    worker maps those to PermanentError and feeds child deaths to a CircuitBreaker.

    ``context`` and ``child_target`` are injectable so the parent control flow is
    unit-testable with a fake multiprocessing context (no real subprocess/GPU).
    """

    def __init__(
        self,
        *,
        timeout: float = config.PARSE_TIMEOUT_SECONDS,
        rlimit_as: int | None = config.PARSE_CHILD_RLIMIT_AS_BYTES,
        poll: float = config.PARSE_CHILD_POLL_SECONDS,
        kill_grace: float = config.PARSE_CHILD_KILL_GRACE_SECONDS,
        context: Any = None,
        child_target: Callable | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.timeout = timeout
        self.rlimit_as = rlimit_as
        self.poll = poll
        self.kill_grace = kill_grace
        self._clock = clock
        if context is None:  # default real spawn context
            import multiprocessing

            context = multiprocessing.get_context("spawn")
        self._ctx = context
        self._child_target = child_target or _child_main
        self._proc = None
        self._task_q = None
        self._result_q = None
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._req_id = 0
        self.respawns = 0

    # -- lifecycle -----------------------------------------------------------
    def _spawn(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="docling-child-")
        self._task_q = self._ctx.Queue()
        self._result_q = self._ctx.Queue()
        self._proc = self._ctx.Process(
            target=self._child_target,
            args=(self._task_q, self._result_q, self._tmpdir.name, self.rlimit_as),
            daemon=True,
        )
        self._proc.start()

    def _ensure_child(self) -> None:
        if self._proc is None or not self._proc.is_alive():
            self._spawn()

    def _wipe_tmpdir(self) -> None:
        # Parent owns cleanup: SIGKILL skips the child's atexit/__del__, so
        # child-created temp files would otherwise leak across many timeouts.
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._tmpdir = None

    def _reap(self) -> None:
        """Terminate→kill the current child and wipe its temp dir. Idempotent."""
        proc = self._proc
        if proc is not None and proc.is_alive():
            try:
                proc.terminate()
                proc.join(self.kill_grace)
                if proc.is_alive():
                    proc.kill()
                    proc.join(self.kill_grace)
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        self._wipe_tmpdir()

    def _kill_and_respawn(self) -> None:
        self._reap()
        self.respawns += 1
        self._spawn()

    def shutdown(self) -> None:
        """Graceful stop: ask the child to exit, then reap + wipe."""
        if self._proc is not None and self._proc.is_alive() and self._task_q is not None:
            try:
                self._task_q.put(None)
                self._proc.join(self.kill_grace)
            except Exception:  # noqa: BLE001
                pass
        self._reap()

    # -- per-doc parse -------------------------------------------------------
    def parse(self, pdf_path: str | Path, options: ParseOptions) -> dict:
        """Parse one doc in the child. Returns validated dicts or raises typed err."""
        self._ensure_child()
        self._req_id += 1
        req_id = self._req_id
        self._task_q.put((req_id, str(pdf_path), dataclasses.asdict(options)))
        payload = self._await_result(req_id)
        return validate_payload(payload)

    def _await_result(self, req_id: int) -> dict:
        deadline = self._clock() + self.timeout
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                # Hard timeout: the child is hung (or crashed without replying).
                self._kill_and_respawn()
                raise DoclingParseTimeout(f"convert exceeded {self.timeout}s")
            try:
                rid, raw = self._result_q.get(timeout=min(self.poll, remaining))
            except queue.Empty:
                if self._proc is None or not self._proc.is_alive():
                    exitcode = getattr(self._proc, "exitcode", None)
                    self._kill_and_respawn()
                    raise DoclingParseCrash(f"child died (exitcode={exitcode}) before reply")
                continue  # alive but still working — keep waiting
            if rid != req_id:
                continue  # stale reply from a prior (reaped) doc — ignore
            return self._interpret(raw)

    def _interpret(self, raw: str) -> dict:
        msg = json.loads(raw)
        if msg.get("ok"):
            return {
                "sections": msg.get("sections"),
                "tables": msg.get("tables"),
                "metadata": msg.get("metadata"),
            }
        error = msg.get("error")
        if error == _CHILD_OOM:
            self._kill_and_respawn()  # ensure clean GPU state after an OOM
            raise DoclingParseOOM("child hit RLIMIT_AS")
        # parse_failed: a Python-level docling error — child is still healthy,
        # so no respawn. Surfaces as a generic parse error in the worker.
        raise ParseChildError(msg.get("detail") or error or "parse_failed")
