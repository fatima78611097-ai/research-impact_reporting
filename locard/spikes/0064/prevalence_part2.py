"""0064 prevalence part 2: attribute the 12,329 quarantines to garble vs not,
bucket by grounding_diag, and size the infographic cohort. Pure DB."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_app_engine

RUN = 10
eng = make_app_engine()
with eng.connect() as c:
    def q(sql, **kw): return c.execute(text(sql), kw)

    # ---- diag JSON keys present ----
    print("=== sample grounding_diag keys ===")
    r = q("SELECT grounding_diag FROM lava_vocab.llm_metrics WHERE run_id=:r AND grounding_diag IS NOT NULL LIMIT 1", r=RUN).fetchone()
    print("  ", r[0])

    # ---- QUARANTINE attribution by DOC pdftotext_coverage band ----
    print("\n=== 12,329 quarantines: attributed by doc pdftotext_coverage (garble fingerprint) ===")
    rows = q("""
        SELECT
          CASE
            WHEN d.text_source='pdftotext_failed' THEN '0_pdftotext_failed'
            WHEN d.pdftotext_coverage IS NULL THEN '1_no_coverage'
            WHEN d.pdftotext_coverage < 0.3 THEN '2_cov<0.3 (severe garble)'
            WHEN d.pdftotext_coverage < 0.5 THEN '3_cov0.3-0.5 (garble)'
            WHEN d.pdftotext_coverage < 0.7 THEN '4_cov0.5-0.7 (partial)'
            WHEN d.pdftotext_coverage < 0.9 THEN '5_cov0.7-0.9 (clean-ish)'
            ELSE '6_cov>=0.9 (clean decode)'
          END AS band,
          count(*) AS quarantined,
          count(DISTINCT m.content_sha256) AS docs
        FROM lava_vocab.llm_metrics m
        JOIN lava_parse.documents d ON d.content_sha256 = m.content_sha256
        WHERE m.run_id=:r AND m.verification_tier='quarantine'
        GROUP BY band ORDER BY band
    """, r=RUN).fetchall()
    tot_q = sum(r[1] for r in rows)
    for band, qn, docs in rows:
        print(f"  {band:30} quarantined={qn:6} ({qn/tot_q:5.1%})  docs={docs}")
    print(f"  TOTAL quarantines attributed: {tot_q}")

    # ---- WHY quarantined: grounding_diag buckets (all quarantines) ----
    print("\n=== 12,329 quarantines: bucketed by grounding_diag (WHY they failed) ===")
    rows = q("""
        SELECT
          CASE
            WHEN (grounding_diag->>'source_chars')::float = 0 THEN 'A_missing_source (no text)'
            WHEN (grounding_diag->>'word_coverage')::float < 0.5 THEN 'B_words_absent (garble/fabrication)'
            WHEN (grounding_diag->>'table_coverage')::float >= 0.8 THEN 'C_table_only'
            WHEN (grounding_diag->>'word_coverage')::float >= 0.8
                 AND (grounding_diag->>'longest_run')::float < 0.5 THEN 'D_fragmented (words present, not contiguous)'
            WHEN (grounding_diag->>'word_coverage')::float >= 0.8 THEN 'E_words_present_runs_ok (near-miss/paraphrase)'
            ELSE 'F_partial (cov 0.5-0.8)'
          END AS bucket,
          count(*) n
        FROM lava_vocab.llm_metrics
        WHERE run_id=:r AND verification_tier='quarantine' AND grounding_diag IS NOT NULL
        GROUP BY bucket ORDER BY bucket
    """, r=RUN).fetchall()
    tq = sum(r[1] for r in rows)
    for bucket, n in rows:
        print(f"  {bucket:48} {n:6} ({n/tq:5.1%})")

    # ---- INFOGRAPHIC cohort: high figure density / low text density ----
    print("\n=== INFOGRAPHIC cohort (run-10 docs by figure density & text density) ===")
    rows = q("""
        WITH dd AS (
          SELECT DISTINCT m.content_sha256 FROM lava_vocab.llm_metrics m WHERE m.run_id=:r
        ),
        docmetrics AS (
          SELECT content_sha256,
                 count(*) m, count(*) FILTER (WHERE verification_tier='verified') v,
                 count(*) FILTER (WHERE verification_tier='quarantine') qn
          FROM lava_vocab.llm_metrics WHERE run_id=:r GROUP BY content_sha256
        )
        SELECT
          CASE
            WHEN d.figure_count >= 10 AND d.page_count>0 AND d.total_text_chars::float/d.page_count < 1200 THEN 'heavy_infographic (>=10 figs, <1200 ch/pg)'
            WHEN d.figure_count >= 5 THEN 'moderate (>=5 figs)'
            WHEN d.figure_count >= 1 THEN 'some_figures (1-4)'
            ELSE 'text_only (0 figs)'
          END AS cohort,
          count(*) docs,
          sum(dm.m) metrics,
          sum(dm.v) verified,
          sum(dm.qn) quarantine
        FROM dd JOIN lava_parse.documents d ON d.content_sha256=dd.content_sha256
        JOIN docmetrics dm ON dm.content_sha256=dd.content_sha256
        GROUP BY cohort ORDER BY cohort
    """, r=RUN).fetchall()
    td = sum(r[1] for r in rows); tm = sum(r[2] for r in rows)
    for cohort, docs, metrics, v, qn in rows:
        print(f"  {cohort:42} docs={docs:5} ({docs/td:4.0%})  metrics={metrics:6} ({metrics/tm:4.0%})  verified={v} quarantine={qn}")
