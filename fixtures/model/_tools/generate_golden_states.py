#!/usr/bin/env python3
"""Generate ``fixtures/model/golden-states.json`` — THE CROSS-LANGUAGE SYNC CHECK.

Each case is an op log plus the ``stateHash`` the document folds to. Both
implementations replay the ops and assert the same 64 hex characters:

* Python: ``apps/api/garh_model/tests/test_fold.py::test_golden_states``
* TypeScript: ``packages/model/src/fold.test.ts`` (see the note in the JSON
  header — the TS test must be pointed at THIS file, not at a second copy)

A red row means the canvas and the server disagree about what a design IS, which
is the one bug class that silently corrupts saved work. Never "fix" a row by
pasting the new hash: work out which side changed and why, and if the change is
intended, regenerate here IN THE SAME COMMIT as the behaviour change and say so
in ``DECISIONS.md``.

Usage (from the repo root, with the API package importable):

    PYTHONPATH=apps/api python3 fixtures/model/_tools/generate_golden_states.py

Add ``--check`` to verify the committed file instead of rewriting it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from garh_model import (  # noqa: E402
    CANONICAL_JSON_SPEC,
    STATE_HASH_ALGORITHM,
    Op,
    apply_group,
    doc_hash,
    empty_project_doc,
)
from garh_model.testing import (  # noqa: E402
    DEMO_PLOT_POLYGON,
    FIXTURE_IDS,
    fixed_id,
    opening_ops,
    two_room_plan_ops,
)

OUT_PATH = REPO_ROOT / "fixtures" / "model" / "golden-states.json"

GF = FIXTURE_IDS["groundStorey"]
FF = FIXTURE_IDS["firstStorey"]


def _pt(x: int, y: int) -> Dict[str, int]:
    return {"x": x, "y": y}


def _op(op_type: str, **payload: Any) -> Dict[str, Any]:
    return {"type": op_type, "payload": payload}


def _wall(
    wall_id: str, storey: str, ax: int, ay: int, bx: int, by: int, thickness: int, kind: str
) -> Dict[str, Any]:
    return _op(
        "wall.add",
        id=wall_id,
        storeyId=storey,
        a=_pt(ax, ay),
        b=_pt(bx, by),
        thicknessMm=thickness,
        kind=kind,
    )


def _two_room_ops() -> List[Dict[str, Any]]:
    return [o.to_json() for o in two_room_plan_ops()]


def _opening_ops() -> List[Dict[str, Any]]:
    return [o.to_json() for o in opening_ops()]


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


def cases() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    out.append(
        {
            "name": "empty-document",
            "description": (
                "The initial state every op log folds from: empty_project_doc('ft-in') with no "
                "ops applied. Pins the defaults (plinth 600, sill 900, lintel 2100, parapet "
                "1000) and the exact key set of the document."
            ),
            "unitsDisplay": "ft-in",
            "ops": [],
        }
    )

    out.append(
        {
            "name": "plot-only",
            "description": (
                "Plot ops 1-4 with no building: 30x40ft boundary, north 0, a 9m road on edge 0, "
                "and the Bengaluru rule pack. plot.set_reg_profile also writes meta.regProfileRef."
            ),
            "unitsDisplay": "ft-in",
            "ops": [
                _op("plot.set_boundary", polygon=list(DEMO_PLOT_POLYGON), source="seed"),
                _op("plot.set_north", deg=23),
                _op("plot.set_road", edgeIndex=0, widthMm=9000, name="9m Road"),
                _op("plot.set_road", edgeIndex=3, widthMm=12000, name="12m Road"),
                _op("plot.set_reg_profile", cityPack="blr", overrides={"farMax": 175}),
            ],
        }
    )

    out.append(
        {
            "name": "brief-merge-patch",
            "description": (
                "RFC 7386 merge patch semantics: nested objects merge, an explicit null DELETES a "
                "key, a later scalar replaces. Ends with data = {bedrooms: 4, budget: {totalInr: "
                "9500000}, style: 'contemporary'} — 'baths' was deleted by the null."
            ),
            "unitsDisplay": "ft-in",
            "ops": [
                _op(
                    "brief.update",
                    patch={"bedrooms": 3, "baths": 2, "budget": {"totalInr": 8500000}},
                    completeness=40,
                ),
                _op(
                    "brief.update",
                    patch={"bedrooms": 4, "budget": {"totalInr": 9500000}, "baths": None},
                    vastuMode="advisory",
                    completeness=65,
                ),
                _op("brief.update", patch={"style": "contemporary"}),
            ],
        }
    )

    out.append(
        {
            "name": "two-room-plan",
            "description": (
                "The canonical fixture: 6000x4000 ground floor, 230mm external walls, a 115mm "
                "spine at x=3000. Room detection must find EXACTLY two rooms of 10_661_560 mm2 "
                "each ((2943-115)x(3885-115)), with derived ids, plus one derived floor slab "
                "grown outward to (-115,-115)..(6115,4115)."
            ),
            "unitsDisplay": "ft-in",
            "ops": _two_room_ops(),
        }
    )

    out.append(
        {
            "name": "two-room-plan-with-openings",
            "description": (
                "The two-room plan plus a 900x2100 door at offset 1500 on the south wall and a "
                "1200x1200 window at offset 2000 (sill 900) on the west wall. Openings never "
                "change room polygons."
            ),
            "unitsDisplay": "ft-in",
            "ops": _two_room_ops() + _opening_ops(),
        }
    )

    room_a = "room_66G2VB6DPW6JB5SWS7BRCH4SPN"  # derived: west room, see the test
    room_b = "room_3STWE7A7RW1W36KN6PTGH7ANAS"  # derived: east room
    out.append(
        {
            "name": "rooms-assigned",
            "description": (
                "room.assign / room.set_target on the two derived rooms. The room ids here are "
                "DERIVED (sha256 of storeyId|polygonKey -> 26 Crockford chars), so this case also "
                "pins derived_id() and polygon_key() across languages."
            ),
            "unitsDisplay": "ft-in",
            "ops": _two_room_ops()
            + [
                _op(
                    "room.assign",
                    roomId=room_a,
                    type="living",
                    name="Living",
                    tags=["front"],
                    locked=True,
                ),
                _op("room.assign", roomId=room_b, type="bedroom_master", name="Master Bedroom"),
                _op("room.set_target", roomId=room_b, targetAreaMm2=12000000, mustFace="NE"),
            ],
        }
    )

    out.append(
        {
            "name": "wall-split-and-move",
            "description": (
                "wall.split at 3000mm re-hosts the openings BEYOND the split onto the new wall "
                "with a rebased offset (the 4500mm door becomes 1500mm on wall WS2) and leaves "
                "the 1500mm door where it is; wall.move then shifts the spine to x=3500, which "
                "re-cuts both rooms while PRESERVING their ids (max-Jaccard match)."
            ),
            "unitsDisplay": "ft-in",
            "ops": _two_room_ops()
            + _opening_ops()
            + [
                _op(
                    "opening.add",
                    id=fixed_id("opening", "D2"),
                    wallId=FIXTURE_IDS["wallSouth"],
                    kind="door",
                    widthMm=900,
                    heightMm=2100,
                    sillMm=0,
                    offsetMm=4500,
                    swing="in-right",
                ),
                _op(
                    "wall.split",
                    wallId=FIXTURE_IDS["wallSouth"],
                    atMm=3000,
                    newWallId=fixed_id("wall", "WS2"),
                ),
                _op(
                    "wall.move",
                    wallId=FIXTURE_IDS["wallSpine"],
                    a=_pt(3500, 0),
                    b=_pt(3500, 4000),
                ),
            ],
        }
    )

    out.append(
        {
            "name": "storeys-stair-levels",
            "description": (
                "G+1 with a dogleg stair on the ground floor: 18 risers x 167mm = 3006mm against "
                "a 3000mm storey (inside the +/-10mm tolerance). The first floor slab must carry "
                "the stair footprint as a cut-out, and levels.set with an explicit plinth "
                "re-derives fflPerStoreyMm = [750, 3750]."
            ),
            "unitsDisplay": "m",
            "ops": _two_room_ops()
            + [
                _op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000),
                _wall(fixed_id("wall", "FFS"), FF, 0, 0, 6000, 0, 230, "external"),
                _wall(fixed_id("wall", "FFE"), FF, 6000, 0, 6000, 4000, 230, "external"),
                _wall(fixed_id("wall", "FFN"), FF, 6000, 4000, 0, 4000, 230, "external"),
                _wall(fixed_id("wall", "FFW"), FF, 0, 4000, 0, 0, 230, "external"),
                _op(
                    "stair.add",
                    id=FIXTURE_IDS["stair"],
                    storeyId=GF,
                    kind="dogleg",
                    origin=_pt(1000, 1000),
                    direction="N",
                    riserMm=167,
                    treadMm=275,
                    widthMm=1000,
                    risersCount=18,
                    landing={"widthMm": 2100, "depthMm": 1000},
                ),
                _op("levels.set", plinthMm=750, parapetMm=1050),
            ],
        }
    )

    out.append(
        {
            "name": "furnishings-and-facade",
            "description": (
                "The non-geometric families in one log: a column (default 230x230 applied by "
                "fold), furniture, a balcony (defaults for railing/projection/slab), a facade kit "
                "with two components, a merge patch on one component's params, a material "
                "assignment and a sheet annotation. Pins every 'defaults applied by fold' path."
            ),
            "unitsDisplay": "ft-in",
            "ops": _two_room_ops()
            + [
                _op("column.set", action="add", id=FIXTURE_IDS["column"], storeyId=GF, pt=_pt(3000, 2000)),
                _op(
                    "furniture.set",
                    action="place",
                    id=FIXTURE_IDS["sofa"],
                    storeyId=GF,
                    catalogId="sofa-3seat-1980x900",
                    pt=_pt(1500, 2000),
                    rotationDeg=90,
                ),
                _op(
                    "balcony.set",
                    action="add",
                    id=FIXTURE_IDS["balcony"],
                    storeyId=GF,
                    polygon=[_pt(0, 4000), _pt(2400, 4000), _pt(2400, 4900), _pt(0, 4900)],
                ),
                _op(
                    "facade.apply_kit",
                    kitId="contemporary",
                    seed=7,
                    colorwayId="mono-wood",
                    components=[
                        {
                            "id": fixed_id("facadecomp", "FC1"),
                            "kind": "chajja",
                            "storeyId": GF,
                            "wallId": FIXTURE_IDS["wallSouth"],
                            "openingId": None,
                            "params": {"projectionMm": 600, "thicknessMm": 100},
                        },
                        {
                            "id": fixed_id("facadecomp", "FC2"),
                            "kind": "parapet_profile",
                            "storeyId": GF,
                            "wallId": None,
                            "openingId": None,
                            "params": {"heightMm": 1000},
                        },
                    ],
                ),
                _op(
                    "facade.edit_component",
                    componentId=fixed_id("facadecomp", "FC1"),
                    patch={"projectionMm": 750},
                ),
                _op(
                    "material.assign",
                    id=FIXTURE_IDS["material"],
                    target={"group": "external_wall", "storeyId": None, "elementId": None},
                    materialId="texture-paint-grey",
                ),
                _op(
                    "annotation.set",
                    action="add",
                    id=FIXTURE_IDS["annotation"],
                    sheetId=FIXTURE_IDS["sheet"],
                    anchorElementId=FIXTURE_IDS["wallSouth"],
                    anchorKind="wall",
                    payload={"text": "RCC beam over — refer structural", "leaderMm": 450},
                ),
            ],
        }
    )

    out.append(
        {
            "name": "solver-apply-option",
            "description": (
                "solver.apply_option carries its own expansion and folds as ONE atomic group, so "
                "this log must produce exactly the same document (and hash) as applying the four "
                "inner ops directly — asserted by the test, not just by this hash."
            ),
            "unitsDisplay": "ft-in",
            "ops": [
                _op("storey.add", id=GF, index=0, name="Ground Floor", heightMm=3000),
                _op(
                    "solver.apply_option",
                    solverJobId="job_golden",
                    optionIndex=0,
                    lockedRoomIds=[],
                    ops=[
                        _wall(FIXTURE_IDS["wallSouth"], GF, 0, 0, 6000, 0, 230, "external"),
                        _wall(FIXTURE_IDS["wallEast"], GF, 6000, 0, 6000, 4000, 230, "external"),
                        _wall(FIXTURE_IDS["wallNorth"], GF, 6000, 4000, 0, 4000, 230, "external"),
                        _wall(FIXTURE_IDS["wallWest"], GF, 0, 4000, 0, 0, 230, "external"),
                    ],
                ),
            ],
        }
    )

    out.append(
        {
            "name": "unicode-and-escapes",
            "description": (
                "Canonical-JSON string rules: non-ASCII is emitted literally as UTF-8 (never "
                "\\uXXXX), while backslash, quote, tab, newline and other C0 controls are escaped "
                "minimally with LOWERCASE hex. Devanagari, an em dash and a rupee sign all appear "
                "in real Indian briefs."
            ),
            "unitsDisplay": "ft-in",
            "ops": [
                _op(
                    "brief.update",
                    patch={
                        "clientName": "श्री रमेश कुमार",
                        "note": 'Tab\there\nnewline "quoted" back\\slash',
                        "control": "\u0007bell\u001fend",
                        "budgetLabel": "₹ 95,00,000 — final",
                        "emoji": "🏠",
                    },
                    completeness=10,
                ),
            ],
        }
    )

    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for case in cases():
        initial = empty_project_doc(case.get("unitsDisplay", "ft-in"))
        doc = apply_group(initial, [Op.from_json(o) for o in case["ops"]]).model
        rows.append(
            {
                "name": case["name"],
                "description": case["description"],
                "unitsDisplay": case.get("unitsDisplay", "ft-in"),
                "ops": case["ops"],
                "expectedStateHash": doc_hash(doc),
            }
        )
    return {
        "$comment": (
            "LANGUAGE-NEUTRAL CROSS-LANGUAGE CONTRACT for the op engine. Each case is an op log "
            "applied to empty_project_doc(unitsDisplay); expectedStateHash is "
            "stateHash(ProjectDoc) = sha256(canonicalJson(doc)) as 64 lowercase hex chars. BOTH "
            "packages/model/src/fold.test.ts AND apps/api/garh_model/tests/test_fold.py must read "
            "THIS file and assert every row. A mismatch means the two implementations disagree "
            "about what a design IS — never paste the new hash to make it pass; find out which "
            "side moved. Generated by fixtures/model/_tools/generate_golden_states.py."
        ),
        "schemaVersion": 1,
        "canonicalJsonSpec": CANONICAL_JSON_SPEC,
        "hashAlgorithm": STATE_HASH_ALGORITHM,
        "notes": [
            "Ops are the wire form: {type, payload} with camelCase payload keys, integer mm only.",
            "An absent payload key means 'unchanged'; an explicit null means 'clear it'.",
            "Element arrays are sorted by id before hashing; storeys keep their semantic order.",
            "Room and slab ids are DERIVED, so they are part of the hash: they pin derived_id().",
        ],
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify instead of rewriting")
    args = parser.parse_args()

    data = build()
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not OUT_PATH.exists():
            print(f"MISSING {OUT_PATH}", file=sys.stderr)
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current != text:
            print(
                f"STALE {OUT_PATH} — re-run without --check and review the diff.", file=sys.stderr
            )
            return 1
        print(f"OK {OUT_PATH} ({len(data['cases'])} cases)")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(data['cases'])} cases)")
    for row in data["cases"]:
        print(f"  {row['expectedStateHash']}  {row['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
