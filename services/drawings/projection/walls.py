"""Walls and openings in plan — the part of §7 a reviewer looks at first. **Real.**

    Plan projection: walls as double lines w/ thickness (fill hatch external), openings
    break walls (door arc + leaf, window triple line) ...

Four things happen in this module, in this order, and each one exists because a
drawing that skips it looks wrong to an Indian municipal reviewer:

1. **Double lines with real thickness.** A wall is stored as a centreline plus a
   thickness; the drawing shows the two faces. :func:`wall_band` computes them.
2. **Mitred junctions.** Where two walls meet, each face is trimmed or extended to the
   neighbour's face so a corner closes and an internal wall stops at the external
   wall's inner face instead of running into its poché. The rule is in
   :func:`face_extents` and it is four lines of vector arithmetic, not a geometry
   library.
3. **Openings genuinely break the wall.** :func:`split_span` removes the opening
   intervals from each face, so a door is a gap with jambs, not a white rectangle
   painted over a line. The test asserts the split sums exactly.
4. **The opening symbol itself**: door leaf + swing arc, window triple line,
   ventilator with a dashed centre line.

CONVENTIONS ARE SHARED WITH THE CANVAS, DELIBERATELY
----------------------------------------------------
``apps/web/src/pages/project/plan/planGeometry.ts`` draws the same building on screen.
Its conventions are mirrored here exactly — left normal ``n = (-uy, ux)``, door hinge at
the ``a``-end for ``*-left`` and the ``b``-end for ``*-right``, leaf to the ``+n`` side
for ``in-*`` and ``-n`` for ``out-*``, openings clamped to the wall and merged when they
overlap. If a door swings one way on screen and the other way on the sheet, an architect
stops trusting both. The sheet adds only what print needs: mitred junctions, the §7
centre glazing line, and the external-wall hatch.

Everything is integer millimetres. Half-thicknesses are the one place a .5 appears
(a 115mm wall has faces at ±57.5); it is rounded half-away-from-zero, which is
symmetric, so the two faces stay the same distance from the centreline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.drawings.layers import A_DOOR, A_WALL, A_WALL_PART, A_WIND
from services.drawings.projection.primitives import (
    K_DOOR_LEAF,
    K_DOOR_SWING,
    K_VENT_GLAZING,
    K_WALL_END,
    K_WALL_FACE,
    K_WALL_HATCH,
    K_WALL_JAMB,
    K_WINDOW_GLAZING,
    PATTERN_MASONRY,
    Arc,
    Hatch,
    Line,
    Point,
    Primitive,
    point_round,
    round_half_away,
)

#: How much of the wall thickness the window frame lines are inset from each face.
#: A sixth reads as a frame at 1:100 and matches the canvas twin's reveal inset.
WINDOW_REVEAL_DIVISOR = 6

#: Door swing arcs are emitted as true arcs; this is the flattening resolution offered
#: to renderers that have no arc entity (matches ``ARC_STEPS`` in the canvas twin).
DOOR_ARC_STEPS = 12


# ---------------------------------------------------------------------------
# Wall frame: the local (along, across) coordinate system of one wall
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WallFrame:
    """A wall's local axes. ``along`` runs a→b, ``across`` is the left normal."""

    wall_id: str
    ax: int
    ay: int
    #: Unit direction and left normal. Floats, and they stay inside this module:
    #: every point that leaves is rounded to integer mm.
    ux: float
    uy: float
    nx: float
    ny: float
    length_mm: int
    #: Half the wall thickness, rounded away from zero so both faces match.
    half_mm: int

    def at(self, along_mm: float, across_mm: float) -> Point:
        """A point in the wall's local frame, as integer mm."""
        return point_round(
            self.ax + self.ux * along_mm + self.nx * across_mm,
            self.ay + self.uy * along_mm + self.ny * across_mm,
        )

    @property
    def angle_deg(self) -> int:
        """Direction of ``a→b`` in integer degrees CCW from +X.

        MVP walls are orthogonal (playbook §7: "MVP is orthogonal-only"), so this is a
        multiple of 90 and the rounding is exact. A diagonal wall would round to the
        nearest degree, which is honest for a swing arc and wrong for nothing else.
        """
        return round_half_away(math.degrees(math.atan2(self.uy, self.ux))) % 360


