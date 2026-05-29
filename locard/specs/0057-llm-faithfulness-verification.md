# Spec 0057 — LLM Extraction Faithfulness Verification (Snippet Grounding)

- **Project:** 0057
- **Status:** conceived (initial draft — awaiting consultation, red-team, and human approval)
- **Depends on:** 0060 (Parse Fidelity Verification)
- **Author:** Architect, 2026-05-29

---

## 1. Problem & Motivation

The pipeline produces LLM-extracted **metrics** (`lava_vocab.llm_metrics`) and **story snippets** (`lava_vocab.llm_stories`), but nothing at the end **certifies** them. Every downstream "end result" — reports, the AI interviewer, the published lexicon, the API — inherits unverified data. For a product that will be scrutinized by mature, well-funded competitors, **"trust our AI" is not a defensible position.** We need an end-cap that makes every published fact **provably traceable to its source**, and that **honestly labels** what it cannot prove.

**Evidence this is real and necessary:**
- **Competitor benchmark** (Think New Mexico 2024-25 vs GivingCompass): we extract far more (29 metrics vs 4) but our own grounding is imperfect — 24/29 normalized-verbatim, **5/29 LLM-composed** (defects). The competitor was worse (1/4 verbatim, 3/4 paraphrased), but "better than a loose competitor" is not the bar.
- **Corpus-wide spike** (run 10, 68,772 metrics + 10,679 stories): **86.7%** of metric snippets and **77.6%** of story snippets are grounded (whitespace-normalized); the ungrounded **13.3% / 22.4%** is a *mix* of true paraphrase defects and measurement artifacts (table cell-vs-prose, curly/straight quotes).

## 2. Goals / Non-Goals

**Goals**
- Every published metric/story carries a **provenance tier** (verified / unverified-labeled / quarantined).
- Verified facts are **provably grounded**: `source_snippet` is a verbatim span of the source text; value/unit/denominator present in that span.
- Unprovable facts (OCR-derived) are **published with a label**, never silently dropped.
- Genuine grounding failures (paraphrase/hallucination) are **quarantined** — never published.
- A per-run **faithfulness score** for monitoring + regression protection.

**Non-Goals**
- Proving "complete thought" / semantic completeness — irreducible; bounded + disclosed (see §7).
- Re-architecting the extractor. The extractive-span **prevention** (§5.2) feeds this gate but is its own work; this spec is the **verification backstop** + data-model + disclosure contract.

## 3. The Integrity Chain (context)

```
PDF ──[0060: text-layer agreement]──▶ parsed text ──[0057: verbatim grounding]──▶ metric/story ──[link-to-source]──▶ published
       "Docling didn't lie"                  "the LLM didn't paraphrase"             "context one click away"
```

0057 is link 2. It **assumes 0060 certifies link 1**, and consumes 0060's *verifiable-vs-OCR* signal to set tiers. Critically, 0057 must ground against **0060-repaired text** (Docling garbles custom-font hero stats; `pdftotext` recovers them) — otherwise a garbled source produces a false quarantine.

## 4. Requirements

### 4.1 Three legs of faithful capture
1. **Verbatim provenance** — `source_snippet` is an exact span of the doc's parsed text (sections + tables), under the §4.2 normalization.
2. **No semantic paraphrase** — composed/editorialized prose is a defect; the snippet is *copied*, not *authored*.
3. **Structural fidelity** — ratio/proportion metrics stored as **numerator + denominator (+ derived %)**; scope captured as structured fields, never collapsed to a bare scalar. ("46 *of 89*", not "46"; "$2B *proposed*", not "$2B".)

### 4.2 Normalization policy (KEY DECISION)
Grounding = exact substring **under normalization**: collapse whitespace + **Unicode NFC** + curly/straight **quote-folding**; plus **table-row-aware** matching (a `label: value` snippet matches when label and value co-occur in one table row, even if not contiguous). This is **not** semantic paraphrase tolerance. Rationale (spike): artifact categories (c) table-cell and (d) quotes inflated the crude 13%/22%; hardening the check separates true defects from artifacts. **The hardened check IS the gate logic.**
- *Open:* number reformatting ("$2.8M" vs "$2.8 million") — normalize, or treat as a defect? (see §10)

