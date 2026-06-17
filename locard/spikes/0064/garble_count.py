"""0064 — definitive corpus-wide glyph-soup count from STORED docling text.
space_fraction high <=> avg token len ~1 <=> glyph-soup garble."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_app_engine

eng = make_app_engine()
with eng.connect() as c:
    rows = c.execute(text("""
      WITH dd AS (SELECT DISTINCT content_sha256 FROM lava_vocab.llm_metrics WHERE run_id=10),
      body AS (
        SELECT s.content_sha256,
               sum(length(s.body_text)) AS chars,
               sum(length(s.body_text) - length(replace(s.body_text,' ',''))) AS spaces
        FROM lava_parse.sections s JOIN dd ON dd.content_sha256=s.content_sha256
        GROUP BY s.content_sha256 HAVING sum(length(s.body_text)) > 200
      ),
      scored AS (SELECT content_sha256, spaces::float/NULLIF(chars,0) AS sf FROM body)
      SELECT CASE WHEN sf>0.40 THEN 'a_>0.40 severe glyph-soup'
                  WHEN sf>0.30 THEN 'b_0.30-0.40 likely soup'
                  WHEN sf>0.22 THEN 'c_0.22-0.30 suspect'
                  ELSE 'd_<=0.22 normal text' END AS band,
             count(*) docs
      FROM scored GROUP BY band ORDER BY band
    """)).fetchall()
    print("=== run-10 docs by glyph-soup score (space fraction of stored docling text) ===")
    for b, n in rows:
        print(f"  {b:32} {n}")

    r = c.execute(text("""
      WITH dd AS (SELECT DISTINCT content_sha256 FROM lava_vocab.llm_metrics WHERE run_id=10),
      body AS (SELECT s.content_sha256, sum(length(s.body_text)) c,
               sum(length(s.body_text)-length(replace(s.body_text,' ',''))) sp
               FROM lava_parse.sections s JOIN dd ON dd.content_sha256=s.content_sha256
               GROUP BY s.content_sha256 HAVING sum(length(s.body_text))>200),
      soup AS (SELECT content_sha256 FROM body WHERE sp::float/NULLIF(c,0)>0.30)
      SELECT count(DISTINCT soup.content_sha256) soup_docs, count(m.*) metrics,
             count(m.*) FILTER (WHERE m.verification_tier='quarantine') quarantined
      FROM soup JOIN lava_vocab.llm_metrics m
        ON m.content_sha256=soup.content_sha256 AND m.run_id=10
    """)).fetchone()
    print(f"\n=== impact of glyph-soup docs (space_frac>0.30) ===")
    print(f"  soup_docs={r[0]}  metrics_in_them={r[1]}  quarantined_in_them={r[2]}")
    print(f"  metrics as pct of 68,772: {r[1]/68772:.3%}   docs as pct of 3,233: {r[0]/3233:.3%}")
