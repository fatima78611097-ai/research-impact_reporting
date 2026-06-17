# Spec 0064 — Docling Parse Optimization: Decision-Ready Synthesis

> Research deliverable produced 2026-06-01 by the 0064 multi-agent sweep (Threads A=config surface from installed `docling-slim 2.93.0` source, B=GitHub/community tribal knowledge, D=no-regression harness design), then critic-reviewed and revised. Source tree under `locard/spikes/0064/docling-src/` (gitignored). Critic gap-list: `research-critic-gaps.md`.

**Scope:** Two distinct, source-verified parse-layer failures on designed/image-heavy "hero stat" pages — (1) subset-font glyph garble, (2) lost visual grouping / reading order. The Spec 0057 faithfulness gate correctly quarantines the bad output; the bottleneck is parse fidelity feeding it. No prompt change can recover context the parse never captured, so every candidate below is a **parse-layer** change. This document bridges the source/community research (Threads A, B) and the harness design (Thread D) into a ranked candidate matrix for GPU testing (Thread C).

**Rigor note (binding):** No candidate below is asserted to "work." **No Thread-C diff exists yet** — the spike directory holds only the docling 2.93.0 wheels/source and two evidence render PNGs; there is no measured before/after on our docs. Accordingly, every candidate is tagged **UNVERIFIED — pending Thread-C diff** in addition to its evidence-source tag (source-verified mechanism / community-claimed result / to-be-tested). The handoff's own warning — "assumptions are making fools of us" — applies here: this document names the *mechanism* and the *exact test*, and refuses to assert the *outcome*. The two failures very likely need **different levers**; do not assume one change fixes both.

---

## 1. Current production baseline (VERIFIED, not assumed)

Verified by reading `lavandula/parse/chunking.py::build_converter` (L214-247) and the worker call chain, plus the installed docling 2.93.0 source. The handoff phrase "bare `DocumentConverter()` defaults" and "we run DoclingParseV4" are **imprecise/stale shorthand** and are corrected here.

Our production worker builds:

```python
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True                 # config-driven; True in prod
pipeline_options.images_scale = 2.0            # config.IMAGES_SCALE_CAP
pipeline_options.generate_page_images = False
# document_timeout set ONLY on the native path
# table_structure_options left at DEFAULT (config.TABLEFORMER_FAST=False)
DocumentConverter(format_options={InputFormat.PDF:
    PdfFormatOption(pipeline_options=pipeline_options)})   # NO backend= arg
```

| Setting | Production value | How verified |
|---|---|---|
| **PDF backend** | **`DoclingParseDocumentBackend`** (Docling's default) — `PdfFormatOption(...)` built with NO `backend=` arg | `chunking.py` L245-246; default `document_converter.py` L150 (`backend: Type[...] = DoclingParseDocumentBackend`) |
| do_ocr | `True` | `chunking.py` L226 |
| force_full_page_ocr | **`False`** (never referenced in our code) | absent from `chunking.py`/`worker.py`; default `pipeline_options.py` |
| images_scale | `2.0` (config.IMAGES_SCALE_CAP) | `chunking.py` L227 |
| generate_page_images | `False` | `chunking.py` L228 |
| Table mode | **ACCURATE** — `TABLEFORMER_FAST=False`, so the FAST branch (L234-243) never fires; left at library default `TableFormerMode.ACCURATE` | `chunking.py` L234; default `pipeline_options.py` L139-145 |
| do_cell_matching | `True` (default) | default `pipeline_options.py` L132 |
| Layout model | `DOCLING_LAYOUT_HERON` (default) | default `layout_model_specs.py` L91 |
| OCR engine | auto-select (`OcrAutoOptions()` → engine by environment) | default `pipeline_options.py` L1555 |

**Corrections to the shorthand (all source-verified):**
- The default backend is **`DoclingParseDocumentBackend`** (`document_converter.py` L150), **not** "DoclingParseV4." In docling 2.93 the public `PdfBackend` enum exposes **only** `pypdfium2` and `docling_parse` (`pipeline_options.py` L1013-1037, values at L1031-1032); the `V1`/`V2`/`V4` names are deprecated aliases that all resolve to `DOCLING_PARSE`. So the spec/handoff framing "we run DoclingParseV4" and "try other backends like DoclingParseV2" is **stale** — there is no separate V2/V4 selection on the supported path. The **only** real alternate public backend is **`pypdfium2`**. (`managed_pdfium_backend.py`, `docling_parse_v2_backend.py`, `docling_parse_v4_backend.py` exist as live modules and can be instantiated programmatically, but they are off the supported enum path.) We have **never overridden the backend** — confirmed.
- Our table path runs **ACCURATE**, not a fast/degraded preset.
- We run on Docling's **most accurate default config**. The two failures occur there; they are NOT caused by an unused accuracy lever. The remaining accuracy levers are a larger layout model, `force_full_page_ocr`, and the entirely separate **VLM pipeline** (§2e).
- The handoff's `parse_pdf(options=None)` bare path (`chunking.py` L260) **is** a true bare `DocumentConverter()`, which at runtime resolves to the same `DoclingParseDocumentBackend` default — but the worker does NOT use it; it goes through `build_converter`. (Thread C should still confirm the bare path's backend at runtime rather than asserting "V4.")

