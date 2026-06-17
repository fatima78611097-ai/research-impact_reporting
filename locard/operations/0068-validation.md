# 0068 — Phase-5 validation results

Live dry run of the marker-citing extraction (render → one-pass loc1 → resolve) over
the eligible coordinate-bearing set. **No DB writes** (dry measurement). Harness:
`locard/operations/0068_validation.py --live`. Model: deepseek-chat @ temp 0, 60k char
cap, T=0.80, MAX_IDMAP_ITEMS=8000. Raw output: `0068-validation-results.json`.

## Dataset
- All **117** coordinate-bearing docs (`lava_parse.tables.cell_locations` populated).
- AC1 denominator = selected metrics on **eligible** docs only (skipped excluded).
- 117/117 eligible, 0 skipped (all parse_version `docling-2.93.0`, trusted).

## Results
| Metric | Value | Acceptance (spec §7) | Verdict |
|--------|-------|----------------------|---------|
| **LOC1_BASELINE** (AC1 resolvable-`value_ref` rate) | **84.79%** (1054/1243) | the frozen reference; future runs ≥ baseline−5pp (≥ 79.79%) | ✅ set |
| AC2 table-cell coverage | **100.0%** | 100% of table-located values → ⟨c#⟩ with row+col+bbox | ✅ |
| AC3 round-trip | **100.0%** (1054/1054) | stored coords == idmap[value_ref] | ✅ |
| selected metrics | 1,243 | — | — |
| resolvable metrics | 1,054 | — | — |
| null / image-only metrics | 189 (15.2%) | stored + flagged, not dropped (AC5) | ✅ first-class |

Per-doc: **86 of 117 fully resolvable**; **6 docs 0-resolvable** (image-heavy — the
featured numbers are image-only, legitimately `value_ref=null` → vision/0069
candidates, per spec §3 recall note).

5-doc pre-run smoke matched: AC1 85.1%, AC2 100%, AC3 100%.

## Reading
- **AC1 = 84.79% is LOC1_BASELINE** (frozen). It sits in the spec's Phase-−1
  calibration band (~85–90% correct). The 15.2% non-resolvable are image-only /
  unlocatable values — a first-class outcome (not a miss), routed to the vision path.
- **AC2 = 100%**: every value located in a table resolved to a cell marker carrying
  row+col+bbox — the co-location anchor 0069 needs for the mispair guard.
- **AC3 = 100%**: the resolved coordinate snapshot is byte-identical to the idmap
  entry across the full 1,054-metric sample — idmap recompute is deterministic and the
  stored coords are trustworthy.

## Status
- One-pass loc1 confirmed sufficient (within tolerance; no two-pass fallback needed).
- This dry run wrote nothing. The persisted run (`--write` under a run_tag) is **held
  for architect go**; the migration is applied live (15 cols, both bbox CHECKs, index,
  74,240 legacy rows sentinel-backfilled).

## Follow-ups (tracked)
- **AC6 — CanCare/BGCSM fixture regression (deferred, architect PR #53 review
  2026-06-17).** The frozen baseline (`frozen/composed-baseline-2026-06-14/`) is the
  **composed-sentence** path; 0068's loc1 selection differs by design, so a literal
  "content unchanged vs frozen" diff is not well-posed here. **Re-scope:** once 0069's
  published surface exists, add a regression comparing 0068 marker output ↔ 0069
  published metrics for CanCare/BGCSM (value+label set stability), in
  `test_0068_fixtures.py`. De-checked from spec §7 / plan AC matrix with architect
  authorization.
