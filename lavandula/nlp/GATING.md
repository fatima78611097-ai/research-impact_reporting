# Gating on the metrics in the viewer — exact code, per step

**Scope:** `new-metric-review.html` → `new-review-data.json`.
**Verified state:** 297 metrics — **258 publish / 39 quarantine** (25 NTEE-P docs). *(271/26 after the text gates; the vision pairing pass then quarantined 13 more — see "Mispairing" below.)*
**All gating code lives in `lavandula/nlp/slot_render.py`** (sibling of the prompt, `llm_extract.py`). Functions are cited by name, not line number (line numbers drift); verified against `slot_render.py @ 0eaa66ab`.
**Inputs:** selective prompt output (`llm_extract.py @ b586e941`) + resolved markers (`s_text`, value/subject pages & bboxes from `build_new_review.py`).

The decision on every record is set by two calls: `dedup()` over each document's metrics, then `render_and_grade(m, s_text)` per metric. Order of gates is exactly the order inside `render_and_grade`.

---

## Step 1 — `dedup(metrics)` · `slot_render.py`
Runs first, per document. **Removed 14** before the viewer.
```python
def dedup(metrics):
    def toks(s):
        return set(w for w in re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split() if len(w) > 2)
    kept, seen = [], []
    for m in metrics:
        v = str(m.get("metric_value")); w = toks(m.get("metric_text"))
        if any(v == sv and len(w & sw) >= 2 for sv, sw in seen):
            continue
        seen.append((v, w)); kept.append(m)
    return kept
```
**Rule:** same `metric_value` AND ≥2 overlapping words in `metric_text` → drop, keep first.

---

## Step 2 — `metric_quality(metric_text)` · `slot_render.py` (called first inside `render_and_grade`)
First gate inside `render_and_grade`; quarantines regardless of inline/split.
```python
_TENURE   = re.compile(r"\b\d+\s*(st|nd|rd|th)\s+(year|anniversary)\b", re.I)
_FIN_FRAG = re.compile(r"\bgrowth on\b|\bawarded\s+\d+\s+grants?\b|%\s*of\s+(revenue|budget|overall|the\s+award)", re.I)

def metric_quality(metric_text):
    t = metric_text or ""
    if _TENURE.search(t):   return ("junk", "tenure / anniversary, not a metric")
    if _FIN_FRAG.search(t): return ("junk", "financial-statement fragment")
    return ("ok", "")
```
**Quarantined here:** tenure/anniversary **1**, financial-statement fragment **2**.

---

## Step 3 — inline pass-through · in `render_and_grade`
```python
inline = m.get("value_ref") == m.get("subject_ref")
...
if inline:
    return ((snip or (s_text or "").strip()), "publish", "")
```
**Rule:** value & label share a marker → no pairing risk → publish the verbatim phrase. **No quality filter applied.**

---

## Step 4 — cross-cell render fix · in `render_and_grade`
```python
digits = str(m.get("metric_value")).split(".")[0].replace(",", "").lstrip("-")
if len(snip.split()) > 6 and digits and digits in snip.replace(",", ""):
    return (snip, "publish", "")
```
**Rule:** split metric whose number sits in a prose cell → render from THAT cell (not a grafted foreign label). **Repair, not a quarantine.** ⚠️ also renders garbled cells verbatim.

---

## Step 5 — `subject_quality(label)` · `slot_render.py` (called last inside `render_and_grade`)
Applies **only to split callouts** that reached this point.
```python
_GRATITUDE  = re.compile(r"\b(thank you|thanks|grateful|gratitude|generous|sincere(?:ly)?|proud(?:ly)? to|honou?red|pleased to|delighted|we appreciate|appreciation|salute|shout[- ]?out|kudos)\b", re.I)
_BARE_META  = re.compile(r"^(total|number|amount|count|sum|balance|net|gross|subtotal|figures?)\s*:?\s*$", re.I)
_FY_HEADER  = re.compile(r"\b(number|figures?|count|amount)\b.*\b(beginning|end)\s+of\b", re.I)
_PREAUDIT   = re.compile(r"\bpre-?audit\b", re.I)
_ONLY_PUNCT = re.compile(r"^[\W\d]+$")

def subject_quality(label):
    s = (label or "").strip()
    if not s or _ONLY_PUNCT.match(s):                       return ("junk", "empty / no words")
    if _GRATITUDE.search(s):                               return ("junk", "gratitude / marketing blurb, not a label")
    if _BARE_META.match(s) or _FY_HEADER.search(s) or _PREAUDIT.search(s):  return ("junk", "contentless statement header")
    n = len(s.split())
    if n > 14:                                            return ("junk", "too long — a sentence, not a label")
    if s.rstrip()[-1] in ".!?" and n > 8:                 return ("junk", "reads as a sentence")
    if n > 10:                                            return ("weak", "long label — check")
    return ("ok", "")
```
**Quarantined here:** contentless header **8**, too long **13**, reads as a sentence **1**, gratitude **1**.

