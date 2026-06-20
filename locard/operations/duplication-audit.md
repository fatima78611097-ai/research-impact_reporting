# Duplication audit — same purpose, different code (2026-06-20)

## Why this exists

The housekeeping sweep (`locard/housekeeping/import_graph.py`) finds **dead code** — files nothing
imports. It is **blind to live duplication** — two *reachable* files that do the same job in different
code. This pass compares same-purpose files by **reading and comparing** them, which the reachability
lens never does. Triggered by discovering two parallel metric gates (`slot_render.py` vs the Spec 0069
gate) that both passed the dead-code sweep as "clean."

## Method

Surfaced candidate clusters by domain keyword + filename family + shared function names, then read and
compared each cluster (parallel readers). Verdict per cluster: genuine duplication vs legitimate
separation. Scope: `lavandula/` (the library). `locard/` scripts are known scratch with expected
duplication and were not audited.

---

## Genuine duplications (same job, different code)

| # | The two implementations | Evidence | Fix | Priority |
|---|---|---|---|---|
| **1** | `nlp/slot_render.py` vs `nlp/gate.py` + `nlp/gate_policy.py` (the metric gate) | Zero shared imports; both implement dedup + quality-reject + financial-reject in different code (`dedup()`/`dedup_indices()`, `metric_quality()`+`subject_quality()`/`decide()`+`_qual_cat()`, `_FIN_FRAG`/`_FIN_LINEITEM`) | Pick the Spec 0069 stack as canonical; retire `slot_render`'s **gating** (keep only its render helpers `fmt_value`/`render_and_grade` if the display path is still used) | **HIGH** — blocks the metric work; two gates actively diverging |
| **2** | `reports/discover.py` vs `reports/async_discover.py` | `async_discover` does not import `discover`; docstring says *"Reimplements"*; helpers `_subpage_priority` + `_is_html_subpage_candidate` are copy-pasted. **But:** on diff they differ only by a missing docstring — **logic is identical, no behavioral drift today** | DRY: extract the shared helpers into `discover.py`, have `async_discover` import them (the pattern `async_fetch_pdf` already uses) | **LOW** — harmless now; latent future-drift risk only |
| **3** | `reports/classify.py` vs `nonprofits/pipeline_classify.py` | Same table (`lava_corpus.corpus`), same columns, same label set, same job — differ only by LLM engine (Anthropic Haiku tool-use vs Gemma HTTP) and execution shape (inline vs queued stage) | Unify on one classifier core (prompt + labels + parse + write), inject the backend behind one interface (the seam `classifier_clients.py` already provides) | **MEDIUM** — currently a *deliberate* A/B (the `reclassify`/`compare-classify`/`promote-classify` stages exist to compare them); consolidate once a definition wins |

## Checked and cleared — legitimate, NOT duplication

- `nlp/gate.py` / `gate_policy.py` / `gate_runner.py` — deliberate **3-layer split** (oracle / policy / orchestration); they import each other and form one stack.
- `faithfulness/gate_runner.py` — **different domain** (faithfulness verification over metrics+stories); name collision only.
- `nlp/mispairing_check.py` — a **diagnostic**, self-labelled "NOT a gate, applies/persists nothing."
- `reports/fetch_pdf.py` ↔ `async_fetch_pdf.py` — **wrapper**: async imports the sync core (validation/throttle logic is shared). Already DRY.
- `reports/crawler.py` ↔ `async_crawler.py` — **intentional dual backend**: one CLI, `--async` flag dispatches to async. Both live (see "Async crawl path" below).
- `nonprofits/brave_search.py` ↔ `web_search.py` — **dispatcher + backend**: `web_search` wraps `brave_search` for the brave-direct path.
- `nonprofits/tools/pipeline_resolve.py` ↔ `pipeline_resolver.py` — **entry + library** (confirmed; entry imports the library).
- The five "extract" files (`llm_extract`, `extractor`, `reports/extraction`, `reports/pdf_extract`, `faithfulness/pdftotext_extract`) — **five distinct jobs** (LLM metrics / spaCy terms / pypdf page-text / byte-sanitize / poppler full-text). No true duplicate.

## Corrections to earlier claims (made during this investigation)

- **"Mispairing in three places" was overstated.** They are three *different approaches*, not three copies: `gate_policy.column_mispair()` (table-column logic — the only **live gate**), `mispairing_check.py` (geometry diagnostic, **unwired**), and `regen_3_vision_pairing.py` (a vision **spike**). Only one is a gate.
- **"discover/async_discover already drifted" was an over-alarm.** On diff, the only difference is a missing docstring in the async copies; the logic is **identical**. No behavioral inconsistency between sync and async crawls today — the risk is purely *future* drift from the copy-paste.

## Async crawl path (context for #2)

The async stack is **live and operator-selectable**, not dead: the dashboard crawl stage has an `async`
boolean (`stages.py` `ParamSpec(cli_flag="--async")`) → orchestrator passes `--async` → `crawler.py`
imports `async_crawler.run_async` → which uses `async_discover` + `async_fetch_pdf`. So both discovery
implementations genuinely run. `fetch_pdf` is shared safely (async imports sync); `discover` is the
copy-paste (item #2).

## Minor hygiene (noted; not duplication)

- `nonprofits/tools/resolve_websites.py` has a private `_brave_search()` HTTP helper that ignores both `brave_search.py` and `web_search.py` — a small third search path (the file itself is eval-only).
- `reports/pdf_extract.py` is **misnamed** — it does byte-scanning/sanitization, not text extraction. Reads as a text extractor next to `reports/extraction.py`, which is the real page-text one. Rename candidate.

## Method gap (so this doc doesn't repeat the housekeeping's mistake)

This was a **targeted** pass over likely clusters in `lavandula/`, not an exhaustive every-pair
comparison. A fully systematic duplication detector was not built. Treat this as "the clusters we
checked," not "duplication-free everywhere." See the blind-spot note in
[`../housekeeping/SYSTEM-MAP.md`](../housekeeping/SYSTEM-MAP.md) §6.
