"""Shared: render a doc's prov as tagged input + id->item map. No side effects."""
from sqlalchemy import text


def render_tagged(conn, sha):
    idmap, items, sectext_parts = {}, [], []
    t, c = [0], [0]
    secs = conn.execute(text(
        "SELECT body_text, source_locations FROM lava_parse.sections "
        "WHERE content_sha256=:s ORDER BY section_index"), {"s": sha}).fetchall()
    for body, sl in secs:
        if body:
            sectext_parts.append(body)
        if sl:
            for it in sl:
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                loc = (it.get("locations") or [{}])[0]
                tid = f"t{t[0]}"; t[0] += 1
                idmap[tid] = {"kind": "text", "idx": int(tid[1:]), "text": txt,
                              "page": loc.get("page_no"), "bbox": loc.get("bbox")}
                items.append((loc.get("page_no") or 0, f"{txt} ⟨{tid}⟩"))
        elif body:
            tid = f"t{t[0]}"; t[0] += 1
            idmap[tid] = {"kind": "text", "idx": int(tid[1:]), "text": body.strip(),
                          "page": None, "bbox": None}
            items.append((0, f"{body.strip()} ⟨{tid}⟩"))
    tabs = conn.execute(text(
        "SELECT page_number, cell_locations FROM lava_parse.tables "
        "WHERE content_sha256=:s ORDER BY table_index"), {"s": sha}).fetchall()
    for ti, (page, cl) in enumerate(tabs):
        if not cl:
            continue
        rows = {}
        for cell in cl:
            rows.setdefault(cell.get("row"), []).append(cell)
        lines = [f"[table on page {page}]"]
        for r in sorted(rows, key=lambda x: (x is None, x)):
            rowtext = " ".join((cc.get("text") or "") for cc in rows[r])
            parts = []
            for cell in sorted(rows[r], key=lambda cc: (cc.get("col") is None, cc.get("col"))):
                cid = f"c{c[0]}"; c[0] += 1
                idmap[cid] = {"kind": "cell", "idx": int(cid[1:]), "text": (cell.get("text") or "").strip(),
                              "row_text": rowtext, "page": page, "row": cell.get("row"),
                              "col": cell.get("col"), "table": ti, "bbox": cell.get("bbox")}
                parts.append(f"{(cell.get('text') or '').strip()} ⟨{cid}⟩")
            lines.append(f"row {r}: " + " | ".join(parts))
        items.append((page or 0, "\n".join(lines)))
    items.sort(key=lambda x: x[0])
    return "\n".join(sectext_parts)[:60_000], "\n".join(b for _, b in items)[:60_000], idmap


def norm(x):
    return x.strip().strip("⟨⟩").strip() if isinstance(x, str) else None
