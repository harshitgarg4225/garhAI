#!/usr/bin/env python3
"""Generate ``fixtures/sheets/inputs/*.json`` — the op logs the sheet goldens render.

Run::

    python3 fixtures/sheets/_tools/generate_inputs.py          # write the inputs
    python3 fixtures/sheets/_tools/generate_inputs.py --check   # verify they are current

Same ``_tools/`` + ``--check`` convention as ``fixtures/model/_tools/`` and
``fixtures/rules/_tools/`` — a fixture set nobody can regenerate becomes a fixture set
nobody dares change.

WHY OP LOGS AND NOT GEOMETRY
----------------------------
``fixtures/plans/README.md`` argues this at length and it applies verbatim here: a
geometry snapshot pins the accident of construction order, and it cannot be replayed. So
these files are op logs. Every room, room id, clear area, slab and level in a golden sheet
is **derived by folding these ops through the real ``garh_model`` engine** — none of it is
typed in by hand, and none of it can quietly disagree with what the product would produce
from the same ops.

The one thing authored here is the *design*: where the walls go. That is unavoidable
until the CP-SAT solver exists (Phase 3) and ``fixtures/plans/`` fills up. It is also
honest: these are hand-drawn plans, labelled as such, not pretend solver output. The
harness switches to ``fixtures/plans/`` automatically the moment it has content.

THE TWO MODELS
--------------
``demo-01-two-room``
    Exactly ``garh_model.testing``'s two-room fixture, op for op. Shared with the model
    core's own goldens, so a change in the fold shows up in both places at once.

``demo-02-blr-30x40-g1``
    §17's demo project geometry: the 30x40 ft Bengaluru plot, 9 m road south, G+1, four
    rooms per floor around a central cross wall, a dogleg stair, and setbacks that are
    real numbers (front 3000, sides 1000, rear 1500) rather than zero — which is what
    makes the site plan's setback chains and the area statement's setback rows mean
    something.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from garh_model.fold import apply_group  # noqa: E402
from garh_model.model import empty_project_doc  # noqa: E402
from garh_model.ops import Op, op  # noqa: E402
from garh_model.testing import (  # noqa: E402
    DEMO_PLOT_POLYGON,
    fixed_id,
    opening_ops,
    two_room_plan_ops,
)

OUT_DIR = os.path.join(_ROOT, "fixtures", "sheets", "inputs")

# ---------------------------------------------------------------------------
# demo-02 geometry. Every number here is a millimetre and every one is deliberate.
# ---------------------------------------------------------------------------
#: 30x40 ft in mm, from DEMO_PLOT_POLYGON.
PLOT_W = 9_144
PLOT_H = 12_192

SETBACK_FRONT = 3_000   # south, onto the 9 m road
SETBACK_REAR = 1_500
SETBACK_SIDE = 1_000

EXT_THICKNESS = 230
INT_THICKNESS = 115
STOREY_HEIGHT = 3_000

#: External wall CENTRELINES: the outer face sits half a thickness outside these, so the
#: setbacks above are the distances an inspector will measure.
X0 = SETBACK_SIDE + EXT_THICKNESS // 2               # 1115
X1 = PLOT_W - SETBACK_SIDE - EXT_THICKNESS // 2      # 8029
Y0 = SETBACK_FRONT + EXT_THICKNESS // 2              # 3115
Y1 = PLOT_H - SETBACK_REAR - EXT_THICKNESS // 2      # 10577

#: The internal cross: one vertical and one horizontal wall make four rooms per floor.
X_SPINE = 4_600
Y_SPINE = 6_800


def _pt(x: int, y: int) -> Dict[str, int]:
    return {"x": x, "y": y}


def _external_walls(storey_id: str, suffix: str) -> List[Op]:
    """The four external walls, ring order CCW starting at the south-west corner."""
    ring = (
        ("S", (X0, Y0), (X1, Y0)),
        ("E", (X1, Y0), (X1, Y1)),
        ("N", (X1, Y1), (X0, Y1)),
        ("W", (X0, Y1), (X0, Y0)),
    )
    return [
        op(
            "wall.add",
            id=fixed_id("wall", "%s%s" % (tag, suffix)),
            storeyId=storey_id,
            a=_pt(*a),
            b=_pt(*b),
            thicknessMm=EXT_THICKNESS,
            kind="external",
            loadBearing=True,
        )
        for tag, a, b in ring
    ]


def _internal_walls(storey_id: str, suffix: str) -> List[Op]:
    return [
        op(
            "wall.add",
            id=fixed_id("wall", "V%s" % suffix),
            storeyId=storey_id,
            a=_pt(X_SPINE, Y0),
            b=_pt(X_SPINE, Y1),
            thicknessMm=INT_THICKNESS,
            kind="internal",
        ),
        op(
            "wall.add",
            id=fixed_id("wall", "H%s" % suffix),
            storeyId=storey_id,
            a=_pt(X0, Y_SPINE),
            b=_pt(X1, Y_SPINE),
            thicknessMm=INT_THICKNESS,
            kind="internal",
        ),
    ]


def _openings(suffix: str) -> List[Op]:
    """Doors and windows, offsets measured to the opening CENTRE from each wall's ``a``.

    Sills come from the model's defaults (900 for windows, 0 for doors) so the section
    and the schedule read the same numbers the levels carry.
    """
    south = fixed_id("wall", "S%s" % suffix)
    east = fixed_id("wall", "E%s" % suffix)
    north = fixed_id("wall", "N%s" % suffix)
    west = fixed_id("wall", "W%s" % suffix)
    vertical = fixed_id("wall", "V%s" % suffix)
    horizontal = fixed_id("wall", "H%s" % suffix)

    def opening(tag: str, wall: str, kind: str, width: int, height: int, sill: int,
                offset: int, swing: str = "in-left") -> Op:
        return op(
            "opening.add",
            id=fixed_id("opening", "%s%s" % (tag, suffix)),
            wallId=wall,
            kind=kind,
            widthMm=width,
            heightMm=height,
            sillMm=sill,
            offsetMm=offset,
            swing=swing,
        )

    return [
        # Main entrance, south facade onto the road.
        opening("D1", south, "door", 1_000, 2_100, 0, 2_000),
        # Windows, one per facade so all four elevations have something to draw.
        opening("W1", south, "window", 1_500, 1_200, 900, 5_200),
        opening("W2", east, "window", 1_500, 1_200, 900, 2_000),
        opening("W3", east, "window", 1_200, 1_200, 900, 5_600),
        opening("W4", north, "window", 1_800, 1_200, 900, 2_400),
        opening("W5", west, "window", 1_200, 1_200, 900, 2_200),
        opening("W6", west, "window", 1_200, 1_200, 900, 5_600),
        # A ventilator, so the schedule exercises all three opening kinds and the V tag.
        opening("V1", north, "ventilator", 600, 450, 1_800, 5_800),
        # Internal doors.
        opening("D2", vertical, "door", 900, 2_100, 0, 1_800, "in-right"),
        opening("D3", vertical, "door", 900, 2_100, 0, 5_400, "in-left"),
        opening("D4", horizontal, "door", 900, 2_100, 0, 1_500, "in-left"),
    ]


def demo_02_ops() -> List[Op]:
    ground = fixed_id("storey", "GF")
    first = fixed_id("storey", "FF")
    ops: List[Op] = [
        op("plot.set_boundary", polygon=list(DEMO_PLOT_POLYGON), source="seed"),
        op("plot.set_north", deg=0),
        op("plot.set_road", edgeIndex=0, widthMm=9_000, name="9 m road (south)"),
        op("plot.set_reg_profile", cityPack="blr", overrides={}),
        op("brief.update", patch={"bedrooms": 3, "storeys": 2}, vastuMode="off",
           completeness=80),
        op("storey.add", id=ground, index=0, name="Ground Floor", heightMm=STOREY_HEIGHT),
        op("storey.add", id=first, index=1, name="First Floor", heightMm=STOREY_HEIGHT),
        op("levels.set", plinthMm=600, sillDefaultMm=900, lintelDefaultMm=2_100,
           parapetMm=1_000),
    ]
    ops.extend(_external_walls(ground, "G"))
    ops.extend(_internal_walls(ground, "G"))
    ops.extend(_openings("G"))
    ops.extend(_external_walls(first, "F"))
    ops.extend(_internal_walls(first, "F"))
    ops.extend(_openings("F"))
    # A dogleg stair in the rear-left room. 18 risers x 167 mm = 3006 mm, which the model
    # accepts against a 3000 mm storey (+/-10 mm) — and 167/275 passes the NBC stair rule.
    ops.append(
        op(
            "stair.add",
            id=fixed_id("stair", "ST1"),
            storeyId=ground,
            kind="dogleg",
            origin=_pt(1_400, 7_100),
            direction="N",
            riserMm=167,
            treadMm=275,
            widthMm=1_000,
            risersCount=18,
            landing={"widthMm": 2_115, "depthMm": 1_000},
        )
    )
    return ops


def demo_01_ops() -> List[Op]:
    """The model core's own two-room fixture, unchanged, plus its two openings."""
    return list(two_room_plan_ops()) + list(opening_ops())


