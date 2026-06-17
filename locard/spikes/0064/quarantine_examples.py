"""0064 — sample the dominant quarantine class (words-present, not contiguous)
from heavy-infographic docs to characterize WHY (layout-scatter vs paraphrase)."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_app_engine

eng = make_app_engine()
with eng.connect() as c:
    rows = c.execute(text("""
      WITH heavy AS (
        SELECT content_sha256 FROM lava_parse.documents
        WHERE figure_count>=10 AND page_count>0 AND total_text_chars::float/page_count<1200
      )
      SELECT m.content_sha256, m.metric_text, m.source_snippet,
             (m.grounding_diag->>'word_coverage') wc,
             (m.grounding_diag->>'longest_run') lr,
             (m.grounding_diag->>'source_chars') sc
      FROM lava_vocab.llm_metrics m JOIN heavy h ON h.content_sha256=m.content_sha256
      WHERE m.run_id=10 AND m.verification_tier='quarantine'
        AND (m.grounding_diag->>'word_coverage')::float >= 0.8
        AND (m.grounding_diag->>'longest_run')::float >= 0.5
      ORDER BY md5(m.id::text||'ex') LIMIT 14
    """)).fetchall()
    for sha, mt, snip, wc, lr, sc in rows:
        print(f"\n[{sha[:8]}] wc={wc} lr={lr} src_chars={sc}")
        print(f"  metric_text: {mt!r}")
        print(f"  source_snip: {snip!r}")

    # longest_run distribution within bucket E (words-present, run>=0.5)
    print("\n=== longest_run distribution within 'words-present' quarantines ===")
    for r in c.execute(text("""
      SELECT width_bucket((grounding_diag->>'longest_run')::float, 0.5, 1.0, 5) b, count(*)
      FROM lava_vocab.llm_metrics
      WHERE run_id=10 AND verification_tier='quarantine'
        AND (grounding_diag->>'word_coverage')::float>=0.8
        AND (grounding_diag->>'longest_run')::float>=0.5
      GROUP BY b ORDER BY b
    """)):
        lo = 0.5 + (r[0]-1)*0.1 if r[0] and r[0]>=1 else None
        print(f"  longest_run bucket {r[0]} (~{lo}): {r[1]}")
