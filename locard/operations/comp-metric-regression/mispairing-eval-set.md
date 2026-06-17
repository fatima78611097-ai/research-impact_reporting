# Mispairing eval set — confirmed false-publishes + ENH-003 recall/precision

Vision-verified ground-truth set of **mispairings** (number paired to the wrong label on a
designed page). These are confirmed **false-publishes** — the gate published them because they
pass every text check (the value and the label are both on the page); the error is purely
spatial. This set measures the **geometry spatial-pairing detector (ENH-003)**: does it *catch*
each mispairing (recall) and *recover* the right label (fix), and how often does it *false-flag*
a correct metric (precision). Grows as more are found by eye.

## Running tally
- **Confirmed mispairings (false-publishes): 6** — across 3 designed docs
- **ENH-003 recall:** caught **6 / 6**; corrected **6 / 6**
- **ENH-003 false-flags (precision):** 1 so far (`5b98b66f:7`, a side-column nearest-centroid miss — fixable by the "label directly below" rule)

## Confirmed mispairings

| metric | org / page | value | model labeled it | truth (vision) | geo caught | geo fixed |
|---|---|---|---|---|---|---|
| `5b98b66f:4` | Capstone p2 | 967 | Head Start children | **housing counseling** | ✓ | ✓ |
| `5b98b66f:5` | Capstone p2 | 353 | Entrepreneurs supported | **Head Start** | ✓ | ✓ |
| `5b98b66f:6` | Capstone p2 | 369 | Housing counseling clients | **financial literacy** | ✓ | ✓ |
| `5b98b66f:9` | Capstone p2 | 2,971 | Financial program participants | **heating assistance** | ✓ | ✓ |
| `68e0e142:1` | Narrow Gate p9 | 95 | Identity confidence rate | **purpose in life** (identity = 100%) | ✓ | ✓ |
| `2cf7c1e8:4` | Forward Stride p1 | 283 | Clients via partnerships | **Equine Assisted Learning** (partnerships = 155) | ✓ | ✓ |

## Geometry false-flags (model was right, geometry disagreed)
| metric | org / page | value | model (correct) | geometry (wrong) | why |
|---|---|---|---|---|---|
| `5b98b66f:7` | Capstone p2 | 32 | CKA graduates | housing counseling | raw nearest-centroid grabbed a side-column label; fix = "label directly below, in-column" |

## Notes
- Both docs are **designed / infographic** pages — the only place this class lives.
- The gate (and any text/number analysis) is **structurally blind** to all of these.
- `68e0e142:1` also shows a *second* model error geometry surfaces for free: the **100% identity** stat was dropped entirely (orphaned number with no metric).
- Pairs with [[enhancements]] ENH-003. NEXT for ENH-003 before pipeline: tighten the pairing rule to clear the `:7` false-flag, then keep growing this set to get a real recall/precision number on a larger sample.