# ---------------------------------------------------------------------------
FIXTURES = (
    {
        "id": "demo-01-two-room",
        "name": "Two-room fixture (shared with garh_model.testing)",
        "unitsDisplay": "ft-in",
        "dimToJamb": False,
        "provenance": (
            "garh_model.testing.two_room_plan_ops() + opening_ops(), verbatim. Shared "
            "with fixtures/model/golden-states.json so a fold change shows up in both."
        ),
        "titleBlock": {
            "firmName": "Studio Demo",
            "projectName": "Two-room test plan",
            "clientName": "-",
            "date": "01-01-2026",
            "drawnBy": "GARH",
            "checkedBy": "-",
            "revision": "A",
        },
        "revisions": [["A", "01-01-2026", "First issue"]],
        "ops": demo_01_ops,
    },
    {
        "id": "demo-02-blr-30x40-g1",
        "name": "Sharma Residence - 30x40 ft Bengaluru, G+1",
        "unitsDisplay": "ft-in",
        "dimToJamb": False,
        "provenance": (
            "Hand-drawn plan on §17's demo plot (30x40 ft Bengaluru, 9 m road south, "
            "G+1). NOT solver output — fixtures/plans/ is empty until Phase 3, and a "
            "fabricated solver golden would be worse than none. Setbacks front 3000, "
            "sides 1000, rear 1500 mm, so the site plan and the area statement have real "
            "numbers to print."
        ),
        "titleBlock": {
            "firmName": "Studio Demo",
            "projectName": "Sharma Residence",
            "clientName": "R. Sharma",
            "date": "01-01-2026",
            "drawnBy": "AR",
            "checkedBy": "HG",
            "revision": "A",
            "notes": "Advisory only - not an approval.",
        },
        "revisions": [
            ["A", "01-01-2026", "First issue for approval"],
            ["B", "15-01-2026", "Kitchen window revised"],
        ],
        "ops": demo_02_ops,
    },
)