---

## 2. Config surface map

### 2a. The two real PDF backends (Thread A1)

| Backend | Class / import | Text decoder | Relevance |
|---|---|---|---|
| **DoclingParse (DEFAULT, ours)** | `docling.backend.docling_parse_backend.DoclingParseDocumentBackend` | In-house `docling-parse` 6.2.0 C++ font/CMap decoder (`pdf_parsers...so`). Parses `/ToUnicode`, `/Differences`, `/Encoding`, CID fonts, embedded CMap streams. | **Locus of failure mode 1.** When a subset/custom font's ToUnicode is missing/malformed, this decoder falls back to STANDARD encoding (wrong char) or drops to a space, producing "27,577 → 725,747"-class garble. |
| **PyPdfium2 (only public override target)** | `docling.backend.pypdfium2_backend.PyPdfiumDocumentBackend` | Google PDFium's `FPDFText_*` text layer — a **completely independent** decoder, the same engine Chrome ships. | Candidate fix for mode 1. **Source-flagged caveat:** `pypdfium2_backend.py` L174 comment: *"PyPdfium2 produces very fragmented cells, with sub-word level boundaries, in many PDFs."* That fragmentation is precisely the class our diagnostic gate buckets as "high coverage + low `longest_run`" — i.e. a **direct no-regression hazard to the 82%**, and it **may worsen mode 2** (grouping/columns). |

Off-enum modules (not the supported path): `DoclingParseV4DocumentBackend`, `DoclingParseV2DocumentBackend` (deprecation aliases → `docling_parse`), `ManagedPdfiumDocumentBackend`. Specialized: `MetsGbsDocumentBackend` (Google-Books), `ImageDocumentBackend` (raster). Not relevant.

### 2b. The backend-override snippet (exact, source-grounded)

`PdfFormatOption.backend` wants the backend **class**, not the `PdfBackend` enum. Minimal diff vs. our `build_converter`:

```python
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

DocumentConverter(format_options={
    InputFormat.PDF: PdfFormatOption(
        backend=PyPdfiumDocumentBackend,    # <-- the only change vs. production
        pipeline_options=pipeline_options,  # unchanged; table opts stay default
    )
})
```

