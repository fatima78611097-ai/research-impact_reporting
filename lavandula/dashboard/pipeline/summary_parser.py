"""
Summary parser for pipeline stage completion.

Parses the structured DONE lines from each stage's log output into
normalized summary dicts suitable for JobEvent payloads.

For protocol v0 stages: extracts only exit code, duration, log file size.
For protocol v1 stages: reads SUMMARY line from fd 3 (handled elsewhere).
"""
from __future__ import annotations

import re
from typing import Any


_DONE_PATTERN = re.compile(
    r"={3,}\s*(.*?)\s*DONE\s*={3,}"
)

_KV_PATTERN = re.compile(r"(\w+)\s*[=:]\s*([^\s,]+)")


def parse_done_line(line: str) -> dict[str, Any]:
    """Parse a ===...DONE=== line into a summary dict."""
    match = _DONE_PATTERN.search(line)
    if not match:
        return {}
    content = match.group(1).strip()
    if not content:
        return {}
    result = {}
    for kv_match in _KV_PATTERN.finditer(content):
        key = kv_match.group(1).lower()
        value = kv_match.group(2)
        if value.isdigit():
            result[key] = int(value)
        elif _is_float(value):
            result[key] = float(value)
        else:
            result[key] = value
    return result


def parse_summary_line(line: str) -> dict[str, Any]:
    """Parse a SUMMARY: key=value line (protocol v1 format)."""
    if not line.startswith("SUMMARY:"):
        return {}
    content = line[len("SUMMARY:"):].strip()
    result = {}
    for kv_match in _KV_PATTERN.finditer(content):
        key = kv_match.group(1).lower()
        value = kv_match.group(2)
        if value.isdigit():
            result[key] = int(value)
        elif _is_float(value):
            result[key] = float(value)
        else:
            result[key] = value
    return result


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """Parse a PROGRESS: key=value line (protocol v1 format)."""
    if not line.startswith("PROGRESS:"):
        return None
    content = line[len("PROGRESS:"):].strip()
    result = {}
    for kv_match in _KV_PATTERN.finditer(content):
        key = kv_match.group(1).lower()
        value = kv_match.group(2)
        if value.isdigit():
            result[key] = int(value)
        elif _is_float(value):
            result[key] = float(value)
        else:
            result[key] = value
    return result if result else None


def parse_error_line(line: str) -> dict[str, str] | None:
    """Parse an ERROR: class=NAME detail=TEXT line (protocol v1 format)."""
    if not line.startswith("ERROR:"):
        return None
    content = line[len("ERROR:"):].strip()
    result = {}
    class_match = re.search(r"class=(\S+)", content)
    if class_match:
        result["error_class"] = class_match.group(1)
    detail_match = re.search(r"detail=(.+)$", content)
    if detail_match:
        result["error_detail"] = detail_match.group(1).strip()
    return result if result else None


def build_v0_summary(exit_code: int, duration_s: float | None, log_file: str | None, log_size_bytes: int | None) -> dict:
    """Build minimal summary for v0 (legacy) stages."""
    summary = {
        "exit_code": exit_code,
        "protocol": "v0",
    }
    if duration_s is not None:
        summary["duration_s"] = round(duration_s)
    if log_file:
        summary["log_file"] = log_file
    if log_size_bytes is not None:
        summary["log_size_bytes"] = log_size_bytes
    return summary


def _is_float(s: str) -> bool:
    try:
        float(s)
        return "." in s
    except (ValueError, TypeError):
        return False
