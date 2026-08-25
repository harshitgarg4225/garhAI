"""The facade sub-model for one direction: which faces are seen, and what is on them.

This is where §7's one hard demand on an elevation is met:

    project facade sub-model + openings per direction

"Per direction" is doing a lot of work in that sentence. A house has four elevations and
every one of them is a *different* sub-model: a window on the north wall belongs to the
north elevation and must be **absent** from the south one, because the building's own mass
is in the way. Getting that wrong produces the single most obvious defect an elevation can
have — a reviewer sees windows where the wall is blank — so hidden-line correctness is
this module's whole job, and it is implemented in three deliberate layers:

1. **Orientation.** A wall is on this facade only if its outward normal *is* the drawing's
   normal. The outward side is found by probing (:func:`outward_normal_of`): step off the
   wall's centreline by half its thickness plus :data:`~.vertical.PROBE_MM` and ask which
   side of the storey footprint the probe landed on. That is a fact about the model, not a
   convention about wall winding order — walls can be drawn in any direction, and the
   editor lets them be.
2. **Depth.** Front-facing faces are ordered by how near the viewer they are, so a
   recessed bay behind a projecting wing is known to be behind it.
3. **Occlusion.** An opening is dropped when a nearer face on the same facade completely
   covers it (:func:`visible_openings`). Partial overlap still draws — a truthful outline
   with one edge hidden beats an omitted window, and the honest limitation is recorded in
   the drawing's notes rather than hidden here.

Everything is integer millimetres and nothing is imported from the model core: a house is
read by attribute access, which is what lets this run on a bare interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.drawings.elevations.vertical import (
    PROBE_MM,
    Interval,
    contains,
    depth_of,
    half_lo,
    merge_intervals,
    point_in_ring,
    u_of,
)

__all__ = [
    "FacadeFace",
    "FacadeOpening",
    "ProjectedBalcony",
    "Ring",
    "facade_faces",
    "footprint_of",
    "merged_extent",
    "footprint_rings",
    "outward_normal_of",
    "storey_extent",
    "visible_openings",
    "visible_balconies",
    "wall_axis_is_horizontal",
    "wall_rect",
]

#: A closed ring of integer-mm model points, no repeated last vertex.
Ring = Tuple[Tuple[int, int], ...]


def _pt(value: Any) -> Tuple[int, int]:
    """Model ``Pt`` → plain tuple. The one place the document's point type is read."""
    return (int(value.x), int(value.y))


def wall_axis_is_horizontal(wall: Any) -> Optional[bool]:
    """True for a wall running along ``+X``, False along ``+Y``, None if it is neither.

    MVP is orthogonal-only (§7 step 1: "H/V; MVP is orthogonal-only"). A diagonal wall
    returns None so callers can skip it and say so in a note, rather than projecting
    nonsense.
    """
    a, b = _pt(wall.a), _pt(wall.b)
    if a[1] == b[1] and a[0] != b[0]:
        return True
    if a[0] == b[0] and a[1] != b[1]:
        return False
    return None


def wall_rect(wall: Any) -> Optional[Tuple[int, int, int, int]]:
    """The wall's axis-aligned footprint ``(x_lo, y_lo, x_hi, y_hi)``, exact.

    The thickness is split ``lo = centre - t // 2`` / ``hi = lo + t`` so a 115mm wall is
    115mm wide in the drawing and not 114 or 116 — see :func:`~.vertical.half_lo`.
    """
    horizontal = wall_axis_is_horizontal(wall)
    if horizontal is None:
        return None
    a, b = _pt(wall.a), _pt(wall.b)
    thickness = int(wall.thickness_mm)
    if horizontal:
        y_lo = a[1] - half_lo(thickness)
        return (min(a[0], b[0]), y_lo, max(a[0], b[0]), y_lo + thickness)
    x_lo = a[0] - half_lo(thickness)
    return (x_lo, min(a[1], b[1]), x_lo + thickness, max(a[1], b[1]))