def wall_frame(wall: Any) -> Optional[WallFrame]:
    """Local frame of a wall, or None when the wall is degenerate.

    Zero-length walls are rejected by the model's own validation, but a document from an
    older op log or a partial rebase can still hold one, and a NaN here would poison
    every coordinate downstream — so it returns None and the projector skips it.
    """
    dx = wall.b.x - wall.a.x
    dy = wall.b.y - wall.a.y
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    return WallFrame(
        wall_id=wall.id,
        ax=wall.a.x,
        ay=wall.a.y,
        ux=dx / length,
        uy=dy / length,
        nx=-dy / length,
        ny=dx / length,
        length_mm=round_half_away(length),
        half_mm=round_half_away(wall.thickness_mm / 2),
    )


# ---------------------------------------------------------------------------
# Junction mitring
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FaceExtents:
    """Along-axis extents of a wall's two faces, after mitring.

    ``left`` is the ``+n`` face, ``right`` the ``-n`` face. They differ at a junction:
    at an external corner the outer face runs long and the inner face stops short, which
    is exactly what a mitre looks like in line work.
    """

    left_start_mm: int
    left_end_mm: int
    right_start_mm: int
    right_end_mm: int

    @property
    def start_mm(self) -> int:
        return min(self.left_start_mm, self.right_start_mm)

    @property
    def end_mm(self) -> int:
        return max(self.left_end_mm, self.right_end_mm)


def face_extents(wall: Any, frame: WallFrame, neighbours: Sequence[Any]) -> FaceExtents:
    """Trim/extend each face of ``wall`` against the walls that meet its ends.

    THE RULE, for each end of the wall and each neighbour whose centreline passes
    through that end:

    * if the neighbour's body covers **this** side of the wall, the face stops at the
      neighbour's near face — ``retract by the neighbour's half-thickness``. That is
      what makes a spine wall end at the external wall's inner face instead of drawing
      two stray lines inside its poché;
    * if it does not, the face runs on to meet the neighbour's far face —
      ``extend by the neighbour's half-thickness``. That is what closes an external
      corner.

    "Covers this side" is a sign test: does the neighbour continue past the junction in
    the direction of this face's normal. At a T-junction it continues both ways, so both
    faces retract; at an L-corner it continues one way, so one face retracts and the
    other extends. Non-orthogonal neighbours are ignored (MVP is orthogonal-only) —
    better an un-mitred corner than an invented one.
    """
    deltas: List[Tuple[int, int]] = []
    for end_x, end_y in ((wall.a.x, wall.a.y), (wall.b.x, wall.b.y)):
        # Retraction and extension are collected separately, and retraction wins: at a
        # cross junction one neighbour may want the face longer and another shorter, and
        # a face drawn into another wall's poché is the worse of the two mistakes.
        retract_left = retract_right = 0
        extend_left = extend_right = 0
        for other in neighbours:
            if other.id == wall.id:
                continue
            other_frame = wall_frame(other)
            if other_frame is None:
                continue
            # Perpendicular only: a collinear neighbour needs no mitre, and an oblique
            # one is out of MVP scope (§7: "MVP is orthogonal-only").
            if abs(other_frame.ux * frame.ux + other_frame.uy * frame.uy) > 1e-9:
                continue
            if not _touches(other, end_x, end_y):
                continue
            covers_left, covers_right = _covered_sides(other, end_x, end_y, frame)
            half = other_frame.half_mm
            if covers_left:
                retract_left = max(retract_left, half)
            else:
                extend_left = max(extend_left, half)
            if covers_right:
                retract_right = max(retract_right, half)
            else:
                extend_right = max(extend_right, half)
        deltas.append(
            (
                -retract_left if retract_left else extend_left,
                -retract_right if retract_right else extend_right,
            )
        )

    (start_left, start_right), (end_left, end_right) = deltas
    return FaceExtents(
        left_start_mm=-start_left,
        left_end_mm=frame.length_mm + end_left,
        right_start_mm=-start_right,
        right_end_mm=frame.length_mm + end_right,
    )


def _touches(other: Any, x: int, y: int) -> bool:
    """Does ``other``'s centreline pass through ``(x, y)``? Endpoint or interior."""
    if (other.a.x == x and other.a.y == y) or (other.b.x == x and other.b.y == y):
        return True
    dx = other.b.x - other.a.x
    dy = other.b.y - other.a.y
    cross = (x - other.a.x) * dy - (y - other.a.y) * dx
    if cross != 0:
        return False
    dot = (x - other.a.x) * dx + (y - other.a.y) * dy
    return 0 <= dot <= dx * dx + dy * dy


