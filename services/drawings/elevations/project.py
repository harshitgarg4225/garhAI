"""The elevation projector: model → §7 primitives, one direction at a time.

    Elevations: project facade sub-model + openings per direction; dims: floor lines
    (plinth/FFL/lintel/parapet levels as level markers, not chains), overall height
    chain; material callout leaders from facade kit metadata.

That sentence is the whole specification and this module implements it literally, in the
order a draughtsman would: the silhouette and the floor lines first, then the openings that
survive the hidden-line test, then the balcony and parapet, then the level markers down the
left, the single height chain up the right, and the material callouts outboard of it.

**What an elevation is not.** It is not a place for dimension chains. §7 is explicit that
the vertical information is *level markers* — a tick and a number at the plinth, at each
FFL, at each sill and lintel, at the terrace and at the top of the parapet — plus exactly
one overall height chain. Chains of storey heights stacked next to level markers say the
same thing twice and disagree the moment somebody edits one, so there is one chain here and
its segments are asserted to sum (§7 step 5) at construction.

**Honesty about what the model carries.** The levels come from ``house.levels`` and
``Storey.level``; the callouts come from ``house.facade.components``; nothing is inferred
from a default when the model has a value, and nothing is invented when it does not — an
absent facade kit produces an elevation with no callouts and a note saying so. Every
projection returns its notes, and the sheet composer is expected to print them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from services.drawings.dimensions import DEFAULT_DIM_TO_JAMB
from services.drawings.elevations.callouts import (
    CALLOUT_COLUMN_GAP_PAPER_MM,
    build_callouts,
    callout_primitives,
    surface_material_notes,
)
from services.drawings.elevations.facade import (
    facade_faces,
    footprint_rings,
    merged_extent,
    storey_extent,
    visible_balconies,
    visible_openings,
)
from services.drawings.elevations.vertical import (
    DIRECTION_NAMES,
    DIRECTIONS_4,
    K_OPENING_FRAME,
    K_PARAPET,
    K_PLINTH,
    K_SILHOUETTE,
    K_SLAB_EDGE,
    K_TITLE,
    LevelSet,
    VerticalDrawing,
    VerticalStyle,
    build_levels,
    height_chain,
    height_chain_primitives,
    level_marker_primitives,
    normals_of,
    rect_ring,
    u_of,
)
from services.drawings.layers import A_DOOR, A_TEXT, A_WALL, A_WALL_PART, A_WIND
from services.drawings.projection.primitives import (
    K_BALCONY,
    K_BALCONY_RAILING,
    K_DOOR_LEAF,
    K_OPENING_TAG,
    K_VENT_GLAZING,
    K_WINDOW_GLAZING,
    Line,
    Polyline,
    Primitive,
    Text,
    sanitise_text,
    validate_primitives,
)

__all__ = [
    "ElevationOptions",
    "build_elevation",
    "build_all_elevations",
    "elevation_title",
    "true_azimuth_deg",
]

#: Frame width drawn inside an opening outline. A **real** size: a window frame is 60mm
#: of aluminium whatever scale the sheet prints at.
OPENING_FRAME_MM = 60
#: A window wider than this gets a centre mullion — the usual Indian 2-track sliding split.
MULLION_MIN_WIDTH_MM = 1_500
#: How far a sill course projects each side of the opening.
SILL_OVERHANG_MM = 75


#: Bearing of each drawing normal, clockwise from the plot's ``+Y`` axis.
_BEARING_FROM_PLUS_Y: Mapping[str, int] = {"N": 0, "E": 90, "S": 180, "W": 270}


@dataclass(frozen=True)
class ElevationOptions:
    """Per-sheet knobs. Every default is §7's."""

    scale_denominator: int = 100
    #: §7 / firm preference. In a *vertical* projection this flag has exactly one place to
    #: act: whether an opening's tag sits on its centreline (§7's default) or on its jamb.
    #: Vertical opening dimensions are sill and lintel *heights*, where there is no jamb to
    #: measure to; the horizontal opening chains the flag mainly governs are the plan
    #: projector's level-3 chains (§7 step 2).
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB
    #: Catalogue material id → display name, for callouts. Optional by design.
    material_names: Mapping[str, str] = field(default_factory=dict)
    include_callouts: bool = True
    #: ``plot.north_deg``: the bearing of TRUE north, clockwise from ``+Y``. Elevations are
    #: named in plot-local directions; a non-cardinal north is reported in the title and
    #: the notes rather than silently renaming the sheet.
    north_deg: int = 0


