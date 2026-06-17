"""Phase 1 spike — vision-first metric pipeline (THROWAWAY).

Goal: validate the LOGIC of the vision-first flow end-to-end on a small mixed
doc set, before any production wiring or schema. Writes JSON for the viewer; no
DB writes, no new schema. Reads existing lava_parse (Docling) read-only.

Per-document flow (the thing we're proving):
  1. fetch PDF (S3) -> render every page to PNG (pdftoppm @150dpi, page cap)
  2. VISION per page (Flash-Lite): canary (infographic?) + numbers/labels/boxes
  3. DOCLING read (lava_parse): prose sections + tables for this doc
  4. DEEPSEEK slot extraction on the Docling text (production prompt, verbatim snippets)
  5. MERGE/RECONCILE: dedupe; vision wins value conflicts (the infographic guarantee)
  6. VERIFY trigger: re-vision pages that are canary-flagged or where vision<>docling disagree
  7. GATES: is-a-metric + faithfulness grounding (docling-sourced); vision-sourced needs a box
  8. emit published metrics per page -> spike-result.json

Reused as libraries (not reimplemented):
  - vision  : bake-off gemini() REST adapter + CANARY/EXTRACT prompts
  - extract : lavandula.nlp.llm_extract.call_deepseek + _METRICS_PROMPT
  - gate    : lavandula.faithfulness.grounding.check (pure R1/R2, no DB)
  - db      : lavandula.common.db.make_ro_engine (read-only IAM/SSM)
  - s3 cfg  : lavandula.parse.config (bucket + key pattern)

Run:
  python3 spike.py --docset docset.json --limit 100 --page-cap 20
Decisions baked into a run are printed at startup (sample, page cap, models,
thresholds) — confirm before spending compute.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]  # /home/ubuntu/research
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE.parent / "bake-off"))  # gemini() adapter

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

# --- reused production / spike libraries ---
from run_bakeoff import gemini, _key as gemini_key  # noqa: E402
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT  # noqa: E402
from lavandula.faithfulness.grounding import check as ground_check  # noqa: E402
from lavandula.faithfulness.source_provider import TableRow  # noqa: E402
from lavandula.common.db import make_app_engine  # noqa: E402  (read-only use; ro-user lacks lava_parse grants)
from lavandula.parse import config as parse_config  # noqa: E402
from lavandula.common.secrets import get_secret  # noqa: E402

VISION_MODEL = "gemini-2.5-flash-lite"  # locked tier (bake-off)
# ONE call per page: infographic flag + featured numbers (canary+extract fused -> half the calls)
COMBINED_PROMPT = (
    "Look at this page from a nonprofit report. First decide: does it contain an INFOGRAPHIC "
    "(a designed graphic with big stylized number callouts, stat tiles, charts, or icon+number "
    "tiles) as opposed to plain paragraphs or a financial table? Then extract the prominently-"
    "featured metrics: big stat tiles/callouts, key outcome percentages, labeled chart/table "
    "figures — NOT raw axis ticks, page numbers, or years. Transcribe digits exactly as printed; "
    "do not infer or round. For each: value (digits, no commas), label (what it counts), and box "
    "[ymin, xmin, ymax, xmax] normalized 0-1000. Reply ONLY JSON: "
    '{"infographic": true, "numbers": [{"value": <digits>, "label": "<what>", "box": [ymin,xmin,ymax,xmax]}]}'
)
RENDER_DPI = 150
MATCH_TOL = 0.5            # numeric tolerance for vision<->docling value match
DEEPSEEK_TEXT_CAP = 60_000
PAGES_DIR = HERE / "pages"


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------
def num_val(s) -> float | None:
    """Printed-digits -> float. '1,514'->1514, '56'->56, '2.8'->2.8."""
    m = re.search(r"\d[\d,]*\.?\d*", str(s) if s is not None else "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def is_year_or_noise(v: float | None, label: str) -> bool:
    """Cheap is-a-metric reject: bare years, page numbers, empty labels."""
    if v is None:
        return True
    if 1900 <= v <= 2099 and v == int(v) and not label.strip():
        return True  # bare year with no subject
    return False


def deepseek_key() -> str:
    for name in ("lavandula/deepseek/api_key", "deepseek-api-key"):
        try:
            return get_secret(name)
        except Exception:
            continue
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    sys.exit("No DeepSeek key (SSM 'lavandula/deepseek/api_key' or env DEEPSEEK_API_KEY).")


def parse_vision(text_reply: str) -> list[dict]:
    """[{value,label,box}] from the vision model's JSON (loose-tolerant)."""
    try:
        j = json.loads(re.search(r"\{.*\}", text_reply, re.S).group(0))
        out = []
        for n in j.get("numbers", []):
            v = str(n.get("value", "")).strip()
            if not v:
                continue
            box = n.get("box")
            box = [float(x) for x in box] if isinstance(box, list) and len(box) == 4 else None
            out.append({"value": v, "label": str(n.get("label", "")).strip(), "box": box})
        return out
    except Exception:
        return [{"value": m, "label": "", "box": None}
                for m in re.findall(r"\d[\d,]*\.?\d*", text_reply or "")]


