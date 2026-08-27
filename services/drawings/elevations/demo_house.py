"""A two-storey demo house, built through the **real** op fold. Test support only.

Why this exists, and when it should stop existing
-------------------------------------------------
The elevation and section projectors need a G+1 house with a stair, wet areas, openings on
all four faces, a balcony and a facade kit before they can be exercised at all.
``fixtures/plans/`` is the place that house is supposed to come from, and it is empty on
purpose: it is filled by Phase 3's solver corpus, and ``fixtures/plans/README.md`` is blunt
about why a hand-written stand-in must never be dressed up as a golden.

So this is not a golden and not a fixture file. It is a **builder**: a list of ops folded by
``garh_model.fold``, exactly as the seeder and the model-core tests build theirs
(``garh_model.testing``). That matters for three reasons:

* every wall, opening and stair passes the real op validation, so the geometry the
  projectors are tested against is geometry the product can actually contain;
* rooms and floor slabs are *derived* by the model core, not asserted here, so the
  footprints the elevations project are the real ones;
* when ``fixtures/plans/`` lands, deleting this module is a mechanical change — the tests
  swap ``demo_house()`` for a fixture load and nothing else moves.

The facade components deserve their own warning. ``facade.apply_kit`` carries the components
the *kit generator* produced, and that generator is TypeScript
(``apps/web/src/features/canvas/facade/generator.ts``). The components below mirror the
shape it emits for the ``contemporary`` kit — same kinds, same param names, same hosts — so
the callout code is exercised against realistic metadata. They are hand-written and are not
a substitute for running the generator; a golden of facade output has to come from the
generator itself.

Geometry (plot-local mm, ``+X`` east, ``+Y`` north, plot datum 0)
----------------------------------------------------------------
::

    y=9000  N ┌──────────────┬────────────┐   external walls 230
              │              │  BATH      │   internal walls  115
    y=7000    │  BEDROOM     ├────────────┤   plot: 30x40 ft Bengaluru (as seeded)
              │              │  KITCHEN   │
    y=5000    ├──────────────┼────────────┤
              │              │  STAIR     │
              │  LIVING      │  HALL      │
    y=0     S └──────────────┴────────────┘
              x=0          x=4000       x=7000

The first floor repeats the same partition lines with a study in place of the kitchen. The
stair sits in the right-hand band on both storeys, which is what lets the section find a cut
that runs **along** the flight *and* through the wet stack — the §7 ideal.
"""

from __future__ import annotations

import os
import sys
from typing import Any

__all__ = [
    "BUILDING_DEPTH_MM",
    "BUILDING_WIDTH_MM",
    "DEMO_IDS",
    "demo_house",
    "demo_material_names",
    "demo_project_doc",
    "ensure_model_importable",
]

#: Centreline extents of the external walls.
BUILDING_WIDTH_MM = 7_000
BUILDING_DEPTH_MM = 9_000

EXTERNAL_MM = 230
INTERNAL_MM = 115

STOREY_HEIGHT_MM = 3_000
#: 18 x 167 = 3006, which is inside the model's ±10mm stair/storey tolerance.
RISER_MM = 167
TREAD_MM = 275
RISERS = 18
STAIR_WIDTH_MM = 1_000
STAIR_ORIGIN = (4_300, 400)
STAIR_LANDING = {"widthMm": 2_115, "depthMm": 1_000}


def ensure_model_importable() -> None:
    """Put the repo root and ``apps/api`` on ``sys.path`` if they are not already.

    Same helper shape as ``services.solver.program._ensure_repo_on_path``: the workers ship
    with ``garh_model`` importable, but a bare ``python3 -m services.drawings.elevations``
    from a checkout does not have ``apps/api`` on the path, and a fixture builder that only
    runs under pytest is a fixture builder nobody runs.
    """
    try:  # pragma: no cover - import probe
        import garh_model as _probe  # noqa: F401

        return
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    for path in (root, os.path.join(root, "apps", "api")):
        if path not in sys.path:
            sys.path.insert(0, path)


