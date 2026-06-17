# Metric & Story Product — Execution Plan (v2, 2026-06-16)

> v2 incorporates a 6-lens adversarial review (all lenses: "needs-work", with specific
> verified fixes). Biggest changes from v1: (1) a **Week-1 proof + measurement** phase
> that needs no build; (2) "wire the gates" is actually a **build** (a second AI grounding
> pass) — split accordingly; (3) automated gates top out ~90% precision, so **publish =
> gates + a human spot-check**, and the main error (wrong label) is **quarantined, not
> auto-fixed**; (4) schema cleanup **moves to the end**, behind a compatibility view.

## Goal
Publish real, accurate impact **metrics and stories** tied to each report in the corpus.
*Fewer, highly accurate* beats *more with errors*. Precision over recall.

## Principles
1. **Precision over recall** — publish only what we prove; quarantine the rest.
2. **Short slots, deterministic render** — no composed sentences.
3. **Ground every number to a page location.**
4. **Solve infographics** — the org's headline metrics live there.
5. **Qualify the input** — only real narrative reports; exclude text-only financial junk.
6. **Reuse, don't rebuild** — the research already built most gates; port them and
   reproduce their dev numbers behind an acceptance suite (canonical task list:
   `comp-metric-regression/production-implementation-plan.md`, READY/WIRE/BUILD/TUNE).

## Honest constraints the review surfaced (read before sequencing)
- **Today's "verified" does not carry forward.** The 56,443 "verified" metrics passed a
  *text-substring* check only — ~9% don't even contain the number, and **none carry a page
  marker**. After re-parse they must be re-grounded. Do not cite "82% verified" as our
  starting precision.
- **"Wire the gates" is a build.** The research grounding gate needs per-item markers
  produced by a **second AI pass** (re-render the doc with markers, a second model call
  cites them) plus a **third small pass** (is a small number actually a measurement). None
  of that exists in production. It's a build, not a hook-up — and it's the long pole.
- **The main error has no reliable auto-fix.** Right-number-wrong-label (mispairing) is the
  dominant residual; the only auto-repair tried mis-flags 88% of the time. So we
  **detect-and-quarantine** mispairs, not fix them (consistent with fewer-but-accurate).
- **Automated precision tops out ~90%.** To hit the bar, **publish = gates + a human
  spot-check** of the published sample. The research's own latest design already does this.
- **The qualifier already exists** as a one-line whitelist in production, and our v1 logic
  was backwards: financial reports have *more* prose, not less — gate on the *kind* of
  language (financial-statement / audit-title vocabulary), not the amount.

## Target pipeline
Qualify report → Parse *with coordinates* → Short-slot extract (value+label+unit+period+
**markers**) → second pass grounds each number to its marker → is-it-a-metric → detect &
quarantine mispairs → de-dup (prose-internal now; prose-vs-infographic after vision) →
**human spot-check** → publish verified-only → per-report product view. Infographic/vision
recovery runs where text grounding can't reach.

## Phases (re-sequenced)

### Phase −1 — Prove it + measure it (Week 1, NO build, runs now)
The fastest "back on the rails" win. Three cheap, parallel, build-free actions:
- **(a) 10-doc grounding demo.** 49 documents *already* have both extracted metrics and
  real coordinates. Take ~10, run the existing research chain (tagged render → marker-citing
  extraction → `gate.verdict` → slot render), hand-check the published set against the page
  images, drop it into the existing per-org viewer. Proves the whole approach end-to-end
  with no re-parse, no schema work, no new build.
- **(b) Calibration.** Hand-grade a sample of today's "verified" metrics for *right-number
  AND right-label* (the real accuracy, vs the text-only 82%), and measure the
  prose-vs-infographic duplication rate. Sets the go/no-go baseline.
- **(c) Recall sizing.** Run the never-run recall check (upgraded to match on *label*, not
  just the number) on ~40 designed-page docs to size how many headline numbers we miss
  entirely. **This gates the whole sequence** — if the image-only gap is large, vision moves
  up from Phase 4 to run alongside Phase 2.

Exit: a viewable accurate sample + three numbers (real precision, duplication rate,
image-only gap) that decide the rest.

### Phase 0 — Contracts & qualification (foundation; parallelizable; NO schema retire)
- **Marker contract (do first).** Pin ONE end-to-end marker scheme: the extractor cites
  Docling location IDs; we store the resolved page/box/row/col on the metric. Decide the
  fate of the existing character-offset grounding. Nothing downstream is safe until this
  is frozen.
- **Published-data contract.** Define "published" as a verified-only, slot-based view the
  product reads (today's per-org view wrongly shows *all* tiers and the old text field —
  fix that).