# ---------------------------------------------------------------------------
# Footprints
# ---------------------------------------------------------------------------
def footprint_of(house: Any, storey_id: str) -> Optional[Ring]:
    """The storey's outer boundary: its floor slab, else the outline of its walls.

    The floor slab is the right answer because the model core derives it from the walls'
    *outer faces* (``garh_model.fold._recompute_derived``), which is exactly the
    silhouette an elevation draws. The wall-outline fallback exists for a storey whose
    walls do not yet close a ring — mid-edit, or a partial solver result — and is a
    bounding box, which is honest for a rectangle and generous for an L.
    """
    for slab in getattr(house, "slabs", ()) or ():
        if slab.storey_id == storey_id and slab.kind == "floor" and len(slab.polygon) >= 3:
            return tuple(_pt(p) for p in slab.polygon)

    rects = [
        rect
        for rect in (wall_rect(w) for w in house.walls if w.storey_id == storey_id)
        if rect is not None
    ]
    if not rects:
        return None
    x_lo = min(r[0] for r in rects)
    y_lo = min(r[1] for r in rects)
    x_hi = max(r[2] for r in rects)
    y_hi = max(r[3] for r in rects)
    return ((x_lo, y_lo), (x_hi, y_lo), (x_hi, y_hi), (x_lo, y_hi))


def footprint_rings(house: Any) -> Dict[str, Ring]:
    """Footprint per storey id, skipping storeys with nothing on them."""
    out: Dict[str, Ring] = {}
    for storey in house.storeys:
        ring = footprint_of(house, storey.id)
        if ring is not None:
            out[str(storey.id)] = ring
    return out


def storey_extent(ring: Ring, u_axis: Tuple[int, int]) -> Interval:
    """The storey's silhouette span in drawing ``u`` (before the origin shift)."""
    values = [u_of(x, y, u_axis) for x, y in ring]
    return (min(values), max(values))


# ---------------------------------------------------------------------------
# Which way does a wall face?
# ---------------------------------------------------------------------------
def outward_normal_of(wall: Any, footprint: Ring) -> Optional[Tuple[int, int]]:
    """The wall's outward normal, or None when the probe is inconclusive.

    Probes both sides of the centreline midpoint at ``t // 2 + PROBE_MM``. Outdoors is the
    side that lands outside the footprint while the other lands inside; a wall with both
    probes inside is an internal wall (it never appears on an elevation), and both outside
    means the footprint does not describe this wall — reported as None rather than guessed.
    """
    horizontal = wall_axis_is_horizontal(wall)
    if horizontal is None:
        return None
    a, b = _pt(wall.a), _pt(wall.b)
    mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
    step = half_lo(int(wall.thickness_mm)) + PROBE_MM
    normal = (0, 1) if horizontal else (1, 0)
    plus = point_in_ring(footprint, mid[0] + normal[0] * step, mid[1] + normal[1] * step)
    minus = point_in_ring(footprint, mid[0] - normal[0] * step, mid[1] - normal[1] * step)
    if plus == minus:
        return None
    return normal if minus else (-normal[0], -normal[1])


@dataclass(frozen=True)
class FacadeFace:
    """One visible wall face on this elevation, in drawing coordinates.

    ``depth_mm`` is the face's distance toward the viewer: **larger is nearer**, and it is
    measured to the outer face (centreline depth plus half the thickness), because that is
    the plane the drawing shows.
    """

    wall_id: str
    storey_id: str
    u_lo: int
    u_hi: int
    z_lo: int
    z_hi: int
    depth_mm: int
    thickness_mm: int

    @property
    def u_span(self) -> Interval:
        return (self.u_lo, self.u_hi)

    @property
    def z_span(self) -> Interval:
        return (self.z_lo, self.z_hi)


