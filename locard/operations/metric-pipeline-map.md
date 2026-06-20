# Metric pipeline map — the model-text run (2026-06-20)

Plain-language reference for what we actually ran this session, what every piece does,
and where it lives. Two layers: the **scripts** we run by hand (in the review folder),
and the **library code** they call (in the app). Models used: **DeepSeek** (reads reports,
writes metrics), **Gemini** (vision/judge checks), **spaCy** (grammar checks).

**Everything below is in one of two folders:**
- **Scripts / data / viewers:** `locard/spikes/0064/eval_set/vision/`
- **Library code:** `lavandula/nlp/` (plus `lavandula/common/` for DB + secrets)

---

## 1. The run at a glance

Four steps, run in order, each a separate script against one data file:

```
report (in the database, already parsed)
   │
   ▼  STEP 1  regen_1_extract.py ............ DeepSeek reads the report, writes the metrics
prompt_test.json   (the model's raw metrics)
   │
   ▼  STEP 2  regen_2_model_text.py ......... build the review set + page images (NO gate, NO rewriting)
model-review-data.json   (statement = the model's own words)
   │
   ▼  STEP 3  gate_on_model_text.py ......... apply the gate's keep/drop DECISIONS (no rewriting)
   ▼  STEP 4  incomplete_gate.py ............ flag bare "no-action" metrics
model-review-data.json   (now carries gate_decision + incomplete flags)
   │
   ▼  view    model-review.html ............. the review screen
```

Live review screen: **https://cloud2.lavandulagroup.com/p20/model-review.html**

---

## 2. The steps — what each does, and the code it calls

### STEP 1 — Extract the metrics
- **Script:** `regen_1_extract.py`
- **What it does:** for each of the 25 test reports, hands the report's text to DeepSeek and gets back the metrics the model found — each with the model's own sentence, the number, the unit, a tier, a verbatim source quote, and two location markers (where the value and the label sit on the page).
- **Library code it calls:**
  - `lavandula/nlp/llm_extract.py` → `_METRICS_PROMPT` (the instructions that define a metric) and `call_deepseek()` (sends text to DeepSeek, returns the metrics).
  - `lavandula/nlp/marker_render.py` → `render_tagged()` (turns the parsed report into "tagged text" — every line and table cell labelled with a ⟨marker⟩ so the model can point at exact locations).
- **Out:** `prompt_test.json` — the model's raw metrics, one entry per report.

### STEP 2 — Build the review set (model's own text)
- **Script:** `regen_2_model_text.py`
- **What it does:** turns the raw metrics into review cards. For each metric it looks up where the value and label markers are on the page, renders the source page with a red box on the value and a blue box on the label, and **keeps the model's own sentence as the displayed metric**. No gate, no dedup, no rewriting — every metric the model produced, shown as-is.
- **Library code it calls:** `lavandula/nlp/marker_render.py` → `render_tagged().idmap` (marker → page + box + text). Plus `pdftoppm` + Pillow to draw the boxed page images.
- **Out:** `model-review-data.json` (the review set) + boxed page images in `new_review_img/`.

### STEP 3 — Apply the gate's decisions
- **Script:** `gate_on_model_text.py`
- **What it does:** runs the real gate's keep/drop checks over the model's text, but **only the decisions** — it never rewrites the sentence (that rewriting is what produced garbage in the old run). Stamps each metric `publish` or `quarantine` with a reason.
- **Library code it calls:** `lavandula/nlp/slot_render.py` → `dedup()`, `metric_quality()`, `subject_quality()`.
- **Note:** two of `subject_quality`'s rules ("too long / reads as a sentence") are turned OFF here — they only made sense for the old short-label construction and wrongly killed good descriptions.
- **Out:** adds `gate_decision` + `gate_reason` to `model-review-data.json`. Result: 282 publish / 14 quarantine.

