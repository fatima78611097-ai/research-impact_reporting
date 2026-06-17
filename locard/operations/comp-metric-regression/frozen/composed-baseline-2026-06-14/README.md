# FROZEN BASELINE — composed-sentence pipeline (2026-06-14)

Snapshot of the metric pipeline as it stands the day we decided to test the
**slot-render** alternative (extract slots with the LLM, render the display
sentence deterministically instead of letting the LLM compose it).

This is the **composed-sentence** version — the LLM writes the metric sentence.
Frozen so we can return to it and build on it if the slot-render test doesn't win.

## State at freeze
- Canonical set: 1,606 metrics (~1,327 publish / 279 quarantine after applied fixes)
- Error rate on operator-checked set: ~0.9% bad metrics still published (4/428)
- Fixes caught 62 of 66 operator-flagged false-publishes
- Vision fixture: 1,605 metrics; test_suite.py = acceptance contract

## What's here
- `data/` — extraction outputs (scale-test2, scale100, scale100-tuned), review-data.json
  (canonical decisions), review.db (operator hand-marks), fixtures/
- `code/` — the gate stack (gate, pipeline, regroup, lowvalue_gate, item1 rules,
  measurable_value_check, fidelity_l1, faithfulness_route, test_suite, score_pipeline, render)
- `prompts/` — v-loc1, KNOWN-GOOD (sha b51dc96a), grounding v1
- `docs/` — manifest, requirements, review log, pipeline flowchart

## To restore
Copy files back to their working locations (code → comp-metric-regression/,
data/review-data.json → spikes/0064/eval_set/vision/, etc.). Nothing here was
modified by the slot-render test — that test only reads these and writes into
`../slot-render-spike/`.
