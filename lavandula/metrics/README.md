# `lavandula.metrics` — the metric engine

One **core**, two thin **I/O adapters**. Built per `locard/operations/metric-engine-plan.md`.

```
   INPUT adapter            CORE (one source of truth)            OUTPUT adapter
 dev:  sample pool  ─┐                                          ┌─ dev:  JSON + viewer
                     ├─►  extract → normalize → gate(checks)  ──┤
 prod: corpus       ─┘     (model's text kept; checks only      └─ prod: RDS commit
                            flag/quarantine — NEVER rewrite)
```

**The one rule:** adapters do **I/O only — zero logic.** Every decision lives in `core/`.
If an adapter ever filters or grades, the two paths have forked. Dev and prod must differ
**only** at the two ends.

## Layout
- `core/types.py` — the `Metric` contract (what every adapter emits, what the gate reads). **Locked.**
- `core/extract.py` — prompt + DeepSeek → raw metrics. *(Phase 1)*
- `core/gate.py` — the check registry + runner. First quarantine wins; flags accumulate. **Skeleton.**
- `core/checks/` — one pure, testable function per check. The whole gate lives here. *(Phase 2)*
- `adapters/{dev,prod}_{input,output}.py` — move data in/out, nothing else. *(Phase 1 / 3)*
- `harness/run_{dev,prod}.py` — wire input → core → output. *(Phase 1 / 3)*
- `tests/fixtures/` — known-bad / known-good per check; the dev sample pool.

## How a change flows (the everyday loop)
1. Branch. Add/edit the check in `core/checks/` — **one place.**
2. Test via `harness/run_dev.py` on the sample pool → JSON → viewer; add the check's fixture.
3. Merge when satisfied. **Prod inherits it on the next run** — never a second edit.

You touch the prod path itself only for an **adapter** change (I/O), never for a gate.

## Status
**Phases 0–4 COMPLETE** (merged to master, verified end-to-end on live `lava_impact`):
0 contract+scaffold · 1 dev harness reproduces the run · 2 gate checks (all measured) ·
3a prod harness + `lava_impact` schema · 3b orchestrator stages (`extract-metrics`/`gate-metrics`) ·
4 v1 gate archived (tag `pre-v1-gate-archive-2026-06-20`), tests + duplication scanner standing.

**Checks** (`core/checks/`, each with a fixture in `tests/`): dedup · quality_text · quality_subject ·
vague_quantity · measurable · is_a_metric · incompleteness · mispairing(flag).

**Run it:**
```
python3 -m lavandula.metrics.harness.run_dev            # sample pool -> JSON + viewer (/metrics-review/)
python3 -m lavandula.metrics.harness.run_prod --limit N # corpus -> lava_impact.metrics
python3 -m lavandula.metrics.harness.run_gate --run-id R# re-gate a run in place (no DeepSeek)
python3 -m lavandula.metrics.tests.test_checks          # measured check fixtures
```

**Migration** (operator, pgAdmin, applied 2026-06-20): `migrations/001_create_lava_impact.sql`.

**Out of scope** (separate tracks, see `locard/operations/metric-quality-backlog.md`): vision **recovery**
of garbled-infographic numbers; model-judge semantic checks (org-vs-community, drift). The **stories**
artifact reuses this skeleton with its own core — `lava_impact` already welcomes it.
