# 0064 — KPI detector: scored against human labels (97 pages)

**Date:** 2026-06-01. Ground truth: `eval_set/eval_labels.csv` (operator-labeled all 97 pages of the 8-doc stratified sample). Detector = v2 (`kpi_detector.py`). The actionable decision is the APPLY lane (predict regroup=yes).

## Result
| Metric | Value |
|---|---|
| **APPLY-lane precision** | **5/18 = 28%** |
| Recall | 5/8 = 62% (83% = 5/6 excluding 2 invisible-garble pages) |
| Confusion | TP 5 · FP 13 · FN 3 · TN 76 |

**13 false positives by true class:** financial_table 6 (`001eb5f8`p20 + `798514f7`p6/8/9/10/11), mixed 5 (`0749b549`p2/3/5/6/7 county-dashboard template), prose 2 (`001eb5f8`p7/14).
**5 true positives:** `001eb5f8`p13, `2fccbb57`p9/10/11, `f3dc12b3`p3 (4 of these found without prior manual verification — real signal).
**3 misses:** `001eb5f8`p2 (in LOG), `7ce2c7fd`p5/p15 (garbled — unregroupable anyway; the garble is invisible in the render so they were labeled kpi_clean by eye).

## Ground-truth class distribution (97 pages)
prose 32 · cover_divider 23 · mixed 17 · financial_table 15 · **kpi_clean 8** · kpi_dense 1 · garble 1.

## Two structural findings
1. **kpi_clean is a RARE positive (~8% of pages).** On an 8%-prevalence target a heuristic that over-fires is dominated by false positives almost by construction; precision must be very high to be useful — beyond where 2 rounds of heuristics reached.
2. **The detector problem and the corpus-junk problem compound.** ~5 of 13 FPs are from one spreadsheet doc (`798514f7`); `0613513f` is 6 financial pages too. 2 of 8 sampled docs are data dumps. Filtering junk docs upstream removes a chunk of FPs for free.

## Recommendation
- Recall is good (83%) → keep the heuristic as a cheap **high-recall pre-filter** (narrows to ~18 candidates).
- Add a **precise second stage** to prune financial/mixed/prose FPs: a small learned classifier trained on these 97 labels (existing signals = features) or a targeted VLM check on candidates only.
- Run the **corpus-junk filter** (separate workstream) — removes spreadsheet/form docs before extraction, helping everything.
- Stop hand-tuning heuristics (diminishing returns at 28% precision). The 97 labels are the asset; expand and learn.
