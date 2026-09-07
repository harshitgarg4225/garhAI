"""Model -> sheet drawings: the renderers' reference input. **Fully implemented, pure.**

READ THIS BEFORE USING IT ELSEWHERE
-----------------------------------
This module is **not** the §7 projection and auto-dimensioning engine. That engine is
``services/drawings/dimensions.py`` plus the sheet builder that consumes it, and it is
owned by the drawings-engine work, not by the renderers.

What this is: a deterministic, dependency-free projector that turns a folded
:class:`garh_model.model.ProjectDoc` into :class:`~...primitives.SheetDrawing` objects
covering all six F7-A sheet kinds. It exists for two reasons, both about not shipping
untested code:

1. **The renderers need a real input to be provable.** Without it, "primitives -> SVG"
   is a function nobody has run over a real building, and the §16 golden corpus is a
   directory of files nobody generated. With it, ``scripts/sheet_goldens.py`` runs
   end-to-end on a bare Python 3.9 with no packages installed.
2. **It pins the primitive contract in runnable code.** When the §7 engine lands, the
   contract it must satisfy is not a paragraph in a docstring — it is this module's
   output, and the goldens say what changed.

The handover, concretely: the golden harness takes ``--source`` (``reference`` today,
``engine`` once the §7 engine exists), and the goldens get regenerated in the same
commit that switches it, with a note — golden rule 10.

WHAT IS GENUINELY §7-CORRECT HERE
---------------------------------
* **Dimension chains.** Built from sorted breakpoint lists, so ``Σ segments == overall``
  is true *by construction*, not by correction (§7 step 5). Opening chains dimension to
  the centreline, with the ``dim_to_jamb`` flag §7 asks for.
* **Every number is integer millimetres**, straight off the model.
* **The area statement comes from the rules engine**, via
  :func:`garh_rules.areas.area_statement` — the same evaluation that produced the
  compliance results. Nothing here recomputes a FAR, a coverage or a setback.
* **Wall projection** is §7's: double lines with the openings breaking them, solid poché
  on external walls, door arc + leaf, window triple line.

WHAT IS DELIBERATELY SIMPLER THAN §7
------------------------------------
Stated plainly so nobody mistakes it for the finished engine:

* Orthogonal walls only (which is also the MVP envelope, §5).
* Label placement is fixed-offset, not §7 step 4's collision-solving greedy placer;
  :func:`~services.drawings.dimensions.find_label_collisions` is available to check the
  result and the golden harness reports it.
* Inner room dimensions are one width + one depth per room, without the
  shared-wall duplicate suppression.
* Elevations project openings on the facade plane; no facade-kit component geometry
  beyond material callouts when a kit has been applied.
* The section cut is placed through the stair when there is one, else through the
  building's centre — no wet-area preference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.drawings.dimensions import (
    DEFAULT_DIM_TO_JAMB,
    LEVEL_1_OFFSET_MM,
    LEVEL_2_OFFSET_MM,
    LEVEL_3_OFFSET_MM,
    DimChain,
    DimSegment,
    assert_chains_sum,
)
from services.drawings.layers import (
    A_AREA,
    A_DIM,
    A_DOOR,
    A_STAIR,
    A_TEXT,
    A_WALL,
    A_WALL_PART,
    A_WIND,
)
from services.drawings.render.frame import frame_group
from services.drawings.render.labels import Box, first_free, paper_to_model_mm, text_box_mm
from services.drawings.render.layout import choose_scale, content_rect, fit_placement
from services.drawings.render.primitives import (
    HATCH_DIAGONAL,
    HATCH_EARTH,
    HATCH_SOLID,
    STYLE_CENTRE,
    STYLE_DASHED,
    STYLE_HIDDEN,
    TEXT_HEIGHT_LABEL_PAPER_UM,
    TEXT_HEIGHT_PAPER_UM,
    TEXT_HEIGHT_SMALL_PAPER_UM,
    Arc,
    Circle,
    Dim,
    DrawingGroup,
    Hatch,
    Line,
    Placement,
    Polyline,
    Primitive,
    Pt2,
    SheetDrawing,
    Text,
    div_round,
)
from services.drawings.render.tables import (
    area_statement_group,
    area_statement_height_mm,
    schedule_group,
)
from services.drawings.revisions import (
    ModelDiff,
    RevisionHistory,
    revision_marks,
    revision_register_group,
)
from services.drawings.schedules.door_window import DoorWindowSchedule, build_schedule
from services.drawings.sections.stair import stair_geometry
from services.drawings.sheets import (
    DEFAULT_SCALE,
    DEFAULT_SHEET_LAYOUT,
    Scale,
    ScheduleRow,
    Sheet,
    SheetLayout,
    TitleBlock,
    Viewport,
)

__all__ = [
    "FOUNDATION_DEPTH_BELOW_PLINTH_MM",
    "SheetSet",
    "build_schedule_rows",
    "build_sheet_set",
    "carpet_lines_for",
    "door_window_schedule",
    "elevation_sheet",
    "floor_plan_sheet",
    "opening_tag_map",
    "outer_chains",
    "inner_chains",
    "room_label_lines",
    "section_sheet",
    "site_plan_sheet",
]

#: §7: "foundation indicative line (900mm below plinth, dashed, labeled ...)".
FOUNDATION_DEPTH_BELOW_PLINTH_MM = 900
FOUNDATION_NOTE = "INDICATIVE - REFER STRUCTURAL"

#: Paper µm. Height of a level-marker triangle on a section/elevation.
_LEVEL_TICK_PAPER_UM = 1_800


# ---------------------------------------------------------------------------
# Small model helpers. All integer, all orthogonal-only.
# ---------------------------------------------------------------------------
def _is_horizontal(wall: Any) -> bool:
    return wall.a.y == wall.b.y


def _is_vertical(wall: Any) -> bool:
    return wall.a.x == wall.b.x


def _wall_axis_span(wall: Any) -> tuple[int, int]:
    """``(lo, hi)`` of the wall along its own axis."""
    if _is_horizontal(wall):
        return (min(wall.a.x, wall.b.x), max(wall.a.x, wall.b.x))
    return (min(wall.a.y, wall.b.y), max(wall.a.y, wall.b.y))


def _wall_line_mm(wall: Any) -> int:
    """The wall's centreline coordinate on its perpendicular axis."""
    return wall.a.y if _is_horizontal(wall) else wall.a.x


def _half(thickness_mm: int) -> int:
    """Half a wall thickness, floored.

    Floored on both faces, matching what the model core's room detection does (a 115
    wall yields faces 57 mm each side of the centreline, so a room polygon and this
    drawing agree to the millimetre). An odd thickness therefore draws 1 mm thinner than
    it is; that is 0.01 mm on a 1:100 print, and agreeing with the room polygons matters
    more than the last micron of a wall that will be built out of 115 mm bricks anyway.
    Dimensions never come from here — they come from centrelines and outer faces.
    """
    return thickness_mm // 2


def _wall_rect(wall: Any, lo: int, hi: int) -> tuple[Pt2, ...]:
    """The footprint ring of a wall span, from ``lo`` to ``hi`` along its axis."""
    half = _half(wall.thickness_mm)
    line = _wall_line_mm(wall)
    if _is_horizontal(wall):
        return ((lo, line - half), (hi, line - half), (hi, line + half), (lo, line + half))
    return ((line - half, lo), (line - half, hi), (line + half, hi), (line + half, lo))


def _walls_of(house: Any, storey_id: str) -> list[Any]:
    return [w for w in house.walls if w.storey_id == storey_id]


def _orthogonal_only(walls: Sequence[Any]) -> list[Any]:
    return [w for w in walls if _is_horizontal(w) or _is_vertical(w)]


def _openings_of_wall(house: Any, wall_id: str) -> list[Any]:
    return sorted((o for o in house.openings if o.wall_id == wall_id), key=lambda o: o.offset_mm)


def _strip_rows(
    revisions: Sequence[tuple[str, str, str]],
    register: RevisionHistory | None,
) -> tuple[tuple[str, str, str], ...]:
    """What the title block's compact REV/DATE/DESCRIPTION strip prints.

    ``revisions`` is the raw three-column form the API has always sent and is passed
    through untouched — it is display text, and validating it here would fail a sheet job
    over a typo in a note. ``register`` is the validated
    :class:`~services.drawings.revisions.RevisionHistory`; when one is supplied and the
    raw form is empty, the strip is derived from it, so the strip on every sheet and the
    register on A-06 cannot disagree about which issues exist.
    """
    if revisions:
        return tuple((str(a), str(b), str(c)) for a, b, c in revisions)
    return register.title_block_rows() if register else ()


def _cloud_primitives(
    diff: ModelDiff | None,
    storey_id: str,
    *,
    revision_number: str,
    scale_denominator: int,
) -> tuple[Primitive, ...]:
    """Revision clouds for one storey, or nothing at all.

    Nothing at all is the common case and has to stay free: a set with no previous issue
    has no diff, and a storey that did not change has no regions.
    """
    if diff is None or not revision_number:
        return ()
    return revision_marks(
        diff,
        storey_id,
        revision_number=revision_number,
        scale_denominator=scale_denominator,
    )


def _extent_of(primitives: Sequence[Primitive]) -> tuple[int, int, int, int] | None:
    xs: list[int] = []
    ys: list[int] = []
    for primitive in primitives:
        for x, y in primitive.points():
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def building_extent(house: Any, storey_id: str) -> tuple[int, int, int, int] | None:
    """Outer-face bounding box of a storey's walls, in model mm.

    This is §7's "building line": every outer dimension chain is anchored to it, so it
    has one definition used everywhere rather than one per sheet.
    """
    walls = _orthogonal_only(_walls_of(house, storey_id))
    if not walls:
        return None
    xs: list[int] = []
    ys: list[int] = []
    for wall in walls:
        lo, hi = _wall_axis_span(wall)
        for x, y in _wall_rect(wall, lo, hi):
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys))


def _opening_centre_along(wall: Any, opening: Any) -> int:
    """The opening centre's coordinate along the wall's axis (not a distance)."""
    if _is_horizontal(wall):
        step = 1 if wall.b.x >= wall.a.x else -1
        return wall.a.x + step * opening.offset_mm
    step = 1 if wall.b.y >= wall.a.y else -1
    return wall.a.y + step * opening.offset_mm


def _storey_ffl_mm(house: Any, index: int) -> int:
    ffls = house.levels.ffl_per_storey_mm
    if index < len(ffls):
        return ffls[index]
    # Derive rather than guess: plinth plus the heights below this storey.
    ffl = house.levels.plinth_mm
    for storey in house.storeys[:index]:
        ffl += storey.height_mm
    return ffl


def _roof_level_mm(house: Any) -> int:
    if not house.storeys:
        return house.levels.plinth_mm
    return _storey_ffl_mm(house, len(house.storeys) - 1) + house.storeys[-1].height_mm


