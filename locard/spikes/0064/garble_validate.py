"""0064 — validate that low pdftotext_coverage == real glyph-soup garble and
high coverage == clean text, using STORED docling section text (no re-parse).
Glyph-soup signature: high fraction of 1-char whitespace tokens, low mean token len."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_app_engine

RUN = 10
eng = make_app_engine()


def soup_score(s):
    toks = s.split()
    if not toks:
        return (0.0, 0.0, 0)
    frac1 = sum(1 for t in toks if len(t) == 1) / len(toks)
    meanlen = sum(len(t) for t in toks) / len(toks)
    return (frac1, meanlen, len(toks))

with eng.connect() as c:
    # sample docs per coverage band that HAVE quarantined metrics
    bands = [
        ("pdftotext_failed", "d.text_source='pdftotext_failed'"),
        ("cov<0.5",        "d.pdftotext_coverage<0.5 AND d.text_source IS DISTINCT FROM 'pdftotext_failed'"),
        ("cov0.7-0.9",     "d.pdftotext_coverage>=0.7 AND d.pdftotext_coverage<0.9"),
        ("cov>=0.9",       "d.pdftotext_coverage>=0.9"),
    ]
    for label, cond in bands:
        rows = c.execute(text(f"""
            WITH q AS (
              SELECT DISTINCT m.content_sha256 FROM lava_vocab.llm_metrics m
              WHERE m.run_id=:r AND m.verification_tier='quarantine'
            )
            SELECT d.content_sha256
            FROM q JOIN lava_parse.documents d ON d.content_sha256=q.content_sha256
            WHERE {cond}
            ORDER BY md5(d.content_sha256||'val') LIMIT 5
        """), {"r": RUN}).fetchall()
        print(f"\n=== band {label}: {len(rows)} sample docs ===")
        for (sha,) in rows:
            body = c.execute(text("""
                SELECT string_agg(body_text, ' ' ORDER BY section_index)
                FROM lava_parse.sections WHERE content_sha256=:s
            """), {"s": sha}).scalar() or ""
            frac1, meanlen, ntok = soup_score(body[:8000])
            flag = "  <<< GLYPH-SOUP" if frac1 > 0.4 or meanlen < 2.5 else ""
            print(f"  {sha[:8]}  frac_1char={frac1:.2f}  mean_tok_len={meanlen:.2f}  toks={ntok}{flag}")
