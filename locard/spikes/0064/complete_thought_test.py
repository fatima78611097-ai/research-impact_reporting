"""0064 — test the COMPLETE-THOUGHT prompt change on the 3 flagged pages.

Only change vs production: metric_text is instructed to be a complete, self-
contained sentence composed from on-page context (label + heading + clause).
source_snippet rule UNCHANGED (verbatim). Compare current vs complete-thought,
grounding source_snippet against the page text. Watch: is metric_text now a
complete thought, does the snippet still ground, and is the composition CORRECT?
"""
import json
import sys
import httpx
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells, to_lines, detect_columns
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import _METRICS_PROMPT
from lavandula.faithfulness.grounding import check as gate_check

KEY = get_secret("lavandula/deepseek/api_key")

OLD_LINE = '- "metric_text": Short natural language description (e.g., "1,514 cancer patients and caregivers served")'
NEW_LINE = ('- "metric_text": A COMPLETE, SELF-CONTAINED sentence stating the metric so it is fully '
            'understandable WITHOUT the source page. Combine the number with its label, the section/'
            'list/card heading, and the measured subject — all present on the page — adding only the '
            'minimal connective words needed to read naturally (e.g. "62.2% of 3rd-graders in the HLWW '
            'district met the Minnesota state reading standard", "volunteers came from 32 countries", '
            '"the expanded Child Tax Credit cut child poverty by about 30%"). Add NO facts not on the page.')
CT_PROMPT = _METRICS_PROMPT.replace(OLD_LINE, NEW_LINE)
assert CT_PROMPT != _METRICS_PROMPT, "metric_text line not replaced!"


def col_text(cells, w):
    parts = []
    for c in detect_columns(cells, 0.09 * w):
        parts.extend(to_lines(c)); parts.append("")
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


def show(label, metrics, text, keys):
    print(f"\n  --- {label} ({len(metrics)} metrics) ---")
    for m in metrics:
        mt = m.get("metric_text", ""); snip = m.get("source_snippet", "")
        if keys and not any(k.lower() in (mt + " " + snip).lower() for k in keys):
            continue
        g = getattr(gate_check(snippet=snip or "", source_text=text, tables=[], value=m.get("metric_value")), "grounded", False)
        print(f"    [{'GND' if g else 'qan'}] {mt}")
        print(f"          snip: {snip!r}")


TESTS = [
    ("001eb5f8", 6, ["child tax credit", "30%", "3.7 million"]),
    ("4d2d0f16", 13, ["hlww", "rockford", "state", "reading", "62.2", "59.9"]),
    ("001eb5f8", 13, ["email", "campaign", "delivered", "63,955", "media", "social"]),
]
import glob
PATH = {p.split("/")[-1][:8]: p for p in glob.glob("pdfs/*.pdf")}
with httpx.Client(timeout=120) as client:
    cache = {}
    for sha8, page, keys in TESTS:
        if sha8 not in cache:
            cache[sha8] = load_doc(PATH[sha8])
        doc, n = cache[sha8]
        seg = doc.get_page(page); cells = page_cells(seg)
        txt = col_text(cells, seg.dimension.width)
        print(f"\n{'='*74}\n{sha8} p{page}\n{'='*74}")
        show("CURRENT prompt", extract(client, _METRICS_PROMPT, txt), txt, keys)
        show("COMPLETE-THOUGHT prompt", extract(client, CT_PROMPT, txt), txt, keys)
