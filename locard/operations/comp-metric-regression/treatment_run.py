"""Comp-metric prompt regression — STEP 2: treatment vs the noise floor.

Renders TAGGED input (text items inline-tagged; tables row-structured with
per-cell tags) from the captured prov, runs the MODIFIED prompt (adds
value_ref/subject_ref) N times on the SAME 10 docs as the noise floor, then:
  - compares treatment selection/count to the virgin runs (within the floor?),
  - checks every cited marker resolves to a real location (or is null; never invented).
Production settings (deepseek-chat, temp 0.0, max_tokens 3000). Runs on cloud2.
"""
import json
import re
import statistics as st
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text

MODIFIED = open("locard/operations/comp-metric-regression/comp-metric-prompt.v-loc1.txt").read()
KEY = get_secret("lavandula/deepseek/api_key")
NOISE = json.load(open("/tmp/comp-metric-noisefloor.json"))
PICKED = NOISE["picked"]
VIRGIN_RUNS = {s: NOISE["results"][s]["runs"] for s in PICKED}
N_RUNS = 3


def chat(system, user):
    body = json.dumps({
        "model": "deepseek-chat", "temperature": 0.0, "max_tokens": 3000,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        t = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:].strip() if t.lower().startswith("json") else t.strip()
    try:
        out = json.loads(t)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def key_metric(m):
    return str(m.get("metric_value")), frozenset(re.findall(r"[a-z]{3,}", (m.get("label") or "").lower()))


def overlap(a, b):
    sa, sb = [key_metric(m) for m in a], [key_metric(m) for m in b]
    matched, used = 0, set()
    for x in sa:
        for j, y in enumerate(sb):
            if j in used:
                continue
            if x[0] == y[0] and (bool(x[1] & y[1]) or not x[1] or not y[1]):
                matched += 1
                used.add(j)
                break
    union = len(sa) + len(sb) - matched
    return matched / union if union else 1.0


def render_tagged(conn, sha):
    idmap, items = {}, []
    t, c = [0], [0]
    secs = conn.execute(text(
        "SELECT section_index, body_text, source_locations FROM lava_parse.sections "
        "WHERE content_sha256=:s ORDER BY section_index"), {"s": sha}).fetchall()
    for _idx, body_text, sl in secs:
        if sl:
            for it in sl:
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                loc = (it.get("locations") or [{}])[0]
                tid = f"t{t[0]}"; t[0] += 1
                idmap[tid] = {"kind": "text", "page": loc.get("page_no"), "bbox": loc.get("bbox")}
                items.append((loc.get("page_no") or 0, f"{txt} ⟨{tid}⟩"))
        elif body_text:
            tid = f"t{t[0]}"; t[0] += 1
            idmap[tid] = {"kind": "text", "page": None, "bbox": None}
            items.append((0, f"{body_text.strip()} ⟨{tid}⟩"))
    tabs = conn.execute(text(
        "SELECT table_index, page_number, cell_locations FROM lava_parse.tables "
        "WHERE content_sha256=:s ORDER BY table_index"), {"s": sha}).fetchall()
    for _ti, page, cl in tabs:
        if not cl:
            continue
        rows = {}
        for cell in cl:
            rows.setdefault(cell.get("row"), []).append(cell)
        lines = [f"[table on page {page}]"]
        for r in sorted(rows, key=lambda x: (x is None, x)):
            parts = []
            for cell in sorted(rows[r], key=lambda cc: (cc.get("col") is None, cc.get("col"))):
                cid = f"c{c[0]}"; c[0] += 1
                idmap[cid] = {"kind": "cell", "page": page, "bbox": cell.get("bbox"),
                              "row": cell.get("row"), "col": cell.get("col")}
                parts.append(f"{(cell.get('text') or '').strip()} ⟨{cid}⟩")
            lines.append(f"row {r}: " + " | ".join(parts))
        items.append((page or 0, "\n".join(lines)))
    items.sort(key=lambda x: x[0])
    return "\n".join(b for _, b in items)[:60_000], idmap


eng = make_app_engine()
out = {}
with eng.connect() as conn:
    for sha in PICKED:
        tagged, idmap = render_tagged(conn, sha)
        runs = [chat(MODIFIED, tagged) for _ in range(N_RUNS)]
        counts = [len(r) for r in runs]
        # treatment-vs-virgin overlap (each treatment run vs each virgin run)
        tv = [overlap(tr, vr) for tr in runs for vr in VIRGIN_RUNS[sha]]
        # marker resolution across all treatment metrics
        refs = [(m.get("value_ref"), m.get("subject_ref")) for r in runs for m in r]
        flat = [x for pair in refs for x in pair]
        resolved = sum(1 for x in flat if x in idmap)
        nulls = sum(1 for x in flat if x is None)
        invented = sum(1 for x in flat if x is not None and x not in idmap)
        out[sha] = {"counts": counts, "treat_vs_virgin": tv,
                    "refs_total": len(flat), "resolved": resolved, "null": nulls, "invented": invented,
                    "runs": runs, "n_ids": len(idmap)}
        print(f"  {sha[:8]}: counts={counts} treat-vs-virgin_ov={[round(o,2) for o in tv][:3]}... "
              f"refs {resolved}res/{nulls}null/{invented}INVENTED of {len(flat)}", flush=True)

allcounts = [c for r in out.values() for c in r["counts"]]
tvall = [o for r in out.values() for o in r["treat_vs_virgin"]]
vvall = [o for s in PICKED for o in NOISE["results"][s]["pairwise_overlap"]]
tot_refs = sum(r["refs_total"] for r in out.values())
print("\n==== TREATMENT vs NOISE FLOOR ====")
print(f"treatment metric count: median {st.median(allcounts)}, range {min(allcounts)}-{max(allcounts)}  "
      f"(virgin floor: median {st.median([c for s in PICKED for c in NOISE['results'][s]['counts']])})")
print(f"treatment-vs-virgin selection overlap: median {st.median(tvall):.2f}, mean {st.mean(tvall):.2f}")
print(f"  vs virgin-vs-virgin (noise floor):   median {st.median(vvall):.2f}, mean {st.mean(vvall):.2f}")
print(f"marker resolution: {sum(r['resolved'] for r in out.values())} resolved / "
      f"{sum(r['null'] for r in out.values())} null / {sum(r['invented'] for r in out.values())} INVENTED "
      f"of {tot_refs} refs")
json.dump(out, open("/tmp/comp-metric-treatment.json", "w"), indent=1)
print("saved /tmp/comp-metric-treatment.json")
