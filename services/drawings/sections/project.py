"""The section projector: one cut, through the staircase, drawn from the model (§7).

    Section (through stair): section line auto-chosen through stair flight + one wet area
    if possible; show storey heights chain, sill/lintel heights, plinth, parapet, mumty,
    foundation indicative line (900mm below plinth, dashed, labeled
    "INDICATIVE — REFER STRUCTURAL").

Everything in that list is here, and two items in it are liability boundaries rather than
draughting:

**The foundation line.** It is dashed, it sits exactly 900mm below the plinth level the
model carries, and it is labelled with :data:`FOUNDATION_LABEL` — the playbook's exact
string, not a paraphrase. This drawing does not design foundations; the line says where one
goes and the label says whose drawing decides. Changing that text changes what the sheet
claims, so it is a constant with a test on it.

**The mumty.** The model has a ``mumty`` slab kind but nothing generates one yet, so when
the top storey has a stair the section derives an indicative mumty over the stair well —
the same thing §8's 3D synthesis does ("mumty box over stair") — clearly labelled
INDICATIVE and recorded in the notes. If the model *does* carry a mumty slab, that wins.
Deriving it can be switched off with ``SectionOptions.include_derived_mumty``.

How the cut is built
--------------------
The whole projection is interval arithmetic on integers. For each storey the cut line is
intersected with the walls (giving the poché blocks), with the openings in those walls
(giving the voids the blocks are subtracted around — this is where sill and lintel become
visible), and with the floor slab polygon minus its stair-well cutouts (giving the slab
bands). Vertically, a wall runs from its FFL to the underside of the slab above, so the
drawing reads as slab-wall-slab rather than as overlapping rectangles.

The one modelling gap is the stair itself, and it is documented where it lives:
:mod:`services.drawings.sections.stair`. A dogleg draws its first flight and its landing
and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from services.drawings.dimensions import DEFAULT_DIM_TO_JAMB, DimChain
from services.drawings.layers import A_STAIR, A_TEXT, A_WALL, A_WALL_PART
from services.drawings.projection.primitives import (
    PATTERN_CONCRETE,
    PATTERN_MASONRY,
    K_STAIR_LABEL,
    K_WALL_FACE,
    K_WALL_HATCH,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Text,
    sanitise_text,
    validate_primitives,
)
from services.drawings.elevations.facade import footprint_rings, wall_rect
from services.drawings.elevations.vertical import (
    K_FOUNDATION_LABEL,
    K_FOUNDATION_LINE,
    K_MUMTY,
    K_PARAPET,
    K_PLINTH,
    K_SLAB_EDGE,
    K_STAIR_PROFILE,
    K_TITLE,
    Interval,
    LevelMarker,
    LevelSet,
    VerticalDrawing,
    VerticalStyle,
    build_levels,
    height_chain,
    height_chain_primitives,
    level_marker_primitives,
    merge_intervals,
    normals_of,
    rect_ring,
    ring_line_intervals,
    subtract_intervals,
    u_of,
)
from services.drawings.sections.choose import CutLine, SectionChoice, choose_section_line
from services.drawings.sections.stair import StairGeometry, stair_geometry

__all__ = [
    "FOUNDATION_DEPTH_BELOW_PLINTH_MM",
    "FOUNDATION_LABEL",
    "FOUNDATION_OVERRUN_PAPER_MM",
    "MUMTY_CLEAR_HEIGHT_MM",
    "MUMTY_SLAB_MM",
    "MUMTY_WALL_MM",
    "PARAPET_THICKNESS_MM",
    "SectionOptions",
    "SectionResult",
    "build_section",
]

#: §7, verbatim. The line is indicative; this text is the liability boundary that says so.
#: Tested for exactly this string — a reworded version is a different claim.
FOUNDATION_LABEL = "INDICATIVE — REFER STRUCTURAL"
#: §7: "900mm below plinth".
FOUNDATION_DEPTH_BELOW_PLINTH_MM = 900
#: How far the foundation line runs past the building each side, **paper** mm.
FOUNDATION_OVERRUN_PAPER_MM = 9.0

#: Mirror of ``garh_model.model.DEFAULTS.parapet_thickness_mm``; asserted equal in tests.
PARAPET_THICKNESS_MM = 115
#: Indicative mumty: clear headroom under its slab, its slab, and its wall thickness.
MUMTY_CLEAR_HEIGHT_MM = 2_100
MUMTY_SLAB_MM = 125
MUMTY_WALL_MM = 115


@dataclass(frozen=True)
class SectionOptions:
    """Per-sheet knobs for the section. Every default is §7's."""

    scale_denominator: int = 100
    label: str = "A"
    #: Derive a mumty over the top-storey stair when the model has no mumty slab (§8's
    #: "mumty box over stair"). Always drawn labelled INDICATIVE.
    include_derived_mumty: bool = True
    mumty_clear_height_mm: int = MUMTY_CLEAR_HEIGHT_MM
    #: Carried for parity with the elevations and the plan; a vertical drawing dimensions
    #: openings by sill and lintel *height*, where there is no jamb to measure to.
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB
    material_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionResult:
    """The drawing, the line it was cut on, and why that line won.

    ``viewport_line`` is the cut in **plan** coordinates, run past the building both ways:
    exactly the shape ``services.drawings.sheets.Viewport.section_line`` persists, and the
    two points the plan projector needs to draw §7's section marker on the floor plan. It
    is handed over rather than recomputed, so the marker on the plan and the cut that
    produced the section can never drift apart.
    """

    drawing: VerticalDrawing
    line: Optional[CutLine]
    choice: Optional[SectionChoice]
    stairs: Tuple[StairGeometry, ...] = ()
    viewport_line: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "line": self.line.to_json() if self.line else None,
            "sectionLine": [list(point) for point in self.viewport_line]
            if self.viewport_line
            else None,
            "choice": self.choice.to_json() if self.choice else None,
            "stairs": [s.to_json() for s in self.stairs],
            "drawing": self.drawing.to_json(),
        }