- **Qualification gate (harden the existing whitelist, two stages).** (1) cheap pre-parse
  reject on first-page audit/compilation titles + the live `material_type` label, to choose
  what to parse; (2) post-parse confirm on financial-statement-vs-impact *vocabulary*. Bias
  toward **inclusion** (don't drop real impact reports); hand-label ~200–300 docs as the
  gold set to set thresholds.

### Phase 1 — Coordinates (linchpin)
- **1(a) Re-parse the QUALIFIED set only** (never the full corpus). Acceptance: BOTH
  section *and* table-cell coordinates populated (box+row+col), verified by count; target
  designed pages explicitly (Docling is weakest there — if they still lack coords, route
  them to vision).
- **1(b) Extractor cites a marker per metric** — a spec'd change: prompt revision + new
  marker columns + backfill, re-validated against the frozen CanCare/BGCSM fixtures. Co-
  linchpin with 1(a), not a sub-bullet.

### Phase 2 — Precision live (first accurate metrics on the pilot)
- **2a BUILD the grounding subsystem:** tagged render + the second AI grounding pass + the
  small-number measurable-value pass, emitting the contracted markers/idmap.
- **2b WIRE the validated gate:** call `gate.verdict`; port `gate.py` / is-a-metric rules as-is
  and reproduce their dev numbers behind an acceptance suite.
- **Mispairs → detect-and-quarantine** (no auto-fix).
- **Prose-internal de-dup only.** (Prose-vs-infographic de-dup is Phase 4.)
- **Quarantine triage** using the research recovery slate: recoverable-by-relabel /
  recoverable-by-vision / true-junk; report recovered count.
- **Human spot-review** of the auto-published sample; a measured accuracy threshold must
  hold before Phase 5.

### Phase 3 — Stories
Concrete bar: every named person and every claim in the summary must appear in the grounded
snippet (no invented people/claims); re-grade the 7,644 already-"verified" stories under the
new bar; add a PII review gate.

### Phase 4 — Infographics / vision (size-gated by Phase −1c)
- **Gate-routed**, not a standalone "is this an infographic?" detector (that detector was
  28% precise). Route off quarantined + figure-dense pages.
- **Expand the vision test set** from 1 verified image to ~15–20 + canary negatives BEFORE
  publishing any image-only number; hold image-only numbers to the same number-on-page +
  human-review bar.
- **Importance-tagging** = a scorable "featured?" classifier with its own labeled set — not
  hand-wave.
- **Prose-vs-infographic de-dup** lives here (both halves now exist).

### Phase 5 — QA loop, then scale, then cleanup
- **QA loop:** a frozen gold set + a stated accuracy target (right-number AND right-label)
  + a human-sample protocol + a per-new-vertical audit gate. **Scale is gated on a measured
  number, not asserted precision.**
- **Cost/scale sizing** for the qualified-set re-parse and the vision pass (GPU-hours,
  wall-clock, cost) — surfaced for go/no-go.
- **Scale** extraction across the qualifying corpus.
- **Schema cleanup LAST:** retire `lava_vocab` behind a compatibility view with a rollback
  snapshot (74,240 live metrics + 11,529 stories; ~20+ hard-coded references); tear down the
  dead Spec 0049/0050 assets. A no-product-signal refactor — done once the approach is proven.

## Cross-cutting deliverables (named, easy to drop otherwise)
- **Precision SLA as a number** (per-metric AND per-org), with how it's measured.
- **Period/timeframe slot** end-to-end: prompt field + column + grounding policy.
- **Reuse, not rebuild:** adopt `production-implementation-plan.md` as the task list; port
  the slot-render spike, `gate.py`, `regroup.py`, `test_suite.py` and reproduce their numbers.

## Deferred (named, not now)
Benchmarking / sector analysis; AI interviewer; newsletters & magazines as sources.

---

## Phase −1 results (2026-06-16) — DONE, with two plan deltas

**(a) Approach works.** 10-doc demo (Experiment 0002): the render→extract→ground→is-a-metric→
gate chain ran end-to-end on coord-bearing docs with no new build; hand-check of ~27 published
metrics on 3 reports = 0 errors (right number + right label); gate correctly quarantined
rankings + ungrounded values.

**(b) Today's "verified" is ~85–90% actually correct.** Hand-graded 49 verified metrics
(stratified): 82% clearly correct, 95% of the text-confirmable, **0 confirmed mispairs**; the
2 real errors were off-by-one PARSE glitches. The 14% "unsure" tail were all **flattened
dotted-leader tables** where number↔label pairing scrambles in the text — figure-heavy
"designed" docs actually graded *better*. **Delta: the hard case is table-flattening, not
hero-stat infographics — point coordinates/vision at TABLES, not just callouts.** Also: ~48% of
sampled values repeat within their doc (de-dup matters).

**(c) Image-only gap is large.** Vision on 40 figure-dense docs: of 1,916 featured callout
numbers, ~27% captured correctly, ~27% parsed-but-not-extracted, ~5% captured-mislabeled,
**~42% NOT_PARSED (image-only)**. The 42% is an inflated ceiling (vision lists generously;
not worth precise verification now — measure properly at vision-build time against a curated
ground truth). **Delta: the "they're mostly echoed in prose" hope holds for only ~1/4 — vision
is essential and moves UP, running alongside the Phase 2 precision work, not last.**

**Net:** approach validated; precision baseline is decent (~85–90%) and the remaining error is
concentrated in flattened tables + image-only numbers — both addressed by coordinates + vision.
Proceed to the first production spec (extractor cites markers / coordinate handoff).
