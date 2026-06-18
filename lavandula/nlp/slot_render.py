"""Deterministic slot-render + subject-quality filter for grounded metrics.
No model. Readable display is built from the grounded pieces (value + verbatim
label, or the verbatim inline phrase); the filter quarantines metrics whose
SUBJECT ingredient is junk (a gratitude blurb, a statement, a contentless header).
Promotable to the extraction pipeline as-is.

Publish gate for the metric-extraction prompt in lavandula/nlp/llm_extract.py
(_METRICS_PROMPT, committed at b586e941). Step-by-step gating is documented in
GATING.md (same directory)."""
import re

_GRATITUDE = re.compile(r"\b(thank you|thanks|grateful|gratitude|generous|sincere(?:ly)?|"
                        r"proud(?:ly)? to|honou?red|pleased to|delighted|we appreciate|"
                        r"appreciation|salute|shout[- ]?out|kudos)\b", re.I)
# bare meta-word alone (no concrete object) -> junk. "Total Income" / "Total Revenue" KEPT.
_BARE_META = re.compile(r"^(total|number|amount|count|sum|balance|net|gross|subtotal|figures?)\s*:?\s*$", re.I)
_FY_HEADER = re.compile(r"\b(number|figures?|count|amount)\b.*\b(beginning|end)\s+of\b", re.I)
_PREAUDIT = re.compile(r"\bpre-?audit\b", re.I)
# metric-level junk the model leaks despite prompt instructions (deterministic, high-precision)
_TENURE = re.compile(r"\b\d+\s*(st|nd|rd|th)\s+(year|anniversary)\b", re.I)
_FIN_FRAG = re.compile(r"\bgrowth on\b|\bawarded\s+\d+\s+grants?\b|%\s*of\s+(revenue|budget|overall|the\s+award)", re.I)

def metric_quality(metric_text):
    """Junk the model emits as a 'metric' but isn't one. -> (verdict, reason)."""
    t = metric_text or ""
    if _TENURE.search(t):
        return ("junk", "tenure / anniversary, not a metric")
    if _FIN_FRAG.search(t):
        return ("junk", "financial-statement fragment")
    return ("ok", "")
_ONLY_PUNCT = re.compile(r"^[\W\d]+$")
_MARK = re.compile(r"\s*⟨[^⟩]*⟩\s*")          # stray marker tokens that leak into a snippet

def _clean(s):
    return _MARK.sub(" ", s or "").strip()

def fmt_value(v, unit):
    u = (unit or "").lower()
    try:
        n = float(str(v).replace(",", "").replace("$", "").replace("%", ""))
    except (TypeError, ValueError):
        return str(v)
    ns = f"{int(n):,}" if n == int(n) else f"{n:,.2f}".rstrip("0").rstrip(".")
    if any(d in u for d in ("dollar", "usd")) or str(v).strip().startswith("$"):
        return "$" + ns
    if "percent" in u or u == "%":
        return ns + "%"
    return ns

def subject_quality(label):
    """Grade a SPLIT metric's label ingredient. -> (verdict, reason)."""
    s = (label or "").strip()
    if not s or _ONLY_PUNCT.match(s):
        return ("junk", "empty / no words")
    if _GRATITUDE.search(s):
        return ("junk", "gratitude / marketing blurb, not a label")
    if _BARE_META.match(s) or _FY_HEADER.search(s) or _PREAUDIT.search(s):
        return ("junk", "contentless statement header")
    n = len(s.split())
    if n > 14:
        return ("junk", "too long — a sentence, not a label")
    if s.rstrip()[-1] in ".!?" and n > 8:
        return ("junk", "reads as a sentence")
    if n > 10:
        return ("weak", "long label — check")
    return ("ok", "")

def render_and_grade(m, s_text):
    """m = raw model metric (value_ref/subject_ref/unit/source_snippet/metric_value).
    s_text = resolved subject-marker cell text. Returns (display, decision, detail)."""
    mq, mwhy = metric_quality(m.get("metric_text"))
    inline = m.get("value_ref") == m.get("subject_ref")
    val = fmt_value(m.get("metric_value"), m.get("unit"))
    snip = _clean(m.get("source_snippet"))
    if mq == "junk":                             # tenure / financial-fragment etc. — quarantine, still show
        disp = snip if inline else f"{val} — {(s_text or '').strip()}"
        return (disp, "quarantine", mwhy)
    if inline:                                   # verbatim phrase already contains the number
        return ((snip or (s_text or "").strip()), "publish", "")
    # the number lives in its OWN descriptive prose cell -> render from THAT, never graft a
    # foreign label (catches table cross-column mislabels like "10 — Accessibility Training").
    digits = str(m.get("metric_value")).split(".")[0].replace(",", "").lstrip("-")
    if len(snip.split()) > 6 and digits and digits in snip.replace(",", ""):
        return (snip, "publish", "")
    disp = f"{val} — {(s_text or '').strip()}"
    q, why = subject_quality(s_text)
    return (disp, "quarantine" if q == "junk" else "publish", why)


def dedup(metrics):
    """Gate 1 — drop near-duplicate metrics within a document: same metric_value AND
    >=2 overlapping words in metric_text. Keeps the first occurrence. Returns kept list."""
    def toks(s):
        return set(w for w in re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split() if len(w) > 2)
    kept, seen = [], []
    for m in metrics:
        v = str(m.get("metric_value")); w = toks(m.get("metric_text"))
        if any(v == sv and len(w & sw) >= 2 for sv, sw in seen):
            continue
        seen.append((v, w)); kept.append(m)
    return kept
