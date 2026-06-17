"""0064 — try-and-measure regroup gate, SPATIAL accept check.

Candidate = page with quarantined baseline metrics (healthy pages skipped).
Apply fix; for each fixed metric that attaches a parent label, VALIDATE the claim
SPATIALLY: the number cell and the parent-label cell must sit in the same card
(x-column overlap + bounded vertical gap) on the page. Mis-pairings (mosaic) fail
this; real recoveries (f3dc12b3) pass. ACCEPT iff the fix yields net benefit
(newly-grounded values + baseline values that GAINED a coherent parent) and loses
no baseline-grounded value.
"""
import json
import sys
import httpx
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells, to_lines, detect_columns
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT
from lavandula.faithfulness.grounding import check as gate_check, normalize

KEY = get_secret("lavandula/deepseek/api_key")
KPI_ADD = """

ADDITIONAL — KPI/INFOGRAPHIC CARDS: when a metric belongs to a visual card (a short
CATEGORY LABEL grouped with numbers), append the parent label to metric_text after
" — " (e.g. "63,955 delivered — Email Campaign"). source_snippet rule UNCHANGED:
verbatim, contiguous."""
KPI_PROMPT = _METRICS_PROMPT.replace("Return ONLY the JSON array, no other text.",
                                     KPI_ADD + "\nReturn ONLY the JSON array, no other text.")


def page_text(cells, w, mode):
    if mode == "row":
        return "\n".join(to_lines(cells))
    parts = []
    for col in detect_columns(cells, 0.09 * w):
        parts.extend(to_lines(col)); parts.append("")
    return "\n".join(parts)


def extract(client, prompt, text):
    r = client.post("https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {KEY}"},
                    json={"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 2200,
                          "messages": [{"role": "system", "content": prompt},
                                       {"role": "user", "content": text}]})
    t = r.json()["choices"][0]["message"]["content"].strip()
    if t.startswith("```"):
        t = t.split("```")[1].lstrip("json").strip()
    try:
        m = json.loads(t)
        return m if isinstance(m, list) else m.get("metrics", [])
    except Exception:
        return []


def cell_with(cells, frag):
    f = normalize(frag)
    if not f:
        return None
    for c in cells:
        if f in normalize(c["text"]):
            return c
    return None


def label_cell(cells, label):
    toks = {t for t in normalize(label).split() if len(t) > 2}
    if not toks:
        return None
    best, bestn = None, 0
    for c in cells:
        ov = len(toks & set(normalize(c["text"]).split()))
        if ov > bestn:
            bestn, best = ov, c
    return best


import re


def num_token(snip, value):
    m = re.search(r"\d[\d,\.]*\+?", snip or "")
    return m.group(0) if m else str(value)


def coherent(cells, value, snip, parent, w, h):
    """number cell and parent-label cell in the same card (col overlap + near y)?"""
    n = cell_with(cells, num_token(snip, value))
    p = label_cell(cells, parent)
    if not n or not p:
        return False
    x_overlap = min(n["r"], p["r"]) - max(n["l"], p["l"]) > -0.05 * w   # columns overlap/adjacent
    x_close = abs(n["xc"] - p["xc"]) < 0.18 * w
    y_close = abs(n["top"] - p["top"]) < 0.32 * h
    return (x_overlap or x_close) and y_close


def grounded(snip, text, value):
    return getattr(gate_check(snippet=snip or "", source_text=text, tables=[], value=value),
                   "grounded", False)


_YEAR = re.compile(r"^\s*\(?(19|20)\d\d\)?(\s*[-–—]\s*(19|20)\d\d)?\s*$")
_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)


def valid_metric(snip):
    """reject non-metrics: no number, a bare year/range, or a date line."""
    s = (snip or "").strip()
    if not re.search(r"\d", s):
        return False
    if _YEAR.match(s):
        return False
    if _MONTH.search(s) and re.search(r"\b(19|20)\d\d\b", s) and len(s.split()) < 9:
        return False
    return True


def page_garbled(cells):
    """glyph-soup guard: fraction of single-char whitespace tokens."""
    toks = " ".join(c["text"] for c in cells).split()
    if not toks:
        return False
    return sum(1 for t in toks if len(t) == 1) / len(toks) > 0.40


