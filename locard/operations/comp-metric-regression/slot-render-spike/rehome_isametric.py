"""SPIKE #2 (isolated, read-only): does the is-a-metric reject signal survive when we read the
SOURCE marker text + label instead of the LLM-composed statement?

The current rules (item1_nonmetric_rules.py) regex the composed `statement`. In the slot world that
sentence is gone. Re-point the SAME regexes at (v_text + s_text + label) — the text the model read —
and measure agreement with the statement-based flags. High agreement => the gate survives the switch.

Reads review-data.json + item1-flags.json read-only; writes only into this folder.
"""
import json
import re
import collections

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"

# the exact rules from item1_nonmetric_rules.py
TENURE = re.compile(r"\b(\d+|\w+ty|\w+th) years? (of|in|serving|of service)|celebrat\w+ \d+ years"
                    r"|\b\d+(st|nd|rd|th) (anniversary|year)|(\b|-)(\d+)-year (history|legacy|tradition)"
                    r"|for (over|more than|nearly) \d+ years|has been .{0,40}(since|for) (19|20)\d{2}"
                    r"|\bsince (19|20)\d{2}\b|turned \d+ years old", re.I)
FORECAST = re.compile(r"\bwill \w+|\bby 20\d{2}\b|\bgoal of\b|\baims? to\b|\bplans? to\b|\bprojected\b"
                      r"|\bexpected to\b|\bcould \w+|\btoward (its|a|the) .{0,30}(goal|target)", re.I)
DURATION_EP = re.compile(r"\b(stayed?|spent|remained|lived|housed|sheltered|works?|working)\b.{0,40}\b\d+([\s-]+)(day|week|month|night|hour)s?\b"
                         r"|\b\d+[\s-]+(day|week|month|night|hour)s?\s+(at|in)\s+(the\s+)?(shelter|program|facility|home)"
                         r"|\bnamed\s+[A-Z][a-z]+\b.{0,80}\b\d+", re.I)
FACTOID = re.compile(r"\b(1|one) in (\d+|\w+)\b|of (all )?(americans|u\.?s\.? adults|adults|women|men|children"
                     r"|people|youth) (are|have|experience|live|face)|\bnationally\b|in the (u\.?s\.?|united states)\b", re.I)
# label hints that re-home the forecast/tenure signal when the source fragment is terse
LABEL_FORECAST = re.compile(r"\b(goal|target|projected|planned|forecast)\b", re.I)
LABEL_TENURE = re.compile(r"\b(year|anniversary|history|legacy|founded|since|tenure|operating)\b", re.I)


def _isyear(v):
    try:
        return 1900 <= float(v) <= 2099 and float(v) == int(float(v))
    except (TypeError, ValueError):
        return False


def hits_on(text, value, label):
    """run the reject regexes on a text blob + label."""
    h = []
    if TENURE.search(text) or LABEL_TENURE.search(label or ""):
        h.append("tenure")
    if _isyear(value):
        bare = str(int(float(value)))
        if re.search(rf"(?<![\d,.]){bare}(?![\d,])(?!\.\d)", text):
            h.append("year-as-value")
    if FORECAST.search(text) or LABEL_FORECAST.search(label or ""):
        h.append("forecast")
    if FACTOID.search(text):
        h.append("factoid")
    if DURATION_EP.search(text):
        h.append("episode-duration")
    return set(h)


def main():
    rev = json.load(open(VIS))
    by = {r["id"]: r for r in rev}
    # what the CURRENT (statement-based) item1 flagged
    item1 = json.load(open(B + "item1-flags.json"))
    cur_flags = set(item1["flagged"])

    stmt_hits, src_hits = {}, {}
    for r in rev:
        v, lab = r.get("value"), r.get("label")
        stmt = r.get("statement") or ""
        src = (r.get("v_text") or "") + " \n " + (r.get("s_text") or "")
        stmt_hits[r["id"]] = hits_on(stmt, v, lab)
        src_hits[r["id"]] = hits_on(src, v, lab)

    # agreement on the population that CURRENTLY has a statement-hit (the live signal)
    stmt_flagged = {i for i, h in stmt_hits.items() if h}
    src_flagged = {i for i, h in src_hits.items() if h}
    both = stmt_flagged & src_flagged
    only_stmt = stmt_flagged - src_flagged       # signal LOST by the switch
    only_src = src_flagged - stmt_flagged         # NEW signal from source/label

    print(f"statement-based stage-1 hits: {len(stmt_flagged)}")
    print(f"source+label  stage-1 hits:   {len(src_flagged)}")
    print(f"  both (survives):     {len(both)}")
    print(f"  LOST (stmt only):    {len(only_stmt)}   <- the risk: rejects we'd stop catching")
    print(f"  NEW  (src/label only): {len(only_src)}")

    # the load-bearing question: of the metrics the current rule actually QUARANTINED
    # (applied flags), how many would the source-based rule still catch?
    recovered = cur_flags & src_flagged
    missed = cur_flags - src_flagged
    print(f"\nvs the {len(cur_flags)} APPLIED item1 rejects:")
    print(f"  re-caught by source+label: {len(recovered)} ({100*len(recovered)/max(1,len(cur_flags)):.0f}%)")
    print(f"  MISSED (would re-publish): {len(missed)}")
    for i in sorted(missed):
        r = by.get(i, {})
        print(f"    {i:14} stmt_hit={stmt_hits.get(i)}  label='{(r.get('label') or '')[:30]}'")
        print(f"        stmt: {(r.get('statement') or '')[:78]}")
        print(f"        src : {((r.get('v_text') or '')+' '+(r.get('s_text') or ''))[:78]}")

    # by-class breakdown of what's lost
    lost_classes = collections.Counter(c for i in only_stmt for c in stmt_hits[i])
    print(f"\nlost signal by class: {dict(lost_classes)}")
    json.dump({"missed_applied": sorted(missed), "lost_stmt_only": sorted(only_stmt),
               "new_src_only": sorted(only_src)}, open(B + "slot-render-spike/rehome-result.json", "w"), indent=1)


if __name__ == "__main__":
    main()
