"""Filterable viewer for the measurable-value (not-a-metric) determinations. Lists every metric the
check flagged, with my verdict and a link to the source PDF page. Serves at /p20/measurable-flags.html.
"""
import html
import json

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
MV = "/home/ubuntu/research/locard/operations/comp-metric-regression/measurable-value-quarantine.json"

flagged = json.load(open(MV))["quarantine"]
review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}
HELD = {"5b8eae73:4", "d58e3d5b:11", "12a4557a:4", "3e3ff326:10",
        "e43d7482:6", "f4819890:8", "81265722:9", "0793289a:2"}


def e(x):
    return html.escape(str(x if x is not None else ""))


def tag_of(i, r):
    if i in HELD:
        return ("held", "HELD — real metric, check wrong", "#2980b9")
    if r.get("gate_decision") == "publish":
        return ("nam", "NOT-A-METRIC (confirmed)", "#777")
    return ("quar", "already quarantined", "#999")


rows = []
counts = {"nam": 0, "held": 0, "quar": 0}
for i in flagged:
    r = review.get(i)
    if not r:
        continue
    cls, label, color = tag_of(i, r)
    counts[cls] += 1
    pg = r.get("v_page") or r.get("page") or 1
    rows.append((cls, f"""<tr data-tag="{cls}">
  <td><span class=tag style="background:{color}">{label}</span></td>
  <td class=id>{e(i)}</td><td class=val>{e(r.get('value'))}</td>
  <td>{e(r.get('statement'))}<div class=org>{e(r.get('org'))}</div></td>
  <td><a href="{e(r.get('pdf'))}#page={pg}" target=_blank>p{pg}</a></td>
</tr>"""))

order = {"nam": 0, "held": 1, "quar": 2}
rows.sort(key=lambda x: order[x[0]])
body = "".join(h for _, h in rows)

page = f"""<!doctype html><meta charset=utf-8><title>Not-a-metric viewer</title>
<style>
body{{font-family:system-ui,Arial;margin:0;background:#f3f3f3;color:#222}}
.bar{{position:sticky;top:0;background:#222;color:#fff;padding:10px 14px;font-size:13px}}
.bar button{{margin-left:6px;padding:4px 10px;border:0;border-radius:5px;cursor:pointer;font-size:12px}}
table{{border-collapse:collapse;width:100%;background:#fff}}
td{{border-bottom:1px solid #e2e2e2;padding:8px 10px;font-size:13px;vertical-align:top}}
.tag{{color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap}}
.id{{font-family:ui-monospace,monospace;font-size:12px;white-space:nowrap}}
.val{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.org{{color:#999;font-size:11px;margin-top:2px}}
</style>
<div class=bar><b>Measurable-value flags — {len(rows)} total.</b>
  <button onclick="f('all')">all</button>
  <button onclick="f('nam')">not-a-metric ({counts['nam']})</button>
  <button onclick="f('held')">held ({counts['held']})</button>
  <button onclick="f('quar')">already quarantined ({counts['quar']})</button>
</div>
<table><tbody id=t>{body}</tbody></table>
<script>
function f(t){{document.querySelectorAll('#t tr').forEach(r=>{{
  r.style.display = (t==='all'||r.dataset.tag===t) ? '' : 'none';}});}}
</script>
"""
open(VIS + "/measurable-flags.html", "w").write(page)
print(f"wrote measurable-flags.html, {len(rows)} rows. counts: {counts}")