def _ids() -> dict[str, str]:
    ensure_model_importable()
    from garh_model.testing import fixed_id

    tags = {
        "gf": ("storey", "GF"),
        "ff": ("storey", "FF"),
        # ground floor
        "gf_s": ("wall", "GFWS"),
        "gf_e": ("wall", "GFWE"),
        "gf_n": ("wall", "GFWN"),
        "gf_w": ("wall", "GFWW"),
        "gf_spine": ("wall", "GFSP"),
        "gf_cross_front": ("wall", "GFCF"),
        "gf_cross_rear": ("wall", "GFCR"),
        "gf_wet": ("wall", "GFWT"),
        "gf_door": ("opening", "GFD1"),
        "gf_win_e": ("opening", "GFW1"),
        "gf_win_n": ("opening", "GFW2"),
        "gf_win_w": ("opening", "GFW3"),
        "gf_stair": ("stair", "GFST"),
        # first floor
        "ff_s": ("wall", "FFWS"),
        "ff_e": ("wall", "FFWE"),
        "ff_n": ("wall", "FFWN"),
        "ff_w": ("wall", "FFWW"),
        "ff_spine": ("wall", "FFSP"),
        "ff_cross_front": ("wall", "FFCF"),
        "ff_cross_rear": ("wall", "FFCR"),
        "ff_wet": ("wall", "FFWT"),
        "ff_door_balcony": ("opening", "FFD2"),
        "ff_win_s": ("opening", "FFW4"),
        "ff_win_e": ("opening", "FFW5"),
        "ff_win_n": ("opening", "FFW6"),
        "ff_win_w": ("opening", "FFW7"),
        "ff_stair": ("stair", "FFST"),
        "balcony": ("balcony", "FFB1"),
        # facade + materials
        "fc_porch": ("facadecomp", "FCP1"),
        "fc_cladding": ("facadecomp", "FCC1"),
        "fc_parapet": ("facadecomp", "FCPP"),
        "fc_railing": ("facadecomp", "FCR1"),
        "mat_wall": ("material", "MW1"),
        "mat_parapet": ("material", "MP1"),
    }
    return {key: fixed_id(kind, tag) for key, (kind, tag) in tags.items()}


#: Stable ids for everything the tests need to name.
DEMO_IDS: dict[str, str] = {}


