#!/usr/bin/env python3
"""Generate ``fixtures/model/golden-transforms.json`` — THE COPY/ARRAY/MIRROR SYNC CHECK.

``golden-states.json`` pins what the two FOLDS agree a document is. This file
pins something the fold cannot see: that the two PLANNERS
(``packages/model/src/transform.ts`` and ``apps/api/garh_model/transform.py``)
turn the same document plus the same request into the same OP LIST — key for
key, id for id, in the same order — before either fold gets a chance to run.

That is the whole risk copy / paste / array / mirror carries. The feature adds no
op type (it is a group of ``wall.add`` / ``opening.add`` / … that both folds
already agree on), so a divergence would never show up as a rejected op. It would
show up as the browser optimistically folding one paste and the server
authoritatively folding a different one, and the two hashes parting company on a
document the user is still typing into.

Each case therefore carries THREE assertions, and both languages make all three:

* ``expectedOps``        — the plan, in the wire form, compared element by element
* ``expectedPlan``       — the counts and labels the UI shows the architect
* ``expectedStateHash``  — the document after ``baseOps`` + ``expectedOps``

Refusal cases carry ``expectedRefusal`` instead: the guards (mixed storeys, zero
offset, count bounds, an opening without its wall) are as much a cross-language
contract as the geometry, because a guard that fires on one side only is a guard
that lets a corrupt document through on the other.

Usage (from the repo root, with the API package importable)::

    PYTHONPATH=apps/api python3 fixtures/model/_tools/generate_golden_transforms.py

Add ``--check`` to verify the committed file instead of rewriting it.

NEVER "fix" a row by pasting the new value. Work out which side moved, and if the
change is intended, regenerate here IN THE SAME COMMIT as the behaviour change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from garh_model.geometry import Pt  # noqa: E402
from garh_model.testing import (  # noqa: E402
    FIXTURE_IDS,
    fixed_id,
    opening_ops,
    two_room_plan_ops,
)
from garh_model.transform import (  # noqa: E402
    ArrayRequest,
    MirrorRequest,
    PasteRequest,
    TransformPlanResult,
    plan_array,
    plan_mirror,
    plan_paste,
)

OUT_PATH = REPO_ROOT / "fixtures" / "model" / "golden-transforms.json"

GF = FIXTURE_IDS["groundStorey"]
FF = FIXTURE_IDS["firstStorey"]

WALLS = [
    FIXTURE_IDS["wallSouth"],
    FIXTURE_IDS["wallEast"],
    FIXTURE_IDS["wallNorth"],
    FIXTURE_IDS["wallWest"],
    FIXTURE_IDS["wallSpine"],
]

#: Group ids are the id-derivation seed, so they are part of every expected id.
#: Fixed here (a valid ULID body) exactly as `fixed_id` fixes element ids.
GROUP = {
    "paste": fixed_id("group", "GPASTE"),
    "paste2": fixed_id("group", "GPASTE2"),
    "array": fixed_id("group", "GARRAY"),
    "array2": fixed_id("group", "GARRAY2"),
    "mirror": fixed_id("group", "GMIRROR"),
    "mirror2": fixed_id("group", "GMIRROR2"),
    "mirror3": fixed_id("group", "GMIRROR3"),
    "refuse": fixed_id("group", "GREFUSE"),
}


def _pt(x: int, y: int) -> Dict[str, int]:
    return {"x": x, "y": y}


def _op(op_type: str, **payload: Any) -> Dict[str, Any]:
    return {"type": op_type, "payload": payload}


def _two_room() -> List[Dict[str, Any]]:
    return [o.to_json() for o in two_room_plan_ops()]


def _openings() -> List[Dict[str, Any]]:
    return [o.to_json() for o in opening_ops()]


def _furnished() -> List[Dict[str, Any]]:
    """A column, a rotated sofa and a balcony on the ground floor."""
    return [
        _op(
            "column.set",
            action="add",
            id=FIXTURE_IDS["column"],
            storeyId=GF,
            pt=_pt(3000, 2000),
            sizeMm={"xMm": 300, "yMm": 230},
        ),
        _op(
            "furniture.set",
            action="place",
            id=FIXTURE_IDS["sofa"],
            storeyId=GF,
            catalogId="sofa-3seat-1980x900",
            pt=_pt(1500, 2000),
            rotationDeg=30,
        ),
        _op(
            "balcony.set",
            action="add",
            id=FIXTURE_IDS["balcony"],
            storeyId=GF,
            polygon=[_pt(0, 4000), _pt(2400, 4000), _pt(2400, 4900), _pt(0, 4900)],
        ),
    ]


def _stair() -> List[Dict[str, Any]]:
    """A dogleg stair travelling north — its origin corner is direction-dependent."""
    return [
        _op(
            "stair.add",
            id=FIXTURE_IDS["stair"],
            storeyId=GF,
            kind="dogleg",
            origin=_pt(1000, 500),
            direction="N",
            riserMm=167,
            treadMm=275,
            widthMm=1000,
            risersCount=18,
            landing={"widthMm": 2100, "depthMm": 1000},
        ),
    ]


def _room_ids(ops: List[Dict[str, Any]], storey_id: str) -> List[str]:
    """Derived room ids of a storey after folding ``ops`` — sorted, as the doc is."""
    doc = apply_group(empty_project_doc("ft-in"), [Op.from_json(o) for o in ops]).model
    return [r.id for r in doc.house.rooms if r.storey_id == storey_id]


def _named_rooms() -> List[Dict[str, Any]]:
    """The two-room plan with both rooms named, so a transform has metadata to carry."""
    base = _two_room()
    rooms = _room_ids(base, GF)
    assert len(rooms) == 2, f"expected 2 derived rooms, got {len(rooms)}"
    return base + [
        _op(
            "room.assign",
            roomId=rooms[0],
            type="living",
            name="Living",
            tags=["front"],
            locked=True,
        ),
        _op("room.assign", roomId=rooms[1], type="bedroom_master", name="Master Bedroom"),
        _op("room.set_target", roomId=rooms[1], targetAreaMm2=12_000_000, mustFace="NE"),
    ]


# ---------------------------------------------------------------------------
# Requests: the language-neutral wire form, and how to run one
# ---------------------------------------------------------------------------


def _run(doc_ops: List[Dict[str, Any]], request: Dict[str, Any]) -> TransformPlanResult:
    """Apply ``request`` to the document ``doc_ops`` folds to."""
    doc = apply_group(empty_project_doc("ft-in"), [Op.from_json(o) for o in doc_ops]).model
    kind = request["kind"]
    if kind == "paste":
        return plan_paste(
            doc,
            PasteRequest(
                element_ids=list(request["elementIds"]),
                group_id=request["groupId"],
                delta_mm=Pt(x=request["deltaMm"]["x"], y=request["deltaMm"]["y"]),
                target_storey_id=request.get("targetStoreyId"),
            ),
        )
    if kind == "array":
        return plan_array(
            doc,
            ArrayRequest(
                element_ids=list(request["elementIds"]),
                group_id=request["groupId"],
                count_x=request["countX"],
                count_y=request["countY"],
                spacing_x_mm=request["spacingXMm"],
                spacing_y_mm=request["spacingYMm"],
            ),
        )
    if kind == "mirror":
        return plan_mirror(
            doc,
            MirrorRequest(
                element_ids=list(request["elementIds"]),
                group_id=request["groupId"],
                axis=request["axis"],
                at_mm=request.get("atMm"),
                keep_original=request.get("keepOriginal", True),
                target_storey_id=request.get("targetStoreyId"),
            ),
        )
    raise ValueError(f"unknown transform kind {kind!r}")


def paste(
    group: str,
    element_ids: List[str],
    dx: int,
    dy: int,
    target: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "kind": "paste",
        "elementIds": element_ids,
        "groupId": group,
        "deltaMm": _pt(dx, dy),
        "targetStoreyId": target,
    }


def array(
    group: str, element_ids: List[str], cx: int, cy: int, sx: int, sy: int
) -> Dict[str, Any]:
    return {
        "kind": "array",
        "elementIds": element_ids,
        "groupId": group,
        "countX": cx,
        "countY": cy,
        "spacingXMm": sx,
        "spacingYMm": sy,
    }


def mirror(
    group: str,
    element_ids: List[str],
    axis: str,
    at_mm: Optional[int] = None,
    keep_original: bool = True,
    target: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "kind": "mirror",
        "elementIds": element_ids,
        "groupId": group,
        "axis": axis,
        "atMm": at_mm,
        "keepOriginal": keep_original,
        "targetStoreyId": target,
    }


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


def cases() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    two_room_openings = _two_room() + _openings()
    named = _named_rooms()
    named_openings = named + _openings()

    out.append(
        {
            "name": "paste-wall-carries-its-door",
            "description": (
                "One wall selected, translated 8m north. The 900x2100 door hosted on it is "
                "carried WITHOUT being selected — an opening travels with its wall — and lands "
                "on the copy at the same 1500mm offset, because a translation preserves distance "
                "along the wall. The swing is unchanged: a translation does not reverse "
                "orientation."
            ),
            "baseOps": two_room_openings,
            "request": paste(GROUP["paste"], [FIXTURE_IDS["wallSouth"]], 0, 8000),
        }
    )

    out.append(
        {
            "name": "paste-whole-floor-to-first-storey",
            "description": (
                "Every wall, the column, the sofa and the balcony pasted straight up onto a new "
                "first floor with a zero delta — the commonest real gesture on a G+1 job. Zero "
                "delta is legal here precisely BECAUSE the target storey differs; the same "
                "request onto the source storey is refused (see refuse-paste-in-place)."
            ),
            "baseOps": (
                named_openings
                + _furnished()
                + [_op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000)]
            ),
            "request": paste(GROUP["paste2"], WALLS + [
                FIXTURE_IDS["column"],
                FIXTURE_IDS["sofa"],
                FIXTURE_IDS["balcony"],
            ], 0, 0, target=FF),
        }
    )

    out.append(
        {
            "name": "array-column-and-sofa-3x2",
            "description": (
                "A rectangular array: 3 across x 2 up, 5 copies (the original is instance 0 and "
                "stays put). Instances are emitted row-major — y outer, x inner — so the op order "
                "is a property of the request and not of a hash map. Nothing in the fold forbids "
                "two columns at one point, which is exactly why the zero-spacing guard exists."
            ),
            "baseOps": _two_room() + _furnished(),
            "request": array(
                GROUP["array"], [FIXTURE_IDS["column"], FIXTURE_IDS["sofa"]], 3, 2, 2000, 1500
            ),
        }
    )

    out.append(
        {
            "name": "array-wall-linear-negative-spacing",
            "description": (
                "A linear array (countY = 1) of the 115mm spine wall with a NEGATIVE spacing, so "
                "the copies march west of the original into empty space. Pins that a negative "
                "spacing is a first-class direction and not an error."
            ),
            "baseOps": _two_room(),
            "request": array(GROUP["array2"], [FIXTURE_IDS["wallSpine"]], 4, 1, -4000, 0),
        }
    )

    out.append(
        {
            "name": "mirror-copy-vertical-with-doors",
            "description": (
                "THE door-hand case. Five walls and their two openings mirrored about x = 9000 "
                "onto clear ground, originals kept. Every 'in-*' swing must come back 'out-*' "
                "with the hinge token UNCHANGED, and every offsetMm must be unchanged: the "
                "mirrored wall keeps a->M(a), b->M(b) rather than being re-normalised, so the "
                "along-wall parameter and the hinge end both survive. The two named rooms are "
                "re-derived on the copy and their names, tags, lock and solver target follow."
            ),
            "baseOps": named_openings,
            "request": mirror(GROUP["mirror"], WALLS, "vertical", at_mm=9000),
        }
    )

    out.append(
        {
            "name": "mirror-in-place-about-selection-centre",
            "description": (
                "Flip the plan: no copy, the originals move. The axis defaults to the selection's "
                "own extent centre — y spans 0..4000 so the line is y = 2000, carried as the "
                "integer 4000 = min+max so an odd extent would still be exact. This is the case "
                "wall.move CANNOT do: south and north swap positions, so moving either first "
                "trips WALL_DUPLICATE. The plan deletes every selected wall, re-adds them at the "
                "mirrored coordinates WITH THEIR ORIGINAL IDS, then re-adds the openings the "
                "cascade took, with the hand flipped. Room names are put back by the same "
                "carry-over, because the re-derived rooms come out blank."
            ),
            "baseOps": named_openings,
            "request": mirror(GROUP["mirror2"], WALLS, "horizontal", keep_original=False),
        }
    )

    out.append(
        {
            "name": "mirror-copy-stair-furniture-balcony",
            "description": (
                "The non-wall families under a reflection, all in one row. The dogleg stair is "
                "rebuilt from its own footprint (origin is a direction-dependent CORNER, so it "
                "cannot simply be mapped); N travel survives a vertical mirror unchanged while "
                "the origin corner moves. The sofa at 30 degrees comes back at 150 = 180 - 30 — a "
                "ROTATION, never a reflection, so a catalogue mesh is never handed a negative "
                "scale. The balcony ring is re-wound, because a reflection reverses orientation."
            ),
            "baseOps": _two_room() + _furnished() + _stair(),
            "request": mirror(
                GROUP["mirror3"],
                [FIXTURE_IDS["stair"], FIXTURE_IDS["sofa"], FIXTURE_IDS["balcony"], FIXTURE_IDS["column"]],
                "vertical",
                at_mm=12000,
            ),
        }
    )

    # -- refusals ----------------------------------------------------------
    # Every guard below is a place where a silent success would corrupt the
    # drawing set. They are pinned across languages for the same reason the
    # successes are.

    out.append(
        {
            "name": "refuse-paste-in-place",
            "description": (
                "A zero delta onto the SAME storey stacks each copy exactly on its original. The "
                "fold catches that for walls (WALL_DUPLICATE) but NOT for columns, furniture or "
                "balconies — nothing forbids two columns at one point — so the planner refuses "
                "before the fold ever sees it. Without this guard a 'paste in place' would "
                "silently double the structural count and the furniture schedule."
            ),
            "baseOps": _two_room() + _furnished(),
            "request": paste(GROUP["refuse"], [FIXTURE_IDS["column"]], 0, 0),
        }
    )

    out.append(
        {
            "name": "refuse-array-zero-spacing",
            "description": (
                "Same failure as refuse-paste-in-place, multiplied: a 6-count array at zero "
                "spacing puts six columns on one point."
            ),
            "baseOps": _two_room() + _furnished(),
            "request": array(GROUP["refuse"], [FIXTURE_IDS["column"]], 3, 2, 0, 1500),
        }
    )

    out.append(
        {
            "name": "refuse-array-count-too-large",
            "description": "40 x 40 is 1600 instances, past the 400 ceiling.",
            "baseOps": _two_room() + _furnished(),
            "request": array(GROUP["refuse"], [FIXTURE_IDS["column"]], 40, 40, 500, 500),
        }
    )

    out.append(
        {
            "name": "refuse-array-of-one",
            "description": "1 x 1 is the original and creates nothing — say so, do not no-op.",
            "baseOps": _two_room() + _furnished(),
            "request": array(GROUP["refuse"], [FIXTURE_IDS["column"]], 1, 1, 2000, 2000),
        }
    )

    out.append(
        {
            "name": "refuse-mixed-storeys",
            "description": (
                "A ground-floor wall and a first-floor wall in one selection. A transform has ONE "
                "target storey; flattening the two onto it would fold cleanly and be wrong."
            ),
            "baseOps": _two_room()
            + [
                _op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000),
                _op(
                    "wall.add",
                    id=fixed_id("wall", "FFS"),
                    storeyId=FF,
                    a=_pt(0, 0),
                    b=_pt(6000, 0),
                    thicknessMm=230,
                    kind="external",
                ),
            ],
            "request": paste(
                GROUP["refuse"], [FIXTURE_IDS["wallSouth"], fixed_id("wall", "FFS")], 0, 8000
            ),
        }
    )

    out.append(
        {
            "name": "refuse-opening-without-its-wall",
            "description": (
                "A door selected on its own. An opening exists only on a host wall, so the copy "
                "would need a wall that is not being created — refuse rather than drop it."
            ),
            "baseOps": two_room_openings,
            "request": paste(GROUP["refuse"], [FIXTURE_IDS["doorMain"]], 0, 8000),
        }
    )

    out.append(
        {
            "name": "refuse-unknown-element",
            "description": "An id of the right shape that is not in this document any more.",
            "baseOps": _two_room(),
            "request": paste(GROUP["refuse"], [fixed_id("wall", "GHOST")], 0, 8000),
        }
    )

    out.append(
        {
            "name": "refuse-unsupported-element",
            "description": (
                "Sheet annotations, material assignments and facade components are not "
                "duplicated: they are sheet- or building-scoped and doubling one would put a "
                "second assignment on a surface with no way for the architect to see it."
            ),
            "baseOps": _two_room(),
            "request": paste(GROUP["refuse"], [FIXTURE_IDS["annotation"]], 0, 8000),
        }
    )

    out.append(
        {
            "name": "refuse-rooms-only-selection",
            "description": (
                "Rooms are DERIVED from the walls around them, so a selection of nothing but "
                "rooms has no geometry to transform. The message names the fix."
            ),
            "baseOps": _two_room(),
            "request": paste(GROUP["refuse"], _room_ids(_two_room(), GF), 0, 8000),
        }
    )

    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _counts(counts: Any) -> Dict[str, int]:
    return {
        "walls": counts.walls,
        "openings": counts.openings,
        "stairs": counts.stairs,
        "columns": counts.columns,
        "furniture": counts.furniture,
        "balconies": counts.balconies,
    }


def build() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for case in cases():
        base_ops = case["baseOps"]
        result = _run(base_ops, case["request"])
        row: Dict[str, Any] = {
            "name": case["name"],
            "description": case["description"],
            "unitsDisplay": "ft-in",
            "baseOps": base_ops,
            "request": case["request"],
        }
        if result.ok:
            assert result.plan is not None
            plan = result.plan
            initial = empty_project_doc("ft-in")
            after = apply_group(
                initial, [Op.from_json(o) for o in base_ops] + list(plan.ops)
            ).model
            row["expectedOps"] = [o.to_json() for o in plan.ops]
            row["expectedPlan"] = {
                "kind": plan.kind,
                "sourceStoreyId": plan.source_storey_id,
                "targetStoreyId": plan.target_storey_id,
                "instances": plan.instances,
                "selected": _counts(plan.selected),
                "created": _counts(plan.created),
                "derivedSkipped": plan.derived_skipped,
                "roomsCarried": plan.rooms_carried,
                "label": plan.label,
            }
            row["expectedStateHash"] = doc_hash(after)
        else:
            assert result.refusal is not None
            row["expectedRefusal"] = {
                "reason": result.refusal.reason,
                "message": result.refusal.message,
            }
        rows.append(row)
    return {
        "$comment": (
            "LANGUAGE-NEUTRAL CROSS-LANGUAGE CONTRACT for the copy / paste / array / mirror "
            "planners. Each case folds baseOps into a document, runs `request` through the "
            "planner, and pins the OP LIST the planner emits, the plan summary the UI shows, and "
            "the state hash of the document afterwards. BOTH packages/model/src/transform.test.ts "
            "AND apps/api/garh_model/tests/test_transform.py must read THIS file and assert every "
            "row. These transforms add no op type, so a divergence between the two planners would "
            "never be rejected by either fold — it would surface as the browser and the server "
            "disagreeing about a document the user is still editing. Never paste a new value to "
            "make a row pass; find out which side moved. Generated by "
            "fixtures/model/_tools/generate_golden_transforms.py."
        ),
        "schemaVersion": 1,
        "canonicalJsonSpec": CANONICAL_JSON_SPEC,
        "hashAlgorithm": STATE_HASH_ALGORITHM,
        "notes": [
            "baseOps and expectedOps are the wire form: {type, payload}, camelCase, integer mm.",
            "New element ids are derived from the request's groupId, so the groupId is part of "
            "every expected id — that is what makes a paste comparable across languages at all.",
            "expectedStateHash is stateHash(ProjectDoc) after baseOps + expectedOps, 64 lowercase "
            "hex chars, the same definition golden-states.json uses.",
            "A row with expectedRefusal must be refused with that exact reason on both sides: a "
            "guard that fires in one language only is worse than no guard.",
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
        if OUT_PATH.read_text(encoding="utf-8") != text:
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
        verdict = row.get("expectedStateHash", "REFUSED " + row.get("expectedRefusal", {}).get("reason", ""))
        print(f"  {verdict}  {row['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
