"""Experiment: born-correct metrics from RE-GROUPED input (the production stage-3 claim).

For the known scrambled-grid docs, re-serialize each page's text from coordinates — every high-conf
number↔caption pair is emitted as one line ("1,575 — individuals received Grief Training."), the
rest in spatial (y,x) order — then run the LOCKED selection prompt on that input and check the
model's pairings against page-truth. Compares to the known scrambled-input output (the dev corpus).
"""
import json
import re
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import regroup
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text

KEY = get_secret("lavandula/deepseek/api_key")
VIRGIN = open("/home/ubuntu/research/locard/operations/prompt-backups/2026-06-06/comp-metric-prompt.KNOWN-GOOD.txt").read()

# page-truth for the tracked numbers (operator + adjudication confirmed)
TRUTH = {
    "b42e6f95": {"2787": "school support", "1282": "private counseling", "703": "grief support group",
                 "653": "co-parenting", "1575": "grief training", "180": "divorce support",
                 "458": "community partnership", "258": "incarcerated", "75": "medical", "15": "organizations"},
    "5b98b66f": {"1562": "meals", "3256": "food shelf", "2971": "heating", "181": "weatherized",
                 "218": "entrepreneur", "353": "head start", "967": "housing", "369": "financial literacy",
                 "32": "graduates", "1903873": "tax refunds"},
    "ec055971": {"3409": "behavioral health", "826": "employment", "1660": "educational"},
}


def regrouped_doc_text(conn, full):
    by_page = regroup.raw_locations(conn, full)
    parts = []
    for pg in sorted(by_page):
        els = regroup.normalize(by_page[pg])
        pairs = regroup.pair_from_elements(els)
        paired_nums = {n for n, d in pairs.items() if d["label"] and d["confidence"] == "high"}
        paired_labels = {d["label"] for n, d in pairs.items() if n in paired_nums}
        lines = [f"[page {pg}]"]
        # stat pairs first, one line each
        for n in sorted(paired_nums, key=lambda x: -len(x)):
            lines.append(f"{n} — {pairs[n]['label']}.")
        # remaining elements in spatial order, skipping consumed ones
        for t, xc, yt, yb, l, r in sorted(els, key=lambda e: (round(e[2] / 14), e[1])):
            if t in paired_nums or t in paired_labels or not t.strip():
                continue
            lines.append(t)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)[:60000]


def chat(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 3000,
                       "messages": [{"role": "system", "content": VIRGIN}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=240).read())["choices"][0]["message"]["content"].strip()
    if out.startswith("```"):
        out = out.split("```")[1].lstrip("json").strip()
    return json.loads(out)


def numkey(v):
    s = str(v or "")
    try:
        f = float(s.replace(",", "").replace("$", "").replace("%", ""))
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return re.sub(r"[^\d]", "", s)


eng = make_app_engine()
grand_ok = grand_tot = 0
with eng.connect() as conn:
    for sha8, truth in TRUTH.items():
        full = conn.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": sha8 + "%"}).scalar()
        doc_text = regrouped_doc_text(conn, full)
        metrics = chat(doc_text)
        got = {}
        for m in metrics if isinstance(metrics, list) else []:
            got[numkey(m.get("metric_value"))] = (str(m.get("label", "")) + " " + str(m.get("statement", ""))).lower()
        ok = tot = 0
        print(f"\n=== {sha8} (re-grouped input -> locked selection prompt) ===")
        for num, want in truth.items():
            if num not in got:
                print(f"  {num:>9}: not selected")
                continue
            tot += 1
            hit = want in got[num]
            ok += hit
            print(f"  {num:>9}: {'CORRECT' if hit else 'WRONG  '} (wants '{want}') -> {got[num][:64]}")
        grand_ok += ok
        grand_tot += tot
print(f"\nBORN-CORRECT PAIRINGS: {grand_ok}/{grand_tot} of tracked numbers the model selected")
print("(scrambled-input baseline on these same numbers: 10 known mispairs across the 3 docs)")
