# Lavandula — System Map (housekeeping, IN PROGRESS)

**Branch:** `repo-housekeeping`. **Method:** derived from ground truth (systemd, `STAGE_REGISTRY`, RDS `information_schema`, import-graph reachability), not memory. Every line is marked **[V]** verified-from-source or **[P]** pending. Canonical-vs-dead uses the two-source rule + `import_graph.py` reachability; "dead" is never asserted without verifying dynamic/string imports.

> Status: **verified [V]** — pipeline backbone, module map, data layer, processes, control panel (67 routes), dead-code analysis, scratch census, and platform/infra. **Remaining pending:** per-subsystem file-level detail, full categorization of the ~50 comp-metric scratch scripts, and the classifier v1-vs-v3 deprecation check.

---

## 1. System at a glance — three layers

1. **Data pipeline** — 13 orchestrated stages, `seed → parse`. **[V]**  *Metric extraction is NOT in the orchestrated graph — it's hand-run.* **[V]**
2. **Orchestration & control** — the Django **`pipeline`** app: orchestrator + scheduler + workers + a 67-route control panel. **[V]**
3. **Platform** — Django/gunicorn on EC2 (`cloud2`), **RDS `lava_prod1`** (IAM-token auth), SSM secrets, S3, nginx, Tailscale. **[V]**

---

## 2. The orchestrated pipeline — VERIFIED from `pipeline/stages.py` `STAGE_REGISTRY` **[V]**

| # | stage | canonical command / module | status column |
|---|---|---|---|
| 1 | seed | `nonprofits.tools.seed_enumerate` | `seed_status` |
| 2 | resolve | `nonprofits.tools.pipeline_resolve` → lib `nonprofits.pipeline_resolver` | `resolve_status` |
| 3 | crawl | `reports.crawler` (delegates to `reports.async_crawler`) | `crawl_status` |
| 4 | classify | `nonprofits.tools.pipeline_classify` → lib `nonprofits.pipeline_classify` | `classify_status` |
| 5 | 990-index | `manage.py load_990_index` | `filing_990_status` |
| 6 | 990-parse | `manage.py process_990_auto` | — |
| 7 | enrich-phone | `nonprofits.tools.pipeline_enrich_phone` | — |
| 8 | extract-context | `manage.py extract_classification_context` | — |
| 9 | reclassify | `manage.py reclassify_corpus` | — |
| 10 | compare-classify | `manage.py compare_classifications` | — |
| 11 | resolve-disagree | `manage.py resolve_disagreements` | — |
| 12 | promote-classify | `manage.py promote_classification_run` | — |
| 13 | parse | `manage.py parse_documents` | — |

**Ends at `parse`.** `extract_metrics` / `llm_extract` / `extract_terms` / `score_keyness` exist as management commands but **no stage runs them** → metric extraction is hand-run. **[V]**
A cron job runs `load_990_index → process_990_auto` on a schedule (Feb/Mar, day 8–15, 3am). **[V]**

---

## 3. Module map — `lavandula/` **[V for roles; P for full file detail]**

| module | .py | role |
|---|---|---|
| `common/` | 12 | shared — DB engine (`make_app_engine` → RDS), secrets (`get_secret` → SSM) |
| `nonprofits/` | 52 | **seed · resolve · classify · 990 · enrich** (in `tools/`); resolver+classify libs at top level |
| `reports/` | 112 | **crawl / discover / fetch** (sync + async, intertwined) + report acquisition. ⚠️ 3 dead files (below) |
| `parse/` | 20 | **parse** (Docling) |
| `nlp/` | 36 | **metric extraction** (hand-run): the sharpened prompt (`llm_extract.py`), gate (`slot_render.py`), markers, `gate*.py` |
| `faithfulness/` | 15 | grounding / faithfulness checks |
| `dashboard/` | 98 | Django app — `pipeline` = orchestration + control panel; `dashboard/` = settings/wsgi/pg_iam_backend |

Orchestration cluster (in `dashboard/pipeline/`): `orchestrator, stages, scheduler, scheduler_config, process_manager, job_stats, protocol_reader, provenance, routers` — **all verified live** (imported via `pipeline.*` alias). **[V]**

---

## 4. Data layer — RDS `lava_prod1`, 6 schemas / 59 tables **[V]**

