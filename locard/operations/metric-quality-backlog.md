# Metric-quality backlog (find → fix)

Working principle we agreed on: **find them consistently FIRST, fix/recover LATER.**
Quarantine ≠ solved — dropping a real number is a recall hole, not a fix. "Solved"
means the number ends up correctly read and **published** with its right label.

Source: review session 2026-06-19 on doc `128de607` (NTEE-P set, new-prompt viewer).

---

## A. FIND (detection) — flag every bad published metric

- [x] **Find-pass built** — `locard/spikes/0064/eval_set/vision/find_pass.py`. Flags
  marketing / jumbled / multiple-mashed / not-a-metric / duplicate over the published set.
  Measured: **3/3 recall** on hand-caught bad cases, **0/6 false positives** on known-good.
  Flags surfaced in the viewer (Queue → ⚑ find-flagged). 55/258 currently flagged.
- [ ] **Operator review of the 55 flagged** — skim in the viewer, confirm precision on the
  full set (ground truth so far is only 3 bad + 6 good).
- [ ] **Inline gratitude gate** — the gate's gratitude rule (`_GRATITUDE`) only runs on
  *split* callouts; the *inline* path (`render_and_grade` Step 3) runs **no quality filter**,
  which is why the "Thank you to the 450 members…" metric (`128de607:2/:3`) reached published.
  Wire `metric_quality` + gratitude onto the inline path. Small, precise fix.
- [ ] **Duplicate detection** — `dedup` catches same-*value* dupes but misses
  same-*sentence / different-number* dupes (450 and 18 from one sentence → two identical
  statements). Add a "same rendered statement" check.

### Product decisions needed (set the line; then tune the judge)
- [ ] **Two co-occurring numbers = one metric or "multiple/bad"?** e.g.
  *"Thirteen households received a total of $577,000 in assistance"*,
  *"386 volunteers donated 3,630 hours."* The judge currently flags these as "multiple."
- [ ] **Is a percent-change a valid metric?** e.g. *"34% increase from the previous year"* —
  judge called it not-a-metric.

---

## A2. Model-judge-tier checks (semantic — build only if the sample shows leaks)

- [ ] **org-vs-community statistic reject.** Reject a number describing the surrounding
  community/population (county/state poverty, unemployment, crime, median-income rates) vs
  the org's own result. The current prompt already instructs this; a model-judge check is the
  backstop if population stats leak through. **A drafted prompt for this exists** in the
  archived v1 `measure_check.py` (the "EXTERNAL/COMMUNITY STATISTIC" addition) — recover via
  `git checkout pre-v1-gate-archive-2026-06-20 -- lavandula/nlp/measure_check.py`.
- [ ] **financial line-item reject (stronger).** v1 drafted a fuller `_FIN_LINEITEM` regex
  (total revenue/income/expenses/assets/liabilities/net assets) beyond our current
  financial-fragment check. Recover from the same tag's `gate_policy.py` if our financial
  handling proves too narrow.

## B. FIX (recovery) — get the trapped numbers back. The hard part.

- [ ] **Infographic reader (the real fix).** Designed stat-callout regions are flattened by
  the parser into jumbled text (`300+ 204 685 One Week One Day *`); everything downstream
  works from noise. The page **image** is intact — vision can read the real number+label off
  it. Spike-proven on a small set, **never wired into the pipeline.** Next step: a *measured*
  experiment — run vision-reads-the-page on ~25 infographic-heavy reports and report the
  **recovery rate** (how many trapped numbers we read correctly), not a claim.
- [ ] **Pairing recoverer.** When a pairing is wrong, read the *correct* label off the page
  and re-pair — instead of only dropping. (Today's vision pairing pass only judges/drops.)
- [ ] **Precise infographic-page detection.** A high-recall / low-precision (28%) page
  detector exists (`locard/spikes/0064/kpi_detector.py`); a reliable corpus-scale detector
  is still open (geometry vs image-classifier vs VLM vs hybrid). Prevalence is known:
  ~89% of all quarantines are the "words present, number split from label" infographic
  signature; true font-garble is only ~0.2%.

---

## C. Notes / smaller items
- [ ] **Reconsider the vision *pairing* pass.** It drops, it doesn't recover — on garbled
  docs its votes are near-random (1/3). It may be the wrong layer once the reader lands;
  keep it only as a precision guardrail, not as progress on the real problem.
- [x] **DeepSeek key is FINE.** The 401 was a bug, not the key: `call_deepseek` ignores its
  `api_key` arg and relies on the caller setting `Authorization: Bearer` on the httpx client
  (`regen_1_extract.py` does this; the find-pass debug did not).
- [ ] **Commit** the find-pass tool + viewer flag badge/filter.
- [ ] **Wire metric extraction into the orchestrator** (longer-term, per SYSTEM-MAP §8).