(`pipeline_cls` may be omitted — defaults to `StandardPdfPipeline`; `backend_options` may be omitted — defaults `None`. Do NOT pass the deprecated `PdfBackend` enum.) Confirmed import path for docling 2.50.0+ (B1, discussion #2182). **Prerequisite (Thread D1):** `ParseOptions` has no `backend` field today (`chunking.py` L61) and `build_converter` has no backend branch — Thread A/C must add a `backend: str | None` field and a `if options.backend: ... PdfFormatOption(backend=...)` branch before this candidate can be expressed.

### 2c. The OCR mechanism — why garble survives (Thread A3, source-confirmed)

On a vector-text "hero stat" page, `do_ocr=True` does **NOT** re-OCR the garbled text. The trace:

1. **Text comes from the backend, not OCR.** `page_preprocessing_model.py` L72 calls `get_segmented_page()`, populating cells directly from the (garbled) embedded text layer. No font-map repair step exists.
2. **OCR is bitmap-gated.** `BaseOcrModel.get_ocr_rects()` (`base_ocr_model.py` L40-113) computes OCR rectangles only from `get_bitmap_rects()` — raster regions. With `force_full_page_ocr=False` and the stat being *vector* text (zero bitmap coverage), no OCR rect covers it. Note `OcrOptions.bitmap_area_threshold` (default 0.05, `pipeline_options.py` L189) gates whether a bitmap region is even large enough to OCR — a small hero-stat bitmap can fall below it.
3. **Even nearby OCR is discarded.** `_filter_ocr_cells` (`base_ocr_model.py` L116-140) drops any OCR cell overlapping an existing programmatic cell — and the broken font still produced a (wrong) programmatic cell there.
4. **`force_full_page_ocr=True` is the bypass.** It forces a full-page OCR rect and switches the combine logic to `combined = ocr_cells` (replace, not merge), discarding the embedded garbled layer entirely (`base_ocr_model.py` L97-108, L163-187). **Caveat (no-regression hazard):** it is a *global* flag — it OCRs **every page including clean embedded text**, at ~216 DPI, and can corrupt correct text. This is exactly the kind of cross-the-board change that risks regressing the 82%; it must be tested **selectively**, never globally (see §2c selectivity signal and Rule 1).

**Ready-made selectivity signal:** `PagePreprocessingModel.rate_text_quality` (`page_preprocessing_model.py` L120-145) already flags `GLYPH<...>` / `/G..` / replacement-char garble and sets `parse_score=0.0`. It only scores — it does not repair — but it is a free per-page trigger for **selectively** enabling full-page OCR on the flagged ~10% cohort only. This aligns with the faithfulness gate.

### 2d. Reading-order surface — there is NO reading-order config knob (Thread A2/A3, source-verified)

- **Reading order is NOT user-configurable.** `standard_pdf_pipeline.py` L506 hardcodes `self.reading_order_model = ReadingOrderModel(options=ReadingOrderOptions())` — `ReadingOrderOptions` is instantiated **empty** and is **never threaded through `PdfPipelineOptions`**. There is no `create_legacy` field, no reading-order setting. The `ReadingOrderModel` runs unconditionally. **Therefore failure mode 2 is, at the reading-order stage, NOT directly config-tunable.** Spec Open Question #2 ("is there a reading-order knob?") is answered: **no.**
- Reading order is decided **inside the layout model**: Heron detects flat `Cluster`s; cells are assigned to the single best-overlapping cluster by raw spatial overlap; only FORM/KEY_VALUE/PICTURE/TABLE become structural wrappers. A designed "EMAIL CAMPAIGN" box that Heron does not label as a wrapper becomes a flat TEXT cluster or is dropped — which matches the evidence that "EMAIL CAMPAIGN" is **entirely absent** from the parse.
- The **only exposed grouping levers** are at the layout stage: `LayoutOptions.model_spec` → a larger layout model (`DOCLING_LAYOUT_EGRET_MEDIUM/LARGE/XLARGE` at `layout_model_specs.py` L105-121, or reverting to `DOCLING_LAYOUT_V2` L84), plus `LayoutOptions.create_orphan_clusters` (`pipeline_options.py` L1372/L1411, already default `True`) and `BaseLayoutOptions.keep_empty_clusters` (L1338). These are model/cluster-retention knobs, not a grouping setting — and none is guaranteed to make Heron emit the missing wrapper.
- **The honest framing for mode 2:** if the layout-model knobs above don't cause the box to be detected, the only remaining in-Docling lever is the **VLM pipeline** (§2e), and failing that, a **different tool** (Spec 0060 territory). Do **not** characterize mode 2 as "fix at the parse layer" — name the specific knob (Egret-XLarge + orphan/empty-cluster flags) and accept it may not exist in config at all.

### 2e. The VLM / image pipeline — the lever that targets BOTH modes (Thread A, source-verified, previously omitted)

This is an **entirely separate pipeline**, not a flag on `StandardPdfPipeline`, and the strongest single candidate for mode 2 (and plausibly mode 1):

- **`VlmPipelineOptions`** (`pipeline_options.py` L1280-1321), selected via `PdfFormatOption(pipeline_cls=VlmPipeline)`, default preset **`granite_docling`** (GraniteDocling, `VlmConvertOptions.from_preset("granite_docling")` L991-993). It reads the **rendered page as an image** and emits structure holistically — sidestepping both the broken embedded font layer (mode 1) and the rule-based linearizer (mode 2). `force_backend_text` is also exposed here (L1303) but should stay `False` for our case (it would re-inject the embedded layer).
- **`do_picture_description`** (`pipeline_options.py` L1196, default preset `smolvlm`) — generates descriptions of picture regions; aimed at designed/image content.
- **`do_chart_extraction`** (`pipeline_options.py` L1216) — *"Enable chart data extraction to convert bar, pie, and line charts into structured tabular data"* (verbatim); auto-enables picture classification. Directly targets infographic/chart pages — the exact failure class.
- **`picture_area_threshold`** (`pipeline_options.py` L616) gates whether a picture region is large enough to be described/classified — relevant to whether a hero-stat infographic box is even processed.

**Honest tag:** IBM frames GraniteDocling as minimizing reading-order/structure errors, but **no official doc and no GitHub evidence claims it recovers infographic *grouping*** specifically. It is the most direct architectural answer to both modes and **must** be in the Thread-C matrix — but it is **community-claimed / to-be-tested**, and it is **EXPENSIVE** (full VLM inference, GPU-mandatory), so corpus-wide use is infeasible; gate to flagged pages.

### 2f. Other fidelity-relevant knobs (completeness; Thread A config map)

Beyond backend/OCR/TableFormer/images_scale, these source-verified knobs are fidelity-relevant and must appear in Thread-A's config map (the draft's earlier list was a strict subset):

| Knob | Location | Relevance / hazard |
|---|---|---|
| `force_backend_text` (StandardPdf) | `pipeline_options.py` L1528 | Bypasses layout-model text, uses embedded PDF text directly. **Propagates the same broken glyph stream** on mode-1 docs — NOT recommended for garble; listed for completeness. |
| `do_cell_matching` | `pipeline_options.py` L124-160 (V2 class) | Default `True`. Source warns verbatim: *"Can break table output if PDF cells are merged across table columns."* A **no-regression hazard** if toggled; keep at production default and watch it as a regression dimension. |
| `LayoutOptions.model_spec` = Egret-XLarge | `layout_model_specs.py` L119 | Mode-2 lever (§2d). |
| `create_orphan_clusters` / `keep_empty_clusters` | `pipeline_options.py` L1372/L1411, L1338 | Cluster-retention; may preserve a marginally-detected box. |
| `bitmap_area_threshold` / `picture_area_threshold` | `pipeline_options.py` L189 / L616 | At default 0.05 a small hero-stat bitmap may be skipped for OCR/description. |
| `ocr_options` (`OcrAutoOptions` vs explicit engine) | `pipeline_options.py` L1555 | Default defers to whatever engine is installed (see §3 cost + §2g trap). |