def build(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """Fold the ops to prove they are valid, then serialise the fixture with its hash."""
    ops = fixture["ops"]()
    result = apply_group(empty_project_doc(fixture["unitsDisplay"]), ops)
    doc = result.model
    payload = {key: value for key, value in fixture.items() if key != "ops"}
    payload["ops"] = [item.to_json() for item in ops]
    # Recorded so a reader can see what the ops produce without folding them, and so a
    # silent change in the fold shows up in this file's diff as well as in the goldens.
    payload["folded"] = {
        "storeys": [storey.id for storey in doc.house.storeys],
        "wallCount": len(doc.house.walls),
        "openingCount": len(doc.house.openings),
        "roomCount": len(doc.house.rooms),
        "stairCount": len(doc.house.stairs),
        "roomAreasMm2": {room.id: room.area_mm2 for room in doc.house.rooms},
        "levels": {
            "plinthMm": doc.house.levels.plinth_mm,
            "fflPerStoreyMm": list(doc.house.levels.ffl_per_storey_mm),
            "sillDefaultMm": doc.house.levels.sill_default_mm,
            "lintelDefaultMm": doc.house.levels.lintel_default_mm,
            "parapetMm": doc.house.levels.parapet_mm,
        },
    }
    return payload


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                       help="verify the committed files match what this script produces")
    args = parser.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    problems: List[str] = []
    for fixture in FIXTURES:
        payload = build(fixture)
        text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        path = os.path.join(OUT_DIR, "%s.json" % fixture["id"])
        if args.check:
            existing = None
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    existing = handle.read()
            if existing != text:
                problems.append(fixture["id"])
                print("  FAIL %s is out of date" % os.path.basename(path))
            else:
                print("  ok   %s" % os.path.basename(path))
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            folded = payload["folded"]
            print("  ok   wrote %s (%d ops -> %d walls, %d openings, %d rooms)"
                  % (os.path.basename(path), len(payload["ops"]), folded["wallCount"],
                     folded["openingCount"], folded["roomCount"]))
    if problems:
        print("FAIL %d fixture(s) out of date. Run without --check and commit." % len(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
