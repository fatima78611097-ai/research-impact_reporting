"""0064 — DEFINITIVE prevalence analysis: how widespread is font-garble vs
infographic/grouping in run-10 (p20-v1)? Pure DB, no GPU.

Strategy: calibrate the garble fingerprint on the 2 evidence docs (7ce2c7fd
known-garbled, 001eb5f8 known-clean-decode), then measure corpus-wide.
"""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_app_engine

RUN = 10
GARBLE = "7ce2c7fdfec69d1a735b4feafb75f94430cbb2c24801ed8f2e0a6e8d1627e014"
CLEAN = "001eb5f86e4c833852f428e6200c21a937dd8b9204ae88b5b91b81a5b6d3dc76"
eng = make_app_engine()


def q(c, sql, **kw):
    return c.execute(text(sql), kw)

with eng.connect() as c:
    # ---- totals ----
    tot = q(c, """SELECT count(*) m, count(*) FILTER (WHERE verification_tier='verified') v,
                  count(*) FILTER (WHERE verification_tier='quarantine') qn,
                  count(DISTINCT content_sha256) docs
                  FROM lava_vocab.llm_metrics WHERE run_id=:r""", r=RUN).fetchone()
    M, V, Qn, DOCS = tot
    print(f"=== RUN {RUN} TOTALS ===")
    print(f"  metrics={M}  verified={V} ({V/M:.1%})  quarantine={Qn} ({Qn/M:.1%})  docs={DOCS}")

    # ---- is grounding_diag populated? ----
    dg = q(c, """SELECT count(*) FILTER (WHERE grounding_diag IS NOT NULL) nn, count(*) n
                 FROM lava_vocab.llm_metrics WHERE run_id=:r""", r=RUN).fetchone()
    print(f"  grounding_diag populated: {dg[0]}/{dg[1]}")

    # ---- CALIBRATION on evidence docs ----
    print("\n=== CALIBRATION (ground-truth evidence docs) ===")
    for label, sha in [("GARBLE 7ce2c7fd", GARBLE), ("CLEAN  001eb5f8", CLEAN)]:
        d = q(c, """SELECT pdftotext_coverage, pdftotext_reverse, text_source,
                    total_text_chars, page_count, figure_count, table_count, section_count
                    FROM lava_parse.documents WHERE content_sha256=:s""", s=sha).fetchone()
        m = q(c, """SELECT count(*) FILTER (WHERE verification_tier='verified'),
                    count(*) FILTER (WHERE verification_tier='quarantine'), count(*)
                    FROM lava_vocab.llm_metrics WHERE run_id=:r AND content_sha256=:s""",
              r=RUN, s=sha).fetchone()
        if d:
            cov, rev, ts, chars, pg, fig, tbl, sec = d
            dens = (chars / pg) if pg else 0
            print(f"  {label}: cov={cov} rev={rev} src={ts} chars={chars} pages={pg} "
                  f"chars/pg={dens:.0f} figs={fig} tables={tbl} secs={sec} "
                  f"| verified={m[0]} quarantine={m[1]}")

    # ---- pdftotext_coverage distribution over run-10 docs ----
    print("\n=== pdftotext_coverage distribution (run-10 docs) ===")
    rows = q(c, """
        WITH d AS (
          SELECT DISTINCT m.content_sha256 FROM lava_vocab.llm_metrics m WHERE m.run_id=:r
        )
        SELECT
          count(*) AS docs,
          count(doc.pdftotext_coverage) AS has_cov,
          count(*) FILTER (WHERE doc.pdftotext_coverage < 0.3) AS lt30,
          count(*) FILTER (WHERE doc.pdftotext_coverage >= 0.3 AND doc.pdftotext_coverage < 0.5) AS p30_50,
          count(*) FILTER (WHERE doc.pdftotext_coverage >= 0.5 AND doc.pdftotext_coverage < 0.7) AS p50_70,
          count(*) FILTER (WHERE doc.pdftotext_coverage >= 0.7 AND doc.pdftotext_coverage < 0.9) AS p70_90,
          count(*) FILTER (WHERE doc.pdftotext_coverage >= 0.9) AS ge90,
          count(*) FILTER (WHERE doc.text_source='scanned') AS scanned,
          count(*) FILTER (WHERE doc.text_source='pdftotext_failed') AS pdffailed
        FROM d JOIN lava_parse.documents doc ON doc.content_sha256 = d.content_sha256
    """, r=RUN).fetchone()
    cols = ["docs", "has_cov", "cov<0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", ">=0.9", "scanned", "pdftotext_failed"]
    for name, val in zip(cols, rows):
        print(f"  {name:18} {val}")