def true_azimuth_deg(direction: str, north_deg: int) -> int:
    """True azimuth of a plot-local elevation direction, degrees clockwise from north."""
    if direction not in _BEARING_FROM_PLUS_Y:
        raise ValueError("%r is not one of %s" % (direction, ", ".join(DIRECTIONS_4)))
    return (_BEARING_FROM_PLUS_Y[direction] - north_deg) % 360


def elevation_title(direction: str, north_deg: int = 0) -> str:
    """``NORTH ELEVATION``, or the honest version when the plot is not north-up.

    Model axes are plot-local (``+Y`` is the plot's north, which is what the editor draws
    against); true north is ``plot.northDeg`` clockwise from it. When the two coincide —
    the overwhelmingly common case, and the seeded demo — the title is just the compass
    name. When they do not, the title carries the true azimuth, because a sheet labelled
    "NORTH ELEVATION" that faces 15° east of north is a drawing that lies.
    """
    name = DIRECTION_NAMES[direction]
    azimuth = true_azimuth_deg(direction, north_deg)
    if azimuth % 90 == 0:
        return "%s ELEVATION" % DIRECTION_NAMES[{0: "N", 90: "E", 180: "S", 270: "W"}[azimuth]]
    return "%s ELEVATION (TRUE AZIMUTH %d°)" % (name, azimuth)