def _covered_sides(other: Any, x: int, y: int, frame: WallFrame) -> Tuple[bool, bool]:
    """Which sides of ``frame`` the neighbour's body continues into, past ``(x, y)``."""
    covers_left = False
    covers_right = False
    for px, py in ((other.a.x, other.a.y), (other.b.x, other.b.y)):
        side = (px - x) * frame.nx + (py - y) * frame.ny
        if side > 1e-9:
            covers_left = True
        elif side < -1e-9:
            covers_right = True
    return covers_left, covers_right


# ---------------------------------------------------------------------------
# Opening intervals and the split
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Span:
    """A closed interval along a wall's axis, in integer mm."""

    start_mm: int
    end_mm: int

    @property
    def length_mm(self) -> int:
        return self.end_mm - self.start_mm


def opening_span(frame: WallFrame, opening: Any) -> Optional[Span]:
    """The interval an opening occupies along its host wall.

    ``offset_mm`` is the distance from ``wall.a`` to the opening **centre** (§3), so the
    span is centre ± half the width, clamped to the wall. Odd widths round the two
    halves apart by at most 1mm and the span still measures exactly ``width_mm``.
    """
    half_low = opening.width_mm // 2
    half_high = opening.width_mm - half_low
    start = max(0, opening.offset_mm - half_low)
    end = min(frame.length_mm, opening.offset_mm + half_high)
    if end <= start:
        return None
    return Span(start, end)


def merge_spans(spans: Sequence[Span]) -> Tuple[Span, ...]:
    """Sort and union overlapping spans.

    ``validate`` rejects overlapping openings, but a rebase can briefly produce them and
    two overlapping gaps must draw as one gap rather than a negative-length run.
    """
    ordered = sorted(spans, key=lambda span: (span.start_mm, span.end_mm))
    merged: List[List[int]] = []
    for span in ordered:
        if merged and span.start_mm <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span.end_mm)
        else:
            merged.append([span.start_mm, span.end_mm])
    return tuple(Span(start, end) for start, end in merged)


def split_span(start_mm: int, end_mm: int, gaps: Sequence[Span]) -> Tuple[Span, ...]:
    """``[start, end]`` minus ``gaps`` — the runs of solid wall along one face.

    THE INVARIANT (asserted in ``tests/test_projection.py``): the runs plus the clipped
    gaps sum exactly to ``end - start``. Integer millimetres are what make that an
    equality rather than a tolerance, and it is the same discipline §7 step 5 demands of
    dimension chains: the parts must equal the whole.
    """
    if end_mm <= start_mm:
        return ()
    runs: List[Span] = []
    cursor = start_mm
    for gap in merge_spans(gaps):
        low = max(gap.start_mm, start_mm)
        high = min(gap.end_mm, end_mm)
        if high <= low:
            continue
        if low > cursor:
            runs.append(Span(cursor, low))
        cursor = max(cursor, high)
    if cursor < end_mm:
        runs.append(Span(cursor, end_mm))
    return tuple(runs)


def clipped_gap_total(start_mm: int, end_mm: int, gaps: Sequence[Span]) -> int:
    """Total gap length inside ``[start, end]`` — the other half of the invariant."""
    total = 0
    for gap in merge_spans(gaps):
        low = max(gap.start_mm, start_mm)
        high = min(gap.end_mm, end_mm)
        if high > low:
            total += high - low
    return total


# ---------------------------------------------------------------------------
# The wall band: everything one wall contributes to the plan
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WallBand:
    """One wall's plan geometry, resolved but not yet turned into primitives."""

    wall: Any
    frame: WallFrame
    extents: FaceExtents
    gaps: Tuple[Span, ...]

    @property
    def layer(self) -> str:
        """Parapets are partial-height (§7 layer table); everything else is A-WALL."""
        return A_WALL_PART if self.wall.kind == "parapet" else A_WALL

    @property
    def hatched(self) -> bool:
        """§7: "fill hatch external"."""
        return self.wall.kind == "external"


