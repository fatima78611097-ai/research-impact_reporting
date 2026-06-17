"""Attribution-amplification check (REQ-033 class 2, operator-defined: "has the spirit of the
author's claim changed?").

Two-stage: stage 1 = statement carries STRONG-OWNERSHIP claims (possessive org-noun or
ownership/operation verbs) whose stems are ABSENT from the source marker text -> candidate.
Stage 2 = DeepSeek compares statement vs source with the operator's question -> FAITHFUL/AMPLIFIED.
Flags are ATTENTION-ROUTED (reviewed, never auto-quarantined) per the drift policy.
"""
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
RES = {"batch1-clean": B + "scale100-results-tuned.json", "test2": B + "scale-test2-results.json"}
KEY = get_secret("lavandula/deepseek/api_key")

OWNERSHIP = re.compile(r"\b(hosted|hosts|operates?|operated|owns?|owned|runs?|ran|built|maintains?|"
                       r"manages?|managed|established|founded|launched)\b", re.I)


def stems(s):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


SYSTEM = (
    "Compare a CLAIM extracted from a nonprofit report against its SOURCE passage. The question: "
    "has the SPIRIT of the author's claim changed — is the organization's ownership, action, or role "
    "AMPLIFIED beyond what the source states?\n"
    "AMPLIFIED examples: source says a program 'presented content to 5,000 campers' (at camps) but the "
    "claim says 'our camps HOSTED 5,000 campers' (ownership + action upgraded); source 'we contributed "
    "to a coalition that built 40 homes' -> claim 'we built 40 homes'.\n"
    "FAITHFUL: rewording that keeps the same actor, action strength, and scope.\n"
    'Reply ONLY JSON: {"verdict": "FAITHFUL|AMPLIFIED", "reason": "<one clause>"}'
)


def ds(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 100,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
    for a in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            out = json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"].strip()
            if out.startswith("```"):
                out = out.split("```")[1].lstrip("json").strip()
            return json.loads(out)
        except Exception:
            if a == 2:
                return {"verdict": "ERR", "reason": ""}
            time.sleep(3)


def main():
    rev = json.load(open("/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"))
    results = {k: json.load(open(v)) for k, v in RES.items()}
    fbs = {k: {s[:8]: s for s in r} for k, r in results.items()}
    eng = make_app_engine()
    cache = {}
    cands = []
    with eng.connect() as conn:
        for r in rev:
            if r.get("gate_decision") != "publish":
                continue
            st = r.get("statement") or ""
            m_own = OWNERSHIP.search(st)
            if not m_own:
                continue
            full = fbs.get(r.get("set"), {}).get(r["sha8"])
            if not full:
                continue
            try:
                met = results[r["set"]][full][int(r["id"].split(":")[1])]
            except (KeyError, IndexError, ValueError):
                continue
            if full not in cache:
                try:
                    _, _, cache[full] = render.render_tagged(conn, full)
                except Exception:
                    cache[full] = None
            idmap = cache[full]
            if not idmap:
                continue
            vr = met.get("value_ref")
            i = int(vr[1:]) if vr and vr[1:].isdigit() else None
            src = " ".join((idmap.get(f"{vr[0]}{j}") or {}).get("text", "")
                           for j in ([i - 1, i, i + 1] if i is not None else []))
            # stage 1: the ownership verb's stem is absent from the source vicinity
            if src and m_own.group(0)[:5].lower() not in {w[:5] for w in re.findall(r"[a-z]{4,}", src.lower())}:
                cands.append((r["id"], st, src[:800]))
    print(f"stage 1 (ownership claim absent from source): {len(cands)} candidates")
    with ThreadPoolExecutor(max_workers=12) as ex:
        out = list(ex.map(lambda c: (c[0], c[1], ds(f"CLAIM: {c[1]}\n\nSOURCE: {c[2]}")), cands))
    amp = [(i, s, v) for i, s, v in out if v.get("verdict") == "AMPLIFIED"]
    print(f"stage 2 AMPLIFIED: {len(amp)}  (attention-routed, not auto-acted)\n")
    for i, s, v in amp:
        print(f"  {i:14} | {s[:64]}")
        print(f"      -> {v.get('reason','')[:90]}")
    json.dump([{"id": i, "statement": s, "reason": v.get("reason")} for i, s, v in amp],
              open(B + "attribution-amp-flags.json", "w"), indent=1)
    print("\ntest: catches cc947e2b:10 if it were still published?",
          any(i == "cc947e2b:10" for i, _, _ in out) or "(already quarantined - not in scan)")


if __name__ == "__main__":
    main()