def facade_faces(
    house: Any,
    *,
    direction: str,
    normal: Tuple[int, int],
    u_axis: Tuple[int, int],
    footprints: Dict[str, Ring],
    storey_levels: Dict[str, Tuple[int, int]],
    include_internal: bool = False,
) -> Tuple[Tuple[FacadeFace, ...], Tuple[str, ...]]:
    """Every wall face pointing at the viewer, plus notes about what was skipped.

    ``storey_levels`` maps a storey id to ``(ffl_mm, top_mm)`` — the wall's vertical
    extent. Internal walls are excluded by default: they are behind the facade, and the
    only reason to include one would be a section, which has its own projector.
    """
    faces: List[FacadeFace] = []
    notes: List[str] = []
    skipped_diagonal = 0
    for wall in sorted(house.walls, key=lambda w: str(w.id)):
        storey_id = str(wall.storey_id)
        footprint = footprints.get(storey_id)
        levels = storey_levels.get(storey_id)
        if footprint is None or levels is None:
            continue
        if not include_internal and wall.kind == "internal":
            continue
        horizontal = wall_axis_is_horizontal(wall)
        if horizontal is None:
            skipped_diagonal += 1
            continue
        # A wall running along the view normal is edge-on: it projects to a line, and
        # drawing it would put a spurious vertical stripe on the facade.
        run = (1, 0) if horizontal else (0, 1)
        if run[0] * normal[0] + run[1] * normal[1] != 0:
            continue
        outward = outward_normal_of(wall, footprint)
        if outward is None or outward != normal:
            continue
        a, b = _pt(wall.a), _pt(wall.b)
        u_a, u_b = u_of(a[0], a[1], u_axis), u_of(b[0], b[1], u_axis)
        mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        thickness = int(wall.thickness_mm)
        depth = depth_of(mid[0], mid[1], normal) + (thickness - half_lo(thickness))
        ffl, top = levels
        faces.append(
            FacadeFace(
                wall_id=str(wall.id),
                storey_id=storey_id,
                u_lo=min(u_a, u_b),
                u_hi=max(u_a, u_b),
                z_lo=ffl,
                z_hi=top,
                depth_mm=depth,
                thickness_mm=thickness,
            )
        )
    if skipped_diagonal:
        notes.append(
            "%d non-orthogonal wall(s) skipped: §7's MVP projection is orthogonal-only."
            % skipped_diagonal
        )
    return tuple(faces), tuple(notes)


# ---------------------------------------------------------------------------
# Openings, and the hidden-line test that keeps far-face ones off the sheet
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FacadeOpening:
    """An opening projected onto the elevation plane."""

    opening_id: str
    wall_id: str
    storey_id: str
    kind: str
    tag: Optional[str]
    u_lo: int
    u_hi: int
    z_lo: int
    z_hi: int
    #: Sill height above the storey FFL, as stored on the opening.
    sill_above_ffl_mm: int
    depth_mm: int

    @property
    def u_span(self) -> Interval:
        return (self.u_lo, self.u_hi)

    @property
    def z_span(self) -> Interval:
        return (self.z_lo, self.z_hi)

    @property
    def u_centre(self) -> int:
        return (self.u_lo + self.u_hi) // 2


