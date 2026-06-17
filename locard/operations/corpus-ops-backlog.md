# Corpus-Ops / Orchestrator Backlog (sweep bucket D — separate workstream, 2026-06-12)

Real documented-but-unbuilt items that belong to the crawler/orchestrator/corpus-integrity track,
NOT the extraction pipeline registry. Source: unbuilt-sweep-result.json (verified findings).

## Corpus integrity (macro-plan)
- Document-to-org attribution evidence table (sha, ein, source_url, evidence per relationship)
- Crawl/fetch gap cohort audit (assign every no-document org to a failure cohort)
- Redesigned targeted recovery (Pass-2 replacement; fresh-recrawl path for never-discovered docs)
- Org-specific filename-grading overrides (0020)

## Orchestrator (0040 audit findings)
- Nationwide-blocks-all conflict rule for mutating v3 phases
- Canonical single _V3_PHASES definition; COMMAND_MAP elimination; unified create_job() factory
- _resolve_depends_on error reporting; PipelineProcess retirement
- Job-event audit for all lifecycle transitions; health-check-based worker liveness

## 0058 leftovers
- §4 cell-content A/B quality gate (knob-ship gate never executed — partially superseded by config-B lock)
