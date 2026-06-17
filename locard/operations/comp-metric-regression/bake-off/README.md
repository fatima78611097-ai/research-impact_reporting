# Vision model bake-off — the infographic problem

Decides, with data, which vision model to use for infographic documents, where the text
parse silently drops/mangles digits (a stylized "56" read as "6") and text-only gates give
zero signal. See the design discussion: the guarantee comes from reading the page IMAGE.

## What it measures

Two tasks, per model, scored against ground truth:

1. **CANARY** — "is there an infographic on this page?" The easy task. **Recall is what matters**
   (a false "no" re-introduces the silent-error risk). Used to route pages to vision-extraction.
2. **EXTRACTION** — read the featured numbers correctly. The hard task; the guarantee. Run over
   **k passes** to expose the **shared-blind-spot** failure: a model that is *consistently wrong*
   (always reads 56 as 6) agrees with the broken parse and manufactures false confidence — that's
   worse than a model that's noisy. A true value found in `0/k` passes is flagged `BLIND-SPOT`,
   and the model's consistent (wrong) reading is printed so you can see it.

Cost is computed from each call's returned token usage at the model's published rate, and
extrapolated to per-1M-reads.

## Run it

```bash
# key: env var, or store in SSM as 'lavandula/gemini/api_key' (same pattern as DeepSeek)
GEMINI_API_KEY=AIza... python3 run_bakeoff.py
# options
python3 run_bakeoff.py --passes 3 --models gemini-2.5-flash,gemini-2.5-flash-lite
```

Add a model: extend `MODELS` / `RATES` in `run_bakeoff.py` (any Gemini model id works as-is;
other providers need an adapter alongside `gemini()`).

## The test set (`test-set.json`)

Each item: `{id, img, infographic: bool, true_values: [...], verified: bool}`.
- **EXTRACTION is scored only on `verified: true` items** (true_values confirmed by reading the
  page). Currently **1 verified anchor**: `1a70e980` — a heavily stylized infographic page with
  big light-on-color numbers `4693 / 213 / 110 / 63`, the exact failure case.
- The other infographic items carry the *parsed* value as a provisional truth (`verified: false`) —
  used for the **canary** (recall) but NOT trusted for extraction scoring until verified.

### To make this a full bake-off, expand the test set:
1. **Add verified extraction items** — open each candidate page image, record the *true* featured
   numbers, set `true_values` + `verified: true`. ~15–20 stylized-number pages gives a solid read.
2. **Add canary negatives** — text/financial-table page images with `infographic: false`, so the
   canary gets a precision score (not just recall). The reviewbox set is all metric-bearing pages,
   so these need to be added from text pages.

## Reading the result

- **Canary**: pick the cheapest model with ~100% recall on infographics.
- **Extraction**: pick the cheapest model that reads the verified true values at `k/k` with **no
  blind spots**. A model that's `0/k` on a true value (and consistently returns a wrong number) is
  disqualified for the authority role regardless of price — it's the shared-blind-spot trap.
- The recommended outcome is usually **tiered**: cheapest passing model on the canary (high volume,
  easy), best-reading model on extraction (low volume, hard).

Opus is the quality **ceiling/reference** — run it via the dual-Opus validation workflow on the
same images to see what a strong model achieves, then find the cheapest model that matches it.
