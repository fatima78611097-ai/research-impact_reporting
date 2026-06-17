# Pipeline Flowchart — plain-language walkthrough of the metric machine

*A non-graphical flowchart of the complete pipeline engineered to date. Two content columns:
the real engineering label, and what it actually does in plain terms. Flow is top→bottom; each
metric travels down the stages and either survives to **published** or is dropped
(**quarantined**) along the way. Pairs with `research-pipeline-manifest.md` (the bill-of-materials)
and `production-requirements.md` (the obligations). Updated 2026-06-14.*

```
INPUT: a nonprofit report PDF
        │
        ▼
```

## Autonomous production pipeline (stages 0–8 — no human in the loop)

| # | Engineering label | What it actually does (plain) |
|---|---|---|
| **0a** | Material-type filter | Is this even an annual/impact report? Throw out tax forms and financial-only statements before wasting effort. |
| **0b** | Low-value doc gate | Is the report mostly fluff (no real numbers, all narrative/event photos)? If so, skip the whole document. |
| **0c** | NTEE cohort onboarding | When a new charity category shows up, run a small audit batch through *review* first before trusting it. |
| | | ↓ |
| **1** | Parse (Docling config B) | Turn the PDF into machine-readable text + remember where every piece sat on the page (the coordinates). |
| | | ↓ |
| **2** | Render / marker tagging | Label every text chunk and table cell with a tag (t01, c04…) so we can later point at exactly which one a number came from. |
| | | ↓ |
| **3** | Geometry re-grouping | On designed pages, physically match each number to the caption next to it *before* the model reads it — fixes the old "wrong number stuck to wrong label" problem (mispairing). Genuine conflicts get quarantined, never silently relabeled. |
| | | ↓ |
| **4** | Extraction (locked LLM prompt) | The model reads the tagged page and writes each metric as a complete sentence, citing which tags the value and subject came from. |
| | | ↓ |
| **5** | Grounding gate (deterministic) | Is the number the model claims actually printed at the tag it cited? If the value isn't really there, drop it. (Hardened so a stray "1" or "2" can't fake a match.) |
| | | ↓ |
| **6** | Validity / is-a-metric | Is this truly a measurement, not a year, a tenure, a forecast, a population stat, or a one-person anecdote? Apply the locked definition; non-metrics dropped. |
| | | ↓ |
| **7** | Repair (recall recovery) | Before discarding, try cheap safe fixes — swap a mis-tagged twin marker, check the neighbor cell — and re-run the gate as a guard. Recovers good metrics that were one tag off. |
| | | ↓ |
| **8** | Verify stage (vision) | The hard residual: a cheap filter flags suspicious ones, then a vision model actually *looks at the page image* and rules. (~$1 per 600 docs.) |
| | | ▼ |
| | | **PUBLISHED**  vs  **QUARANTINED** |

## Parallel track (same machine, different content)

| # | Engineering label | What it actually does (plain) |
|---|---|---|
| **9** | Stories pipeline | Same flow for impact *stories* instead of numbers: locked 3-part definition, a qualifier stage, a span gate, and PII handling (names get bracketed/redacted at publish). |

## Sitting OUTSIDE the pipeline — not a stage in it

| Workflow | What it actually does (plain) |
|---|---|
| **Review** (dev only) | You read metrics by hand against the source. Its *only* purpose is to validate the pipeline — build the ground-truth answer key and prove each gate before it's trusted to run unattended. It is never part of the production run. |
| **Vision ground-truth fixture + test suite** | The frozen answer key (1,605 metrics) and the 9-check contract every change must pass. This is how a gate "earns trust" before shipping. |

## Two honest gaps in stage 6 (not yet real gates)

| Engineering label | Plain | State |
|---|---|---|
| Ownership-amplification check | Catches the model upgrading the org's role ("presented to" → "hosted," "made available" → "secured"). | Validated on 3 cases (2026-06-13); **not measured at scale, not a gate yet** |
| Modifier-class drift | Catches a changed describing-word ("capital" vs "commercial" investment). | **Open — nothing catches it** |

---

**Framing note:** stages 0–8 are the **autonomous production pipeline** (no human). Review and the
fixtures are the **validation workflow** that proves it — a separate workflow whose intention is to
validate the pipeline, never a stage within it. The two faithfulness items above are the live
frontier and are still pre-gate.