### 4.3 Graded provenance + disclosure ("truth in advertising")
| Tier | Condition | Treatment |
|---|---|---|
| **A — Verified** | text-native (0060-covered) + grounded + value present | publish, badge "✓ sourced" |
| **B — Unverified (OCR)** | scanned/image source (0060 can't certify) | **publish WITH a label** ("OCR-derived, not verified", + confidence) — do NOT quarantine |
| **C — Quarantine** | genuine grounding failure after normalization | **never published** |

Surface the tier as a badge in the viewer + API + a methodology note. **Guardrail:** labels stay targeted/rare (the exception, pointing at specific facts) — a blanket disclaimer reads as the generic AI hedge and destroys the "provable" value prop.

### 4.4 Data-model changes
Add to `llm_metrics` (and provenance fields to `llm_stories`):
- structured value for ratios (**numerator/denominator** or structured JSON), preserving the denominator;
- **modality** (achieved / proposed / advocated / projected — NEW); `time_aggregation` and `geo_impact` already exist;
- **verification_tier**, **grounding_offsets** (char offsets into source), **context_window** (surrounding sentence(s)).

## 5. Technical Implementation

### 5.1 The gate — a decision procedure (exact, not statistical)
For each fact: load 0060-repaired source text → normalize (§4.2) → test snippet membership (substring + table-row-aware) → test value/denominator presence → assign tier → quarantine Tier C. **Per-fact verdict is a proof; statistics only summarize the rate.**

### 5.2 Extractive-span prevention (recommendation; feeds the gate)
Constrain the extractor to emit a **verbatim span / char offsets** (extractive, not abstractive). A fact whose snippet can't be located shouldn't be emitted. This **reduces** "did the AI understand?" (unprovable) to "is this span present?" (decidable).

### 5.3 Test → fix posture
- **Phase 1:** build + run the gate over existing data → *failure catalog* (baseline 13%/22%, four categories).
- **Phase 2..N:** extractor fixes driven by the catalog (target the genuine category-(a) composed-prose and (b) reformatting defects) → re-run gate → failure set shrinks → iterate to target.
- **Final:** lock the gate as a permanent regression guard.

## 6. Acceptance Criteria
- Gate assigns a tier to **100%** of metrics/stories; quarantines all post-normalization Tier-C.
- After extractor fixes, **≥ [TARGET, propose 99%]** of non-OCR facts are Tier-A verified.
- **0 published facts are ungrounded** (Tier C never reaches output).
- Per-run faithfulness score emitted and trended.
- Normalization policy documented + unit-tested (whitespace / Unicode / quote / table-row).
- Any extractor change validated by a quality A/B using **cell-content diffs, not just counts**.

## 7. Context Completeness (the third, partly-unsolvable tier)
"Is it the complete thought the author conveyed?" is **semantic/judgment — not provable** (even humans disagree at the margin; "complete" has no fixed boundary). We do **not** attempt to prove it. We **bound and disclose**:
- **(a) Structural scope capture** — modality, time, geo, condition — so the obvious distortions (proposal-as-achievement, e.g. Think New Mexico's "$2B Medicaid trust fund" which is *advocated*, not raised) become detectable.
- **(b) Context-sensitivity flag** — linguistic markers (hedges/conditionals/temporal/partial) in the context window → stamp "context-sensitive — qualifier required."
- **(c) Never sever from source** — preserve offsets/context window so the complete thought is always one click away. *The source IS the complete thought; we faithfully preserve the link, we don't re-summarize it.*
- **(d) Use-case-gated presentation** — viewer (context at hand) may show bare; standalone surfaces (report / AI interviewer / API / lexicon) must carry scope+qualifier or the "context-sensitive — see source" flag.

## 8. Dependencies
- **0060 (Parse Fidelity)** — supplies (1) the verifiable-vs-OCR signal that sets Tier A vs B, and (2) the **`pdftotext`-repaired text** to ground against (Docling garbles ~custom-font hero stats; grounding against garbled text would false-quarantine real facts). Uses 0060's **bidirectional-coverage** gate (NOT sequence alignment — see Traps).
- **The LLM extraction pass** (`run_tag p20-*`) — the extractive-span prevention (§5.2) lands here.

## 9. Traps to Avoid (lessons banked 2026-05-29)
- **Do not use sequence-alignment** for grounding/fidelity — it conflates reading-order with content (proven in 0060: identical-content docs scored align=0.20 from order alone). Use set/substring membership.
- **Do not blanket-disclaim** — targeted per-fact labels only, or the value prop dies.
- Whitespace/Unicode normalization is fine; **semantic paraphrase is a defect** — define the line precisely.
- **Do not collapse ratios to scalars** (the denominator is part of the metric).
- **Cell-count parity ≠ cell-content correctness** when validating extractor changes.
- The grounding **rate** is statistics; the per-fact **verdict** is a proof — never conflate.

## 10. Open Questions
- Number-reformatting normalization ("$2.8M" vs "$2.8 million") — normalize or defect?
- Modality enum — finalize (achieved / proposed / advocated / projected / …).
- Target verified-rate threshold (propose ≥99% of non-OCR).
- Structured-value representation — numerator/denominator columns vs structured JSON.
- Story grounding: stories are longer/narrative — does the same span-membership rule apply, or a relaxed "quote substring" rule for direct quotes within a summary?
