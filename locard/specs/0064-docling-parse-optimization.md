# Spec 0064 — Docling Parse Optimization (Research + Recommend)

- **Project:** 0064
- **Status:** research complete (2026-06-01) — config surface + tribal knowledge + no-regression harness delivered; **awaiting human go/no-go on Thread C GPU matrix-test**. See `locard/spikes/0064/research-findings.md`.
- **Depends on:** none (informs 0057/0060/0063 + future full-corpus parse)
- **Author:** Architect, 2026-06-01
- **Protocol:** EXPERIMENT/research → recommend (no corpus change in this spec)

> **Read first:** `locard/operations/parse-fidelity-investigation-handoff.md` — the full investigation that produced this problem, with evidence renders. This spec is the formal "fix the parser" follow-on.

> **⚑ Research delivered (2026-06-01) → `locard/spikes/0064/research-findings.md`.** Threads A/B/D done (Thread C = GPU, gated on human go/no-go). Two assumptions in the problem statement below were **overturned by reading the installed source** and are corrected here:
> 1. **Backend framing is stale.** We do NOT run "DoclingParseV4" as a distinct mode — the default is `DoclingParseDocumentBackend` and docling 2.93's public `PdfBackend` enum exposes **only** `docling_parse` and `pypdfium2`; V1/V2/V4 are deprecated aliases for `docling_parse`. The **only real alternate public backend is `pypdfium2`** (and it carries a sub-word-fragmentation regression risk to our 82%).
> 2. **Reading order is NOT user-configurable** (`standard_pdf_pipeline.py` hardcodes empty `ReadingOrderOptions()`). So failure mode #2 (lost grouping) likely has **no Docling config fix** — the only in-Docling levers are a larger layout model (Egret) or the separate **VLM pipeline**; failing those it's a different-tool problem (0060). Mode #1 (garble) is the more tractable, cheaper target. **Nothing is proven until the Thread-C diff exists.**

---

## 1. Problem Statement (the WHY — verified, not assumed)

We run Docling **2.93.0** on **default settings** — literally `DocumentConverter()` with no options (proven: `lavandula/parse/chunking.py` `parse_pdf()`, `options is None` branch → bare converter). We have **never** tested whether the default is Docling's accurate mode or its fastest mode, nor tried any non-default PDF backend (we have only ever run the default `DoclingParseV4`).

On **designed / image-heavy pages** (the highest-value content — the "hero stat" infographics), the default parse **loses or mangles content the LLM then cannot extract correctly**. Two failure modes, both confirmed by opening the actual PDFs:

