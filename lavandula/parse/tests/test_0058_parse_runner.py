"""Unit tests for Spec 0058 Phase 2 — subprocess parse runner (§3.3).

CI tier: the PURE pieces (validate_payload, CircuitBreaker, _options_key) and
the parent control flow exercised with a FAKE multiprocessing context — no real
subprocess, no docling, no GPU. The child loop + real convert/extract are the
integration tier (operator-run on GPU).
"""
from __future__ import annotations

import json
import queue

import pytest

from lavandula.parse.chunking import ParseOptions
from lavandula.parse.parse_runner import (
    CircuitBreaker,
    DoclingParseCrash,
    DoclingParseMalformed,
    DoclingParseOOM,
    DoclingParseTimeout,
    ParseChildError,
    PersistentParseRunner,
    _options_key,
    validate_payload,
)


# ---------------------------------------------------------------------------
# Fixtures: a well-formed payload + fake multiprocessing primitives
# ---------------------------------------------------------------------------
def _good_section(i=0):
    return {
        "section_index": i, "heading": "H", "heading_level": 1,
        "body_text": "body", "char_count": 4, "page_start": 1,
        "page_end": 1, "parent_headings": ["H"],
    }


def _good_table(i=0):
    return {
        "table_index": i, "section_index": 0, "page_number": 1, "caption": None,
        "row_count": 1, "col_count": 2, "data_json": [["a", "b"]], "markdown": "| a | b |",
    }


def _good_payload():
    return {
        "sections": [_good_section()],
        "tables": [_good_table()],
        "metadata": {"page_count": 1, "figure_count": 0, "metadata": {}},
    }


_EMPTY = object()  # sentinel: this get() should raise queue.Empty


class FakeQueue:
    def __init__(self):
        self.items = []        # task_q: records what the parent put
        self.responses = []    # result_q: scripted get() outcomes

    def put(self, item):
        self.items.append(item)

    def get(self, timeout=None):
        if not self.responses:
            raise queue.Empty
        r = self.responses.pop(0)
        if r is _EMPTY:
            raise queue.Empty
        return r


class FakeProcess:
    def __init__(self, alive_script=None, exitcode=None):
        self._alive = True
        self._script = list(alive_script) if alive_script is not None else None
        self.exitcode = exitcode
        self.started = False

    def start(self):
        self.started = True
        self._alive = True

    def is_alive(self):
        if self._script:
            return self._script.pop(0)
        return self._alive

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False

    def join(self, timeout=None):
        self._alive = False


class FakeContext:
    def __init__(self, process_factory=None):
        self.queues = []
        self.processes = []
        self._factory = process_factory

    def Queue(self):
        q = FakeQueue()
        self.queues.append(q)
        return q

    def Process(self, target, args, daemon=None):
        p = self._factory() if self._factory else FakeProcess()
        self.processes.append(p)
        return p


def _runner(ctx, *, timeout=10.0, clock=None):
    return PersistentParseRunner(
        context=ctx, timeout=timeout, rlimit_as=None, poll=1.0,
        clock=clock or (lambda: 0.0),
    )


# ---------------------------------------------------------------------------
# validate_payload (pure)
# ---------------------------------------------------------------------------
class TestValidatePayload:
    def test_accepts_well_formed(self):
        assert validate_payload(_good_payload()) == _good_payload()

    def test_accepts_empty_lists(self):
        p = {"sections": [], "tables": [], "metadata": {"page_count": 0, "figure_count": 0}}
        assert validate_payload(p) == p

    def test_rejects_non_dict(self):
        with pytest.raises(DoclingParseMalformed):
            validate_payload([1, 2, 3])

    def test_rejects_missing_top_key(self):
        p = _good_payload()
        del p["tables"]
        with pytest.raises(DoclingParseMalformed):
            validate_payload(p)

    def test_rejects_wrong_top_type(self):
        p = _good_payload()
        p["sections"] = {"not": "a list"}
        with pytest.raises(DoclingParseMalformed):
            validate_payload(p)

    def test_rejects_section_missing_keys(self):
        p = _good_payload()
        p["sections"] = [{"section_index": 0}]
        with pytest.raises(DoclingParseMalformed):
            validate_payload(p)

    def test_rejects_table_missing_keys(self):
        p = _good_payload()
        p["tables"] = [{"table_index": 0}]
        with pytest.raises(DoclingParseMalformed):
            validate_payload(p)

    def test_rejects_metadata_missing_counts(self):
        p = _good_payload()
        p["metadata"] = {"something": "else"}
        with pytest.raises(DoclingParseMalformed):
            validate_payload(p)