| schema | tables (n) | subsystem |
|---|---|---|
| `lava_corpus` | 17 | seed (`nonprofits_seed`), orgs (`nonprofits`), crawl (`crawled_orgs`,`fetch_log`), classify (`classification_*`), 990 (`filing_index`,`filing_status_audit`), people |
| `lava_parse` | 9 | parse (`documents`,`pages`,`sections`,`tables`,`pdftotext`) + worker queue (`work_queue`,`worker_heartbeats`) |
| `lava_vocab` | 14 | metrics/terms (`llm_metrics`,`published_metrics`,`gate_runs`,`gate_review`,`tfidf_scores`,`cvalue_terms`,`archetypes`) |
| `lava_dashboard` | 17 | Django auth/admin + orchestration (`jobs`,`job_events`,`workers`,`pipeline_config`,`pipeline_processes`,`host_commands`) |
| `lava_pipeline` | 1 | `org_provenance` |
| `public` | 1 | — |

**Local Postgres** (127.0.0.1:5432) holds only `scratch_0069v_*` — throwaway scratch DBs from the 0069 builder, **NOT production**. Cleanup candidate. **[V]**

---

## 5. Live processes / services **[V]**

- `lavandula-dashboard` (gunicorn ×2) · `lavandula-orchestrator` (`run_orchestrator`) · `metric-review` (`review_server.py`) · nginx · local postgres (scratch) · tailscale
- **Stale / cleanup:** `vl2_eval_server.py` running 15 days (leftover research server)
- **Out of scope** (confirmed): `etsy-*` services (separate product), agent-farm `node` servers (dev tooling), `aws_dev` (a host name; zero repo references)

---

## 5b. Control panel — the 67-route Dashboard surface **[V]** (`pipeline/urls.py` + `views.py`)

- **Per-stage run UIs** (each = a panel + a `/queue` action to enqueue a job): `seeder · resolver · crawler · classifier · classifier-v3 (status/queue/state-grid/log) · phone-enrich · parse (progress/stop/quarantine) · 990-index · 990-parse · llm-extract (queue/status/stop) · faithfulness (verify/status/pdftotext-backfill)`
- **Job management** (`/jobs`): list · detail · cancel · retry · progress · log
- **Workers** (`/workers`): list · edit
- **Data views**: `/orgs` (+detail, +qa) · `/reports` (+detail, download, pdf, qa) · `/provenance` · `/stats`
- **Orchestration control** (`/control` — 12 routes): queue pause/resume · bulk cancel/retry · clear-queue · health (fix-stale, release-locks) · **multi-host command/status**

Notes: **`llm-extract` and `faithfulness` HAVE dashboard UIs** (queue/status/stop) — so metric extraction is operator-runnable from the panel, just not in `STAGE_REGISTRY`. **Two classifier UIs** exist (`classifier` + `classifier-v3`) — v3 is the current reclassify path; whether v1 is still used or legacy is a deprecation question to verify.

---

## 6. Dead code & version-confusion — VERIFIED via `import_graph.py` **[V]**

**The core library is clean.** Of 347 lavandula modules, only **3 genuinely orphaned** (0 importers, not entry/framework):
- `lavandula/reports/report.py`
- `lavandula/reports/catalogue.py`
- `lavandula/reports/schema.py`
(plus `dashboard/pipeline/tests.py`, an empty Django stub.) — **but "orphaned" ≠ junk:** these are *unwired features* (coverage report, corpus retention, a shim), see §8.

**The "version minefield" was largely a misread** — verified as entry+library structure, not duplicates:
| looked like | actually is |
|---|---|
| 5 resolver files | `pipeline_resolve` (stage entry) + `pipeline_resolver` (live lib, in-degree 7) + helpers; `batch_resolve`/`cli_resolve` are standalone CLI scripts |
| crawler sync vs async | `crawler.py` *imports* `async_crawler` — intertwined, both live |
| duplicate `pipeline_classify` | `tools/` = stage entry, top-level = live lib (in-degree 3) |

