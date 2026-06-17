"""Two-stage quality check (reusable harness), first plug-in = loose-OR-composite.

Stage 1  — cheap regex pre-filter, runs on EVERY metric → tiny candidate set.
Stage 2  — DeepSeek (non-vision, our metric model) qualifies ONLY the candidates,
           so the LLM cost scales with the sliver the regex flags, not the corpus.

A "check" = (stage1 regex, stage2 question). Swap those two and the harness handles
any semantically-fuzzy-but-rare quality gate (loose-OR, not-a-metric, abstract-value…).
"""
import json
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
OUT = "/home/ubuntu/research/locard/operations/comp-metric-regression/loose-or-quarantine.json"
KEY = get_secret("lavandula/deepseek/api_key")


# ---------- the check definition (this is all you swap per check) ----------
CHECK = {
    "name": "loose-or-composite",
    # Stage 1: high-recall — the METRIC'S OWN statement is a percentage, AND a comma-list
    # with 'or' appears in the statement OR its source (catches model-flipped 'and' cases).
    "stage1": lambda r: bool(re.search(r"\d\s?%|\bpercent\b", r["statement"] or "", re.I))
                        and bool(re.search(r",[^.]*\bor\b",
                                           (r["statement"] or "") + "  " + (r.get("v_text") or ""), re.I)),
    "system": (
        "You flag ONE metric flaw. FLAW = a single percentage is counted as satisfied when a "
        "person meets just ONE of several DIFFERENT things joined by 'or', which lowers the bar "
        "and inflates the number.\n"
        "FLAWED: '88% feel they have control over their life, are optimistic about their future, "
        "or have positive self-worth' (counts for ANY of three different things).\n"
        "NOT FLAWED: 're-engaged in school or work' (one outcome); 'one or two children' (a number); "
        "'Black or African American' (synonym); 'meeting or exceeding standards' (comparison).\n"
        "Reply with exactly one word: YES (flawed) or NO."
    ),
    # feed the sentence that actually holds the or-list: the statement if it has one,
    # else the source snippet (catches the cases where the model flipped 'or' to 'and').
    "user": lambda r: (r["statement"] if re.search(r",[^.]*\bor\b", r["statement"] or "", re.I)
                       else (r.get("v_text") or r["statement"])),
}


def deepseek(system, user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 8,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())["choices"][0]["message"]["content"].strip().upper()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                return f"ERR:{e}"
    return "ERR"


def main():
    d = json.load(open(DATA))
    # ---- Stage 1 ----
    cands = [r for r in d if CHECK["stage1"](r)]
    print(f"STAGE 1 (regex) → {len(cands)} candidates of {len(d)} ({100*len(cands)/len(d):.1f}%)")
    print()
    # ---- Stage 2 ----
    quarantine = []
    for r in cands:
        v = deepseek(CHECK["system"], CHECK["user"](r))
        yes = v.startswith("YES")
        if yes:
            quarantine.append(r["id"])
        snip = (r.get("v_text") or r["statement"])[:95]
        print(f"  {'QUARANTINE' if yes else 'keep      '}  {r['id']:14} [{v[:3]}]  {snip}")
    print()
    print(f"STAGE 2 (DeepSeek) → {len(quarantine)} confirmed loose-OR-composites → quarantine")
    print("  ", quarantine)
    json.dump({"check": CHECK["name"], "stage1_candidates": [r["id"] for r in cands],
               "quarantine": quarantine}, open(OUT, "w"), indent=1)
    print(f"\nsaved {OUT}")


if __name__ == "__main__":
    main()