### STEP 4 — Flag incomplete metrics
- **Script:** `incomplete_gate.py`
- **What it does:** flags metrics that are a bare count with no action — "838 individuals" (individuals doing what?). Uses grammar analysis to spot a generic people-word being counted with no verb and no context. Only flags; never rewrites.
- **Library code it calls:** **spaCy** (`en_core_web_sm` language model).
- **Out:** adds `incomplete` + `incomplete_reason` to `model-review-data.json`. Result: 6 flagged.

---

## 3. Component reference — what each piece is, and where

### The model's brain — extraction
| piece | where | what it does | used in recent run? |
|---|---|---|---|
| `_METRICS_PROMPT` | `lavandula/nlp/llm_extract.py` | the written instructions that tell DeepSeek what counts as a metric (tiers + a do-not-extract list) | **yes** |
| `call_deepseek()` | `lavandula/nlp/llm_extract.py` | sends a report's text + the prompt to DeepSeek, returns the metrics as data | **yes** |
| `render_tagged()` | `lavandula/nlp/marker_render.py` | turns a parsed report into tagged text + a marker→page/box/text map | **yes** (steps 1 & 2) |

### The gate — keep/drop checks (in `lavandula/nlp/slot_render.py`)
| piece | what it does | used in recent run? |
|---|---|---|
| `dedup()` | drops near-duplicate metrics (same number + overlapping words) | **yes** |
| `metric_quality()` | rejects tenure/anniversary numbers and financial-statement fragments | **yes** |
| `subject_quality()` | rejects gratitude/thank-you, empty, and contentless-header labels (its length rules are disabled in the recent run) | **yes** |
| `render_and_grade()` | the OLD combined "rebuild the sentence + grade it" — **the rewriting that produced garbage.** Used only in the old constructed run, NOT the model-text run | no |

### The detectors built this session (in the review folder)
| piece | where | what it does | used in recent run? |
|---|---|---|---|
| incompleteness check | `incomplete_gate.py` | flags bare "no-action" counts (spaCy grammar) | **yes** |
| find-pass | `find_pass.py` | flags junk published metrics with rules + a Gemini judge | no — only ran on the old set |

### Built but NOT pointed at the recent run
| piece | where | what it does | status |
|---|---|---|---|
| geometry mispairing | `lavandula/nlp/mispairing_check.py` → `geometry_pairing_check()` | flags value/label pairs that sit physically far apart on the page (the m5 problem) | exists, **never applied to model text** |
| vision pairing | `regen_3_vision_pairing.py` | shows the page to Gemini, asks if the value↔label pairing is right | only ran on the old constructed set |
| **production gate harness** | `lavandula/nlp/gate_runner.py`, `gate_policy.py`, `measure_check.py` (Spec 0069) | a real, sequenced check-stack (dedup, mispairing, financial line-item, is-a-metric, measurable/bare-1) that runs on the database — **but it's wired to the OLD marker/construction pipeline, not this** | exists, not used here |

### Shared infrastructure
| piece | where | what it does |
|---|---|---|
| `make_app_engine()` | `lavandula/common/db.py` | opens the connection to the production database (RDS) |
| `get_secret()` | `lavandula/common/secrets.py` | fetches API keys (DeepSeek, Gemini) from AWS SSM |

---

## 4. Data files (all in `locard/spikes/0064/eval_set/vision/`)

| file | what it holds |
|---|---|
| `prompt_test.json` | the model's raw metrics, per report (output of step 1) |
| `model-review-data.json` | **the recent run** — model text + gate decisions + incomplete flags + page-image paths (what `model-review.html` reads) |
| `new-review-data.json` | the OLD constructed run (garbled sentences + vision-pairing quarantines) — kept for comparison |
| `find_pass_results.json`, `vision_pairing_results.json` | logs from the old-set detectors |
| `*.before-*.json` | timestamped backups written before each gate stamped the data |

## 5. Review screens

