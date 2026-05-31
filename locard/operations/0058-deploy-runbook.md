# Spec 0058 — Deploy Runbook (Phase 6, operator-run)

Migration-BEFORE-tarball gate (same as 0056). Never deploy mid-run — check no
parse job is active first (`af`/dashboard, or `SELECT … FROM lava_parse.parse_runs
WHERE finished_at IS NULL`).

## 0. Pre-flight
- [ ] No active parse run (don't restart the orchestrator with jobs running).
- [ ] Phase-0 spike verdict recorded (`locard/spikes/0058/RESULTS.md`) → subprocess
      path shipped. `PARSE_TIMEOUT_SECONDS=180`, child `RLIMIT_AS≈14 GB` (config.py).

## 1. Apply migrations FIRST (psql as the master/DDL role)
```bash
psql "$RDS_URL" -f lavandula/migrations/parse/0058_parse_outcome.sql
psql "$RDS_URL" -f lavandula/migrations/parse/0058_parse_blocklist.sql
```
Verify:
```sql
\d lava_parse.documents      -- has docling_convert_ms, parse_outcome
\d lava_parse.parse_blocklist -- exists; docling_writer has SELECT, research_app full
```

## 2. Quarantine the known poison doc
Resolve the full sha and quarantine it (it SEGFAULTS Docling's pdf_parsers.so):
```bash
# resolve:
psql "$RDS_URL" -c "SELECT content_sha256 FROM lava_corpus.corpus WHERE content_sha256 LIKE 'e038a9e75317ff86%';"
# quarantine (Django side, research_app):
python manage.py quarantine_parse_doc --sha e038a9e75317ff86 \
    --reason "segfaults docling pdf_parsers.so (exit 139)" --by operator \
    --evidence '{"spike":"0058","exit_code":139,"reproduced":true}'
python manage.py quarantine_parse_doc --list   # confirm
```

## 3. Rebuild + deploy the worker tarball (bundles the NUL `_scrub` fix)
```bash
bash locard/operations/0058-build-worker-tarball.sh --upload
```
This backs up the current `deploy/worker-code.tar.gz` to `deploy/backups/` before
overwriting (never delete the old tarball until the new run is confirmed writing).

## 4. Smoke test — re-run the run-32 set (283 P-cleanup docs)
Launch a single g6 worker on the run-32 doc set. **Watch for, in order:**
- [ ] **Worker starts + parses normal docs** — confirms the child inits CUDA under
      `RLIMIT_AS=14 GB`. **If the worker can't start / CUDA fails to init, the
      RLIMIT_AS is too low** (CUDA reserves large virtual address space). Raise
      `PARSE_CHILD_RLIMIT_AS_BYTES` (e.g. 20–24 GB) or set it to `None` to disable,
      rebuild, redeploy. (See `parse_runner._apply_rlimit`.)
- [ ] **Poison doc `e038a9e75317ff86`** is QUARANTINED → never enqueued. If you
      intentionally un-quarantine it to test: it must record `parse_crash` (clean
      child death + respawn), NOT wedge the worker.
- [ ] **NUL doc `359f55959df4e637`** parses successfully (NUL `_scrub` fix in prod).
- [ ] Run completes (or ends with honest per-doc outcomes); no worker wedge.
- [ ] Dashboard parse page shows the **parse_outcome rollup** (ok/downgraded/
      timeout/error) and the **Quarantined Docs** panel.

## 5. A/B quality gate (before any pipeline-tuning knob ships)
TableFormer FAST + the images_scale cap stay OFF in config until the §4 A/B passes:
```bash
# Fill locard/operations/0058-ab-sample.json with real shas first.
python -m lavandula.parse.ab_quality --host "$RDS_HOST" --database "$RDS_DB"
# Record verdict in locard/operations/0058-ab-results.md, then flip the passing
# knobs in lavandula/parse/config.py (TABLEFORMER_FAST, IMAGES_SCALE_CAP), rebuild,
# redeploy. The triage/timeout/quarantine defenses do NOT depend on the A/B and
# ship now.
```

## Rollback
- Tarball: `aws s3 cp s3://…/deploy/backups/worker-code-<stamp>.tar.gz s3://…/deploy/worker-code.tar.gz`
- Migrations: rollback blocks at the bottom of each .sql (drop the added columns / table).
- Un-quarantine: `python manage.py quarantine_parse_doc --sha <full> --remove`.
