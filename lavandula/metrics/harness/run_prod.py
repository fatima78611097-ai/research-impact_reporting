"""Prod harness — wires the corpus through the SAME core into RDS (lava_impact).

    prod_input (corpus)  ->  core: prepare -> extract -> normalize -> gate  ->  prod_output (RDS upsert)

Identical shape to run_dev; only the two adapters differ. That sameness is the guarantee:
dev and prod produce identical gate decisions because they call the identical core.

    python3 -m lavandula.metrics.harness.run_prod --limit 25 --dry-run   # no writes; prove parity vs dev
    python3 -m lavandula.metrics.harness.run_prod --limit 100            # real run -> lava_impact.metrics
"""
from __future__ import annotations

import argparse

import httpx

from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from lavandula.metrics.core import extract as ex
from lavandula.metrics.core import normalize as nz
from lavandula.metrics.core.gate import gate_all
from lavandula.metrics.adapters import prod_input, prod_output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap docs (0 = all eligible)")
    ap.add_argument("--shas", nargs="*", help="restrict to specific content_sha256 docs")
    ap.add_argument("--dry-run", action="store_true", help="run the full pipeline but write nothing")
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    api_key = get_secret("lavandula/deepseek/api_key")
    eng = make_app_engine()

    with httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=180) as client, eng.begin() as conn:
        docs = prod_input.load_corpus(conn, limit=args.limit or None, shas=args.shas)  # INPUT
        run_id = None if args.dry_run else prod_output.start_run(conn, "extract", args.note)
        total, errors = 0, []
        for i, (sha, org) in enumerate(docs):
            try:
                tagged, idmap = ex.prepare(conn, sha)                    # CORE
                raw = ex.extract(tagged, api_key, client)
                metrics = nz.normalize(raw, idmap, content_sha256=sha, org=org)
                gate_all(metrics)
                n = prod_output.commit(conn, metrics, run_id or 0, write=not args.dry_run)  # OUTPUT
                total += n
                print(f"  [{i+1}/{len(docs)}] {sha[:8]} {str(org)[:24]:24} +{n}", flush=True)
            except Exception as e:
                errors.append((sha[:8], f"{type(e).__name__}: {str(e)[:60]}"))
                print(f"  [{i+1}/{len(docs)}] {sha[:8]} SKIP {type(e).__name__}", flush=True)
        if not args.dry_run:
            prod_output.finish_run(conn, run_id, len(docs) - len(errors), total)
    tag = "DRY-RUN (no writes)" if args.dry_run else f"run_id={run_id}"
    print(f"DONE {tag}: {total} metrics over {len(docs)-len(errors)} docs, {len(errors)} skipped", flush=True)
    for sha8, err in errors:
        print(f"   skipped {sha8}: {err}", flush=True)


if __name__ == "__main__":
    main()
