"""Hardened grounding gate for comp-metrics.

Given a metric (value, label) and the markers it cited (value_ref, subject_ref)
plus the id->item map, decide PUBLISH vs QUARANTINE:
  - value grounded: the cited cell/line text actually expresses the value, robust
    to spelled-out numbers ("Five"=5), abbreviations ("$4.4 million"=4.4e6),
    rounding ("$6,841,065.77" vs 6,841,066), commas/$/%.
  - subject grounded: cited text shares label words OR is co-located with the value.
  - co-location: same cell, same table row, or same page with bbox proximity —
    the anti-mispairing check.
A metric publishes only if value AND subject are grounded. Value-less ("qualitative")
metrics can't be value-verified -> quarantine (not a comp-metric of record).
"""
import re

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_SUF = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "thousand": 1e3, "k": 1e3}


def to_float(v):
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v)))
    except (TypeError, ValueError):
        return None


SMALL_INT_MAX = 20  # values <=20 ground trivially; treated specially (see value_grounded / verdict)


def is_small_int(v):
    """A small integer value (|v| <= SMALL_INT_MAX). These ground against almost any text and
    are where non-measurements hide (event=1, multiplier=2, rank, identifier)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == int(f) and abs(f) <= SMALL_INT_MAX


def candidate_numbers(text):
    """All numeric values the text could express (digit, abbreviated, spelled-out)."""
    out = set()
    low = (text or "").lower()
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


def value_grounded(metric_value, value_ref_text):
    v = to_float(metric_value)
    if v is None:
        return None  # qualitative / no number -> can't verify by value
    cands = candidate_numbers(value_ref_text)
    if is_small_int(v):
        # HARDENED for small integers: exact standalone-token match only. NO +-1 tolerance floor
        # and NO digit-substring fallback -- otherwise a "1" matches any stray digit on the page
        # (a year, "$10,000", "Section 1"), which is how invented "event=1" / "a"=1 / rank=2 values
        # slipped through. candidate_numbers yields proper number tokens (incl. spelled-out "one"=1),
        # not substrings, so a real "9 vans" / "one client" still grounds; "a food truck" (no numeral)
        # and "second highest" ("second" is not a number word) no longer do.
        return any(abs(c - v) < 1e-9 for c in cands)
    tol = max(1.0, 0.001 * abs(v))
    if any(abs(c - v) <= tol for c in cands):
        return True
    # Digit-substring fallback (formatting variants like "1.3M" vs 1,300,000): WITHIN one number
    # token only. Never across concatenated digits of the whole marker — "111" must not ground by
    # spanning "...11 ... 121..." (efd61fea:11: parse dropped a char, model hallucinated 111, and
    # the old all-digits concatenation "11121..." contained it).
    d = re.sub(r"\D", "", str(metric_value))
    if not d:
        return False
    for tok in re.findall(r"[\d][\d,.]*", value_ref_text or ""):
        if d in re.sub(r"\D", "", tok):
            return True
    return False


def _words(s):
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def _center(bbox):
    if not bbox or "t" not in bbox or "b" not in bbox:
        return None
    return (bbox["t"] + bbox["b"]) / 2.0


def co_located(a, b, idmap):
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


def subject_grounded(label, subject_ref, value_ref, idmap):
    sr = idmap.get(subject_ref)
    if not sr:
        return False
    stext = (sr.get("text", "") or "") + " " + (sr.get("row_text", "") or "")
    if _words(label) & _words(stext):
        return True
    return co_located(value_ref, subject_ref, idmap)


_TARGET = {"target", "goal", "projected", "budget", "budgeted", "planned", "expected", "forecast"}
_ACTUAL = {"actual", "actuals", "achieved", "attained", "realized"}
_BENCH = {"average", "averages", "national", "benchmark", "median", "peer"}


def _qual_cat(wset):
    if wset & _TARGET:
        return "target"
    if wset & _ACTUAL:
        return "actual"
    if wset & _BENCH:
        return "bench"
    return None


_TOTAL_HDR = ("total", "all funds", "combined", "consolidated", "grand", "subtotal")


def _header_for(cells, col):
    if col is None:
        return ""
    hr = min((c.get("row") for c in cells if c.get("row") is not None), default=None)
    return next((c.get("text", "") for c in cells if c.get("row") == hr and c.get("col") == col), "")


def _is_total_hdr(h):
    return any(t in (h or "").lower() for t in _TOTAL_HDR)


def column_guard(metric_value, value_ref, idmap, statement="", label=""):
    """#9 guard (v2): wrong-COLUMN attribution for a value in a table cell.
    A) value EXACTLY duplicated across non-total columns of its row -> column
       unverifiable. A 'total'-type column matching a component (e.g. unrestricted ==
       total when restricted = 0) is BENIGN, not flagged.
    B) the value cell's column header conflicts with the metric's wording (target vs
       achieved / a different year). Returns a quarantine reason or None."""
    a = idmap.get(value_ref)
    if not a or a.get("kind") != "cell" or a.get("col") is None:
        return None
    cells = [c for c in idmap.values()
             if c.get("kind") == "cell" and c.get("table") == a.get("table") and c.get("page") == a.get("page")]
    if not cells:
        return None
    aval = to_float(a.get("text"))
    if aval is not None:
        samerow = [c for c in cells if c.get("row") == a.get("row") and c.get("col") != a.get("col")]
        matches = [c for c in samerow
                   if to_float(c.get("text")) is not None and abs(to_float(c.get("text")) - aval) < 1e-6]
        if matches and not _is_total_hdr(_header_for(cells, a.get("col"))) \
                and not any(_is_total_hdr(_header_for(cells, c.get("col"))) for c in matches):
            return "column_ambiguous"
    hdr = _header_for(cells, a.get("col"))
    mtext = (statement or "") + " " + (label or "")
    hc, mc = _qual_cat(_words(hdr)), _qual_cat(_words(mtext))
    if hc and mc and hc != mc:
        return "column_header_conflict"
    syear = set(re.findall(r"(?:19|20)\d{2}", statement or ""))
    hyear = set(re.findall(r"(?:19|20)\d{2}", hdr or ""))
    if syear and hyear and not (syear & hyear):
        return "column_year_conflict"
    return None


def verdict(metric, value_ref, subject_ref, idmap, measured=None):
    """Return (decision, reason). decision in {'publish','quarantine'}.

    `measured` is the small-integer semantic-validity signal (ENH-005): for a value <=20, the
    measurable-value classifier's answer to "does this number measure a real quantity?" --
    True=yes, False=no (event/multiplier/rank/identifier), None=not checked (e.g. large values).
    """
    vr = idmap.get(value_ref)
    mv = metric.get("metric_value")
    vg = value_grounded(mv, vr.get("text") if vr else "")
    if vg is None:
        return "quarantine", "no_numeric_value"
    if not vg:
        return "quarantine", "value_not_at_marker"
    if not subject_grounded(metric.get("label"), subject_ref, value_ref, idmap):
        return "quarantine", "subject_not_grounded"
    # Small-integer semantic gate (ENH-005): a small int grounds trivially even when it is not a
    # measurement (event=1, "the only..."=1, multiplier "doubled"=2). For value<=20 require the
    # measurable-value classifier to confirm it measures a real quantity. Keeps real small counts
    # ("9 vans", "served 6 counties"); rejects the artifacts the grounding check cannot see.
    fv = to_float(mv)
    if fv is not None and is_small_int(fv) and measured is False:
        return "quarantine", "not_a_metric"
    # column_guard REMOVED 2026-06-07: it over-flagged benign financials (56 column_ambiguous,
    # almost all on excluded compilation reports) and caught zero confirmed real #9 — the
    # abandoned guard never belonged in the validated tuned gate. The #9 (table column-mispairing)
    # residual is handled by the geometry spatial-pairing layer (ENH-003), not this guard.
    # Function kept below for history; intentionally not called.
    # Standalone co-location REMOVED (2026-06-06 tuning): it double-jeopardied prose
    # metrics whose subject already word-matches its marker (value + subject both
    # genuinely present, just cited in different items / pages), and it cannot catch
    # the real residual — table COLUMN mispairing (the #9 case: value in the right
    # ROW but wrong COLUMN, where value and its row-label ARE co-located). That residual
    # needs the separate column-header guard (future) and is measured by the gold review.
    # The publish bar is: value grounded at its marker AND subject grounded at its marker.
    return "publish", "ok"
