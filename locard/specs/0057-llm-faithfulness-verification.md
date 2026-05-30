# Spec 0057 — LLM Extraction Faithfulness Verification (Snippet Grounding)

- **Project:** 0057
- **Status:** specified (multi-agent review + red-team incorporated; human-approved 2026-05-29)
- **Depends on:** 0060 (Parse Fidelity Verification)
- **Author:** Architect, 2026-05-29

> **Terminology note (per review):** the gate establishes **verifiable presence** — a deterministic membership check that a snippet is a span of the source, and that the value/denominator appear in it. That is an exact, reproducible *proof of presence*. It is **not** a proof of *semantic correctness* or completeness. We use "verified"/"grounded"/"deterministic membership" — never "provably true."

---

## 1. Problem & Motivation

The pipeline produces LLM-extracted **metrics** (`lava_vocab.llm_metrics`) and **story snippets** (`lava_vocab.llm_stories`), but nothing certifies them. Every downstream "end result" — reports, the AI interviewer, the lexicon, the API — inherits unverified data. For a product scrutinized by mature competitors, "trust our AI" is indefensible. We need an end-cap that makes every published fact **traceable to its source by a deterministic check**, and that **honestly labels** what it cannot verify.

**Evidence:** competitor benchmark (Think New Mexico 2024-25 vs GivingCompass) — we extract 29 metrics vs 4, but 5/29 of ours are LLM-composed (defects). Corpus spike (run 10, 68,772 metrics + 10,679 stories): **86.7% / 77.6%** grounded (whitespace-normalized); the ungrounded **13.3% / 22.4%** is a *mix* of true paraphrase defects and measurement artifacts (table cell-vs-prose, curly/straight quotes).

## 2. Scope (what 0057 ships) / Non-Goals

**0057 delivers:** the **verification gate** (the deterministic grounding decision procedure), the **data-model additions** (tier, offsets, structured scope), the **graded-disclosure contract**, and a **per-run faithfulness score**.

**Companion dependency, NOT shipped by 0057:** the *extractive-span prevention* in the LLM extractor (§5.2) — it reduces defects upstream and feeds the gate, but it lives in the extractor project. 0057 must function as a backstop regardless of whether prevention has landed.

**Non-Goals:** proving "complete thought"/semantic completeness (irreducible — bounded + disclosed, §7); the exact migration DDL (plan phase).

## 3. The Integrity Chain

```
PDF ─[0060: text-layer agreement]→ parsed text ─[0057: deterministic grounding]→ metric/story ─[link-to-source]→ published
```
0057 is link 2; it assumes 0060 certifies link 1 and consumes 0060's verifiable-vs-OCR signal to set tiers. It grounds against **0060-repaired text** (`pdftotext` where Docling garbled) — else a garbled source falsely quarantines real facts.

## 4. Requirements

### 4.1 Three legs of faithful capture
1. **Verbatim provenance** — `source_snippet` is an exact span of the source text (sections + tables) under §4.2 normalization.
2. **No semantic paraphrase** — composed/editorialized prose is a defect; the snippet is *copied*, not *authored*.
3. **Structural fidelity** — ratios stored as **numerator + denominator (+ derived %)**; scope as structured fields; never a bare scalar. ("46 *of 89*"; "$2B *proposed*".)

### 4.2 Matching rules (the gate's core — precise, with counterexamples)
A snippet is **GROUNDED** iff, after normalization N, it satisfies one of:
- **(R1) Contiguous span:** `N(snippet)` is a substring of `N(section_text)`.
- **(R2) Same-table-row:** every token of the snippet's `label` AND its `value` appear within a single normalized table row (handles "Total expenses: $26,122,404" where label/value are separate cells).

Normalization **N** = lowercase → collapse whitespace → Unicode **NFC** → fold curly↔straight quotes/dashes. N does **not** reorder tokens and does **not** alter digits/units.

