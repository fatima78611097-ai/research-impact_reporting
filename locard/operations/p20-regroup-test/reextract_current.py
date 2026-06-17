"""Re-extract the 600 sample docs with the CURRENT prompt (May-30 verbatim _METRICS_PROMPT),
production text path (lava_parse.sections joined, 60k cap). Writes new-metrics-current.json
in the same shape the gate expects. No DB writes.
"""
import json, sys, os, httpx
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 600


def get_text(conn, sha):
    rows = conn.execute(text(
        "SELECT heading, body_text FROM lava_parse.sections WHERE content_sha256=:s "
        "ORDER BY section_index"), {"s": sha}).fetchall()
    parts = []
    for h, b in rows:
        if h:
            parts.append(h)
        if b:
            parts.append(b)
    return "\n\n".join(parts)[:60000]


def main():
    man = json.load(open(f"{BASE}/sample-manifest.json"))[:LIMIT]
    dkey = get_secret("lavandula/deepseek/api_key")
    eng = make_app_engine()
    client = httpx.Client(headers={"Authorization": f"Bearer {dkey}"})
    print(f"re-extracting {len(man)} docs with current _METRICS_PROMPT", flush=True)

    def work(d):
        sha = d["sha"]
        rec = {"sha": sha, "stratum": d["stratum"], "metrics": [], "err": None}
        try:
            with eng.connect() as conn:
                txt = get_text(conn, sha)
            if len(txt) < 100:
                rec["err"] = "short_text"; return rec
            res, _, _ = call_deepseek(dkey, txt, client, _METRICS_PROMPT)
            mets = res.get("metrics") if isinstance(res, dict) else res
            rec["metrics"] = mets if isinstance(mets, list) else []
        except Exception as e:
            rec["err"] = f"{type(e).__name__}:{str(e)[:60]}"
        return rec

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, r in enumerate(ex.map(work, man), 1):
            results.append(r)
            if i % 25 == 0:
                done = sum(len(x["metrics"]) for x in results)
                errs = sum(1 for x in results if x["err"])
                print(f"  {i}/{len(man)}  metrics so far {done}  errs {errs}", flush=True)

    json.dump(results, open(f"{BASE}/new-metrics-current.json", "w"))
    tot = sum(len(x["metrics"]) for x in results)
    errs = [x for x in results if x["err"]]
    print(f"\nDONE: {len(results)} docs, {tot} metrics, {len(errs)} errored")
    print(f"  avg metrics/doc: {tot/max(1,len(results)):.1f}")
    from collections import Counter
    print("  err kinds:", dict(Counter(x['err'].split(':')[0] for x in errs)))


if __name__ == "__main__":
    main()