def gate_page(client, doc, page):
    seg = doc.get_page(page)
    cells = page_cells(seg)
    if not cells:
        return None
    w, h = seg.dimension.width, seg.dimension.height
    if page_garbled(cells):                         # garbled text -> needs pypdfium2 backend, not regrouping
        return {"cand": "garbled", "base_g": 0, "base_n": 0,
                "kept": 0, "rec": 0, "enr": 0, "lost": 0, "decision": "SKIP(garbled)"}
    row, col = page_text(cells, w, "row"), page_text(cells, w, "col")
    base = extract(client, _METRICS_PROMPT, row)
    base_g = {str(m.get("metric_value")) for m in base
              if valid_metric(m.get("source_snippet")) and grounded(m.get("source_snippet"), row, m.get("metric_value"))}
    candidate = len(base_g) < len(base)            # has baseline quarantine?
    if not candidate:
        return {"cand": "no", "base_g": len(base_g), "base_n": len(base),
                "kept": 0, "rec": 0, "enr": 0, "lost": 0, "decision": "SKIP(healthy)"}
    fixed = extract(client, KPI_PROMPT, col)
    kept, parented = set(), set()
    text_of = {}
    for m in fixed:
        val = str(m.get("metric_value")); mt = m.get("metric_text", ""); snip = m.get("source_snippet", "")
        if not valid_metric(snip):
            continue
        if not grounded(snip, col, m.get("metric_value")):
            continue
        if "—" in mt:
            parent = mt.split("—")[-1].strip()
            if coherent(cells, val, snip, parent, w, h):
                kept.add(val); parented.add(val); text_of[val] = mt
            # else: incoherent parent -> dropped (scramble guard)
        else:
            kept.add(val); text_of.setdefault(val, mt)
    recovered = kept - base_g
    enriched = parented & base_g
    lost = base_g - kept
    rec_examples = [text_of[v] for v in list(recovered)[:6] if v in text_of]
    enr_examples = [text_of[v] for v in list(enriched)[:6] if v in text_of]
    # accept: the fix ADDS value (recovers new grounded values or enriches baseline
    # ones with a spatially-coherent parent) AND destroys little baseline-grounded
    # content (lost within jitter tolerance). The lost-budget rejects scramble
    # (mosaic loses ~45%) while tolerating extraction jitter (p13 loses ~17%).
    lost_budget = max(1, round(0.25 * len(base_g)))
    accept = (len(recovered) + len(enriched)) > 0 and len(lost) <= lost_budget
    return {"cand": "yes", "base_g": len(base_g), "base_n": len(base),
            "kept": len(kept), "rec": len(recovered), "enr": len(enriched), "lost": len(lost),
            "decision": "ACCEPT" if accept else "REJECT",
            "rec_examples": rec_examples, "enr_examples": enr_examples}


if __name__ == "__main__":
    TESTS = [
        ("001eb5f8", 13, "ACCEPT (EMAIL CAMPAIGN enrich)"),
        ("f3dc12b3", 3, "ACCEPT (recovers)"),
        ("2fccbb57", 9, "SKIP/REJECT (baseline healthy)"),
        ("61d4b4bb", 1, "REJECT (mosaic scramble)"),
        ("001eb5f8", 20, "SKIP/REJECT (financial)"),
        ("001eb5f8", 7, "SKIP/REJECT (prose)"),
    ]
    import glob
    PATH = {p.split("/")[-1][:8]: p for p in glob.glob("pdfs/*.pdf")}
    print(f"{'page':13} {'cand':4} {'decision':14} base_g/n kept rec enr lost | expected")
    with httpx.Client(timeout=120) as client:
        cache = {}
        for sha8, page, exp in TESTS:
            if sha8 not in cache:
                cache[sha8] = load_doc(PATH[sha8])
            doc, n = cache[sha8]
            r = gate_page(client, doc, page)
            print(f"{sha8} p{page:<3} {r['cand']:4} {r['decision']:14} "
                  f"{r['base_g']:>2}/{r['base_n']:<2}   {r['kept']:>3}  {r['rec']:>2}  {r['enr']:>2}  {r['lost']:>2}  | {exp}")
