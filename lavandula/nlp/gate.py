"""Marker-grounded publish/quarantine gate — the pure oracle (Spec 0069, Phase 1).

Productionizes the research `comp-metric-regression/gate.py`. Given a metric
(``value``, ``label``) and the markers it cited (``value_ref`` / ``subject_ref``)
plus the doc's recomputed ``idmap`` (from :mod:`lavandula.nlp.marker_render`), decide
PUBLISH vs QUARANTINE:

  - **value grounded** — the cited cell/line text actually expresses the value,
    robust to spelled-out numbers ("Five"=5), abbreviations ("$4.4 million"=4.4e6),
    rounding ("$6,841,065.77" vs 6,841,066), commas / $ / %. HARDENED for small
    integers (|v|<=20): exact standalone-token match only — no ±1 tolerance, no
    digit-substring fallback — so a "1" no longer matches any stray digit on the page.
  - **subject grounded** — cited text shares label words OR is co-located with the value.
  - **co-location** — same cell, same table row, or same page within bbox proximity:
    the anti-mispairing check.

A metric publishes only if value AND subject are grounded (and, for small ints, the
measurable-value classifier confirms the number measures a real quantity). Value-less
("qualitative") metrics can't be value-verified -> quarantine.

This module is the **regression-locked oracle** (spec §4 "ported verdict oracle"): it
reproduces the research verdicts exactly. Production-only policy (is-a-metric rules,
de-dup, mispair detect, stale-coords) lives in :mod:`lavandula.nlp.gate_policy` so its
regressions isolate from the oracle's.

Hardening over the research code (spec §5.2 / §8):
  - ``to_float`` rejects non-finite (``NaN`` / ``Infinity``) and out-of-bounds values
    *before* grounding (S1) — no malformed numeric reaches the DB or the JSON view.
  - all per-metric string work runs on length-bounded text (``MAX_GATE_TEXT``) with a
    per-metric wall-clock budget (S4 / ReDoS) — a metric exceeding it raises
    :class:`GateTimeout`, which the runner maps to ``quarantine/gate_timeout``.

Pure and deterministic: no DB, no network, no global state.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Callable

# --- Tunable bounds (spec §5.2 / §8) ---
# A finite, JSON-safe, schema-safe magnitude ceiling. Real nonprofit metrics are far
# below this; anything larger is a parse/overflow artifact -> value_out_of_bounds.
VALUE_ABS_MAX = 1e15
# Per-metric text length cap for all grounding/co-location string work. The idmap is
# already capped by marker_render.MAX_IDMAP_ITEMS; this bounds per-element text so a
# single crafted giant cell cannot blow up the linear scans (S4 / ReDoS guard).
MAX_GATE_TEXT = 20_000
# Default per-metric wall-clock budget (seconds). None disables the check.
DEFAULT_TIME_BUDGET_S = 2.0

SMALL_INT_MAX = 20  # values <=20 ground trivially; treated specially (see value_grounded / verdict)


class GateTimeout(Exception):
    """A single metric's grounding exceeded its per-metric time budget (S4)."""


_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_SUF = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "thousand": 1e3, "k": 1e3}


def _raw_float(v: Any) -> tuple[bool, float | None]:
    """Parse ``v`` to a float WITHOUT bound checks. Returns ``(is_number, value)``.

    A real numeric (int/float, e.g. a DB ``REAL``) is taken directly so scientific
    notation isn't mangled by the string strip; a string is currency/comma-stripped.
    ``is_number`` is False only when ``v`` expresses no number at all.
    """
    if isinstance(v, bool):
        return False, None
    if isinstance(v, (int, float)):
        return True, float(v)
    try:
        return True, float(re.sub(r"[^0-9.\-]", "", str(v)))
    except (TypeError, ValueError):
        return False, None


def to_float(v: Any) -> float | None:
    """Parse a model-emitted value to a finite, in-bounds float, else ``None``.

    Strips currency / commas / units, then rejects non-finite (``NaN``/``Inf``) and
    out-of-bounds magnitudes (S1). A value that fails here can never ground and never
    reaches the DB as a numeric.
    """
    is_num, f = _raw_float(v)
    if not is_num or f is None:
        return None
    if not math.isfinite(f) or abs(f) > VALUE_ABS_MAX:
        return None
    return f


