"""0064 — the TRY-AND-MEASURE regroup gate (dissolves the KPI-classifier problem).

Per candidate page:
  1. baseline  = extract from ROW-major (current) page text; ground snippets vs the
     page's ORIGINAL text -> baseline_grounded {values}.
  2. fixed     = extract from COLUMN-major (regrouped) page text + KPI parent prompt;
     ground each snippet vs the SAME ORIGINAL text (symmetric — a scrambled
     mis-pairing will NOT be a contiguous span of the original, so it fails here).
  3. ACCEPT the regrouping iff it RECOVERS grounded metrics (>0 new) AND loses NONE
     of the baseline-grounded values. Otherwise keep baseline.

A wrong "candidate" guess is harmless: if the fix doesn't help, the gate rejects it.
No KPI-vs-not classification anywhere.
"""
import json
import sys
import httpx
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells, to_lines, detect_columns
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT
from lavandula.faithfulness.grounding import check as gate_check

KEY = get_secret("lavandula/deepseek/api_key")
KPI_ADD = """

ADDITIONAL — KPI/INFOGRAPHIC CARDS: when a metric belongs to a visual card (a short
CATEGORY LABEL grouped with numbers), make metric_text self-contained by including
that parent label. source_snippet rule UNCHANGED: verbatim, contiguous."""
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
                    json={"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 2000,
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


def grounded_values(metrics, original_text):
    """values whose snippet grounds (verbatim) against the ORIGINAL page text."""
    g = set()
    for m in metrics:
        snip = m.get("source_snippet", "")
        v = gate_check(snippet=snip or "", source_text=original_text, tables=[],
                       value=m.get("metric_value"))
        if getattr(v, "grounded", False):
            g.add(str(m.get("metric_value")))
    return g


def gate_page(client, doc, page):
    seg = doc.get_page(page)
    cells = page_cells(seg)
    if not cells:
        return None
    row = page_text(cells, seg.dimension.width, "row")
    col = page_text(cells, seg.dimension.width, "col")
    base = extract(client, _METRICS_PROMPT, row)
    fixed = extract(client, KPI_PROMPT, col)
    base_g = grounded_values(base, row)            # baseline grounded vs original
    fixed_g = grounded_values(fixed, row)          # fixed grounded vs ORIGINAL (symmetric)
    recovered = fixed_g - base_g
    lost = base_g - fixed_g
    accept = len(recovered) > 0 and len(lost) == 0
    return {"base_grounded": len(base_g), "fixed_grounded_vs_orig": len(fixed_g),
            "recovered": len(recovered), "lost": len(lost),
            "decision": "ACCEPT" if accept else "REJECT"}


TESTS = [  # (doc8, page, expected)
    ("001eb5f8", 13, "ACCEPT (EMAIL CAMPAIGN — fix helps)"),
    ("f3dc12b3", 3, "ACCEPT (fix recovers ~20)"),
    ("2fccbb57", 9, "REJECT (baseline already perfect)"),
    ("61d4b4bb", 1, "REJECT (mosaic scrambles)"),
    ("001eb5f8", 20, "REJECT/none (financial already grounds)"),
    ("001eb5f8", 7, "REJECT/none (prose)"),
]
import glob
PATH = {p.split("/")[-1][:8]: p for p in glob.glob("pdfs/*.pdf")}
print(f"{'page':14} {'decision':8} base_g fixed_g recov lost | expected")
with httpx.Client(timeout=120) as client:
    cache = {}
    for sha8, page, exp in TESTS:
        if sha8 not in cache:
            cache[sha8] = load_doc(PATH[sha8])
        doc, n = cache[sha8]
        r = gate_page(client, doc, page)
        print(f"{sha8} p{page:<3} {r['decision']:8} {r['base_grounded']:6} "
              f"{r['fixed_grounded_vs_orig']:7} {r['recovered']:5} {r['lost']:4} | {exp}")