def _model_bbox(house: Any) -> Tuple[int, int, int, int]:
    xs: List[int] = []
    ys: List[int] = []
    for wall in house.walls:
        for point in (wall.a, wall.b):
            xs.append(int(point.x))
            ys.append(int(point.y))
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))


def _openings_of_wall(house: Any, wall_id: str) -> List[Any]:
    return sorted(
        (o for o in house.openings if str(o.wall_id) == wall_id), key=lambda o: str(o.id)
    )


def _block(
    u_span: Interval,
    z_span: Interval,
    *,
    owner_id: Optional[str],
    kind: str,
    pattern: str,
    sizes: VerticalStyle,
    layer: str = A_WALL,
) -> Tuple[Primitive, ...]:
    """A cut solid: outline plus poché.

    Both ride on ``A-WALL``. The nine §7 layers have no dedicated poché layer, and
    ``A-AREA`` is the plan's room-area layer — putting section masonry there would make a
    reviewer's "areas" layer render as hatching, which is worse than sharing a layer with
    the outline the hatch belongs to.
    """
    ring = rect_ring(u_span[0], z_span[0], u_span[1], z_span[1])
    return (
        Polyline(layer, ring, closed=True, owner_id=owner_id, kind=kind),
        Hatch(
            layer,
            ring,
            pattern=pattern,
            spacing_mm=sizes.style.hatch_spacing_mm,
            owner_id=owner_id,
            kind=K_WALL_HATCH,
        ),
    )


