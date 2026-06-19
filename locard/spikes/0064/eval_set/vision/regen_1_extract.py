"""Regenerate the metric-review data — STEP 1 of 2: EXTRACT.

Runs the FEATURED metric prompt (`_METRICS_PROMPT` in lavandula/nlp/llm_extract.py)
over 25 NTEE-P documents and writes `prompt_test.json` (one entry per doc: the new
metrics + the old run-12 metrics for reference). STEP 2 is regen_2_build_review.py.

    python3 regen_1_extract.py
"""
import json
import os
import sys
import httpx

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from lavandula.nlp.marker_render import render_tagged, SkipDocument
from lavandula.nlp.llm_extract import _METRICS_PROMPT, call_deepseek
from sqlalchemy import text

VDIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(VDIR, "prompt_test.json")
N_DOCS = 25

eng = make_app_engine()
api_key = get_secret("lavandula/deepseek/api_key")

with eng.connect() as c:
    docs = [r[0] for r in c.execute(text("""
        SELECT m.content_sha256 FROM lava_vocab.llm_metrics m
        JOIN lava_corpus.corpus co ON co.content_sha256 = m.content_sha256
        WHERE m.run_id = 12 AND co.material_type IN
          ('annual_report','impact_report','year_in_review','community_benefit_report','donor_impact_report')
        GROUP BY m.content_sha256 HAVING count(*) >= 4
        ORDER BY md5(m.content_sha256) LIMIT :n"""), {"n": N_DOCS}).fetchall()]
print("docs:", [d[:8] for d in docs], flush=True)

results = []
with httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=180) as client, eng.connect() as c:
    for sha in docs:
        try:
            tagged = render_tagged(c, sha).tagged_text
        except SkipDocument as e:
            results.append({"sha8": sha[:8], "error": f"skip:{e}"})
            print(f"  {sha[:8]} SKIP {e}", flush=True)
            continue
        try:
            new, _pt, _ct = call_deepseek(api_key, tagged, client, _METRICS_PROMPT)
            if not isinstance(new, list):
                new = []
        except Exception as e:
            results.append({"sha8": sha[:8], "error": f"deepseek:{type(e).__name__}"})
            print(f"  {sha[:8]} ERR {e}", flush=True)
            continue
        old = [dict(r._mapping) for r in c.execute(text(
            "SELECT metric_value, metric_type, source_snippet, value_ref, subject_ref "
            "FROM lava_vocab.llm_metrics WHERE run_id=12 AND content_sha256=:s ORDER BY id"),
            {"s": sha}).fetchall()]
        name = c.execute(text(
            "SELECT ns.name FROM lava_parse.documents d "
            "LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein=d.source_org_ein "
            "WHERE d.content_sha256=:s"), {"s": sha}).scalar()
        results.append({"sha8": sha[:8], "org": str(name), "new": new, "old": old})
        print(f"  {sha[:8]} {str(name)[:30]:30} old={len(old)} new={len(new)}", flush=True)

json.dump(results, open(OUT, "w"), default=str)
print(f"DONE -> {OUT}", flush=True)
