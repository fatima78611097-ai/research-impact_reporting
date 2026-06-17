# Grounding-repair eval set — recoverable false-quarantines

Source-verified **false-quarantines**: the value is real and in the document, but the
grounding cited the wrong marker, so the gate (correctly) held a correct metric. These are
the candidates a cheap **grounding-repair** rule would recover. The labeled set that would
measure such a rule's recall/precision. **Not building yet — gathering data** (per operator).

## Sub-pattern: off-by-one-neighbor (the cheapest to fix)
The value sits in the item **immediately adjacent** (±1 in reading order) to the cited marker.
Repair rule (candidate, not built): *if the value isn't at the cited marker, check the ±1
neighbors; if it's there with the subject, re-anchor and publish.* Local + unambiguous —
beats a whole-doc value search, which coincidentally matches small numbers (list bullets, etc.).

### Tally: 2
| metric | org / page | value | subject | cited marker | real marker (neighbor) |
|---|---|---|---|---|---|
| `015893d4:2` | MFAN p2 | 14 | Advisory board members | t1 (intro) | **t2** (next item) |
| `015893d4:3` | MFAN p2 | 4 | Advisory board meetings | t1 (intro) | **t2** (next item) |

**Note:** both metrics live in the *same* sentence (t2: *"the MFAN advisory board, consisting of
14 thought leaders… met four times"*); the grounding pinned **both** to t1, the intro above — so
one neighbor re-anchor recovers both at once.

## Marking convention (in the viewer)
`verdict = false-quarantine` · `cause = grounding` · `mode = adjacent-marker` · `actual_location = <real page>`

Pairs with [[enhancements]] — a candidate grounding-repair rule, distinct from the geometry fix
(ENH-003, which handles spatial infographics; this handles prose wrong-markers). Not yet an ENH
per "gather data first."