def build_elevation(
    house: Any,
    direction: str,
    options: ElevationOptions | None = None,
) -> VerticalDrawing:
    """Project one elevation. Pure: same model in, same primitives out, every time."""
    opts = options or ElevationOptions()
    normal, u_axis = normals_of(direction)
    scale = opts.scale_denominator
    sizes = VerticalStyle.of(scale)
    levels: LevelSet = build_levels(house)
    footprints = footprint_rings(house)
    storey_levels: dict[str, tuple[int, int]] = {
        s.storey_id: (s.ffl_mm, s.top_mm) for s in levels.storeys
    }
    storey_ffl: dict[str, int] = {s.storey_id: s.ffl_mm for s in levels.storeys}

    notes: list[str] = []
    primitives: list[Primitive] = []

    if not footprints:
        notes.append("This storey has no closed wall outline yet, so there is no facade to draw.")
        return VerticalDrawing(
            kind="elevation",
            name=elevation_title(direction, opts.north_deg),
            direction=direction,
            primitives=(),
            level_markers=levels.markers(),
            chains=(),
            levels=levels,
            notes=tuple(notes),
            scale_denominator=scale,
        )

    # ---- one origin shift for the whole drawing -----------------------------
    all_points: list[tuple[int, int]] = []
    for ring in footprints.values():
        all_points.extend(ring)
    for balcony in getattr(house, "balconies", ()) or ():
        all_points.extend((int(p.x), int(p.y)) for p in balcony.polygon)
    u_origin = min(u_of(x, y, u_axis) for x, y in all_points)

    def su(value: int) -> int:
        """Shift a raw ``u`` into drawing space."""
        return value - u_origin

    # ---- the facade sub-model ----------------------------------------------
    faces, face_notes = facade_faces(
        house,
        direction=direction,
        normal=normal,
        u_axis=u_axis,
        footprints=footprints,
        storey_levels=storey_levels,
    )
    notes.extend(face_notes)
    openings, opening_notes = visible_openings(
        house, faces=faces, u_axis=u_axis, storey_ffl=storey_ffl
    )
    notes.extend(opening_notes)
    balconies = visible_balconies(
        house,
        normal=normal,
        u_axis=u_axis,
        footprints=footprints,
        storey_ffl=storey_ffl,
    )

    # ---- silhouette: one band per storey, sharing its floor lines ----------
    spans: list[tuple[int, int]] = []
    for storey in levels.storeys:
        ring = footprints.get(storey.storey_id)
        if ring is None:
            continue
        u_lo_raw, u_hi_raw = storey_extent(ring, u_axis)
        u_lo, u_hi = su(u_lo_raw), su(u_hi_raw)
        spans.append((u_lo, u_hi))
        primitives.append(
            Polyline(
                A_WALL,
                rect_ring(u_lo, storey.ffl_mm, u_hi, storey.top_mm),
                closed=True,
                owner_id=storey.storey_id,
                kind=K_SILHOUETTE,
            )
        )
        # The slab edge reads as the horizontal shadow line between storeys.
        if storey.index > 0:
            primitives.append(
                Line(
                    A_WALL_PART,
                    (u_lo, storey.ffl_mm - storey.slab_thickness_mm),
                    (u_hi, storey.ffl_mm - storey.slab_thickness_mm),
                    owner_id=storey.storey_id,
                    kind=K_SLAB_EDGE,
                )
            )

    extent = merged_extent(spans) or (0, 0)
    u_left, u_right = extent

    # ---- plinth band and the ground line -----------------------------------
    ground_ring = footprints.get(levels.storeys[0].storey_id) if levels.storeys else None
    if ground_ring is not None and levels.plinth_mm > levels.datum_mm:
        g_lo_raw, g_hi_raw = storey_extent(ground_ring, u_axis)
        primitives.append(
            Polyline(
                A_WALL_PART,
                rect_ring(su(g_lo_raw), levels.datum_mm, su(g_hi_raw), levels.plinth_mm),
                closed=True,
                kind=K_PLINTH,
            )
        )
    overrun = sizes.ground_overrun_mm
    primitives.append(
        Line(
            A_WALL_PART,
            (u_left - overrun, levels.datum_mm),
            (u_right + overrun, levels.datum_mm),
            kind="ground-line",
        )
    )

    # ---- stepped facades: draw the faces only when there is a step ---------
    depths = sorted({face.depth_mm for face in faces})
    if len(depths) > 1:
        notes.append(
            "Facade is stepped (%d depths): each face is outlined and openings behind a "
            "nearer face are hidden." % len(depths)
        )
        for face in faces:
            primitives.append(
                Polyline(
                    A_WALL,
                    rect_ring(su(face.u_lo), face.z_lo, su(face.u_hi), face.z_hi),
                    closed=True,
                    owner_id=face.wall_id,
                    kind="facade-step",
                )
            )

    # ---- openings ----------------------------------------------------------
    frame = OPENING_FRAME_MM
    sill_overhang = SILL_OVERHANG_MM
    tag_height = sizes.tag_text_mm
    for opening in openings:
        layer = A_DOOR if opening.kind == "door" else A_WIND
        u_lo, u_hi = su(opening.u_lo), su(opening.u_hi)
        primitives.append(
            Polyline(
                layer,
                rect_ring(u_lo, opening.z_lo, u_hi, opening.z_hi),
                closed=True,
                owner_id=opening.opening_id,
                kind=K_OPENING_FRAME,
            )
        )
        inner = (u_lo + frame, opening.z_lo + frame, u_hi - frame, opening.z_hi - frame)
        if inner[2] > inner[0] and inner[3] > inner[1]:
            primitives.append(
                Polyline(
                    layer,
                    rect_ring(*inner),
                    closed=True,
                    owner_id=opening.opening_id,
                    kind=K_DOOR_LEAF if opening.kind == "door" else K_WINDOW_GLAZING,
                )
            )
        if opening.kind == "window" and (u_hi - u_lo) >= MULLION_MIN_WIDTH_MM:
            mid = (u_lo + u_hi) // 2
            primitives.append(
                Line(
                    layer,
                    (mid, opening.z_lo + frame),
                    (mid, opening.z_hi - frame),
                    owner_id=opening.opening_id,
                    kind=K_WINDOW_GLAZING,
                )
            )
        if opening.kind == "ventilator":
            mid_z = (opening.z_lo + opening.z_hi) // 2
            primitives.append(
                Line(
                    layer,
                    (u_lo + frame, mid_z),
                    (u_hi - frame, mid_z),
                    owner_id=opening.opening_id,
                    kind=K_VENT_GLAZING,
                )
            )
        if opening.sill_above_ffl_mm > 0:
            primitives.append(
                Line(
                    A_WALL_PART,
                    (u_lo - sill_overhang, opening.z_lo),
                    (u_hi + sill_overhang, opening.z_lo),
                    owner_id=opening.opening_id,
                    kind="sill-course",
                )
            )
        if opening.tag:
            tag_u = u_lo if opts.dim_to_jamb else (u_lo + u_hi) // 2
            primitives.append(
                Text(
                    A_TEXT,
                    (tag_u, (opening.z_lo + opening.z_hi) // 2),
                    sanitise_text(opening.tag),
                    tag_height,
                    h_align="left" if opts.dim_to_jamb else "center",
                    v_align="middle",
                    owner_id=opening.opening_id,
                    kind=K_OPENING_TAG,
                )
            )
    if opts.dim_to_jamb:
        notes.append("Firm preference: openings referenced to the jamb, not the centreline.")

    # ---- balconies ---------------------------------------------------------
    for balcony in balconies:
        u_lo, u_hi = su(balcony.u_lo), su(balcony.u_hi)
        primitives.append(
            Polyline(
                A_WALL_PART,
                rect_ring(u_lo, balcony.z_slab_lo, u_hi, balcony.z_slab_hi),
                closed=True,
                owner_id=balcony.balcony_id,
                kind=K_BALCONY,
            )
        )
        primitives.append(
            Polyline(
                A_WALL_PART,
                rect_ring(u_lo, balcony.z_slab_hi, u_hi, balcony.railing_top_mm),
                closed=True,
                owner_id=balcony.balcony_id,
                kind=K_BALCONY_RAILING,
            )
        )

    # ---- parapet -----------------------------------------------------------
    if levels.parapet_height_mm > 0 and levels.storeys:
        top_ring = footprints.get(levels.storeys[-1].storey_id)
        if top_ring is not None:
            t_lo_raw, t_hi_raw = storey_extent(top_ring, u_axis)
            primitives.append(
                Polyline(
                    A_WALL_PART,
                    rect_ring(su(t_lo_raw), levels.terrace_mm, su(t_hi_raw), levels.parapet_top_mm),
                    closed=True,
                    kind=K_PARAPET,
                )
            )

    # ---- level markers (left) and the one height chain (right) -------------
    markers = levels.markers()
    primitives.extend(level_marker_primitives(markers, u_left_mm=u_left, sizes=sizes))
    chain = height_chain(
        levels,
        chain_id="elev-%s-height" % direction.lower(),
        offset_mm=u_right + sizes.chain_offset_mm,
    )
    primitives.extend(height_chain_primitives(chain, sizes=sizes, witness_from_u_mm=u_right))

    # ---- material callouts -------------------------------------------------
    if opts.include_callouts:
        column_u = (
            u_right
            + sizes.chain_offset_mm
            + sizes.style.paper_to_model_mm(CALLOUT_COLUMN_GAP_PAPER_MM)
        )
        callouts, callout_notes = build_callouts(
            house,
            faces=faces,
            openings=openings,
            balconies=balconies,
            u_axis=u_axis,
            u_origin_mm=u_origin,
            column_u_mm=column_u,
            top_z_mm=levels.parapet_top_mm,
            terrace_mm=levels.terrace_mm,
            parapet_top_mm=levels.parapet_top_mm,
            sizes=sizes,
            material_names=opts.material_names,
        )
        primitives.extend(callout_primitives(callouts, sizes=sizes))
        notes.extend(callout_notes)
        notes.extend(surface_material_notes(house, material_names=opts.material_names))

    # ---- title -------------------------------------------------------------
    title = elevation_title(direction, opts.north_deg)
    title_height = sizes.title_text_mm
    primitives.append(
        Text(
            A_TEXT,
            ((u_left + u_right) // 2, levels.datum_mm - overrun - title_height),
            sanitise_text(title),
            title_height,
            h_align="center",
            v_align="top",
            kind=K_TITLE,
        )
    )
    if opts.north_deg % 90 != 0:
        notes.append(
            "Plot is not square to true north (northDeg=%d): elevation names are "
            "plot-local, the title carries the true azimuth." % opts.north_deg
        )

    validate_primitives(primitives)
    return VerticalDrawing(
        kind="elevation",
        name=title,
        direction=direction,
        primitives=tuple(primitives),
        level_markers=markers,
        chains=(chain,),
        levels=levels,
        notes=tuple(notes),
        scale_denominator=scale,
    )


def build_all_elevations(
    house: Any, options: ElevationOptions | None = None
) -> dict[str, VerticalDrawing]:
    """All four §7 elevations, keyed by direction, in N-E-S-W order."""
    return {d: build_elevation(house, d, options) for d in DIRECTIONS_4}
