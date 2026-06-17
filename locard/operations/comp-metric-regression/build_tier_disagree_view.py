"""Filterable viewer for the TIER disagreements (vision-tier vs code-tier) — the 159 contested cases,
each shown in plain-language buckets with the statement and a page link, so the operator can SEE the
fuzzy cases and adapt the tier rules from reality. Serves at /p20/tier-disagreements.html.
"""
import html
import json

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"

# merged vision verdicts
raw = {}
for f in ["vision-sweep-352-result.json", "vision-sweep-complement-result.json", "vision-sweep-rerun-result.json"]:
    for v in json.load(open(B + f))["result"]["raw"]:
        raw[v["id"]] = v
# refs (code_tier, claim) + review (org, pdf, page)
refs = {}
for f in ["vision-sweep-sample.json", "vision-sweep-complement-sample.json", "vision-sweep-rerun-sample.json"]:
    for r in json.load(open(B + f))["records"]:
        refs[r["id"]] = r
review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}

# plain-language bucket names
SHORT = {"reach_output": "helped", "capacity_input": "have", "outcome_impact": "changed",
         "activity_count": "do", "financial": "money", "non_metric": "not-a-metric"}
COLOR = {"helped": "#2980b9", "have": "#8e44ad", "changed": "#27ae60", "do": "#e67e22",
         "money": "#16a085", "not-a-metric": "#777"}


def e(x):
    return html.escape(str(x if x is not None else ""))


# contested = valid metric (per code) that vision also calls a metric, tiers differ
rows = []
counts = {}
for i, v in raw.items():
    rf = refs.get(i, {})
    if rf.get("ref_validity") != "valid_metric" or v.get("is_metric") != "metric":
        continue
    ct, vt = rf.get("ref_tier"), v.get("tier")
    if not ct or not vt or ct == vt:
        continue
    cs, vs = SHORT.get(ct, ct), SHORT.get(vt, vt)
    boundary = " ↔ ".join(sorted([cs, vs]))
    counts[boundary] = counts.get(boundary, 0) + 1
    r = review.get(i, {})
    pg = r.get("v_page") or r.get("page") or 1
    rows.append((boundary, f"""<tr data-b="{e(boundary)}">
  <td class=id>{e(i)}</td><td class=val>{e(rf.get('value'))}</td>
  <td>{e(rf.get('statement'))}<div class=org>{e(r.get('org'))}</div></td>
  <td><span class=t style="background:{COLOR.get(cs,'#999')}">code: {e(cs)}</span><br>
      <span class=t style="background:{COLOR.get(vs,'#999')}">vision: {e(vs)}</span></td>
  <td>{e(v.get('reason'))[:240]}</td>
  <td><a href="{e(r.get('pdf'))}#page={pg}" target=_blank>p{pg}</a></td>
</tr>"""))

order = sorted(counts, key=lambda k: -counts[k])
rows.sort(key=lambda x: order.index(x[0]))
body = "".join(h for _, h in rows)
buttons = "".join(f'<button onclick="f(\'{e(b)}\')">{e(b)} ({counts[b]})</button>' for b in order)

page = f"""<!doctype html><meta charset=utf-8><title>Tier disagreements</title>
<style>
body{{font-family:system-ui,Arial;margin:0;background:#f3f3f3;color:#222}}
.bar{{position:sticky;top:0;background:#222;color:#fff;padding:10px 14px;font-size:13px;line-height:1.9}}
.bar button{{margin:0 4px;padding:4px 9px;border:0;border-radius:5px;cursor:pointer;font-size:12px}}
table{{border-collapse:collapse;width:100%;background:#fff}}
td{{border-bottom:1px solid #e2e2e2;padding:8px 10px;font-size:13px;vertical-align:top}}
th{{position:sticky;top:78px;background:#eee;padding:6px 10px;font-size:11px;text-align:left}}
.t{{color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap;display:inline-block;margin:1px 0}}
.id{{font-family:ui-monospace,monospace;font-size:12px;white-space:nowrap}}
.val{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.org{{color:#999;font-size:11px;margin-top:2px}}
</style>
<div class=bar><b>Tier disagreements (vision vs code) — {len(rows)} contested.</b><br>
Buckets: <b>helped</b>=people served &middot; <b>have</b>=own resources &middot; <b>changed</b>=results/improvement &middot; <b>do</b>=events/launches &middot; <b>money</b>=$ <br>
<button onclick="f('all')">all</button>{buttons}</div>
<table><thead><tr><th>id<th>value<th>statement / org<th>code vs vision<th>vision reason<th>page</tr></thead>
<tbody id=t>{body}</tbody></table>
<script>
function f(b){{document.querySelectorAll('#t tr').forEach(r=>{{
  r.style.display = (b==='all'||r.dataset.b===b) ? '' : 'none';}});}}
</script>
"""
open(VIS + "/tier-disagreements.html", "w").write(page)
print(f"wrote tier-disagreements.html, {len(rows)} rows")
print("boundaries:", {k: counts[k] for k in order})
