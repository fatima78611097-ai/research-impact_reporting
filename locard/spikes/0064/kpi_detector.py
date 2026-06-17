"""0064 — KPI/infographic page detector v2 (iterated on empirical validation).

v1 (committed) implemented the advisor spec faithfully; validation exposed 3 gaps:
  (1) score>=8 fired on the dense mosaic where regrouping FAILS,
  (2) table-grid rejector misfired on KPI card grids,
  (3) glyph-soup garble masqueraded as KPI on every page.

v2 fixes:
  - APPLY is gated on the WHITELIST (clean multi-cluster separation), not score>=8.
    "Cards cleanly separate" is both the detection signal AND the precondition for
    the fix to work, so this routes APPLY only where the fix succeeds.
  - Clustering is finer + TIGHTNESS-aware: a "card column" must be x-tight, which
    distinguishes clean grids (canonical target) from dense mosaics.
  - detect_table_grid is an occupancy-fill discriminator: a real data table fills
    >=3 columns across >=4 rows; KPI cards do not.
  - Garble guard: pages that are glyph-soup (mostly 1-char tokens) never APPLY.
"""
import re

KPI_WORDS = ["impressions", "delivered", "opens", "clicks", "page views",
             "volunteers"]
_NUM = re.compile(
    r"(\$?\d{1,3}(?:,\d{3})+\+?)"
    r"|(\$?\d+(?:\.\d+)?\s?[MKB]\b)"
    r"|(\b\d+%?\+?)"
)


def looks_like_metric(t):
    toks = t.split()
    if not toks or len(toks) > 8:
        return False
    return bool(_NUM.search(t)) and any(c.isdigit() for c in t)


def _is_short(t):
    return len(t.split()) <= 5


def _is_paragraph(t):
    return len(t.split()) >= 15 or t.count(".") >= 1


def garble_fraction(blocks):
    """Fraction of whitespace tokens that are single characters (glyph-soup)."""
    toks = " ".join(b["text"] for b in blocks).split()
    if not toks:
        return 0.0
    return sum(1 for t in toks if len(t) == 1) / len(toks)


def _cluster_1d(values, gap):
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups, cur = [], [order[0]]
    for k in range(1, len(order)):
        if values[order[k]] - values[order[k - 1]] > gap:
            groups.append(cur); cur = []
        cur.append(order[k])
    groups.append(cur)
    return groups


def cluster_by_x(blocks, page_w):
    if not blocks:
        return []
    xs = [b["xc"] for b in blocks]
    groups = _cluster_1d(xs, 0.06 * page_w)   # finer than v1 (was 0.12)
    return [[blocks[i] for i in g] for g in groups]


def _xspread(cluster):
    xs = [b["xc"] for b in cluster]
    return max(xs) - min(xs)


def card_columns(clusters, page_w):
    """Tight, multi-member columns — the 'clean card column' shape. A dense mosaic
    yields wide-spread clusters that are rejected here."""
    return [c for c in clusters
            if len(c) >= 2 and _xspread(c) < 0.12 * page_w]


def detect_heading_metric_pairs(clusters):
    n_above = n_adj = 0
    for cl in clusters:
        metrics = [b for b in cl if b["_metric"]]
        labels = [b for b in cl if (not b["_metric"]) and _is_short(b["text"])]
        if not metrics or not labels:
            continue
        above = adj = False
        for lab in labels:
            for m in metrics:
                if min(lab["r"], m["r"]) - max(lab["l"], m["l"]) <= 0:
                    continue
                if lab["bot"] <= m["top"]:
                    above = True; adj = True
                elif lab["top"] >= m["bot"]:
                    adj = True
        n_above += 1 if above else 0
        n_adj += 1 if adj else 0
    return n_above, n_adj


def detect_table_grid(blocks, page_w, page_h):
    """Occupancy-fill: a real data table fills >=3 columns across >=4 row-bands.
    KPI card columns are sparse (each card spans only a few rows)."""
    if len(blocks) < 12:
        return False
    row_groups = _cluster_1d([b["top"] for b in blocks], 0.02 * page_h)
    col_groups = _cluster_1d([b["xc"] for b in blocks], 0.06 * page_w)
    rows = [r for r in row_groups if r]
    cols = [c for c in col_groups if len(c) >= 3]
    if len(rows) < 4 or len(cols) < 3:
        return False
    # per-column fill = fraction of row-bands in which this column has a cell
    row_of = {}
    for ri, r in enumerate(rows):
        for i in r:
            row_of[i] = ri
    fills = []
    for c in cols:
        rows_hit = {row_of[i] for i in c if i in row_of}
        fills.append(len(rows_hit) / len(rows))
    dense_cols = [f for f in fills if f >= 0.5]
    return len(dense_cols) >= 3


def classify_page(cells, page_w, page_h):
    blocks = [c for c in cells if c.get("text", "").strip()]
    for b in blocks:
        b["_metric"] = looks_like_metric(b["text"])
    n = max(len(blocks), 1)
    short = [b for b in blocks if _is_short(b["text"])]
    para = [b for b in blocks if _is_paragraph(b["text"])]
    metrics = [b for b in blocks if b["_metric"]]
    sbr = len(short) / n
    pbr = len(para) / n
    gfrac = garble_fraction(blocks)
    garbled = gfrac > 0.40

    clusters = cluster_by_x(short + metrics, page_w)
    cards = card_columns(clusters, page_w)
    n_above, n_adj = detect_heading_metric_pairs(cards)
    table_like = detect_table_grid(blocks, page_w, page_h)
    prose_like = pbr > 0.35
    kpi_kw = any(w in b["text"].lower() for b in blocks for w in KPI_WORDS)

    rc = []
    score = 0
    if sbr > 0.65:
        score += 2; rc.append("low_paragraph_density")
    if pbr < 0.25:
        score += 2; rc.append("very_low_paragraph_ratio")
    if len(metrics) >= 5:
        score += 2; rc.append("high_numeric_density")
    if len(cards) >= 2:
        score += 3; rc.append(f"{len(cards)}_card_columns")
    if n_above >= 1:
        score += 2; rc.append("heading_above_metric_pairs")
    if kpi_kw:
        score += 1; rc.append("kpi_keywords")
    if table_like:
        score -= 3; rc.append("table_grid_detected")
    if prose_like:
        score -= 3; rc.append("prose_dominant")

    # WHITELIST is the APPLY gate: clean card separation == fix will work.
    whitelist = (len(metrics) >= 5 and len(cards) >= 2 and n_adj >= 1
                 and pbr < 0.25 and not table_like and not prose_like
                 and not garbled)

    if garbled:
        lane = "normal(garbled)"; rc.append(f"garble_frac={gfrac:.2f}")
    elif whitelist:
        lane = "APPLY_regrouping"
    elif score >= 5:
        lane = "LOG_only(medium)"
    else:
        lane = "normal_pipeline"

    return {
        "score": score, "lane": lane, "whitelist": whitelist, "garbled": garbled,
        "sbr": round(sbr, 2), "pbr": round(pbr, 2), "n_metrics": len(metrics),
        "n_cards": len(cards), "n_clusters": len([c for c in clusters if len(c) >= 2]),
        "head_above": n_above, "head_adj": n_adj, "table_like": table_like,
        "prose_like": prose_like, "gfrac": round(gfrac, 2), "n_blocks": len(blocks),
        "reason_codes": rc,
    }
