# Parse Fidelity Investigation — Handoff (2026-06-01)

> **Purpose:** durable record of the faithfulness / Docling-fidelity investigation so we can resume after this context is purged. The live thread is **a Docling config/backend change to capture designed-page content better**; once that lands, re-run the prototype below. Read this top-to-bottom before resuming — several conclusions overturned earlier assumptions.

## The one-paragraph state
The faithfulness gate (Spec 0057) is **sound and trustworthy** — verified by hand against real PDFs. p20-v1 (run 10) sits at **82.1% metrics / 71.5% stories verbatim-verified**; the rest are **quarantined, never published** (incomplete, never wrong — as intended). The ~18% quarantine + a class of "unsourceable" values are **overwhelmingly caused by Docling mangling designed/image-heavy pages**, NOT by the LLM fabricating and NOT by a bad gate. **Root fix = parse the designed pages better (Docling config/backend), then the LLM and gate both work.** We are running Docling on **default settings** and have never tested whether default is its accurate mode or fastest mode, nor tried an alternate PDF backend.

## What is PROVEN (verified, not assumed)
1. **Gate logic is correct.** Every disputed "verified" metric I checked was a genuine verbatim substring of the parsed text. Operator hand-confirmed multiple docs in the QA viewer (e.g. `c6dc7cca…` 7/7 verbatim; `f3fe0891…`; `22e8b6bc…`). The gate is deterministic — re-running reproduced 82.1% exactly.
2. **The LLM is better than we'd credited.** On garbled/scrambled parse input it still extracted real numbers; genuine fabrication is a tiny slice.
3. **Docling loses designed-page context — confirmed by looking at real PDFs:**
   - **27,577 case** (`7ce2c7fd…`, see `evidence-7ce2c7fd-infographic-render.png`): a "2020 IMPACT" infographic. Real page says **27,577 Patient Visits**; LLM stored **725,747** (digit-scramble from garbled glyph input). Quarantined correctly — wrong number never published. This is Docling subset-font glyph garble (Docling issue #2334, OPEN: "known limitation … subsetted/glyph-based fonts … falls back to glyph codes").
   - **Email-campaign case** (`001eb5f8…` page 13, see `evidence-001eb5f8-page13-parse.txt` + `-render.png`): rendered page clearly groups "63,955 DELIVERED / 23,255 OPENS / 2,542 CLICKS" under an **"EMAIL CAMPAIGN"** box. Docling's parsed text for page 13 (the exact text the LLM received) **does NOT contain "EMAIL CAMPAIGN" at all**, and mis-pairs the stats with the heading "47,155,255 IMPRESSIONS". So the metrics are faithful-but-context-orphaned, and **the missing scope is absent from the parse, not just unattached.**

## Key REFRAMES (things I asserted then corrected — don't repeat the mistakes)
- I repeatedly stated guesses as facts (e.g. "97% parser / 1.3% fabrication" from a 300-row heuristic sample). Treat any un-measured split as a hypothesis. The diagnostic columns (below) exist now so we can MEASURE instead of guess.
- "Just attach the section parent_heading as context" does NOT solve context-orphaning: (a) it noises up the 82% that are already clear, (b) Docling's heading is often a sibling hero-number ("47,155,255 IMPRESSIONS"), not the category label, and (c) for designed pages the real label isn't even in the parse.
- **A prompt fix alone cannot add context the LLM never received.** Prompt-enrichment ("classify self-contained vs context-dependent; supply scope from the doc") is the right idea and will work **for clean-text pages** — but is blocked on designed/infographic pages until the parse captures the grouping labels. Hence: **fix the parse first.**
- The pdftotext re-verify EXPERIMENT failed (82%→50%) because snippets were extracted from Docling text; grounding them against a different serialization (pdftotext) mismatches. Single-source SWAP is wrong; UNION (Docling OR pdftotext) is the only safe direction. p20-v1 was restored to Docling tiers. (See project 0063.)
- An LLM "vision auditor" swarm was tried to validate the gate and proved LESS reliable than the gate (it mis-judged word-order/table-layout as "paraphrase", missed a plainly-present "777 donors"). **Abandoned.** The trustworthy audit instrument is the **human in the QA viewer**, not another unverified LLM. (Architecture principle: never trust an LLM without a deterministic check; a vision auditor reintroduces the very thing the gate exists to avoid.)

## Tooling built THIS session (already committed/live)
- **Diagnostic gate** (Spec 0057 amendment, committed): `grounding.check()` now records `word_coverage`, `longest_run`, `table_coverage`, `source_chars` on EVERY fact → `grounding_diag` JSONB on `llm_metrics`/`llm_stories` (migration `0057-grounding-diag-pgadmin.sql`, applied). The quarantine bucket self-categorizes via GROUP BY (fragmentation = high coverage + low run; garble/fabrication = low coverage; missing = source_chars 0; table = high table_coverage).
- **QA viewer now shows the verbatim `source:` snippet** beside the composed `metric_text` (commit on extraction_qa.html + extraction-qa.css). Critical for hand-auditing — before this, reviewers eyeballed `metric_text` (allowed to be composed), not the field the gate verifies. URL: `https://cloud2.lavandulagroup.com/dashboard/reports/<sha>/qa/?run_id=10`.
- **Prompt** (`lavandula/nlp/llm_extract.py`): already tightened so `source_snippet` must be verbatim. `metric_text`/`metric_type`/`unit`/`geo_impact` are intentionally COMPOSED (not under the verbatim guarantee). Verbatim applies to `source_snippet` ONLY — this is the unlock for any enrichment work.

## THE OPEN THREAD — resume here
1. **Docling config/backend experiment (the live task).** On a g6 GPU box, run the known-garbled/designed docs through Docling with NON-default settings and diff the text for the lost content:
   - `PyPdfiumDocumentBackend` (issue #2334's first suggestion — we've NEVER run a non-default backend; we only ever ran the default `DoclingParseV4`).
   - `TableFormerMode.ACCURATE` vs default; `do_ocr=True` / `force_full_page_ocr`; higher `images_scale`.
   - Test docs: `7ce2c7fd…` (27,577 infographic, garble), `001eb5f8…` page 13 (email-campaign grouping loss). Success = "EMAIL CAMPAIGN" and "27,577" appear correctly in the extracted text.
2. **IF a config captures designed pages better → re-parse → re-extract → the prompt-enrichment idea becomes viable.** THEN prototype the prompt change (classify self-contained vs context-dependent; for context-dependent, compose scope from text that is now present — and verify the added scope is itself a verbatim doc span, keeping "you cannot paraphrase a metric"). I had a prototype ready to run against page-13 text + a clean-text doc side-by-side to show the boundary of what prompt-alone can fix.
3. **Project 0063 (reserved):** union grounding (Docling OR pdftotext) + re-extraction from repaired text — the path to lift verbatim rate above 82% without the failed single-source swap.

## Operator preferences captured this session (honor these)
- Evidence over assumptions — "assumptions are making fools of us." Don't state a split/number as fact unless measured. Open the actual PDF before concluding.
- Prefer RICHER metric capture than the competitor (we extract ~29 vs their ~4) — do NOT discard context-orphaned metrics to look cleaner; ENRICH them. Discard only as last resort.
- The gate must stay strict; quarantine = "not proven verbatim", which is correct conservative behavior.
- Stop keeping running tallies/scores mid-investigation — it's noise.

## Pointers
- Diagnostic anatomy of quarantine: re-run `GROUP BY` on `grounding_diag` (helper logic in this session's `/tmp/anatomy.py`, reproduce from the column definitions above).
- Evidence files: `locard/operations/evidence-001eb5f8-page13-parse.txt` (the parse jumble), `evidence-001eb5f8-page13-render.png` (real page), `evidence-7ce2c7fd-infographic-render.png` (the 27,577 page).
- Docling upstream: github.com/docling-project/docling issue **#2334** (subset-font glyph garble, OPEN).
