"""Plan fixtures for the auto-dimensioning engine.

Test support that lives in the package (the same call ``garh_model/testing.py`` makes)
so the smoke runner, the unit tests and any future golden generator all dimension the
*same* plans. A fixture that only one caller can build is a fixture that drifts.

**The multi-room plans are folded by the real model core.** They are op logs —
``wall.add``, ``opening.add``, ``room.assign`` — handed to ``garh_model.fold``, so their
rooms come out of the real planar-subdivision room detector with the real
``thickness_mm // 2`` face insets, not out of a dictionary somebody typed. That matters
here more than anywhere: the engine's whole job is to print the numbers a reader will
scale off those faces, and a hand-written fixture would let the engine agree with a
fiction.

The one hand-written fixture is :func:`json_plan_with_diagonal`, and it is hand-written
on purpose: it is wire JSON rather than dataclasses (proving the adapter accepts both
shapes) and it contains a diagonal wall (proving non-orthogonal walls are skipped rather
than mis-dimensioned). The model core would happily fold it too — but then the fixture
would depend on ``apps/api`` being importable, and the point of it is that the engine
needs nothing at all.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

#: 230mm external masonry, 115mm partitions: the Indian brick module, and the two
#: thicknesses every catalogue plan uses.
EXTERNAL_MM = 230
PARTITION_MM = 115


class _ModelCore:
    """The three ``garh_model`` entry points these fixtures use.

    Named explicitly rather than reached through the package, because
    ``garh_model.__init__`` re-exports a *function* called ``fold`` over the module of
    the same name — ``garh_model.fold.apply_group`` is an ``AttributeError`` waiting to
    happen.
    """

    __slots__ = ("apply_group", "op", "make_empty_doc", "fixed_id", "two_room_openings")

    def __init__(self) -> None:
        from garh_model.fold import apply_group
        from garh_model.ops import op
        from garh_model.testing import (
            fixed_id,
            make_empty_doc,
            make_two_room_plan_with_openings,
        )

        self.apply_group = apply_group
        self.op = op
        self.make_empty_doc = make_empty_doc
        self.fixed_id = fixed_id
        self.two_room_openings = make_two_room_plan_with_openings


def _require_model_core() -> _ModelCore:
    """Load ``garh_model``, with an error that says how to fix the path.

    ``apps/api`` is on ``sys.path`` in the API process, in ``pytest`` (via conftest) and
    in ``scripts/*`` (via their bootstrap). Anywhere else, this is the message that
    saves ten minutes.
    """
    try:
        return _ModelCore()
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise ImportError(
            "garh_model is not importable: add <repo>/apps/api to sys.path (see "
            "services/solver/tests/conftest.py or scripts/solver_smoke.py for the "
            "established bootstrap). The engine itself needs no imports — only these "
            "op-log fixtures do."
        ) from exc


def _fold(op_list: Sequence[Any]) -> Any:
    """Fold an op log into a ``ProjectDoc``. ``apply_group`` returns a result envelope
    (model + inverse ops), so the document is ``.model``."""
    core = _require_model_core()
    return core.apply_group(core.make_empty_doc(), list(op_list)).model


def _op(op_type: str, **kwargs: Any) -> Any:
    """``garh_model.ops.op`` with a keyword name that cannot clash with ``kind=``."""
    core = _require_model_core()
    return core.op(op_type, **kwargs)


def _fixed_id(element_type: str, tag: str) -> str:
    core = _require_model_core()
    return core.fixed_id(element_type, tag)


def _pt(x: int, y: int) -> Dict[str, int]:
    return {"x": x, "y": y}


def _wall(
    tag: str,
    storey_id: str,
    a: Tuple[int, int],
    b: Tuple[int, int],
    thickness_mm: int,
    kind: str,
) -> Any:
    return _op(
        "wall.add",
        id=_fixed_id("wall", tag),
        storeyId=storey_id,
        a=_pt(*a),
        b=_pt(*b),
        thicknessMm=thickness_mm,
        kind=kind,
    )


def _opening(
    tag: str,
    wall_tag: str,
    kind: str,
    width_mm: int,
    height_mm: int,
    sill_mm: int,
    offset_mm: int,
) -> Any:
    return _op(
        "opening.add",
        id=_fixed_id("opening", tag),
        wallId=_fixed_id("wall", wall_tag),
        kind=kind,
        widthMm=width_mm,
        heightMm=height_mm,
        sillMm=sill_mm,
        offsetMm=offset_mm,
        swing="in-left",
    )


def _assign_rooms(
    house: Any, wanted: Sequence[Tuple[int, int, str, str]]
) -> List[Any]:
    """Build ``room.assign`` ops by matching each room's centroid to a table row.

    Room ids are *derived* — they fall out of the planar subdivision, so a fixture
    cannot know them in advance (``fixtures/model/README.md`` explains why they are also
    history-dependent). Matching on the containing point is stable regardless.
    """
    out: List[Any] = []
    for x, y, room_type, name in wanted:
        for room in house.rooms:
            xs = [p.x for p in room.polygon]
            ys = [p.y for p in room.polygon]
            if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys):
                out.append(
                    _op(
                        "room.assign",
                        roomId=room.id,
                        type=room_type,
                        name=name,
                        tags=[],
                        locked=False,
                    )
                )
                break
    return out


# ---------------------------------------------------------------------------
# Fixture 1 — the demo project's ground floor: a real G+1 3BHK, 30x40 ft plot
# ---------------------------------------------------------------------------
#: Grid lines of :func:`demo_3bhk_ground`, in model mm. Setbacks off the 9144x12192
#: demo plot leave a 6750 x 7650 footprint (centreline to centreline).
BHK3_X = (1200, 4300, 6100, 7950)
BHK3_Y = (3000, 6900, 8900, 10650)


def demo_3bhk_ground() -> Any:
    """The ground floor of the seeded demo project: seven rooms, fourteen openings.

    ::

        y=10650  +---------------+--------+-------+   N
                 |    dining     |  bath  |  wc   |
        y= 8900  +---------------+--------+-------+
                 |    kitchen    |     stair      |
        y= 6900  +---------------+----------------+
                 |               |                |
                 |    living     |    bedroom     |
        y= 3000  +---------------+----------------+
                x=1200         4300     6100    7950

    External walls 230mm, partitions 115mm. Doors and windows on all four facades so
    every side produces a level 3 chain, and five internal doors so the inner chains
    have real door-side walls to hug.
    """
    storey = _fixed_id("storey", "GF")
    ops: List[Any] = [
        _op(
            "plot.set_boundary",
            polygon=[_pt(0, 0), _pt(9144, 0), _pt(9144, 12192), _pt(0, 12192)],
            source="seed",
        ),
        _op("plot.set_north", deg=0),
        _op("plot.set_road", edgeIndex=0, widthMm=9000, name="9m Road"),
        _op("storey.add", id=storey, index=0, name="Ground Floor", heightMm=3000),
        # Envelope, drawn anticlockwise from the south-west corner.
        _wall("S", storey, (1200, 3000), (7950, 3000), EXTERNAL_MM, "external"),
        _wall("E", storey, (7950, 3000), (7950, 10650), EXTERNAL_MM, "external"),
        _wall("N", storey, (7950, 10650), (1200, 10650), EXTERNAL_MM, "external"),
        _wall("W", storey, (1200, 10650), (1200, 3000), EXTERNAL_MM, "external"),
        # Partitions.
        _wall("PV1", storey, (4300, 3000), (4300, 10650), PARTITION_MM, "internal"),
        _wall("PH1", storey, (1200, 6900), (7950, 6900), PARTITION_MM, "internal"),
        _wall("PH2", storey, (1200, 8900), (7950, 8900), PARTITION_MM, "internal"),
        _wall("PV2", storey, (6100, 8900), (6100, 10650), PARTITION_MM, "internal"),
        # South facade: main door at x=2700, bedroom window at x=6100.
        _opening("D1", "S", "door", 900, 2100, 0, 1500),
        _opening("W1", "S", "window", 1500, 1200, 900, 4900),
        # East facade (a=(7950,3000), so offsets run north).
        _opening("W2", "E", "window", 1200, 1200, 900, 1900),
        _opening("W3", "E", "window", 900, 1200, 900, 4900),
        _opening("V1", "E", "ventilator", 600, 450, 1800, 6700),
        # North facade (a=(7950,10650), so offsets run west).
        _opening("W4", "N", "window", 1200, 1200, 900, 5250),
        _opening("V2", "N", "ventilator", 600, 450, 1800, 2750),
        # West facade (a=(1200,10650), so offsets run south).
        _opening("W5", "W", "window", 1500, 1200, 900, 5650),
        _opening("W6", "W", "window", 1200, 1200, 900, 2750),
        # Internal doors — these decide which face each room's inner chains hug.
        _opening("D2", "PV1", "door", 900, 2100, 0, 2000),
        _opening("D3", "PH1", "door", 900, 2100, 0, 800),
        _opening("D4", "PH2", "door", 900, 2100, 0, 800),
        _opening("D5", "PH2", "door", 750, 2100, 0, 4000),
        _opening("D6", "PH2", "door", 750, 2100, 0, 5800),
    ]
    doc = _fold(ops)
    named = _assign_rooms(
        doc.house,
        (
            (2700, 5000, "living", "Living"),
            (6100, 5000, "bedroom_master", "Master Bedroom"),
            (2700, 7900, "kitchen", "Kitchen"),
            (6100, 7900, "staircase", "Stair"),
            (2700, 9700, "dining", "Dining"),
            (5200, 9700, "bath", "Bath"),
            (7000, 9700, "wc", "W.C."),
        ),
    )
    core = _require_model_core()
    return core.apply_group(doc, named).model.house


def l_shaped_plan() -> Any:
    """An L footprint: two rooms, a jog on the north and east sides.

    Exercises the parts of §7 a rectangle cannot: facade occlusion (the recessed leg is
    still north-facing and its window belongs on the north chain), jog breakpoints on
    level 2, and a non-rectangular room dimensioned to its bounding box with a note.
    """
    storey = _fixed_id("storey", "LGF")
    ops: List[Any] = [
        _op("storey.add", id=storey, index=0, name="Ground Floor", heightMm=3000),
        _wall("LS", storey, (1200, 3000), (7950, 3000), EXTERNAL_MM, "external"),
        _wall("LE", storey, (7950, 3000), (7950, 8500), EXTERNAL_MM, "external"),
        _wall("LJH", storey, (7950, 8500), (5500, 8500), EXTERNAL_MM, "external"),
        _wall("LJV", storey, (5500, 8500), (5500, 10650), EXTERNAL_MM, "external"),
        _wall("LN", storey, (5500, 10650), (1200, 10650), EXTERNAL_MM, "external"),
        _wall("LW", storey, (1200, 10650), (1200, 3000), EXTERNAL_MM, "external"),
        _wall("LPH", storey, (1200, 6800), (7950, 6800), PARTITION_MM, "internal"),
        _opening("LD1", "LS", "door", 900, 2100, 0, 1500),
        _opening("LW1", "LE", "window", 1200, 1200, 900, 2000),
        _opening("LW2", "LJH", "window", 1200, 1200, 900, 1250),
        _opening("LW3", "LN", "window", 1200, 1200, 900, 2500),
        _opening("LD2", "LPH", "door", 900, 2100, 0, 1500),
    ]
    doc = _fold(ops)
    named = _assign_rooms(
        doc.house,
        ((4000, 5000, "living_dining", "Living / Dining"), (3000, 9000, "bedroom", "Bedroom")),
    )
    core = _require_model_core()
    return core.apply_group(doc, named).model.house


def two_room_plan() -> Any:
    """``garh_model``'s own two-room fixture, with its door and window.

    Shared with the model core's golden states, so a dimension bug that is really a
    geometry bug shows up against a plan whose numbers are already pinned elsewhere.
    """
    core = _require_model_core()
    return core.two_room_openings().house


def storey_id_of(house: Any, index: int = 0) -> str:
    return str(house.storeys[index].id)


# ---------------------------------------------------------------------------
# Fixture 2 — wire JSON, no model core, one diagonal wall
# ---------------------------------------------------------------------------
def json_plan_with_diagonal(storey_id: str = "storey_JSON") -> Mapping[str, Any]:
    """A single room in wire JSON, plus one diagonal wall the engine must skip.

    Deliberately dependency-free: this is the fixture that proves ``autodim`` runs on a
    bare interpreter with nothing but the standard library on the path.
    """
    return {
        "storeys": [
            {
                "id": storey_id,
                "name": "Ground Floor",
                "heightMm": 3000,
                "level": {"fflMm": 600, "slabThicknessMm": 125},
            }
        ],
        "walls": [
            {
                "id": "wall_JS",
                "storeyId": storey_id,
                "a": {"x": 0, "y": 0},
                "b": {"x": 4000, "y": 0},
                "thicknessMm": EXTERNAL_MM,
                "kind": "external",
                "loadBearing": True,
            },
            {
                "id": "wall_JE",
                "storeyId": storey_id,
                "a": {"x": 4000, "y": 0},
                "b": {"x": 4000, "y": 3000},
                "thicknessMm": EXTERNAL_MM,
                "kind": "external",
                "loadBearing": True,
            },
            {
                "id": "wall_JN",
                "storeyId": storey_id,
                "a": {"x": 4000, "y": 3000},
                "b": {"x": 0, "y": 3000},
                "thicknessMm": EXTERNAL_MM,
                "kind": "external",
                "loadBearing": True,
            },
            {
                "id": "wall_JW",
                "storeyId": storey_id,
                "a": {"x": 0, "y": 3000},
                "b": {"x": 0, "y": 0},
                "thicknessMm": EXTERNAL_MM,
                "kind": "external",
                "loadBearing": True,
            },
            {
                "id": "wall_JDIAG",
                "storeyId": storey_id,
                "a": {"x": 0, "y": 0},
                "b": {"x": 2000, "y": 3000},
                "thicknessMm": PARTITION_MM,
                "kind": "internal",
                "loadBearing": False,
            },
        ],
        "openings": [
            {
                "id": "opening_JD1",
                "wallId": "wall_JS",
                "kind": "door",
                "widthMm": 900,
                "heightMm": 2100,
                "sillMm": 0,
                "offsetMm": 1200,
                "swing": "in-left",
                "tag": "D1",
            },
            {
                "id": "opening_JW1",
                "wallId": "wall_JS",
                "kind": "window",
                "widthMm": 1200,
                "heightMm": 1200,
                "sillMm": 900,
                "offsetMm": 2900,
                "swing": "in-left",
                "tag": "W1",
            },
        ],
        "rooms": [
            {
                "id": "room_JSON1",
                "storeyId": storey_id,
                "type": "living",
                "name": "Living",
                "polygon": [
                    {"x": 115, "y": 115},
                    {"x": 3885, "y": 115},
                    {"x": 3885, "y": 2885},
                    {"x": 115, "y": 2885},
                ],
                "areaMm2": 3770 * 2770,
                "tags": [],
                "locked": False,
            }
        ],
    }


#: Every plan the smoke report and the tests walk, as ``(name, builder)``.
#: JSON first so a broken model-core path still leaves something runnable.
def all_plans() -> Tuple[Tuple[str, Any, str], ...]:
    """``(name, house-or-json, storey_id)`` for every fixture, model core permitting."""
    out: List[Tuple[str, Any, str]] = []
    json_plan = json_plan_with_diagonal()
    out.append(("json-diagonal", json_plan, "storey_JSON"))
    for name, builder in (
        ("demo-3bhk-ground", demo_3bhk_ground),
        ("l-shaped", l_shaped_plan),
        ("two-room", two_room_plan),
    ):
        house = builder()
        out.append((name, house, storey_id_of(house)))
    return tuple(out)


def room_label_obstacles(house: Any, storey_id: str, *, box_mm: int = 1800) -> Tuple[Any, ...]:
    """Room-name blocks as the plan projector will place them: centred, per room.

    §7 step 4 says dims, text and symbols all register on one collision grid. The
    dimension engine does not own the room labels, so the caller passes them in; this
    helper builds the same boxes the projector will, which is what makes the smoke
    report's flip/shift/shrink/leader counts mean something.
    """
    from services.drawings.dimensions import LabelBox

    from services.drawings.autodim.extract import collect_rooms

    out = []
    for room in collect_rooms(house, storey_id):
        cx, cy = room.centre
        width = min(box_mm, max(400, room.width_mm - 200))
        height = min(box_mm // 3, max(200, room.depth_mm - 200))
        out.append(
            LabelBox(
                x_mm=cx - width // 2,
                y_mm=cy - height // 2,
                width_mm=width,
                height_mm=height,
                owner_id="roomlabel:%s" % room.id,
            )
        )
    return tuple(out)


__all__ = [
    "BHK3_X",
    "BHK3_Y",
    "EXTERNAL_MM",
    "PARTITION_MM",
    "all_plans",
    "demo_3bhk_ground",
    "json_plan_with_diagonal",
    "l_shaped_plan",
    "room_label_obstacles",
    "storey_id_of",
    "two_room_plan",
]
