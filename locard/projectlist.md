# Project List

Centralized tracking of all projects with status, priority, and dependencies.

> **Quick Reference**: See `locard/resources/workflow-reference.md` for stage diagrams and common commands.

## Project Lifecycle

Every project goes through stages. Not all projects reach completion:

**Active Lifecycle:**
1. **conceived** - Initial idea captured. Spec file may exist but is not yet approved. **AI agents must stop here after writing a spec.**
2. **specified** - Specification approved by human. **ONLY the human can mark a project as specified.**
3. **planned** - Implementation plan created (locard/plans/NNNN-name.md exists)
4. **implementing** - Actively being worked on (one or more phases in progress)
5. **implemented** - Code complete, tests passing, PR created and awaiting review
6. **committed** - PR merged to main branch
7. **integrated** - Merged to main, deployed to production, validated, reviewed (locard/reviews/NNNN-name.md exists), and **explicitly approved by project owner**. **ONLY the human can mark a project as integrated** - AI agents must never transition to this status on their own.

**Terminal States:**
- **abandoned** - Project canceled/rejected, will not be implemented (explain reason in notes)
- **on-hold** - Temporarily paused, may resume later (explain reason in notes)

## Format

```yaml
projects:
  - id: "NNNN"              # Four-digit project number
    title: "Brief title"
    summary: "One-sentence description of what this project does"
    status: specified|specified|planned|implementing|implemented|committed|integrated|abandoned|on-hold
    priority: high|medium|low
    files:
      spec: locard/specs/NNNN-name.md       # Required after "specified"
      plan: locard/plans/NNNN-name.md       # Required after "planned"
      review: locard/reviews/NNNN-name.md   # Required after "integrated"
    dependencies: []         # List of project IDs this depends on
    tags: []                # Categories (e.g., auth, billing, ui)
    notes: ""               # Optional notes about status or decisions
```

## Numbering Rules

