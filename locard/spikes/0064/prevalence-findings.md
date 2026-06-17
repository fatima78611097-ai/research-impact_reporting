# 0064 — DEFINITIVE prevalence: garble vs infographic/layout (run-10 / p20-v1)

**Date:** 2026-06-01. **Method:** pure DB analysis of run-10 (the population the 82.1% describes — 68,772 metrics, 56,443 verified, 12,329 quarantined, 3,233 docs), no GPU. Scripts: `prevalence_analysis.py`, `prevalence_part2.py`, `garble_validate.py`, `garble_count.py`, `quarantine_examples.py`. Calibrated against the two ground-truth evidence docs.

## TL;DR (answers the question directly)
- **Font-garble is a corner case: ~0.2% of the extraction (6 docs, 121 metrics).** The operator's "<2%" estimate was generous — it's ~10× smaller. Triangulated three independent ways (below).
- **The infographic/designed-page problem is the real, dominant driver of the 17.9% quarantine — and it scales with design density.** 34% of docs are heavy-infographic; 94% have ≥5 figures. Quarantine rate rises monotonically with figure density. **~89% of all quarantines** are the "all words present, broken contiguity" signature — overwhelmingly hero-numbers spatially separated from their labels on designed pages. The operator's instinct is correct, and this will grow as report design grows.

## 1. Garble prevalence — ~0.2% (triangulated, definitive)
| Method | Measure | Result |
|---|---|---|
| **Direct glyph-soup count** (space-fraction of stored docling text > 0.30 ⇒ avg token ≈ 1 char) | docs / metrics corpus-wide | **6 docs (0.19%), 121 metrics (0.18%)**, 102 quarantined |
| **grounding_diag "words-absent"** bucket (`word_coverage < 0.5`) | share of 12,329 quarantines | 173 (1.4%) ⇒ 0.25% of all metrics (and this includes fabrication, so garble is *less*) |
| **doc pdftotext-coverage attribution** (calibrated on evidence docs) | quarantines in `pdftotext_failed`+`no_cov`+`cov<0.5` docs | 263 (2.1% of quarantines) — but the glyph-soup validation showed *most* of these docs are NOT actually garbled (low coverage came from other causes), so this is an upper bound |

Calibration: garble doc `7ce2c7fd` = coverage 0.25, reverse 0.01, `text_source=pdftotext_failed`, **96% single-char tokens** (glyph soup), 0/29 verified. Clean doc `001eb5f8` = coverage 0.89, `text_native`, normal tokens, 16/17 verified — *and it is itself infographic-heavy (37 figures)*, proving infographic ≠ garble. The glyph-soup validation across coverage bands found **only `7ce2c7fd` itself** with the extreme scramble; other low-coverage / `pdftotext_failed` docs have clean ~5-char-word text.

**Conclusion:** the dramatic `27,577 → 725,747` subset-font scramble is nearly a one-off. Real corpus-wide impact ≈ 0.2% of metrics. The pypdfium2 backend swap (proven on CPU to fix it) remains a cheap, correct win for those 6 docs, but it is **not** where the recall is lost.

## 2. Infographic / layout is the dominant driver — quarantine scales with design density
Run-10 docs binned by figure density, with metric outcomes:

| Cohort | docs | % docs | metrics | quarantine | **quarantine rate** |
|---|---|---|---|---|---|
| heavy_infographic (≥10 figs, <1200 ch/pg) | 1,109 | 34% | 22,047 | 4,742 | **21.5%** |
| moderate (≥5 figs) | 1,932 | 60% | 42,849 | 7,138 | **16.7%** |
| some_figures (1–4) | 164 | 5% | 3,176 | 346 | **10.9%** |
| text_only (0 figs) | 28 | 1% | 700 | 103 | 14.7% (n=28, noisy) |

**Dose-response: 10.9% → 16.7% → 21.5%** quarantine as figure density rises. Design density predicts failure. And designed reports are the norm (94% of docs ≥5 figures), not the exception — consistent with the operator's "growing problem."

## 3. WHY metrics quarantine — bucketed by grounding_diag (the 12,329)
| Bucket (signature) | count | % of quarantine | interpretation |
|---|---|---|---|
| **E — words present, runs OK, no exact substring** (`wc≥0.8, run≥0.5`) | **10,915** | **89.2%** | hero-number/label separated by layout, reordered, or LLM-stitched on designed pages |
| F — partial (cov 0.5–0.8) | 575 | 4.7% | heavier scatter |
| D — fragmented (`wc≥0.8, run<0.5`) | 408 | 3.3% | severe layout shatter |
| C — table-only | 170 | 1.4% | grounds in table, not prose |
| B — words absent (garble/fabrication) | 173 | 1.4% | **the garble+fabrication slice** |

82.8% of quarantines are in cleanly-decoded (`coverage≥0.9`) docs — i.e., the failure is **not** a decode problem.

### What bucket E actually is (sampled, heavy-infographic docs)
Concrete quarantined examples — all words present, contiguity broken by 2D layout:
- `'200 attendees at the 2nd Bastrop County Local Food Fair'` ← source `'attendees 200 joined the 2nd Bastrop County Local Food Fair'` (**reorder**)
- `'26M square feet of facilities cleaned'` ← `'26M SQUARE FEET OF FACILITIES CLEANED'` (hero number split from label, lr=0.83)
- `'8 new homeowner closings'` ← `'26 8 new homeowner closings'` (**neighboring number interposed**)
- `'29,600 people with access from Wote, Kenya'` ← `'WOTE, KENYA … People with access: 29,600'` (**scattered across boxes**)
- `'54 children supported in Klamath County'` ← `'Klamath County: … supporting 54 children.'` (scattered)

`longest_run` within bucket E is spread (≈0.5: 1,885 · 0.6: 1,929 · 0.7: 1,527 · 0.8: 3,693 · 0.9: 1,974). The high-run mass (~5,700 with run≥0.8) = "one break / one inserted token / light paraphrase"; the low-run mass (~3,800 with run≤0.6) = heavier scatter. **Honest split:** the bulk is layout-scatter (hero number separated from label/context), with a minority being LLM lightly paraphrasing the `source_snippet`. Both are designed-page/composition failures — neither is garble, neither is a Docling backend/config issue (reading order is hardcoded).

## 4. Implications for direction
- **Garble (mode-1, ~0.2%):** the pypdfium2 swap fixes it and is nearly free, but it does **not** warrant an expensive GPU validation campaign as a priority. Bank it as a cheap improvement; validate no-regression later/lighter.
- **Infographic/layout (the 89%, ~16% of all metrics + the context-orphaning of many *verified* designed-page metrics):** this is the real prize and it is downstream of the text decoder. Because the **words are present and mostly contiguous** (wc=1.0, run often ≥0.8), it is potentially addressable WITHOUT re-parsing the corpus, via some combination of:
  1. **Layout-aware chunking** that keeps a hero number contiguous with its label (use the cell coordinates Docling already produces) — so the verbatim snippet exists.
  2. **Prompt change** enforcing a tighter, truly-contiguous `source_snippet` (recovers the light-paraphrase slice cheaply).
  3. **A grounding rule (R3)** for near-contiguous reordered matches — risky vs the "stay strict" stance; would need careful design.
  4. **VLM pipeline** (expensive; the only in-Docling lever for true 2D grouping) — unproven for grouping, infeasible corpus-wide.

The 0064 spec was scoped around garble + grouping with garble as the headline. The data inverts that: **garble is negligible; designed-page layout/composition is the dominant, growing problem, and the fix lives downstream (chunking/prompt/grounding), not in a Docling backend swap.**
