# Gating on the metrics in the viewer — exact code, per step

**Scope:** `new-metric-review.html` → `new-review-data.json`.
**Verified state:** 297 metrics — **271 publish / 26 quarantine** (25 NTEE-P docs).
**All gating code lives in `lavandula/nlp/slot_render.py`** (sibling of the prompt, `llm_extract.py`). Line numbers below are exact.
**Inputs:** selective prompt output (`llm_extract.py @ b586e941`) + resolved markers (`s_text`, value/subject pages & bboxes from `build_new_review.py`).

The decision on every record is set by two calls: `dedup()` over each document's metrics, then `render_and_grade(m, s_text)` per metric. Order of gates is exactly the order inside `render_and_grade`.

---

## Step 1 — `dedup(metrics)` · `slot_render.py:86`
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

## Step 2 — `metric_quality(metric_text)` · `slot_render.py:19` (called at `render_and_grade:67`)
First gate inside `render_and_grade`; quarantines regardless of inline/split.
```python
_TENURE   = re.compile(r"\b\d+\s*(st|nd|rd|th)\s+(year|anniversary)\b", re.I)            # line 16
_FIN_FRAG = re.compile(r"\bgrowth on\b|\bawarded\s+\d+\s+grants?\b|%\s*of\s+(revenue|budget|overall|the\s+award)", re.I)  # line 17

def metric_quality(metric_text):                       # line 19
    t = metric_text or ""
    if _TENURE.search(t):   return ("junk", "tenure / anniversary, not a metric")
    if _FIN_FRAG.search(t): return ("junk", "financial-statement fragment")
    return ("ok", "")
```
**Quarantined here:** tenure/anniversary **1**, financial-statement fragment **2**.

---

## Step 3 — inline pass-through · `render_and_grade:68,74`
```python
inline = m.get("value_ref") == m.get("subject_ref")
...
if inline:
    return ((snip or (s_text or "").strip()), "publish", "")
```
**Rule:** value & label share a marker → no pairing risk → publish the verbatim phrase. **No quality filter applied.**

---

## Step 4 — cross-cell render fix · `render_and_grade:76`
```python
digits = str(m.get("metric_value")).split(".")[0].replace(",", "").lstrip("-")
if len(snip.split()) > 6 and digits and digits in snip.replace(",", ""):
    return (snip, "publish", "")
```
**Rule:** split metric whose number sits in a prose cell → render from THAT cell (not a grafted foreign label). **Repair, not a quarantine.** ⚠️ also renders garbled cells verbatim.

---

## Step 5 — `subject_quality(label)` · `slot_render.py:46` (called at `render_and_grade:82`)
Applies **only to split callouts** that reached this point.
```python
_GRATITUDE  = re.compile(r"\b(thank you|thanks|grateful|gratitude|generous|sincere(?:ly)?|proud(?:ly)? to|honou?red|pleased to|delighted|we appreciate|appreciation|salute|shout[- ]?out|kudos)\b", re.I)  # line 8
_BARE_META  = re.compile(r"^(total|number|amount|count|sum|balance|net|gross|subtotal|figures?)\s*:?\s*$", re.I)   # line 12
_FY_HEADER  = re.compile(r"\b(number|figures?|count|amount)\b.*\b(beginning|end)\s+of\b", re.I)                    # line 13
_PREAUDIT   = re.compile(r"\bpre-?audit\b", re.I)                                                                 # line 14
_ONLY_PUNCT = re.compile(r"^[\W\d]+$")                                                                            # line 27

def subject_quality(label):                            # line 46
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
- `fmt_value(v, unit)` · `slot_render.py:33` — formats `$ / % / commas` for the display only.
- `_clean(s)` · `slot_render.py:30` — strips stray `⟨…⟩` markers from a snippet.

## Quarantine tally (matches the data: 26)
`13` too long · `8` contentless header · `2` financial fragment · `1` tenure · `1` reads-as-sentence · `1` gratitude.

---

## Mispairing — status (NOT applied to this data)

- **Programmatic geometry check: run ONCE as a diagnostic only — it did NOT apply a fix and did NOT persist anything.** It classified the 271 published metrics (190 inline = no pairing risk, 79 split co-located = coherent, 2 split far-apart = suspect, both `128de607`). No fix was applied, the flags were **not written into the data**, and the 2 suspects were **not quarantined** — they are still `publish`. Check-and-flag, nothing more.
  - **Code (exact, now captured):** `lavandula/nlp/mispairing_check.py` → `geometry_pairing_check(records)`. Thresholds: `dy < 60` pts = same row; `(dx < 160 and dy < 160)` = same card; midpoints from stored `v_bbox`/`s_bbox`. Verified to reproduce `190 / 79 / 2`. It is **not** part of `slot_render.py` / the publish gate.
- **Vision mispairing run: NOT COMPLETED.** A vision judge (Flash-Lite, label-vs-page) was proposed to confirm pairing on this set but was **never run**. There is no vision verdict on any metric in this data.
- **The only mispairing-adjacent REPAIR that is applied** is the cross-cell render fix (Step 4, `slot_render.py:76`) — narrow: re-renders a number from its own prose cell when the label was grafted from a different cell. It does **not** address within-grid cross-pairs.

## Other NOT-gated gaps
- **Bare-1s / weak buried-prose** — no rule; currently publish.
- **Garbled-parse docs** (`029625c6`, `38c9a81d`, `e2416d20`, `128de607`) — Step 4 renders their garble verbatim; not gated out.

---

## Reproducing `new-review-data.json` (honest state)

Three steps:
1. **Extract** — `_METRICS_PROMPT` over the 25 NTEE-P docs. *(script `prompt_headtohead.py` — currently in `/tmp`, **NOT committed**.)*
2. **Build records + boxed images** — resolve markers, render pages with value/subject boxes. *(script `build_new_review.py` — currently in `/tmp`, **NOT committed**.)*
3. **Apply the gate** — `dedup()` then `render_and_grade()` per metric, writing `statement` + `gate_decision`.

**Import note:** since `slot_render.py` now lives in `lavandula/nlp/`, step 3 imports it as
`from lavandula.nlp.slot_render import render_and_grade, dedup` — **not** a local `import slot_render`.

**Caveat:** steps 1–2 are ad-hoc `/tmp` scripts and are **not committed**, so this is **not** a one-command reproducible regeneration. Only the gate (step 3, `slot_render.py`) and the diagnostic (`mispairing_check.py`) are committed code.