**The real mess — censused [V]:**
- **`spikes/0064/` = 5.0 GB**, almost entirely **duplicated, regenerable PDFs + page images** under `eval_set/vision/*_img/` (the *same* source PDFs copied into ~6 separate review-image dirs; gitignored, so disk-only). → delete (regenerable from S3).
- **`operations/` = 450 MB / 932 JSON run-artifacts:** `p20-regroup-test/` (341 MB, **857 JSON**), `story-definition/` (76 MB), `comp-metric-regression/` (32 MB, 45 JSON). Mostly throwaway run outputs.
- **`comp-metric-regression/` = ~55 one-off research scripts.** Keepers: `review_server.py` (⚠️ **LIVE — it's `metric-review.service`, misplaced in a research dir; should be relocated to real code**), `regroup.py` (validated geometry method), `frozen/composed-baseline-2026-06-14/` (deliberate baseline), `fixtures/`. The other ~50 (`build_*`, `review_*`, one-off checks) → archive.
- Plus `.builders/` worktree duplicates and **4 nested venvs** (`reports/venv`, `nonprofits/venv`, root, `.builders/.../venv`).

**Headline:** the disk + file sprawl is ~5.4 GB and ~2,800 scratch files, but it's **regenerable artifacts + one-off scripts**, not load-bearing code. One genuine structural fix surfaced: a **live service (`review_server.py`) lives in a scratch dir**.

---

## 7. PENDING — next probes

- [ ] 67-route control panel → views → features map (`pipeline/urls.py` + `views.py`)
- [ ] Per-subsystem file-level detail (which files implement each stage)
- [ ] **Scratch-dir census** — `locard/operations`, `spikes`, `experiments` (the bulk of the cleanup)
- [ ] Infra layer — SSM parameter list, deploy scripts, systemd units, `gunicorn.conf.py`, IAM/RDS connectivity

---

## 7b. Platform / infrastructure **[V]**

- **Host & web:** EC2 `cloud2` (172.31.35.76), Ubuntu. nginx (443, `cloud2.lavandulagroup.com`) → gunicorn (`127.0.0.1:8000`, 2 workers, timeout 120) → Django (`dashboard.wsgi`).
- **systemd services:** `lavandula-dashboard` (gunicorn) · `lavandula-orchestrator` (`run_orchestrator`) · `metric-review` (`review_server.py` — ⚠️ runs from the scratch dir) · plus nginx, tailscale, local-pg (scratch).
- **Database:** RDS `lava_prod1` (us-east-1). Django authenticates with **IAM tokens** (`dashboard.pg_iam_backend`, `sslmode=require`); scripts use the `research_app` user. Reached over the **private VPC IP**.
- **Secrets (SSM, via `get_secret`):** `rds-endpoint/port/database/schema`, `django-secret-key`, `lavandula/deepseek/api_key`, `brave-api-key`, `serpex-api-key`.
- **External APIs:** DeepSeek (metric extraction), **Brave + Serpex** (URL resolve / search), Gemini (vision — research scripts).
- **Deploy:** ⚠️ **no automated deploy/IaC script** — manual, runbook-only (`locard/operations/0058-deploy-runbook.md`). A genuine handoff gap.
- **Networking:** Tailscale for cross-host reach.

## 8. Decisions (operator-confirmed 2026-06-18)

- **Metric extraction → wire into orchestrator + dashboard next iteration** (a planned TODO, not a permanent gap). After metrics, a **seed→crawl refactor** toward a search-engine model + website HTML index + update engine. **[planned]**
- **Cleanup policy — two-stage:** regenerable junk (5 GB duplicated images, local-PG scratch DBs, stale `vl2_eval_server`, nested venvs, `.builders/`) → **delete directly** (regenerates). Deprecated *code/scripts* → **Stage 1** git snapshot tag `pre-housekeeping-<date>` (recoverable forever), **Stage 2** delete from tree; drop the tag after the metric+crawl refactors land (or 90 days). **[approved]**
- **The 3 "dead" `reports/` files are orphaned *features*, not junk** (read 2026-06-18): `schema.py` = vestigial Spec-0017 shim → **drop**; `report.py` = coverage-report generator (unwired) → keep/decide; `catalogue.py` = corpus deletion/retention logic (unwired) → **lean keep** (real capability).

## 9. Pending probes (next)
- ~~67-route control-panel map~~ ✅ (§5b) · per-subsystem file detail · full scratch-script categorization (the ~50 comp-metric one-off scripts) · ~~infra layer~~ ✅ (§7b) · classifier v1-vs-v3 deprecation check
