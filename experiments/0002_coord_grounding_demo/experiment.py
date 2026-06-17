"""Phase -1(a): run the coord-grounded chain on 10 docs that already have coordinates.
Reuses comp-metric-regression/run_doc.py end-to-end. Saves per-doc records + summary.
"""
import json, sys, time

ROOT = "/home/ubuntu/research"
CMR = ROOT + "/locard/operations/comp-metric-regression"
sys.path.insert(0, ROOT)
sys.path.insert(0, CMR)
import run_doc  # noqa: E402
from lavandula.common.db import make_app_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

OUT = ROOT + "/experiments/0002_coord_grounding_demo/data/output/demo_results.json"
DOCS = ["e6696bd9", "f92248ef", "865fb62f", "88bfa5f4", "e31d17df",
        "c1a9f80b", "ee33d0e3", "efd61fea", "fb8f56bd", "f090f7af"]

eng = make_app_engine()
results = {}
with eng.connect() as conn:
    for sha8 in DOCS:
        full = conn.execute(text(
            "SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
            {"p": sha8 + "%"}).scalar()
        t0 = time.time()
        try:
            recs = run_doc.run_doc(conn, full)
        except Exception as e:
            results[sha8] = {"error": repr(e)[:300]}
            print(sha8, "ERROR", repr(e)[:120], flush=True)
            continue
        recs = [r for r in recs if not r.get("doc_excluded")]
        pub = [r for r in recs if r.get("decision") == "publish"]
        qn = [r for r in recs if r.get("decision") == "quarantine"]
        results[sha8] = {"full": full, "n": len(recs), "publish": len(pub),
                         "quarantine": len(qn), "secs": round(time.time() - t0, 1),
                         "records": recs}
        print(f"{sha8}: {len(recs)} -> {len(pub)} pub / {len(qn)} quar ({results[sha8]['secs']}s)", flush=True)

json.dump(results, open(OUT, "w"), indent=1, default=str)
tot = sum(r.get("n", 0) for r in results.values() if "n" in r)
tp = sum(r.get("publish", 0) for r in results.values() if "publish" in r)
ok = sum(1 for r in results.values() if "n" in r)
print(f"\nTOTAL: {ok}/{len(DOCS)} docs ran; {tot} metrics -> {tp} publish / {tot - tp} quarantine")
print("wrote", OUT)
