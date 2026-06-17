"""Coordinate-based layout re-grouping (the small-gun fix for infographic mispairing).

Reads per-element bboxes from lava_parse.sections.source_locations (already stored), normalizes
coordinate origin, and re-pairs each number with its label in the natural grouping (directly below in
its column, OR immediately to the right in its row). Safe by construction: single-column/prose -> the
number already carries its own label (no bare numbers) so nothing changes; clean grid -> re-pairs;
dense/ambiguous -> ABSTAINS (no confident label) rather than guessing.
"""
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from sqlalchemy import text


def normalize(raw):
    """raw = [(text, bbox, origin)] -> [(text, xc, ytop, ybot, l, r)], y increasing DOWNWARD from top."""
    if not raw:
        return []
    H = max(max(b["t"], b["b"]) for _, b, _ in raw) + 1
    out = []
    for t, b, origin in raw:
        if (origin or "BOTTOMLEFT") == "BOTTOMLEFT":
            ytop, ybot = H - b["t"], H - b["b"]
        else:
            ytop, ybot = b["t"], b["b"]
        out.append((t, (b["l"] + b["r"]) / 2, min(ytop, ybot), max(ytop, ybot), b["l"], b["r"]))
    return out


# Generic participation/measure words: NEVER sufficient for label agreement on their own —
# "180 Co-Parenting Seminar ATTENDEES" must not 'agree' with "individuals ATTENDED Divorce
# Support Groups" (b42e6f95:9 — the stem 'atten' swallowed a real mispair flag).
GENERIC_STEMS = {"atten", "serve", "servi", "recei", "benef", "indiv", "peopl", "famil",
                 "clien", "membe", "perso", "parti", "stude", "youth", "adult", "child",
                 "throu", "progr", "total"}


def labels_agree(label_a, label_b):
    """Stem overlap on DISTINCTIVE words only."""
    sa = {w[:5] for w in re.findall(r"[a-z]{4,}", (label_a or "").lower())} - GENERIC_STEMS
    sb = {w[:5] for w in re.findall(r"[a-z]{4,}", (label_b or "").lower())} - GENERIC_STEMS
    return bool(sa & sb)


def isnum(t):
    return bool(re.fullmatch(r"\$?[\d][\d.,]*\+?%?", t))


def islabel(t):
    return len(re.findall(r"[A-Za-z]{3,}", t)) >= 2 and len(t) > 10


def _gap(n, l):
    """If label l is in number n's natural grouping (RIGHT same-row, BELOW same-col, or ABOVE same-col
    header), return the gap distance; else None. n,l = (text,xc,ytop,ybot,l,r)."""
    nt, nxc, nyt, nyb, nl, nr = n
    lt, lxc, lyt, lyb, ll, lr = l
    row_overlap = lyt <= nyb + 3 and lyb >= nyt - 3
    col_overlap = (nl - 8) <= lxc <= (nr + 8) or (ll - 8) <= nxc <= (lr + 8)
    if ll >= nr - 8 and row_overlap:           # RIGHT, same row
        return ll - nr
    if lyt >= nyb - 3 and col_overlap:         # BELOW, same column
        return lyt - nyb
    if lyb <= nyt + 3 and col_overlap and (nyt - lyb) < 30 and len(lt) <= 40:
        # ABOVE, same column: header-over-number grids only — headers are SHORT and CLOSE.
        # (Long prose paragraphs above a stat are never its label.)
        return (nyt - lyb) + 4                 # slight penalty so below/right win ties
    return None


_QUALIFIER = re.compile(r"\b(of which|of whom|of these|of them|including)\b", re.I)
_SENTENCE = re.compile(r"\b(was|were|is|are|has|have|had|will|would|can|could|may|might|"
                       r"say|says|said|feel|feels|helps|brings|makes|gives|provides|offers|serves)\b", re.I)


