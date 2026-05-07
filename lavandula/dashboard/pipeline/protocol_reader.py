"""
Protocol reader for the orchestrator's fd 3 pipe.

When spawning a protocol v1 stage, the orchestrator creates a pipe,
passes the write end as fd 3 to the subprocess, and reads structured
events from the read end in a background thread.

For protocol v0 stages: no pipe is created. Exit code + log file size only.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable

from .summary_parser import parse_error_line, parse_progress_line, parse_summary_line

logger = logging.getLogger("pipeline.protocol_reader")


class ProtocolReader:
    """Reads structured events from a pipe (fd 3 of a subprocess).

    Runs a background thread that continuously drains the pipe and
    dispatches parsed events via callbacks.
    """

    def __init__(
        self,
        read_fd: int,
        on_progress: Callable[[dict], None] | None = None,
        on_error: Callable[[dict], None] | None = None,
        on_warning: Callable[[dict], None] | None = None,
        on_summary: Callable[[dict], None] | None = None,
    ):
        self._read_fd = read_fd
        self._on_progress = on_progress
        self._on_error = on_error
        self._on_warning = on_warning
        self._on_summary = on_summary
        self._thread: threading.Thread | None = None
        self._stopped = False
        self.last_summary: dict | None = None
        self.errors: list[dict] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _read_loop(self) -> None:
        try:
            with os.fdopen(self._read_fd, "r") as f:
                for line in f:
                    if self._stopped:
                        break
                    self._dispatch_line(line.rstrip("\n"))
        except (OSError, ValueError):
            pass

    def _dispatch_line(self, line: str) -> None:
        if not line:
            return

        if line.startswith("PROGRESS:"):
            parsed = parse_progress_line(line)
            if parsed and self._on_progress:
                self._on_progress(parsed)
        elif line.startswith("ERROR:"):
            parsed = parse_error_line(line)
            if parsed:
                self.errors.append(parsed)
                if self._on_error:
                    self._on_error(parsed)
        elif line.startswith("WARNING:"):
            parsed = parse_error_line("ERROR:" + line[len("WARNING:"):])
            if parsed:
                warning = {"warning_class": parsed.get("error_class", ""), "detail": parsed.get("error_detail", "")}
                if self._on_warning:
                    self._on_warning(warning)
        elif line.startswith("SUMMARY:"):
            parsed = parse_summary_line(line)
            if parsed:
                self.last_summary = parsed
                if self._on_summary:
                    self._on_summary(parsed)
        else:
            logger.debug("Ignoring unrecognized protocol line: %s", line[:100])


def create_protocol_pipe() -> tuple[int, int]:
    """Create a pipe for fd 3 protocol communication.

    Returns (read_fd, write_fd). The write_fd should be passed to
    the subprocess as fd 3. The read_fd is consumed by ProtocolReader.
    """
    return os.pipe()


def setup_fd3_for_subprocess(write_fd: int) -> dict:
    """Return kwargs for subprocess.Popen to pass write_fd as fd 3.

    The caller must:
    1. Create the pipe: read_fd, write_fd = create_protocol_pipe()
    2. Pass write_fd via pass_fds
    3. Use preexec_fn to dup2 write_fd to 3 (if write_fd != 3)
    4. Close write_fd in the parent after spawn
    5. Start ProtocolReader on read_fd
    """
    def _preexec():
        if write_fd != 3:
            os.dup2(write_fd, 3)
            os.close(write_fd)

    return {
        "pass_fds": (write_fd,) if write_fd == 3 else (write_fd,),
        "preexec_fn": _preexec,
    }