### 2g. OCR engine trap (Thread C must pin the engine)

Default `ocr_options=OcrAutoOptions()` (`pipeline_options.py` L1555) defers to whatever engine the environment provides. Source warns (`pipeline_options.py` docstring L1484): **"RapidOCR has known issues with read-only filesystems (e.g., Databricks). Consider Tesseract or alternative."** Thread C must **pin a specific engine** on the g6 (rather than rely on auto-select), and must test **selective `do_ocr` / selective `force_full_page_ocr`**, **never global `force_full_page_ocr`**, in the no-regression A/B.

---

## 3. Tribal knowledge findings (tagged)

Tags: **[SOURCE-VERIFIED]** = read in our installed code · **[COMMUNITY-CLAIMED]** = asserted online, unverified by us · **[TO-BE-TESTED]** = must be GPU-validated. **All result claims are additionally UNVERIFIED until a Thread-C diff exists.**

### Failure mode 1 — subset-font glyph garble

- **[SOURCE-VERIFIED]** Root cause is the docling-parse C++ font decoder: missing/broken ToUnicode → STANDARD-encoding fallback (wrong char) or space-drop. `keep_glyphs` flag + `GLYPH<...>` fallback strings present in `pdf_parsers...so`. (Thread A1.)
- **[SOURCE-VERIFIED]** `do_ocr=True` cannot fix it: OCR is bitmap-gated and overlap-filtered (Thread A3, §2c).
- **[COMMUNITY-CLAIMED / OPEN]** Upstream issue **#2334** is OPEN, Docling 2.54.0, no maintainer reply (only `dosubot[bot]`). Duplicates all OPEN: #2170, #3081 (→ docling-parse #238), #2256, #1443, docling-parse #137. The holistic fix PR **docling-parse #176** is OPEN, unmerged, `mergeable_state: dirty`, last touched 2026-01-08. **No shipped version through docling 2.96.1 / docling-parse 6.2.0 fixes ToUnicode recovery.** The OCR/PyPdfium suggestions in these threads are **hypotheses from issue discussion, not measured on our docs.** URLs: github.com/docling-project/docling/issues/2334, /2170, /3081, /2256; docling-parse/pull/176.
- **[COMMUNITY-CLAIMED]** Backend swap to PyPdfium2 **fixed some files** (#2021 "just fixed by changing to PyPdfiumDocumentBackend"; #2697 fixed Turkish garble) but **regresses columns** on others (#2697: "mixes different columns to 1 column"; #2256: pypdfium2 extracts the *failing* file correctly). No maintainer endorsement — only `dosubot[bot]`. The source fragmentation comment (§2a, L174) corroborates the regression risk. **[TO-BE-TESTED]** on our hero docs.
- **[COMMUNITY-CLAIMED]** `force_full_page_ocr=True` historically leaked GLYPH into tables (#2737) — **FIXED** by PR #2738 (v2.64.1) + #3107 (v2.79.0), both already in our 2.93.0. #2170 OP reports full-page OCR did NOT fix his garble. **[TO-BE-TESTED]** on our docs.
- **[COMMUNITY-CLAIMED]** Pre-detect doomed files (a-n-d-r-e-a-b, #3081): open with pdfminer, flag fonts whose `Encoding/Differences` contains `/gid…` AND have a `ToUnicode` → route to alternate extractor. Most reliable user-built mitigation for exactly our failure class. URL: github.com/docling-project/docling/issues/3081.
- **[SOURCE-VERIFIED, cautionary]** `keep_glyphs=False` (docling-parse v5.4.0, PR #231) replaces `GLYPH<...>` with a space — it **deletes** unrecoverable chars, making data silently *missing* instead of *garbled*. Worse for a gate that relies on detecting garble. Avoid.
- **[COMMUNITY-CLAIMED, cost]** OCR is the single most expensive stage: ~481 ms/page (L4 GPU), ~3.1 s/page (x86 CPU); disabling OCR saves ~50-60% of runtime. Default OCR engine is now RapidOCR (PP-OCRv4); EasyOCR deprecated as default (#2391). No published digit-level head-to-head. (Docling tech report arxiv 2408.09869v4; DeepWiki OCR models.)

### Failure mode 2 — lost visual grouping / reading order

- **[SOURCE-VERIFIED]** Reading order is hardcoded (`standard_pdf_pipeline.py` L506, `ReadingOrderOptions()` empty, not threaded through `PdfPipelineOptions`). Grouping loss is inherent to the flat layout-cluster + rule-based predictor. The missing "EMAIL CAMPAIGN" cluster was never emitted, so no post-processor or prompt can restore it. **No reading-order config exists.** (Threads A2/A3, §2d.)
- **[SOURCE-VERIFIED]** The **broader mis-pairing** is confirmed in `evidence-001eb5f8-page13-parse.txt`: the literal `47,155,255 IMPRESSIONS` line is **repeated before each of the three email stats** (`63,955 DELIVERED`, `23,255 OPENS`, `2,542 CLICKS`, lines 1-6) **and** `TAX CREDIT CALCULATOR` repeats as a separate jumbled label block (lines 7-16). So the grouping loss spans **at least two distinct boxes**, not just the email campaign box — Thread C should assert success on both mis-pairings.
- **[COMMUNITY-CLAIMED]** Maintainer-channel (Dosu) on discussion #2791: "reading order is handled internally by the layout model and isn't user-configurable." Reaffirmed on #2201 (CLOSED): "no user-facing parameters beyond model selection." Multi-column / cross-box order is an **OPEN, unresolved class**: #2067, #1203, #1868 (all OPEN). URLs: docling/discussions/2791; issues/2201, /2067, /1203, /1868.
- **[SOURCE-VERIFIED lever] + [COMMUNITY-CLAIMED not-guaranteed]** Layout-model swap (`LayoutOptions.model_spec` → Egret-LARGE/XLARGE, or `DOCLING_LAYOUT_V2`) is the only exposed grouping lever; #3004 (CLOSED) shows V2-vs-Heron only changes *which* mis-ordering you get on ordinary single-column docs (NOT validated for infographics). Egret (LARGE/XLARGE) is the higher-accuracy alternative. **[TO-BE-TESTED]** on doc 001eb5f8 p13. (arxiv 2509.11720; model catalog.)
- **[SOURCE-VERIFIED pipeline] + [COMMUNITY-CLAIMED effect]** VLM pipeline (`pipeline_cls=VlmPipeline`, GraniteDocling) plus `do_chart_extraction`/`do_picture_description` (§2e) read the page image holistically and target infographic/chart pages directly. IBM claims fewer reading-order errors. **No official or GitHub evidence it recovers infographic *grouping*** specifically. A candidate to test, not a documented fix. High inference cost. URLs: docling-project.github.io/docling/usage/vision_models/; ibm.com Granite-Docling announcement.
- **[COMMUNITY-CLAIMED]** `docling-hierarchical-pdf` post-processor does NOT help: it only infers heading hierarchy for text-based PDFs and explicitly does not recover grouping on image-heavy pages. Nothing to reorder if the cluster was never emitted. URL: github.com/krrome/docling-hierarchical-pdf.

---

## 4. RANKED CANDIDATE CONFIG MATRIX (the key deliverable → Thread C)

Ranked cheapest-and-most-likely-tractable first. **Cost is at ~333K-doc corpus scale.** Evidence basis is per Section 3 tags. **Every row is UNVERIFIED — pending Thread-C diff**; the "Predicted effect" column is a *hypothesis with a named mechanism*, never a result. The two failures are addressed by different rows.

| # | Candidate change | Targets mode | Predicted effect (HYPOTHESIS) | Cost at corpus scale | Evidence basis | EXACT Thread-C test |
|---|---|---|---|---|---|---|
| **1** | **Backend swap → `PyPdfiumDocumentBackend`** (single kwarg; §2b) | **1** (garble) | Independent PDFium decoder re-reads bytes DoclingParse mis-decodes; on a valid ToUnicode map returns "27,577". **Source-flagged downside:** produces fragmented sub-word cells (L174) → may *lower* `longest_run` on clean text and *worsen* mode 2 (column merge). | **CHEAP** — config-only, no model load, comparable parse speed. Re-parse only flagged or all docs. | Community-claimed result (#2021, #2697 fixed some files; #2697 regressed columns); source-verified mechanism + source-flagged fragmentation (A1, L174) | grep parsed text of **7ce2c7fd** for literal `27,577` (un-mojibake'd) AND on `text_native` strata confirm **no `longest_run`/fragmentation regression** and word coverage ≥ 0.999 (§5 Rule 2/3) |
| **2** | **Selective `force_full_page_ocr=True`** on pages `rate_text_quality` flags (parse_score→0), engine pinned (§2g) | **1** (garble) | Rasterizes + OCRs only the flagged page, discards garbled embedded layer entirely. Bypasses broken font map. Table-leak (#2737) already fixed in 2.93.0. | **CHEAP if gated** to the ~10% designed/quarantine cohort via existing parse_score signal. **MEDIUM-EXPENSIVE and FORBIDDEN if global** (OCRs clean pages too → corrupts good text, violates Rule 1). Pair with `images_scale` 3-4, raised `document_timeout`. | Source-verified bypass mechanism (A3, §2c); community-claimed partial (#2170 OP says it failed for him) | grep OCR'd text of **7ce2c7fd** for `27,577`; verify clean-text strata **NOT** globally OCR'd (word coverage ≥ 0.999, zero `verified→quarantine`) |
| **3** | **Pre-route detector** (pdfminer: font has `/gid…` Differences + ToUnicode → extract with pdfminer/alternate, not Docling) | **1** (garble) | Avoids the docling-parse decoder for exactly the doomed font class. Most reliable user-built mitigation for our failure class. | **CHEAP** to detect (pdfminer scan first pages); MEDIUM to maintain a second extraction path. No corpus-wide re-parse needed. | Community-claimed (#3081, a-n-d-r-e-a-b working code) | run detector on **7ce2c7fd** → must flag it; alternate-extractor output contains `27,577` |
| **4** | **Layout model swap → `LayoutOptions.model_spec = DOCLING_LAYOUT_EGRET_LARGE/XLARGE`** + confirm `create_orphan_clusters=True`, test `keep_empty_clusters` | **2** (grouping) | Larger model *may* detect the designed box as FORM/KV/PICTURE wrapper, preserving label→stat grouping. **Not guaranteed**; reading order downstream stays hardcoded (§2d). | **MEDIUM** — larger model, slower per page, GPU memory. Corpus-wide re-parse if adopted globally. | Source-verified lever (only exposed grouping knob, §2d); community-claimed not-guaranteed (#3004, Heron caveats) | grep parsed text of **001eb5f8 p13** for `EMAIL CAMPAIGN` surviving AND the three stats `63,955`/`23,255`/`2,542` correctly grouped (not the repeated `47,155,255 IMPRESSIONS`/`TAX CREDIT CALCULATOR` mis-pairing) |
| **5** | **Layout model revert → `DOCLING_LAYOUT_V2`** | **2** (grouping) | Changes *which* mis-ordering occurs; a regression mitigation for ordinary docs, NOT validated for infographics. Expected weak. | **CHEAP** — model already bundled, comparable speed. | Community-claimed (#3004, CLOSED — single-column regression only) | same as #4 on **001eb5f8 p13**; expected weak — measure, don't assume |
| **6** | **VLM pipeline** (`pipeline_cls=VlmPipeline`, GraniteDocling) ± **`do_chart_extraction`** / **`do_picture_description`** (§2e) | **2** (grouping) AND possibly **1** | Reads page image holistically; `do_chart_extraction` converts charts→tabular data. Sidesteps both broken font layer and rule-based linearization. **No evidence it recovers infographic grouping specifically.** The most direct architectural answer to mode 2. | **EXPENSIVE** — full VLM inference, seconds-to-minutes/page; GPU mandatory; different pipeline, not a flag. Infeasible corpus-wide; gate to flagged pages only. | Source-verified pipeline (§2e); community-claimed effect (IBM framing); NO GitHub evidence for infographics | grep VLM output of **001eb5f8 p13** for `EMAIL CAMPAIGN` + three stats + correct grouping AND **7ce2c7fd** for `27,577`; record per-page GPU-sec |

**Explicitly NOT recommended (anti-candidates):**
- `force_backend_text=True` (StandardPdf, `pipeline_options.py` L1528) — propagates the same broken glyph stream (A2); no help for garble.
- `keep_glyphs=False` — silently deletes data, defeats the gate's garble detection (B1).
- **Global** `force_full_page_ocr=True` — OCRs clean pages too, corrupts good embedded text, violates Rule 1 (B3). Only the *selective* form (#2) is allowed.
- Toggling `do_cell_matching=False` casually — source warns it can break tables where PDF cells merge across columns; treat any change as a regression-tested dimension, not a free win.
- Blanket backend swap to PyPdfium2 — fragmentation (L174) + community-reported column regression; only acceptable if Rule 1-3 pass on every clean stratum.

---

## 5. No-regression harness + sample plan (Threads D1, D2)

### A/B procedure
New module `lavandula/parse/ab_0064.py` (do not bloat the shipped 0058 gate). For each doc in a **frozen** manifest, parse under **Variant A = current production** and **Variant B = candidate** via `PersistentParseRunner.parse()` (mandatory — the manifest includes a poison doc that segfaults `pdf_parsers.so`; a bare loop would wedge the sweep). Persist to a **scratch namespace** keyed by `(sha, config)` — never mutate `lava_parse.*`/`lava_vocab.*` production tables. Run on a g6 via SSM, mirroring the 0058 deploy mechanics, with the **OCR engine pinned explicitly** (§2g), not auto-selected.

**CRITICAL — symmetric grounding (the trap that sank pdftotext):** the baseline 82.1% `source_snippet`s were grounded against the **OLD Docling serialization**. A naive re-run that grounds Variant B's metrics against Variant A's text (or vice-versa) will show **spurious regressions**. Therefore **both arms must re-extract AND re-ground from their own parse output** — Variant B's tier is computed against Variant B's own serialized text, Variant A's against A's. No cross-serialization grounding anywhere. (Handoff line 19.)

**Three diff layers:**
1. **Table cells** — reuse 0058 `cell_set`/`row_set`/`set_coverage`/`lost_rows` verbatim (normalized SET coverage, reorder-insensitive).
2. **Section text (NEW)** — `section_word_coverage = |A∩B|/|A|` (did B drop words A had?); `target_string_recovery` (presence of exact success strings after `grounding.normalize`); **and `longest_run` / fragmentation** per section (catches the PyPdfium2 sub-word-cell regression — high coverage but shattered runs).
3. **The 0057 gate as oracle (load-bearing)** — re-run `grounding.check` + `gate_runner._assign_tier` per metric **at the same `run_id` and the same extraction prompt**, with `source_text` derived from **each config's own parse** (symmetric grounding — the whole point). Only the parse varies. Emit the **tier-transition matrix**: `verified→verified`, `verified→quarantine` (FATAL), `quarantine→verified` (the recovery win), `quarantine→quarantine`.

The 0058 harness compared only cells; these failures live in section body text and would be **invisible to `cell_set`** — hence layers 2 and 3 are required, and layer 2 now explicitly includes the fragmentation metric.

### PASS/FAIL rule (frozen, exact, hard numeric bounds)
A candidate **PASSES no-regression** iff ALL hold. This rule is **absolute and takes precedence over any recovery win**:

1. **No `verified→quarantine` transition anywhere.** A single metric dropping out of `verified` FAILS the candidate. *No tolerance band — zero is the bound.* This is the hard numeric no-regression bound: **Δ(verbatim rate) ≥ 0 with zero `verified→quarantine` events**, not "drop < X%."
2. **No section-text loss on clean docs:** `text_native` → `section_word_coverage ≥ 0.999`; `scanned` → `≥ 0.95`. (B may reorder/add words, may not drop them.)
3. **No fragmentation regression on clean docs:** on `text_native`, per-section `longest_run` must not fall below the Variant-A value by more than 5% (catches PyPdfium2 sub-word shattering even when word coverage looks fine).
4. **No table-cell regression on clean docs:** existing `_grade` PASS for every `text_native`/`scanned` doc (`cell_coverage ≥ 0.9999`, `lost_rows == 0`, `new_tier_c == 0`).
5. **No new fabrication:** aggregate `new_tier_c == 0` + `section_invention == 0` on clean strata.

Then graded on the **separate, additive recovery objective** (does NOT relax 1-5):
6. **Target-string recovery** on `designed_evidence`: `27,577` decodes for 7ce2c7fd; `EMAIL CAMPAIGN` + `63,955`/`23,255`/`2,542` survive **and group correctly** for 001eb5f8 p13 (i.e., the `47,155,255 IMPRESSIONS` / `TAX CREDIT CALCULATOR` repetition is gone). A config that regresses nothing but recovers nothing = "no-regression PASS / recovery FAIL" (safe, not the fix).

**Asymmetry on designed pages:** `quarantine→verified` is the win we chase; `verified→quarantine` is still forbidden even there.

### Cost capture (Success Criterion 3 — decision-readiness)
For every candidate × stratum, the harness records **wall-clock and GPU-seconds per doc** and extrapolates to the **333K-doc corpus** (× the relevant cohort fraction for gated candidates). Egret-XLarge layout, ACCURATE TableFormer, OCR, and the VLM pipeline each multiply GPU time; the matrix recommendation is not decision-ready without these numbers. Report: per-doc median GPU-sec, projected corpus GPU-hours, and projected cost at current g6 rate, for Variant A vs each candidate.

### Stratified sample (`locard/operations/0064-ab-sample.json`, clone 0058 shape)
Stratify to **real corpus skew, NOT 1:1:1**, weighted toward regression-risk cohorts. **~68 docs total**, frozen with explicit per-stratum counts. All queries scoped to **run-10 / p20-v1** (the population the 82.1% describes), joined to `lava_vocab.llm_metrics.verification_tier` (the gate oracle).

| Stratum | N | Selection signal | Gate |
|---|---|---|---|
| clean-text (`text_native`) | 25 | `text_source='text_native'`, `total_text_chars>2000`, `table_count≤2`, **currently verified** | strict parity (Rules 1-3) |
| table-heavy (`text_native`) | 15 | `text_source='text_native'`, `table_count≥4`, currently verified | strict parity + speed floor (Rules 1,3,4) |
| scanned (`scanned`) | 12 | `text_source='scanned'` or thin text layer | OCR word-coverage ≥ 0.95 (Rule 2) |
| designed/infographic | 16 (incl. 2 evidence docs) | `mb_per_page>1.0`, `total_text_chars<1500`, `figure_count≥3`, biased to `n_quarantine≥1` OR `pdftotext_coverage<0.6` (garble fingerprint) | report-only + substring assertions (Rule 6) |

Deterministic ordering via `md5(content_sha256 || '0064')` (seedable, superset-stable so a stratum can be widened later). The two evidence docs are **mandatory members** of `designed_evidence` with explicit `target_strings`.

### Evidence-artifact gaps Thread C MUST close before the diff is meaningful
1. **Full SHAs.** The repo stores only 8-char prefixes; full 64-char SHAs are not recorded. Resolve once via `SELECT content_sha256 FROM lava_corpus.corpus WHERE content_sha256 LIKE '7ce2c7fd%' OR content_sha256 LIKE '001eb5f8%';`, write into the manifest, then commit it frozen. PDFs pull from `s3://lavandula-nonprofit-collaterals/pdfs/{sha}.pdf`.
2. **Missing before-state for the garble case.** Only `evidence-001eb5f8-page13-parse.txt` exists; there is **no committed parse-text artifact for 7ce2c7fd** showing the mojibake/`725,747`-class string the LLM actually saw (only a render PNG). Thread C MUST capture **`evidence-7ce2c7fd-infographic-parse.txt`** (the garbled glyph string under production config) as the before-baseline — otherwise the success test "does `27,577` decode" has **no recorded before-state to diff against**.

---

## 6. Open questions for the GPU phase (Thread C)

**Open questions:**
1. Does PyPdfium2 actually decode 7ce2c7fd's `27,577`, or does this PDF genuinely lack any usable ToUnicode (in which case BOTH text engines fail and only OCR/VLM can recover)? — Candidate #1 test.
2. If PyPdfium2 fixes mode 1, how badly does its known sub-word fragmentation (source L174) regress `longest_run`/columns/grouping on clean and designed pages (Rules 1-3)? — measured on the same run.
3. Does **selective** `force_full_page_ocr` + `images_scale` 3-4 recover the digits without injecting OCR noise into clean text? With which **pinned** engine — RapidOCR (default, read-only-FS caveat) vs EasyOCR vs Tesseract — do clean large display numerals read most exactly? No published digit-level benchmark exists.
4. Does any layout model (Egret-LARGE/XLARGE, or V2) + orphan/empty-cluster flags cause Heron to **emit** an "EMAIL CAMPAIGN"/"TAX CREDIT CALCULATOR" wrapper cluster, or is the box simply never detected at any model size? (Reading-order itself is **not** tunable — §2d.)
5. Does the VLM pipeline (GraniteDocling) ± `do_chart_extraction`/`do_picture_description` recover infographic grouping — the one thing no source confirms — and at what GPU-sec/doc and projected 333K-corpus cost?

**The two honest possibilities, and where the evidence points:**

- **(a) Mode 1 (garble) — likely a cheap-to-medium parse-layer fix, NOT YET PROVEN.** A genuine, independent second decoder (PyPdfium2) exists and is community-confirmed to fix *some* files; failing that, selective full-page OCR provably bypasses the broken font map (source-confirmed mechanism); failing both, the VLM pipeline reads the image. The mechanism for a fix exists — but whether it fires on **our** docs (vs. a ToUnicode-less PDF where every text engine fails) is **unverified pending the Thread-C diff on 7ce2c7fd**. Do not state the fix layer as fact.

- **(b) Mode 2 (lost grouping / reading order) — likely NO direct Docling config fix.** This is the stronger, convergent, source-verified conclusion: reading order is **hardcoded** (`standard_pdf_pipeline.py` L506), explicitly NOT user-configurable; the grouping label is never emitted as a cluster (so nothing downstream can restore it); multi-column/cross-box order is an acknowledged OPEN class of bug. The only in-Docling experiments are a **larger layout model** (may or may not detect the box) or the **VLM pipeline** (the most direct architectural lever, but no evidence it recovers infographic grouping). **The evidence points to mode 2 needing a different pipeline (VLM) or a different tool (Spec 0060), not a config tweak** — and possibly to accepting that these designed pages stay quarantined (the gate behaving correctly) until a VLM path is validated.

**Net for Spec 0064:** prioritize Candidate #1 (cheapest, targets the more tractable failure), with Candidate #6 (VLM ± chart extraction) as the strongest — and possibly only — lever for mode 2. Treat mode 2 as a research question with a real chance of "no config fix exists," not as a config to be found. Do not let a mode-1 backend swap silently regress mode-2 or clean pages — Rules 1-3 of the harness exist precisely to catch that. **Nothing here is proven until the Thread-C diff on 7ce2c7fd / 001eb5f8 exists.**

**Key file references:** `/home/ubuntu/research/lavandula/parse/chunking.py` (L214-247 build_converter, L260 bare path, L61 ParseOptions), `/home/ubuntu/research/lavandula/parse/ab_quality.py` (0058 harness to extend), `/home/ubuntu/research/lavandula/faithfulness/grounding.py` + `gate_runner.py` (the gate oracle to reuse verbatim, with symmetric grounding), `/home/ubuntu/research/locard/operations/0058-ab-sample.json` (manifest template), `/home/ubuntu/research/locard/operations/evidence-001eb5f8-page13-parse.txt` (mode-2 before-state), `/home/ubuntu/research/locard/spikes/0064/docling-src/docling_slim-2.93.0-py3-none-any/docling/datamodel/pipeline_options.py` + `pipeline/standard_pdf_pipeline.py` (L506) + `datamodel/layout_model_specs.py` + `backend/pypdfium2_backend.py` (L174) (the config surface). **To capture:** `/home/ubuntu/research/locard/operations/evidence-7ce2c7fd-infographic-parse.txt` (mode-1 before-state — does not yet exist).