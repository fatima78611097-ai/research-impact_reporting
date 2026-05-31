# Spec 0058 §4 — Cell-Content A/B Results (TEMPLATE — operator fills after the GPU run)

> **Status:** ⏳ NOT YET RUN. This is the persisted, replayable deliverable
> (plan Phase 5). It gates whether the Variant-B pipeline knobs (TableFormer FAST
> + capped images_scale + conditional OCR) ship. Reads the frozen sample
> `0058-ab-sample.json`. If any knob fails, revert that knob and re-test.

- **Sample manifest:** `0058-ab-sample.json` (seed + tool/version hashes pinned there)
- **Harness:** `lavandula/parse/ab_quality.py` — uses the REAL 0057 grounding primitive
- **Run:** `python manage.py … / python -m lavandula.parse.ab_quality --host … --database …`
- **Instance / docling / git SHA / date:** _fill in_

## Frozen thresholds (spec §4)
| Stratum | cell parity `|A∩B|/|A|` | lost rows | grounding | OCR words |
|---|---|---|---|---|
| text_native | ≥ 0.9999 | 0 | new Tier-C = 0 | — |
| scanned | ≥ 0.95 | ≤ 1% | new Tier-C = 0 | ≥ 0.95 |
| designed/image-heavy | report-only | report-only | flag if > 0 | — |
| poison/slow | outcome-only: B must not WEDGE (clean parse_crash/parse_timeout within the bound) |

## Per-doc results
| sha | stratum | conv_ms A | conv_ms B | tables A/B | cells A/B | cell cov | invention | lost rows | row Jaccard | OCR words | new Tier-C | speedup | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| _…_ | text_native | | | | | | | | | — | | | |
| _…_ | scanned | | | | | | | | | | | | |
| _…_ | designed_image_heavy | | | | | | | | — | | | |
| e038a9e7… | poison | — | — | — | — | — | — | — | — | — | — | — | _clean parse_crash? (no wedge)_ |

## Per-stratum aggregates
- text_native: min cell parity ___, total lost rows ___, total new Tier-C ___ → **PASS/FAIL**
- scanned: min cell parity ___, max lost-row frac ___, min OCR word cov ___ → **PASS/FAIL**
- TableFormer FAST speedup on table-heavy: median ___× (informational floor 1.3×)

## Knob-by-knob verdict (each must pass independently before shipping)
- [ ] **TableFormer FAST** — text-native zero cell loss + zero grounding regression
- [ ] **images_scale cap** — chosen cap value ___; no text-native parity loss
- [ ] **conditional OCR** — scanned docs keep OCR (word cov ≥ 0.95); text-native correctly skipped

## FINAL VERDICT: ☐ PASS (ship Variant B) ☐ CONDITIONAL (ship subset) ☐ FAIL (revert)

**Which knobs ship (flip in config.py): _______________________________**
(e.g. set TABLEFORMER_FAST=True, IMAGES_SCALE_CAP=… only for the knobs that passed.)
