"""Browser viewer for the flagged metrics: parsed marker text (where OCR garble shows) next to the
rendered page. Sorted so real problems come first, with a tag per card saying what it is, so the
operator can SEE and judge instead of relying on narration. Serves at /p20/parse-flags.html.
"""
import html
import json
import os
import re
import subprocess

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
FLAGS = "/home/ubuntu/research/locard/operations/comp-metric-regression/subject-fidelity-flags.json"

flags = json.load(open(FLAGS))
review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}
os.makedirs(VIS + "/parseview", exist_ok=True)

MISLABEL = {"f1189246:5", "f1189246:7", "f1189246:8", "8b5543a9:4"}  # vision-confirmed wrong labels


def is_garble(t):
    t = t or ""
    return bool(re.search(r"[A-Za-z]{14,}", t)             # long concatenated run (TOTALSUPPORTANDREVENUE)
                or re.search(r"(?:\b[A-Za-z] ){4,}", t)    # spaced single chars (l i v e s)
                or re.search(r"[a-z][A-Z]", t)             # camel-concat (TotalRevenue / Servedover)
                or re.search(r"[A-Za-z],?\d{3}", t.replace(",", "")))  # letter glued to digits


def categorize(i, vt, st):
    if i in MISLABEL:
        return (0, "MISLABEL", "#c0392b",
                "REAL ERROR: the stated subject names a different row than the one this value sits in. Compare to the page.")
    if is_garble(vt) or is_garble(st):
        return (1, "PARSE GARBLE", "#b8860b",
                "Docling mangled the text (run-together / spaced). Check the number against the page — usually right, but confirm.")
    return (2, "CHECK MISS", "#777",
            "Check couldn't word-match the subject (synonym / acronym / year). Likely a correct metric — verify on the page.")


def e(x):
    return html.escape(str(x if x is not None else ""))


rows = []
for fl in flags:
    i = fl["id"]
    r = review.get(i)
    if not r:
        continue
    sha = r["sha8"]
    pg = r.get("v_page") or r.get("page") or 1
    safe = i.replace(":", "_")
    pdf = VIS + "/reviewbox/%s.pdf" % sha
    img = "parseview/%s.png" % safe
    if os.path.exists(pdf) and not os.path.exists(VIS + "/" + img):
        subprocess.run(["pdftoppm", "-png", "-r", "140", "-f", str(pg), "-l", str(pg),
                        "-singlefile", pdf, VIS + "/parseview/" + safe], check=False)
    rank, tag, color, hint = categorize(i, r.get("v_text"), r.get("s_text"))
    card = f"""<div class=card>
  <div class=meta>
    <div><span class=tag style="background:{color}">{tag}</span> <span class=id>{e(i)}</span> <span class=org>{e(r.get('org'))} &middot; p{pg}</span></div>
    <div class=hint>{e(hint)}</div>
    <div class=stmt>{e(r.get('statement'))}</div>
    <div class=lbl>stated subject</div><div class=subj><b>{e(r.get('subject'))}</b> = {e(r.get('value'))}</div>
    <div class=lbl>parsed VALUE marker</div><div class=parsed>{e(r.get('v_text'))}</div>
    <div class=lbl>parsed SUBJECT marker</div><div class=parsed>{e(r.get('s_text'))}</div>
    <div class=link><a href="{e(r.get('pdf'))}#page={pg}" target=_blank>open full PDF &rarr; p{pg}</a></div>
  </div>
  <div class=pageimg><a href="{img}" target=_blank><img src="{img}" loading=lazy></a></div>
</div>"""
    rows.append((rank, card))

rows.sort(key=lambda x: x[0])
counts = {}
for fl in flags:
    r = review.get(fl["id"])
    if r:
        _, tag, _, _ = categorize(fl["id"], r.get("v_text"), r.get("s_text"))
        counts[tag] = counts.get(tag, 0) + 1

page = f"""<!doctype html><meta charset=utf-8><title>Parse / flag viewer</title>
<style>
body{{font-family:system-ui,Arial;margin:0;background:#eee;color:#222}}
.hdr{{position:sticky;top:0;z-index:5;background:#222;color:#fff;padding:10px 16px;font-size:13px;line-height:1.5}}
.card{{background:#fff;margin:14px;border:1px solid #ccc;border-radius:8px;padding:14px;
      display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}}
.tag{{color:#fff;font-size:10px;font-weight:700;letter-spacing:.04em;padding:2px 7px;border-radius:4px}}
.id{{font-weight:700;font-size:14px}} .org{{color:#888;font-size:12px}}
.hint{{font-size:12px;color:#555;margin:6px 0 4px;font-style:italic}}
.stmt{{margin:8px 0;font-size:14px;line-height:1.35}}
.lbl{{color:#999;font-size:10px;letter-spacing:.05em;text-transform:uppercase;margin-top:10px}}
.subj{{font-size:14px}}
.parsed{{font-family:ui-monospace,Menlo,monospace;background:#fdf6e3;border:1px solid #e3dcb8;
        padding:7px;white-space:pre-wrap;word-break:break-word;font-size:12px;border-radius:4px}}
.link{{margin-top:10px;font-size:13px}}
.pageimg img{{max-width:100%;border:1px solid #bbb;border-radius:4px}}
</style>
<div class=hdr><b>Subject-fidelity flags, sorted by what they actually are.</b><br>
<span class=tag style="background:#c0392b">MISLABEL</span> {counts.get('MISLABEL',0)} real errors (page row &ne; stated subject) &nbsp;
<span class=tag style="background:#b8860b">PARSE GARBLE</span> {counts.get('PARSE GARBLE',0)} (Docling mangled the text) &nbsp;
<span class=tag style="background:#777">CHECK MISS</span> {counts.get('CHECK MISS',0)} (check word-match miss, likely fine).
Tags are my best guess &mdash; trust your eyes over them.</div>
{''.join(c for _, c in rows)}
"""
open(VIS + "/parse-flags.html", "w").write(page)
print(f"wrote parse-flags.html, {len(rows)} cards. counts: {counts}")