def _pt(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _wall(
    op: Any,
    wid: str,
    storey: str,
    a: tuple[int, int],
    b: tuple[int, int],
    thickness: int,
    kind: str,
) -> Any:
    return op(
        "wall.add",
        id=wid,
        storeyId=storey,
        a=_pt(*a),
        b=_pt(*b),
        thicknessMm=thickness,
        kind=kind,
    )


def _shell_ops(op: Any, ids: dict[str, str], storey_key: str, prefix: str) -> list[Any]:
    """The four external walls plus the three partitions, for one storey."""
    storey = ids[storey_key]
    w, d = BUILDING_WIDTH_MM, BUILDING_DEPTH_MM
    return [
        _wall(op, ids[prefix + "_s"], storey, (0, 0), (w, 0), EXTERNAL_MM, "external"),
        _wall(op, ids[prefix + "_e"], storey, (w, 0), (w, d), EXTERNAL_MM, "external"),
        _wall(op, ids[prefix + "_n"], storey, (w, d), (0, d), EXTERNAL_MM, "external"),
        _wall(op, ids[prefix + "_w"], storey, (0, d), (0, 0), EXTERNAL_MM, "external"),
        _wall(op, ids[prefix + "_spine"], storey, (0, 5_000), (w, 5_000), INTERNAL_MM, "internal"),
        _wall(
            op,
            ids[prefix + "_cross_front"],
            storey,
            (4_000, 0),
            (4_000, 5_000),
            INTERNAL_MM,
            "internal",
        ),
        _wall(
            op,
            ids[prefix + "_cross_rear"],
            storey,
            (4_000, 5_000),
            (4_000, d),
            INTERNAL_MM,
            "internal",
        ),
        _wall(
            op, ids[prefix + "_wet"], storey, (4_000, 7_000), (w, 7_000), INTERNAL_MM, "internal"
        ),
    ]


def _opening(
    op: Any, oid: str, wall: str, kind: str, width: int, height: int, sill: int, offset: int
) -> Any:
    return op(
        "opening.add",
        id=oid,
        wallId=wall,
        kind=kind,
        widthMm=width,
        heightMm=height,
        sillMm=sill,
        offsetMm=offset,
        swing="in-left",
    )


def _facade_components(ids: dict[str, str]) -> list[dict[str, Any]]:
    """Components in the shape ``generateFacadeComponents`` emits for ``contemporary``.

    Window trims and chajjas are minted per opening by the real generator; the four kept
    here are the ones that exercise every anchor path in
    :mod:`services.drawings.elevations.callouts` — an opening-hosted component, a
    wall-hosted one with an ``offsetMm``, a balcony-hosted railing, and a building-wide
    parapet profile with no host at all.
    """
    return [
        {
            "id": ids["fc_porch"],
            "kind": "porch",
            "storeyId": ids["gf"],
            "wallId": ids["gf_s"],
            "openingId": ids["gf_door"],
            "params": {
                "style": "cantilever",
                "projectionMm": 1_800,
                "thicknessMm": 200,
                "widthMm": 1_600,
                "colorHex": "#7A5230",
            },
        },
        {
            "id": ids["fc_cladding"],
            "kind": "cladding_zone",
            "storeyId": None,
            "wallId": ids["gf_e"],
            "openingId": None,
            "params": {
                "rule": "stack full-height at entry bay",
                "materialId": "wpc-cladding",
                "widthMm": 1_200,
                "offsetMm": 2_000,
                "colorHex": "#7A5230",
            },
        },
        {
            "id": ids["fc_parapet"],
            "kind": "parapet_profile",
            "storeyId": ids["ff"],
            "wallId": None,
            "openingId": None,
            "params": {
                "style": "banded",
                "heightMm": 1_050,
                "capThicknessMm": 75,
                "colorHex": "#2E2E2E",
            },
        },
        {
            "id": ids["fc_railing"],
            "kind": "railing",
            "storeyId": ids["ff"],
            "wallId": None,
            "openingId": None,
            "params": {
                "balconyId": ids["balcony"],
                "style": "ms-slim",
                "heightMm": 1_050,
                "materialId": "ms-railing",
                "colorHex": "#2E2E2E",
            },
        },
    ]


def demo_ops() -> list[Any]:
    """Every op, in order. Folding this list is the whole fixture."""
    ensure_model_importable()
    from garh_model.model import DEFAULTS
    from garh_model.ops import op
    from garh_model.testing import DEMO_PLOT_POLYGON

    ids = _ids()
    DEMO_IDS.clear()
    DEMO_IDS.update(ids)
    ops: list[Any] = [
        op("plot.set_boundary", polygon=list(DEMO_PLOT_POLYGON), source="seed"),
        op("plot.set_north", deg=0),
        op("plot.set_road", edgeIndex=0, widthMm=9_000, name="9m Road"),
        op("storey.add", id=ids["gf"], index=0, name="Ground Floor", heightMm=STOREY_HEIGHT_MM),
        op("storey.add", id=ids["ff"], index=1, name="First Floor", heightMm=STOREY_HEIGHT_MM),
    ]
    ops.extend(_shell_ops(op, ids, "gf", "gf"))
    ops.extend(_shell_ops(op, ids, "ff", "ff"))

    # ---- ground floor openings: one on every face -------------------------
    ops.extend(
        [
            _opening(
                op, ids["gf_door"], ids["gf_s"], "door", 1_000, DEFAULTS.door_height_mm, 0, 2_000
            ),
            _opening(op, ids["gf_win_e"], ids["gf_e"], "window", 1_500, 1_200, 900, 2_500),
            _opening(op, ids["gf_win_n"], ids["gf_n"], "window", 900, 1_200, 900, 2_000),
            _opening(op, ids["gf_win_w"], ids["gf_w"], "window", 1_500, 1_200, 900, 3_000),
        ]
    )
    # ---- first floor openings + the balcony door --------------------------
    ops.extend(
        [
            _opening(
                op,
                ids["ff_door_balcony"],
                ids["ff_s"],
                "door",
                900,
                DEFAULTS.door_height_mm,
                0,
                2_200,
            ),
            _opening(op, ids["ff_win_s"], ids["ff_s"], "window", 1_800, 1_200, 900, 5_000),
            _opening(op, ids["ff_win_e"], ids["ff_e"], "window", 1_500, 1_200, 900, 2_500),
            _opening(op, ids["ff_win_n"], ids["ff_n"], "window", 900, 1_200, 900, 2_000),
            _opening(op, ids["ff_win_w"], ids["ff_w"], "window", 1_500, 1_200, 900, 3_000),
        ]
    )
    # ---- stairs: ground → first, first → terrace (the mumty) --------------
    for key, storey_key in (("gf_stair", "gf"), ("ff_stair", "ff")):
        ops.append(
            op(
                "stair.add",
                id=ids[key],
                storeyId=ids[storey_key],
                kind="dogleg",
                origin=_pt(*STAIR_ORIGIN),
                direction="N",
                riserMm=RISER_MM,
                treadMm=TREAD_MM,
                widthMm=STAIR_WIDTH_MM,
                risersCount=RISERS,
                landing=dict(STAIR_LANDING),
            )
        )
    # ---- balcony on the south face of the first floor ---------------------
    ops.append(
        op(
            "balcony.set",
            action="add",
            id=ids["balcony"],
            storeyId=ids["ff"],
            polygon=[_pt(1_200, -1_015), _pt(3_200, -1_015), _pt(3_200, -115), _pt(1_200, -115)],
            railingKind="ms",
            railingHeightMm=1_000,
            projectionMm=900,
            slabThicknessMm=125,
        )
    )
    # ---- levels: the numbers every vertical drawing reads -----------------
    ops.append(
        op(
            "levels.set",
            plinthMm=600,
            sillDefaultMm=900,
            lintelDefaultMm=2_100,
            parapetMm=1_000,
        )
    )
    return ops


#: (storey key, x range, y range) → room type. Applied after the fold, because room ids
#: are *derived* by the model core and cannot be known before it runs.
_ROOM_PROGRAMME: tuple[tuple[str, tuple[int, int], tuple[int, int], str, str], ...] = (
    ("gf", (0, 4_000), (0, 5_000), "living", "Living"),
    ("gf", (4_000, 7_000), (0, 5_000), "staircase", "Stair Hall"),
    ("gf", (0, 4_000), (5_000, 9_000), "bedroom_master", "Master Bedroom"),
    ("gf", (4_000, 7_000), (5_000, 7_000), "kitchen", "Kitchen"),
    ("gf", (4_000, 7_000), (7_000, 9_000), "bath", "Bath"),
    ("ff", (0, 4_000), (0, 5_000), "bedroom", "Bedroom 2"),
    ("ff", (4_000, 7_000), (0, 5_000), "staircase", "Stair Hall"),
    ("ff", (0, 4_000), (5_000, 9_000), "bedroom", "Bedroom 3"),
    ("ff", (4_000, 7_000), (5_000, 7_000), "study", "Study"),
    ("ff", (4_000, 7_000), (7_000, 9_000), "bath", "Bath"),
)


def _room_programme_ops(doc: Any, ids: dict[str, str]) -> list[Any]:
    from garh_model.ops import op

    storey_of = {"gf": ids["gf"], "ff": ids["ff"]}
    out: list[Any] = []
    for room in doc.house.rooms:
        xs = [p.x for p in room.polygon]
        ys = [p.y for p in room.polygon]
        cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
        for storey_key, (x_lo, x_hi), (y_lo, y_hi), room_type, name in _ROOM_PROGRAMME:
            if room.storey_id != storey_of[storey_key]:
                continue
            if x_lo <= cx <= x_hi and y_lo <= cy <= y_hi:
                out.append(
                    op("room.assign", roomId=room.id, type=room_type, name=name, locked=False)
                )
                break
    return out


def demo_project_doc() -> Any:
    """The folded ``ProjectDoc`` — plot, brief, house, annotations.

    Built in four groups because two of the ops need the *result* of the ones before them:
    room types need derived room ids, and the facade kit is an atomic op of its own.
    """
    ensure_model_importable()
    from garh_model.fold import apply_group
    from garh_model.model import empty_project_doc
    from garh_model.ops import op

    doc = apply_group(empty_project_doc("ft-in"), demo_ops()).model
    ids = dict(DEMO_IDS)
    doc = apply_group(doc, _room_programme_ops(doc, ids)).model
    doc = apply_group(
        doc,
        [
            op(
                "facade.apply_kit",
                kitId="contemporary",
                seed=7,
                colorwayId="mono-wood",
                components=_facade_components(ids),
            )
        ],
    ).model
    doc = apply_group(
        doc,
        [
            op(
                "material.assign",
                id=ids["mat_wall"],
                target={"group": "external_wall", "storeyId": None, "elementId": None},
                materialId="exterior-texture",
            ),
            op(
                "material.assign",
                id=ids["mat_parapet"],
                target={"group": "parapet", "storeyId": None, "elementId": None},
                materialId="exterior-texture",
            ),
        ],
    ).model
    return doc


def demo_house() -> Any:
    """The folded :class:`~garh_model.model.HouseModel` — what a projector consumes."""
    return demo_project_doc().house


def demo_material_names(catalog_path: str | None = None) -> dict[str, str]:
    """Material id → display name, read from ``fixtures/catalog/materials.json``.

    Optional everywhere it is used: a missing catalogue means callouts print material ids,
    never that a callout disappears.
    """
    import json

    if catalog_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, "..", "..", ".."))
        catalog_path = os.path.join(root, "fixtures", "catalog", "materials.json")
    try:
        with open(catalog_path, encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {str(item["id"]): str(item["name"]) for item in entries if "id" in item}
