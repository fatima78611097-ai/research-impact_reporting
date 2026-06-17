# Phase 1 spike — vision-first metric pipeline (THROWAWAY)

Validates the **logic** of the vision-first flow end-to-end before any production
wiring or new schema. No DB writes; reads existing `lava_parse` read-only; emits
JSON for the viewer. Code here is disposable — its job is to produce learnings
(does merge/reconcile work, when to fire vision-verify, do the gates behave) that
feed the Phase 2 spec.

## Flow (per document)
1. fetch PDF (local reviewbox or S3) → render pages to PNG (`pdftoppm` @150dpi, page cap)
2. **vision** per page (Flash-Lite): canary (infographic?) + numbers/labels/boxes
3. **docling** read from `lava_parse`: prose sections + tables
4. **deepseek** slot extraction on the Docling prose (production `_METRICS_PROMPT`, verbatim snippets)
5. **merge/reconcile**: match by value; vision wins value conflicts (the infographic guarantee);
   docling-only kept if grounded; vision-only = the infographic catch
6. **verify**: re-vision canary-flagged pages, confirm the published value reads off the image
7. **gates**: is-a-metric (reject bare years / no-subject / no-box) + faithfulness grounding
   (R1/R2, docling-sourced); vision-sourced is grounded by the image (needs a box)
8. emit `spike-result.json` (+ `pages/*.png`)

## Reused as libraries (not reimplemented)
| Piece | Source |
|---|---|
| vision REST adapter + prompts | `../bake-off/run_bakeoff.py` (`gemini`, `CANARY_PROMPT`, `EXTRACT_PROMPT`) |
| DeepSeek extraction | `lavandula.nlp.llm_extract.call_deepseek` + `_METRICS_PROMPT` |
| faithfulness grounding | `lavandula.faithfulness.grounding.check` (pure R1/R2, no DB) |
| read-only DB engine | `lavandula.common.db.make_ro_engine` (IAM/SSM) |
| S3 bucket/key | `lavandula.parse.config` (`lavandula-nonprofit-collaterals`, `pdfs/{sha}.pdf`) |

Keys via SSM: `gemini-api-key`, `lavandula/deepseek/api_key`.

## Run
```bash
cd locard/operations/comp-metric-regression/phase1-spike
python3 build_set.py --infographic 50 --table 25 --prose 25   # -> docset.json
python3 spike.py --docset docset.json --limit 100 --page-cap 20  # -> spike-result.json
# view: serve this dir; open viewer.html  (boxes overlay published metrics on the page image)
```

## Decisions baked into a run (confirm before spending compute)
- **sample**: ~100 docs, mix 50 infographic / 25 table / 25 prose, sampled from
  `lava_parse.documents` by Docling-count proxy (`figure_count` / `table_count`) + 2
  operator-named infographic anchors. Proxy is for *sampling only*; the canary judges truth.
- **page cap**: 20 pages/doc (vision cost & render time bound).
- **models**: vision Flash-Lite @150dpi (canary + extract/page); DeepSeek `deepseek-chat`, prose cap 60k chars.
- **merge**: numeric match tolerance 0.5; **vision wins** value conflicts.
- **verification**: re-vision **canary-flagged pages only** (not every page) — bounds the 2nd-pass cost.
- **PNG only** (no JPEG right-size — that's a Phase 2 optimization).

## Known rough edges (expected — this is what the spike surfaces)
- value↔slot matching is numeric+tolerance only (label association is secondary); mismatches are
  intentionally visible in the viewer.
- `figure_count` is a weak infographic proxy (the whole reason vision exists) — fine for sampling.
- low-value/doc-level gate not implemented here (document selection stands in); it's a Phase 2 stage.
- DeepSeek runs on whole-doc prose (not per-page) — page attribution for docling-only metrics is null.

## Throwaway
Everything in this dir is deleted after Phase 1. Learnings → Phase 2 spec
(`lava_metrics` schema, orchestrated `parse`-successor stage, org-detail repoint).
`pages/` and `*-result.json` are gitignored.