def _bad_label(lt):
    """Candidates that are never a stat caption: contain a digit (the caption carries its own
    quantity — '...to 52 women', '271 of which were children', '1:1 IN DEPTH SUPPORT'), a subordinate-
    qualifier phrase, or a finite verb (a SENTENCE — 'Limits were changed so...', 'of Achievers say
    they feel...' — stat captions are noun phrases). Abstaining is the safe direction."""
    if _QUALIFIER.search(lt) or _SENTENCE.search(lt):
        return True
    return bool(re.search(r"\d", lt))


def pair_from_elements(els):
    """Mutual-nearest pairing: a number keeps a label only if that label's nearest number is also this
    number, the match is unambiguous (clear gap to runner-up), and within range. Else ABSTAIN."""
    nums = [e for e in els if isnum(e[0])]
    labs = [e for e in els if islabel(e[0])]

    def cands_for_num(n):                       # [(gap, label_idx)]
        c = [(g, j) for j, l in enumerate(labs)
             if not _bad_label(l[0]) and (g := _gap(n, l)) is not None and g < 90]
        return sorted(c)

    def nearest_num_for_label(j):               # index of the number whose grouping best claims label j
        c = [(g, i) for i, n in enumerate(nums) if (g := _gap(n, labs[j])) is not None and g < 90]
        return min(c)[1] if c else None

    out = {}
    for i, n in enumerate(nums):
        c = cands_for_num(n)
        if not c:
            out[n[0]] = {"label": None, "confidence": "abstain"}
            continue
        best_gap, j = c[0]
        sep = c[1][0] - best_gap if len(c) > 1 else 99
        mutual = nearest_num_for_label(j) == i          # label j's nearest number is THIS number
        lbl = labs[j][0]
        try:
            numval = float(re.sub(r"[^\d.]", "", n[0]).replace(".", "", re.sub(r"[^\d.]", "", n[0]).count(".") - 1) or 0)
        except ValueError:
            numval = 0  # phone-number-like tokens etc.
        # don't engage on financial-statement regions (handled by the table structure, not stat grids):
        financial = bool(re.search(r"\$\s?\d", lbl)) or (numval > 1_000_000 and len(re.findall(r"[A-Za-z]{3,}", lbl)) <= 3)
        if mutual and sep > 8 and not financial:
            out[n[0]] = {"label": lbl, "confidence": "high" if sep > 14 else "low"}
        else:
            out[n[0]] = {"label": None, "confidence": "abstain"}
    return out


def raw_locations(conn, full):
    """All (text, bbox, origin) for a doc, grouped by page."""
    rows = conn.execute(text("SELECT source_locations FROM lava_parse.sections "
                             "WHERE content_sha256=:s AND source_locations IS NOT NULL"), {"s": full}).fetchall()
    by_page = {}
    for (sl,) in rows:
        for item in (sl or []):
            t = (item.get("text") or "").strip()
            for loc in item.get("locations") or []:
                if loc.get("bbox") and loc.get("page_no") is not None:
                    by_page.setdefault(loc["page_no"], []).append((t, loc["bbox"], loc["bbox"].get("coord_origin")))
    return by_page


def pairings(conn, full, page, debug=False):
    by_page = raw_locations(conn, full)
    els = normalize(by_page.get(page, []))
    if debug:
        for t, xc, yt, yb, l, r in sorted(els, key=lambda e: (round(e[2] / 20), e[1])):
            kind = "NUM" if isnum(t) else ("LBL" if islabel(t) else "   ")
            print(f"    [{kind}] y={yt:6.0f} x={xc:6.0f}  {t[:48]!r}")
    return pair_from_elements(els)


if __name__ == "__main__":
    sha8 = sys.argv[1] if len(sys.argv) > 1 else "5b98b66f"
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    eng = make_app_engine()
    with eng.connect() as conn:
        full = conn.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": sha8 + "%"}).scalar()
        p = pairings(conn, full, page, debug=True)
    print("\n  re-paired:")
    for n, d in p.items():
        print(f"    {n:12} -> [{d['confidence']:7}] {d['label']}")
