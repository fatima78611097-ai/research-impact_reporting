# 0064 — The solution: try-and-measure regroup gate (no KPI classifier)

**Date:** 2026-06-02. This supersedes the detector/classifier line of work.

## Why every classifier stalled at ~30%
We spent the heuristic (28%), DeepSeek-layout (22%), and vision (84% class agreement but
12% regroup-recall) all trying to answer **"is this a KPI page?"** — to decide up front
whether to regroup. The disputed-page experiment proved that is the **wrong question**:

| Page | human label | vision label | baseline grounded | after fix | truth |
|---|---|---|---|---|---|
| `f3dc12b3` p3 | kpi_clean | mixed | **3 / 10** (broken) | **23** | fix HELPS |
| `2fccbb57` p9–11 | kpi_clean | mixed | **28 / 28** (perfect) | 6 | fix HARMS |

Both look like "clean KPI cards"; both were labeled `kpi_clean` by the human and `mixed`
by vision. **Neither label predicts whether the fix helps.** On one it recovers ~20
metrics; on the other it wrecks a page that already extracted perfectly. The KPI-vs-not
boundary is irrelevant — that's why no classifier could crack it.

## The signal that actually predicts fix success
**Is the baseline already broken on this page?** (= does it have quarantined metrics?)
- broken baseline (f3dc12b3 3/10) → regrouping can recover → helps.
- healthy baseline (2fccbb57 28/28) → nothing to fix → regrouping only hurts.
This is **deterministic and already computed** by the Spec 0057 gate (quarantine counts).

## The architecture (try-and-measure)
Per candidate page — **no KPI classification**:
1. **Candidate = page has baseline-quarantined metrics.** Healthy pages are never touched.
2. Apply the regrouping fix (column-major + KPI parent prompt), re-extract.
3. **Ground the fixed output's snippets against the page's ORIGINAL text** (symmetric — a
   scrambled mis-pairing is NOT a contiguous span of the original, so it fails here; this
   closes the "grounds-against-its-own-scrambled-serialization" loophole that made the
   mosaic look 100% grounded).
4. **ACCEPT iff** the fix recovers grounded metrics (>0 new) **AND loses none** of the
   baseline-grounded values. Else keep baseline.

A wrong candidate guess is **harmless**: the measurement throws it away. Expected decisions:
EMAIL CAMPAIGN p13 ACCEPT, f3dc12b3 ACCEPT, 2fccbb57 REJECT, mosaic REJECT, financial/prose
never-candidate (already grounded). Validation run: `regroup_gate.py` (results pending).

## VALIDATED on the 6 known pages (`regroup_gate_spatial.py`, 2026-06-02)
| page | candidate? | decision | why |
|---|---|---|---|
| 001eb5f8 p13 (EMAIL CAMPAIGN) | yes | **ACCEPT** ✓ | enriched 10 metrics w/ coherent parent, lost 2 |
| f3dc12b3 p3 | yes | **ACCEPT** ✓ | recovered 1 + enriched 4, lost 1 |
| 2fccbb57 p9 (looks KPI, already works) | no | **SKIP** ✓ | baseline 9/9 grounded — healthy |
| 61d4b4bb p1 (mosaic) | yes | **REJECT** ✓ | lost 9/22 (41%) — scramble caught |
| 001eb5f8 p20 (financial) | no | **SKIP** ✓ | healthy |
| 001eb5f8 p7 (prose) | no | **SKIP** ✓ | healthy |

6/6 correct, no KPI classifier. The two earlier accept-measures failed first
(text-grounding-vs-original rejected everything; spatial-but-strict rejected p13
on jitter) — the working version uses comma-aware number location + a 25%
lost-budget that tolerates extraction jitter while rejecting scramble.

## Honest caveats (proven vs not)
- **Proven:** the architecture and the candidate filter. The filter alone is robust
  and deterministic (skip pages with no baseline quarantine).
- **Tuned, needs scale validation:** the thresholds (col-gap 0.09·w, coherence
  x<0.18·w / y<0.32·h, lost-budget 25%) were fit on 6 pages. Must be validated on the
  broader candidate population (all pages with baseline quarantine) before trusting at
  scale; they may need adjustment.
- **Two LLM extractions per candidate page** (baseline + fixed) — bounded because only
  quarantine-bearing pages are candidates, but a real cost to size at corpus scale.
- Per-page (not per-doc); production runs it on each candidate page.

## Why this solves the problem
- Removes the fuzzy classifier entirely — the thing that blocked us for the whole effort.
- Recovers the genuinely-broken designed pages (the ~89%-of-quarantine target) where the fix
  actually works, page by page, proven by measurement not by guessing.
- Cannot damage healthy pages (rejected on loss) or be fooled by scramble (grounded vs
  original). The gate stays strict.
- Cost-bounded: only runs on pages that already have quarantined metrics (a minority).