def _room_extent(room: Any) -> tuple[int, int, int, int]:
    xs = [p.x for p in room.polygon]
    ys = [p.y for p in room.polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def room_label_lines(room: Any) -> tuple[str, str, str, str]:
    """``(name, "W x D", m², sq ft)`` — what an Indian municipal plan writes in a room.

    The clear dimensions are the room polygon's axis-aligned extent, the same numbers
    :func:`inner_chains` measures, so a contractor reading the label and a reviewer
    reading the chain get one answer. Both areas come from ``garh_model.units``, whose
    formatters are golden-tested against their TypeScript twins, so the label and the
    area statement (m² for the municipality, sq ft for the client) cannot drift.
    """
    from garh_model.units import format_sqft, format_sqm

    name = room.name or str(room.type).replace("_", " ").upper()
    min_x, min_y, max_x, max_y = _room_extent(room)
    return (
        name.upper(),
        "%d x %d" % (max_x - min_x, max_y - min_y),
        format_sqm(room.area_mm2, 2),
        format_sqft(room.area_mm2, 1),
    )


#: Text heights the room label block steps down through until it fits its room:
#: ``(name, other lines)`` in paper µm. 2.5/2.0 mm is the normal print; 1.8/1.5 mm is
#: the smallest a municipal desk reads without complaint, and a WC gets it.
_ROOM_LABEL_LADDER: tuple[tuple[int, int], ...] = (
    (TEXT_HEIGHT_PAPER_UM, TEXT_HEIGHT_SMALL_PAPER_UM),
    (2_200, TEXT_HEIGHT_LABEL_PAPER_UM),
    (TEXT_HEIGHT_LABEL_PAPER_UM, 1_500),
)
#: Paper µm of air between a label block and the room's inner faces.
_ROOM_LABEL_MARGIN_PAPER_UM = 800
#: Paper µm between the lines of a block.
_ROOM_LABEL_GAP_PAPER_UM = 500


def _free_region(room_box: Box, obstacles: Sequence[Box]) -> Box:
    """The largest strip of ``room_box`` clear of every obstacle (a stair flight).

    Each obstacle that overlaps the region cuts it down to the biggest of the four
    strips beside it. A staircase room keeps its label off the treads this way; a room
    with nothing in it is returned untouched.
    """
    region = room_box
    for obstacle in obstacles:
        if not obstacle.overlaps(region):
            continue
        strips = (
            Box(region.x0, region.y0, min(region.x1, obstacle.x0), region.y1),
            Box(max(region.x0, obstacle.x1), region.y0, region.x1, region.y1),
            Box(region.x0, region.y0, region.x1, min(region.y1, obstacle.y0)),
            Box(region.x0, max(region.y0, obstacle.y1), region.x1, region.y1),
        )
        region = max(
            (s for s in strips if s.width > 0 and s.height > 0),
            key=lambda s: (s.width * s.height, s.x0, s.y0),
            default=region,
        )
    return region


def _room_label_block(
    room: Any, *, scale_denominator: int, obstacles: Sequence[Box] = ()
) -> tuple[tuple[Text, ...], tuple[Box, ...], bool]:
    """The label block placed in a room: ``(texts, their boxes, complete)``.

    Centred on the room's extent — less any ``obstacles`` (stair flights) drawn in it —
    stacked name / dimensions / m² / sq ft. The block walks :data:`_ROOM_LABEL_LADDER`
    until every line clears the inner faces by the margin; a room deeper than it is
    wide gets the block turned 90° before the size is dropped, which is what a
    draughtsman does with a passage. Only when the smallest size still overflows does
    the block shed lines, areas first. ``complete`` is True only when all four values
    are on the plan; the test that reads it lists every room that got less, so a shaft
    may lose its area but a bedroom cannot lose it silently.
    """
    name, dims, sqm, sqft = room_label_lines(room)
    min_x, min_y, max_x, max_y = _room_extent(room)
    margin = paper_to_model_mm(_ROOM_LABEL_MARGIN_PAPER_UM, scale_denominator)
    gap = paper_to_model_mm(_ROOM_LABEL_GAP_PAPER_UM, scale_denominator)
    room_box = _free_region(
        Box(min_x + margin, min_y + margin, max_x - margin, max_y - margin), obstacles
    )
    centre = ((room_box.x0 + room_box.x1) // 2, (room_box.y0 + room_box.y1) // 2)
    rotations = (0, 90) if room_box.height > room_box.width else (0,)
    # Arrangements, fullest first. A room too small for four lines gets two — the
    # areas share a line, then the areas go — and a shaft gets its name. Every line
    # kept is exactly the text the four-line block would print.
    arrangements: tuple[tuple[str, ...], ...] = (
        (name, dims, sqm, sqft),
        (name, dims, "%s (%s)" % (sqm, sqft)),
        (name, dims),
        (name,),
    )

    def build(
        lines: tuple[str, ...], name_um: int, line_um: int, rotation: int
    ) -> tuple[tuple[Text, ...], tuple[Box, ...]]:
        heights = [
            paper_to_model_mm(name_um, scale_denominator),
            *(paper_to_model_mm(line_um, scale_denominator) for _ in lines[1:]),
        ]
        total = sum(heights) + gap * (len(heights) - 1)
        texts: list[Text] = []
        boxes: list[Box] = []
        cursor = total // 2  # signed distance from the block centre to the current top
        for index, (text, height) in enumerate(zip(lines, heights, strict=False)):
            offset = cursor - height // 2
            at = (
                (centre[0], centre[1] + offset)
                if rotation == 0
                else (centre[0] - offset, centre[1])
            )
            primitive = Text(
                at=at,
                text=text,
                layer=A_TEXT,
                height_paper_um=name_um if index == 0 else line_um,
                anchor="middle",
                baseline="middle",
                rotation_deg=rotation,
                element_id=room.id,
                bold=index == 0,
            )
            texts.append(primitive)
            boxes.append(text_box_mm(primitive, scale_denominator))
            cursor -= height + gap
        return (tuple(texts), tuple(boxes))

    for lines in arrangements:
        for rotation in rotations:
            for name_um, line_um in _ROOM_LABEL_LADDER:
                texts, boxes = build(lines, name_um, line_um, rotation)
                if all(box.inside(room_box) for box in boxes):
                    return (texts, boxes, len(lines) >= 3)
    # Not even the name fits at the smallest size: draw it anyway, unrotated.
    texts, boxes = build((name,), *_ROOM_LABEL_LADDER[-1], 0)
    return (texts, boxes, False)


# ---------------------------------------------------------------------------
# Dimension chains (§7 steps 2 and 3)
# ---------------------------------------------------------------------------
def _chain_from_breaks(
    *,
    chain_id: str,
    orientation: str,
    level: int,
    offset_mm: int,
    lo: int,
    hi: int,
    breaks: Sequence[int],
    anchors: Mapping[int, str] = {},
    storey_id: str | None = None,
) -> DimChain | None:
    """Build a chain from sorted interior breakpoints between ``lo`` and ``hi``.

    **This function is why §7 step 5 holds.** The segments are consecutive differences
    of a sorted position list that starts at ``lo`` and ends at ``hi``, so their sum is
    ``hi - lo`` identically — the same value passed as ``overall_mm``. There is no
    rounding, no accumulation and therefore no correction step. A chain that summed
    wrong would have to be a bug in ``sorted()``.

    Breakpoints closer than 1 mm to a neighbour are dropped: a zero-length segment
    prints as "0" on a drawing, which is worse than not printing it.
    """
    positions: list[int] = [lo]
    for value in sorted({int(v) for v in breaks}):
        if lo < value < hi and value - positions[-1] >= 1:
            positions.append(value)
    if hi - positions[-1] < 1 and len(positions) > 1:
        positions.pop()
    positions.append(hi)
    if len(positions) < 2 or hi <= lo:
        return None

    segments: list[DimSegment] = []
    for index in range(len(positions) - 1):
        start = positions[index]
        end = positions[index + 1]
        segments.append(
            DimSegment(
                start_mm=start - lo,
                length_mm=end - start,
                anchor_element_id=anchors.get(start) or anchors.get(end),
            )
        )
    return DimChain(
        id=chain_id,
        orientation=orientation,  # type: ignore[arg-type]
        level=level,  # type: ignore[arg-type]
        offset_mm=offset_mm,
        origin_mm=lo,
        segments=tuple(segments),
        overall_mm=hi - lo,
        storey_id=storey_id,
    )


def outer_chains(
    house: Any,
    storey_id: str,
    *,
    scale_denominator: int = 100,
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB,
) -> tuple[DimChain, ...]:
    """§7 step 2: three levels per side of the building.

    Level 1 is the overall extent, level 2 the external-wall segment breakpoints (where
    internal walls meet the facade), level 3 the opening centrelines — or their jambs
    when the firm has set ``dimToJamb``.

    Offsets are §7's 2400 / 1800 / 1200, in **paper-scaled** millimetres: the constants
    are what the offset would be at 1:100, multiplied through the scale so the chains sit
    the same distance off the building line on paper at any scale. A 1:200 sheet with
    1:100 offsets crowds the chains into the wall.
    """
    extent = building_extent(house, storey_id)
    if extent is None:
        return ()
    min_x, min_y, max_x, max_y = extent
    walls = _orthogonal_only(_walls_of(house, storey_id))
    factor = scale_denominator / 100.0

    def offset_for(level_offset_mm: int) -> int:
        return int(round(level_offset_mm * factor))

    l1, l2, l3 = (
        offset_for(LEVEL_1_OFFSET_MM),
        offset_for(LEVEL_2_OFFSET_MM),
        offset_for(LEVEL_3_OFFSET_MM),
    )

    # Breakpoints: wall centrelines crossing each facade direction — the internal
    # walls, and any external wall that is not the facade's own return. The return
    # walls' centrelines sit half a wall inside the building line, and breaking there
    # printed a "115" at each end of every level-2 chain: a figure wider than its
    # segment, overlapping its neighbour, which the collision audit could not see
    # while it boxed only Text. Level 2 measures from the outer face to the first
    # centreline, as a setting-out chain is read.
    vertical_breaks: list[int] = []
    horizontal_breaks: list[int] = []
    vertical_anchors: dict[int, str] = {}
    horizontal_anchors: dict[int, str] = {}
    for wall in walls:
        half = _half(wall.thickness_mm)
        if _is_vertical(wall):
            if wall.kind == "external" and (wall.a.x - half <= min_x or wall.a.x + half >= max_x):
                continue
            vertical_breaks.append(wall.a.x)
            vertical_anchors[wall.a.x] = wall.id
        else:
            if wall.kind == "external" and (wall.a.y - half <= min_y or wall.a.y + half >= max_y):
                continue
            horizontal_breaks.append(wall.a.y)
            horizontal_anchors[wall.a.y] = wall.id

    # Opening positions, per facade side.
    south_openings: list[int] = []
    north_openings: list[int] = []
    west_openings: list[int] = []
    east_openings: list[int] = []
    opening_anchors: dict[int, str] = {}
    for wall in walls:
        if wall.kind != "external":
            continue
        half = _half(wall.thickness_mm)
        line = _wall_line_mm(wall)
        for opening in _openings_of_wall(house, wall.id):
            centre = _opening_centre_along(wall, opening)
            positions = (
                [centre]
                if not dim_to_jamb
                else [centre - opening.width_mm // 2, centre + opening.width_mm // 2]
            )
            for position in positions:
                opening_anchors[position] = opening.id
            if _is_horizontal(wall):
                target = south_openings if line - half <= min_y else north_openings
            else:
                target = west_openings if line - half <= min_x else east_openings
            target.extend(positions)

    chains: list[DimChain | None] = [
        # South (below the plan): horizontal chains.
        _chain_from_breaks(
            chain_id="%s-S-L1" % storey_id,
            orientation="horizontal",
            level=1,
            offset_mm=min_y - l1,
            lo=min_x,
            hi=max_x,
            breaks=(),
            storey_id=storey_id,
        ),
        _chain_from_breaks(
            chain_id="%s-S-L2" % storey_id,
            orientation="horizontal",
            level=2,
            offset_mm=min_y - l2,
            lo=min_x,
            hi=max_x,
            breaks=vertical_breaks,
            anchors=vertical_anchors,
            storey_id=storey_id,
        ),
        _chain_from_breaks(
            chain_id="%s-S-L3" % storey_id,
            orientation="horizontal",
            level=3,
            offset_mm=min_y - l3,
            lo=min_x,
            hi=max_x,
            breaks=south_openings,
            anchors=opening_anchors,
            storey_id=storey_id,
        ),
        # West (left of the plan): vertical chains.
        _chain_from_breaks(
            chain_id="%s-W-L1" % storey_id,
            orientation="vertical",
            level=1,
            offset_mm=min_x - l1,
            lo=min_y,
            hi=max_y,
            breaks=(),
            storey_id=storey_id,
        ),
        _chain_from_breaks(
            chain_id="%s-W-L2" % storey_id,
            orientation="vertical",
            level=2,
            offset_mm=min_x - l2,
            lo=min_y,
            hi=max_y,
            breaks=horizontal_breaks,
            anchors=horizontal_anchors,
            storey_id=storey_id,
        ),
        _chain_from_breaks(
            chain_id="%s-W-L3" % storey_id,
            orientation="vertical",
            level=3,
            offset_mm=min_x - l3,
            lo=min_y,
            hi=max_y,
            breaks=west_openings,
            anchors=opening_anchors,
            storey_id=storey_id,
        ),
    ]
    if north_openings:
        chains.append(
            _chain_from_breaks(
                chain_id="%s-N-L3" % storey_id,
                orientation="horizontal",
                level=3,
                offset_mm=max_y + l3,
                lo=min_x,
                hi=max_x,
                breaks=north_openings,
                anchors=opening_anchors,
                storey_id=storey_id,
            )
        )
    if east_openings:
        chains.append(
            _chain_from_breaks(
                chain_id="%s-E-L3" % storey_id,
                orientation="vertical",
                level=3,
                offset_mm=max_x + l3,
                lo=min_y,
                hi=max_y,
                breaks=east_openings,
                anchors=opening_anchors,
                storey_id=storey_id,
            )
        )
    return tuple(chain for chain in chains if chain is not None)


def inner_chains(house: Any, storey_id: str) -> tuple[DimChain, ...]:
    """§7 step 3: one width and one depth chain per room, at the room's inner faces.

    Simpler than §7 (no shared-wall duplicate suppression — see the module docstring),
    but the numbers are the room's real clear dimensions off its polygon, so a
    contractor reading them gets the right answer.
    """
    chains: list[DimChain] = []
    for room in house.rooms:
        if room.storey_id != storey_id:
            continue
        xs = [p.x for p in room.polygon]
        ys = [p.y for p in room.polygon]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = _chain_from_breaks(
            chain_id="%s-W" % room.id,
            orientation="horizontal",
            level=4,
            offset_mm=min_y + (max_y - min_y) // 2,
            lo=min_x,
            hi=max_x,
            breaks=(),
            anchors={min_x: room.id},
            storey_id=storey_id,
        )
        depth = _chain_from_breaks(
            chain_id="%s-D" % room.id,
            orientation="vertical",
            level=4,
            offset_mm=min_x + (max_x - min_x) // 2,
            lo=min_y,
            hi=max_y,
            breaks=(),
            anchors={min_y: room.id},
            storey_id=storey_id,
        )
        for chain in (width, depth):
            if chain is not None:
                chains.append(chain)
    return tuple(chains)


# ---------------------------------------------------------------------------
# Plan projection (§7 "Plan projection")
# ---------------------------------------------------------------------------
def _wall_solid_spans(house: Any, wall: Any) -> list[tuple[int, int]]:
    """Spans of a wall's axis that are solid masonry, i.e. not an opening.

    Pure interval arithmetic on the opening list. This is what makes "openings break
    walls" true for the poché fill and the face lines at once — one span list, used for
    both, so a filled wall can never cover a window.
    """
    lo, hi = _wall_axis_span(wall)
    cuts: list[tuple[int, int]] = []
    for opening in _openings_of_wall(house, wall.id):
        centre = _opening_centre_along(wall, opening)
        half = opening.width_mm // 2
        cuts.append((max(lo, centre - half), min(hi, centre + half)))
    cuts.sort()
    spans: list[tuple[int, int]] = []
    cursor = lo
    for start, end in cuts:
        if start > cursor:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < hi:
        spans.append((cursor, hi))
    return spans


def _door_primitives(wall: Any, opening: Any, centre_along: int) -> list[Primitive]:
    """Leaf + 90° swing arc (§7: "door arc + leaf")."""
    half_width = opening.width_mm // 2
    half_thickness = _half(wall.thickness_mm)
    line = _wall_line_mm(wall)
    leaf = opening.width_mm
    swings_left = opening.swing in ("in-left", "out-left")
    inward = opening.swing.startswith("in")

    if _is_horizontal(wall):
        hinge_x = centre_along - half_width if swings_left else centre_along + half_width
        hinge: Pt2 = (hinge_x, line)
        # Leaf perpendicular to the wall, on the swing side.
        tip: Pt2 = (hinge_x, line + leaf if inward else line - leaf)
        start = 90 if inward else 270
        end = start + (90 if swings_left else -90)
    else:
        hinge_y = centre_along - half_width if swings_left else centre_along + half_width
        hinge = (line, hinge_y)
        tip = (line + leaf if inward else line - leaf, hinge_y)
        start = 0 if inward else 180
        end = start + (-90 if swings_left else 90)

    out: list[Primitive] = [
        Line(hinge, tip, A_DOOR, element_id=opening.id),
        Arc(
            centre=hinge,
            radius_mm=leaf,
            start_deg=min(start, end) % 360,
            end_deg=max(start, end) % 360,
            layer=A_DOOR,
            element_id=opening.id,
        ),
    ]
    # Jamb lines close the wall break so the plan reads as a doorway, not a gap.
    if _is_horizontal(wall):
        for x in (centre_along - half_width, centre_along + half_width):
            out.append(
                Line(
                    (x, line - half_thickness),
                    (x, line + half_thickness),
                    A_DOOR,
                    element_id=opening.id,
                )
            )
    else:
        for y in (centre_along - half_width, centre_along + half_width):
            out.append(
                Line(
                    (line - half_thickness, y),
                    (line + half_thickness, y),
                    A_DOOR,
                    element_id=opening.id,
                )
            )
    return out


def _window_primitives(wall: Any, opening: Any, centre_along: int) -> list[Primitive]:
    """§7: "window triple line" — two frame lines plus the glazing centreline."""
    half_width = opening.width_mm // 2
    half_thickness = _half(wall.thickness_mm)
    line = _wall_line_mm(wall)
    lo = centre_along - half_width
    hi = centre_along + half_width
    out: list[Primitive] = []
    if _is_horizontal(wall):
        for offset in (-half_thickness, 0, half_thickness):
            out.append(
                Line((lo, line + offset), (hi, line + offset), A_WIND, element_id=opening.id)
            )
        for x in (lo, hi):
            out.append(
                Line(
                    (x, line - half_thickness),
                    (x, line + half_thickness),
                    A_WIND,
                    element_id=opening.id,
                )
            )
    else:
        for offset in (-half_thickness, 0, half_thickness):
            out.append(
                Line((line + offset, lo), (line + offset, hi), A_WIND, element_id=opening.id)
            )
        for y in (lo, hi):
            out.append(
                Line(
                    (line - half_thickness, y),
                    (line + half_thickness, y),
                    A_WIND,
                    element_id=opening.id,
                )
            )
    return out


def _stair_primitives(house: Any, stair: Any, storey: Any) -> list[Primitive]:
    """Treads, going arrow and the ``UP nR`` label (§7: "stairs w/ arrow + UP 15R")."""
    out: list[Primitive] = []
    ox, oy = stair.origin.x, stair.origin.y
    width = stair.width_mm
    tread = stair.tread_mm
    treads = max(1, stair.risers_count - 1)
    if stair.landing is not None:
        # A dogleg's drawn flight is half the risers; the landing takes the rest.
        treads = max(1, treads // 2)

    if stair.direction in ("N", "S"):
        sign = 1 if stair.direction == "N" else -1
        length = treads * tread
        ring = (
            (ox, oy),
            (ox + width, oy),
            (ox + width, oy + sign * length),
            (ox, oy + sign * length),
        )
        out.append(Polyline(ring, A_STAIR, closed=True, element_id=stair.id))
        for index in range(1, treads):
            y = oy + sign * index * tread
            out.append(Line((ox, y), (ox + width, y), A_STAIR, element_id=stair.id))
        arrow_x = ox + width // 2
        out.append(Line((arrow_x, oy), (arrow_x, oy + sign * length), A_STAIR, element_id=stair.id))
        head = oy + sign * length
        out.append(
            Line(
                (arrow_x, head),
                (arrow_x - width // 6, head - sign * width // 6),
                A_STAIR,
                element_id=stair.id,
            )
        )
        out.append(
            Line(
                (arrow_x, head),
                (arrow_x + width // 6, head - sign * width // 6),
                A_STAIR,
                element_id=stair.id,
            )
        )
        label_at: Pt2 = (arrow_x, oy + sign * (length + 200))
    else:
        sign = 1 if stair.direction == "E" else -1
        length = treads * tread
        ring = (
            (ox, oy),
            (ox, oy + width),
            (ox + sign * length, oy + width),
            (ox + sign * length, oy),
        )
        out.append(Polyline(ring, A_STAIR, closed=True, element_id=stair.id))
        for index in range(1, treads):
            x = ox + sign * index * tread
            out.append(Line((x, oy), (x, oy + width), A_STAIR, element_id=stair.id))
        arrow_y = oy + width // 2
        out.append(Line((ox, arrow_y), (ox + sign * length, arrow_y), A_STAIR, element_id=stair.id))
        head = ox + sign * length
        out.append(
            Line(
                (head, arrow_y),
                (head - sign * width // 6, arrow_y - width // 6),
                A_STAIR,
                element_id=stair.id,
            )
        )
        out.append(
            Line(
                (head, arrow_y),
                (head - sign * width // 6, arrow_y + width // 6),
                A_STAIR,
                element_id=stair.id,
            )
        )
        label_at = (ox + sign * (length + 200), arrow_y)

    out.append(
        Text(
            at=label_at,
            text="UP %dR" % stair.risers_count,
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            element_id=stair.id,
        )
    )
    return out


def _north_arrow(at: Pt2, length_mm: int, north_deg: int) -> list[Primitive]:
    """A north arrow rotated by the plot's ``north_deg`` (clockwise from +Y)."""
    import math

    radians = math.radians(-north_deg)
    cx, cy = at

    def rotate(dx: int, dy: int) -> Pt2:
        return (
            cx + int(round(dx * math.cos(radians) - dy * math.sin(radians))),
            cy + int(round(dx * math.sin(radians) + dy * math.cos(radians))),
        )

    tip = rotate(0, length_mm)
    left = rotate(-length_mm // 4, -length_mm // 4)
    right = rotate(length_mm // 4, -length_mm // 4)
    tail = rotate(0, -length_mm // 2)
    return [
        Polyline((tip, left, tail, right), A_TEXT, closed=True),
        Text(
            at=rotate(0, length_mm + length_mm // 3),
            text="N",
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_PAPER_UM,
            anchor="middle",
            baseline="middle",
            bold=True,
        ),
    ]


#: Paper µm from the building line to the centre of a section bubble: past the
#: level-1 chain (24 000) and its figure, with the bubble's own radius to spare.
_SECTION_BUBBLE_REACH_PAPER_UM = 33_000
_SECTION_BUBBLE_RADIUS_PAPER_UM = 4_500


def _section_marker(
    cut: tuple[Pt2, Pt2],
    tag: str,
    *,
    extent: tuple[int, int, int, int],
    scale_denominator: int,
) -> list[Primitive]:
    """A section line with a bubble at each end, drawn clear of the dimension chains.

    ``cut`` is the cut itself (what the section sheet's viewport records); the drawn
    line runs on past the three chain levels so the bubbles sit outside them rather
    than on the level-3 figures, which is where a bubble 900 mm off the building
    line landed.
    """
    reach = paper_to_model_mm(_SECTION_BUBBLE_REACH_PAPER_UM, scale_denominator)
    radius = paper_to_model_mm(_SECTION_BUBBLE_RADIUS_PAPER_UM, scale_denominator)
    min_x, min_y, max_x, max_y = extent
    (ax, ay), (bx, by) = cut
    if ax == bx:
        a: Pt2 = (ax, min(ay, by, min_y - reach))
        b: Pt2 = (ax, max(ay, by, max_y + reach))
    else:
        a = (min(ax, bx, min_x - reach), ay)
        b = (max(ax, bx, max_x + reach), ay)
    out: list[Primitive] = [Line(a, b, A_TEXT, style=STYLE_CENTRE)]
    for point in (a, b):
        out.append(Circle(point, radius, A_TEXT))
        out.append(
            Text(
                at=point,
                text=tag,
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                anchor="middle",
                baseline="middle",
                bold=True,
            )
        )
    return out


#: Paper µm between an opening tag and the wall face it sits beside.
_TAG_CLEARANCE_PAPER_UM = 3_000

#: Paper µm of air around a floor plan's building line: the section bubbles' reach
#: plus a bubble radius and a little more, so the sheet title clears them.
_PLAN_PAD_PAPER_UM = _SECTION_BUBBLE_REACH_PAPER_UM + _SECTION_BUBBLE_RADIUS_PAPER_UM + 6_000


def _tag_candidates(
    wall: Any,
    opening: Any,
    centre_along: int,
    tag: str,
    *,
    extent: tuple[int, int, int, int],
    scale_denominator: int,
) -> list[tuple[Text, Box]]:
    """Where an opening's tag may go, best first.

    A door's tag sits inside its swing (the quadrant the leaf sweeps is the one place
    on a plan guaranteed to hold nothing else), then on the far side. A window's sits
    inside the building — outside is where the three dimension levels live. Each side
    is tried centred on the opening, then shifted past either jamb.
    """
    clearance = paper_to_model_mm(_TAG_CLEARANCE_PAPER_UM, scale_denominator)
    perpendicular = _half(wall.thickness_mm) + clearance
    line = _wall_line_mm(wall)
    horizontal = _is_horizontal(wall)
    min_x, min_y, max_x, max_y = extent
    if horizontal:
        inward = 1 if line <= (min_y + max_y) // 2 else -1
    else:
        inward = 1 if line <= (min_x + max_x) // 2 else -1
    if opening.kind == "door":
        swing = 1 if str(opening.swing).startswith("in") else -1
        sides = (swing, -swing)
    else:
        sides = (inward, -inward)
    shift = opening.width_mm // 2 + clearance
    candidates: list[tuple[Text, Box]] = []
    for side in sides:
        for along in (0, shift, -shift):
            across = line + side * perpendicular
            at: Pt2 = (
                (centre_along + along, across) if horizontal else (across, centre_along + along)
            )
            text = Text(
                at=at,
                text=tag,
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
                anchor="middle",
                baseline="middle",
                element_id=opening.id,
            )
            candidates.append((text, text_box_mm(text, scale_denominator)))
    return candidates


def plan_primitives(
    doc: Any,
    storey_id: str,
    *,
    scale_denominator: int = 100,
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB,
    section_line: tuple[Pt2, Pt2] | None = None,
    opening_tags: Mapping[str, str] | None = None,
) -> tuple[tuple[Primitive, ...], tuple[DimChain, ...]]:
    """One storey's plan: walls, openings, stairs, room labels, tags, dims, markers.

    ``opening_tags`` is ``{opening id: tag}`` — the schedule sheet's own assignment.
    When it is not supplied it is computed by :func:`opening_tag_map`, the same call
    :func:`build_schedule_rows` makes, so a ``W2`` on the plan is the ``W2`` row on
    A-05 by construction. Passing a mapping is for callers that build a whole set and
    want one computation; passing ``{}`` draws no tags.
    """
    house = doc.house
    storey = next((s for s in house.storeys if s.id == storey_id), None)
    if storey is None:
        raise KeyError("no storey %r in this model" % storey_id)
    storey_index = list(house.storeys).index(storey)
    walls = _orthogonal_only(_walls_of(house, storey_id))
    tags = opening_tag_map(house) if opening_tags is None else dict(opening_tags)
    out: list[Primitive] = []

    # -- walls: poché + faces, broken by openings -------------------------
    for wall in sorted(walls, key=lambda w: w.id):
        layer = A_WALL if wall.kind == "external" else A_WALL_PART
        for lo, hi in _wall_solid_spans(house, wall):
            if hi <= lo:
                continue
            ring = _wall_rect(wall, lo, hi)
            out.append(
                Hatch(
                    outline=ring,
                    layer=layer,
                    pattern=HATCH_SOLID if wall.kind == "external" else HATCH_DIAGONAL,
                    spacing_mm=150,
                    element_id=wall.id,
                )
            )
            out.append(Polyline(ring, layer, closed=True, element_id=wall.id))

    # -- openings ---------------------------------------------------------
    for wall in sorted(walls, key=lambda w: w.id):
        for opening in _openings_of_wall(house, wall.id):
            centre = _opening_centre_along(wall, opening)
            if opening.kind == "door":
                out.extend(_door_primitives(wall, opening, centre))
            else:
                out.extend(_window_primitives(wall, opening, centre))

    # -- stairs -----------------------------------------------------------
    flights: list[Box] = []
    for stair in sorted((s for s in house.stairs if s.storey_id == storey_id), key=lambda s: s.id):
        out.extend(_stair_primitives(house, stair, storey))
        # The drawn flight (the same one _stair_primitives draws) is an obstacle a room
        # label must not sit on; the return flight of a dogleg is not drawn, so the
        # label may use that half of the stair room.
        flights.append(Box(*stair_geometry(stair).flight_rect))

    # -- room labels + area outline ---------------------------------------
    for room in sorted((r for r in house.rooms if r.storey_id == storey_id), key=lambda r: r.id):
        out.append(
            Polyline(
                tuple((p.x, p.y) for p in room.polygon),
                A_AREA,
                closed=True,
                style=STYLE_DASHED,
                element_id=room.id,
            )
        )
        texts, _boxes, _complete = _room_label_block(
            room, scale_denominator=scale_denominator, obstacles=flights
        )
        out.extend(texts)

    extent = building_extent(house, storey_id)

    # -- opening tags, kept off every label already on the plan -----------
    # The room labels and the stair's UP label are placed first and are fixed; each
    # tag then takes the first of its candidate spots that touches none of them and
    # none of the tags placed before it. Walls and openings are visited in id order,
    # so the result is a pure function of the design.
    if extent is not None:
        placed: list[Box] = [text_box_mm(p, scale_denominator) for p in out if isinstance(p, Text)]
        for wall in sorted(walls, key=lambda w: w.id):
            for opening in _openings_of_wall(house, wall.id):
                tag = tags.get(opening.id)
                if not tag:
                    continue
                candidates = _tag_candidates(
                    wall,
                    opening,
                    _opening_centre_along(wall, opening),
                    tag,
                    extent=extent,
                    scale_denominator=scale_denominator,
                )
                text, box, _found = first_free(candidates, placed)
                placed.append(box)
                out.append(text)

    if extent is not None:
        min_x, min_y, max_x, max_y = extent
        # FFL marker (§7 "FFL markers"), OUTSIDE the building beside the north
        # arrow. Inside the top-left corner it sat on whatever room lived there —
        # the 40 x 60 library plan's first-floor bedroom label, on two sheets.
        out.append(
            Text(
                at=(max_x + 1500, max_y - 2300),
                text="FFL +%d" % _storey_ffl_mm(house, storey_index),
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            )
        )
        out.extend(_north_arrow((max_x + 1500, max_y - 1200), 900, doc.plot.north_deg))
        if section_line is not None:
            out.extend(
                _section_marker(
                    section_line, "A", extent=extent, scale_denominator=scale_denominator
                )
            )

    # Outer chains only. The per-room clear dimensions are printed in the room label
    # (``3623 x 2700``) rather than as :func:`inner_chains`: drawn as chains they were
    # a cross-hair through every room with their figures under the room name, which
    # the collision audit could not see because a chain's figures are not Text. The
    # numbers are identical — both read the room polygon's extent.
    chains = outer_chains(
        house, storey_id, scale_denominator=scale_denominator, dim_to_jamb=dim_to_jamb
    )
    assert_chains_sum(chains)
    out.extend(
        Dim(chain=chain, layer=A_DIM, text_height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM)
        for chain in chains
    )
    return (tuple(out), chains)


# ---------------------------------------------------------------------------
# Elevations (§7 "Elevations")
# ---------------------------------------------------------------------------
_DIRECTION_NAMES = {"N": "NORTH", "E": "EAST", "S": "SOUTH", "W": "WEST"}


def _facade_walls(house: Any, storey_id: str, direction: str) -> list[Any]:
    """External walls whose outer face lies on the named side of the building."""
    extent = building_extent(house, storey_id)
    if extent is None:
        return []
    min_x, min_y, max_x, max_y = extent
    result: list[Any] = []
    for wall in _orthogonal_only(_walls_of(house, storey_id)):
        if wall.kind != "external":
            continue
        half = _half(wall.thickness_mm)
        line = _wall_line_mm(wall)
        if (
            direction == "S"
            and _is_horizontal(wall)
            and line - half <= min_y
            or direction == "N"
            and _is_horizontal(wall)
            and line + half >= max_y
            or direction == "W"
            and _is_vertical(wall)
            and line - half <= min_x
            or direction == "E"
            and _is_vertical(wall)
            and line + half >= max_x
        ):
            result.append(wall)
    return result


def _facade_along(direction: str, extent: tuple[int, int, int, int], point_along: int) -> int:
    """Map a model coordinate to the facade's left-to-right axis.

    N and E facades are viewed from the opposite side, so their along-axis runs
    backwards relative to the model — mirroring here is what stops a north elevation
    from showing a west-side window on the east.
    """
    min_x, min_y, max_x, max_y = extent
    if direction == "S":
        return point_along - min_x
    if direction == "N":
        return max_x - point_along
    if direction == "W":
        return max_y - point_along
    return point_along - min_y


def elevation_primitives(
    doc: Any, direction: str, *, scale_denominator: int = 100
) -> tuple[tuple[Primitive, ...], tuple[DimChain, ...]]:
    """One facade: ground line, plinth, storey lines, openings, level markers, height chain.

    Coordinates in the returned primitives are ``(along_facade_mm, height_above_datum_mm)``
    — a 2D elevation space, not plan space. Ground level is 0.
    """
    house = doc.house
    if not house.storeys:
        return ((), ())
    ground_extent = building_extent(house, house.storeys[0].id)
    if ground_extent is None:
        return ((), ())
    min_x, min_y, max_x, max_y = ground_extent
    facade_width = (max_x - min_x) if direction in ("S", "N") else (max_y - min_y)

    levels = house.levels
    plinth = levels.plinth_mm
    roof = _roof_level_mm(house)
    parapet_top = roof + levels.parapet_mm
    out: list[Primitive] = []

    # Ground line, extended past the building the way an elevation is drawn.
    out.append(Line((-600, 0), (facade_width + 600, 0), A_WALL))
    # Plinth band.
    out.append(
        Polyline(
            ((0, 0), (facade_width, 0), (facade_width, plinth), (0, plinth)),
            A_WALL_PART,
            closed=True,
        )
    )
    # Building envelope up to the parapet.
    out.append(
        Polyline(
            ((0, plinth), (facade_width, plinth), (facade_width, parapet_top), (0, parapet_top)),
            A_WALL,
            closed=True,
        )
    )
    # Floor lines and the parapet coping.
    for index in range(len(house.storeys)):
        ffl = _storey_ffl_mm(house, index)
        out.append(Line((0, ffl), (facade_width, ffl), A_WALL_PART, style=STYLE_HIDDEN))
    out.append(Line((0, roof), (facade_width, roof), A_WALL_PART))

    # Openings on this facade, per storey.
    for index, storey in enumerate(house.storeys):
        ffl = _storey_ffl_mm(house, index)
        for wall in sorted(_facade_walls(house, storey.id, direction), key=lambda w: w.id):
            storey_extent = building_extent(house, storey.id) or ground_extent
            for opening in _openings_of_wall(house, wall.id):
                centre_model = _opening_centre_along(wall, opening)
                along = _facade_along(direction, storey_extent, centre_model)
                lo = along - opening.width_mm // 2
                hi = along + opening.width_mm // 2
                bottom = ffl + opening.sill_mm
                top = bottom + opening.height_mm
                layer = A_DOOR if opening.kind == "door" else A_WIND
                out.append(
                    Polyline(
                        ((lo, bottom), (hi, bottom), (hi, top), (lo, top)),
                        layer,
                        closed=True,
                        element_id=opening.id,
                    )
                )
                if opening.kind != "door":
                    mid = (bottom + top) // 2
                    out.append(Line((lo, mid), (hi, mid), layer, element_id=opening.id))

    # Level markers (§7: "floor lines ... as level markers, not chains").
    marker_x = facade_width + 400
    for label, level in _level_markers(house):
        out.append(Line((facade_width, level), (marker_x + 900, level), A_DIM, style=STYLE_CENTRE))
        out.append(
            Text(
                at=(marker_x + 200, level + 120),
                text="%s +%d" % (label, level),
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
            )
        )

    # Overall height chain: plinth + each storey height + parapet, summing to the top.
    breaks = [plinth] + [_storey_ffl_mm(house, i) for i in range(1, len(house.storeys))] + [roof]
    chain = _chain_from_breaks(
        chain_id="elev-%s-H" % direction,
        orientation="vertical",
        level=1,
        offset_mm=-int(round(LEVEL_1_OFFSET_MM * scale_denominator / 100.0)),
        lo=0,
        hi=parapet_top,
        breaks=breaks,
    )
    chains = (chain,) if chain is not None else ()
    assert_chains_sum(chains)
    out.extend(
        Dim(chain=c, layer=A_DIM, text_height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM) for c in chains
    )

    # Material callouts, only when a facade kit has actually been applied.
    facade = house.facade
    if facade.kit_id:
        out.append(
            Text(
                at=(0, parapet_top + 600),
                text="FACADE KIT: %s%s"
                % (
                    facade.kit_id.upper(),
                    (" / " + facade.colorway_id.upper()) if facade.colorway_id else "",
                ),
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            )
        )
    return (tuple(out), chains)


def _level_markers(house: Any) -> tuple[tuple[str, int], ...]:
    """The level set §7 wants on elevations and sections, deduplicated and sorted."""
    levels = house.levels
    markers: list[tuple[str, int]] = [("GL", 0), ("PLINTH", levels.plinth_mm)]
    for index in range(len(house.storeys)):
        ffl = _storey_ffl_mm(house, index)
        markers.append(("FFL %d" % index, ffl))
        markers.append(("LINTEL %d" % index, ffl + levels.lintel_default_mm))
    roof = _roof_level_mm(house)
    markers.append(("ROOF", roof))
    markers.append(("PARAPET", roof + levels.parapet_mm))
    seen: dict[int, str] = {}
    for label, level in markers:
        seen.setdefault(level, label)
    return tuple(sorted(((label, level) for level, label in seen.items()), key=lambda p: p[1]))


# ---------------------------------------------------------------------------
# Section (§7 "Section (through stair)")
# ---------------------------------------------------------------------------
def choose_section_line(doc: Any) -> tuple[Pt2, Pt2] | None:
    """§7: cut through the stair flight when there is one, else the building centre."""
    house = doc.house
    if not house.storeys:
        return None
    extent = building_extent(house, house.storeys[0].id)
    if extent is None:
        return None
    min_x, min_y, max_x, max_y = extent
    stairs = [s for s in house.stairs if s.storey_id == house.storeys[0].id]
    if stairs:
        stair = sorted(stairs, key=lambda s: s.id)[0]
        if stair.direction in ("N", "S"):
            x = stair.origin.x + stair.width_mm // 2
            return ((x, min_y - 900), (x, max_y + 900))
        y = stair.origin.y + stair.width_mm // 2
        return ((min_x - 900, y), (max_x + 900, y))
    x = (min_x + max_x) // 2
    return ((x, min_y - 900), (x, max_y + 900))


def section_primitives(
    doc: Any, *, scale_denominator: int = 100
) -> tuple[tuple[Primitive, ...], tuple[DimChain, ...]]:
    """The section: storey heights chain, sill/lintel levels, plinth, parapet, foundation.

    Coordinates are ``(along_cut_mm, height_above_datum_mm)``, ground at 0 — the same
    2D convention as the elevations, so both consume one placement helper.
    """
    house = doc.house
    if not house.storeys:
        return ((), ())
    cut = choose_section_line(doc)
    ground_extent = building_extent(house, house.storeys[0].id)
    if cut is None or ground_extent is None:
        return ((), ())
    min_x, min_y, max_x, max_y = ground_extent
    vertical_cut = cut[0][0] == cut[1][0]
    span = (max_y - min_y) if vertical_cut else (max_x - min_x)

    levels = house.levels
    plinth = levels.plinth_mm
    roof = _roof_level_mm(house)
    parapet_top = roof + levels.parapet_mm
    out: list[Primitive] = []

    # Ground and the indicative foundation (§7, verbatim label).
    out.append(Line((-900, 0), (span + 900, 0), A_WALL))
    foundation = -FOUNDATION_DEPTH_BELOW_PLINTH_MM
    out.append(
        Polyline(
            ((0, 0), (span, 0), (span, foundation), (0, foundation)),
            A_WALL_PART,
            closed=True,
            style=STYLE_DASHED,
        )
    )
    out.append(
        Hatch(
            outline=((0, 0), (span, 0), (span, foundation), (0, foundation)),
            layer=A_WALL_PART,
            pattern=HATCH_EARTH,
            spacing_mm=300,
        )
    )
    out.append(
        Text(
            at=(span // 2, foundation - 250),
            text=FOUNDATION_NOTE,
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
            anchor="middle",
        )
    )

    # Cut walls at both ends of the span, plus the slabs.
    wall_thickness = 230
    for index, storey in enumerate(house.storeys):
        ffl = _storey_ffl_mm(house, index)
        top = ffl + storey.height_mm
        for x0 in (0, span - wall_thickness):
            ring = ((x0, ffl), (x0 + wall_thickness, ffl), (x0 + wall_thickness, top), (x0, top))
            out.append(Hatch(ring, A_WALL, pattern=HATCH_SOLID))
            out.append(Polyline(ring, A_WALL, closed=True))
        slab = storey.level.slab_thickness_mm
        slab_ring = ((0, ffl - slab), (span, ffl - slab), (span, ffl), (0, ffl))
        out.append(Hatch(slab_ring, A_WALL, pattern=HATCH_DIAGONAL, spacing_mm=120))
        out.append(Polyline(slab_ring, A_WALL, closed=True))
        # Sill and lintel lines for this storey (§7: "sill/lintel heights").
        for label, level in (
            ("SILL", ffl + levels.sill_default_mm),
            ("LINTEL", ffl + levels.lintel_default_mm),
        ):
            out.append(Line((0, level), (span, level), A_WALL_PART, style=STYLE_HIDDEN))
            out.append(
                Text(
                    at=(span // 2, level + 100),
                    text="%s +%d" % (label, level),
                    layer=A_TEXT,
                    height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
                    anchor="middle",
                )
            )

    # Roof slab and parapet.
    out.append(
        Polyline(
            ((0, roof), (span, roof), (span, parapet_top), (0, parapet_top)),
            A_WALL_PART,
            closed=True,
        )
    )
    out.append(Polyline(((0, plinth), (span, plinth)), A_WALL_PART))

    # Storey-height chain.
    breaks = [plinth] + [_storey_ffl_mm(house, i) for i in range(1, len(house.storeys))] + [roof]
    chain = _chain_from_breaks(
        chain_id="section-H",
        orientation="vertical",
        level=1,
        offset_mm=-int(round(LEVEL_1_OFFSET_MM * scale_denominator / 100.0)),
        lo=foundation,
        hi=parapet_top,
        breaks=[0, *breaks],
    )
    chains = (chain,) if chain is not None else ()
    assert_chains_sum(chains)
    out.extend(
        Dim(chain=c, layer=A_DIM, text_height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM) for c in chains
    )
    return (tuple(out), chains)


# ---------------------------------------------------------------------------
# Site plan (§7 / F7-A item 1)
# ---------------------------------------------------------------------------
#: Paper µm from a plot edge (or the road band outside it) to the plot-edge chain.
_SITE_EDGE_CHAIN_OFFSET_PAPER_UM = 12_000
#: Paper µm from the footprint's face to its overall chain, inside the setback.
_SITE_FOOTPRINT_CHAIN_OFFSET_PAPER_UM = 5_000
#: Paper µm of line spacing in the site plan's notes block.
_SITE_NOTE_LINE_PAPER_UM = 4_000


def _edge_outward(a: Pt2, b: Pt2, centre: Pt2) -> tuple[int, int]:
    """Unit outward normal of an axis-aligned plot edge, judged against the centroid."""
    if a[1] == b[1]:
        return (0, -1) if a[1] <= centre[1] else (0, 1)
    return (-1, 0) if a[0] <= centre[0] else (1, 0)


def _plot_size_note(boundary: Sequence[Pt2]) -> str | None:
    """``PLOT SIZE: 9144 x 12192 mm (30'-0" x 40'-0")`` for a rectangular plot.

    Both figures come off the boundary; the feet are how a Bengaluru or Hyderabad
    sanction plan quotes a site ("30 x 40"), the millimetres are what the chains
    print. A plot that is not an axis-aligned rectangle gets its edge chains and no
    single size line, rather than a size that is true of no side.
    """
    from garh_model.units import format_ft_in

    if len(boundary) != 4:
        return None
    xs = sorted({x for x, _ in boundary})
    ys = sorted({y for _, y in boundary})
    if len(xs) != 2 or len(ys) != 2:
        return None
    width = xs[1] - xs[0]
    depth = ys[1] - ys[0]
    return "PLOT SIZE: %d x %d mm (%s x %s)" % (
        width,
        depth,
        format_ft_in(width),
        format_ft_in(depth),
    )


def site_plan_primitives(
    doc: Any, *, statement: Any = None, scale_denominator: int = 200
) -> tuple[tuple[Primitive, ...], tuple[DimChain, ...]]:
    """Plot boundary, footprint, road, dimensioned setbacks, north, coverage/FAR note.

    Every side of the plot is chained (outside the road band where there is one), the
    footprint carries its overall width and depth, each road its width across the
    band, and the four setbacks their clear distance — so a desk can read the whole
    site off the sheet without a scale rule.

    Setback *values* are not measured here — they are read off the area statement's
    ``setbacks`` rows, which the rules engine produced. §7's "same numbers, one source"
    applies to the site plan more than anywhere else: a setback dimension that disagrees
    with the compliance chip is the drawing a municipality rejects. The notes block
    prints the statement's plot area, coverage, FAR and each setback's provided /
    required pair, formatted by the engine's own formatters.
    """
    plot = doc.plot
    house = doc.house
    boundary = [(p.x, p.y) for p in plot.boundary]
    out: list[Primitive] = []
    if len(boundary) < 3:
        return ((), ())

    def paper(um: int) -> int:
        return paper_to_model_mm(um, scale_denominator)

    # The plot boundary doubles as the compound wall, hence A-WALL-PART rather than a
    # geometry layer: it is a real built thing, but not full-height building fabric.
    out.append(Polyline(tuple(boundary), A_WALL_PART, closed=True))
    xs = [x for x, _ in boundary]
    ys = [y for _, y in boundary]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    centre = ((min_x + max_x) // 2, (min_y + max_y) // 2)
    chains: list[DimChain] = []

    # Roads, drawn as a band outside the relevant edge, each with a width chain
    # across the band at the edge's quarter point (the name sits at its centre).
    road_width_on_edge: dict[int, int] = {}
    for road in plot.roads:
        if road.width_mm is None:
            continue
        index = road.edge_index % len(boundary)
        a = boundary[index]
        b = boundary[(index + 1) % len(boundary)]
        if a[0] != b[0] and a[1] != b[1]:
            continue  # a road on a skew edge is drawn by nobody yet; the chain would lie
        width = road.width_mm
        road_width_on_edge[index] = width
        nx, ny = _edge_outward(a, b, centre)
        ring = (
            a,
            b,
            (b[0] + nx * width, b[1] + ny * width),
            (a[0] + nx * width, a[1] + ny * width),
        )
        out.append(Polyline(ring, A_WALL_PART, closed=True, style=STYLE_DASHED))
        out.append(
            Text(
                at=((ring[0][0] + ring[2][0]) // 2, (ring[0][1] + ring[2][1]) // 2),
                text=road.name or "%d mm WIDE ROAD" % width,
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                anchor="middle",
                baseline="middle",
            )
        )
        quarter = (a[0] + (b[0] - a[0]) // 4, a[1] + (b[1] - a[1]) // 4)
        if nx == 0:
            lo, hi = sorted((a[1], a[1] + ny * width))
            chain = _chain_from_breaks(
                chain_id="site-road-%d" % index,
                orientation="vertical",
                level=1,
                offset_mm=quarter[0],
                lo=lo,
                hi=hi,
                breaks=(),
            )
        else:
            lo, hi = sorted((a[0], a[0] + nx * width))
            chain = _chain_from_breaks(
                chain_id="site-road-%d" % index,
                orientation="horizontal",
                level=1,
                offset_mm=quarter[1],
                lo=lo,
                hi=hi,
                breaks=(),
            )
        if chain is not None:
            chains.append(chain)

    # Plot edges: one chain per axis-aligned side, outside the road band if any.
    edge_offset = paper(_SITE_EDGE_CHAIN_OFFSET_PAPER_UM)
    for index, a in enumerate(boundary):
        b = boundary[(index + 1) % len(boundary)]
        if a[0] != b[0] and a[1] != b[1]:
            continue
        nx, ny = _edge_outward(a, b, centre)
        clearance = road_width_on_edge.get(index, 0) + edge_offset
        if ny != 0:
            chain = _chain_from_breaks(
                chain_id="site-plot-edge-%d" % index,
                orientation="horizontal",
                level=1,
                offset_mm=a[1] + ny * clearance,
                lo=min(a[0], b[0]),
                hi=max(a[0], b[0]),
                breaks=(),
            )
        else:
            chain = _chain_from_breaks(
                chain_id="site-plot-edge-%d" % index,
                orientation="vertical",
                level=1,
                offset_mm=a[0] + nx * clearance,
                lo=min(a[1], b[1]),
                hi=max(a[1], b[1]),
                breaks=(),
            )
        if chain is not None:
            chains.append(chain)

    # Footprint of the ground storey, with its overall width and depth.
    footprint = building_extent(house, house.storeys[0].id) if house.storeys else None
    if footprint is not None:
        f_min_x, f_min_y, f_max_x, f_max_y = footprint
        ring = ((f_min_x, f_min_y), (f_max_x, f_min_y), (f_max_x, f_max_y), (f_min_x, f_max_y))
        out.append(Hatch(ring, A_AREA, pattern=HATCH_DIAGONAL, spacing_mm=500, angle_deg=45))
        out.append(Polyline(ring, A_WALL, closed=True))
        footprint_offset = paper(_SITE_FOOTPRINT_CHAIN_OFFSET_PAPER_UM)
        for chain in (
            _chain_from_breaks(
                chain_id="site-footprint-W",
                orientation="horizontal",
                level=1,
                offset_mm=f_max_y + footprint_offset,
                lo=f_min_x,
                hi=f_max_x,
                breaks=(),
            ),
            _chain_from_breaks(
                chain_id="site-footprint-D",
                orientation="vertical",
                level=1,
                offset_mm=f_max_x + footprint_offset,
                lo=f_min_y,
                hi=f_max_y,
                breaks=(),
            ),
        ):
            if chain is not None:
                chains.append(chain)

        # Setback dimensions, one chain per plot edge, from the edge to the footprint
        # face. They sit at the footprint's quarter points so their figures stay clear
        # of the footprint chains, whose figures are centred. With a statement each
        # chain is named by the rules engine's role for that edge (front / rear /
        # side-a / side-b) and its length is checked against the engine's
        # ``providedMm``: the number on the drawing and the number on the compliance
        # tab are the same number or the sheet is not produced.
        quarter_x = f_min_x + (f_max_x - f_min_x) // 4
        quarter_y = f_min_y + (f_max_y - f_min_y) // 4
        roles: dict[int, Any] = {}
        if statement is not None:
            roles = {int(row.edge_index): row for row in statement.setbacks}
        for index, a in enumerate(boundary):
            b = boundary[(index + 1) % len(boundary)]
            if a[0] != b[0] and a[1] != b[1]:
                continue
            nx, ny = _edge_outward(a, b, centre)
            # Signed clear distance from the plot edge inward to the footprint face,
            # clamped at zero exactly as the rules engine clamps it: a wall face on or
            # over the plot line is a setback of 0, and 0 is not dimensioned.
            if ny != 0:
                face = f_min_y if ny < 0 else f_max_y
                provided = max(0, (face - a[1]) if ny < 0 else (a[1] - face))
                lo, hi = sorted((a[1], face))
                orientation, offset_line = "vertical", quarter_x
            else:
                face = f_min_x if nx < 0 else f_max_x
                provided = max(0, (face - a[0]) if nx < 0 else (a[0] - face))
                lo, hi = sorted((a[0], face))
                orientation, offset_line = "horizontal", quarter_y
            row = roles.get(index)
            name = str(row.role) if row is not None else "edge-%d" % index
            if row is not None and int(row.provided_mm) != provided:
                raise ValueError(
                    "Setback on plot edge %d (%s) measures %d mm on the site plan but the "
                    "compliance evaluation says %d mm; a sheet that disagrees with the "
                    "compliance report is not drawn."
                    % (index, name, provided, int(row.provided_mm))
                )
            if provided == 0:
                continue
            chain = _chain_from_breaks(
                chain_id="site-setback-%s" % name,
                orientation=orientation,
                level=1,
                offset_mm=offset_line,
                lo=lo,
                hi=hi,
                breaks=(),
            )
            if chain is not None:
                chains.append(chain)

    assert_chains_sum(tuple(chains))
    out.extend(
        Dim(chain=c, layer=A_DIM, text_height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM) for c in chains
    )
    out.extend(
        _north_arrow((max_x + paper(12_000), max_y - paper(12_000)), paper(12_000), plot.north_deg)
    )

    # Notes block, below everything drawn so far (road bands and chains included):
    # plot size off the boundary, then the statement's numbers — never recomputed here.
    lines: list[str] = []
    size_note = _plot_size_note(boundary)
    if size_note:
        lines.append(size_note)
    if statement is not None:
        from garh_model.units import format_sqm
        from garh_rules.formatting import format_ratio

        lines.append("PLOT AREA: %s" % format_sqm(statement.plot_area_mm2, 2))
        lines.append(
            "GROUND COVERAGE: %s (%s)"
            % (
                format_sqm(statement.footprint_area_mm2, 2),
                "permissible %s" % format_sqm(statement.coverage_allowed_mm2, 2)
                if statement.coverage_allowed_mm2 is not None
                else "no coverage rule applied",
            )
        )
        lines.append(
            "FAR ACHIEVED: %s%s"
            % (
                format_ratio(statement.far_achieved),
                " (permissible %s)" % format_ratio(statement.far_allowed)
                if statement.far_allowed is not None
                else "",
            )
        )
        for row in statement.setbacks:
            lines.append(
                "%s SETBACK: %d mm%s"
                % (
                    str(row.role).upper().replace("-", " "),
                    row.provided_mm,
                    (" (required %d mm)" % row.required_mm)
                    if row.required_mm is not None
                    else " (not regulated)",
                )
            )
    if lines:
        lowest = min(y for primitive in out for _x, y in primitive.points())
        top = lowest - paper(_SITE_NOTE_LINE_PAPER_UM + 2_000)
        for index, text in enumerate(lines):
            out.append(
                Text(
                    at=(min_x, top - index * paper(_SITE_NOTE_LINE_PAPER_UM)),
                    text=text,
                    layer=A_AREA,
                    height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                )
            )
    return (tuple(out), tuple(chains))


# ---------------------------------------------------------------------------
# Door / window schedule rows (§7 "group openings by (kind, w, h) -> tags")
# ---------------------------------------------------------------------------
def door_window_schedule(house: Any) -> DoorWindowSchedule:
    """The one tagging of this model's openings, shared by A-05 and every plan.

    :mod:`services.drawings.schedules.door_window` groups by ``(kind, w, h)`` exactly as
    §7 says, orders doors-then-windows-then-ventilators widest first, honours tags an
    opening already carries and never recycles a retired number. Both
    :func:`build_schedule_rows` (the A-05 table) and :func:`opening_tag_map` (what the
    plans print beside each opening) are views of this one object, which is what makes
    "the tag on the plan is the row in the schedule" true by construction rather than
    by review.
    """
    return build_schedule(house)


def opening_tag_map(house: Any) -> dict[str, str]:
    """``{opening id: tag}`` for the plans — the schedule's assignment, never a local one."""
    return dict(door_window_schedule(house).tag_by_opening_id)


def build_schedule_rows(house: Any) -> tuple[ScheduleRow, ...]:
    """A-05's rows, from :func:`door_window_schedule`.

    The sheet's table has a SILL column, so the module's ``Sill 900`` remark would print
    the same number twice; the remark is kept only when a group's sills differ, which
    is the one case a reader needs telling.
    """
    rows: list[ScheduleRow] = []
    for group in door_window_schedule(house).groups:
        row = group.to_row()
        rows.append(
            ScheduleRow(
                tag=row.tag,
                kind=row.kind,
                width_mm=row.width_mm,
                height_mm=row.height_mm,
                sill_mm=row.sill_mm,
                counts_by_storey=row.counts_by_storey,
                total=row.total,
                notes=row.notes if len(group.sills_mm) > 1 else "",
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
def _title_block(base: TitleBlock, *, title: str, number: str, scale: Scale) -> TitleBlock:
    from dataclasses import replace

    return replace(base, drawing_title=title, sheet_number=number, scale_label=scale.label)


def _sheet(
    *,
    sheet_id: str,
    kind: str,
    number: str,
    title: str,
    viewport: Viewport,
    scale: Scale,
    title_block: TitleBlock,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> Sheet:
    sheet = Sheet(
        id=sheet_id,
        kind=kind,  # type: ignore[arg-type]
        number=number,
        title=title,
        viewport=viewport,
        scale=scale,
        frame=layout.frame(_title_block(title_block, title=title, number=number, scale=scale)),
    )
    sheet.validate()
    return sheet


def floor_plan_sheet(
    doc: Any,
    storey_id: str,
    *,
    number: str,
    title_block: TitleBlock,
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    diff: ModelDiff | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
    opening_tags: Mapping[str, str] | None = None,
) -> SheetDrawing:
    """One storey's plan, with revision clouds when a diff against the previous issue
    is supplied.

    ``opening_tags`` is the schedule's ``{opening id: tag}``; left as ``None`` it is
    computed here by the same call the schedule sheet makes (see
    :func:`plan_primitives`).

    ``diff`` is a :class:`~services.drawings.revisions.ModelDiff` between the state the
    previous revision was issued at and this one. Its clouds are drawn in the plan's own
    model space, in the same group, so they scale and place with the building rather than
    floating over it.
    """
    house = doc.house
    storey = next(s for s in house.storeys if s.id == storey_id)
    frame = layout.frame()
    rect = content_rect(frame)
    extent = building_extent(house, storey_id)
    if extent is None:
        raise ValueError("storey %r has no walls, so it has no plan" % storey_id)
    # Pad the extent so the three dimension chain levels and the section bubbles
    # beyond them fit inside the sheet, and the sheet title clears the bubbles. The
    # pad is a paper distance (the chains are paper-spaced), so it is chosen at the
    # preferred scale and recomputed once the scale is known.
    pad = paper_to_model_mm(_PLAN_PAD_PAPER_UM, DEFAULT_SCALE.denominator)
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    denominator = choose_scale(padded, rect, preferred=DEFAULT_SCALE.denominator)
    pad = paper_to_model_mm(_PLAN_PAD_PAPER_UM, denominator)
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    section_line = choose_section_line(doc)
    primitives, chains = plan_primitives(
        doc,
        storey_id,
        scale_denominator=denominator,
        dim_to_jamb=dim_to_jamb,
        section_line=section_line,
        opening_tags=opening_tags,
    )
    revision_number = (register.latest.number if register and register.latest else "") or (
        title_block.revision if diff is not None else ""
    )
    clouds = _cloud_primitives(
        diff, storey_id, revision_number=revision_number, scale_denominator=denominator
    )
    if clouds:
        # A cloud sits outside the geometry it points at, and its delta tag sits outside
        # the cloud, so the padded extent computed for the dimension chains can be too
        # small for it. Grow the box to contain them and re-choose the scale ONCE — a
        # loop would be chasing its own tail, since the clouds are sized from the scale.
        # The clouds keep the size they were built at; at worst they print one scale step
        # small, which is a cosmetic difference and not a clipped annotation.
        cloud_extent = _extent_of(clouds)
        assert cloud_extent is not None
        padded = (
            min(padded[0], cloud_extent[0]),
            min(padded[1], cloud_extent[1]),
            max(padded[2], cloud_extent[2]),
            max(padded[3], cloud_extent[3]),
        )
        denominator = choose_scale(padded, rect, preferred=denominator)
        primitives = (*primitives, *clouds)
    scale = Scale(denominator)
    placement = fit_placement(padded, rect, denominator)
    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-plan-%s" % storey.name.lower().replace(" ", "-"),
        kind="floor-plan",
        number=number,
        title="%s Plan" % storey.name,
        viewport=Viewport(storey_id=storey_id),
        scale=scale,
        title_block=title_block,
    )
    label_at = ((extent[0] + extent[2]) // 2, padded[1] + paper_to_model_mm(3_000, denominator))
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            DrawingGroup(
                id="plan-%s" % storey_id,
                placement=placement,
                primitives=primitives,
                label="%s PLAN - %s" % (storey.name.upper(), scale.label),
                label_at=label_at,
            ),
        ),
        chains=chains,
        meta={"storeyId": storey_id, "kind": "floor-plan"},
    )


def elevation_sheet(
    doc: Any,
    direction: str,
    *,
    number: str,
    title_block: TitleBlock,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    frame = layout.frame()
    rect = content_rect(frame)
    primitives, chains = elevation_primitives(doc, direction, scale_denominator=100)
    if not primitives:
        raise ValueError("no facade geometry for direction %r" % direction)
    group = DrawingGroup(id="elev-%s" % direction, placement=Placement(100), primitives=primitives)
    extent = group.extent_model_mm()
    assert extent is not None
    pad = 1_500
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    denominator = choose_scale(padded, rect, preferred=DEFAULT_SCALE.denominator)
    if denominator != 100:
        primitives, chains = elevation_primitives(doc, direction, scale_denominator=denominator)
        group = DrawingGroup(
            id="elev-%s" % direction, placement=Placement(denominator), primitives=primitives
        )
        extent = group.extent_model_mm()
        assert extent is not None
        padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    scale = Scale(denominator)
    placement = fit_placement(padded, rect, denominator)
    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-elev-%s" % direction.lower(),
        kind="elevation",
        number=number,
        title="%s Elevation" % _DIRECTION_NAMES[direction].title(),
        viewport=Viewport(elevation_direction=direction),  # type: ignore[arg-type]
        scale=scale,
        title_block=title_block,
    )
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            DrawingGroup(
                id="elev-%s" % direction,
                placement=placement,
                primitives=primitives,
                label="%s ELEVATION - %s" % (_DIRECTION_NAMES[direction], scale.label),
                label_at=((extent[0] + extent[2]) // 2, padded[1] + 400),
            ),
        ),
        chains=chains,
        meta={"direction": direction, "kind": "elevation"},
    )


def section_sheet(
    doc: Any,
    *,
    number: str,
    title_block: TitleBlock,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    frame = layout.frame()
    rect = content_rect(frame)
    primitives, chains = section_primitives(doc, scale_denominator=100)
    if not primitives:
        raise ValueError("this model has no storeys, so it has no section")
    group = DrawingGroup(id="section", placement=Placement(100), primitives=primitives)
    extent = group.extent_model_mm()
    assert extent is not None
    pad = 1_500
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    denominator = choose_scale(padded, rect, preferred=DEFAULT_SCALE.denominator)
    scale = Scale(denominator)
    placement = fit_placement(padded, rect, denominator)
    cut = choose_section_line(doc)
    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-section-a",
        kind="section",
        number=number,
        title="Section A-A",
        viewport=Viewport(section_line=cut),
        scale=scale,
        title_block=title_block,
    )
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            DrawingGroup(
                id="section",
                placement=placement,
                primitives=primitives,
                label="SECTION A-A - %s" % scale.label,
                label_at=((extent[0] + extent[2]) // 2, padded[1] + 400),
            ),
        ),
        chains=chains,
        meta={"kind": "section"},
    )


def site_plan_sheet(
    doc: Any,
    *,
    number: str,
    title_block: TitleBlock,
    statement: Any = None,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    frame = layout.frame()
    rect = content_rect(frame)
    site_scales = (100, 125, 150, 200, 250, 500, 1000)
    primitives, chains = site_plan_primitives(doc, statement=statement, scale_denominator=200)
    if not primitives:
        raise ValueError("no plot boundary, so no site plan")
    group = DrawingGroup(id="site", placement=Placement(200), primitives=primitives)
    extent = group.extent_model_mm()
    assert extent is not None
    pad = 2_000
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    # No `preferred` here, and 1:100 is the finest scale offered: a site plan is drawn
    # as large as the sheet allows (a reviewer measures setbacks off it) but never
    # finer than 1:100, which is the convention for a plot drawing.
    denominator = choose_scale(padded, rect, scales=site_scales)
    if denominator != 200:
        # The chain offsets and the notes block are paper distances, so the drawing
        # is rebuilt at the scale it will actually print at (as the elevations are).
        primitives, chains = site_plan_primitives(
            doc, statement=statement, scale_denominator=denominator
        )
        group = DrawingGroup(id="site", placement=Placement(denominator), primitives=primitives)
        extent = group.extent_model_mm()
        assert extent is not None
        padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
        denominator = choose_scale(padded, rect, scales=site_scales)
    scale = Scale(denominator)
    placement = fit_placement(padded, rect, denominator)
    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-site",
        kind="site-plan",
        number=number,
        title="Site Plan",
        viewport=Viewport(storey_id=doc.house.storeys[0].id if doc.house.storeys else "site"),
        scale=scale,
        title_block=title_block,
    )
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            DrawingGroup(
                id="site",
                placement=placement,
                primitives=primitives,
                label="SITE PLAN - %s" % scale.label,
                label_at=((extent[0] + extent[2]) // 2, padded[1] + 500),
            ),
        ),
        chains=chains,
        meta={"kind": "site-plan"},
    )


def schedule_sheet(
    doc: Any,
    *,
    number: str,
    title_block: TitleBlock,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    house = doc.house
    rows = build_schedule_rows(house)
    storey_labels = tuple((storey.id, storey.name) for storey in house.storeys)
    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-schedule",
        kind="door-window-schedule",
        number=number,
        title="Door & Window Schedule",
        viewport=Viewport(),
        scale=DEFAULT_SCALE,
        title_block=title_block,
    )
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            schedule_group(rows, storey_labels=storey_labels, origin_mm=(25, 25)),
        ),
        meta={"kind": "door-window-schedule", "rows": str(len(rows))},
    )


#: ``garh_rules.areas.AreaStatement.rows()`` keys its per-storey built-up lines
#: ``built_up.<storeyId>``; ``built_up_total`` is the total and deliberately does not
#: match, because it is not a storey.
_BUILT_UP_PREFIX = "built_up."


def _storey_names(doc: Any) -> dict[str, str]:
    """``{storey_id: name}`` from a folded doc, a house, or either one's JSON."""
    house = doc.get("house", doc) if isinstance(doc, Mapping) else getattr(doc, "house", doc)
    storeys = (
        house.get("storeys") or ()
        if isinstance(house, Mapping)
        else getattr(house, "storeys", ()) or ()
    )
    if isinstance(house, Mapping):
        return {str(s["id"]): str(s.get("name") or "") for s in storeys}
    return {str(s.id): str(getattr(s, "name", "") or "") for s in storeys}


def carpet_lines_for(doc: Any, statement: Any) -> tuple[Any, ...]:
    """The carpet section's storey lines, derived from the model the sheet is drawn from.

    Carpet area is the one figure §7 asks for that the rules engine does not carry (its
    ``StoreyAreaRow`` has built-up and no carpet), so it comes from the same rooms every
    plan in the set is drawn from — see
    :func:`services.drawings.schedules.area_statement.carpet_by_storey`, which is the one
    definition of what counts as carpet.

    **Deriving it here rather than making every caller pass it is the point.** It used to
    be a keyword nobody supplied: :meth:`AreaStatementSheet.municipal_form` passed its own
    storeys, but the shipped path (``pipeline._sheet_plan``) and the §16 golden harness
    both passed nothing, so the sheet the product prints had no carpet section — and
    carpet is section 5, so every section after it was numbered one lower than the
    rendering the tests and the docstrings cite. A serial that differs between the tested
    sheet and the shipped sheet is not a citable serial.

    One line per built-up row the engine reported, in the engine's order, labelled the way
    the built-up rows are labelled. A storey with no rooms in the model gets a line with
    no carpet figure, which :func:`~services.drawings.schedules.municipal.municipal_form`
    prints as an omission plus a warning rather than as a zero.
    """
    if doc is None or statement is None:
        return ()
    from services.drawings.schedules.area_statement import StoreyLine, carpet_by_storey
    from services.drawings.schedules.display import storey_row_label

    try:
        carpet = carpet_by_storey(doc)
    except TypeError:
        # No readable rooms (a caller that passed a statement and no model). The carpet
        # section is absent, exactly as it is for a first-issue set — never guessed.
        return ()
    names = _storey_names(doc)
    lines: list[Any] = []
    storey_rows = [row for row in statement.rows() if str(row.key).startswith(_BUILT_UP_PREFIX)]
    for index, row in enumerate(storey_rows):
        storey_id = str(row.key)[len(_BUILT_UP_PREFIX) :]
        carpet_row = carpet.get(storey_id)
        built_up = (
            row.value if isinstance(row.value, int) and not isinstance(row.value, bool) else None
        )
        lines.append(
            StoreyLine(
                storey_id=storey_id,
                index=index,
                label=storey_row_label(names.get(storey_id, "")) or str(row.label),
                built_up_area_mm2=built_up,
                carpet_area_mm2=(carpet_row.carpet_area_mm2 if carpet_row is not None else None),
            )
        )
    return tuple(lines)


def area_statement_sheet(
    doc: Any,
    statement: Any,
    *,
    number: str,
    title_block: TitleBlock,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    carpet_lines: Sequence[Any] | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    """The area statement in the municipal proforma, with the revision register beneath it.

    The register goes here rather than on the site plan because this is the set's
    information sheet: it has the white space, and it is where a reviewer already looks
    for the numbers. Every *other* sheet still carries the compact REV/DATE/DESCRIPTION
    strip in its title block, fed from the same history — see :func:`_strip_rows`.

    ``carpet_lines`` defaults to :func:`carpet_lines_for` of ``doc`` and ``statement``, so
    every caller draws the same sheet with the same section numbers. Pass an explicit
    sequence (``()`` included) only to override that.
    """
    lines = carpet_lines_for(doc, statement) if carpet_lines is None else tuple(carpet_lines)
    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-areas",
        kind="area-statement",
        number=number,
        title="Area Statement",
        viewport=Viewport(),
        scale=DEFAULT_SCALE,
        title_block=title_block,
    )
    groups = [
        frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
        area_statement_group(statement, origin_mm=(25, 25), carpet_lines=lines),
    ]
    if register:
        below = 25 + area_statement_height_mm(statement, carpet_lines=lines) + 12
        groups.append(revision_register_group(register, origin_mm=(25, below)))
    return SheetDrawing(
        sheet=sheet,
        groups=tuple(groups),
        meta={"kind": "area-statement"},
    )


class SheetSet(tuple):
    """The generated sheets for one project, in submission order."""

    __slots__ = ()

    def by_kind(self, kind: str) -> tuple[SheetDrawing, ...]:
        return tuple(d for d in self if getattr(d.sheet, "kind", None) == kind)

    def all_chains(self) -> tuple[DimChain, ...]:
        chains: list[DimChain] = []
        for drawing in self:
            chains.extend(drawing.chains)
        return tuple(chains)


def build_sheet_set(
    doc: Any,
    *,
    title_block: TitleBlock | None = None,
    statement: Any = None,
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    diff: ModelDiff | None = None,
    carpet_lines: Sequence[Any] | None = None,
) -> SheetSet:
    """All F7-A sheets for a model: site, one plan per storey, four elevations, section, tables.

    One sheet per storey and one per elevation direction, rather than four elevations on
    one sheet: :class:`services.drawings.sheets.Viewport` requires exactly one selector,
    so a sheet showing four facades could not describe itself honestly. Numbering follows
    the §7 plan with a letter suffix (``A-02A``, ``A-02B``) — which is also how a set of
    submission prints is actually numbered.

    ``statement`` must be the :class:`garh_rules.areas.AreaStatement` from the project's
    compliance evaluation. It is a required input rather than something computed here, by
    design: this module must not be *able* to produce a second version of those numbers.

    ``register`` is the validated revision history (D-1). When it is present every sheet's
    title block carries the issue strip and A-06 carries the full register; when ``diff``
    is present too, the floor plans carry clouds around what the latest issue changed.
    Both are optional and both default to nothing, so a first issue costs no geometry.
    """
    block = title_block or TitleBlock()
    drawings: list[SheetDrawing] = []
    drawings.append(
        site_plan_sheet(
            doc,
            number="A-01",
            title_block=block,
            statement=statement,
            revisions=revisions,
            register=register,
        )
    )
    # One tagging for the whole set: the plans print it, A-05 tabulates it.
    tags = opening_tag_map(doc.house)
    for index, storey in enumerate(doc.house.storeys):
        if not _walls_of(doc.house, storey.id):
            continue
        drawings.append(
            floor_plan_sheet(
                doc,
                storey.id,
                number="A-02%s" % chr(ord("A") + index),
                title_block=block,
                dim_to_jamb=dim_to_jamb,
                revisions=revisions,
                register=register,
                diff=diff,
                opening_tags=tags,
            )
        )
    for index, direction in enumerate(("N", "E", "S", "W")):
        drawings.append(
            elevation_sheet(
                doc,
                direction,
                number="A-03%s" % chr(ord("A") + index),
                title_block=block,
                revisions=revisions,
                register=register,
            )
        )
    drawings.append(
        section_sheet(doc, number="A-04", title_block=block, revisions=revisions, register=register)
    )
    drawings.append(
        schedule_sheet(
            doc, number="A-05", title_block=block, revisions=revisions, register=register
        )
    )
    if statement is not None:
        drawings.append(
            area_statement_sheet(
                doc,
                statement,
                number="A-06",
                title_block=block,
                revisions=revisions,
                register=register,
                carpet_lines=carpet_lines,
            )
        )
    return SheetSet(drawings)


# ---------------------------------------------------------------------------
# D-2 — the setting-out plan (the GFC drawing that goes to site)
# ---------------------------------------------------------------------------
#: Arm length of the datum cross, in model mm. Big enough to find on an A2 print.
DATUM_ARM_MM = 600

#: Radius of the column marker on the setting-out plan.
COLUMN_MARK_MM = 150


def setting_out_primitives(
    house: Any, storey_id: str
) -> tuple[list[Primitive], tuple[DimChain, ...], Pt2]:
    """Wall CENTRELINES, columns and a datum — the drawing a site engineer works from.

    This is deliberately not a floor plan with the furniture switched off. A submission
    plan answers "is this compliant"; a setting-out plan answers "where do I put the
    first brick", and the two are drawn differently on purpose:

    * **Centrelines, not poché.** A mason sets out to a line, not to a face. The model
      already stores each wall as ``a``/``b`` — that IS the centreline, so nothing is
      derived and nothing can drift from the plan sheet.
    * **Every dimension from ONE datum**, not chained bay to bay. A chained dimension
      accumulates the setting-out error of every bay before it; a running dimension
      from a single corner does not. This is why the sheet states its datum in words
      as well as drawing it.
    * **No openings, no fittings, no room names.** They are not set out at this stage
      and they would bury the only lines that matter.

    Returns ``(primitives, chains, datum)``. The datum is the building's own
    lower-left extent corner — the corner a site engineer can find with a tape from
    two boundary lines.
    """
    walls = [w for w in house.walls if w.storey_id == storey_id]
    if not walls:
        return [], (), (0, 0)

    xs = [v for w in walls for v in (w.a.x, w.b.x)]
    ys = [v for w in walls for v in (w.a.y, w.b.y)]
    datum: Pt2 = (min(xs), min(ys))

    out: list[Primitive] = []
    for wall in walls:
        out.append(
            Line(
                (wall.a.x, wall.a.y),
                (wall.b.x, wall.b.y),
                A_WALL,
                style=STYLE_CENTRE,
                element_id=wall.id,
            )
        )

    for column in getattr(house, "columns", ()):
        if getattr(column, "storey_id", storey_id) != storey_id:
            continue
        cx, cy = column.pt.x, column.pt.y
        out.append(Circle((cx, cy), COLUMN_MARK_MM, A_WALL, element_id=column.id))
        # A cross through the circle: a filled dot is ambiguous at 1:100, and the
        # engineer needs the centre, not the blob.
        out.append(Line((cx - COLUMN_MARK_MM, cy), (cx + COLUMN_MARK_MM, cy), A_WALL))
        out.append(Line((cx, cy - COLUMN_MARK_MM), (cx, cy + COLUMN_MARK_MM), A_WALL))

    # The datum itself, drawn where it is and named in words. A setting-out plan whose
    # datum is implicit is a setting-out plan nobody can use.
    dx, dy = datum
    out.append(Line((dx - DATUM_ARM_MM, dy), (dx + DATUM_ARM_MM, dy), A_DIM))
    out.append(Line((dx, dy - DATUM_ARM_MM), (dx, dy + DATUM_ARM_MM), A_DIM))
    out.append(Circle(datum, DATUM_ARM_MM // 3, A_DIM))
    out.append(
        Text(
            at=(dx + DATUM_ARM_MM + 200, dy - 400),
            text="DATUM (0,0) - ALL DIMENSIONS FROM THIS POINT",
            layer=A_TEXT,
            height_paper_um=2_000,
            anchor="start",
        )
    )

    # Running dimensions off the datum: every distinct wall-end coordinate becomes a
    # break, so each line the engineer has to place gets a number measured from the
    # datum rather than from its neighbour.
    chains: list[DimChain] = []
    horizontal = _chain_from_breaks(
        chain_id="setout-x-%s" % storey_id,
        orientation="horizontal",
        level=1,
        offset_mm=min(ys) - LEVEL_1_OFFSET_MM,
        lo=min(xs),
        hi=max(xs),
        breaks=sorted({x for x in xs if min(xs) < x < max(xs)}),
        storey_id=storey_id,
    )
    vertical = _chain_from_breaks(
        chain_id="setout-y-%s" % storey_id,
        orientation="vertical",
        level=1,
        offset_mm=min(xs) - LEVEL_1_OFFSET_MM,
        lo=min(ys),
        hi=max(ys),
        breaks=sorted({y for y in ys if min(ys) < y < max(ys)}),
        storey_id=storey_id,
    )
    for chain in (horizontal, vertical):
        if chain is not None:
            chains.append(chain)

    # Draw them. A chain returned in `SheetDrawing.chains` and never turned into a
    # Dim primitive is annotation the sheet believes it has and the printer never
    # sees — and on a setting-out plan the dimensions ARE the drawing; the
    # centrelines without them are decoration.
    assert_chains_sum(tuple(chains))
    out.extend(
        Dim(chain=chain, layer=A_DIM, text_height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM)
        for chain in chains
    )

    return out, tuple(chains), datum


def setting_out_sheet(
    doc: Any,
    storey_id: str,
    *,
    number: str,
    title_block: TitleBlock,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    """One storey's setting-out plan (D-2, job J-21).

    An architect's fee is earned twice — once at submission and once on site — and
    until now this product only produced the first half. This is the sheet the site
    engineer actually holds.
    """
    house = doc.house
    # Named, not `next(...)`: an unknown storey id off a job payload otherwise
    # surfaces as a bare StopIteration three frames away from the cause, and the
    # worker reports it as an unexplained skipped sheet.
    storey = next((s for s in house.storeys if s.id == storey_id), None)
    if storey is None:
        raise ValueError("no storey %r, so it has nothing to set out" % storey_id)
    frame = layout.frame()
    rect = content_rect(frame)
    primitives, chains, _datum = setting_out_primitives(house, storey_id)
    if not primitives:
        raise ValueError("storey %r has no walls, so it has nothing to set out" % storey_id)

    group = DrawingGroup(id="setout", placement=Placement(100), primitives=primitives)
    extent = group.extent_model_mm()
    assert extent is not None
    pad = LEVEL_1_OFFSET_MM + 1_200
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    denominator = choose_scale(padded, rect, preferred=DEFAULT_SCALE.denominator)
    scale = Scale(denominator)
    placement = fit_placement(padded, rect, denominator)

    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-setout-%s" % storey_id,
        kind="setting-out",
        number=number,
        title="Setting Out - %s" % storey.name,
        viewport=Viewport(storey_id=storey_id),
        scale=scale,
        title_block=title_block,
    )
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            DrawingGroup(
                id="setout",
                placement=placement,
                primitives=primitives,
                label="SETTING OUT - %s - %s" % (storey.name.upper(), scale.label),
                label_at=((extent[0] + extent[2]) // 2, padded[1] + 500),
            ),
        ),
        chains=chains,
        meta={"kind": "setting-out", "storeyId": storey_id},
    )


# ---------------------------------------------------------------------------
# D-7 — the structural grid (working drawing W-02)
# ---------------------------------------------------------------------------
#: How far the grid line runs past the outermost column before its bubble.
GRID_OVERRUN_MM = 1_500

#: Radius of a grid bubble, model mm. Sized so the letter inside stays legible at 1:100.
GRID_BUBBLE_MM = 450

#: Columns within this distance of each other in one axis are on the same grid line.
#: 75 mm because a 230 mm column nudged half a brick during layout is still meant to be
#: on its grid, and a tolerance of zero would give every such column a grid of its own —
#: which is a grid nobody can build from.
GRID_TOLERANCE_MM = 75


def _grid_positions(values: Sequence[int], tolerance: int = GRID_TOLERANCE_MM) -> list[int]:
    """Cluster ordinates into grid lines, returning one representative each.

    The representative is the cluster's ROUNDED MEAN rather than its first member: a
    grid taken from whichever column happened to be drawn first inherits that column's
    setting-out error, and the engineer then dimensions everything from it.
    """
    if not values:
        return []
    ordered = sorted(values)
    clusters: list[list[int]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    # `div_round`, not a float mean: every coordinate in this repository is an
    # integer millimetre, and half-away-from-zero is the rounding contract.
    return [div_round(sum(c), len(c)) for c in clusters]


def structural_grid_primitives(house: Any, storey_id: str) -> tuple[list[Primitive], list[dict]]:
    """Grid lines, bubbles and the column marks they organise.

    Returns ``(primitives, schedule_rows)``. The schedule is returned rather than drawn
    so the caller can put it in a table using the shared table machinery — one column
    size appearing twice in a schedule and once on the plan is the kind of disagreement
    a contractor prices twice.
    """
    columns = [c for c in getattr(house, "columns", ()) if c.storey_id == storey_id]
    if not columns:
        return [], []

    xs = _grid_positions([c.pt.x for c in columns])
    ys = _grid_positions([c.pt.y for c in columns])
    x_lo, x_hi = min(c.pt.x for c in columns), max(c.pt.x for c in columns)
    y_lo, y_hi = min(c.pt.y for c in columns), max(c.pt.y for c in columns)

    out: list[Primitive] = []

    def bubble(at: Pt2, label: str) -> None:
        out.append(Circle(at, GRID_BUBBLE_MM, A_DIM))
        out.append(
            Text(
                at=at,
                text=label,
                layer=A_TEXT,
                height_paper_um=2_500,
                anchor="middle",
                baseline="middle",
            )
        )

    # Vertical grid lines are LETTERED left to right, horizontals NUMBERED bottom to
    # top. That is the drafting convention, and getting it the other way round makes
    # every "column at B/3" on a structural drawing point somewhere else.
    for index, x in enumerate(xs):
        out.append(
            Line(
                (x, y_lo - GRID_OVERRUN_MM),
                (x, y_hi + GRID_OVERRUN_MM),
                A_DIM,
                style=STYLE_CENTRE,
            )
        )
        bubble((x, y_hi + GRID_OVERRUN_MM + GRID_BUBBLE_MM), chr(ord("A") + index))

    for index, y in enumerate(ys):
        out.append(
            Line(
                (x_lo - GRID_OVERRUN_MM, y),
                (x_hi + GRID_OVERRUN_MM, y),
                A_DIM,
                style=STYLE_CENTRE,
            )
        )
        bubble((x_lo - GRID_OVERRUN_MM - GRID_BUBBLE_MM, y), str(index + 1))

    # The columns themselves, drawn at their real size — a structural plan that shows
    # columns as dots hides the one number the engineer is checking.
    schedule: list[dict] = []
    for column in sorted(columns, key=lambda c: (c.pt.y, c.pt.x)):
        half_w = column.size_mm.x_mm // 2
        half_d = column.size_mm.y_mm // 2
        cx, cy = column.pt.x, column.pt.y
        out.append(
            Polyline(
                vertices=(
                    (cx - half_w, cy - half_d),
                    (cx + half_w, cy - half_d),
                    (cx + half_w, cy + half_d),
                    (cx - half_w, cy + half_d),
                ),
                layer=A_WALL,
                closed=True,
                element_id=column.id,
            )
        )
        ref = "%s/%s" % (
            chr(ord("A") + min(range(len(xs)), key=lambda i: abs(xs[i] - cx))),
            min(range(len(ys)), key=lambda i: abs(ys[i] - cy)) + 1,
        )
        schedule.append(
            {
                "ref": ref,
                "size": "%d x %d" % (column.size_mm.x_mm, column.size_mm.y_mm),
                "x": cx,
                "y": cy,
            }
        )

    return out, schedule


def structural_grid_sheet(
    doc: Any,
    storey_id: str,
    *,
    number: str,
    title_block: TitleBlock,
    revisions: Sequence[tuple[str, str, str]] = (),
    register: RevisionHistory | None = None,
    layout: SheetLayout = DEFAULT_SHEET_LAYOUT,
) -> SheetDrawing:
    """One storey's structural grid (D-7).

    Refuses a storey with no columns rather than issuing an empty sheet. That is the
    common case, not an edge case: most Indian residential work is load-bearing
    masonry, and a "structural grid" for a house with no frame is a drawing that
    implies an engineer was involved when none was.
    """
    house = doc.house
    storey = next((s for s in house.storeys if s.id == storey_id), None)
    if storey is None:
        raise ValueError("no storey %r, so it has no structural grid" % storey_id)
    frame = layout.frame()
    rect = content_rect(frame)
    primitives, _schedule = structural_grid_primitives(house, storey_id)
    if not primitives:
        raise ValueError(
            "storey %r has no columns, so it has no structural grid — a load-bearing "
            "plan is set out from its walls (see the setting-out plan)" % storey_id
        )

    group = DrawingGroup(id="grid", placement=Placement(100), primitives=primitives)
    extent = group.extent_model_mm()
    assert extent is not None
    pad = 1_200
    padded = (extent[0] - pad, extent[1] - pad, extent[2] + pad, extent[3] + pad)
    denominator = choose_scale(padded, rect, preferred=DEFAULT_SCALE.denominator)
    scale = Scale(denominator)
    placement = fit_placement(padded, rect, denominator)

    sheet = _sheet(
        layout=layout,
        sheet_id="sheet-grid-%s" % storey_id,
        kind="structural-grid",
        number=number,
        title="Structural Grid - %s" % storey.name,
        viewport=Viewport(storey_id=storey_id),
        scale=scale,
        title_block=title_block,
    )
    return SheetDrawing(
        sheet=sheet,
        groups=(
            frame_group(sheet.frame, revisions=_strip_rows(revisions, register)),
            DrawingGroup(
                id="grid",
                placement=placement,
                primitives=primitives,
                label="STRUCTURAL GRID - %s - %s" % (storey.name.upper(), scale.label),
                label_at=((extent[0] + extent[2]) // 2, padded[1] + 500),
            ),
        ),
        chains=(),
        meta={"kind": "structural-grid", "storeyId": storey_id},
    )