# ----------------------------------------------------------------------------
# 1. fetch + render
# ----------------------------------------------------------------------------
def render_pages(sha256: str, sha8: str, page_cap: int, local_pdf: str | None) -> list[Path]:
    """Get the PDF (local override or S3) and render up to page_cap pages -> PNG."""
    PAGES_DIR.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="spike_"))
    pdf_path = tmp / f"{sha8}.pdf"
    if local_pdf and Path(local_pdf).exists():
        pdf_path = Path(local_pdf)
    else:
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        key = f"{parse_config.S3_PREFIX}{sha256}.pdf"
        s3.download_file(parse_config.S3_BUCKET, key, str(pdf_path))
    # render all pages, then cap (pdftoppm auto-paginates -> {base}-N.png)
    base = PAGES_DIR / sha8
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(RENDER_DPI), "-l", str(page_cap), str(pdf_path), str(base)],
        capture_output=True, timeout=180,
    )
    imgs = sorted(PAGES_DIR.glob(f"{sha8}-*.png"), key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1)))
    return imgs[:page_cap]


# ----------------------------------------------------------------------------
# 2. vision per page
# ----------------------------------------------------------------------------
def vision_page(img_path: Path, key: str) -> dict:
    """ONE combined call: infographic flag + featured numbers."""
    txt, _, _ = gemini(VISION_MODEL, COMBINED_PROMPT, str(img_path), key, temperature=0.0)
    info = False
    try:
        info = bool(json.loads(re.search(r"\{.*\}", txt, re.S).group(0)).get("infographic"))
    except Exception:
        pass
    return {"canary": "yes" if info else "no", "numbers": parse_vision(txt)}


# ----------------------------------------------------------------------------
# 3. docling read (read-only)
# ----------------------------------------------------------------------------
def read_docling(engine, sha256: str) -> tuple[str, list[list[TableRow]]]:
    """Return (full_prose_text, tables-as-TableRow-lists) from lava_parse."""
    with engine.connect() as conn:
        secs = conn.execute(text(
            "SELECT heading, body_text FROM lava_parse.sections "
            "WHERE content_sha256 = :s ORDER BY section_index"
        ), {"s": sha256}).fetchall()
        tbls = conn.execute(text(
            "SELECT data_json, markdown FROM lava_parse.tables "
            "WHERE content_sha256 = :s ORDER BY table_index"
        ), {"s": sha256}).fetchall()
    parts = []
    for heading, body in secs:
        if heading:
            parts.append(heading)
        if body:
            parts.append(body)
    prose = "\n".join(parts)
    tables: list[list[TableRow]] = []
    for data_json, markdown in tbls:
        rows: list[TableRow] = []
        data = data_json
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = None
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    cells = [str(v) for v in row.values()]
                elif isinstance(row, list):
                    cells = [str(c) for c in row]
                else:
                    cells = [str(row)]
                rows.append(TableRow(cells=cells))
        if rows:
            tables.append(rows)
    return prose, tables


# ----------------------------------------------------------------------------
# 4. deepseek slot extraction
# ----------------------------------------------------------------------------
def deepseek_slots(prose: str, key: str, client: httpx.Client) -> list[dict]:
    if len(prose) < 100:
        return []
    result, _, _ = call_deepseek(key, prose[:DEEPSEEK_TEXT_CAP], client, system_prompt=_METRICS_PROMPT)
    metrics = result.get("metrics") if isinstance(result, dict) else result
    return metrics if isinstance(metrics, list) else []


