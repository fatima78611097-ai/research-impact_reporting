"""Vision model bake-off for the infographic problem.

Two tasks, scored against ground truth, across a slate of vision models:
  CANARY     — "is there an infographic on this page?" (easy; high recall is what matters)
  EXTRACTION — read the prominently-featured numbers correctly (hard; the guarantee).
               Run k passes to expose the SHARED-BLIND-SPOT failure: a model that is
               *consistently wrong* (e.g. always reads the stylized 56 as 6) is worse than
               useless — it manufactures false agreement with the broken parse.

Models: Gemini 2.5 Flash + Flash-Lite via REST (key in env GEMINI_API_KEY or SSM
'lavandula/gemini/api_key'). Add more by extending MODELS. Opus is the reference ceiling
(run separately via the validation workflow).

Cost is computed from the API's returned token usage at each model's published rate.

  GEMINI_API_KEY=... python3 bake-off/run_bakeoff.py
  python3 bake-off/run_bakeoff.py --passes 3 --models gemini-2.5-flash,gemini-2.5-flash-lite
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

# published per-1M-token rates (USD) — used for cost
RATES = {
    "gemini-2.5-flash":      {"in": 0.30, "out": 2.50},
    "gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40},
}
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

CANARY_PROMPT = (
    "Look at this page from a nonprofit report. Does it contain an INFOGRAPHIC — a designed "
    "graphic that features numbers as big stylized callouts, stat blocks, charts, or icon+number "
    "tiles (as opposed to plain paragraphs or a financial table)? Answer with exactly one word: "
    "YES or NO."
)
EXTRACT_PROMPT = (
    "This nonprofit report page has an infographic. Extract ONLY the prominently-featured "
    "metrics — the big stat tiles/callouts, key outcome percentages, and labeled chart/table "
    "figures — NOT raw axis ticks. Transcribe digits exactly as printed; do not infer or round. "
    "For each: value (digits, no commas), label (what it counts), and box [ymin, xmin, ymax, xmax] "
    'normalized 0-1000 (top, left, bottom, right). Reply ONLY JSON: '
    '{"numbers": [{"value": <digits>, "label": "<what it counts>", "box": [ymin, xmin, ymax, xmax]}]}'
)


def _key():
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k
    try:
        sys.path.insert(0, "/home/ubuntu/research")
        from lavandula.common.secrets import get_secret
        return get_secret("gemini-api-key")  # SSM /cloud2.lavandulagroup.com/gemini-api-key
    except Exception:
        sys.exit("No Gemini key: set GEMINI_API_KEY or SSM short-name 'gemini-api-key'.")


def gemini(model, prompt, img_path, key, temperature=0.0):
    """One REST call. Returns (text, in_tokens, out_tokens)."""
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": b64}}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 3000},
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=120).read())
            cand = (r.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            u = r.get("usageMetadata", {})
            return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
        except Exception as e:
            if attempt == 3:
                return f"ERR:{e}", 0, 0
            time.sleep(2 * (attempt + 1))


def _nums(text):
    """Pull numeric values from the model's JSON (or loose) reply."""
    out = []
    try:
        j = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        for n in j.get("numbers", []):
            v = re.sub(r"[^0-9.]", "", str(n.get("value", "")))
            if v:
                out.append(float(v))
    except Exception:
        for m in re.findall(r"\d[\d,]*\.?\d*", text):
            out.append(float(m.replace(",", "")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--testset", default=os.path.join(HERE, "test-set.json"))
    args = ap.parse_args()
    key = _key()
    models = args.models.split(",")
    ts = json.load(open(args.testset))
    infographic = [t for t in ts if t["infographic"]]
    verified = [t for t in ts if t.get("verified") and t.get("true_values")]

    report = {}
    for model in models:
        rate = RATES.get(model, {"in": 0, "out": 0})
        tin = tout = 0
        # ---- CANARY (1 pass/item) ----
        canary_hit = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            cres = list(ex.map(lambda t: gemini(model, CANARY_PROMPT, t["img"], key), ts))
        for t, (txt, i, o) in zip(ts, cres):
            tin += i; tout += o
            said_yes = txt.strip().upper().startswith("YES")
            if said_yes == t["infographic"]:
                canary_hit += 1
        # ---- EXTRACTION (k passes/item) on verified-truth items ----
        ex_rows = []
        for t in verified:
            passes = []
            for p in range(args.passes):
                txt, i, o = gemini(model, EXTRACT_PROMPT, t["img"], key, temperature=0.4)
                tin += i; tout += o
                passes.append(set(_nums(txt)))
            # per true value: found in how many passes? + the model's consistent reading
            per_val = {}
            for tv in t["true_values"]:
                hits = sum(1 for pv in passes if any(abs(x - tv) < 0.5 for x in pv))
                per_val[tv] = hits
            ex_rows.append({"id": t["id"], "true": t["true_values"], "found": per_val,
                            "passes": [sorted(p) for p in passes]})
        cost = tin / 1e6 * rate["in"] + tout / 1e6 * rate["out"]
        report[model] = {
            "canary_accuracy": round(canary_hit / len(ts), 3),
            "extraction": ex_rows,
            "tokens_in": tin, "tokens_out": tout, "cost_usd": round(cost, 4),
        }

    # ---- print scorecard ----
    print(f"\n=== BAKE-OFF ({len(ts)} pages, {len(verified)} with verified truth, {args.passes} extraction passes) ===\n")
    for model, r in report.items():
        print(f"## {model}")
        print(f"  canary accuracy: {r['canary_accuracy']*100:.0f}%  (recall on infographics is the key metric)")
        print(f"  extraction (verified items): true value found in N/{args.passes} passes — blind spot = 0/N")
        for row in r["extraction"]:
            marks = "  ".join(f"{tv}:{n}/{args.passes}" for tv, n in row["found"].items())
            blind = [tv for tv, n in row["found"].items() if n == 0]
            print(f"    {row['id']:14} {marks}" + (f"   ⚠ BLIND-SPOT (never read): {blind}" if blind else ""))
            if blind:
                print(f"        model kept reading: {row['passes']}")
        # extrapolated corpus cost (1M canary pages light + extraction on flagged)
        per_read = r["cost_usd"] / max(1, (len(ts) + len(r['extraction']) * args.passes))
        print(f"  cost this run: ${r['cost_usd']}  (~${per_read*1e6:.0f}/1M reads at this mix)\n")

    json.dump(report, open(os.path.join(HERE, "bakeoff-result.json"), "w"), indent=1)
    print("saved bake-off/bakeoff-result.json")


if __name__ == "__main__":
    main()
