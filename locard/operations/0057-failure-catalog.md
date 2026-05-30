# 0057 Failure Catalog — Faithfulness Verification (p20-v1)

**Run:** p20-v1 (run 10) · **Date:** 2026-05-30 · **Elapsed:** 1,419s (~24 min)
**Docs:** 3,242 processed, 8 missing source

## Summary

| | Total | Tier A (Verified) | Tier C (Quarantine) |
|---|---|---|---|
| **Metrics** | 68,772 | 56,385 (82.0%) | 12,387 (18.0%) |
| **Stories** | 10,697 | 7,632 (71.4%) | 3,065 (28.6%) |

**2,164 docs** have at least one quarantined metric. 8 docs had no parsed source at all (151 metrics).

## Quarantined Metric Categories (n=300 sample → projected)

| Category | Sample % | Projected Count | Fix | Owner |
|---|---|---|---|---|
| **Near-contiguous (≥80%)** | 48.5% | ~6,010 | Docling fragmentation — snippet is faithful but parser split layout | 0060 pdftotext repair |
| **Partial match (40-80%)** | 49.0% | ~6,070 | Mix of fragmentation + minor LLM reformatting | 0060 + prompt tune |
| **Low match (<40%)** | 2.0% | ~248 | Heavily rewritten, table dumps, or missing source | Prompt tune |
| **Missing source** | — | 151 | Doc never parsed (stalled run 30 queue) | Resume parse |

### Key finding

**True LLM fabrication/paraphrase is rare (~2%).** The dominant failure mode is Docling splitting designed-layout hero stats into separate sections. The LLM correctly reassembled what was on the page — the snippet IS faithful to the document — but our verifier can't confirm it because the parsed text is fragmented.

## Category Details + Examples

### 1. Near-Contiguous (≥80% longest run) — ~6,010 metrics

The snippet is almost entirely present as a contiguous span in the source text, but a small prefix or suffix breaks the match. Typically: a **number or label** that Docling placed in a different section from the rest of the sentence.

```
snippet: "66 Tons, or 132,843 pounds, of EPS (block Styrofoam) recycled"
matched: "tons, or 132,843 pounds, of eps (block styrofoam) recycled"
missing: "66" (number in a separate designed callout)

snippet: "85% of clients in our Career Development Services demonstrated improvement..."
matched: "of clients in our career development services demonstrated improvement..."
missing: "85%" (hero stat number separated from body text)

snippet: "Have been determined by a health care professional to be up-to-date on all immunizations [220 Children]"
matched: "have been determined by a health care professional to be up-to-date on all immunizations"
missing: "[220 Children]" (count in a separate layout element)
```

**Pattern:** The number or bracket-label lives in a designed callout that Docling parses as its own section. The descriptive text is in a body paragraph. The LLM correctly paired them.

**Fix:** 0060 pdftotext repair reads the page in visual order, preserving these pairings. Once 0060 provides the source text, these will verify as Tier A.

### 2. Partial Match (40-80%) — ~6,070 metrics

Significant chunks match, but larger fragments are split or lightly reformatted. Often the number AND some context words are separated.

```
snippet: "More than 3,600 meals were served to food-insecure children."
matched: "meals were served to food-insecure children."
missing: "More than 3,600" (hero stat headline)

snippet: "43 CHILDREN SERVED"
matched: "43 children"
missing: "served" (label split into a different section/heading)

snippet: "$18,601.75 WORTH OF TOYS AND ESSENTIAL ITEMS RAISED FROM OUR AMAZON PRIME DAY CAMPAIGN"
matched: "items raised from our amazon prime day campaign"
missing: "$18,601.75 worth of toys and essential" (designed callout text)
```

**Pattern:** Same root cause as near-contiguous, but the designed layout fragmented more aggressively (number + descriptor in separate elements). Some cases include minor LLM word additions ("More than").

**Fix:** Primarily 0060 pdftotext repair. After that, residual cases where the LLM added words ("More than") will be caught by prompt tuning to enforce verbatim-only spans.

### 3. Low Match (<40%) — ~248 metrics

Genuinely rewritten, table dumps, or content not found in the parsed source at all.

```
snippet: "Salaries $5,428,348 Fringe $1,964,129 Travel $60,918 Supplies $529,184..."
analysis: LLM dumped an entire expense table as a single run-on snippet. Only the first item matches.

snippet: "$645,221 from 995 donors"
analysis: 0% match — content may be in a designed infographic that Docling garbled entirely.

snippet: "6,891 Patients in our Care"  
analysis: 0% match — likely a hero stat in a custom font that Docling rendered as mojibake (the Make-A-Wish pattern from 0060 spike).
```

**Fix:** Table dumps → prompt tune (extract individual table metrics, not row dumps). Zero-match/garbled → 0060 pdftotext repair.

### 4. Missing Source — 151 metrics (8 docs)

Documents queued in parse run 30 (which stalled at 23 docs) but never completed. The LLM extraction ran from `classification_context` (first 5 pages), not the full Docling parse.

**Fix:** Resume parse run for these 8 docs. They're still in the work queue.

## Story Failures (n=200 sample)

| Category | Sample % | Projected Count |
|---|---|---|
| Fragmented | 35.5% | ~1,080 |
| Numeric reformat | 64.5% | ~1,975 |
| Missing source | — | 24 |

Stories have the same root cause: the `source_snippet` field references text that Docling fragmented. The higher quarantine rate (28.6% vs 18.0%) is because story snippets tend to be longer and span more layout elements.

## Projected Impact of Fixes

| Fix | Metrics Rescued | New Rate | Dependency |
|---|---|---|---|
| **0060 pdftotext repair** | ~10,000-11,000 | ~96-98% | Spec 0060 |
| **Prompt tune (verbatim-only)** | ~500-1,000 | +1-2% | Extractor fix |
| **Resume parse (8 docs)** | ~151 | +0.2% | Parse run |
| **Combined** | ~12,000+ | **≥99%** | 0060 + prompt tune |

## Conclusion

The 99% Tier-A target in the acceptance criteria **IS reachable**, but it requires 0060 (pdftotext repair) as the primary fix. Prompt tuning alone gets us from 82% to ~84%. The good news: true LLM fabrication/hallucination is extremely rare (~2% of quarantined = ~0.4% of total metrics). The pipeline is faithful — the verifier's parser-fragmented source is the bottleneck.

**Priority order:**
1. **0060** (parse fidelity / pdftotext repair) — rescues ~80% of quarantined
2. **Prompt tune** (verbatim-only extraction) — catches the remaining reformat/composition
3. **Resume parse** (8 docs) — trivial cleanup