# ----------------------------------------------------------------------------
# 5+7. merge / reconcile + gates
# ----------------------------------------------------------------------------
def reconcile(vision_pages: list[dict], slots: list[dict], prose: str,
              tables: list[list[TableRow]]) -> list[dict]:
    """Produce published metrics. vision wins value conflicts; docling adds
    prose-only metrics (grounded); vision-only numbers are the infographic catch."""
    published: list[dict] = []

    # index vision numbers by value across pages
    vis = []  # (val, page_idx, entry)
    for pi, vp in enumerate(vision_pages):
        for n in vp["numbers"]:
            vis.append((num_val(n["value"]), pi, n, vp["canary"]))

    matched_vis = set()
    # docling slots first; try to confirm each against a vision number
    for s in slots:
        sval = num_val(s.get("metric_value")) or num_val(s.get("metric_text"))
        label = (s.get("metric_type") or "").strip()
        snippet = s.get("source_snippet") or ""
        v = ground_check(snippet, prose, tables, value=s.get("metric_value"))
        match = None
        for j, (vv, pi, n, cy) in enumerate(vis):
            if vv is not None and sval is not None and abs(vv - sval) <= max(MATCH_TOL, 0.001 * max(abs(vv), 1)):
                match = (j, pi, n, cy)
                break
        if match:
            j, pi, n, cy = match
            matched_vis.add(j)
            published.append(_pub(n["value"], label or n["label"], "both", pi, n["box"],
                                  "high", v.rule if v.grounded else "none", snippet, verified=True,
                                  infographic=(cy == "yes")))  # both = cross-modal confirmed
        else:
            # docling-only: keep only if grounded in the prose/tables
            published.append(_pub(s.get("metric_value", sval), label, "docling", None, None,
                                  "med" if v.grounded else "low",
                                  v.rule if v.grounded else "none", snippet,
                                  verified=False, infographic=False,
                                  quarantined=not v.grounded))

    # vision-only numbers (no docling slot agreed) — the infographic guarantee path
    for j, (vv, pi, n, cy) in enumerate(vis):
        if j in matched_vis:
            continue
        label = n["label"]
        bad = is_year_or_noise(vv, label) or n["box"] is None
        published.append(_pub(n["value"], label, "vision", pi, n["box"],
                              "vision" if cy == "yes" else "med",
                              "image", "", verified=False,
                              infographic=(cy == "yes"), quarantined=bad,
                              needs_verify=False))  # vision-only stays honestly unverified
    return published


def _pub(value, label, source, page, box, conf, rule, snippet, *, verified,
         infographic, quarantined=False, needs_verify=False) -> dict:
    return {
        "value": value, "label": label, "source": source, "page": page,
        "box": box, "confidence": conf, "grounded_rule": rule,
        "snippet": snippet, "verified": verified, "infographic": infographic,
        "quarantined": quarantined, "needs_verify": needs_verify,
    }


# ----------------------------------------------------------------------------
# 6. targeted vision verification (re-read trigger pages)
# ----------------------------------------------------------------------------
def verify_pages(published: list[dict], imgs: list[Path], key: str) -> None:
    """Re-vision the trigger pages (canary-flagged / disagreements) and confirm
    the published value is still read off the image. Marks verified=True."""
    trigger_pages = {p["page"] for p in published if p.get("needs_verify") and p["page"] is not None}
    reread: dict[int, set] = {}
    for pi in trigger_pages:
        if pi < len(imgs):
            vp = vision_page(imgs[pi], key)  # second independent read
            reread[pi] = {num_val(n["value"]) for n in vp["numbers"]}
    for p in published:
        if p["page"] in reread:
            pv = num_val(p["value"])
            p["verified"] = pv is not None and any(
                abs(pv - x) <= MATCH_TOL for x in reread[p["page"]] if x is not None)
            p["needs_verify"] = False


