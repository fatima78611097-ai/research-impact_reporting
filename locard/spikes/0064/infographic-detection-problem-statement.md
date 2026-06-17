# Problem Statement — Detecting Infographic / KPI Pages in Nonprofit Reports

*Prepared for external advisory consultation. Self-contained; internal infrastructure detail intentionally omitted.*

---

## 1. Context

We operate a pipeline that extracts **impact metrics** (e.g. "27,577 patient visits," "63,955 emails delivered") from nonprofit **annual and impact reports** at scale. Source documents are PDFs of widely varying design. The pipeline today:

1. **Parses** each PDF with a commodity open-source document parser (Docling) that produces text in *reading order*, plus per-element bounding-box coordinates.
2. **Extracts** metrics with an LLM that reads the parsed text.
3. **Validates** each extracted metric by requiring its supporting quote to be a *verbatim, contiguous substring* of the parsed text (a strict grounding check); anything that fails is withheld ("quarantined"), never published.

The corpus is **~333,000 documents and growing**; roughly **90,000** are annual/impact reports. We are early (pilot): only a few percent has been parsed and extracted so far, so decisions made now shape how the bulk is processed.

## 2. The problem we need help with

A large and **growing** fraction of these reports present their best numbers as **designed "infographic" / KPI pages**: multi-card visual layouts where a category label (e.g. "EMAIL CAMPAIGN," "MEDIA") sits near a cluster of hero numbers and short uppercase sub-labels, arranged in columns or cards rather than prose.

On these pages, reading-order linearization **breaks**: the parser interleaves cards and separates each hero number from its category label. Downstream, the metric becomes **context-orphaned** ("63,955 delivered" — delivered *what*?) or fails the grounding check entirely. We have confirmed a working fix for *cleanly structured* infographic pages — re-group the page by coordinates, then extract — but it **regresses ordinary prose pages** if applied blindly, and it **fails on dense/irregular** infographic layouts.

Therefore the fix must be applied **surgically**, only to the pages (ideally the regions) that need it. **The precondition — and our specific ask — is reliable detection of infographic / KPI pages (and, ideally, segmentation of the card regions within them), robust across enormous design diversity and at corpus scale.**

## 3. Evidence we have gathered (to calibrate the problem)

- **Prevalence.** On a representative extracted slice, **quarantine/failure rate rises monotonically with figure density**: text-only pages ~11%, moderate ~17%, figure-heavy pages ~21%. Figure-heavy reports are the **norm** — ~94% of documents carry ≥5 figures. The dominant failure signature (≈89% of all withheld metrics) is "all the words are present but not contiguous" — i.e. a number separated from its label by layout, **not** an OCR or character-decode problem.
- **Two ends of the difficulty spectrum** (both real documents):
  - **Clean card grid:** three clear columns; each label vertically adjacent to its stats. Coordinate re-grouping fixes it cleanly and the metrics become complete and verifiable.
  - **Dense mosaic:** ~126 tightly-packed elements on one page, multiple card labels sharing a row at different x-positions, tokens glued together. Naive geometric grouping scrambles it.
- **Signals already available, cheaply (CPU), per page:** every text element's **bounding box**, the page's **figure/picture count**, **text character density** (chars per page), **table presence**, and a rasterized **page image** on demand.
- **Crude heuristics we've tried:** thresholds on figure count and text density (e.g. ≥10 figures and <1200 chars/page) coarsely flag "designed" pages, and coordinate-clustering separates clean grids — but neither is precise enough for surgical routing, and both degrade on dense or hybrid layouts.

## 4. The detection problem, stated precisely

Given a PDF page (with text + bounding boxes, and optionally its rendered image):

1. **Page-level classification:** is this a *designed infographic / KPI page* whose metrics will be mis-linearized by reading-order parsing — as distinct from prose, tabular, or cover/decorative pages?
2. **(Stretch) Region segmentation:** delineate the **card / group regions** within such a page (the label-plus-numbers units), so a number can be re-associated with its correct category label.
3. **(Refinement) Structure-clarity / "regroupability" score:** because our downstream fix succeeds only on cleanly-structured infographics and struggles on dense mosaics, it would help to estimate *how tractable* a given page is — not just a binary flag.

Constraints: must scale to **hundreds of thousands of documents (growing)**; must generalize across **highly diverse, evolving designs** (every organization's designer differs); must keep **false positives low** (mis-flagging a prose page triggers a fix that degrades it); cost-sensitivity favors a cheap CPU pre-filter with selective use of any heavier model.

## 5. Questions for the advisors

1. **Architecture.** For page-level infographic detection at this scale and design diversity, what is the right approach and accuracy/cost sweet spot — geometric/statistical features on parser coordinates, a document-image classifier (CNN/ViT), a document layout-analysis / object-detection model on the page image, a vision-language model, or a **hybrid** (cheap geometric pre-filter → heavier model only on uncertain pages)?
2. **Off-the-shelf vs build.** Are there pretrained models, datasets, or tools (e.g. document layout analysis, "figure/infographic region" detection, document-type classification) we should adopt rather than build? What are their known limits on *designed marketing-style* pages (vs scientific-paper layouts most layout datasets target)?
3. **Region segmentation.** What is the most robust way to delineate visual *card* boundaries (so a number maps to its label) across diverse designs — coordinate clustering, layout models, or a VLM — and is detection + segmentation better solved **jointly** than separately?
4. **Labels & evaluation.** How would you bootstrap a labeled set cheaply (weak/auto-labels from our existing signals, active learning)? What evaluation protocol and precision/recall **operating point** make sense, given that the downstream fix is *partial* and false positives regress prose?
5. **Hard cases.** How should we handle **hybrid pages** (part prose, part infographic) and **design drift** over time? Should detection be page-level, region-level, or both?
6. **Feasibility.** Is reliable detection achievable CPU-only, or does it require GPU inference at our scale — and what throughput/cost should we plan for?

## 6. Scope note

This request is about **detection** (and optionally region segmentation). The downstream re-grouping + extraction changes, and an unrelated rare font-decode issue (~0.2% of metrics), are separate workstreams. We are seeking guidance on the most reliable, scalable way to **identify the pages/regions that need special handling** so we can apply the fix surgically.