| Case | Verdict | Why |
|---|---|---|
| snippet matches source after whitespace/quote/NFC fold | **GROUNDED (R1)** | benign rendering diff |
| "Total expenses: $26,122,404", source has them in one table row | **GROUNDED (R2)** | row-local label+value |
| "420 Naturalizations is a 65% increase from last year!" (source has parts, not this sentence) | **DEFECT** | composed prose |
| "$2.8M" where source says "$2.8 million" | **DEFECT** (default) | numeric reformat — see §10 |
| tokens present but scattered across the doc, not a span or a row | **DEFECT** | not a contiguous/row span; scatter ≠ grounding |

### 4.3 Graded provenance + disclosure ("truth in advertising")
| Tier | Condition | Treatment |
|---|---|---|
| **A — Verified** | text-native (0060-covered) + grounded (R1/R2) + value present | publish; badge "✓ sourced" |
| **B — Unverified (OCR)** | scanned/image source (0060 cannot certify) | **publish WITH a label** + a confidence; do NOT quarantine |
| **C — Quarantine** | grounding failure after normalization | **never published** |

**Tier-B confidence** = a defined, recorded signal — from 0060: OCR present + multi-reader agreement score (or the OCR engine's mean token confidence); stored, not invented. **Downstream policy:** Tier B is allowed into the viewer and the API **with its label and confidence surfaced**; standalone/published surfaces (report, AI interviewer, lexicon) must render the "unverified — OCR" badge and may be filtered by a minimum-confidence threshold per consumer. **Guardrail:** labels stay targeted/rare; never a blanket disclaimer.

### 4.4 Data-model additions (intent; exact DDL → plan)
On `llm_metrics` (provenance fields also on `llm_stories`):
- structured ratio value — **numerator/denominator** (or structured JSON), denominator preserved;
- **modality** enum — `achieved | proposed | advocated | projected | unspecified` (NEW); `time_aggregation`, `geo_impact` exist;
- **verification_tier** enum; **grounding_rule** (R1/R2/none); **grounding_offsets** `[start,end]` into the source; **context_window** (surrounding sentence(s)); **tier_b_confidence** (nullable).
- **Multi-span facts** (a fact supported by >1 span): offsets is a list; grounded iff *all* required spans match.
- New nullable columns → backward compatible (existing rows default `tier=unverified-legacy` until re-gated).

## 5. Technical Implementation

### 5.1 The gate — a deterministic decision procedure
Per fact: load 0060-repaired source → normalize (§4.2) → test R1 then R2 → test value/denominator presence → assign tier → quarantine Tier C. **Per-fact verdict is exact and reproducible; statistics only summarize the rate.** Must be **deterministic** (defined tie-breaks; same input → same verdict, recorded with `grounding_rule`).

### 5.2 Extractive-span prevention (companion dependency)
Constrain the extractor to emit a verbatim span / char offsets (extractive, not abstractive). Reduces "did the AI understand?" (unverifiable) to "is this span present?" (decidable). Lives in the extractor project; 0057 does not depend on it being done.

### 5.3 Test → fix posture
Phase 1: run gate over existing data → failure catalog (the 13%/22% baseline, four categories). Phase 2..N: extractor fixes driven by the catalog → re-run → shrink failures → iterate. Final: lock the gate as a regression guard with fixtures from the catalog.

## 6. Acceptance Criteria (frozen)
- Gate assigns a tier to **100%** of metrics/stories; quarantines all post-normalization Tier-C; **0 published facts are ungrounded**.
- After extractor fixes, **≥ 99%** of *non-OCR* facts are Tier-A verified (target frozen; revisit only with data).
- Gate is **deterministic** (re-run yields identical verdicts) and records `grounding_rule` + offsets per fact.
- Per-run faithfulness score emitted and trended.
- **Required test cases:** normalization edge cases (whitespace / NFC / quotes / dashes); table R2 (label+value across cells; multi-row tables); multi-span facts; numeric-reformat defect detection; Tier-B/OCR fallback path; quarantine behavior; **regression fixtures derived from the baseline failure catalog**; story rule (§4.5).
- Any extractor change validated by a quality A/B using **cell-content diffs, not just counts**.

### 4.5 Story grounding rule (decided)
A `story_summary` is, by definition, an abstractive summary — it **may** be composed. But: (a) the story's **`source_snippet`** (the evidence) must be **grounded (R1/R2)** like a metric; (b) any **direct quote** inside the summary (text in quotation marks) must be **verbatim-grounded**; (c) a story with no grounded source_snippet is **Tier C**. So stories relax the *summary* prose but hold the line on *evidence and quotes*.

## 7. Context Completeness (partly-unsolvable tier — bounded, not proven)
"Complete thought" is semantic/judgment, not verifiable (humans disagree; "complete" has no fixed boundary). We bound + disclose: **(a)** structural scope (modality/time/geo/condition) so obvious distortions (proposal-as-achievement) are detectable; **(b)** a context-sensitivity flag (linguistic markers in the window); **(c)** never sever from source — preserve offsets/context window; **(d)** use-case-gated presentation (viewer bare OK; standalone must carry scope+qualifier or "context-sensitive — see source"). *The source is the complete thought; we preserve the link, we do not re-summarize it.*

## 8. Security & Abuse Considerations
Source documents are external/untrusted input to a gate that controls publication:
- **Malformed / pathological source text:** bound the source text size loaded per doc; **timeout** per fact; reject control characters except those normalized.
- **Pathologically large snippets:** cap `source_snippet` length; oversize → defect (cannot be a legitimate verbatim span).
- **Offset integrity:** validate `grounding_offsets` are in-bounds of the source; never trust extractor-supplied offsets without re-checking the span at those offsets (offset-injection guard).
- **Determinism / DoS:** R2 table search bounded to the doc's own rows; no super-linear blowup on adversarial input.
- **Metadata leakage:** the disclosed tier/label/confidence must not expose anything beyond "OCR-derived / confidence N" — no raw source structure or internal scoring internals in public surfaces.

**Red-team additions (incorporated 2026-05-29; Gemini 0 CRITICAL / 2 HIGH):**
- **0060 input integrity (HIGH):** 0057 grounds against 0060-*repaired* text, so 0057 is only as trustworthy as 0060's repair. The repaired text 0057 consumes must itself be tier-stamped/verified by 0060; an unverified or compromised repair would let a "verified" grounding rest on bad text. The gate records *which* source (Docling vs pdftotext-repaired) each verdict grounded against.
- **Numeric reformat (HIGH):** "$2.8M" vs "$2.8 million" is a **defect by default**; any numeric-normalization allowlist (plan phase) must be explicit, bounded, and unit-preserving — never open-ended "smart" number parsing.
- **`context_window` is unverified display context, not evidence:** it is bounded in length, **never** counted toward grounding, and flagged display-only; if surfaced it carries its own offsets so it can be independently checked — it must not become a backdoor for ungrounded text riding along with a verified fact.
- **Multi-span bound:** the offsets list is capped at a small N (plan-set); a fact claiming support from many tiny scattered spans is rejected (anti-gaming / anti-DoS), and "required spans" is explicitly defined.
- **Control characters:** reject **all** non-printable characters except the whitespace produced by normalization; the allowed set is the explicit, enumerated output of N — not "whatever survives."

## 9. Failure & Error Scenarios (must be defined, fail-safe)
- **Source text missing/unloadable** → cannot verify → **Tier C (quarantine)** if text-native expected, or **Tier B** if known-OCR; never default to "verified."
- **Offsets uncomputable** → fall back to R1/R2 substring search; if still unlocatable → defect.
- **Repaired text (0060) vs extractor snippet disagree** → ground against the 0060-repaired text (source of truth for link 1); snippet not found there → defect.
- **Non-deterministic classification** is a bug → defined tie-break order (R1 before R2) and recorded rule.
- Fail-safe principle: **uncertainty resolves toward quarantine/label, never toward "verified."**

## 10. Open Questions (genuine plan-phase decisions)
- Number-reformatting ("$2.8M" vs "$2.8 million") — defect-by-default now; decide whether to add a bounded numeric-normalization allowlist in the plan.
- Structured-value representation — numerator/denominator columns vs structured JSON (DDL choice, plan).
- Exact `tier_b_confidence` formula (which 0060 signal) — plan, once 0060's repair/agreement output is finalized.