1. **Sequential**: Use next available number (0001-9999)
2. **Reservation**: Add entry to this file FIRST before creating spec
3. **Renumbering**: If collision detected, newer project gets renumbered
4. **Gaps OK**: Deleted projects leave gaps (don't reuse numbers)

## Archiving Completed Projects

Once projects are `integrated` or `abandoned` for 3+ days, move them to `projectlist-archive.md`:

```
locard/
  projectlist.md          # Active projects (conceived → committed)
  projectlist-archive.md  # Completed projects (integrated, abandoned)
```

**Why archive?**
- Keeps daily work file small and fast
- Full history still versioned in git
- Can grep across both files when needed

**Archive format**: Same YAML format, sorted by ID (historical record).

## Usage Guidelines

### When to Add a Project

Add a project entry when:
- You have a concrete idea worth tracking
- The work is non-trivial (not just a bug fix or typo)
- You want to reserve a number before writing a spec

### Status Transitions

```
conceived → [HUMAN] → specified → planned → implementing → implemented → committed → [HUMAN] → integrated
     ↑                                                                                   ↑
Human approves                                                                    Human approves
   the spec                                                                      production deploy

Any status can transition to: abandoned, on-hold
```

**Human approval gates:**
- `conceived` → `specified`: Human must approve the specification
- `committed` → `integrated`: Human must validate production deployment

### Priority Guidelines

- **high**: Critical path, blocking other work, or significant business value
- **medium**: Important but not urgent, can wait for high-priority work
- **low**: Nice to have, polish, or speculative features

### Tags

Use consistent tags across projects for filtering:
- `auth`, `security` - Authentication and security features
- `ui`, `ux` - User interface and experience
- `api`, `architecture` - Backend and system design
- `testing`, `infrastructure` - Development and deployment
- `billing`, `credits` - Payment and monetization
- `features` - New user-facing functionality

---

## Projects

```yaml
projects:
  - id: "0001"
    title: "Nonprofit Seed List Extraction"
    summary: "Extract ~48K US nonprofit profiles (EIN, name, website URL, rating, revenue, sector, state) from Charity Navigator's public sitemap into a queryable SQLite database, to serve as the seed list for a future report-harvesting bot."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0001-nonprofit-seed-list-extraction.md
      plan: locard/plans/0001-nonprofit-seed-list-extraction.md
      review: locard/reviews/0001-nonprofit-seed-list-extraction.md
    dependencies: []
    tags: [crawler, data-acquisition, nonprofit, lavandula-sales]
    notes: "Spec + plan approved 2026-04-17. Implementation merged to master 2026-04-17 after architect APPROVE (.consult/0001/architect-signoff.md). 96 tests passing + lint.sh clean. TICK-001 (curated-lists pivot) 2026-04-17. TICK-005 + TICK-006 merged 2026-04-20. TICK-008 (IRS fields from ProPublica) merged 2026-04-20 — OrgDetail dataclass + 6 columns + PRAGMA migrations. Marked integrated 2026-04-22 — seed enumeration is running in production (TX 100 + NY 5K runs both successful)."

  - id: "0002"
    title: "Corpus Search Engine (abandoned)"
    summary: "Generic search-to-catalogue pipeline with Google Custom Search provider. Abandoned 2026-04-19 in favor of a site-crawl approach (see 0004) after external developer input + two rounds of multi-agent review showed the search-first architecture kept producing CRITICAL findings that the site-crawl approach sidesteps entirely."
    status: abandoned
    priority: high
    files:
      spec: locard/specs/0002-corpus-search-engine.abandoned.md
      plan: null
      review: null
    dependencies: []
    tags: [infrastructure, search, crawler, shared-engine, abandoned]
    notes: "Abandoned 2026-04-19. Superseded by 0004. Spec and review artifacts preserved at .consult/0002/, .consult/0002-v2/, .consult/0002-v3/."

  - id: "0003"
    title: "Nonprofit Report Catalogue — search-first (abandoned)"
    summary: "Topic plugin on top of 0002's search-first engine. Abandoned 2026-04-19 alongside 0002; the site-crawl approach in 0004 replaces the functional goal and uses LLM-based classification instead of a hand-rolled design-score rubric."
    status: abandoned
    priority: high
    files:
      spec: locard/specs/0003-nonprofit-report-catalogue.abandoned.md
      plan: null
      review: null
    dependencies: ["0002"]
    tags: [lavandula-core, reports, catalogue, classifier, abandoned]
    notes: "Abandoned 2026-04-19 alongside 0002. Superseded by 0004. Review artifacts preserved at .consult/0003/, .consult/0003-v2/."

  - id: "0004"
    title: "Site-Crawl Report Catalogue"
    summary: "Crawl known nonprofit websites directly for annual/impact reports. Uses 0001's curated nonprofit list as the seed, robots.txt + sitemap + homepage-link extraction with anchor-text + URL-path + hosting-platform filters to find candidate PDFs, HEAD/fetch with throttle, and Haiku-class LLM classification of first-page text to decide whether each PDF is actually a real report. Produces the design inspiration library + prospect signal Lavandula needs."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0004-site-crawl-report-catalogue.md
      plan: locard/plans/0004-site-crawl-report-catalogue.md
      review: null
    dependencies: ["0001"]
    tags: [lavandula-core, reports, catalogue, crawler]
    notes: "Replaces 0002 + 0003. Spec approved 2026-04-19 after 3 review rounds (CRITICAL progression 4 → 0 → 1 → 0). Plan approved 2026-04-19 after 1 review round (0 CRITICAL; all HIGH addressed in 9b752b5). 8 phases, 39 ACs. Phases 0-6 built 2026-04-19 (TDD scaffolding + schema/HTTP/SSRF + discovery + fetch/archive + sandbox + classifier + orchestration). All 138 AC tests green. Phase 7 live-validation pending. TICK-001 v3 approved 2026-04-19 after 3 revisions (Codex REQUEST_CHANGES → v2 → v3) + Gemini APPROVE. Relaxed PDF filter on report-anchor subpages. Implementation in progress."

  - id: "0005"
    title: "DeepSeek-Backed Nonprofit Website Resolver"
    summary: "Replace Brave Search + heuristic resolver with a model-backed three-phase pipeline: DeepSeek/Qwen generates 2 candidate URLs, SSRF-hardened HTTP verifies them, model confirms identity from homepage content. Supports DeepSeek-V3 and Qwen via OpenAI-compatible client, selected by RESOLVER_LLM env var."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0005-deepseek-resolver.md
      plan: locard/plans/0005-deepseek-resolver.md
      review: null
    dependencies: ["0001"]
    tags: [lavandula-core, resolver, llm, deepseek, qwen]
    notes: "Spec approved 2026-04-21 after 2 review rounds + red-team (2 CRITICAL fixed: SSRF + indirect prompt injection). Plan approved 2026-04-21 after 1 review round + red-team (tag breakout HIGH fixed). Builder spawned 2026-04-21. PR #1 merged 2026-04-21 after 8 review rounds. Precision gate (≥80% on TX 100-org dataset) deferred — must run before --resolver llm used on production seeds."

  - id: "0006"
    title: "Pipeline Status Dashboard (abandoned)"
    summary: "Read-only web dashboard exposing live state of seeds, resolver, crawl, classify across all SQLite DBs. Shows counts by state/NTEE/revenue band, resolver status breakdown, crawl progress, classification mix, and any running background jobs. Auto-refresh every 10s. Port 4350, FastAPI + SQLAlchemy for forward compatibility with RDS migration."
    status: abandoned
    priority: high
    files:
      spec: locard/specs/0006-pipeline-status-dashboard.md
      plan: null
      review: null
    dependencies: []
    tags: [dashboard, observability, infrastructure, abandoned]
    notes: "Abandoned 2026-05-11. Superseded by 0019 (Django Pipeline Dashboard)."

  - id: "0007"
    title: "S3-Backed PDF Archive"
    summary: "Replace local-disk PDF archive with direct-to-S3 streaming upload. Addresses two concerns: (1) disk exhaustion at scale (5K+ orgs × ~5MB × 2 PDFs = 50GB+), (2) data protection (EBS is single-point-of-failure). PDF bytes stream through memory for first-page text extraction, then PUT to S3 by SHA256 key. SQLite metadata unchanged — classifier hot path unaffected."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0007-s3-pdf-archive.md
      plan: locard/plans/0007-s3-pdf-archive.md
      review: null
    dependencies: ["0004"]
    tags: [infrastructure, storage, s3, crawler]
    notes: "Spec + plan approved 2026-04-22 (red-team APPROVE). PR #4 merged 2026-04-22 after 5 review rounds. Bucket: lavandula-nonprofit-collaterals (us-east-1, SSE-S3, versioned, private). Follow-up TICK: unify Archive.head() shape across LocalArchive/S3Archive + harden startup_probe."

  - id: "0008"
    title: "Agent Batch Runner"
    summary: "Orchestrate Claude Code WebSearch sub-agents for URL resolution at scale. Takes an EIN list → splits into N-org chunks → spawns K agents in parallel → waits → ingests results back into seeds.db. Resumable (agents append to results file as they go), skip-already-resolved by default."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0008-agent-batch-runner.md
      plan: locard/plans/0008-agent-batch-runner.md
      review: null
    dependencies: ["0001"]
    tags: [resolver, orchestration, agents, haiku]
    notes: "Spec + plan approved 2026-04-22 (red-team APPROVE, 0 CRITICAL, 0 HIGH). 25 ACs. Key security: agents run with WebSearch+WebFetch only (no Bash/Read/Write), per-agent subprocess timeout, 2MB output cap, json.dumps() for input generation."

  - id: "0009"
    title: "Address Verification Pass (abandoned)"
    summary: "Second-pass agent fetches the chosen homepage (and about/contact pages) for each resolved org and confirms the street address matches. Detects wrong-state same-name collisions (Columbus TX hospital vs Columbus NE) that the URL-discovery pass misses."
    status: abandoned
    priority: high
    files:
      spec: locard/specs/0009-address-verification.md
      plan: null
      review: null
    dependencies: ["0008"]
    tags: [resolver, verification, agents, data-quality, abandoned]
    notes: "Abandoned 2026-05-11. National resolve complete (89K orgs); Layer 1 corpus assembly done — focus shifting to vocabulary extraction and analysis layers."

  - id: "0010"
    title: "Tiered Model Strategy (abandoned)"
    summary: "Default URL-resolution agents to Haiku for the easy 80%. Route only low-confidence / null / ambiguous results to Opus for a second look. Optional cross-model voting (Haiku + Qwen API) flags disagreements for manual review. Keeps per-org cost low while preserving accuracy."
    status: abandoned
    priority: medium
    files:
      spec: locard/specs/0010-tiered-model-strategy.md
      plan: null
      review: null
    dependencies: ["0008"]
    tags: [resolver, cost-optimization, routing, abandoned]
    notes: "Abandoned 2026-05-11. Gemma self-hosted resolver (0018) made agent-based tiering moot. National resolve complete."

  - id: "0011"
    title: "Operational Controls (abandoned)"
    summary: "Hard budget cap per batch run (orgs + tokens). EIN cache so re-runs use cached agent results unless explicitly invalidated. Runtime warnings when approaching quota limits."
    status: abandoned
    priority: medium
    files:
      spec: locard/specs/0011-operational-controls.md
      plan: null
      review: null
    dependencies: ["0008"]
    tags: [operations, cost-control, caching, abandoned]
    notes: "Abandoned 2026-05-11. Agent batch runner (0008) superseded by Gemma pipeline (0018). Budget controls for agent-based resolution no longer needed."

  - id: "0012"
    title: "Agent Runner Abstraction (abandoned)"
    summary: "Pluggable interface so Claude Code agents, OpenAI Swarm, or local models can be swapped as the URL-resolution backend without rewriting the pipeline. Stable input/output contract; backends register via entry-point or config."
    status: abandoned
    priority: low
    files:
      spec: locard/specs/0012-agent-runner-abstraction.md
      plan: null
      review: null
    dependencies: ["0008", "0010"]
    tags: [architecture, future-proofing, abandoned]
    notes: "Abandoned 2026-05-11. Agent-based resolution replaced by Gemma pipeline (0018). Abstraction over a retired approach has no value."

  - id: "0013"
    title: "SQLite → PostgreSQL (RDS) Dual-Write Migration"
    summary: "Staged migration to managed Postgres RDS. Deploys RDS alongside existing SQLite with clean Alembic-managed schema, adds dual-write mode to db_writer so every write hits both backends, one-time backfills existing SQLite rows to RDS, then flips reads to RDS once dual-write is proven stable. Avoids a clean-cutoff pause because corpus build is continuous. Uses SQLAlchemy (already committed for 0006/0008) so the code change is minimal."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0013-rds-postgres-migration.md
      plan: null
      review: null
    dependencies: ["0004", "0007"]
    tags: [infrastructure, database, rds, migration, dual-write]
    notes: "Timing: start in parallel with 0006 (dashboard). Option A (dual-write) chosen 2026-04-22 over clean-cutover and sync-job alternatives because corpus build is continuous (no natural cutoff) and extraction-app (0014) needs to begin before corpus is 'complete.' Crawler writes stay fast on SQLite; RDS receives every write via async queue. After 2-4 weeks of proven dual-write stability, reads flip to RDS and SQLite writes eventually retire. All new specs (0006+) must use SQLAlchemy so dual-write is a config change, not a rewrite. Marked integrated 2026-04-23 by human."

  - id: "0014"
    title: "PDF Full-Page Text Extraction for Training (abandoned)"
    summary: "Reads PDFs from s3://bucket/pdfs/, runs full-document text extraction (pypdf + OCR fallback via Tesseract or equivalent for scanned docs), produces structured JSON per PDF at s3://bucket/extractions/v1/. Output feeds the interview-training model pipeline. Versioned prefix (v1/) so future extractions don't clobber prior runs."
    status: abandoned
    priority: medium
    files:
      spec: locard/specs/0014-pdf-extraction-training.md
      plan: null
      review: null
    dependencies: ["0007", "0013"]
    tags: [extraction, training-data, future-app, abandoned]
    notes: "Abandoned 2026-05-11. Extraction approach will be revisited as part of vocabulary extraction pipeline (Layer 2) — likely with different scope and tooling than originally conceived."

  - id: "0015"
    title: "Report Gallery UI (abandoned)"
    summary: "Web gallery app for browsing the corpus. Reads metadata from RDS (org name, year, state, NTEE, classification), displays thumbnails from s3://bucket/thumbnails/, full PDFs via presigned S3 URLs. Supports search/filter. Multi-user, read-only, private (auth required)."
    status: abandoned
    priority: medium
    files:
      spec: locard/specs/0015-report-gallery.md
      plan: null
      review: null
    dependencies: ["0007", "0013", "0016"]
    tags: [gallery, ui, future-app, abandoned]
    notes: "Abandoned 2026-05-11. Corpus browsing may resurface as part of the lexicon/interviewer product but will be scoped differently."

  - id: "0016"
    title: "PDF Thumbnail Generator (abandoned)"
    summary: "Batch job that reads PDFs from s3://bucket/pdfs/, renders the first page as a JPEG, uploads to s3://bucket/thumbnails/{sha256}.jpg. Runs either post-crawl or on-demand via lambda. Feeds the gallery UI (0015)."
    status: abandoned
    priority: low
    files:
      spec: locard/specs/0016-pdf-thumbnail-generator.md
      plan: null
      review: null
    dependencies: ["0007"]
    tags: [rendering, gallery-support, future-app, abandoned]
    notes: "Abandoned 2026-05-11 alongside 0015 (Report Gallery UI)."

  - id: "0017"
    title: "Retire SQLite — Use PostgreSQL Directly"
    summary: "Remove SQLite from the runtime write path entirely. Migrate every pipeline module (seed_enumerate, resolve_websites, batch_resolve, crawler, db_writer, budget, classify_null, reconcile_s3) to write directly to RDS via the SQLAlchemy engine from Phase 1. Delete the code-coupled dual-write infrastructure from Spec 0013 Phase 3 (rds_db_writer.py, db_queue.py, rds_writer kwargs, LAVANDULA_DUAL_WRITE flag, verify_dual_write tool). Supersedes Spec 0013 Phase 3 (retained but flag-off forever) and cancels Phase 4 (read flip — obsolete when there's only one store). Schema source of truth moves to lavandula/migrations/rds/*.sql."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0017-retire-sqlite.md
      plan: locard/plans/0017-retire-sqlite.md
      review: null
    dependencies: ["0013"]
    tags: [infrastructure, database, postgres, simplification]
    notes: "Decision 2026-04-22 after demonstrating that code-coupled dual-write is fragile-by-design (breaks every time a new write path forgets to thread rds_writer kwarg). Phase 3 shipped covered only crawler paths; seed/resolver writes were SQLite-only. Plan chosen: truncate RDS, migrate code, test 15-org pipeline, truncate again, backfill from archival SQLite. pg_dump backup at lavandula/nonprofits/data/rds-backups/ + RDS automated 7-day backup cover restore. 15 ACs. Marked integrated 2026-04-23 by human."

  - id: "0018"
    title: "Gemma Pipeline Resolver & Classifier"
    summary: "Replace agent-loop URL resolver (0008) and DeepSeek three-phase resolver (0005) with a code-driven pipeline: Brave Search → filter → HTTP fetch → Gemma 4 E4B (self-hosted on cloud1) disambiguates. Same queue pattern for report classification. 20-100x cost reduction vs agent loop."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0018-gemma-pipeline-resolver.md
      plan: locard/plans/0018-gemma-pipeline-resolver.md
      review: null
    dependencies: ["0001", "0004", "0013"]
    tags: [resolver, classifier, gemma, pipeline, cost-optimization, self-hosted]
    notes: "Validated 2026-04-23: Gemma 4 E4B + Brave resolved 9/10 previously-unresolved TX orgs. 74 tok/s, 0.5s warm latency, 9.7GB VRAM on L4 GPU. PR #10 merged 2026-04-23 after 3 review rounds. 14 new files, 78 unit tests. Manual validation (AC12/AC13) pending — requires autossh tunnel + live Gemma."

  - id: "0019"
    title: "Pipeline Dashboard & Control Center (Django)"
    summary: "Django web app serving as operations cockpit: real-time pipeline progress (seed, resolver, crawler, classifier), process controls (start/stop/configure with model selection), and foundation for future report interviewer. Replaces read-only 0006 concept. Supersedes 0006."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0019-pipeline-dashboard.md
      plan: locard/plans/0019-pipeline-dashboard.md
      review: null
    dependencies: ["0013", "0017"]
    tags: [dashboard, django, operations, interviewer-foundation]
    notes: "Supersedes 0006 (read-only FastAPI dashboard, never specced). Scope: Phase 1 = pipeline dashboard + controls, Phase 2 = report data extraction viewer, Phase 3 = interviewer MVP. Spec approved 2026-04-26 after 4 review rounds. Plan approved 2026-04-27 after 2 review rounds. PR #19 merged 2026-04-27. 99 tests, 51 files, +3876 lines. Marked integrated 2026-05-11."

  - id: "0020"
    title: "Data-driven crawler taxonomy & precision improvements"
    summary: "Phase 1 of collateral-taxonomy rollout: convert approved taxonomy (lavandula/docs/collateral_taxonomy.md) to YAML as source of truth for keyword lists and signal weights, refactor crawler to read from YAML, add filename heuristic grading with 3-tier triage, add alt/title/aria-label to anchor extraction, tier path keywords (strong=pass-alone, weak=require-anchor). Enables PM-level taxonomy edits without code changes."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0020-data-driven-crawler-taxonomy.md
      plan: locard/plans/0020-data-driven-crawler-taxonomy.md
      review: null
    dependencies: ["0004", "0018"]
    tags: [crawler, taxonomy, precision, data-driven, config]
    notes: "Driven by observed junk in 2026-04-23 crawl (Fordham returned 207 false-positive PDFs via /media path keyword). Taxonomy approved 2026-04-24. Phase 1 ships precision/recall improvements; Phase 2 (classifier expansion) and Phase 3 (DB rename + dashboard) follow. Marked integrated 2026-04-27 by human."

  - id: "0021"
    title: "Async I/O Crawler Pipeline"
    summary: "Replace synchronous requests + ThreadPoolExecutor with aiohttp + asyncio event loop. Per-host rate limiting via async semaphores instead of time.sleep(). Producer-consumer separation (discovery feeds download queue). Target: 100K+ orgs on a single machine."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0021-async-crawler-pipeline.md
      plan: locard/plans/0021-async-crawler-pipeline.md
      review: null
    dependencies: ["0004", "0020"]
    tags: [crawler, performance, async, architecture, national-scale]
    notes: "Motivated by 100-org test run taking ~4 hours with 8 threads. National crawl (100K+ orgs) would take weeks at current throughput. Most time spent in time.sleep() throttle waits — async I/O can multiplex those idle periods across hundreds of orgs. Plan approved 2026-04-25 after 2 review rounds (plan-review + red-team). Spec synced with plan: ThreadPoolExecutor PDF validation, DummyCookieJar, non-zero exit on flush failure. PR #12 merged 2026-04-25 after 3 architect review rounds: round 1 caught 8 issues (AC23 transient handling, AC34 exit code, AC24 permanent_skip, AC8 retries, AC22 ordering, AC36 queue depth, AC26 extract parity, plus dead code); round 2 fixed all 8 + added 3 must-fix tests (AC26 parity, AC22 slow-flush durability, shutdown integration); round 3 fixed mislabeled shutdown test to actually trigger SIGINT mid-flight. 329 tests passing (66 new async + 263 existing). 100-org validation 2026-04-25: 83 completed/17 transient/0 permanent, 487 PDFs, 60 min wall, 605 MB peak RSS, exit 0. Found NUL byte bug (commit c611181) and shutdown race (commit 188bc25), both fixed. Migration 004 applied 2026-04-25: status + attempts columns + auto-promotion to permanent_skip after MAX_TRANSIENT_ATTEMPTS=3. Verified end-to-end: transient row written, attempts increments on retry, auto-promoted on 3rd attempt."

  - id: "0022"
    title: "Wayback Machine CDX Fallback for Cloudflare-blocked Sites"
    summary: "When a nonprofit site returns Cloudflare 403 (cf-mitigated: challenge) or otherwise yields zero candidates, query the Wayback Machine CDX API for archived PDFs under the domain and download via web.archive.org. Recovers ~70-80% of the otherwise-lost ~17% of orgs. Tagged discovered_via='wayback' for traceability."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0022-wayback-cdx-fallback.md
      plan: locard/plans/0022-wayback-cdx-fallback.md
      review: null
    dependencies: ["0021"]
    tags: [crawler, fallback, wayback, cloudflare, national-scale, data-recovery]
    notes: "Motivated by Spec 0021 100-org validation 2026-04-25 finding: 17% transient failure rate, with 5/5 sampled failures showing Cloudflare bot-challenge responses. Wayback CDX fallback recovers ~77% of blocked orgs. PR #17 merged 2026-04-25 after builder + architect review. 3 production bugs fixed post-merge (commit d8c744b 2026-04-26): CHECK constraint migration 006, _pick_discovered_via wayback preservation, wayback-cdx body-cap registration. 100-org validation: 95% coverage (up from 83%), 17 Wayback recoveries / 22 attempts, 707 PDFs total. Migrations 004-006 applied to RDS. 500-org validation in progress 2026-04-26."

  - id: "0023"
    title: "Classifier Expansion - Full Taxonomy Labels"
    summary: "Expand the binary report classifier to output the full collateral taxonomy from collateral_taxonomy.yaml with material_type, event_type, and group columns. Classifier prompt reads taxonomy YAML so PM-level edits flow through without code changes. Backfill existing classified rows."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0023-classifier-expansion.md
      plan: locard/plans/0023-classifier-expansion.md
      review: null
    dependencies: ["0020", "0004"]
    tags: [classifier, taxonomy, data-quality, national-scale]
    notes: "Motivated by 0020 taxonomy expansion. The crawler knows 70+ material types but the classifier only outputs 5 labels. First-page text from pypdf is sufficient for type classification. PR #18 merged. Post-merge commit 00d4cb3 added taxonomy labels, timing, run_id tracking. Marked integrated 2026-04-27 by human."

  - id: "0024"
    title: "Rename reports table to corpus"
    summary: "Rename lava_impact.reports to lava_impact.corpus and lava_impact.reports_public to lava_impact.corpus_public across RDS, pipeline code, dashboard, and tests. Python module lavandula/reports/ and user-facing URL paths remain unchanged."
    status: integrated
    priority: medium
    files:
      spec: locard/specs/0024-rename-reports-to-corpus.md
      plan: locard/plans/0024-rename-reports-to-corpus.md
      review: null
    dependencies: ["0017", "0019"]
    tags: [database, naming, migration, cleanup]
    notes: "Requested 2026-04-27. Single operator on DB, controlled migration window. Plan approved 2026-04-27. PR #20 merged 2026-04-27. Marked integrated 2026-05-11."

  - id: "0025"
    title: "Definition-Driven Classifier"
    summary: "Decouple classifier behavior from code via swappable definition files. Each definition file specifies categories, descriptions, examples, and counter-examples that the classifier prompt consumes at runtime. Enables PM-level taxonomy iteration without code changes and reuse of the classifier engine for different document types (corpus PDFs, scraped HTML, etc.)."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0025-definition-driven-classifier.md
      plan: locard/plans/0025-definition-driven-classifier.md
      review: null
    dependencies: ["0023"]
    tags: [classifier, taxonomy, data-quality, architecture]
    notes: "Motivated by 2026-04-28 analysis: 30%+ junk rate in corpus, 'other' bucket contains misclassified real reports (endowment reports, financial statements, research reports). Current classifier prompt has zero category definitions — LLM guesses what 'annual' vs 'impact' vs 'other' means. Spec 0023 added material_type columns but the V1 prompt was never replaced. Builder spawned 2026-04-29. PR #21 merged 2026-04-29. Marked integrated 2026-05-11."

  - id: "0026"
    title: "990 Leadership & Contractor Intelligence"
    summary: "Extract named individuals (officers, directors, key employees, top contractors) from IRS 990 XML filings (TEOS bulk download) into a people table keyed by EIN+object_id. Enables pre-call briefings with CEO tenure, board composition, compensation levels, and existing vendor relationships."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0026-990-leadership-intelligence.md
      plan: locard/plans/0026-990-leadership-intelligence.md
      review: null
    dependencies: ["0001"]
    tags: [data-acquisition, enrichment, nonprofit, lavandula-sales, 990]
    notes: "Motivated by 2026-04-30 conversation about 990 Part VII data for prospect intelligence. Source: IRS TEOS bulk XML (not frozen AWS S3 bucket). Table name: people. Spec completed 2026-04-30 after 2 consultation rounds (spec-review + red-team, both Codex + Claude). 54 ACs. PR #22 merged 2026-04-30. Marked integrated 2026-05-11."

  - id: "0027"
    title: "990 Dashboard: Org Detail View & Pipeline Controls"
    summary: "Enhance the dashboard org detail page with 990 leadership data (officers, directors, compensation, Schedule J) and add two pipeline control forms for TEOS Index Download and 990 XML Parse/Import."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0027-990-dashboard-org-detail.md
      plan: locard/plans/0027-990-dashboard-org-detail.md
      review: null
    dependencies: ["0019", "0026"]
    tags: [dashboard, django, ui, 990, pipeline-controls]
    notes: "Spec approved 2026-05-01. Plan approved 2026-05-01 after Codex + Claude plan-review + red-team. PR #23 merged 2026-05-01 after 2 integration review rounds (Codex + Claude). 47 ACs, 19 files, 74 tests. Marked integrated 2026-05-11."

  - id: "0028"
    title: "Contractor Intelligence Resolver"
    summary: "AI-powered enrichment pipeline that researches contractor names from the people table, generates structured descriptions (what the company does, size, relevance), and writes back to a contractor_description column. Same producer-consumer pattern as Spec 0018."
    status: abandoned
    priority: medium
    files:
      spec: locard/specs/0028-contractor-intelligence-resolver.md
      plan: null
      review: null
    dependencies: ["0026"]
    tags: [enrichment, ai, resolver, contractor, lavandula-sales, abandoned]
    notes: "Abandoned 2026-05-14. Deprioritized — focus shifting to document viewer and vocabulary extraction (Layer 2)."

  - id: "0029"
    title: "Retire Legacy Classification Column"
    summary: "Stop writing the lossy 5-value 'classification' column (derived from material_type_to_legacy mapping). Migrate all dashboard and pipeline references from 'classification' to 'material_type'/'material_group'. The classification column currently overwrites the LLM's granular material_type with a coarse bucket (e.g. financial_report → annual)."
    status: abandoned
    priority: medium
    files:
      spec: locard/specs/0029-retire-legacy-classification.md
      plan: null
      review: null
    dependencies: ["0025"]
    tags: [classifier, cleanup, data-quality, dashboard, abandoned]
    notes: "Abandoned 2026-05-14. Legacy classification column still written but material_type is the authoritative field. Low-impact cleanup deprioritized."

  - id: "0030"
    title: "990 Filing Index Automation & S3 Archive"
    summary: "Bulk-load the complete IRS TEOS 990 index (2017-2026, ~2.6M rows), store batch zips and per-org XMLs in S3 instead of EBS, and automatically maintain 990 data for all orgs in nonprofits_seed via nightly refresh and auto-process worker."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0030-990-index-automation.md
      plan: locard/plans/0030-990-index-automation.md
      review: null
    dependencies: ["0026", "0027"]
    tags: [990, infrastructure, s3, automation, pipeline]
    notes: "Spec approved 2026-05-01 after 4 review rounds (Codex spec + red-team, Claude spec + red-team). Plan approved 2026-05-01 after 4 review rounds (Codex plan + red-team, Claude plan + red-team). 32 ACs, 8 phases. S3 bucket: lavandula-990-corpus. Unified codebase — manual controls reuse new infrastructure. Builder spawned 2026-05-01."

  - id: "0031"
    title: "Serpex Search Adapter with Multi-Engine & Phone Enrichment"
    summary: "Replace direct Brave Search API calls with Serpex proxy, adding a search adapter with configurable engine selection (brave, google, bing) and multi-engine mode that merges/dedupes candidates for higher recall. Includes phone number enrichment pass that extracts org phone numbers from search snippets and website contact pages. 6-17x cost reduction vs Brave direct."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0031-serpex-search-adapter.md
      plan: locard/plans/0031-serpex-search-adapter.md
      review: null
    dependencies: ["0018"]
    tags: [resolver, search, cost-optimization, serpex, multi-engine]
    notes: "Motivated by experiment 0001: Serpex matched Brave on easy cases (90% overlap), slightly outperformed on hard cases (5 wins vs 2 losses in manual review of 15 zero-overlap samples). Multi-engine mode addresses the 47% zero-overlap on hard cases — different engines surface different candidates. Marked integrated 2026-05-11."

  - id: "0032"
    title: "Dashboard & Phase Pages for National Ingest Tracking"
    summary: "Overhaul the main dashboard into a national ingest tracker with state-by-state pipeline progress grid, and enhance resolver/classifier phase pages with recent jobs, running job config, and per-state stats — matching the seeder page pattern."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0032-dashboard-national-ingest.md
      plan: locard/plans/0032-dashboard-national-ingest.md
      review: null
    dependencies: []
    tags: [dashboard, ui, operations, national-ingest]
    notes: "Motivated by upcoming full US ingest. Current dashboard shows only aggregates — need per-state pipeline stage visibility to track what's done and what's not across 50 states. PR #27 merged 2026-05-03. 10 files, +502/-197. Marked integrated 2026-05-11."

  - id: "0033"
    title: "Multi-Host Job Distribution & Remote Workers"
    summary: "Extend the existing host-aware job queue into a fully operational multi-host system: host registry with capability tags, dashboard UI for targeting remote hosts, orchestrator heartbeat/health monitoring, and automated work distribution across hosts for national-scale ingest."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0033-multi-host-workers.md
      plan: locard/plans/0033-multi-host-workers.md
      review: null
    dependencies: ["0019", "0032"]
    tags: [infrastructure, orchestration, multi-host, national-scale, operations]
    notes: "Motivated by national ingest bottleneck shifting from API rate limits to ops throughput. Job model already has host field, orchestrator already filters by hostname. Need registry, health, UI routing, and auto-distribution. Spec approved 2026-05-03. Plan approved 2026-05-03. PR #28 merged 2026-05-03. 20 files, +890/-32. Awaiting operator: Django migration + gunicorn reload + setup_worker on each host."

  - id: "0034"
    title: "Pipeline Control Plane & Org Provenance"
    summary: "Re-engineer job lifecycle (scheduler, dependency resolution, structured logging), unified org provenance tracking (single view of where each org is across all pipeline stages), and extensible stage registry so future stages (extract, aggregate, report) plug in without re-engineering."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0034-pipeline-control-plane.md
      plan: locard/plans/0034-pipeline-control-plane.md
      review: null
    dependencies: ["0019", "0033"]
    tags: [infrastructure, orchestration, observability, pipeline, architecture]
    notes: "Motivated by 2026-05-05 observation: jobs silently fail to start, exit codes require log spelunking, org pipeline status spread across multiple tables with no unified view. Current system grew organically — needs a proper control plane before adding extract/aggregate/report stages. Spec approved 2026-05-06 after 4 review rounds (spec+red-team, Codex+Claude). Plan approved 2026-05-06 after 4 review rounds (plan+red-team, Codex+Claude). 53 ACs, 5 phases. PR #29 merged 2026-05-07: 32 files, +3189/-59. Awaiting operator: Django migrations 0007+0008, RDS migration 013, gunicorn reload."

  - id: "0035"
    title: "Multi-Page Classification Context & Hardened Classifier"
    summary: "Extract pages 1-5 from S3-archived PDFs into a persistent classification_context table, augment the classifier prompt with metadata (URL, page count, pdf_creator), add rule-based pre-filters for obvious categories (990s, financial statements), and re-classify the full corpus. Keeps extracted context for iterative prompt tuning without re-touching S3."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0035-multipage-classification.md
      plan: locard/plans/0035-multipage-classification.md
      review: null
    dependencies: ["0023", "0025", "0007"]
    tags: [classifier, data-quality, extraction, cost-optimization]
    notes: "Motivated by 2026-05-08 analysis: 33% of corpus has <200 chars of first-page text (cover pages only), causing low-confidence guesses. 1,965 IRS 990s misclassified as financial_report. Impact_report has 17% low-confidence rate. Sending 5 pages + metadata to DeepSeek costs $32 total for 145K docs. PR #30 merged 2026-05-08: 18 files, +3636/-10, 49 tests. Phases 1-7 complete (schema, extraction, prefilter, augmented prompt, reclassification, comparison, promotion). Phase 8 (dashboard integration) remains. Awaiting operator: RDS migration 014, run extract_classification_context, then reclassify_corpus."

  - id: "0036"
    title: "Pipeline Stall Watchdog"
    summary: "Generic deadlock/stall detection for producer-consumer pipelines. Monitors progress counters and detects when active workers stop completing work. Covers crawler, extract_classification_context, reclassify_corpus, and future Docling extraction."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0036-pipeline-stall-watchdog.md
      plan: locard/plans/0036-pipeline-stall-watchdog.md
      review: null
    dependencies: []
    tags: [reliability, crawler, pipeline, observability, ses-notification]
    notes: "Motivated by 2026-05-08/09 deadlock incidents: VA crawler stalled 2h at 38/2935, WA crawler stalled 5h at 699/2137. PR #31 merged 2026-05-09. 47 tests, +2618/-131. Includes SES email alerts, dashboard Job updates, pre_abort cleanup hooks. Set WATCHDOG_NOTIFY_EMAIL env var to enable email alerts."

  - id: "0037"
    title: "Rename RDS Roles to Project-Prefixed Names"
    summary: "Rename app_user1/ro_user1 to research_app/research_ro on shared RDS so a second project can coexist without role-name collisions. SSM keys (rds-app-user, rds-ro-user) stay stable; only values change."
    status: integrated
    priority: medium
    files:
      spec: locard/specs/0037-rds-role-rename.md
      plan: locard/plans/0037-rds-role-rename.md
      review: null
    dependencies: []
    tags: [rds, iam, multi-tenant, ops]
    notes: "Motivated by adding a second project (flipbook hosting) to the shared RDS instance. Prefix decision: research (operator-confirmed 2026-05-10). Cutover completed 2026-05-11: roles renamed in PgAdmin, SSM updated, IAM policy updated (dbuser: format, not dbuser/), services restarted and verified. Also: cloud1 split to own instance profile, stale rds-schema SSM corrected, RDS deletion protection enabled."
  - id: "0038"
    title: "Classifier Pipeline Rebuild"
    summary: "Ground-up rewrite of reclassify_corpus: require extraction context (no silent fallback), real progress logging, state/EIN/org filtering, actual concurrent workers, quality gates. Replace the broken v3 pipeline with something trustworthy."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0038-classifier-pipeline-rebuild.md
      plan: locard/plans/0038-classifier-pipeline-rebuild.md
      review: null
    dependencies: [0035]
    tags: [classifier, pipeline, quality, reliability]
    notes: "Motivated by discovering the v3 classifier never used multi-page context (Spec 0035), accepted a --workers flag it ignored, logged nothing during runs, and had no way to target by state/EIN. 133 results from test run all used first_page_text fallback. PR #33 merged 2026-05-12. 11 files, +3270/-474, 61 tests. Awaiting operator: test with --dry-run --state TX, then single-EIN smoke test."

  - id: "0040"
    title: "Classifier V3 Job Queue Integration"
    summary: "Migrate classifier v3 pipeline (extract-context, reclassify, compare, resolve-disagree, promote) from ad-hoc PipelineProcess to the Job queue system. Adds StageDefinition entries, state-isolated job creation, run-vs-queue choice, and job queue visibility in the dashboard — matching the pattern used by seed/resolve/crawl."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0040-classifier-v3-job-queue.md
      plan: locard/plans/0040-classifier-v3-job-queue.md
      review: null
    dependencies: ["0034", "0038"]
    tags: [classifier, pipeline, job-queue, multi-host, operations]
    notes: "Motivated by classifier v3 running as ad-hoc PipelineProcess while all other stages use the Job queue. No queue visibility, no state isolation, no multi-host dispatch. User needs to run multiple state-isolated classifier jobs simultaneously for national-scale reclassification."

  - id: "0041"
    title: "Classifier V3 Pipeline — Operational Parity"
    summary: "Fix 6 gaps left by Spec 0040 builder: add dependency chaining dropdown, cancel button, progress stats for all 5 stages, allow completed dependencies, fix check_phase_conflict scheduled status bug, delete dead _launch_immediately code."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0041-v3-pipeline-operational-parity.md
      plan: locard/plans/0041-v3-pipeline-operational-parity.md
      review: null
    dependencies: ["0040"]
    tags: [classifier, pipeline, job-queue, operations, bugfix]
    notes: "Motivated by audit finding that 0040 delivered correct data model but regressed operator experience. PR #35 merged 2026-05-13. 9 files, +283/-39. _launch_immediately dead code cleaned from working tree post-merge. Awaiting operator: restart gunicorn, verify dashboard."

  - id: "0039"
    title: "SSM Parameter Type Optimization (KMS Cost Reduction)"
    summary: "Convert non-sensitive SSM parameters (rds-endpoint, rds-port, rds-database, rds-schema) from SecureString to String type, eliminating unnecessary KMS Decrypt calls. Every process startup currently makes 5+ KMS calls for config values that aren't secrets. With 22+ concurrent crawlers plus Django workers, this generates ~17K KMS requests/month approaching the 20K free tier limit."
    status: integrated
    priority: medium
    files:
      spec: null
      plan: null
      review: null
    dependencies: []
    tags: [infrastructure, cost-optimization, ssm, kms, ops]
    notes: "Discovered 2026-05-12: AWS KMS budget alert triggered at 17K/20K free tier requests. Verified 2026-05-14: all 6 non-sensitive RDS params (rds-endpoint, rds-port, rds-database, rds-schema, rds-app-user, rds-ro-user) are already String type. Only actual secrets remain SecureString. No code or infra changes needed — problem was already resolved. Only borderline item: rds-dashboard-user is SecureString (username, not password) but impact is trivial."

  - id: "0042"
    title: "Classifier V3 State Progress Grid"
    summary: "Add a state × step progress matrix to the classifier v3 dashboard page showing per-state completion across all 5 pipeline steps (extract, reclassify, compare, resolve, promote). Same live-query pattern as the crawler/resolver state grids, adapted for the v3 pipeline's sequential multi-step structure."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0042-classifier-v3-state-progress.md
      plan: locard/plans/0042-classifier-v3-state-progress.md
      review: null
    dependencies: ["0040", "0041"]
    tags: [dashboard, ui, classifier, operations, national-scale]
    notes: "Motivated by observation that v3 pipeline has 5 sequential steps per state but no per-state visibility. Current v3 page shows global step status only. Crawler/resolver pages have state grids but they're single-axis (one process). V3 needs a matrix view."

  - id: "0043"
    title: "Pipeline Control Panel"
    summary: "Comprehensive control panel for managing pipeline operations: service lifecycle (start/stop/restart), job queue management (pause/resume, bulk cancel, retry), host management, and orphan lock cleanup. Designed to be separable from processing hosts for Layer 2 architecture."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0043-pipeline-control-panel.md
      plan: locard/plans/0043-pipeline-control-panel.md
      review: null
    dependencies: ["0040"]
    tags: [dashboard, operations, infrastructure, control-plane, layer-2]
    notes: "Motivated by manual operational pain: restarting services via SSH, cleaning orphan advisory locks, cancelling stuck jobs one-by-one. Should support future separation of dashboard host from processing hosts."

  - id: "0044"
    title: "Org Search, Document Listing & PDF Viewer"
    summary: "Add name/keyword search to the org list page, list all corpus documents on the org detail page with human-readable names and classification labels, and build a standalone PDF viewer page with in-browser rendering, download, print, and metadata sidebar."
    status: specified
    priority: high
    files:
      spec: locard/specs/0044-org-search-doc-viewer.md
      plan: null
      review: null
    dependencies: ["0019"]
    tags: [dashboard, ui, corpus, document-viewer, search]
    notes: "Prerequisite for Layer 2 vocabulary extraction — need to browse and understand the corpus before building extraction pipelines."

  - id: "0045"
    title: "Vocabulary Extraction & Archetype Discovery — Pilot"
    summary: "Production-quality vocabulary extraction pipeline scoped to P20 (Human Services, ~1,400 docs). LLM extracts domain-specific terms, Market Basket Analysis (FP-Growth + lift scoring) discovers sub-archetypes. Schema designed for full-corpus scale but populated with one vertical as proof-of-concept."
    status: abandoned
    priority: high
    files:
      spec: locard/specs/0045-vocabulary-extraction-pilot.md
      plan: locard/plans/0045-vocabulary-extraction-pilot.md
      review: null
    dependencies: ["0035", "0038"]
    tags: [layer-2, vocabulary, extraction, archetype, mba, pilot]
    notes: "ABANDONED 2026-05-16. Skipped Docling parsing stage (prerequisite). LLM extraction from partial text (~5 pages) produces throwaway results — full structured docs required. Also: statistical NLP methods (TF-IDF, C-value) are superior to LLM prompting for term extraction."

  - id: "0046"
    title: "Docling Full-Document Parsing"
    summary: "GPU-accelerated structured text extraction from corpus PDFs via Docling. Produces section-labeled, table-aware, full-document text that feeds the NLP vocabulary extraction pipeline. Prerequisite for all Layer 2 work."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0046-docling-parsing.md
      plan: locard/plans/0046-docling-parsing.md
      review: null
    dependencies: []
    tags: [layer-2, docling, parsing, infrastructure, gpu]
    notes: "Pipeline stage: Parse (between Classify and Extract per PROJECT_SEED). G6 spot instance for GPU (~$38 priority batch, ~$230 full corpus). Output feeds statistical term extraction (spaCy, TF-IDF, C-value)."

  - id: "0047"
    title: "Cross-Origin PDF Recovery & Crawler Fix"
    summary: "Fix the crawler's cross-origin blocking that silently drops PDFs hosted on CDNs (Squarespace, Webflow, Wix, etc.). Recover ~31K known PDF URLs from fetch_log (pass 1), re-scan ~40K orgs to discover CDN-hosted PDF links previously dropped by candidate_filter (pass 2), and permanently fix both candidate_filter.py and redirect_policy.py to accept PDFs linked from an org's own pages regardless of hosting domain."
    status: integrated
    priority: high
    files:
      spec: locard/specs/0047-cross-origin-pdf-recovery.md
      plan: locard/plans/0047-cross-origin-pdf-recovery.md
      review: null
    dependencies: ["0004", "0021"]
    tags: [crawler, data-recovery, cdn, cross-origin, national-scale]
    notes: "Discovered 2026-05-16: 36K PDFs blocked, 5K orgs affected in fetch_log alone. Candidate_filter silently drops another ~40K orgs worth of CDN links. Squarespace alone = 3,431 orgs / 22K PDFs. Status/plan-path reconciliation folds in the intent of dropped hot-patch commit e2183a5 ([Spec 0047] Update status to implementing, add plan path); origin/master PR #40 is the canonical reconcile_s3 line."

  - id: "0048"
    title: "Recovery Readiness: Repository Stabilization & Data-Quality Bar"
    summary: "First micro-plan gate from the corpus-integrity macro-plan. Reconcile the divergent git state (local master 4 ahead / origin/master 2 ahead), make an explicit human decision on the 4 direct-to-master hot-patch commits, discard the unreviewed Content-Type/structure-validation bypass while keeping the legitimate CSRF hardening, remove or ignore generated artifacts, AND author a falsifiable Data-Quality & Attribution-Confidence Standard that all downstream recovery/ingestion must satisfy before any production write."
    status: planned
    priority: high
    files:
      spec: locard/specs/0048-recovery-readiness-baseline.md
      plan: locard/plans/0048-recovery-readiness-baseline.md
      review: null
    dependencies: ["0047"]
    tags: [maintain, git-hygiene, data-quality, attribution, recovery-readiness, blocker]
    notes: "Critical-path blocker for the macro-plan (locard/operations/macro-plan-corpus-integrity-rag-readiness.md). No production recovery may run until this is integrated. Supersedes the earlier untracked draft locard/plans/0048-repository-cleanup-for-approval.md (cleanup-only; mislocated the 4 commits). Human approved spec/plan + builder spawned 2026-05-18 (through expert + red-team consult, 0 CRITICAL); status held at 'planned' per AI lifecycle ceiling — human marks specified/integrated. Inventory: locard/maintain/0048-inventory.md. Default disposition Option B (non-destructive; backup/master-pre-0048 preserved, no reset)."

  - id: "0049"
    title: "Statistical NLP Extraction & Sub-Archetype Discovery"
    summary: "NLP pipeline operating on Docling-parsed sections: spaCy NER, TF-IDF keyness, C-value multi-word term extraction, and sentence embeddings per section. Market basket analysis (FP-Growth + lift scoring) on term co-occurrence discovers sub-archetypes within NTEE verticals. Replaces abandoned LLM extraction approach (0045)."
    status: implementing
    priority: high
    files:
      spec: locard/specs/0049-nlp-extraction-archetypes.md
      plan: locard/plans/0049-nlp-extraction-archetypes.md
      review: null
    dependencies: ["0046"]
    tags: [layer-2, nlp, extraction, archetype, mba, spacy, tfidf]
    notes: "Replaces abandoned 0045 (LLM extraction). Statistical methods extract what's IN documents; LLMs project priors. Pipeline: spaCy NER → TF-IDF/C-value → embeddings → FP-Growth MBA → sub-archetype clustering."
```

## Next Available Number

```yaml
  - id: "0050"
    title: "Metric Extraction with Context Snippets"
    summary: "Extract performance metrics (term + number pairs) from parsed document sections, preserving full sentence/paragraph context, section heading, and source provenance. Builds on archetype vocabulary (0049) to identify metrics per org type. Feeds the AI interviewer with real phrasing patterns and metric templates."
    status: abandoned
    priority: low
    files:
      spec: locard/specs/0050-metric-extraction-context.md
      plan: locard/plans/0050-metric-extraction-context.md
      review: null
    dependencies: ["0049"]
    tags: [layer-2, nlp, metrics, extraction, interviewer]
    notes: "Abandoned: regex approach produced ~750 junk observations per doc. Lesson: statistical methods work for vocabulary discovery, not per-document metric extraction. Superseded by 0051 (LLM extraction via DeepSeek)."
```

```yaml
  - id: "0051"
    title: "LLM Impact Extraction (Metrics + Stories)"
    summary: "Replace Spec 0050 regex pipeline with full-document LLM extraction via DeepSeek. Single API call per document extracts structured impact metrics AND narrative stories. Validated at 100% competitor match rate across 3 test reports. ~$12-15 for full P20 corpus."
    status: conceived
    priority: high
    files:
      spec: locard/specs/0051-llm-impact-extraction.md
      plan: null
      review: null
    dependencies: []
    tags: [layer-2, extraction, llm, deepseek, metrics, stories, interviewer]
    notes: "Validated via experiment 2026-05-26: DeepSeek combined extraction matched 100% of competitor metrics across CanCare, Think New Mexico, Boys & Girls Club. Also extracts stories. Supersedes Spec 0050 regex approach which produced 750 junk observations per doc."
```

```yaml
  - id: "0052"
    title: "Extraction QA Viewer (PDF + Metrics/Stories)"
    summary: "Side-by-side viewer showing source PDF and LLM-extracted metrics/stories with interactive hover-to-highlight and click-to-lock linking between extracted items and their source snippets in the document. Primary QA tool for validating extraction quality at scale."
    status: implementing
    priority: high
    files:
      spec: locard/specs/0052-extraction-qa-viewer.md
      plan: locard/plans/0052-extraction-qa-viewer.md
      review: null
    dependencies: ["0044", "0051"]
    tags: [dashboard, ui, extraction, qa, pdf-viewer, layer-2]
    notes: "Spec approved 2026-05-26. Builds on 0044's PDF viewer infrastructure. Interactive affordance: hover metric → PDF scrolls + highlights source_snippet. Click locks highlight for reading context."
```

```yaml
  - id: "0053"
    title: "Metric Type Taxonomy (Controlled Vocabulary)"
    summary: "Derive a curated metric_type taxonomy from observed extraction data, cluster synonyms into canonical types, publish as YAML definition, and constrain the LLM extraction prompt to use the pick list (with escape hatch for new types). Same pattern as Spec 0020/0025 collateral taxonomy."
    status: conceived
    priority: medium
    files:
      spec: null
      plan: null
      review: null
    dependencies: ["0051"]
    tags: [layer-2, taxonomy, extraction, llm, data-quality]
    notes: "Requires enough extraction data to observe natural clusters (P20 run will produce ~5K+ metric_type values). Pattern: observe → cluster → curate YAML → constrain prompt with pick list + 'suggest new' escape hatch."
```

```yaml
  - id: "0054"
    title: "Parse Dashboard (Docling GPU Orchestration)"
    summary: "Integrate Docling GPU parse orchestration into the pipeline dashboard. Launch/monitor/stop parse runs via the web UI with pre-flight dry-run, live progress, instance health, cost tracking, and run history. Replaces ad-hoc CLI operation."
    status: committed
    priority: high
    files:
      spec: locard/specs/0054-parse-dashboard.md
      plan: locard/plans/0054-parse-dashboard.md
      review: null
    dependencies: ["0030"]
    tags: [dashboard, pipeline, parse, gpu, orchestration]
    notes: "Motivated by operational incident 2026-05-27: ad-hoc CLI parse runs led to bad status queries and unnecessary kill/restart cycles. Parse is the only pipeline stage not managed through the dashboard. PR #47 merged 2026-05-27."
```

  - id: "0055"
    title: "Multi-Instance Parse (SKIP LOCKED Work Claiming)"
    summary: "Enable N concurrent GPU workers for Docling parsing via PostgreSQL SKIP LOCKED work claiming. Orchestrator launches/monitors multiple spot instances; workers self-coordinate via DB. Dashboard shows per-instance health and aggregate progress."
    status: committed
    priority: high
    files:
      spec: locard/specs/0055-multi-instance-parse.md
      plan: locard/plans/0055-multi-instance-parse.md
      review: null
    dependencies: ["0054"]
    tags: [dashboard, pipeline, parse, gpu, concurrency]
    notes: "Motivated by 45h+ single-instance backlog for P-category alone. Full corpus will be 100K+ docs. Builder spawned 2026-05-28. PR #48 merged 2026-05-28."

  - id: "0056"
    title: "Parse Orchestrator Reliability & Observability"
    summary: "Make the parse control plane production-grade so long runs reliably FINISH — the gate before the next full-corpus/national run. Covers: worker heartbeat (liveness independent of completions), relaunch budget that resets on progress (not a hard per-run cap), capacity-death vs bug-death distinction, PLUS the original observability (ship worker logs to S3/CloudWatch so they survive termination; exit_reason on parse_runs)."
    status: conceived
    priority: high
    files:
      spec: locard/specs/0056-parse-observability.md
      plan: null
      review: null
    dependencies: ["0054"]
    tags: [dashboard, pipeline, parse, reliability, observability, operations]
    notes: "Motivated by run 15 (P-all-24h_max) early termination investigation 2026-05-28. Spent significant time diagnosing without the worker log. WORKER HEARTBEAT (added 2026-05-29): the orchestrator currently infers worker liveness ONLY from MAX(completed_at) in the work_queue, so a worker grinding a slow/long doc (parse times up to 646s observed, and the full corpus likely has worse) is indistinguishable from a hung/dead one. Result on run 31: B1 stale-detection fired twice in ~15 min (slots 0 and 2, each after ~21 min with no completion vs HEARTBEAT_STALE_MINUTES=20), relaunching workers that may have been healthy-but-slow — each false relaunch costs ~6 min model warmup + a reclaimed/redone batch. NOTE: A1's per-item catch-all only handles docs that ERROR; a doc that HANGS Docling (no exception) still needs B1 to terminate, so we cannot remove B1 — we need to make it accurate. FIX: have the worker emit a lightweight periodic heartbeat (a timestamp updated independent of doc completion, e.g. lava_parse.parse_runs or a worker_heartbeat row) so the orchestrator can distinguish 'hung' from 'slow'; B1 should key off the heartbeat (+ raise/timeout per-doc) instead of completions alone. Pairs with 0058 long-tail per-doc timeout. RELAUNCH-BUDGET FINDING (run 31, 2026-05-29): MAX_RELAUNCH_ATTEMPTS=3 is a HARD per-run cap that never resets, so over a long run normal spot churn + B1 false-positives erode the fleet 3->2->1->0. Run 31 confirmed it: ended status=failed at ~97% (7782 ok / 78 err / 217 incomplete of 8077) after 15.5h — NOT a compute failure, a control-plane failure. The cap must reset on successful progress (a slot productively parsing for hours shouldn't die from 3 lifetime relaunches), or become heartbeat/age-based. NOTE: the failure was GRACEFUL (B4 preserved the queue, B5 left no orphans, honest 'failed' status, resumable) — the safety nets worked; the erosion is the bug. SCALE GATE: this project must land before the next full-corpus/national parse run, or it will bleed out near the finish again."

  - id: "0057"
    title: "LLM Extraction Faithfulness Verification (Snippet Grounding)"
    summary: "Verification pass that runs after the LLM metric/story extraction. Confirms every source_snippet — and the metric value/unit tied to it — appears verbatim (character-for-character) in the parsed source document text, so we can prove zero hallucination/fabrication. Quarantines or flags any LLM output not grounded in the source."
    status: implementing
    priority: high
    files:
      spec: locard/specs/0057-llm-faithfulness-verification.md
      plan: locard/plans/0057-llm-faithfulness-verification.md
      review: null
    dependencies: []
    tags: [extraction, llm, quality, verification]
    notes: "Added 2026-05-29. VERY IMPORTANT — quality-first; this data will be heavily scrutinized by mature competitors, so LLM output must be defensibly grounded. Mechanics: for each lava_vocab.llm_metrics.source_snippet and lava_vocab.llm_stories.source_snippet, verify it is an exact substring of the document's parsed text (concat of lava_parse.sections.body_text for that content_sha256), and that metric_value/unit appear in / are derivable from the snippet; emit a per-run faithfulness score and quarantine non-matching rows. KEY SPEC DECISION: define the matching policy — strict char-for-char vs normalized (collapse whitespace / Unicode NFC) — because the text the LLM saw may differ subtly from our parsed sections; strict is the strongest claim but may false-positive on whitespace. Depends on the LLM metric/story extraction pass that populates llm_metrics/llm_stories (run_tag p20-*). CORE PRINCIPLE (operator, 2026-05-29): 'you cannot paraphrase a metric' — a metric = a value + its VERBATIM provenance. source_snippet must be a verbatim span copied from the source; whitespace/PDF-artifact normalization is allowed (same text, re-spaced) but SEMANTIC composition/paraphrase is a defect that must be rejected. value/unit must be present in that span. TWO-SIDED FIX: (a) PREVENTION lives in the extractor — constrain the LLM to emit a verbatim span / character offsets (extractive, not abstractive); a metric whose snippet can't be located as a span should not be emitted. (b) 0057 is the VERIFICATION backstop that quarantines non-grounded metrics. BENCHMARK vs competitor GivingCompass API (Think New Mexico 2024-2025 Annual Report, EIN 311611995, our sha 55a91a34): coverage ours 29 metrics vs their 4 (they omit financials, the $2B Medicaid trust-fund campaign, legislative outcomes — material gaps); faithfulness ours 24/29 verbatim-normalized (10/29 strict; 5/29 LLM-composed = defects to repair), competitor 1/4 verbatim (3/4 paraphrased, e.g. '+27 days for elementary' vs source '27 extra days a year for elementary school'); RECALL gap on our side — we missed 2 material metrics (27-day elementary / 10-day secondary extended learning time) that ARE verbatim in our parse but phrased as a roadmap checklist item the extractor under-weighted.STRUCTURAL FIDELITY REQUIREMENT (operator, 2026-05-29): '46 of 89' is a different metric than '46' — a proportion (~52% majority) vs a bare count; the denominator IS part of the metric. CURRENT GAP: llm_metrics stores only a scalar metric_value (we store 46.0; the '89' lives only in free-text metric_text/source_snippet, NOT structured — so a query/aggregation of our DB returns '46', the same flattening the competitor did). Faithful capture therefore needs THREE legs: (1) verbatim provenance, (2) no semantic paraphrase, (3) STRUCTURAL fidelity — represent ratio/proportion metrics as numerator + denominator (+ optional derived %), queryable and comparable across orgs, never collapsed to a scalar. Leg 3 is a metric-schema + extractor change feeding the accuracy guarantee.SPIKE BASELINE (2026-05-29, run 10): grounding of source_snippet vs parsed text (sections+tables) across 68,772 metrics + 10,679 stories — METRICS 72.4% strict / 86.7% whitespace-normalized / 13.3% UNGROUNDED; STORIES 64.2% / 77.6% / 22.4% UNGROUNDED; 0 null snippets. CRUCIAL: the ungrounded bucket is a MIX, not all hallucination — four categories from samples: (a) composed/editorialized prose ('420 Naturalizations is a 65% increase from last year!') = TRUE paraphrase defect; (b) number reformatting ('$2.8M' vs source '$2.8 million') = defect/normalization call; (c) table-derived 'label: value' ('Total operating expenses: $26,122,404') where the data IS in a table but the snippet isn't a contiguous cell string = our CHECK is too naive (needs table-row-aware matching); (d) story quotes with curly vs straight quotes = Unicode/punctuation MEASUREMENT artifact. So 13%/22% is the CEILING under a whitespace-only check; categories (c)+(d) inflate it. STEP 1 of the spec = harden the grounding CHECK (normalization policy: whitespace + Unicode NFC + quote-folding; table-row-aware matching) to get the TRUE defect rate AND because that matching logic IS the gate's logic; THEN test->fix the genuine (a)+(b) defects in the extractor. GRADED PROVENANCE + DISCLOSURE (operator, 2026-05-29 — a 'truth in advertising' stance): the gate is NOT binary publish/quarantine; it assigns each fact a verification TIER and DISCLOSES it — a targeted, per-fact confidence map, the precise inverse of a blanket 'AI may be wrong' hedge. TIER A VERIFIED = text-native source, snippet grounded + source passed 0060 coverage (mathematically traceable PDF->published). TIER B UNVERIFIED-OCR = scanned/image source, no text layer to diff (0060 cannot certify) -> PUBLISH WITH A LABEL ('OCR-derived, not mathematically verified', optional confidence), do NOT quarantine (often the only data for that org). TIER C QUARANTINE = genuine grounding failure (ungrounded/paraphrased) -> never published. Surface the tier as a badge in the viewer + API + a methodology note; reviewer focuses human QA on Tier B, consumer/funder weights accordingly. GUARDRAIL: the label must stay targeted/rare (the exception, pointing at specific OCR facts) — a blanket disclaimer on everything reads as the generic AI hedge and torches the 'provable' value prop. STRATEGIC: honestly disclosing where certainty ends is MORE credible than a competitor's unlabeled blob with implied (false) certainty everywhere; the boundary drawn honestly IS the moat. Fed by 0060's verifiable-vs-OCR signal."

  - id: "0058"
    title: "Parse Performance Optimization (TableFormer FAST + Conditional OCR)"
    summary: "Cut Docling per-doc parse time (currently ~15.5s/doc avg with the L4 GPU only ~12% utilized) via TableFormer FAST mode and conditional OCR, each gated by an extraction-quality A/B so no speedup is shipped at the cost of accuracy."
    status: conceived
    priority: high
    files:
      spec: null
      plan: null
      review: null
    dependencies: ["0055"]
    tags: [pipeline, parse, gpu, performance, quality]
    notes: "Added 2026-05-29. Grounded in measured profiling (g6/L4, docling 2.93.0, 6 docs 1-44pg). FINDINGS: per-doc parse avg 15.5s (p50 11.4, p90 27.9, MAX 646s); GPU util only ~12%; ~all time is inside Docling convert() (our chunking/extract/DB inserts and per-doc DocumentConverter() build are ~0s — build-once is a NON-win, models cache globally). Cost is split between OCR and TableFormer, varying by doc. QUALITY-GATED A/B RESULTS: (1) TableFormer FAST = 1.5x on table-heavy docs, 2.6x combined on 44pg, with NO loss in cell/char COUNTS (slightly higher) and keeps OCR on -> KEEP CANDIDATE, but MUST validate cell-CONTENT correctness (not just counts) on a larger table-heavy sample before ship. (2) Blanket OCR-off = CUT: quality-neutral on the digital-native majority but DESTROYED 88% of table cells + 6% of text on a scanned 13pg doc -> must instead be CONDITIONAL OCR (skip only when an embedded text layer is present; candidate signals: corpus.first_page_text / pdf_* columns or pdfminer text-layer detection), preserving OCR for scanned docs. (3) FP16/BF16 + intra-worker concurrency = SECONDARY (cut the work first; feeding the 88%-idle GPU is a later multiplier). (4) Long tail: add a per-doc timeout / page cap (646s monster docs dominate total compute). ACCEPTANCE CRITERIA must include a quality A/B methodology with cell-CONTENT diffs, not just counts (competitor-scrutiny / quality-first). Intersects 0057 (faithfulness verification) — OCR'd text is where char-for-char grounding is hardest."

  - id: "0059"
    title: "Housecleaning: Deprecate Superseded Metric-Pipeline Tables & Code"
    summary: "Inventory and retire obsolete artifacts superseded by the LLM extraction + planned extractive/faithfulness work — the statistical/market-basket per-doc extractor (metric_observations), fixture runs, stale tests, dead worker paths. Inventory-FIRST: verify references before dropping; RDS table drops are operator-run DDL migrations."
    status: conceived
    priority: medium
    files:
      spec: null
      plan: null
      review: null
    dependencies: []
    tags: [maintenance, cleanup, database, tech-debt]
    notes: "Added 2026-05-29. Use MAINTAIN protocol. CANDIDATES (UNVERIFIED — confirm nothing still reads each before dropping): (1) lava_vocab.metric_observations — the statistical/market-basket 'first attempt' per-doc extractor (extract_metrics.py); produced ~750 noisy observations for ONE Think New Mexico doc (a Library of Congress photo-catalog number became 5 'metrics'); superseded by llm_metrics. (2) fixture extraction_runs (run 8 fixture-bgcsm2, run 11 fixture-cancare-split) + their rows. (3) stale TestFetchWorkBatch tests (assert a removed fetch_work_batch API: retry_errors/reparse/min_version params). (4) legacy non-queue worker path in worker.py (advisory-lock _run_loop) if single-instance mode is retired. KEEP (do NOT deprecate): the statistical DISCOVERY pipeline (extract_terms / discover_archetypes / keyness) — still the right tool for vocabulary/archetype discovery per the discovery-vs-extraction lesson; only the stat EXTRACTION (metric_observations) is dead. DISCIPLINE: read-only inventory first (what writes/reads each artifact); nothing deleted without verification + operator confirmation; table drops are operator-run migrations (Claude cannot apply DDL)."

  - id: "0060"
    title: "Parse Fidelity Verification (Docling output vs source PDF)"
    summary: "Verify the extracted text is true to the source PDF — don't trust the parser, check it. Cross-check Docling output against the PDF's embedded text layer via an independent deterministic extractor (pdftotext/poppler, present on cloud2): coverage (Docling dropped nothing) + inverse (Docling invented nothing). The foundation link beneath 0057 — together they form an unbroken PDF->text->snippet->published chain of custody."
    status: conceived
    priority: high
    files:
      spec: null
      plan: null
      review: null
    dependencies: []
    tags: [pipeline, parse, quality, verification, integrity]
    notes: "Added 2026-05-29 (operator requirement: 'how do we know the extracted text is true to the document?'). HASH NUANCE: a hash (we already have content_sha256) proves file integrity/identity + extraction REPRODUCIBILITY (same PDF+version -> same output), NOT fidelity (output true to the doc) — fidelity needs an independent reference. METHOD: for text-native PDFs (the majority), the embedded text layer IS ground truth; extract it deterministically with pdftotext (poppler, on cloud2, zero AI) and diff vs lava_parse.sections/tables — (a) COVERAGE: fraction of text-layer content present in Docling output (catches DROPPED content); (b) INVERSE: Docling text absent from the layer (catches OCR-INVENTED content). SCANNED/IMAGE PDFs have no text layer -> no free ground truth; fidelity there needs a second OCR cross-check or sampling (residual-risk minority — flag & lower confidence). CHAIN OF CUSTODY: 0060 (PDF->text true) + 0057 (text->snippet verbatim) + structural fidelity = provable provenance from source page to published number. WHY (operator): a published fact must be traceable to its source; the gate must QUARANTINE anything not provable rather than publish it, so the failure mode is 'incomplete' (recoverable) never 'misrepresented' (a reputation event). Spike-able now: sample docs -> pdftotext text layer vs Docling section text -> coverage %.SPIKE BASELINE (2026-05-29, 80-doc run-31 sample, pdftotext vs Docling): TEXT-NATIVE 79/80 (~99%), SCANNED/no-text-layer 1/80 -> the mathematically VERIFIABLE population (Tier A) is the vast majority; OCR 'unverified' gray area (Tier B) is small (caveat: small sample, true scanned rate likely a few %). FIDELITY: word-set coverage mean 0.944 / median 0.960 / p10 0.868 / min 0.534 — Docling captures ~94% of the text-layer content but DROPS ~5% on a typical doc; 13/79 <0.90, 1/79 <0.70. 5-gram shingle coverage mean 0.757, LOWER than word coverage because pdftotext reads PDF-stream order while Docling reconstructs reading order — the gap is mostly benign REORDER, not dropped content. CHECK-DESIGN LESSON (mirrors 0057): the 0060 gate must be WORD-SET/coverage-based (robust to benign reading-order), NOT naive contiguous shingle, or it false-alarms on multi-column pages. RECALL LINK: Docling's ~5% drop is an UPSTREAM root cause of 0057 recall gaps — a metric in dropped text can never be extracted; low-coverage tail (e.g. sha b49954520, 1pg, word=0.53) is where to investigate (designed/infographic/multi-column pages). Spike ran in 34s; cheap to run corpus-wide.SPOT-CHECK (sha b49954520, Make-A-Wish FY25 1-pg impact report, word-cov 0.53): the low coverage was NOT dropped content — Docling GARBLED the custom/subsetted DISPLAY font into mojibake (e.g. 'o;u ƐƏķƕƏƏ wishes sin1e ƐƖѶƒ' = 'over 10,700 wishes since 1983'; 'ver ƒƏƏ -1|b; oѴm|;;uv' = 'Over 300 active volunteers'), while pdftotext decoded it cleanly (applied the font's ToUnicode map; Docling read raw glyph codes). Garbled lines = the HERO STATS (designed callouts) — the highest-value content on an impact report. IMPACT CHECK: 0 of 68,772 run-10 metric snippets contain these mojibake glyphs -> the garble did NOT reach published metrics as WRONG data. LIKELY mechanism: the LLM declines to extract from gibberish -> garbled hero-stats SILENTLY DROPPED = RECALL loss on the most valuable designed numbers ('incomplete not wrong' held). FIX (validated here): pdftotext reads them correctly -> 0060 becomes ACTIVE REPAIR, not just verification: use pdftotext to repair/replace Docling output for text-native docs (primary, or garble-detected fallback). CROSS-LINKS: confirmed recall root-cause for 0057 (parser-garble drops the best stats); cheaper than 0058 force-OCR for designed pages (this is a text-layer-decode failure, not OCR).LARGE SAMPLE (2026-05-29, n=1500 all runs incl. failures): BOTH_OK 99.3% | SCANNED 0.3% (5) | PDFTOTEXT_SUSPECT 0.3% (broken-font, pdftotext NOT an oracle) | DOCLING_FAILED 0.07%. So VERIFIABLE-by-agreement ~99.3% with a TIGHT CI; scanned/Tier-B ~0.3% [~0.1-0.8%], resolving the n=80 'can't pin the rate' problem -> the empirical residual (only labelable, not provable) is <1% of corpus. AGREEMENT within BOTH_OK: word-coverage mean 0.938 / median 0.964 (held vs n=80 spike -> central tendency confident) BUT a real LEFT TAIL: 15.6% <0.90, 2.3% <0.70, MIN 0.107 (Docling kept 11% of a 20pg doc). Average is fine; the TAIL is the integrity risk, concentrated on designed/custom-font docs (Make-A-Wish pattern) where hero stats live -> where pdftotext-REPAIR pays off. CAVEATS: used crude word-set coverage NOT alignment math (edit-distance/LCS) — rates defensible but per-doc divergence lenient; alignment upgrade would reclassify some low-coverage as benign reorder. QUANTIFIED PROVABLE-vs-EMPIRICAL split: ~99% exact-math territory, ~1% empirical residual (Tier B labels).ALIGNMENT COMPLETION (n=397): word-set coverage HELD (mean 0.936 / median 0.963 — consistent across n=80/1500/397; the picture is stable). REVERSE coverage mean 0.988 / median 0.998 -> Docling rarely INVENTS content; it DROPS, doesn't fabricate ('incomplete not wrong' at the parse layer too). METRIC CORRECTION (RETRACT the earlier 'use edit-distance/LCS alignment' recommendation): the sequence-ALIGNMENT ratio (mean 0.691) is the WRONG tool for content fidelity — it conflates benign reading-order differences with real divergence. PROOF: docs with wc=1.00 AND rev=1.00 (identical content, nothing dropped or invented) scored align=0.20 purely because pdftotext's PDF-stream order != Docling's reconstructed reading order. THEREFORE the 0060 gate uses BIDIRECTIONAL word/set COVERAGE (catches drop + invention), NOT global sequence alignment. Lesson: rigor = the metric that measures what you care about (content presence = set membership), not the fanciest string math (alignment measures order). REAL divergence tail = ~29/397 (~7%) low on BOTH coverage and alignment = genuine content drops (pdftotext-repair targets), worst wc=0.04 (near-total Docling miss)."
  - id: "0061"
    title: "Continuous + At-Request Link Verification (component of the new crawl engine)"
    summary: "For viewers that hot-link the ORG'S hosted report (unclaimed profiles), verify the target still exists AND is byte-identical to our corpus copy (content_sha256) — both continuously (background sweep) and at request-time (real-time on view). On divergence, fall back to our verified S3 copy or hide+flag so we NEVER present a swapped doc under our label. A component of the planned new search-engine-grade crawl pipeline, not a standalone tool. Same truth-in-advertising / chain-of-custody principle as 0057/0060, applied to the live external link."
    status: conceived
    priority: medium
    files:
      spec: null
      plan: null
      review: null
    dependencies: []
    tags: [crawl, integrity, verification, viewer, operations]
    notes: "Added 2026-05-29. Folds into a NEW search-engine-grade crawl pipeline the operator intends to build; runs BOTH continuously and AT REQUEST TIME. TIERED LADDER (cost->certainty; escalate only on doubt): T0 HEAD / conditional GET (If-None-Match/If-Modified-Since) -> exists? + cheap CHANGE HINT (ETag/Last-Modified/Content-Length); LIMIT: ETag != content hash (hint, not proof). T1 RFC 9530 Content-Digest/Repr-Digest -> server sha vs ours, identity with NO body; LIMIT: ~unimplemented on org sites -> opportunistic. T2 Range bytes=0-N + partial hash + Content-Length -> cheap change-detect; LIMIT: partial != full identity, Range spotty, needs precomputed partial-hash+size per corpus doc. T3 FULL STREAM-HASH -> stream the body, hash on the fly (no buffering), gated behind a Content-Length pre-check (size differs => changed, skip; size matches => stream-hash to confirm byte-identity) -> authoritative; expensive, only on suspicion/audit. STATE MACHINE: OK->serve link; CHANGED->stop serving under our label, fall back to S3 copy OR hide+flag (product/legal call for unclaimed) + queue re-crawl (swap may be a newer report to ingest); GONE(404/410)->fall back/flag; MOVED(3xx)->follow+re-verify+update source_url; UNREACHABLE->backoff. GOTCHA 1 (resolve FIRST, may reshape approach) — CORS: org servers won't send ACAO for impactreporting.com, so PDF.js can't embed/fetch their PDF cross-origin (SAME wall fixed in 0052) -> likely LINK OUT (new tab) not embed, OR proxy (reintroduces redistribution). GOTCHA 2 — politeness at ~89K-org scale: reuse existing crawl infra (lavandula/reports: host_throttle.py, robots.py, wayback_validation.py, wayback_fallback.py, s3_archive.py). DATA: corpus.source_url_redacted, content_sha256, file_size_bytes, archived_at. PREREQ for T2: backfill partial-hash(first-N-bytes)+size per corpus doc."

```

## Next Available Number

**0062** - Reserve this number for your next project

---

## Quick Reference

### View by Status
To see all projects at a specific status, search for `status: <status>` in this file.

### View by Priority
To see high-priority work, search for `priority: high`.

### Check Dependencies
Before starting a project, verify its dependencies are at least `implemented`.

### Protocol Selection
- **SPIDER**: Most projects (formal spec → plan → implement → review)
- **TICK**: Small, well-defined tasks (< 300 lines) or amendments to existing specs
- **EXPERIMENT**: Research/prototyping before committing to a project
