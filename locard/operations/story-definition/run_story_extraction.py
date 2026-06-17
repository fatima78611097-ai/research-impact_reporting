"""Story extraction run 1 — stage-1 prompt over the 165-doc dev corpus.

Reuses the metric pipeline's plumbing: render_tagged (tagged text + idmap) + DeepSeek (temp 0).
Per doc: tagged text -> stage-1 prompt -> JSON story records (classification, span markers,
structured fields, confidence). Saves raw output per doc; deterministic verbatim/span validation
happens separately. No DB writes.
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret

B = "/home/ubuntu/research/locard/operations/"
PROMPT = open(B + "story-definition/story-extract-prompt.v1.txt").read()
RES = {"batch1-clean": B + "comp-metric-regression/scale100-results-tuned.json",
       "test2": B + "comp-metric-regression/scale-test2-results.json"}
KEY = get_secret("lavandula/deepseek/api_key")
OUT = B + "story-definition/story-extraction-run1.json"

SHAS = sorted({s for p in RES.values() for s in json.load(open(p))})


def chat(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 4000,
                       "messages": [{"role": "system", "content": PROMPT},
                                    {"role": "user", "content": user}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            out = json.loads(urllib.request.urlopen(req, timeout=240).read())["choices"][0]["message"]["content"].strip()
            if out.startswith("```"):
                out = out.split("```")[1].lstrip("json").strip()
            return json.loads(out)
        except Exception as e:
            if attempt == 2:
                return {"error": str(e)[:200]}
            time.sleep(5)


eng = make_app_engine()
docdata = {}
with eng.connect() as conn:
    for sha in SHAS:
        try:
            _, tagged, _ = render.render_tagged(conn, sha)
            docdata[sha] = tagged
        except Exception as e:
            docdata[sha] = None
print(f"prefetched {sum(1 for v in docdata.values() if v)} / {len(SHAS)} docs", flush=True)


def process(sha):
    tagged = docdata.get(sha)
    if not tagged:
        return sha, {"error": "no_parse"}
    return sha, chat(tagged)


t0 = time.time()
results = {}
with ThreadPoolExecutor(max_workers=6) as ex:
    for n, (sha, recs) in enumerate(ex.map(process, SHAS), 1):
        results[sha] = recs
        if n % 20 == 0:
            print(f"  {n}/{len(SHAS)} ({(time.time()-t0)/60:.1f}m)", flush=True)

json.dump(results, open(OUT, "w"), indent=1)
import collections
cls = collections.Counter()
errs = 0
for sha, recs in results.items():
    if isinstance(recs, dict) and "error" in recs:
        errs += 1
        continue
    for r in recs if isinstance(recs, list) else []:
        cls[r.get("classification", "?")] += 1
print(f"\nDONE {len(SHAS)} docs in {(time.time()-t0)/60:.0f}m | errors: {errs}")
print("class distribution:", dict(cls.most_common()))
print(f"saved {OUT}")
