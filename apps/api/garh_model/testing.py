"""testing.py — deterministic fixtures shared by this package's tests, by the
golden-state generator and by ``apps/api``'s seed script.

Mirror of ``packages/model/src/testing.ts``: SAME ids, SAME geometry, SAME op
order, so a state hash printed on one side is meaningful on the other and a
failure diffs readably.

This is test support, not product code. It lives in the package (rather than a
test folder) only so other modules can import it; nothing on a request path may.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .fold import apply_group
from .model import DEFAULTS, ProjectDoc, empty_project_doc
from .ops import Op, op

__all__ = [
    "fixed_id",
    "FIXTURE_IDS",
    "DEMO_PLOT_POLYGON",
    "make_empty_doc",
    "two_room_plan_ops",
    "make_two_room_plan",
    "opening_ops",
    "make_two_room_plan_with_openings",
]

_CROCKFORD_OK = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def fixed_id(element_type: str, tag: str) -> str:
    """A stable, readable, VALID element id: ``type_01J0000000000000000000TAG``.

    ``tag`` is upper-cased and stripped to the Crockford alphabet.
    """
    clean = "".join(ch for ch in tag.upper() if ch in _CROCKFORD_OK)
    body = ("01J" + "0" * 26)[: 26 - len(clean)] + clean
    return f"{element_type}_{body}"


#: Ids used by :func:`two_room_plan_ops`.
FIXTURE_IDS: Mapping[str, str] = {
    "groundStorey": fixed_id("storey", "GF"),
    "firstStorey": fixed_id("storey", "FF"),
    "wallSouth": fixed_id("wall", "WS"),
    "wallEast": fixed_id("wall", "WE"),
    "wallNorth": fixed_id("wall", "WN"),
    "wallWest": fixed_id("wall", "WW"),
    "wallSpine": fixed_id("wall", "WSP"),
    "doorMain": fixed_id("opening", "D1"),
    "windowWest": fixed_id("opening", "W1"),
    "stair": fixed_id("stair", "ST1"),
    "column": fixed_id("column", "C1"),
    "sofa": fixed_id("furniture", "FS1"),
    "balcony": fixed_id("balcony", "B1"),
    "material": fixed_id("material", "M1"),
    "annotation": fixed_id("annotation", "A1"),
    "sheet": fixed_id("sheet", "SH1"),
}

#: The demo plot: 30 x 40 ft (9144 x 12192 mm) Bengaluru plot, north up.
DEMO_PLOT_POLYGON: List[Dict[str, int]] = [
    {"x": 0, "y": 0},
    {"x": 9144, "y": 0},
    {"x": 9144, "y": 12192},
    {"x": 0, "y": 12192},
]


def make_empty_doc() -> ProjectDoc:
    """An empty document with ft-in display: the state every op log folds from."""
    return empty_project_doc("ft-in")


def _pt(x: int, y: int) -> Dict[str, int]:
    return {"x": x, "y": y}


def two_room_plan_ops() -> List[Op]:
    """Ops that build a ground floor with TWO rooms.

    .. code-block:: text

        (0,4000) +-----------+-----------+ (6000,4000)
                 |           |           |
                 |  room A   |  room B   |   external walls 230mm
                 |           |           |   spine wall     115mm
           (0,0) +-----------+-----------+ (6000,0)
                         x = 3000

    Clear areas (centreline face inset by half thickness):
    A = (2943-115) x (3885-115) = 2828 x 3770 = 10_661_560 mm^2, B the same.
    """
    s = FIXTURE_IDS["groundStorey"]
    return [
        op("plot.set_boundary", polygon=list(DEMO_PLOT_POLYGON), source="seed"),
        op("plot.set_north", deg=0),
        op("plot.set_road", edgeIndex=0, widthMm=9000, name="9m Road"),
        op(
            "storey.add",
            id=s,
            index=0,
            name="Ground Floor",
            heightMm=DEFAULTS.storey_height_mm,
        ),
        op(
            "wall.add",
            id=FIXTURE_IDS["wallSouth"],
            storeyId=s,
            a=_pt(0, 0),
            b=_pt(6000, 0),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=FIXTURE_IDS["wallEast"],
            storeyId=s,
            a=_pt(6000, 0),
            b=_pt(6000, 4000),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=FIXTURE_IDS["wallNorth"],
            storeyId=s,
            a=_pt(6000, 4000),
            b=_pt(0, 4000),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=FIXTURE_IDS["wallWest"],
            storeyId=s,
            a=_pt(0, 4000),
            b=_pt(0, 0),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=FIXTURE_IDS["wallSpine"],
            storeyId=s,
            a=_pt(3000, 0),
            b=_pt(3000, 4000),
            thicknessMm=115,
            kind="internal",
        ),
    ]


def make_two_room_plan() -> ProjectDoc:
    """The two-room plan, already folded."""
    return apply_group(make_empty_doc(), two_room_plan_ops()).model


def opening_ops() -> List[Op]:
    """A main door on the south wall and a window on the west wall."""
    return [
        op(
            "opening.add",
            id=FIXTURE_IDS["doorMain"],
            wallId=FIXTURE_IDS["wallSouth"],
            kind="door",
            widthMm=DEFAULTS.door_width_mm,
            heightMm=DEFAULTS.door_height_mm,
            sillMm=0,
            offsetMm=1500,
            swing="in-left",
        ),
        op(
            "opening.add",
            id=FIXTURE_IDS["windowWest"],
            wallId=FIXTURE_IDS["wallWest"],
            kind="window",
            widthMm=DEFAULTS.window_width_mm,
            heightMm=DEFAULTS.window_height_mm,
            sillMm=DEFAULTS.sill_default_mm,
            offsetMm=2000,
            swing="in-left",
        ),
    ]


def make_two_room_plan_with_openings() -> ProjectDoc:
    """The two-room plan plus a main door on the south wall and a west window."""
    doc = make_two_room_plan()
    return apply_group(doc, opening_ops()).model


def ops_to_json(ops: List[Op]) -> List[Dict[str, Any]]:
    """Wire form of an op list — what ``fixtures/model/golden-states.json`` stores."""
    return [o.to_json() for o in ops]
