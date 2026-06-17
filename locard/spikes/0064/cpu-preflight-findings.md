# 0064 — CPU pre-flight (free, no GPU): backend decoder comparison

**Date:** 2026-06-01. **Method:** `cpu_backend_compare.py` reproduces the two real PDF text decoders directly (no GPU pipeline, no models): production `docling_parse.pdf_parser.DoclingPdfParser` vs candidate-#1 `pypdfium2` (PDFium). Run on the two evidence PDFs pulled from S3. Full per-page text dumps: `evidence-{sha}-docling_parse.txt` / `evidence-{sha}-pypdfium2.txt`.

This is the cheapest experiment that could falsify the top-ranked fix — run **before** any GPU spend.

## Full SHAs (resolved from S3 listing — closes findings §5 gap #1)
- `7ce2c7fdfec69d1a735b4feafb75f94430cbb2c24801ed8f2e0a6e8d1627e014` — mode-1 garble exemplar
- `001eb5f86e4c833852f428e6200c21a937dd8b9204ae88b5b91b81a5b6d3dc76` — mode-2 grouping exemplar
- S3 key pattern confirmed: `s3://lavandula-nonprofit-collaterals/pdfs/{sha}.pdf`

## Result 1 — Mode 1 (font garble), doc 7ce2c7fd page 4 (the "2020 IMPACT" infographic)

**`docling_parse` (PRODUCTION) — glyph soup, characters shattered & reordered:**
```
7 2 / 5 , / 7 / P a / e i t / n t / V i / i s / s t / , 3 / 1 0 / B e / h a / i v / o r ...
```
The hero number's digits (`2 7 5 7 7`) and letters are individually present but scrambled in position. `27,577` / `Patient Visits` are **not** recoverable as strings. This is exactly why the LLM stored **725,747** — it reconstructed the scrambled digit glyphs `7 2 5 , 7 7`.

**`pypdfium2` (CANDIDATE #1) — flawless, in reading order:**
```
27,577 Patient Visits
3,001 Behavioral Health Visits
24,576 Primary Care Visits
6,981 Patients in our Care
16,920 Telehealth Visits
361,332 Pounds of Fresh Produce Distributed
72 Staff members across 2 clinic locations
68% Staff members are South Los Angeles residents
2020 IMPACT
```

**Verdict: CONFIRMED at the decoder level — the pypdfium2 backend swap recovers the mode-1 garble.** PDFium applies the font's character map correctly where the in-house docling-parse C++ decoder falls back to scrambled glyph positions. This is candidate #1 from the research matrix, and it is the cheapest possible fix (one kwarg). Mode-1 is, in principle, solved by a backend swap — **pending the no-regression check** (does pypdfium2 harm clean docs? — the real remaining risk, see below).

## Result 2 — Mode 2 (lost grouping), doc 001eb5f8 page 13 (EMAIL CAMPAIGN)

Both decoders produce **near-identical** output (`docling_parse` 16,744 chars vs `pypdfium2` 17,208). Both:
- decode the page cleanly — **no font garble on this doc** (it's a pure mode-2 case; `63,955 DELIVERED` / `23,255 OPENS` / `2,542 CLICKS` all read correctly in BOTH);
- contain the words `EMAIL` and `CAMPAIGN` in the raw text (as separate lines), but **not** as a grouped unit, and place them next to `47,155,255 IMPRESSIONS` rather than next to the three email stats — the same 2D→1D linearization ambiguity in both.

**Verdict: a backend swap does NOT fix mode 2.** The grouping loss is inherent to linearizing a 2D designed infographic and lives downstream of the text decoder (layout / reading-order / chunker). pypdfium2 ≈ docling_parse here. Confirms the research conclusion: mode 2's only in-Docling lever is the **VLM pipeline** (candidate #6), else a different tool / accept quarantine.

## Two more handoff corrections (evidence over assumptions)
1. **"EMAIL CAMPAIGN is absent from the parse entirely" — imprecise.** The label IS in the raw backend text. It is absent from the **post-chunker text the LLM received** (`evidence-001eb5f8-page13-parse.txt`), because the downstream reading-order/chunker stage drops/mis-pairs it. What is truly lost is the label↔stat **grouping**, not the label string.
2. **Doc 001eb5f8 has no font garble** — both backends decode it fine. It is purely a grouping case. (7ce2c7fd is the garble case; the two evidence docs cleanly separate the two failure modes.)

## What this means for the GPU run (re-scoped)
Mode-1's fix (pypdfium2) is now CPU-proven on the evidence doc. The GPU run's job is therefore:
1. **NO-REGRESSION VALIDATION of the pypdfium2 swap (the real risk, the gating question):** run the FULL docling pipeline (layout + TableFormer + OCR) with `backend=PyPdfiumDocumentBackend` vs production over the stratified sample, re-extract + re-ground (symmetric), and confirm **zero `verified→quarantine`** + no sub-word fragmentation / column-merge regression on clean/table docs. The raw-text coverage looked comparable on these 2 docs (no obvious loss), but the segmented-cell fragmentation the research flagged (`pypdfium2_backend.py` L174) only manifests through the full pipeline — must be measured.
2. **Mode-2 VLM probe:** test candidate #6 (VlmPipeline / GraniteDocling ± `do_chart_extraction`) on 001eb5f8 p13 — the one unverified shot at recovering grouping — with per-page GPU-sec cost.

Candidates #2 (selective OCR) and #3 (pdfminer pre-route) for mode-1 become **fallbacks** — only needed if pypdfium2 FAILS the no-regression gate. Don't burn GPU on them unless #1 fails.
