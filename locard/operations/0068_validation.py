"""0068 Phase-5 validation harness — measure AC1/AC2/AC3 over the eligible set.

Runs the marker-citing extraction (render -> loc1 -> resolve) per eligible doc and
reports:
  AC1  resolvable-value_ref rate  (non-null value_ref present in idmap / selected
       metrics on eligible docs) -> this run's number is LOC1_BASELINE.
  AC2  table-cell coverage        (metrics whose value is in a table that resolve to
       a ⟨c#⟩ with row+col+bbox).
  AC3  round-trip                 (stored coords == idmap[value_ref] for a sample).

This SPENDS DeepSeek compute (one call/doc). It is operator-gated: it does nothing
without `--live` (no silent stub that fakes acceptance numbers).

Usage:
    python3 locard/operations/0068_validation.py --live --run-tag 0068-baseline [--limit N] [--write]

Without --write it is a dry measurement (no DB rows written); with --write it persists
under the given run_tag (idempotent per (run_id, content_sha256)).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import sqlalchemy as sa

# repo root = three levels up from locard/operations/ (works in a worktree too)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from lavandula.common.db import make_app_engine          # noqa: E402
from lavandula.common.secrets import get_secret           # noqa: E402
from lavandula.nlp.marker_render import render_tagged, SkipDocument  # noqa: E402
from lavandula.nlp.marker_extract import (                # noqa: E402
    LOC1_PROMPT, compute_eligibility, normalize_loc1, deepseek_chat, run_extraction,
    trusted_parse_versions,
)
from lavandula.nlp.marker_resolve import resolve_metric   # noqa: E402


def eligible_shas(engine, limit=None):
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT DISTINCT content_sha256 FROM lava_parse.tables "
            "WHERE cell_locations IS NOT NULL ORDER BY content_sha256")).fetchall()
    shas = [r[0] for r in rows]
    return shas[:limit] if limit else shas


def measure(engine, shas, chat_fn):
    sel_total = sel_resolvable = 0
    tablevalue_total = tablevalue_celled = 0
    roundtrip_ok = roundtrip_n = 0
    per_doc = []
    trusted = trusted_parse_versions(engine)
    for sha in shas:
        with engine.connect() as conn:
            secs = [(b, sl) for b, sl in conn.execute(sa.text(
                "SELECT body_text, source_locations FROM lava_parse.sections "
                "WHERE content_sha256=:s ORDER BY section_index"), {"s": sha}).fetchall()]
            tabs = [(p, cl) for p, cl in conn.execute(sa.text(
                "SELECT page_number, cell_locations FROM lava_parse.tables "
                "WHERE content_sha256=:s ORDER BY table_index"), {"s": sha}).fetchall()]
            pv = conn.execute(sa.text(
                "SELECT parse_version FROM lava_parse.documents WHERE content_sha256=:s"),
                {"s": sha}).scalar()
        ok, reason, _ = compute_eligibility(secs, tabs)
        if not ok or pv not in trusted:
            per_doc.append({"sha": sha[:12], "skipped": reason if not ok else "parse_unverified"})
            continue
        try:
            with engine.connect() as conn:
                render = render_tagged(conn, sha)
        except SkipDocument as sd:
            per_doc.append({"sha": sha[:12], "skipped": sd.reason})
            continue
        metrics, _ = normalize_loc1(chat_fn(LOC1_PROMPT, render.tagged_text))
        d_sel = d_res = 0
        for m in metrics:
            r = resolve_metric(m, render.idmap)
            sel_total += 1; d_sel += 1
            if r["value_ref"] is not None:
                sel_resolvable += 1; d_res += 1
                e = render.idmap[r["value_ref"]]
                if e["kind"] == "cell":
                    tablevalue_total += 1
                    if e.get("row") is not None and e.get("col") is not None and e.get("bbox"):
                        tablevalue_celled += 1
                # round-trip
                roundtrip_n += 1
                if (r["value_page"] == e["page"] and r["value_bbox"] == e["bbox"]
                        and r["value_row"] == e["row"] and r["value_col"] == e["col"]):
                    roundtrip_ok += 1
        per_doc.append({"sha": sha[:12], "selected": d_sel, "resolvable": d_res})
    return {
        "docs": len(shas),
        "AC1_resolvable_value_ref_rate": round(sel_resolvable / sel_total, 4) if sel_total else None,
        "selected_metrics": sel_total,
        "resolvable_metrics": sel_resolvable,
        "AC2_table_cell_coverage": round(tablevalue_celled / tablevalue_total, 4) if tablevalue_total else None,
        "AC3_round_trip": round(roundtrip_ok / roundtrip_n, 4) if roundtrip_n else None,
        "round_trip_sample": roundtrip_n,
        "per_doc": per_doc,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually call DeepSeek (spends compute)")
    ap.add_argument("--run-tag", default="0068-baseline")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--write", action="store_true", help="persist rows under run_tag")
    args = ap.parse_args()
    if not args.live:
        print("Refusing to run without --live (this spends DeepSeek compute). See header.")
        sys.exit(2)
    engine = make_app_engine()
    chat_fn = deepseek_chat(get_secret("lavandula/deepseek/api_key"))
    shas = eligible_shas(engine, args.limit)
    if args.write:
        stats = run_extraction(engine, args.run_tag, shas, chat_fn=chat_fn, write=True)
        print(json.dumps(stats, indent=2))
    else:
        print(json.dumps(measure(engine, shas, chat_fn), indent=2))


if __name__ == "__main__":
    main()
