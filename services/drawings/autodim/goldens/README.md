# `autodim/goldens/` — dimension-engine golden files

Golden rule 10: **golden files gate merges.** These are the auto-dimensioning engine's,
and every one of them was produced by running the engine in this repo — none is
hand-written, and none is a placeholder.

| File | What it pins |
|---|---|
| `autodim-<fixture>.json` | the full result: chains (with every segment), placed labels (box, strategy, leader), the `A-DIM` primitive stream, placement stats, skipped walls, suppressed chains, sheet notes |
| `autodim-<fixture>.svg` | the same run as ink — openable in a browser, so a reviewer can *see* a layout regression instead of reading integers |

Fixtures come from `services/drawings/autodim/testing.py`:

| Fixture | Why it is in the corpus |
|---|---|
| `demo-3bhk-ground` | the seeded demo project's ground floor: 7 rooms, 14 openings, all four facades dimensioned, 5 duplicate inner chains suppressed |
| `l-shaped` | an L footprint: facade occlusion (a recessed leg's window must land on the north chain), a level-2 jog breakpoint, one non-rectangular room |
| `two-room` | `garh_model`'s own two-room fixture, so a dimension bug that is really a geometry bug shows up against numbers pinned elsewhere |
| `json-diagonal` | wire JSON instead of dataclasses, plus a diagonal wall that must be **skipped** and reported, never approximated |

## Regenerating

```bash
python3 services/drawings/tests/test_autodim.py --regen   # rewrite
python3 services/drawings/tests/test_autodim.py           # verify + print the report
```

Regenerate **in the same commit** as the change that moved the numbers, and say in the
commit message why they moved. A golden diff is the engine telling you a drawing changed;
if you cannot explain the diff in one sentence, it is a bug, not a new golden.

## Tolerance is zero

Comparison is byte-for-byte. That is affordable because the engine is integer
millimetres end to end and has no timestamps, ids or floats in its output — see §16
("plan JSON goldens with tolerance 0 (integer mm!)"). If a golden ever starts flapping,
the fix is to remove the source of nondeterminism, never to add a tolerance.

## What is *not* here

**DXF goldens.** The DXF writer (`services/drawings/dxf.py`) needs `ezdxf`, which is
pinned but not installed on the machine this engine was authored on, so a DXF golden
could not be generated — and a golden nobody generated is worse than no golden. They land
with the DXF export work, from the same fixtures, and CI's `ezdxf.audit()` check is what
proves them.

**Sheet goldens.** A full municipal sheet (frame, title block, walls, hatches, room
labels, schedules) is the sheet renderer's output and belongs in `fixtures/sheets/`.
These files cover the `A-DIM` layer only — which is exactly the layer this package owns.
