"""Cheap 2nd-stage gate WITHOUT vision: feed DeepSeek (text) the page's cells +
normalized bounding boxes and ask it to classify the layout. Layout is the
discriminative signal; coords survive even when glyphs are garbled."""
import json
import sys
import httpx
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash"

PROMPT = """You classify ONE page of a nonprofit report from its text-block layout.
You are given each text block as: "text" @ [x0,y0,x1,y1] on a 0-100 normalized page
(x0=left, y0=top). Decide the page's class and whether a "visual regrouping" fix
(re-associating each metric number with its nearby category label by position)
should run on it.

Classes:
- kpi_clean: designed metric cards in CLEAN, separated groups/columns; each short
  label sits next to its number(s). Regrouping WORKS -> regroup=yes.
- kpi_dense: KPI/infographic but tightly packed / overlapping / chaotic; cards NOT
  cleanly separable. regroup=no.
- prose: mostly narrative sentences/paragraphs (a single callout stat is fine). regroup=no.
- financial_table: financial statements / data tables / grids of label+value rows. regroup=no.
- cover_divider: cover, section divider, photo page, donor/sponsor list; little metric content. regroup=no.
- mixed: substantial prose AND KPI cards together. regroup=no.
- garble: text looks scrambled/unreadable. regroup=no.

Rule: regroup=yes ONLY for kpi_clean.
Return ONLY JSON: {"class":"...","regroup":"yes|no","reason":"<8 words"}."""


def layout_text(cells, w, h):
    lines = []
    for c in sorted(cells, key=lambda c: (c["top"], c["l"])):
        x0, y0 = round(c["l"] / w * 100), round(c["top"] / h * 100)
        x1, y1 = round(c["r"] / w * 100), round(c["bot"] / h * 100)
        t = c["text"][:60].replace("\n", " ")
        lines.append(f'"{t}" @ [{x0},{y0},{x1},{y1}]')
    return "\n".join(lines)


def classify(client, doc, n, page):
    seg = doc.get_page(page)
    cells = page_cells(seg)
    body = layout_text(cells, seg.dimension.width, seg.dimension.height)
    r = client.post("https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {KEY}"},
                    json={"model": MODEL, "temperature": 0.0, "max_tokens": 120,
                          "messages": [{"role": "system", "content": PROMPT},
                                       {"role": "user", "content": body}]})
    txt = r.json()["choices"][0]["message"]["content"].strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1].lstrip("json").strip()
    try:
        return json.loads(txt)
    except Exception:
        return {"class": "?", "regroup": "?", "reason": txt[:60]}


TESTS = [  # (pdf, page, ground_truth_class)
    ("001eb5f8", 13, "kpi_clean"), ("001eb5f8", 20, "financial_table"),
    ("001eb5f8", 7, "prose"), ("0749b549", 3, "mixed"),
    ("798514f7", 10, "financial_table"), ("61d4b4bb", 1, "kpi_dense"),
    ("2fccbb57", 9, "kpi_clean"),
]
import glob
PATH = {p.split("/")[-1][:8]: p for p in glob.glob("pdfs/*.pdf")}
print(f"model={MODEL}")
with httpx.Client(timeout=90) as client:
    for sha8, page, truth in TESTS:
        doc, n = load_doc(PATH[sha8])
        v = classify(client, doc, n, page)
        ok = "OK " if v.get("class") == truth else "XX "
        print(f"  {ok} {sha8} p{page}: pred={v.get('class'):16} regroup={v.get('regroup'):3} "
              f"truth={truth:16} | {v.get('reason','')}")
