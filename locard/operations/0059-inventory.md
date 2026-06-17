# Spec 0059 — Housecleaning Inventory (read-only, 2026-05-31)

MAINTAIN-protocol inventory. Verified what writes/reads each artifact before recommending removal. Nothing dropped here — table drops are operator-run DDL.

## 1. Stale `TestFetchWorkBatch` tests — FIXED (committed)
3 tests asserted the pre-0055 `fetch_work_batch(priority_list, …, retry_errors/reparse/min_version)` signature. That behavior moved to `fetch_work_batch_legacy` (still used by `worker.py:247`, the single-worker advisory-lock path); the name `fetch_work_batch` is now the SKIP-LOCKED queue claim `(conn, run_id, batch_size, worker_id)`. Repointed the tests at `fetch_work_batch_legacy` (NOT deleted — legacy path is live). Full parse+faithfulness suite: **365 passed / 0 failed**.

## 2. `lava_vocab.metric_observations` — DEPRECATE (operator DDL)
The statistical / market-basket "first attempt" per-doc metric extractor's output. Superseded by `llm_metrics` (the LLM extractor). Produced ~750 noisy observations/doc (a Library-of-Congress photo-catalog number became 5 "metrics").

**Inventory (verified):**
- **Rows:** 1,551,882 (vs `llm_metrics` 74,240 — the live replacement).
- **Writers:** ONLY `lavandula/dashboard/pipeline/management/commands/extract_metrics.py` (INSERT at line ~356). Not in STAGE_REGISTRY → cannot be triggered by the orchestrator/dashboard, only a manual `manage.py extract_metrics`.
- **Readers:** NONE — no `.py`/`.html`/`.sql` outside `extract_metrics.py` references `metric_observations` (only the creating migration `003_metric_observations.sql`).
- **Created by:** `lavandula/migrations/lava_vocab/003_metric_observations.sql`.

**KEEP (do NOT touch):** the statistical *discovery* pipeline — `extract_terms` / `discover_archetypes` / `score_keyness` and `lava_vocab.archetypes` — still the right tool for vocabulary/archetype discovery (discovery-vs-extraction lesson). `extract_metrics.py` *reads* `archetypes` for config but that's input only; removing it doesn't affect discovery.

**Recommended removal (2 steps):**
1. **Operator DDL (pgAdmin):** `DROP TABLE lava_vocab.metric_observations;` (+ revoke any grants). 1.55M junk rows reclaimed. See `0059-drop-metric-observations.sql`.
2. **Code (TICK, after the drop):** delete `extract_metrics.py` (the only writer, dead) and mark `003_metric_observations.sql` historical. Low risk — git-reversible, read by nothing.

## 3. Out of scope / deferred
- Legacy non-queue `_run_loop` worker path (worker.py) — KEEP; `fetch_work_batch_legacy` is still wired for single-worker runs. Not dead.
- Fixture extraction_runs (run 8 `fixture-bgcsm2`, run 11 `fixture-cancare-split`) — small; optional cleanup, low priority.
