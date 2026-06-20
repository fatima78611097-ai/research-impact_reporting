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
**Phase 0 — scaffold + contract.** Core is the contract + an empty gate registry. No logic yet.