def value_in_bounds(v: Any) -> bool:
    """True iff ``v`` parses to a finite, in-bounds number. Distinguishes a genuinely
    out-of-bounds/non-finite value (which must quarantine ``value_out_of_bounds``) from
    a plain no-number value (``no_numeric_value``)."""
    is_num, f = _raw_float(v)
    if not is_num or f is None:
        return True  # not a number at all -> not an out-of-bounds case
    return math.isfinite(f) and abs(f) <= VALUE_ABS_MAX


def is_small_int(v: Any) -> bool:
    """A small integer value (|v| <= SMALL_INT_MAX). These ground against almost any
    text and are where non-measurements hide (event=1, multiplier=2, rank, identifier)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(f):
        return False
    return f == int(f) and abs(f) <= SMALL_INT_MAX


def _cap(text: Any) -> str:
    """Length-bound source text for the linear scans (S4)."""
    s = text or ""
    if not isinstance(s, str):
        s = str(s)
    return s[:MAX_GATE_TEXT]


def candidate_numbers(text: Any) -> set[float]:
    """All numeric values the text could express (digit, abbreviated, spelled-out)."""
    out: set[float] = set()
    low = _cap(text).lower()
    for m in re.finditer(r"([\d][\d,]*(?:\.\d+)?)[\s\-]*(million|billion|thousand|bn|m|b|k)?\b", low):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        out.add(val)
        if m.group(2):
            out.add(val * _SUF[m.group(2)])
    # parse-shattered numbers: digits split by WHITESPACE ONLY ("1 4, 884" -> 14884). Joining never
    # crosses letters, so "...11 individuals ... 121 tours" can NOT form 111 (the efd61fea trap).
    for m in re.finditer(r"\d(?:[\d,.]|\s)*\d", low):
        joined = re.sub(r"[\s,]", "", m.group(0))
        try:
            out.add(float(joined))
        except ValueError:
            pass
    toks = re.findall(r"[a-z]+", low)
    for i, w in enumerate(toks):
        base = _ONES.get(w, _TENS.get(w))
        if base is not None:
            out.add(float(base))
            if i + 1 < len(toks) and toks[i + 1] in _SCALES:
                out.add(float(base) * _SCALES[toks[i + 1]])
    return out


def value_grounded(metric_value: Any, value_ref_text: Any) -> bool | None:
    """True/False if the value is/ isn't expressed at the marker; ``None`` if the metric
    carries no numeric value at all (can't verify by value)."""
    v = to_float(metric_value)
    if v is None:
        return None  # qualitative / no (in-bounds) number -> can't verify by value
    cands = candidate_numbers(value_ref_text)
    if is_small_int(v):
        # HARDENED for small integers: exact standalone-token match only. NO +-1 tolerance floor
        # and NO digit-substring fallback -- otherwise a "1" matches any stray digit on the page
        # (a year, "$10,000", "Section 1"). candidate_numbers yields proper number tokens (incl.
        # spelled-out "one"=1), so a real "9 vans" / "one client" still grounds; "a food truck" (no
        # numeral) and "second highest" ("second" is not a number word) no longer do.
        return any(abs(c - v) < 1e-9 for c in cands)
    tol = max(1.0, 0.001 * abs(v))
    if any(abs(c - v) <= tol for c in cands):
        return True
    # Digit-substring fallback (formatting variants like "1.3M" vs 1,300,000): WITHIN one number
    # token only. Never across concatenated digits of the whole marker — "111" must not ground by
    # spanning "...11 ... 121..." (efd61fea: parse dropped a char, model hallucinated 111, and the
    # old all-digits concatenation "11121..." contained it).
    d = re.sub(r"\D", "", str(metric_value))
    if not d:
        return False
    for tok in re.findall(r"[\d][\d,.]*", _cap(value_ref_text)):
        if d in re.sub(r"\D", "", tok):
            return True
    return False


def _words(s: Any) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", _cap(s).lower()))


def _center(bbox: Any) -> float | None:
    if not bbox or "t" not in bbox or "b" not in bbox:
        return None
    return (bbox["t"] + bbox["b"]) / 2.0


def co_located(a: str | None, b: str | None, idmap: dict) -> bool:
    """True iff markers ``a`` and ``b`` are the same cell, same table row, or the same
    page within bbox proximity — the anti-mispairing co-location check."""
    ia, ib = idmap.get(a), idmap.get(b)
    if not ia or not ib:
        return False
    if a == b:
        return True
    if ia.get("row") is not None and ia.get("row") == ib.get("row") and ia.get("page") == ib.get("page"):
        return True
    if ia.get("page") is not None and ia.get("page") == ib.get("page"):
        ca, cb = _center(ia.get("bbox")), _center(ib.get("bbox"))
        if ca is not None and cb is not None:
            return abs(ca - cb) <= 60  # ~ a few lines apart on the page
        return True  # same page, no bbox -> weak accept
    return False


def subject_grounded(label: Any, subject_ref: str | None, value_ref: str | None, idmap: dict) -> bool:
    """True iff the subject marker shares distinctive words with the label OR is
    co-located with the value marker."""
    sr = idmap.get(subject_ref)
    if not sr:
        return False
    stext = (sr.get("text", "") or "") + " " + (sr.get("row_text", "") or "")
    if _words(label) & _words(stext):
        return True
    return co_located(value_ref, subject_ref, idmap)


def _now_check(clock: Callable[[], float], start: float, budget_s: float | None) -> None:
    if budget_s is not None and (clock() - start) > budget_s:
        raise GateTimeout()


def verdict(
    metric: dict,
    value_ref: str | None,
    subject_ref: str | None,
    idmap: dict,
    measured: bool | None = None,
    *,
    budget_s: float | None = DEFAULT_TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str]:
    """Return ``(decision, reason)`` — decision in ``{'publish', 'quarantine'}``.

    This is the pure oracle (spec §4 steps 2–4). The decision order is fixed and the
    FIRST failing check wins (single-valued reason). ``measured`` is the small-integer
    semantic-validity signal: for |value|<=20, the measurable-value classifier's answer
    to "does this number measure a real quantity?" — True=yes, False=no
    (event/multiplier/rank/identifier), None=not checked (e.g. large values).

    Raises :class:`GateTimeout` if the per-metric ``budget_s`` is exceeded (S4); the
    runner maps that to ``quarantine/gate_timeout``.
    """
    start = clock()
    vr = idmap.get(value_ref)
    mv = metric.get("metric_value")

    # Float finiteness + bounds BEFORE grounding (S1): a non-finite / overflow value is
    # a distinct quarantine reason and never reaches grounding or the DB.
    if not value_in_bounds(mv):
        return "quarantine", "value_out_of_bounds"

    _now_check(clock, start, budget_s)
    vg = value_grounded(mv, vr.get("text") if vr else "")
    if vg is None:
        return "quarantine", "no_numeric_value"
    if not vg:
        return "quarantine", "value_not_at_marker"

    _now_check(clock, start, budget_s)
    if not subject_grounded(metric.get("label"), subject_ref, value_ref, idmap):
        return "quarantine", "subject_not_grounded"

    # Small-integer semantic gate: a small int grounds trivially even when it is not a
    # measurement (event=1, "the only..."=1, multiplier "doubled"=2). For value<=20 require
    # the measurable-value classifier to confirm it measures a real quantity. Keeps real small
    # counts ("9 vans", "served 6 counties"); rejects the artifacts grounding cannot see.
    fv = to_float(mv)
    if fv is not None and is_small_int(fv) and measured is False:
        return "quarantine", "not_a_metric"

    # The publish bar: value grounded at its marker AND subject grounded at its marker.
    # (column_guard / standalone co-location intentionally NOT in the oracle — they live as
    # production-only mispair DETECT policy in gate_policy, never an auto-fix.)
    return "publish", "ok"
