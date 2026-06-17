"""Assemble the ~100-doc mixed set for the Phase 1 spike -> docset.json.

Samples from lava_parse.documents (already parsed) joined to lava_corpus.corpus,
using Docling counts as a ROUGH type proxy for a balanced mix:
  infographic-likely : figure_count high
  table-likely       : table_count > 0, figure_count low
  prose-likely       : tables/figures ~0, real text

The proxy is only for SAMPLING a mix — the vision canary tells the truth per page.
Force-includes operator-named infographic docs by sha8 prefix, attaching the local
reviewbox PDF when present (avoids an S3 fetch).

  python3 build_set.py --infographic 50 --table 25 --prose 25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))
from sqlalchemy import text  # noqa: E402
from lavandula.common.db import make_app_engine  # noqa: E402  (read-only use; ro-user lacks lava_parse grants)

REVIEWBOX = REPO / "locard/spikes/0064/eval_set/vision/reviewbox"
FORCE_INFOGRAPHIC = ["5b98b66f", "1a70e980"]  # operator-named anchors

# Bound page_count + text size so the spike exercises LOGIC fast, not pathological
# 200-page / 170k-char monsters (those are a separate cost/timeout concern, deferred).
_SAMPLE = {
    "infographic": ("d.figure_count BETWEEN 3 AND 40 AND d.page_count <= 25", "d.figure_count DESC"),
    "table":       ("d.table_count >= 2 AND d.figure_count = 0 AND d.page_count <= 25", "d.table_count DESC"),
    "prose":       ("d.table_count = 0 AND d.figure_count = 0 AND d.total_text_chars BETWEEN 4000 AND 55000 AND d.page_count <= 25", "d.total_text_chars DESC"),
}


def resolve_local_pdf(sha8: str) -> str | None:
    p = REVIEWBOX / f"{sha8}.pdf"
    return str(p) if p.exists() else None


def sample(engine, kind: str, n: int, exclude: set[str]) -> list[dict]:
    where, order = _SAMPLE[kind]
    sql = (
        "SELECT d.content_sha256, c.source_org_ein, c.material_type, "
        "       d.figure_count, d.table_count, d.total_text_chars "
        "FROM lava_parse.documents d "
        "JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256 "
        f"WHERE d.error IS NULL AND {where} "
        f"ORDER BY {order} LIMIT :lim"
    )
    rows, out = [], []
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"lim": n * 3}).fetchall()
    for sha, ein, mtype, figs, tbls, chars in rows:
        if sha in exclude:
            continue
        sha8 = sha[:8]
        out.append({"sha256": sha, "sha8": sha8, "org": ein, "type": kind,
                    "local_pdf": resolve_local_pdf(sha8),
                    "_proxy": {"figs": figs, "tables": tbls, "chars": chars}})
        exclude.add(sha)
        if len(out) >= n:
            break
    return out


def resolve_forced(engine, sha8_list: list[str]) -> list[dict]:
    out = []
    with engine.connect() as conn:
        for sha8 in sha8_list:
            row = conn.execute(text(
                "SELECT content_sha256, source_org_ein FROM lava_corpus.corpus "
                "WHERE content_sha256 LIKE :p LIMIT 1"
            ), {"p": sha8 + "%"}).fetchone()
            if row:
                out.append({"sha256": row[0], "sha8": sha8, "org": row[1],
                            "type": "infographic", "local_pdf": resolve_local_pdf(sha8)})
            else:
                print(f"  WARN forced {sha8} not found in corpus", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--infographic", type=int, default=50)
    ap.add_argument("--table", type=int, default=25)
    ap.add_argument("--prose", type=int, default=25)
    ap.add_argument("--out", default=str(HERE / "docset.json"))
    args = ap.parse_args()

    engine = make_app_engine()
    seen: set[str] = set()
    forced = resolve_forced(engine, FORCE_INFOGRAPHIC)
    for d in forced:
        seen.add(d["sha256"])
    docs = forced
    docs += sample(engine, "infographic", max(0, args.infographic - len(forced)), seen)
    docs += sample(engine, "table", args.table, seen)
    docs += sample(engine, "prose", args.prose, seen)

    json.dump(docs, open(args.out, "w"), indent=1)
    mix = {}
    for d in docs:
        mix[d["type"]] = mix.get(d["type"], 0) + 1
    print(f"wrote {len(docs)} docs -> {args.out}  mix={mix}")
    print(f"  (local PDFs available for {sum(1 for d in docs if d.get('local_pdf'))} docs; rest via S3)")


if __name__ == "__main__":
    main()
