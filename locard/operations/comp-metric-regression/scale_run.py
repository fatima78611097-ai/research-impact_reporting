"""Two-step + hardened gate, scaled to the full baseline-100.

Step 1 = virgin selection (unchanged). Step 2 = grounding pass. Then gate.verdict
(hardened value match + subject/co-location) -> publish | quarantine. Reports the
split + quarantine reasons, and saves full per-metric output for the gold review.
Pre-fetches all doc text/prov sequentially (RDS), then parallelizes the API calls.
"""
import collections
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text

VIRGIN = open("locard/operations/prompt-backups/2026-06-06/comp-metric-prompt.KNOWN-GOOD.txt").read()
GROUND = open("locard/operations/comp-metric-regression/comp-metric-grounding.v1.txt").read()
KEY = get_secret("lavandula/deepseek/api_key")
INPUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/baseline-100.json"
SHAS = [d["sha"] for d in json.load(open(INPUT))["docs"]]
OUT = sys.argv[2] if len(sys.argv) > 2 else "locard/operations/comp-metric-regression/scale100-results.json"


def chat(system, user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 3000,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions", data=body,
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                t = json.loads(r.read())["choices"][0]["message"]["content"].strip()
            if t.startswith("```"):
                t = t.split("```")[1]
                t = t[4:].strip() if t.lower().startswith("json") else t.strip()
            o = json.loads(t)
            return o if isinstance(o, list) else []
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == 2:
                return []
            time.sleep(2 * (attempt + 1))
    return []


def render_tagged(conn, sha):
    idmap, items, sectext_parts = {}, [], []
    t, c = [0], [0]
    secs = conn.execute(text(
        "SELECT body_text, source_locations FROM lava_parse.sections "
        "WHERE content_sha256=:s ORDER BY section_index"), {"s": sha}).fetchall()
    for body, sl in secs:
        if body:
            sectext_parts.append(body)
        if sl:
            for it in sl:
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                loc = (it.get("locations") or [{}])[0]
                tid = f"t{t[0]}"; t[0] += 1
                idmap[tid] = {"text": txt, "page": loc.get("page_no"), "bbox": loc.get("bbox")}
                items.append((loc.get("page_no") or 0, f"{txt} ⟨{tid}⟩"))
        elif body:
            tid = f"t{t[0]}"; t[0] += 1
            idmap[tid] = {"text": body.strip(), "page": None, "bbox": None}
            items.append((0, f"{body.strip()} ⟨{tid}⟩"))
    tabs = conn.execute(text(
        "SELECT page_number, cell_locations FROM lava_parse.tables "
        "WHERE content_sha256=:s ORDER BY table_index"), {"s": sha}).fetchall()
    for page, cl in tabs:
        if not cl:
            continue
        rows = {}
        for cell in cl:
            rows.setdefault(cell.get("row"), []).append(cell)
        lines = [f"[table on page {page}]"]
        for r in sorted(rows, key=lambda x: (x is None, x)):
            rowtext = " ".join((cc.get("text") or "") for cc in rows[r])
            parts = []
            for cell in sorted(rows[r], key=lambda cc: (cc.get("col") is None, cc.get("col"))):
                cid = f"c{c[0]}"; c[0] += 1
                idmap[cid] = {"text": (cell.get("text") or "").strip(), "row_text": rowtext,
                              "page": page, "row": cell.get("row"), "bbox": cell.get("bbox")}
                parts.append(f"{(cell.get('text') or '').strip()} ⟨{cid}⟩")
            lines.append(f"row {r}: " + " | ".join(parts))
        items.append((page or 0, "\n".join(lines)))
    items.sort(key=lambda x: x[0])
    return "\n".join(sectext_parts)[:60_000], "\n".join(b for _, b in items)[:60_000], idmap


def norm(x):
    return x.strip().strip("⟨⟩").strip() if isinstance(x, str) else None


eng = make_app_engine()
docdata = {}
with eng.connect() as conn:
    for sha in SHAS:
        docdata[sha] = render_tagged(conn, sha)
print(f"prefetched {len(docdata)} docs", flush=True)


def process(sha):
    sectext, tagged, idmap = docdata[sha]
    metrics = chat(VIRGIN, sectext)
    lines = [f"M{i+1}: {m.get('statement')} (value={m.get('metric_value')}, subject={m.get('label')})"
             for i, m in enumerate(metrics)]
    g = chat(GROUND, "METRICS:\n" + "\n".join(lines) + "\n\nDOCUMENT:\n" + tagged)
    bym = {x.get("m"): x for x in g if isinstance(x, dict)}
    recs = []
    for i, m in enumerate(metrics):
        row = bym.get(i + 1) or (g[i] if i < len(g) and isinstance(g[i], dict) else {})
        vr, srf = norm(row.get("value_ref")), norm(row.get("subject_ref"))
        dec, reason = gate.verdict(m, vr, srf, idmap)
        recs.append({"statement": m.get("statement"), "value": m.get("metric_value"),
                     "label": m.get("label"), "value_ref": vr, "subject_ref": srf,
                     "decision": dec, "reason": reason})
    return sha, recs


results = {}
with ThreadPoolExecutor(max_workers=6) as ex:
    for sha, recs in ex.map(process, SHAS):
        results[sha] = recs
        pub = sum(1 for r in recs if r["decision"] == "publish")
        print(f"  {sha[:8]}: {len(recs)} metrics, {pub} pub / {len(recs)-pub} quar", flush=True)

allrecs = [r for recs in results.values() for r in recs]
pub = [r for r in allrecs if r["decision"] == "publish"]
reasons = collections.Counter(r["reason"] for r in allrecs if r["decision"] == "quarantine")
print("\n==== HARDENED GATE ON FULL 100 ====")
print(f"docs={len(results)}  metrics={len(allrecs)}")
print(f"PUBLISH {len(pub)} ({len(pub)/len(allrecs)*100:.0f}%)  "
      f"QUARANTINE {len(allrecs)-len(pub)} ({(len(allrecs)-len(pub))/len(allrecs)*100:.0f}%)")
print("quarantine reasons:", dict(reasons))
json.dump(results, open(OUT, "w"), indent=1)
print(f"saved {OUT}")