def visible_openings(
    house: Any,
    *,
    faces: Sequence[FacadeFace],
    u_axis: Tuple[int, int],
    storey_ffl: Dict[str, int],
) -> Tuple[Tuple[FacadeOpening, ...], Tuple[str, ...]]:
    """Openings on the faces the viewer can see — the hidden-line filter.

    Two rules, both load-bearing:

    * **Only front-facing hosts.** ``faces`` already contains just the walls whose outward
      normal is the drawing's, so an opening on the far face of the building is never a
      candidate. This is the rule the test suite pins ("far-face openings are excluded"):
      it is structural, not a special case.
    * **Fully occluded openings are dropped.** If a nearer face on this same facade covers
      the opening's whole rectangle, the opening is behind masonry and must not draw. A
      *partly* covered opening still draws, and the caller notes it — see the module
      docstring for why that is the safer default.
    """
    by_wall = {face.wall_id: face for face in faces}
    out: List[FacadeOpening] = []
    notes: List[str] = []
    hidden_far = 0
    occluded = 0
    partial = 0
    for opening in sorted(house.openings, key=lambda o: str(o.id)):
        face = by_wall.get(str(opening.wall_id))
        if face is None:
            hidden_far += 1
            continue
        wall = _find(house.walls, str(opening.wall_id))
        if wall is None:
            continue
        a, b = _pt(wall.a), _pt(wall.b)
        length = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if length <= 0:
            continue
        # Direction along the wall, as a unit step in the one axis it runs along.
        step = ((b[0] - a[0]) // length, (b[1] - a[1]) // length)
        width = int(opening.width_mm)
        centre = int(opening.offset_mm)
        near = centre - width // 2
        far = near + width
        p_near = (a[0] + step[0] * near, a[1] + step[1] * near)
        p_far = (a[0] + step[0] * far, a[1] + step[1] * far)
        u_a = u_of(p_near[0], p_near[1], u_axis)
        u_b = u_of(p_far[0], p_far[1], u_axis)
        ffl = storey_ffl.get(face.storey_id, face.z_lo)
        z_lo = ffl + int(opening.sill_mm)
        z_hi = z_lo + int(opening.height_mm)
        span_u = (min(u_a, u_b), max(u_a, u_b))
        span_z = (z_lo, z_hi)

        nearer = [f for f in faces if f.depth_mm > face.depth_mm]
        if any(contains(f.u_span, span_u) and contains(f.z_span, span_z) for f in nearer):
            occluded += 1
            continue
        if any(
            _overlaps(f.u_span, span_u) and _overlaps(f.z_span, span_z) for f in nearer
        ):
            partial += 1
        out.append(
            FacadeOpening(
                opening_id=str(opening.id),
                wall_id=str(opening.wall_id),
                storey_id=face.storey_id,
                kind=str(opening.kind),
                tag=(str(opening.tag) if opening.tag else None),
                u_lo=span_u[0],
                u_hi=span_u[1],
                z_lo=z_lo,
                z_hi=z_hi,
                sill_above_ffl_mm=int(opening.sill_mm),
                depth_mm=face.depth_mm,
            )
        )
    if hidden_far:
        notes.append(
            "%d opening(s) hidden: their host wall does not face this elevation." % hidden_far
        )
    if occluded:
        notes.append("%d opening(s) hidden behind a nearer part of the building." % occluded)
    if partial:
        notes.append(
            "%d opening(s) are partly behind a nearer wall and are drawn whole — check "
            "against the plan." % partial
        )
    return tuple(out), tuple(notes)


def _overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _find(items: Sequence[Any], element_id: str) -> Optional[Any]:
    for item in items:
        if str(item.id) == element_id:
            return item
    return None


# ---------------------------------------------------------------------------
# Balconies — part of the facade, and the thing a railing callout points at
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectedBalcony:
    balcony_id: str
    storey_id: str
    u_lo: int
    u_hi: int
    #: Slab soffit and top.
    z_slab_lo: int
    z_slab_hi: int
    railing_top_mm: int
    railing_kind: str


def visible_balconies(
    house: Any,
    *,
    normal: Tuple[int, int],
    u_axis: Tuple[int, int],
    footprints: Dict[str, Ring],
    storey_ffl: Dict[str, int],
) -> Tuple[ProjectedBalcony, ...]:
    """Balconies that project past the building line **on this facade**.

    A balcony on another face is behind the mass and is not drawn. "Past the building
    line" is tested by depth: the balcony's furthest point toward the viewer has to be
    beyond the storey footprint's, which is the same criterion the projection rules use
    for ``projection_mm``.
    """
    out: List[ProjectedBalcony] = []
    for balcony in sorted(getattr(house, "balconies", ()), key=lambda b: str(b.id)):
        storey_id = str(balcony.storey_id)
        footprint = footprints.get(storey_id)
        ffl = storey_ffl.get(storey_id)
        if footprint is None or ffl is None or len(balcony.polygon) < 3:
            continue
        points = [_pt(p) for p in balcony.polygon]
        depth = max(depth_of(x, y, normal) for x, y in points)
        building_depth = max(depth_of(x, y, normal) for x, y in footprint)
        if depth <= building_depth:
            continue
        us = [u_of(x, y, u_axis) for x, y in points]
        slab = int(balcony.slab_thickness_mm)
        out.append(
            ProjectedBalcony(
                balcony_id=str(balcony.id),
                storey_id=storey_id,
                u_lo=min(us),
                u_hi=max(us),
                z_slab_lo=ffl - slab,
                z_slab_hi=ffl,
                railing_top_mm=ffl + int(balcony.railing_height_mm),
                railing_kind=str(balcony.railing_kind),
            )
        )
    return tuple(out)


def merged_extent(spans: Sequence[Interval]) -> Optional[Interval]:
    """Overall span of a set of spans, or None when there are none."""
    merged = merge_intervals(spans)
    if not merged:
        return None
    return (merged[0][0], merged[-1][1])
