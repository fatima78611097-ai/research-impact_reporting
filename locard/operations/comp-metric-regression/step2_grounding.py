"""Comp-metric regression — two-step: virgin selection + separate grounding pass.

Step 1 (selection) = VIRGIN prompt unchanged → reuse saved virgin runs (selection
within floor by construction). Step 2 = focused grounding call assigning
value_ref/subject_ref only. Then a CONTENT-CORRECTNESS check: does the cited
marker's text actually contain the metric's value / match its subject — not just
"is it a real marker." cloud2 (DeepSeek API + RDS read).
"""
import json
import re
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text

GROUND = open("locard/operations/comp-metric-regression/comp-metric-grounding.v1.txt").read()
KEY = get_secret("lavandula/deepseek/api_key")
NOISE = json.load(open("/tmp/comp-metric-noisefloor.json"))
PICKED = NOISE["picked"]


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
                idmap[tid] = {"text": txt, "page": loc.get("page_no")}
                items.append((loc.get("page_no") or 0, f"{txt} ⟨{tid}⟩"))
        elif body_text:
            tid = f"t{t[0]}"; t[0] += 1
            idmap[tid] = {"text": body_text.strip(), "page": None}
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
                ctext = (cell.get("text") or "").strip()
                # a cell's neighbours on its row carry its label — keep the row text for subject matching
                idmap[cid] = {"text": ctext, "row_text": " ".join((cc.get("text") or "") for cc in rows[r]),
                              "page": page}
                parts.append(f"{ctext} ⟨{cid}⟩")
            lines.append(f"row {r}: " + " | ".join(parts))
        items.append((page or 0, "\n".join(lines)))
    items.sort(key=lambda x: x[0])
    return "\n".join(b for _, b in items)[:60_000], idmap


def norm(x):
    return x.strip().strip("⟨⟩").strip() if isinstance(x, str) else None


def digits(s):
    return re.sub(r"[^0-9]", "", str(s if s is not None else ""))


def words(s):
    return set(re.findall(r"[a-z]{3,}", (s or "").lower()))


eng = make_app_engine()
tot_v = vc_real = vc_correct = 0          # value refs
tot_s = sc_real = sc_correct = 0          # subject refs
both_correct = total_metrics = 0
misses = []
out = {}
with eng.connect() as conn:
    for sha in PICKED:
        metrics = NOISE["results"][sha]["runs"][0]
        tagged, idmap = render_tagged(conn, sha)
        lines = [f"M{i+1}: {m.get('statement')} (value={m.get('metric_value')}, subject={m.get('label')})"
                 for i, m in enumerate(metrics)]
        g = chat(GROUND, "METRICS:\n" + "\n".join(lines) + "\n\nDOCUMENT:\n" + tagged)
        bym = {x.get("m"): x for x in g if isinstance(x, dict)}
        dvr = dsr = 0
        for i, m in enumerate(metrics):
            total_metrics += 1
            row = bym.get(i + 1) or (g[i] if i < len(g) and isinstance(g[i], dict) else {})
            vr, srf = norm(row.get("value_ref")), norm(row.get("subject_ref"))
            mval = digits(m.get("metric_value"))
            lbl = words(m.get("label"))
            # value_ref correctness: cited cell/line text contains the metric's number
            tot_v += 1
            v_ok = False
            if vr in idmap:
                vc_real += 1
                cell_digits = digits(idmap[vr]["text"])
                v_ok = bool(mval) and mval in cell_digits
                if v_ok:
                    vc_correct += 1; dvr += 1
            # subject_ref correctness: cited text (or its table row) shares label words
            tot_s += 1
            s_ok = False
            if srf in idmap:
                sc_real += 1
                stext = idmap[srf].get("text", "") + " " + idmap[srf].get("row_text", "")
                s_ok = bool(lbl & words(stext))
                if s_ok:
                    sc_correct += 1; dsr += 1
            if v_ok and s_ok:
                both_correct += 1
            else:
                misses.append({
                    "sha8": sha[:8], "statement": m.get("statement"),
                    "value": m.get("metric_value"), "label": m.get("label"),
                    "value_ref": vr, "value_ref_text": (idmap.get(vr) or {}).get("text"),
                    "subject_ref": srf, "subject_ref_text": (idmap.get(srf) or {}).get("text"),
                    "v_ok": v_ok, "s_ok": s_ok,
                })
        print(f"  {sha[:8]}: {len(metrics)} metrics  value-ref correct {dvr}/{len(metrics)}  "
              f"subject-ref correct {dsr}/{len(metrics)}", flush=True)
        out[sha] = {"n": len(metrics), "value_correct": dvr, "subject_correct": dsr}

print("\n==== TWO-STEP — grounding CORRECTNESS (not just marker exists) ====")
print("Step 1 selection = VIRGIN prompt unchanged → within noise floor by construction.")
print(f"value_ref:   {vc_real}/{tot_v} resolve · {vc_correct}/{tot_v} CORRECT "
      f"(cited cell actually contains the number) = {vc_correct/tot_v*100:.0f}%")
print(f"subject_ref: {sc_real}/{tot_s} resolve · {sc_correct}/{tot_s} CORRECT "
      f"(cited text matches the label) = {sc_correct/tot_s*100:.0f}%")
print(f"metrics with BOTH value+subject correctly grounded: {both_correct}/{total_metrics} "
      f"= {both_correct/total_metrics*100:.0f}%")
json.dump(out, open("/tmp/comp-metric-twostep.json", "w"), indent=1)
print("saved /tmp/comp-metric-twostep.json")
print(f"\n==== {len(misses)} MISSES (for eyeball) ====")
for x in misses:
    flag = ("value" if not x["v_ok"] else "") + ("+subject" if not x["s_ok"] else "")
    print(f"\n[{x['sha8']}] {flag.strip('+')} miss")
    print(f"  metric: {x['statement']}")
    print(f"  value={x['value']!r}  label={x['label']!r}")
    if not x["v_ok"]:
        print(f"  value_ref={x['value_ref']!r} -> {str(x['value_ref_text'])[:100]!r}")
    if not x["s_ok"]:
        print(f"  subject_ref={x['subject_ref']!r} -> {str(x['subject_ref_text'])[:100]!r}")
json.dump(misses, open("/tmp/comp-metric-misses.json", "w"), indent=1)
