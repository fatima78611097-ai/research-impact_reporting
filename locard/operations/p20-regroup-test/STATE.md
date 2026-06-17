# Metric accuracy — where this actually stands (2026-06-16)

**Goal:** accurately capture the *featured* impact metrics from nonprofit reports (the big infographic
callouts like "1,000 children served"), with correct labels. Accuracy is the precondition for the product —
a report tool that emits wrong numbers is worse than none.

## What we know (hand-verified)
- BE SKEPTICLE UNTIL PROVEN!! **Accuracy of what we publish: ~99%** on the subset we can place on a page (hand-checked, two prompts —
  May-26 run-10 and current May-30). Real error rate ~1%.
- **The ~1% errors are mispairs:** the right number attached to the wrong label (e.g. "316 = accessed
  training" when 316 = found jobs; "213 = ESL" when 213 = Empowered Youth).

## Root cause of the mispairs (verified in `sections` table, not just element JSON)
The parser stores a callout's **number and its label as separate pieces** (e.g. one row `"316"`, another row
`"people accessed training"`). The spatial link between them is lost in the flattened text, so the extractor
(DeepSeek) has to **guess** which loose number goes with which label — and sometimes guesses wrong.

## The fix (deterministic — NOT model turns)
Every piece is stored **with its box on the page**. So reconnect each number to its nearest label **by
position**. Tooling exists (`regroup`). 
- Going forward: add the pairing step to the parse so the connected unit is what gets stored.
- Back catalog: likely fixable **without re-parsing** — re-run pairing over stored coordinates (confirm
  `sections.source_locations` is fully populated first).

## Location link — DONE and working
Grounded re-extraction (`reextract_grounded.py` → `metrics-grounded.json`) places **97% of extracted
metrics on an exact box** (heavy 96.6 / moderate 97.1 / low 99.4). Remaining ~3% = snippet-match misses.
Verifying a metric is now "go to its box and check," no searching.

## The cleanest-signature risky class
Label-only snippets (number paired in from a separate piece): **0.5% (63/11,586)**. This UNDERCOUNTS the
true mispair rate (~1%) because numbered-but-wrong pairs (like 213) still contain a number.

## BIGGEST UNKNOWN — not measured
**Recall: how many featured numbers we never capture at all.** A metric can only exist if the parser read its
number; numbers truly painted into raster images are missed entirely and aren't in our data. This is the gap
most tied to the goal, and we have **no number for it**. `recall_check.py` is written but NOT run — it has
vision list featured callouts on infographic pages and compares to what we captured (buckets: captured /
parser-read-but-not-extracted / never-parsed).

## Dead-ends / cautions
- "Vision scans the doc to verify" **over-confirms** when shown the label (it parrots it). A blind version
  (don't show the label; compare its independent caption) is better but could not be eyeball-sealed this
  session (image-read limit). Prefer the deterministic coordinate path over model turns.

## Key files (`locard/operations/p20-regroup-test/`)
`reextract_grounded.py`, `metrics-grounded.json`, `classify_gate_json.py`, `vision_judge.py`,
`new-metrics-current.json`, `recall_check.py` (unrun), sample = `sample-manifest.json` (600 docs, stratified).

## Next step if resumed
Run `recall_check.py` to size the capture gap — that's the number that speaks to the goal. Then wire the
coordinate-pairing step into the parse.
