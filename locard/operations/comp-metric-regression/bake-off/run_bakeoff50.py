"""Run the 50-report infographic bake-off → results for the review viewer.

Each report's rendered infographic page is sent to each model for:
  canary    — "infographic? yes/no"
  extraction — the featured numbers (value+label), one pass (operator judges in the viewer)

Writes bakeoff50-result.json into the web-served bakeoff/ dir.
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from run_bakeoff import gemini, CANARY_PROMPT, EXTRACT_PROMPT, _key

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/"
MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


def parse_pairs(text):
    """Return [{value, label}] from the model's JSON reply (loose-tolerant)."""
    try:
        j = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        out = []
        for n in j.get("numbers", []):
            v = str(n.get("value", "")).strip()
            if not v:
                continue
            box = n.get("box")
            box = [float(x) for x in box] if isinstance(box, list) and len(box) == 4 else None
            out.append({"value": v, "label": str(n.get("label", "")).strip(), "box": box})
        return out
    except Exception:
        return [{"value": m, "label": "", "box": None} for m in re.findall(r"\d[\d,]*\.?\d*", text)]


def main():
    key = _key()
    reports = [r for r in json.load(open(os.path.join(HERE, "reports-50.json"))) if r.get("img")]

    def work(r):
        img = VIS + r["img"]
        out = {"sha8": r["sha8"], "org": r.get("org"), "figs_per_page": r.get("figs_per_page"),
               "img": r["img"], "models": {}}
        for m in MODELS:
            ctext, _, _ = gemini(m, CANARY_PROMPT, img, key)
            etext, _, _ = gemini(m, EXTRACT_PROMPT, img, key, temperature=0.0)
            out["models"][m] = {
                "canary": "yes" if ctext.strip().upper().startswith("YES") else ("no" if ctext.strip().upper().startswith("NO") else ctext.strip()[:12]),
                "numbers": parse_pairs(etext),
            }
        return out

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(work, reports))

    dest = VIS + "bakeoff/bakeoff50-result.json"
    json.dump(results, open(dest, "w"), indent=1)
    print(f"ran {len(results)} reports x {len(MODELS)} models -> {dest}")
    # quick canary tally
    for m in MODELS:
        yes = sum(1 for r in results if r["models"][m]["canary"] == "yes")
        avg_nums = sum(len(r["models"][m]["numbers"]) for r in results) / max(1, len(results))
        print(f"  {m}: canary yes {yes}/{len(results)} | avg numbers extracted/page {avg_nums:.1f}")


if __name__ == "__main__":
    main()