def wall_band(wall: Any, neighbours: Sequence[Any], openings: Sequence[Any]) -> Optional[WallBand]:
    """Resolve one wall into its frame, mitred face extents and opening gaps."""
    frame = wall_frame(wall)
    if frame is None:
        return None
    gaps: List[Span] = []
    for opening in openings:
        if opening.wall_id != wall.id:
            continue
        span = opening_span(frame, opening)
        if span is not None:
            gaps.append(span)
    return WallBand(wall, frame, face_extents(wall, frame, neighbours), merge_spans(gaps))


def wall_primitives(band: WallBand, *, hatch_spacing_mm: int) -> Tuple[Primitive, ...]:
    """The double lines, the end caps, the jambs and the external hatch of one wall.

    ``hatch_spacing_mm`` is paper-scaled by the caller: poché lines have to stay the
    same distance apart on paper whatever the drawing scale, or a 1:50 sheet turns
    solid black.
    """
    out: List[Primitive] = []
    frame = band.frame
    extents = band.extents
    half = frame.half_mm
    layer = band.layer
    owner = band.wall.id

    # -- the two faces, broken by every opening ---------------------------
    for across, start, end in (
        (half, extents.left_start_mm, extents.left_end_mm),
        (-half, extents.right_start_mm, extents.right_end_mm),
    ):
        for run in split_span(start, end, band.gaps):
            out.append(
                Line(
                    layer=layer,
                    a=frame.at(run.start_mm, across),
                    b=frame.at(run.end_mm, across),
                    owner_id=owner,
                    kind=K_WALL_FACE,
                )
            )

    # -- end caps: only where the wall genuinely stops ---------------------
    # A wall that butts into another needs no cap — the neighbour's face closes it, and
    # a cap drawn there would read as a joint that does not exist. The test for "is this
    # end open" is that the mitre left both faces at their unadjusted position.
    for at_mm, left_mm, right_mm in (
        (0, extents.left_start_mm, extents.right_start_mm),
        (frame.length_mm, extents.left_end_mm, extents.right_end_mm),
    ):
        if left_mm == at_mm and right_mm == at_mm:
            out.append(
                Line(
                    layer=layer,
                    a=frame.at(at_mm, half),
                    b=frame.at(at_mm, -half),
                    owner_id=owner,
                    kind=K_WALL_END,
                )
            )

    # -- external poché, broken at the openings too ------------------------
    if band.hatched:
        for run in split_span(extents.start_mm, extents.end_mm, band.gaps):
            boundary = _band_quad(frame, extents, run)
            if boundary is not None:
                out.append(
                    Hatch(
                        layer=layer,
                        boundary=boundary,
                        pattern=PATTERN_MASONRY,
                        angle_deg=45,
                        spacing_mm=hatch_spacing_mm,
                        owner_id=owner,
                        kind=K_WALL_HATCH,
                    )
                )
    return tuple(out)


def _band_quad(
    frame: WallFrame, extents: FaceExtents, run: Span
) -> Optional[Tuple[Point, ...]]:
    """The mitred quad of one solid run: right face out, left face back.

    Clamping each corner to its own face's extents is what keeps the hatch inside the
    mitre instead of spilling over the neighbour's face.
    """
    half = frame.half_mm
    right_start = min(max(run.start_mm, extents.right_start_mm), extents.right_end_mm)
    right_end = max(min(run.end_mm, extents.right_end_mm), extents.right_start_mm)
    left_start = min(max(run.start_mm, extents.left_start_mm), extents.left_end_mm)
    left_end = max(min(run.end_mm, extents.left_end_mm), extents.left_start_mm)
    if right_end <= right_start and left_end <= left_start:
        return None
    return (
        frame.at(right_start, -half),
        frame.at(right_end, -half),
        frame.at(left_end, half),
        frame.at(left_start, half),
    )


