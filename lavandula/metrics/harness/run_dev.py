"""Dev harness — sample pool through the core into the review viewer.

    input (sample pool) -> core: prepare -> extract -> normalize -> [FREEZE] -> gate -> output (JSON + images)

The extraction is FROZEN to extraction_cache.json. By DEFAULT, if a frozen cache exists this
RE-GATES it (no DeepSeek, SAME metric ids/order) so review scores survive a check change. Only
--reextract re-runs extraction and overwrites the cache (the deliberate prompt-change case).

    python3 -m lavandula.metrics.harness.run_dev               # re-gate the frozen set (default; scores safe)
    python3 -m lavandula.metrics.harness.run_dev --reextract   # re-extract + re-freeze (reshuffles metrics)
    python3 -m lavandula.metrics.harness.run_dev --limit N      # (with --reextract) cap docs for a smoke run
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from lavandula.metrics.core import extract as ex
from lavandula.metrics.core import normalize as nz
from lavandula.metrics.core.gate import gate_all
from lavandula.metrics.core.serialize import metric_to_dict, metric_from_dict
from lavandula.metrics.adapters import dev_input, dev_output

DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "..", "_dev_run")        # regenerable viewer output (gitignored)
FROZEN = os.path.join(os.path.dirname(__file__), "..", "frozen", "extraction.json")  # durable, committed


def _extract_and_freeze(conn, client, api_key, limit):
    """Run extraction over the sample pool, FREEZE the pre-gate metrics to FROZEN, return them."""
    docs = dev_input.load_sample_pool(conn)
    if limit:
        docs = docs[:limit]
    metrics, errors = [], []
    for i, (sha, org) in enumerate(docs):
        try:
            tagged, idmap = ex.prepare(conn, sha)
            raw = ex.extract(tagged, api_key, client)
            ms = nz.normalize(raw, idmap, content_sha256=sha, org=org)
            metrics.extend(ms)
            print(f"  [{i+1}/{len(docs)}] {sha[:8]} {str(org)[:24]:24} +{len(ms)}", flush=True)
        except Exception as e:
            errors.append((sha[:8], f"{type(e).__name__}: {str(e)[:60]}"))
            print(f"  [{i+1}/{len(docs)}] {sha[:8]} SKIP {type(e).__name__}", flush=True)
    os.makedirs(os.path.dirname(FROZEN), exist_ok=True)
    json.dump([metric_to_dict(m) for m in metrics], open(FROZEN, "w"), default=str)
    print(f"FROZE {len(metrics)} metrics -> {os.path.relpath(FROZEN)}", flush=True)
    return metrics, errors


def _load_frozen():
    return [metric_from_dict(d) for d in json.load(open(FROZEN))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.abspath(DEFAULT_OUT))
    ap.add_argument("--reextract", action="store_true",
                    help="re-run extraction and overwrite the frozen cache (reshuffles metrics; only when the PROMPT changed)")
    ap.add_argument("--limit", type=int, default=0, help="cap docs (only meaningful with --reextract)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    eng = make_app_engine()
    errors = []
    if args.reextract or not os.path.exists(FROZEN):
        if not args.reextract:
            print("no frozen extraction found — extracting once and freezing it.", flush=True)
        api_key = get_secret("lavandula/deepseek/api_key")
        with httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=180) as client, eng.connect() as conn:
            metrics, errors = _extract_and_freeze(conn, client, api_key, args.limit)
    else:
        metrics = _load_frozen()                               # RE-GATE the frozen set — no DeepSeek, stable ids
        print(f"re-gating frozen extraction ({len(metrics)} metrics) — scores preserved. "
              f"(use --reextract to re-extract)", flush=True)

    # gate per document (dedup is cross-metric within a doc)
    from collections import defaultdict
    bydoc = defaultdict(list)
    for m in metrics:
        bydoc[m.content_sha256].append(m)
    for ms in bydoc.values():
        ms.sort(key=lambda x: x.idx)
        gate_all(ms)

    with eng.connect() as conn:
        dev_output.write_review(metrics, conn, args.out)
    pub = sum(1 for m in metrics if m.decision == "publish")
    print(f"DONE metrics={len(metrics)} ({pub} publish / {len(metrics)-pub} quarantine) -> {args.out}", flush=True)
    for sha8, err in errors:
        print(f"   skipped {sha8}: {err}", flush=True)


if __name__ == "__main__":
    main()