---

## Support code (not gates)
- `fmt_value(v, unit)` · `slot_render.py` — formats `$ / % / commas` for the display only.
- `_clean(s)` · `slot_render.py` — strips stray `⟨…⟩` markers from a snippet.

## Quarantine tally (matches the data: 39)
**Text gates (26):** `13` too long · `8` contentless header · `2` financial fragment · `1` tenure · `1` reads-as-sentence · `1` gratitude.
**Vision pairing (13):** majority-vote mispairs, `decided_by="vision"` (see Mispairing section).

---

## Mispairing — status (vision pass APPLIED to this data)

- **Vision pairing pass: COMPLETED & APPLIED 2026-06-19.** `regen_3_vision_pairing.py` (STEP 3, in `locard/spikes/0064/eval_set/vision/`) judged all **81 published split metrics** (value & label from different markers) with **gemini-2.5-flash, 2-of-3 majority vote**, looking at the boxed source page (red value box / blue subject box). On a majority *disagree* the metric is **quarantined** (`gate_decision="quarantine"`, `decided_by="vision"`, with the model's suggested correct label). Result: **13 quarantined**, 0 undetermined → totals went `271/26` → `258/39`. Every one of the 81 carries a `vision_pairing` verdict (`match`, `yes`/`parsed` vote count, `suggested_label`); the viewer (`new-metric-review.html`) renders it as a `👁 ✓/✗` badge. The 190 inline metrics (same marker = same cell) have no pairing to get wrong and are **not** judged. Backup of the pre-vision data: `new-review-data.before-vision-*.json`; per-metric log: `vision_pairing_results.json`.
  - Concentrated in the known garbled-parse docs (`029625c6`, `128de607`, `38c9a81d`) where Step 4 was rendering grafted-neighbor labels verbatim — vision is the gate that catches those.
- **Programmatic geometry check (superseded as the pairing gate, kept as a diagnostic).** `lavandula/nlp/mispairing_check.py` → `geometry_pairing_check(records)` classified the 271 (190 inline / 79 split co-located / 2 far-apart suspects) but **applied nothing** (no fix, no flags persisted) — it was detection-only. Vision now does the actual quarantining; geometry remains a cheap standalone cross-check. Thresholds: `dy < 60` = same row; `(dx < 160 and dy < 160)` = same card. **Not** part of `slot_render.py`.
- **The only mispairing-adjacent REPAIR (vs. quarantine)** is the cross-cell render fix (Step 4, in `render_and_grade`) — narrow: re-renders a number from its own prose cell when the label was grafted from a different cell. Vision *quarantines* a bad pairing; it does not re-pair. Re-pairing (keeping the metric with the corrected label) is still future work.

## Other NOT-gated gaps
- **Bare-1s / weak buried-prose** — no rule; currently publish.
- **Garbled-parse docs** (`029625c6`, `38c9a81d`, `e2416d20`, `128de607`) — Step 4 renders their garble verbatim; not gated out.

---

## Reproducing `new-review-data.json`

Two committed scripts in `locard/spikes/0064/eval_set/vision/`:
```
python3 regen_1_extract.py         # STEP 1: _METRICS_PROMPT over 25 NTEE-P docs -> prompt_test.json
python3 regen_2_build_review.py    # STEP 2: dedup -> resolve markers -> boxed images -> gate -> new-review-data.json
python3 regen_3_vision_pairing.py  # STEP 3: vision pairing pass (gemini-2.5-flash, 2-of-3) -> quarantine mispairs in place
```
- **`regen_1_extract.py`** — extraction only.
- **`regen_2_build_review.py`** — builds records + boxed page images **and folds the gate in** (`dedup()` then `render_and_grade()` per metric), so its output is the final gated data — no separate apply step.

Both import the gate as `from lavandula.nlp.slot_render import render_and_grade, dedup`.
The ~445 MB of images under `new_review_img/` are produced by STEP 2 and are **NOT committed** (regenerable).