# ---------------------------------------------------------------------------
# CircuitBreaker (pure)
# ---------------------------------------------------------------------------
class TestCircuitBreaker:
    def test_consecutive_trips(self):
        cb = CircuitBreaker(consecutive_max=3, window=100, window_max=50)
        cb.record_failure()
        cb.record_failure()
        assert not cb.tripped()
        cb.record_failure()
        assert cb.tripped()
        assert "consecutive" in cb.reason()

    def test_success_resets_consecutive(self):
        cb = CircuitBreaker(consecutive_max=3, window=100, window_max=50)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert not cb.tripped()  # never 3 in a row

    def test_windowed_catches_interleaved(self):
        # Interleaved poison/valid never hits 3-consecutive but exceeds the
        # windowed rate (red-team HIGH — Gemini).
        cb = CircuitBreaker(consecutive_max=3, window=10, window_max=4)
        for _ in range(5):
            cb.record_failure()
            cb.record_success()
        assert cb.tripped()
        assert "windowed" in cb.reason()

    def test_clean_run_never_trips(self):
        cb = CircuitBreaker(consecutive_max=3, window=10, window_max=4)
        for _ in range(50):
            cb.record_success()
        assert not cb.tripped()
        assert cb.reason() is None


# ---------------------------------------------------------------------------
# _options_key
# ---------------------------------------------------------------------------
class TestOptionsKey:
    def test_distinguishes_ocr(self):
        a = _options_key(ParseOptions(do_ocr=True))
        b = _options_key(ParseOptions(do_ocr=False))
        assert a != b

    def test_same_options_same_key(self):
        a = _options_key(ParseOptions(do_ocr=True, images_scale=1.5))
        b = _options_key(ParseOptions(do_ocr=True, images_scale=1.5))
        assert a == b
        assert isinstance(a, tuple)


# ---------------------------------------------------------------------------
# PersistentParseRunner parent control flow (fake context)
# ---------------------------------------------------------------------------
class TestRunnerControlFlow:
    def _opts(self):
        return ParseOptions(do_ocr=True)

    def test_ok_returns_validated_dicts(self):
        ctx = FakeContext()
        runner = _runner(ctx)
        runner._ensure_child()
        runner._result_q.responses = [(1, json.dumps({"ok": True, **_good_payload()}))]
        out = runner.parse("/tmp/x.pdf", self._opts())
        assert out["sections"] == [_good_section()]
        assert out["metadata"]["page_count"] == 1
        assert runner.respawns == 0
        # the request crossed as plain data (path + options dict)
        assert runner._task_q.items[0][1] == "/tmp/x.pdf"
        assert isinstance(runner._task_q.items[0][2], dict)

    def test_oom_marker_raises_and_respawns(self):
        ctx = FakeContext()
        runner = _runner(ctx)
        runner._ensure_child()
        runner._result_q.responses = [(1, json.dumps({"ok": False, "error": "parse_oom"}))]
        with pytest.raises(DoclingParseOOM):
            runner.parse("/tmp/x.pdf", self._opts())
        assert runner.respawns == 1  # killed + respawned for clean GPU state

    def test_parse_failed_marker_raises_without_respawn(self):
        ctx = FakeContext()
        runner = _runner(ctx)
        runner._ensure_child()
        runner._result_q.responses = [
            (1, json.dumps({"ok": False, "error": "parse_failed", "detail": "ValueError: bad"}))
        ]
        with pytest.raises(ParseChildError):
            runner.parse("/tmp/x.pdf", self._opts())
        assert runner.respawns == 0  # child healthy — a data error, not a death

    def test_malformed_payload_raises(self):
        ctx = FakeContext()
        runner = _runner(ctx)
        runner._ensure_child()
        bad = {"ok": True, "sections": [], "tables": [], "metadata": {}}
        runner._result_q.responses = [(1, json.dumps(bad))]
        with pytest.raises(DoclingParseMalformed):
            runner.parse("/tmp/x.pdf", self._opts())

    def test_timeout_kills_and_respawns(self):
        # Child stays alive, never replies; the stepping clock crosses the deadline.
        steps = iter([0.0, 4.0, 8.0, 12.0, 16.0, 20.0])
        ctx = FakeContext()
        runner = _runner(ctx, timeout=10.0, clock=lambda: next(steps))
        runner._ensure_child()
        runner._result_q.responses = [_EMPTY, _EMPTY]
        with pytest.raises(DoclingParseTimeout):
            runner.parse("/tmp/x.pdf", self._opts())
        assert runner.respawns == 1

    def test_crash_detected_when_child_dies(self):
        # is_alive: True at _ensure_child, then False after the empty get -> crash.
        ctx = FakeContext(process_factory=lambda: FakeProcess(
            alive_script=[True, False, False], exitcode=-11))
        runner = _runner(ctx)
        runner._ensure_child()
        runner._result_q.responses = [_EMPTY]
        with pytest.raises(DoclingParseCrash):
            runner.parse("/tmp/x.pdf", self._opts())
        assert runner.respawns == 1

    def test_stale_reply_is_ignored(self):
        ctx = FakeContext()
        runner = _runner(ctx)
        runner._ensure_child()
        # A leftover reply from a prior (reaped) doc, then the real one.
        runner._result_q.responses = [
            (99, json.dumps({"ok": True, **_good_payload()})),
            (1, json.dumps({"ok": True, **_good_payload()})),
        ]
        out = runner.parse("/tmp/x.pdf", self._opts())
        assert out["metadata"]["page_count"] == 1

    def test_error_codes_map_to_outcomes(self):
        assert DoclingParseTimeout("x").code == "parse_timeout"
        assert DoclingParseCrash("x").code == "parse_crash"
        assert DoclingParseOOM("x").code == "parse_oom"
        assert DoclingParseMalformed("x").code == "parse_malformed"