1. **Subset-font glyph garble.** Docling emits mojibake for custom/subsetted display fonts (reads raw glyph codes, doesn't apply the font's Unicode map). Evidence: doc `7ce2c7fd…`, a "2020 IMPACT" infographic, real page shows **27,577 Patient Visits**; the LLM (fed garbled text) stored **725,747** — a digit-scramble. (Quarantined correctly — never published — but it's a silent recall loss of the real number.) This is a **known, OPEN Docling limitation: github.com/docling-project/docling issue #2334** ("subsetted or glyph-based fonts … falls back to glyph codes rather than actual text"; community workarounds suggested there: OCR, switch backend to `PyPdfiumDocumentBackend`).

2. **Lost visual grouping / layout structure.** Designed multi-box infographics get flattened into a jumble where the grouping labels disappear. Evidence: doc `001eb5f8…` page 13 — rendered page clearly groups "63,955 DELIVERED / 23,255 OPENS / 2,542 CLICKS" under an **"EMAIL CAMPAIGN"** box; Docling's parsed text for that page **does not contain "EMAIL CAMPAIGN" at all** and mis-pairs the stats with a sibling hero-number heading ("47,155,255 IMPRESSIONS"). The metrics become faithful-but-context-orphaned, and the disambiguating label isn't even in the parse. (Evidence files: `locard/operations/evidence-001eb5f8-page13-parse.txt` = the jumble, `evidence-001eb5f8-page13-render.png` = the real page, `evidence-7ce2c7fd-infographic-render.png` = the 27,577 page.)

**Why this matters:** these are designed *impact* pages — exactly where the org puts its best numbers. Losing them is a recall hit on the most valuable content, and (per the handoff) **no prompt change can recover context the parse never captured** — the fix must be at the parse layer. The faithfulness gate (0057) is sound and quarantines the bad output correctly; the bottleneck is parse fidelity feeding it.

## 2. Scope

**0064 delivers (research + recommend ONLY — no corpus change):**
1. A **complete map of Docling 2.93's configuration surface** — every relevant knob: PDF backend choices (`DoclingParseV4` default, `PyPdfium`, others), `PdfPipelineOptions` (do_ocr, ocr_options/engines, force_full_page_ocr, TableFormerMode FAST/ACCURATE, do_cell_matching, images_scale, generate_page_images, document_timeout, batch sizes), and anything else that affects **text fidelity / layout / font decode**.
2. **Tribal-knowledge harvest beyond formal docs** — GitHub issues (start: #2334) + PRs + discussions, forum/blog/Stack Overflow workarounds, release notes — specifically human-posted fixes for: subset-font garble, infographic/multi-column layout loss, OCR config, backend selection. The formal API reference is NOT enough; capture undocumented behavior and community fixes.
3. **Empirical test against our known-bad docs** — run candidate configs on `7ce2c7fd…` (garble) and `001eb5f8…` (EMAIL CAMPAIGN loss) and diff the extracted text. Success is measurable: does "27,577" decode correctly, does "EMAIL CAMPAIGN" survive.
4. A **recommended config** with evidence + the no-regression result.

**NON-goals (explicitly out — separate downstream projects):**
- Implementing the config in the worker / re-parsing the corpus / re-extraction (that's a follow-on once a config is approved).
- Union grounding / re-extraction (project 0063).

## 3. Success Criteria (frozen)

1. **Recover the named failures:** the recommended config makes the lost designed-page content appear correctly in the extracted text — "27,577" decodes (no mojibake) and "EMAIL CAMPAIGN" (the grouping label) survives — verified on the evidence docs.
2. **HARD no-regression constraint:** the recommended config must NOT degrade the content on docs that already parse well. The ~82% of p20-v1 metrics currently verbatim-verified must stay verified. Any config that fixes designed pages but harms clean-text/table docs **fails**. Must be shown via an A/B on a stratified sample (clean-text, table-heavy, scanned, designed) comparing extracted-text fidelity before/after — cell/label CONTENT, not just counts (the 0058 A/B lesson).
3. Deliverable: a written config recommendation + the config map + the tribal-knowledge findings + the A/B evidence, decision-ready for a human go/no-go on the (separate) implementation project.

## 4. Method (agent-team research — to run in a fresh session)

Multi-agent research sweep (workflow), parallel threads:
- **Thread A — config surface:** read the *installed* docling 2.93 source (`/opt/docling/lib/python3.10/site-packages/docling`) and enumerate every backend + PdfPipelineOptions knob affecting fidelity, with exact class/param names. (Don't trust memory/web — read the installed lib.)
- **Thread B — tribal knowledge:** GitHub (issues/PRs/discussions, #2334 first), forums, blogs — human workarounds for garble / layout loss / OCR / backend, especially undocumented behavior.
- **Thread C — empirical:** on a g6 GPU box, matrix-test candidate configs (PyPdfium backend; accurate TableFormer; do_ocr=True / force_full_page_ocr; higher images_scale) against the two evidence docs; diff for "27,577" and "EMAIL CAMPAIGN".
- **Thread D — no-regression harness:** define the A/B (reuse 0058 `ab_quality.py` patterns / 0057 gate as oracle) on a stratified sample so any candidate is checked against the already-good 82%.
- Synthesize → recommended config + evidence.

GPU work mirrors the 0058 spike mechanics (SSM to a g6; see `reference_parse_worker_deploy`). Read `parse-fidelity-investigation-handoff.md` for the assumptions-not-to-repeat.

## 5. Traps to Avoid (banked)
- **Don't trust the formal docs alone** — the answer is likely in GitHub/community (operator's explicit instruction; #2334 is exhibit A).
- **Don't trust memory for API names** — read the installed docling source.
- **No-regression is HARD** — a backend/OCR change re-parses everything; prove the good stays good before recommending.
- **Don't conflate "config recommended" with "corpus fixed"** — this spec stops at recommend; re-parse/re-extract is a separate, GPU-expensive project.
- **Evidence over assumptions** — measure on the real evidence docs; don't assert a config works without the diff.

## 6. Open Questions (for the research to resolve)
- Is the garble fixed by a **backend swap** (PyPdfium) — cheap, one-line — or does it require **OCR** (slower, re-parse cost)? #2334 suggests both as candidates.
- Is layout-grouping loss fixable via Docling config at all, or is it inherent to its reading-order model (→ would push toward 0060 pdftotext-repair / a different tool for designed pages)?
- Does any fidelity-improving config carry an unacceptable speed/cost hit at corpus scale (~333K docs)?
