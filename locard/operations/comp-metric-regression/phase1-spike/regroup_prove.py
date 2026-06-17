"""Prove Fable's coordinate-regroup on the spike's 100 docs (CPU, no GPU, no API).

Reuses the EXACT proven path: p20 parse_runner.make_converter('A') + extract_elements
(the parse that was vision-adjudicated at 92% recovery / ~50% flag), then regroup.pair_from_elements.
Cross-checks regroup's geometric label against the spike's DeepSeek labels per value:
  AGREE      regroup label shares distinctive words with the DeepSeek metric label
  FLAG       high-conf regroup label DISAGREES -> candidate mispairing (the catch)
  NOT_PAIRED regroup abstained / value not on a clean grid

Run with the p20 venv (has docling):
  locard/operations/p20-regroup-test/.venv/bin/python regroup_prove.py --limit 100
"""
import argparse, json, os, re, sys, time, tempfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
P20 = "/home/ubuntu/research/locard/operations/p20-regroup-test"
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
sys.path.insert(0, P20)
import boto3
from lavandula.parse import config as pc
import regroup
import parse_runner as pr  # reads argv at import but only make_converter/extract_elements used


def numkey(v):
    s = str(v or "").strip()
    try:
        f = float(s.replace(",", "").replace("$", "").replace("%", ""))
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return re.sub(r"[^\d]", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default=f"{HERE}/regroup-prove-result.json")
    args, _ = ap.parse_known_args()

    docset = json.load(open(f"{HERE}/docset.json"))[:args.limit]
    spike = {d["sha256"]: d for d in json.load(open(f"{HERE}/spike-result.json"))}
    conv = pr.make_converter("A")
    s3 = boto3.client("s3", region_name="us-east-1")
    tmp = tempfile.mkdtemp(prefix="rgprove_")

    counts = Counter()
    flags = []
    out_docs = []
    t0 = time.time()
    for i, doc in enumerate(docset, 1):
        sha, sha8 = doc["sha256"], doc["sha8"]
        rec = {"sha8": sha8, "type": doc.get("type"), "pairs": [], "agree": 0, "flag": 0,
               "not_paired": 0, "error": None}
        pdf = doc.get("local_pdf")
        tmp_pdf = None
        try:
            if not (pdf and os.path.exists(pdf)):
                tmp_pdf = f"{tmp}/{sha8}.pdf"
                s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", tmp_pdf)
                pdf = tmp_pdf
            els = pr.extract_elements(conv.convert(pdf).document)
            by_page = defaultdict(list)
            for e in els:
                by_page[e["page"]].append((e["text"], e["bbox"], e["origin"]))
            pairs = {}  # numkey -> (raw_value, label, page)
            for pg, raw in by_page.items():
                for n, d in regroup.pair_from_elements(regroup.normalize(raw)).items():
                    if d["confidence"] == "high" and d["label"]:
                        pairs.setdefault(numkey(n), (n, d["label"], pg))
            rec["pairs"] = [{"value": v[0], "label": v[1], "page": v[2]} for v in pairs.values()]
            # cross-check vs the spike's DeepSeek-derived labels (source both/docling)
            ds = [m for m in spike.get(sha, {}).get("metrics", []) if m["source"] in ("both", "docling")]
            for m in ds:
                vk = numkey(m["value"])
                if vk in pairs:
                    if regroup.labels_agree(pairs[vk][1], m["label"]):
                        rec["agree"] += 1; counts["AGREE"] += 1
                    else:
                        rec["flag"] += 1; counts["FLAG"] += 1
                        flags.append({"sha8": sha8, "value": str(m["value"]), "page": pairs[vk][2],
                                      "deepseek_label": (m["label"] or "")[:80],
                                      "regroup_label": pairs[vk][1][:80]})
                else:
                    rec["not_paired"] += 1; counts["NOT_PAIRED"] += 1
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            counts["ERROR"] += 1
        finally:
            if tmp_pdf and os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
        out_docs.append(rec)
        print(f"[{i}/{len(docset)}] {sha8} {rec['type']:11} pairs={len(rec['pairs'])} "
              f"agree={rec['agree']} flag={rec['flag']} {time.time()-t0:.0f}s "
              f"{'ERR:'+rec['error'] if rec['error'] else ''}", flush=True)

    json.dump({"counts": dict(counts), "flags": flags, "docs": out_docs},
              open(args.out, "w"), indent=1)
    print(f"\n=== REGROUP-ON-100 ({time.time()-t0:.0f}s) ===")
    print("cross-check vs DeepSeek labels:", dict(counts))
    tot = counts["AGREE"] + counts["FLAG"]
    if tot:
        print(f"flag rate (candidate mispairs): {counts['FLAG']}/{tot} = {100*counts['FLAG']/tot:.0f}%")
    print(f"sample flags ({min(12,len(flags))} of {len(flags)}):")
    for f in flags[:12]:
        print(f"  {f['sha8']} p{f['page']} {f['value']:>10} | deepseek={f['deepseek_label']!r}")
        print(f"             regroup={f['regroup_label']!r}")


if __name__ == "__main__":
    main()
