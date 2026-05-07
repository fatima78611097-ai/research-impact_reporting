"""
Structured progress protocol for pipeline stages.

Stages emit structured events via file descriptor 3 (separate from
stdout/stderr). The orchestrator reads fd 3 for structured events
and captures stdout/stderr only for the raw log file.

This prevents untrusted content in stdout (org names, error traces)
from being parsed as structured events.

Usage in a pipeline stage:
    from lavandula.pipeline_protocol import emit_progress, emit_error, emit_summary

    emit_progress(current=500, total=3589, rate="437 orgs/hr")
    emit_error("flush_failure", "3 unresolved flush failures")
    emit_summary(duration_s=29376, records_processed=3537, records_failed=50)
"""
from __future__ import annotations

import os
import sys

_fd3 = None
_fd3_checked = False


def _get_fd3():
    global _fd3, _fd3_checked
    if _fd3_checked:
        return _fd3
    _fd3_checked = True
    try:
        _fd3 = os.fdopen(3, "w", buffering=1)  # line-buffered
    except OSError:
        _fd3 = None
    return _fd3


def emit_progress(current: int, total: int | None = None, **kwargs) -> None:
    """Emit a PROGRESS event on fd 3."""
    fd = _get_fd3()
    if fd is None:
        return
    parts = [f"current={current}"]
    if total is not None:
        parts.append(f"total={total}")
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    try:
        fd.write(f"PROGRESS: {' '.join(parts)}\n")
    except (OSError, ValueError):
        pass


def emit_error(error_class: str, detail: str) -> None:
    """Emit an ERROR event on fd 3."""
    fd = _get_fd3()
    if fd is None:
        return
    detail_safe = detail.replace("\n", " ").replace("\r", "")[:1000]
    try:
        fd.write(f"ERROR: class={error_class} detail={detail_safe}\n")
    except (OSError, ValueError):
        pass


def emit_warning(warning_class: str, detail: str) -> None:
    """Emit a WARNING event on fd 3."""
    fd = _get_fd3()
    if fd is None:
        return
    detail_safe = detail.replace("\n", " ").replace("\r", "")[:1000]
    try:
        fd.write(f"WARNING: class={warning_class} detail={detail_safe}\n")
    except (OSError, ValueError):
        pass


def emit_summary(**kwargs) -> None:
    """Emit a SUMMARY event on fd 3 (should be the last emission before exit)."""
    fd = _get_fd3()
    if fd is None:
        return
    parts = [f"{k}={v}" for k, v in kwargs.items()]
    try:
        fd.write(f"SUMMARY: {' '.join(parts)}\n")
        fd.flush()
    except (OSError, ValueError):
        pass


def reset():
    """Reset fd 3 state (for testing)."""
    global _fd3, _fd3_checked
    if _fd3 is not None:
        try:
            _fd3.close()
        except (OSError, ValueError):
            pass
    _fd3 = None
    _fd3_checked = False
