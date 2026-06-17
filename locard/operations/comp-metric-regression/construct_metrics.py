"""Implementation of the metric-construction approach from nonprofit_metric_extraction_guidance.md,
with the fixes from the review applied (rewrite limited to source facts; financial as its own tier;
capacity-vs-activity rule; explicit auditable rejections). Runs on a doc's parsed text -> structured
metric records per the §7 schema. This is the thing under test; vision hand-review validates its output.
"""
import json
import re
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")

SYSTEM = (
"You convert a nonprofit annual/impact report into structured metric records. Follow this rubric exactly.\n\n"
"A METRIC is a complete numeric claim that measures something the nonprofit did, reached, produced, used, "
"changed, or achieved in the reporting year. Required parts: quantity + concrete measured subject + "
"action/relationship + enough context to stand alone for a reader who knows only the mission and report year.\n\n"
"Classify each numeric claim into a tier:\n"
"- outcome_impact: change/benefit/result for people or communities (85% improved reading; recidivism fell 12%).\n"
"- reach_output: scale of service delivered (served 6,000 families; 12,500 meals).\n"
"- capacity_input: resources/infrastructure/staff/partners/locations/vehicles (9 vans; 16 centers; 42 staff; 4 partners).\n"
"- financial: dollars raised/spent/held (revenue, expenses, net assets, funds raised).\n"
"- activity_count: things done or launched, not reach or outcome (launched 3 programs; hosted 5 events; 25 teams).\n"
"- non_metric: REJECT. The number does not measure output/reach/capacity/activity/outcome. Includes: event "
"occurrence encoded as 1 (launched X = 1); rankings (#1, 3rd largest); awards/honors (1 of 7 honorees); dates/years "
"(founded 2021); durations (9-month program); labels/ratings (4-star); unsupported multipliers (doubled); forecasts.\n\n"
"Distinguish capacity (what EXISTS) from activity (what was DONE): '16 centers' = capacity; 'opened 2 centers' = activity.\n\n"
"Write metric_statement as a complete sentence. CRITICAL: use ONLY facts present in the source text. Never invent a "
"subject, object, or context not in the source. If needed context is absent, set standalone_quality='insufficient_context' "
"and do not fabricate it.\n\n"
"Return ONLY a JSON array. Each element: {\"value\", \"unit\", \"measured_subject\", \"action_or_relationship\", "
"\"metric_statement\", \"metric_tier\", \"standalone_quality\" (complete|needs_rewrite|insufficient_context|reject), "
"\"validity\" (valid_metric|borderline_metric|non_metric), \"reason\"}.\n"
"Prefer fewer high-quality metrics. Include non_metric rejections with a reason so the decision is auditable."
)


def deepseek(system, user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 4000,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())["choices"][0]["message"]["content"].strip()


TOTAL_PAT = re.compile(r"total (revenue|support|revenues|expenses?|operating revenue|operating expense)|"
                       r"net (change|increase|decrease) in net assets|net assets (increased|decreased|grew)|"
                       r"(increase|change) in net assets", re.I)
EVENT_CUE = re.compile(r"\b(raised|gala|fundraiser|campaign|granted|awarded|scholarship|donated)\b", re.I)
STATEMENT_TERM = re.compile(r"revenue|expenses?|net assets|assets|liabilities|cash|investment|"
                            r"contributions|grants|gaming|administrative|fund-?rais|restricted|endowment|reserve", re.I)


def roll_up_financials(records):
    """Deterministic roll-up (moved out of the prompt): among financial metrics keep top-line totals
    and narrative/event amounts; drop statement line items, expense categories, and balances."""
    kept, dropped = [], []
    for r in records:
        if r.get("metric_tier") != "financial":
            kept.append(r)
            continue
        blob = (r.get("measured_subject") or "") + " " + (r.get("metric_statement") or "")
        if TOTAL_PAT.search(blob):
            kept.append(r)
        elif EVENT_CUE.search(blob) and not STATEMENT_TERM.search(r.get("measured_subject") or ""):
            kept.append(r)
        else:
            dropped.append(r)
    return kept, dropped


def main(sha8):
    eng = make_app_engine()
    with eng.connect() as conn:
        from sqlalchemy import text
        full = conn.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": sha8 + "%"}).scalar()
        sectext, _, _ = render.render_tagged(conn, full)
    out = deepseek(SYSTEM, sectext[:14000])
    if out.startswith("```"):
        out = out.split("```")[1].lstrip("json").strip()
    try:
        records = json.loads(out)
    except json.JSONDecodeError:
        print("PARSE FAIL -- raw model output:\n" + out)
        return
    kept, dropped = roll_up_financials(records)
    print(json.dumps(kept, indent=2))
    print(f"\n--- roll-up: {len(records)} model records -> {len(kept)} kept; "
          f"dropped {len(dropped)} financial line items: "
          + "; ".join((r.get("measured_subject") or "?") for r in dropped))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "011c1177")