# ---------------------------------------------------------------------------
# Opening symbols
# ---------------------------------------------------------------------------
def opening_primitives(band: WallBand, opening: Any) -> Tuple[Primitive, ...]:
    """One opening's plan symbol: jambs, then the door/window/ventilator itself.

    §7: "openings break walls (door arc + leaf, window triple line)". The break is
    already done by :func:`wall_primitives`; what is added here is what tells a reviewer
    *which kind of hole* it is:

    door
        two jambs, the leaf at 90°, and the swing arc — hand and side taken from the
        opening's ``swing`` field, matching the canvas.
    window
        two jambs and three lines: a frame line inside each reveal plus the centre
        glazing line §7 asks for.
    ventilator
        the same two frame lines, and a **dashed** centre line. A ventilator sits above
        head height, so dashing it is the drafting convention for "cut plane passes
        below this" — and it makes a 600mm ventilator impossible to mistake for a small
        window on a printed sheet.
    """
    frame = band.frame
    span = opening_span(frame, opening)
    if span is None:
        return ()
    half = frame.half_mm
    layer = A_DOOR if opening.kind == "door" else A_WIND
    owner = opening.id
    out: List[Primitive] = [
        Line(
            layer=layer,
            a=frame.at(span.start_mm, half),
            b=frame.at(span.start_mm, -half),
            owner_id=owner,
            kind=K_WALL_JAMB,
        ),
        Line(
            layer=layer,
            a=frame.at(span.end_mm, half),
            b=frame.at(span.end_mm, -half),
            owner_id=owner,
            kind=K_WALL_JAMB,
        ),
    ]

    if opening.kind == "door":
        out.extend(_door_primitives(frame, span, opening))
        return tuple(out)

    inset = max(1, half // WINDOW_REVEAL_DIVISOR)
    kind = K_WINDOW_GLAZING if opening.kind == "window" else K_VENT_GLAZING
    for across, dashed in ((half - inset, False), (-(half - inset), False), (0, opening.kind == "ventilator")):
        out.append(
            Line(
                layer=layer,
                a=frame.at(span.start_mm, across),
                b=frame.at(span.end_mm, across),
                dashed=dashed,
                owner_id=owner,
                kind=kind,
            )
        )
    return tuple(out)


def _door_primitives(frame: WallFrame, span: Span, opening: Any) -> List[Primitive]:
    """Leaf line + swing arc. Hand and side per the model's ``swing`` enum."""
    hinge_at_start = opening.swing in ("in-left", "out-left")
    side = 1 if opening.swing in ("in-left", "in-right") else -1
    hinge_mm = span.start_mm if hinge_at_start else span.end_mm
    leaf_mm = span.length_mm
    hinge = frame.at(hinge_mm, 0)

    # The open leaf, drawn at 90° to the wall on the swing's side.
    leaf_end = frame.at(hinge_mm, side * leaf_mm)

    # The arc joins the open leaf to the closed position (along the wall, away from the
    # hinge). Both vectors are unit-length in the wall frame, so the sweep is exactly
    # 90°; ordering them so the sweep runs CCW is what makes the DXF ARC come out on the
    # correct side of the wall.
    dir_sign = 1 if hinge_at_start else -1
    open_deg = (frame.angle_deg + (90 if side > 0 else -90)) % 360
    closed_deg = frame.angle_deg if dir_sign > 0 else (frame.angle_deg + 180) % 360
    if (open_deg - closed_deg) % 360 == 90:
        start_deg, end_deg = closed_deg, open_deg
    else:
        start_deg, end_deg = open_deg, closed_deg

    return [
        Line(
            layer=A_DOOR,
            a=hinge,
            b=leaf_end,
            owner_id=opening.id,
            kind=K_DOOR_LEAF,
        ),
        Arc(
            layer=A_DOOR,
            centre=hinge,
            radius_mm=leaf_mm,
            start_deg=start_deg,
            end_deg=end_deg,
            owner_id=opening.id,
            kind=K_DOOR_SWING,
        ),
    ]


def wall_bands(walls: Sequence[Any], openings: Sequence[Any]) -> Tuple[WallBand, ...]:
    """Resolve every wall of a storey against its neighbours. Degenerate walls skipped."""
    bands: List[WallBand] = []
    for wall in walls:
        band = wall_band(wall, walls, openings)
        if band is not None:
            bands.append(band)
    return tuple(bands)


def band_index(bands: Sequence[WallBand]) -> Dict[str, WallBand]:
    return {band.wall.id: band for band in bands}


__all__ = [
    "DOOR_ARC_STEPS",
    "WINDOW_REVEAL_DIVISOR",
    "FaceExtents",
    "Span",
    "WallBand",
    "WallFrame",
    "band_index",
    "clipped_gap_total",
    "face_extents",
    "merge_spans",
    "opening_primitives",
    "opening_span",
    "split_span",
    "wall_band",
    "wall_bands",
    "wall_frame",
    "wall_primitives",
]
