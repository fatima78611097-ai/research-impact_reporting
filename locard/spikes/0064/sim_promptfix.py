"""0064 — FULL-FIX test: grouping + minimal KPI prompt addition, with gate check.

Same grouped (column-major) text fed to two prompts:
  B = existing _METRICS_PROMPT
  C = existing prompt + ONE addition: make KPI/infographic metrics self-contained
      by including the parent category label (snippet rule unchanged: verbatim,
      contiguous; the now-adjacent label may be part of the span).
Then run the REAL gate (grounding.check) on each metric's snippet against the
grouped text -> proves context-complete AND still verbatim-verified.
"""
import sys
import json
import httpx
sys.path.insert(0, "/home/ubuntu/research")
from grouping_lib import load_doc, all_pages_cells, serialize
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT, _MODEL
from lavandula.faithfulness.grounding import check as gate_check

PDF = sys.argv[1]
COL_GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
import os
SHOW_ALL = os.environ.get("SHOW_ALL") == "1"

# --- the minimal, clearly-labeled prompt addition (everything else unchanged) ---
KPI_ADDITION = """

ADDITIONAL INSTRUCTION — KPI / INFOGRAPHIC CARDS:
Some pages present metrics as visual cards: a short CATEGORY LABEL (often
uppercase, e.g. "EMAIL CAMPAIGN", "MEDIA", "PEER-TO-PEER TEXTING") grouped with
several numbers and their immediate labels. When a metric belongs to such a
card, make "metric_text" self-contained by including that parent category label
(e.g. "63,955 emails delivered — Email Campaign"). The "source_snippet" rule is
UNCHANGED: it must remain a VERBATIM, contiguous copy from the text; when the
category label sits adjacent to the number, you MAY include both in the snippet
(they are contiguous). Never stitch a label from a distant part of the page.
"""
KPI_PROMPT = _METRICS_PROMPT.replace(
    "Return ONLY the JSON array, no other text.",
    KPI_ADDITION + "\nReturn ONLY the JSON array, no other text.",
)

doc, n = load_doc(PDF)
pages = all_pages_cells(doc, n)
col_text = serialize(pages, "col", COL_GAP)
row_text = serialize(pages, "row", COL_GAP)

api_key = get_secret("lavandula/deepseek/api_key")
KEYS = ["deliver", "open", "click", "campaign", "email", "impression", "media",
        "social", "radio", "web ad", "print", " tv", "texting", "volunteer",
        "page view", "tax credit", "scholar", "homeowner", "attendee"]


def run(label, prompt, doc_text):
    with httpx.Client(headers={"Authorization": f"Bearer {api_key}"}) as hc:
        metrics, _, _ = call_deepseek(api_key, doc_text, hc, prompt)
    if isinstance(metrics, dict):
        metrics = metrics.get("metrics", [])
    grounded = 0
    examples = []
    for m in metrics:
        snip = m.get("source_snippet", "")
        v = gate_check(snippet=snip or "", source_text=doc_text, tables=[],
                       value=m.get("metric_value"))
        ok = getattr(v, "grounded", False)
        grounded += 1 if ok else 0
        mt = m.get("metric_text", "")
        if SHOW_ALL or any(k in (mt + " " + snip).lower() for k in KEYS):
            examples.append((f"GROUNDED:{v.rule}" if ok else "QUARANTINE", mt, snip))
    tot = len(metrics)
    print(f"\n{'='*74}\n{label}\n  -> {tot} metrics, {grounded} grounded/verified ({grounded/tot:.0%} if tot else 0), {tot-grounded} quarantine\n{'='*74}")
    for tag, mt, snip in examples[:14]:
        print(f"  [{tag:14}] {mt}")
        print(f"                 snip: {snip!r}")
    return metrics


run("A — ROW-major (current) + existing prompt", _METRICS_PROMPT, row_text)
run("B — COLUMN-major (grouping fix) + existing prompt", _METRICS_PROMPT, col_text)
run("C — COLUMN-major + KPI prompt (full fix)", KPI_PROMPT, col_text)
