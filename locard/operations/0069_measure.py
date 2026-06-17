"""Phase-0 baseline measurement for Spec 0069 (operator-run, READ-ONLY).

Runs the gate as a dry run (write=False) over a 0068 marker run and reports the numbers
the operator needs to FREEZE before any production write:

  - publish / quarantine split + reason histogram + quarantine triage;
  - the prose-internal **de-dup rate** (how many publish-eligible metrics merge);
  - the **column-coherence (mispair) flag count** — run with mispair-detect ON vs OFF to
    isolate how many rows the column DETECT would quarantine, so the operator can
    hand-check a sample and measure the false-flag rate.

Decision rule (plan §0): ship the mispair column-detect iff its measured false-flag rate
<= 20%; else defer to 0070 and rely on subject-grounding + spot-review. This script
produces the COUNTS; the false-flag rate is a hand-check of the flagged sample, recorded
in 0069-baselines.md.

Usage:
    python3 locard/operations/0069_measure.py 0068-markers-2026-06-17
Nothing is written (no gate_runs row, no UPDATEs); the measurable-value LLM is NOT called
(measure_fn=None) so this is free and side-effect-free.
"""
import json
import sys

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from lavandula.nlp import gate_runner as gr


def main():
    run_tag = sys.argv[1] if len(sys.argv) > 1 else "0068-markers-2026-06-17"
    eng = make_app_engine()
    on = gr.run_gate(eng, run_tag, measure_fn=None, mispair_detect=True, write=False)
    off = gr.run_gate(eng, run_tag, measure_fn=None, mispair_detect=False, write=False)
    mispair_flagged = on["reason_histogram"].get("mispair", 0)
    dup = off["reason_histogram"].get("duplicate", 0)
    out = {
        "run_tag": run_tag,
        "metrics_total": on["metrics_total"],
        "with_mispair_detect": on,
        "without_mispair_detect": off,
        "mispair_flagged": mispair_flagged,
        "dedup_merged": dup,
        "note": "measure_fn=None -> small ints quarantine 'measure_unchecked'; a real run "
                "wires DeepSeek. Hand-check the mispair-flagged sample for false-flag rate.",
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
