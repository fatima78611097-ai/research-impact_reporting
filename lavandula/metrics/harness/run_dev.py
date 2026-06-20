"""Dev harness — wires the sample pool through the core into the review viewer.

    input adapter (sample pool)  ->  core: prepare -> extract -> normalize -> gate  ->  output adapter (JSON + images)

This file only WIRES — no extraction/gating/decision logic lives here. The prod harness
(run_prod) is the same shape with the two adapters swapped. Same core, both paths.

    python3 -m lavandula.metrics.harness.run_dev --out <dir> [--limit N]
"""
from __future__ import annotations

import argparse
import os

import httpx

from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from lavandula.metrics.core import extract as ex
from lavandula.metrics.core import normalize as nz
from lavandula.metrics.core.gate import gate_all
from lavandula.metrics.adapters import dev_input, dev_output

DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "..", "_dev_run")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.abspath(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0, help="process only the first N pool docs (smoke)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    api_key = get_secret("lavandula/deepseek/api_key")
    eng = make_app_engine()

    with httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=180) as client, eng.connect() as conn:
        docs = dev_input.load_sample_pool(conn)              # INPUT adapter — which docs
        if args.limit:
            docs = docs[:args.limit]
        all_metrics, errors = [], []
        for i, (sha, org) in enumerate(docs):
            try:                                             # isolate per-doc: one bad doc must not kill the run
                tagged, idmap = ex.prepare(conn, sha)        # CORE
                raw = ex.extract(tagged, api_key, client)
                metrics = nz.normalize(raw, idmap, content_sha256=sha, org=org)
                gate_all(metrics)                            # empty registry (Phase 1) -> all publish
                all_metrics.extend(metrics)
                print(f"  [{i+1}/{len(docs)}] {sha[:8]} {str(org)[:24]:24} +{len(metrics)}", flush=True)
            except Exception as e:
                errors.append((sha[:8], f"{type(e).__name__}: {str(e)[:60]}"))
                print(f"  [{i+1}/{len(docs)}] {sha[:8]} SKIP {type(e).__name__}", flush=True)
        dev_output.write_review(all_metrics, conn, args.out)  # OUTPUT adapter — JSON + images
    pub = sum(1 for m in all_metrics if m.decision == "publish")
    print(f"DONE metrics={len(all_metrics)} ({pub} publish / {len(all_metrics)-pub} quarantine), "
          f"{len(errors)} doc(s) skipped -> {args.out}", flush=True)
    for sha8, err in errors:
        print(f"   skipped {sha8}: {err}", flush=True)


if __name__ == "__main__":
    main()
