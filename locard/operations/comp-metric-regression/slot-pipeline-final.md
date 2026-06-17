# Slot Pipeline — finished state and decisions (2026-06-14)

The slot pipeline: the LLM extracts slots (value + label + source markers); code renders the
display sentence; deterministic gates decide publish/quarantine. Composed-sentence path is frozen
(`frozen/composed-baseline-2026-06-14/`, git tag `baseline-composed-sentence-2026-06-14`).

**Current state: 1,166 publish / 440 quarantine** (of 1,606). Viewer: `/p20/slot-review.html`
(graded in a separate `slot_reviews` table — old marks untouched).

## Calls I made (not open questions)

| Decision | Call | Why |
|---|---|---|
| Sentence | Render deterministically from value+label, not LLM-composed | Kills paraphrase drift / amplification / dangling-reference by construction; matches the published bar (Giving Compass) |
| Grounding gate | **Kept** (inherited) | Value must be at the source; the only thing protecting against made-up numbers |
| Low-value doc gate | **Kept** (inherited) | Judges the document, independent of sentence style |
| Mispairing gate | **Kept** (inherited), flag-mode quarantine | Detects value↔label mispairing; the 15 grid cases with a geometry-proposed label are HELD pending validation, NOT auto-published |
| Is-a-metric | **Aggressive** new LLM judge (fed value+label+full page) | Operator preference: over-correct, never miss. Catches regional/market factoids the old regex missed (e.g. "11.5% rent increase"). 65 quarantined |
| Simple confirm | **New**: value AND label words must be in the source page | The "dead simple" check; validated at 97.1% |
| Drift gates (faithfulness/amplification/dangling) | **Dropped** | Existed only to police composed prose; no prose to police |
| Unit rendering | %, $, acronym-preservation handled deterministically | Bugs found and fixed in the spike |

## Validation (running)

Dual **Opus** sweep — one agent reads the page IMAGE (vision), one reads the page TEXT — each judges
independently whether the value is a real metric of the org's own work, correctly paired. Run on 238
metrics (all 118 contested gate calls + a 120 random sample of clean-published).
- **Both sweeps agree + agree with gate** → confirmed.
- **Sweeps disagree, or disagree with the gate** → surfaced for operator review (the real error bar).

## Open — genuinely the operator's call (few)

1. **Context-stat policy**: reject ALL external/market/population stats, or keep mission-framing ones?
   (The aggressive is-a-metric currently rejects them.)
2. **The 15 grid mispairings**: publish with the geometry-corrected label once validation confirms the
   geometry caption is right; otherwise leave quarantined. (Validation decides this.)

## Recoverability
- Composed baseline frozen (branch + tag + on-disk snapshot).
- Old pipeline (`metric-review.html`, `review-data.json`) restored and untouched.
- `review-data.before-slotfield.json` snapshot retained.