| file | what it shows | URL |
|---|---|---|
| `model-review.html` | **the recent run** — model's own text, tier/gate/incomplete badges, keep·junk·unsure·debate marks | `/p20/model-review.html` |
| `new-metric-review.html` | the old constructed set (with the vision + find-flag badges) | `/p20/new-metric-review.html` |

## 6. Models / services used

| model | where | role |
|---|---|---|
| DeepSeek `deepseek-chat` | `api.deepseek.com` (key in SSM) | reads reports, writes the metrics (step 1) |
| Gemini `gemini-2.5-flash` | Google API (key in SSM) | vision pairing + find-pass judge (old set only) |
| spaCy `en_core_web_sm` | local | grammar analysis for the incompleteness check (step 4) |

---

## 7. The honest state (why this doc exists)

The recent run is **4 hand-run scripts + 3 gate functions + 1 spaCy check** against one JSON file.
A fuller gate harness already exists in `lavandula/nlp/` (Spec 0069) but is pointed at the old
database/construction pipeline, not this. The open organizing question: re-aim that harness at the
model-text approach (shedding its construction-era pieces) and fold in the new checks
(incompleteness, vague-quantity, mispairing-as-flag) so everything runs in one place instead of
scattered scripts.

---

## 8. ✅ RESOLVED — the two parallel gates were consolidated (2026-06-20)

> **This whole section is now history.** The fork below was fixed: a single gate core lives in
> `lavandula/metrics/core/` (the metric engine, this doc's subject). The v1 gate cluster
> (`slot_render` + the Spec 0069 `gate*.py` + `mispairing_check`) was **archived** — tag
> `pre-v1-gate-archive-2026-06-20`, recoverable forever. The duplication scanner
> (`locard/housekeeping/duplication_scan.py`) no longer reports it. Kept below for the record.

The gate logic **was** **not one codebase.** There were **two separate, drifted implementations** in
`lavandula/nlp/` that shared **zero code** (verified: neither imported the other), doing the same jobs
in different functions:

| job | research gate — `slot_render.py` (Jun 18, what we ran) | production harness — `gate_policy.py` / `gate_runner.py` (Jun 17, Spec 0069) |
|---|---|---|
| dedup | `dedup()` (same value + overlapping words) | `dedup_indices()` (markers + reading order) |
| quality reject | `metric_quality()` + `subject_quality()` (gratitude/tenure/financial-fragment regexes) | `decide()` + `_qual_cat()` + reject rules |
| financial | `_FIN_FRAG` regex | `financial_lineitem()` / `_FIN_LINEITEM` |
| measurable / bare-1 | *(none)* | `measure_check.py` (`measure_unchecked`) |
| operates on | a JSON file of metrics | the database `llm_metrics` rows |

`slot_render.py` (Jun 18) is the **newer** one — a fresh research re-implementation written the day
*after* the production harness, alongside it rather than from it.

**Mispairing is handled by THREE different *approaches* (not three copies of one thing):**
1. `lavandula/nlp/gate_policy.py` → `column_mispair()` (table column-header logic) — **the only one that is a live gate**
2. `lavandula/nlp/mispairing_check.py` → `geometry_pairing_check()` (bbox-geometry **diagnostic**, never wired in, applies nothing)
3. `locard/spikes/0064/eval_set/vision/regen_3_vision_pairing.py` (Gemini vision **spike**, old set only)

(Earlier I called this "duplication in three places" — that was overstated: they're three distinct
methods, only #1 is a gate. The genuine duplication is the gate pair above.)

**Why this matters:** every new check has to be written twice (or thrice), and the two gates keep
diverging. **Consolidate to one gate before adding more checks** — otherwise the organizing problem
gets worse with every rule we add.

**Not caught by housekeeping:** the housekeeping sweep (`import_graph.py`) uses a dead-code /
reachability lens — it flags files nothing imports. Both gates are reachable, so both passed; the
method is blind to *live duplication*. See the blind-spot note added to
`locard/housekeeping/SYSTEM-MAP.md` §6.
