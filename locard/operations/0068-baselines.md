# 0068 — Phase-0 baselines & frozen decisions

Spec/plan-deferred numbers, set with evidence before build. Measured 2026-06-16.

## Dataset
- **Coordinate-bearing set:** 117 documents with `lava_parse.tables.cell_locations`
  populated (the demo "49-doc" set is a historical subset; the live eligible set is
  derived programmatically by the §5.4 rule, not a frozen sha-list).
- Eligibility/items measured over all 117 (pure render, **no LLM**, DB read-only).

## Frozen constants (confirmed by measurement)
| Constant | Value | Evidence |
|----------|-------|----------|
| Coordinate threshold `T` (§5.4) | **0.80** (≥80% text elements carry bbox AND 100% cells carry row+col+bbox) | 117/117 (100%) eligible at 0.80; zero docs failed either sub-rule |
| `MAX_IDMAP_ITEMS` (§5.8) | **8,000** | items/doc: median 245, p90 1,165, p99 6,298, **max 6,441** → 8,000 clears the max with ~24% margin; 0 docs skipped `idmap_too_large` |
| Surge: per-doc quarantine (§5.9) | **>50% null/forged value_ref AND ≥3 invalid refs** | policy default (low-N floor, Gemini MEDIUM); tune post-launch on real forged-rate |
| Surge: per-run alert | skip-rate >20% OR forged-rate >5% | policy default; tunable |

These live as named constants in `lavandula/nlp/marker_render.py`
(`MAX_IDMAP_ITEMS`, `MAX_TAGGED_CHARS`) and `lavandula/nlp/marker_extract.py`
(`ELIGIBILITY_THRESHOLD`, `SURGE_*`) — change only with evidence.

## Live DeepSeek run — DONE (dry, 2026-06-16; see 0068-validation.md)
- **`LOC1_BASELINE`** (§7 AC1) = **84.79%** (1054/1243) over all 117 eligible docs.
  AC2 table-cell coverage = **100%**, AC3 round-trip = **100%** (1054 sample). Frozen
  as the acceptance reference (future runs ≥ baseline−5pp).
- **One-pass vs two-pass** confirmation (§10.3): one-pass loc1 ships if within 5pp of
  the demo's two-pass; measured in the same run.
- **AC2/AC3/AC6** (table-cell coverage, round-trip sample, CanCare/BGCSM fixture diff):
  all derive from the same live run.

Run with `python3 locard/operations/0068_validation.py --live --run-tag 0068-baseline`
(see harness header). Estimated cost: ~117 deepseek-chat calls (~$0.30 order). This
run is **not** executed by the builder — it spends compute and is surfaced for operator
go-ahead per the "surface decisions before running" rule.