def build_section(
    house: Any,
    *,
    line: Optional[CutLine] = None,
    options: Optional[SectionOptions] = None,
) -> SectionResult:
    """Project the §7 section. Chooses its own cut line unless one is given."""
    opts = options or SectionOptions()
    scale = opts.scale_denominator
    sizes = VerticalStyle.of(scale)
    choice: Optional[SectionChoice] = None
    if line is None:
        choice = choose_section_line(house, label=opts.label)
        if choice.best is None:
            levels_empty = build_levels(house)
            return SectionResult(
                drawing=VerticalDrawing(
                    kind="section",
                    name="SECTION %s-%s" % (opts.label, opts.label),
                    direction="E",
                    primitives=(),
                    level_markers=levels_empty.markers(),
                    chains=(),
                    levels=levels_empty,
                    notes=choice.notes,
                    scale_denominator=scale,
                ),
                line=None,
                choice=choice,
            )
        line = choice.best.line

    normal, u_axis = normals_of(line.view_direction)
    levels: LevelSet = build_levels(house)
    footprints = footprint_rings(house)
    notes: List[str] = list(choice.notes) if choice else []
    primitives: List[Primitive] = []

    if not levels.storeys or not footprints:
        notes.append("Nothing built yet on any storey, so the section has nothing to cut.")
        return SectionResult(
            drawing=VerticalDrawing(
                kind="section",
                name=line.name(),
                direction=line.view_direction,
                primitives=(),
                level_markers=levels.markers(),
                chains=(),
                levels=levels,
                notes=tuple(notes),
                scale_denominator=scale,
            ),
            line=line,
            choice=choice,
        )

    all_points: List[Tuple[int, int]] = []
    for ring in footprints.values():
        all_points.extend(ring)
    u_origin = min(u_of(x, y, u_axis) for x, y in all_points)

    def su(value: int) -> int:
        return value - u_origin

    def shift(spans: Sequence[Interval]) -> Tuple[Interval, ...]:
        return tuple((su(lo), su(hi)) for lo, hi in spans)

    storeys = levels.storeys
    #: The terrace slab is not a model entity (no storey sits above it), so its thickness is
    #: assumed equal to the top storey's floor slab. Said out loud in the notes.
    terrace_slab_mm = storeys[-1].slab_thickness_mm
    notes.append(
        "Terrace slab shown %dmm thick, assumed equal to the top floor slab: the model has "
        "no storey above the terrace to carry a thickness." % terrace_slab_mm
    )

    # ---- storey by storey: walls (with their openings) and the slab under ----
    for index, storey in enumerate(storeys):
        slab_above = (
            storeys[index + 1].slab_thickness_mm if index + 1 < len(storeys) else terrace_slab_mm
        )
        wall_z: Interval = (storey.ffl_mm, storey.top_mm - slab_above)

        for wall in sorted(house.walls, key=lambda w: str(w.id)):
            if str(wall.storey_id) != storey.storey_id:
                continue
            rect = wall_rect(wall)
            if rect is None or not line.straddles(rect):
                continue
            u_lo = min(u_of(rect[0], rect[1], u_axis), u_of(rect[2], rect[3], u_axis))
            u_hi = max(u_of(rect[0], rect[1], u_axis), u_of(rect[2], rect[3], u_axis))
            span = (su(u_lo), su(u_hi))

            voids: List[Interval] = []
            for opening in _openings_of_wall(house, str(wall.id)):
                o_rect = _opening_model_rect(house, wall, opening)
                if o_rect is None or not line.straddles(o_rect):
                    continue
                z_lo = storey.ffl_mm + int(opening.sill_mm)
                voids.append((z_lo, z_lo + int(opening.height_mm)))
                # Sill and lintel read as the two edges of the void.
                for z in (z_lo, z_lo + int(opening.height_mm)):
                    primitives.append(
                        Line(
                            A_WALL_PART,
                            (span[0], z),
                            (span[1], z),
                            owner_id=str(opening.id),
                            kind="sill-lintel-line",
                        )
                    )
            for block in subtract_intervals([wall_z], voids):
                primitives.extend(
                    _block(
                        span,
                        block,
                        owner_id=str(wall.id),
                        kind=K_WALL_FACE,
                        pattern=PATTERN_MASONRY,
                        sizes=sizes,
                    )
                )

        # The floor slab of every storey above the ground: cut, minus its stair wells.
        if index == 0:
            continue
        slab = _floor_slab_of(house, storey.storey_id)
        if slab is None:
            continue
        spans = ring_line_intervals(
            tuple((int(p.x), int(p.y)) for p in slab.polygon),
            axis=line.axis,
            position_mm=line.position_mm,
            u_axis=u_axis,
        )
        holes: List[Interval] = []
        for cutout in slab.cutouts:
            holes.extend(
                ring_line_intervals(
                    tuple((int(p.x), int(p.y)) for p in cutout),
                    axis=line.axis,
                    position_mm=line.position_mm,
                    u_axis=u_axis,
                )
            )
        z_span = (storey.ffl_mm - storey.slab_thickness_mm, storey.ffl_mm)
        for span in shift(subtract_intervals(spans, holes)):
            primitives.extend(
                _block(
                    span,
                    z_span,
                    owner_id=str(slab.id),
                    kind=K_SLAB_EDGE,
                    pattern=PATTERN_CONCRETE,
                    sizes=sizes,
                )
            )

    # ---- plinth: solid from the datum up to the ground FFL -----------------
    ground_ring = footprints.get(storeys[0].storey_id)
    ground_spans: Tuple[Interval, ...] = ()
    if ground_ring is not None:
        ground_spans = shift(
            ring_line_intervals(
                ground_ring, axis=line.axis, position_mm=line.position_mm, u_axis=u_axis
            )
        )
        for span in ground_spans:
            primitives.extend(
                _block(
                    span,
                    (levels.datum_mm, levels.plinth_mm),
                    owner_id=None,
                    kind=K_PLINTH,
                    pattern=PATTERN_MASONRY,
                    sizes=sizes,
                )
            )

    # ---- terrace slab, parapet, mumty --------------------------------------
    top_ring = footprints.get(storeys[-1].storey_id)
    terrace_spans: Tuple[Interval, ...] = ()
    if top_ring is not None:
        raw_terrace = ring_line_intervals(
            top_ring, axis=line.axis, position_mm=line.position_mm, u_axis=u_axis
        )
        top_stair_wells: List[Interval] = []
        for stair in house.stairs:
            if str(stair.storey_id) != storeys[-1].storey_id:
                continue
            geometry = stair_geometry(stair)
            if line.straddles(geometry.footprint):
                top_stair_wells.append(_rect_u_span(geometry.footprint, u_axis))
        terrace_spans = shift(subtract_intervals(raw_terrace, top_stair_wells))
        for span in terrace_spans:
            primitives.extend(
                _block(
                    span,
                    (levels.terrace_mm - terrace_slab_mm, levels.terrace_mm),
                    owner_id=None,
                    kind=K_SLAB_EDGE,
                    pattern=PATTERN_CONCRETE,
                    sizes=sizes,
                )
            )
        if levels.parapet_height_mm > 0 and terrace_spans:
            # The cut crosses the parapet at the two ends of the terrace, not along it.
            edges = (terrace_spans[0][0], terrace_spans[-1][1])
            for u_edge, direction in ((edges[0], 1), (edges[1], -1)):
                lo = min(u_edge, u_edge + direction * PARAPET_THICKNESS_MM)
                primitives.extend(
                    _block(
                        (lo, lo + PARAPET_THICKNESS_MM),
                        (levels.terrace_mm, levels.parapet_top_mm),
                        owner_id=None,
                        kind=K_PARAPET,
                        pattern=PATTERN_MASONRY,
                        sizes=sizes,
                    )
                )

    mumty_markers, mumty_primitives, mumty_notes = _mumty(
        house,
        line=line,
        u_axis=u_axis,
        shift=shift,
        levels=levels,
        options=opts,
        sizes=sizes,
    )
    primitives.extend(mumty_primitives)
    notes.extend(mumty_notes)

    # ---- the stairs the cut actually crosses -------------------------------
    geometries: List[StairGeometry] = []
    for stair in sorted(house.stairs, key=lambda s: str(s.id)):
        geometry = stair_geometry(stair)
        if not line.straddles(geometry.footprint):
            continue
        geometries.append(geometry)
        storey = levels.storey(geometry.storey_id)
        if storey is None:
            continue
        stair_primitives, stair_notes = _stair_primitives(
            geometry,
            line=line,
            u_axis=u_axis,
            shift_one=su,
            ffl_mm=storey.ffl_mm,
            sizes=sizes,
        )
        primitives.extend(stair_primitives)
        notes.extend(stair_notes)

    # ---- extent, foundation line, markers, chain, title -------------------
    spans_all = merge_intervals(list(ground_spans) + list(terrace_spans))
    if spans_all:
        u_left, u_right = spans_all[0][0], spans_all[-1][1]
    else:
        u_left, u_right = 0, 0

    overrun = sizes.style.paper_to_model_mm(FOUNDATION_OVERRUN_PAPER_MM)
    foundation_z = levels.plinth_mm - FOUNDATION_DEPTH_BELOW_PLINTH_MM
    primitives.append(
        Line(
            A_WALL_PART,
            (u_left - overrun, foundation_z),
            (u_right + overrun, foundation_z),
            dashed=True,
            kind=K_FOUNDATION_LINE,
        )
    )
    dim_height = sizes.dim_text_mm
    primitives.append(
        Text(
            A_TEXT,
            ((u_left + u_right) // 2, foundation_z - dim_height),
            sanitise_text(FOUNDATION_LABEL),
            dim_height,
            h_align="center",
            v_align="top",
            kind=K_FOUNDATION_LABEL,
        )
    )
    notes.append(
        "Foundation line is indicative only, %dmm below plinth level (%+d): %s."
        % (FOUNDATION_DEPTH_BELOW_PLINTH_MM, foundation_z, FOUNDATION_LABEL)
    )

    markers = levels.markers() + mumty_markers
    primitives.extend(level_marker_primitives(markers, u_left_mm=u_left, sizes=sizes))
    chain: DimChain = height_chain(
        levels,
        chain_id="section-%s-height" % opts.label.lower(),
        offset_mm=u_right + sizes.chain_offset_mm,
    )
    primitives.extend(
        height_chain_primitives(chain, sizes=sizes, witness_from_u_mm=u_right)
    )

    title = "%s — LOOKING %s" % (line.name(), line.looking)
    title_height = sizes.title_text_mm
    primitives.append(
        Text(
            A_TEXT,
            ((u_left + u_right) // 2, foundation_z - dim_height * 3 - title_height),
            sanitise_text(title),
            title_height,
            h_align="center",
            v_align="top",
            kind=K_TITLE,
        )
    )

    validate_primitives(primitives)
    return SectionResult(
        drawing=VerticalDrawing(
            kind="section",
            name=title,
            direction=line.view_direction,
            primitives=tuple(primitives),
            level_markers=markers,
            chains=(chain,),
            levels=levels,
            notes=tuple(notes),
            scale_denominator=scale,
        ),
        line=line,
        choice=choice,
        stairs=tuple(geometries),
        viewport_line=line.endpoints(_model_bbox(house), overrun_mm=overrun * 2),
    )


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------
def _floor_slab_of(house: Any, storey_id: str) -> Optional[Any]:
    for slab in getattr(house, "slabs", ()) or ():
        if str(slab.storey_id) == storey_id and slab.kind == "floor" and len(slab.polygon) >= 3:
            return slab
    return None


def _rect_u_span(rect: Tuple[int, int, int, int], u_axis: Tuple[int, int]) -> Interval:
    """``u`` span of an axis-aligned model rectangle (raw, before the origin shift)."""
    values = [
        u_of(rect[0], rect[1], u_axis),
        u_of(rect[2], rect[1], u_axis),
        u_of(rect[0], rect[3], u_axis),
        u_of(rect[2], rect[3], u_axis),
    ]
    return (min(values), max(values))


def _opening_model_rect(house: Any, wall: Any, opening: Any) -> Optional[Tuple[int, int, int, int]]:
    """The opening's plan rectangle: jamb to jamb along the wall, wall-thick across it."""
    rect = wall_rect(wall)
    if rect is None:
        return None
    a = (int(wall.a.x), int(wall.a.y))
    b = (int(wall.b.x), int(wall.b.y))
    length = abs(b[0] - a[0]) + abs(b[1] - a[1])
    if length <= 0:
        return None
    step = ((b[0] - a[0]) // length, (b[1] - a[1]) // length)
    width = int(opening.width_mm)
    near = int(opening.offset_mm) - width // 2
    p1 = (a[0] + step[0] * near, a[1] + step[1] * near)
    p2 = (a[0] + step[0] * (near + width), a[1] + step[1] * (near + width))
    if step[1] == 0:
        return (min(p1[0], p2[0]), rect[1], max(p1[0], p2[0]), rect[3])
    return (rect[0], min(p1[1], p2[1]), rect[2], max(p1[1], p2[1]))


def _stair_primitives(
    geometry: StairGeometry,
    *,
    line: CutLine,
    u_axis: Tuple[int, int],
    shift_one: Callable[[int], int],
    ffl_mm: int,
    sizes: VerticalStyle,
) -> Tuple[Tuple[Primitive, ...], Tuple[str, ...]]:
    """The stair as the cut sees it: a stepped profile, or an honest cross-cut block."""
    out: List[Primitive] = []
    notes: List[str] = []
    height = sizes.dim_text_mm

    travel_axis = "x" if geometry.direction in ("N", "S") else "y"
    if travel_axis != line.axis:
        # The flight is cut across: what a section can honestly show is the block the cut
        # passes through, not a profile. Drawn dashed so it does not read as masonry.
        span = _rect_u_span(geometry.footprint, u_axis)
        out.append(
            Polyline(
                A_STAIR,
                rect_ring(
                    shift_one(span[0]), ffl_mm, shift_one(span[1]), ffl_mm + geometry.total_rise_mm
                ),
                closed=True,
                dashed=True,
                owner_id=geometry.stair_id,
                kind=K_STAIR_PROFILE,
            )
        )
        notes.append(
            "Stair %s is cut across its flight, so it is shown as a dashed extent rather "
            "than a profile — see the floor plan for the treads." % geometry.stair_id
        )
        return tuple(out), tuple(notes)

    # Stepped profile, riser by riser, along the direction of travel.
    points: List[Tuple[int, int]] = []
    origin = geometry.origin
    forward = geometry.forward

    def u_at(along_mm: int) -> int:
        return shift_one(
            u_of(origin[0] + forward[0] * along_mm, origin[1] + forward[1] * along_mm, u_axis)
        )

    z = ffl_mm
    points.append((u_at(0), z))
    for step in range(geometry.drawn_risers):
        z += geometry.riser_mm
        points.append((u_at(step * geometry.tread_mm), z))
        points.append((u_at(min((step + 1) * geometry.tread_mm, geometry.going_mm)), z))
    if geometry.landing_rect is not None and geometry.landing_depth_mm > 0:
        points.append((u_at(geometry.going_mm + geometry.landing_depth_mm), z))
    out.append(
        Polyline(
            A_STAIR,
            tuple(points),
            owner_id=geometry.stair_id,
            kind=K_STAIR_PROFILE,
        )
    )
    label = "%dR @ %d / %d" % (geometry.risers_count, geometry.riser_mm, geometry.tread_mm)
    mid_u = (points[0][0] + points[-1][0]) // 2
    out.append(
        Text(
            A_TEXT,
            (mid_u, ffl_mm + geometry.drawn_rise_mm + height),
            sanitise_text(label),
            height,
            h_align="center",
            v_align="bottom",
            owner_id=geometry.stair_id,
            kind=K_STAIR_LABEL,
        )
    )
    note = geometry.note()
    if note:
        notes.append(note)
    return tuple(out), tuple(notes)


def _mumty(
    house: Any,
    *,
    line: CutLine,
    u_axis: Tuple[int, int],
    shift: Callable[[Sequence[Interval]], Tuple[Interval, ...]],
    levels: LevelSet,
    options: SectionOptions,
    sizes: VerticalStyle,
) -> Tuple[Tuple[LevelMarker, ...], Tuple[Primitive, ...], Tuple[str, ...]]:
    """The mumty over the stair: from the model if it exists, derived if it does not."""
    out: List[Primitive] = []
    notes: List[str] = []
    top_storey_id = levels.storeys[-1].storey_id

    modelled = [
        slab
        for slab in getattr(house, "slabs", ()) or ()
        if slab.kind == "mumty" and len(slab.polygon) >= 3
    ]
    spans: Tuple[Interval, ...] = ()
    derived = False
    if modelled:
        raw: List[Interval] = []
        for slab in modelled:
            raw.extend(
                ring_line_intervals(
                    tuple((int(p.x), int(p.y)) for p in slab.polygon),
                    axis=line.axis,
                    position_mm=line.position_mm,
                    u_axis=u_axis,
                )
            )
        spans = shift(merge_intervals(raw))
    elif options.include_derived_mumty:
        wells: List[Interval] = []
        for stair in sorted(house.stairs, key=lambda s: str(s.id)):
            if str(stair.storey_id) != top_storey_id:
                continue
            geometry = stair_geometry(stair)
            if line.straddles(geometry.footprint):
                wells.append(_rect_u_span(geometry.footprint, u_axis))
        spans = shift(merge_intervals(wells))
        derived = bool(spans)

    if not spans:
        return (), (), ()

    top_z = levels.terrace_mm + options.mumty_clear_height_mm + MUMTY_SLAB_MM
    for span in spans:
        # Two walls and a roof slab: a mumty in section is a small room, not a solid box.
        for u_edge, direction in ((span[0], 1), (span[1], -1)):
            lo = min(u_edge, u_edge + direction * MUMTY_WALL_MM)
            out.extend(
                _block(
                    (lo, lo + MUMTY_WALL_MM),
                    (levels.terrace_mm, top_z - MUMTY_SLAB_MM),
                    owner_id=None,
                    kind=K_MUMTY,
                    pattern=PATTERN_MASONRY,
                    sizes=sizes,
                )
            )
        out.extend(
            _block(
                span,
                (top_z - MUMTY_SLAB_MM, top_z),
                owner_id=None,
                kind=K_MUMTY,
                pattern=PATTERN_CONCRETE,
                sizes=sizes,
            )
        )
    label = "MUMTY (INDICATIVE)" if derived else "MUMTY"
    height = sizes.dim_text_mm
    out.append(
        Text(
            A_TEXT,
            ((spans[0][0] + spans[-1][1]) // 2, levels.terrace_mm + height),
            sanitise_text(label),
            height,
            h_align="center",
            v_align="bottom",
            kind=K_MUMTY,
        )
    )
    if derived:
        notes.append(
            "Mumty derived over the top-storey stair well (%dmm clear + %dmm slab): the "
            "model carries no mumty slab, and §8 builds the same box in 3D. Shown "
            "INDICATIVE." % (options.mumty_clear_height_mm, MUMTY_SLAB_MM)
        )
    marker = LevelMarker(
        level_mm=top_z,
        labels=("MUMTY TOP (INDICATIVE)" if derived else "MUMTY TOP",),
    )
    return (marker,), tuple(out), tuple(notes)