# ----------------------------------------------------------------------------
# per-document driver
# ----------------------------------------------------------------------------
def run_doc(doc: dict, engine, gkey: str, dkey: str, client: httpx.Client, page_cap: int) -> dict:
    sha256, sha8 = doc["sha256"], doc["sha8"]
    out = {"sha8": sha8, "sha256": sha256, "org": doc.get("org"), "type": doc.get("type"),
           "pages": [], "metrics": [], "error": None}
    try:
        imgs = render_pages(sha256, sha8, page_cap, doc.get("local_pdf"))
        with ThreadPoolExecutor(max_workers=5) as ex:
            vpages = list(ex.map(lambda p: vision_page(p, gkey), imgs))
        out["pages"] = [{"img": f"pages/{p.name}", "canary": vp["canary"],
                         "vision_numbers": vp["numbers"]} for p, vp in zip(imgs, vpages)]
        prose, tables = read_docling(engine, sha256)
        slots = deepseek_slots(prose, dkey, client)
        out["docling_chars"] = len(prose)
        out["docling_slots"] = len(slots)
        # --- TRUNCATION VISIBILITY (the operator's concern: never silently drop) ---
        with engine.connect() as conn:
            total_pages = conn.execute(text(
                "SELECT page_count FROM lava_parse.documents WHERE content_sha256=:s"
            ), {"s": sha256}).scalar()
        out["total_pages"] = total_pages
        out["pages_rendered"] = len(imgs)
        out["page_cap_hit"] = bool(total_pages and total_pages > len(imgs))      # vision missed pages
        out["docling_truncated"] = len(prose) > DEEPSEEK_TEXT_CAP                  # DeepSeek missed text
        out["docling_chars_dropped"] = max(0, len(prose) - DEEPSEEK_TEXT_CAP)
        published = reconcile(vpages, slots, prose, tables)
        out["metrics"] = published
    except Exception as e:  # spike: never let one doc kill the batch
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docset", default=str(HERE / "docset.json"))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--page-cap", type=int, default=20)
    ap.add_argument("--doc-workers", type=int, default=4)  # docs processed concurrently
    ap.add_argument("--out", default=str(HERE / "spike-result.json"))
    args = ap.parse_args()

    docs = json.load(open(args.docset))[:args.limit]
    print("=== Phase 1 spike — decisions baked into this run ===")
    print(f"  docs           : {len(docs)} (from {args.docset})")
    print(f"  page cap/doc   : {args.page_cap}")
    print(f"  vision model   : {VISION_MODEL} @ {RENDER_DPI}dpi  (canary + extract per page)")
    print(f"  deepseek       : deepseek-chat, prose cap {DEEPSEEK_TEXT_CAP} chars")
    print(f"  match tol      : {MATCH_TOL}   merge rule: vision wins value conflicts")
    print(f"  verification   : re-vision canary-flagged pages only")
    print(f"  output         : {args.out} (+ pages/*.png)  — NO DB writes")
    types = {}
    for d in docs:
        types[d.get("type", "?")] = types.get(d.get("type", "?"), 0) + 1
    print(f"  type mix       : {types}")
    print("=====================================================")

    gkey, dkey = gemini_key(), deepseek_key()
    engine = make_app_engine()
    # call_deepseek ignores its api_key arg and relies on the client's auth header
    client = httpx.Client(headers={"Authorization": f"Bearer {dkey}"})
    import threading
    t0 = time.time()
    jsonl_path = args.out.replace(".json", ".jsonl")
    jf = open(jsonl_path, "w")  # one line per doc, flushed -> live progress + kill-safe
    lock = threading.Lock()
    results: dict[int, dict] = {}
    done = [0]

    def work(idx_doc):
        idx, doc = idx_doc
        dt = time.time()
        r = run_doc(doc, engine, gkey, dkey, client, args.page_cap)
        with lock:
            done[0] += 1
            results[idx] = r
            jf.write(json.dumps(r) + "\n"); jf.flush()
            pub = sum(1 for m in r["metrics"] if not m["quarantined"])
            trunc = []
            if r.get("page_cap_hit"):
                trunc.append(f"PAGES-CAPPED({r.get('pages_rendered')}/{r.get('total_pages')})")
            if r.get("docling_truncated"):
                trunc.append(f"TEXT-CAPPED(+{r.get('docling_chars_dropped')}ch)")
            print(f"[{done[0]}/{len(docs)}] {r['sha8']} {r.get('type',''):11} "
                  f"pages={len(r['pages'])} slots={r.get('docling_slots','-')} "
                  f"published={pub} {' '.join(trunc)} {time.time()-dt:.0f}s "
                  f"{'ERR:'+r['error'] if r['error'] else ''}", flush=True)

    with ThreadPoolExecutor(max_workers=args.doc_workers) as ex:
        list(ex.map(work, enumerate(docs)))
    jf.close()
    ordered = [results[i] for i in sorted(results)]
    json.dump(ordered, open(args.out, "w"), indent=1)
    print(f"\ndone in {time.time()-t0:.0f}s -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
