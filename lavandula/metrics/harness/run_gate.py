"""Gate harness (the gate-metrics stage) — re-gate stored metrics WITHOUT re-extracting.

    load stored metrics (lava_impact)  ->  core: gate  ->  update gate columns in place

This is the payoff of the two-stage split: change a check, re-run THIS over a run, and
every stored metric gets the new decision — no DeepSeek calls, cheap. Same core gate as
extract-metrics and the dev harness.

    python3 -m lavandula.metrics.harness.run_gate --run-id 5
"""
from __future__ import annotations

import argparse

from lavandula.common.db import make_app_engine
from lavandula.metrics.core.gate import gate_all
from lavandula.metrics.adapters import prod_output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True, help="the extract run whose metrics to re-gate")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    eng = make_app_engine()
    with eng.begin() as conn:
        metrics = prod_output.load_for_regate(conn, args.run_id)
        # gate per document (dedup is cross-metric within a doc)
        from collections import defaultdict
        bydoc = defaultdict(list)
        for m in metrics:
            bydoc[m.content_sha256].append(m)
        for ms in bydoc.values():
            ms.sort(key=lambda x: x.idx)
            gate_all(ms)
        pub = sum(1 for m in metrics if m.decision == "publish")
        if args.dry_run:
            print(f"DRY-RUN: would re-gate {len(metrics)} metrics -> {pub} publish / {len(metrics)-pub} quarantine")
            return
        gate_run_id = prod_output.start_run(conn, "gate", args.note)
        n = prod_output.update_decisions(conn, metrics, args.run_id, gate_run_id)
        prod_output.finish_run(conn, gate_run_id, len(bydoc), n)
        print(f"DONE gate_run_id={gate_run_id}: re-gated {n} metrics -> {pub} publish / {len(metrics)-pub} quarantine")


if __name__ == "__main__":
    main()
