"""M1c — live per-doc runner: the integrated pipeline end-to-end for ONE document.

render -> SELECT [VIRGIN comp-metric prompt b51dc96a] -> GROUND [grounding prompt, assigns
value_ref/subject_ref] -> decide() chain (hardened gate + live is-a-metric for small ints + twin
repair) -> records (tier held). The corpus replay lives in pipeline.py; this re-runs extraction too,
so it's the single live command from a document to final metrics.

Usage: python run_doc.py <sha8>
"""
import json
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
import measurable_value_check as mvc
from pipeline import decide
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text

KEY = get_secret("lavandula/deepseek/api_key")
VIRGIN = open("/home/ubuntu/research/locard/operations/prompt-backups/2026-06-06/comp-metric-prompt.KNOWN-GOOD.txt").read()
GROUND = open("/home/ubuntu/research/locard/operations/comp-metric-regression/comp-metric-grounding.v1.txt").read()


def chat(system, user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 3000,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=180).read())["choices"][0]["message"]["content"].strip()
    if out.startswith("```"):
        out = out.split("```")[1].lstrip("json").strip()
    return json.loads(out)


def run_doc(conn, sha):
    """Live end-to-end pipeline for one doc -> final metric records (publish/quarantine + reason).
    Stage 0 = corpus-inclusion (REQ-PROD-018): low-value docs are excluded BEFORE extraction."""
    from sqlalchemy import text as _t
    from lowvalue_gate import doc_included
    m = conn.execute(_t("SELECT COALESCE(figure_count,0), COALESCE(total_text_chars,0), COALESCE(page_count,1) "
                        "FROM lava_parse.documents WHERE content_sha256=:s"), {"s": sha}).fetchone()
    if m:
        ok, why = doc_included(m[0], m[1] / max(1, m[2]))
        if not ok:
            return [{"doc_excluded": True, "reason": "lowvalue_doc", "detail": why}]
    sectext, tagged, idmap = render.render_tagged(conn, sha)
    metrics = chat(VIRGIN, sectext)                                            # SELECT
    lines = [f"M{i+1}: {m.get('statement')} (value={m.get('metric_value')}, subject={m.get('label')})"
             for i, m in enumerate(metrics)]
    g = chat(GROUND, "METRICS:\n" + "\n".join(lines) + "\n\nDOCUMENT:\n" + tagged)   # GROUND
    bym = {x.get("m"): x for x in g if isinstance(x, dict)}
    out = []
    for i, m in enumerate(metrics):
        row = bym.get(i + 1) or (g[i] if i < len(g) and isinstance(g[i], dict) else {})
        vr, srf = render.norm(row.get("value_ref")), render.norm(row.get("subject_ref"))
        val = m.get("metric_value")
        measured = None
        if gate.is_small_int(val):                                             # IS-A-METRIC (small ints, live)
            measured = not mvc.deepseek(mvc.CHECK["system"], m.get("statement") or "").startswith("NOT")
        d = {"value": val, "label": m.get("label"), "value_ref": vr, "subject_ref": srf}
        dec, reason, _ = decide(d, idmap, measured)                            # gate + twin-repair
        out.append({"statement": m.get("statement"), "value": val, "subject": m.get("label"),
                    "value_ref": vr, "subject_ref": srf, "decision": dec, "reason": reason})
    return out


if __name__ == "__main__":
    sha8 = sys.argv[1]
    eng = make_app_engine()
    with eng.connect() as conn:
        full = conn.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": sha8 + "%"}).scalar()
        recs = run_doc(conn, full)
    pub = sum(1 for r in recs if r["decision"] == "publish")
    print(f"\n{sha8}: {len(recs)} metrics -> {pub} publish / {len(recs)-pub} quarantine\n")
    for r in recs:
        print(f"  [{r['decision']}/{r['reason']}] val={str(r['value'])[:10]:>10} | {(r['statement'] or '')[:68]}")
