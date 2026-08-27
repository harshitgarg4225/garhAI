"""§5.1 — buildable envelope derivation. **Fully implemented.**

    Offset plot polygon inward by per-edge setbacks → buildable envelope. Validate
    coverage: envelope area vs allowed ground coverage; if brief's target built area /
    floors > envelope, shrink footprint target and record the assumption chip.

This is pure integer geometry with no CP-SAT in it, so it is real code today rather
than a Phase 3 stub. Everything downstream depends on it being right: a 100mm error
here is a 100mm error on every dimension in the submission drawing set.

**The offset algorithm.** For a CCW polygon the interior lies to the *left* of each
directed edge, so each edge's inward normal is its left normal. Every edge is pushed
inward by its own setback, and the new vertices are the intersections of consecutive
offset lines. Offsetting the *lines* rather than the *vertices* is what makes per-edge
setbacks work at all — a corner between a 1500mm front setback and a 900mm side setback
has no single "offset distance", only two lines that meet somewhere.

**Where floats appear, and why that is safe.** A line intersection is a division, so
the arithmetic is done in ``float`` and the result is immediately rounded back with
``round_half_away`` — the model core's rounding contract. Doubles carry 53 bits of
mantissa; plot coordinates are under 10^5 mm and the intersection maths stays well
inside 10^15, so the rounded result is exact for every input this system accepts. The
alternative (exact rationals) would cost clarity and buy nothing measurable.

**Degenerate cases are refused, not approximated.** Setbacks that consume the plot, or
that fold the boundary through itself, produce :class:`EnvelopeError` with copy an
architect can act on — not a tiny or self-intersecting polygon that would silently
poison the solver.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from services.common.assumptions import Assumption
from services.common.logging import get_logger
from services.solver.geometry import (
    Polygon,
    Pt,
    area_mm2,
    dedupe_collinear,
    ensure_ccw,
    is_simple,
    point_in_polygon,
    polygon_contains_polygon,
    round_half_away,
)
from services.solver.types import BuildableEnvelope, PlotEdge, RegProfile

log = get_logger("solver.envelope")

#: Below this the envelope cannot hold a habitable room; treat as unbuildable.
MIN_ENVELOPE_AREA_MM2 = 4_000_000  # 4 m²
#: Two edges whose directions differ by less than this are treated as parallel.
_PARALLEL_EPSILON = 1e-9


class EnvelopeError(ValueError):
    """The setbacks leave nothing to build on. Carries user-facing copy."""

    def __init__(self, message: str, *, action: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.action = action or "Check the setbacks on the Plot tab."
        self.detail = detail


def offset_polygon_inward(polygon: Polygon, setbacks_mm: Sequence[int]) -> Polygon:
    """Offset each edge inward by its own setback. Integer mm in, integer mm out.

    ``setbacks_mm[i]`` applies to the edge from vertex ``i`` to vertex ``i+1``. A zero
    setback leaves that edge where it is.
    """
    ring = dedupe_collinear(ensure_ccw(polygon))
    count = len(ring)
    if count < 3:
        raise EnvelopeError(
            "This plot boundary isn't a closed shape yet.",
            action="Draw at least three corners on the Plot tab.",
            detail="polygon has %d distinct vertices after collinear dedupe" % count,
        )
    if len(setbacks_mm) != count:
        raise EnvelopeError(
            "The setbacks don't line up with the plot's edges.",
            detail="%d setbacks for %d edges (after collinear dedupe)" % (len(setbacks_mm), count),
        )
    if any(value < 0 for value in setbacks_mm):
        raise EnvelopeError(
            "A setback can't be negative.",
            detail="setbacks: %s" % list(setbacks_mm),
        )

    # Each edge becomes a line pushed inward along its left normal.
    lines: list[tuple[float, float, float, float]] = []  # (px, py, dx, dy)
    for index in range(count):
        ax, ay = ring[index]
        bx, by = ring[(index + 1) % count]
        dx = float(bx - ax)
        dy = float(by - ay)
        length = math.hypot(dx, dy)
        if length == 0.0:
            raise EnvelopeError(
                "Two corners of the plot are in the same place.",
                detail="zero-length edge at vertex %d" % index,
            )
        # Left normal of (dx, dy) is (-dy, dx); for a CCW ring that points inward.
        offset = float(setbacks_mm[index])
        nx = -dy / length * offset
        ny = dx / length * offset
        lines.append((ax + nx, ay + ny, dx, dy))

    vertices: list[Pt] = []
    for index in range(count):
        previous = lines[index - 1]
        current = lines[index]
        point = _intersect(previous, current)
        if point is None:
            # Parallel neighbours: the corner between them is a straight run. Keeping
            # the current line's start point preserves the edge without inventing a
            # vertex that the two lines never actually share.
            point = (current[0], current[1])
        vertices.append((round_half_away(point[0]), round_half_away(point[1])))

    return dedupe_collinear(tuple(vertices))


def derive_envelope(
    plot_polygon: Polygon,
    edges: Sequence[PlotEdge],
    profile: RegProfile,
    *,
    target_built_up_mm2: int = 0,
    storeys: int = 1,
) -> BuildableEnvelope:
    """Full §5.1: offset, validate, and reconcile the brief's target with the caps.

    ``target_built_up_mm2`` is what the brief wants across all floors. When it cannot
    fit — because of coverage, FAR, or the envelope itself — the target is reduced and
    an assumption chip records exactly which limit bound and by how much.
    """
    ring = dedupe_collinear(ensure_ccw(plot_polygon))
    if len(ring) != len(edges):
        raise EnvelopeError(
            "The plot's edges and their setbacks don't match up.",
            detail="%d edges supplied for a %d-sided boundary" % (len(edges), len(ring)),
        )

    plot_area = area_mm2(ring)
    if plot_area <= 0:
        raise EnvelopeError(
            "This plot has no area yet.",
            action="Draw the plot boundary on the Plot tab.",
            detail="plot area is %d mm2" % plot_area,
        )

    setbacks = [edge.setback_mm for edge in edges]
    envelope = offset_polygon_inward(ring, setbacks)
    _validate(envelope, ring, setbacks)

    envelope_area = area_mm2(envelope)
    allowed_footprint = profile.allowed_footprint_mm2(plot_area)
    allowed_built_up = profile.allowed_built_up_mm2(plot_area)
    effective_footprint = min(envelope_area, allowed_footprint)

    assumptions: list[Assumption] = []
    notes: list[str] = []

    if allowed_footprint > envelope_area:
        notes.append(
            "Setbacks bind harder than ground coverage on this plot: the envelope is "
            "%d m2 against a %d m2 coverage allowance."
            % (envelope_area // 1_000_000, allowed_footprint // 1_000_000)
        )

    floors = max(1, storeys)
    target = target_built_up_mm2
    if target <= 0:
        target = effective_footprint * floors
        assumptions.append(
            Assumption(
                field="envelope.targetBuiltUpAreaMm2",
                value=target,
                reason=(
                    "The brief didn't give a built-up area, so we aimed to use the "
                    "buildable envelope across all %d floor(s)." % floors
                ),
                source="solver-envelope",
            )
        )

    if target > allowed_built_up:
        assumptions.append(
            Assumption(
                field="envelope.targetBuiltUpAreaMm2",
                value=allowed_built_up,
                reason=(
                    "The requested built-up area needs more floor space than the FAR "
                    "allows here, so we reduced it from %d m2 to %d m2."
                    % (target // 1_000_000, allowed_built_up // 1_000_000)
                ),
                cite="FAR %s (%s pack)" % (_far_text(profile.far_x100), profile.city_pack),
                source="solver-envelope",
            )
        )
        target = allowed_built_up

    per_floor = _ceil_div(target, floors)
    target_footprint = per_floor
    if per_floor > effective_footprint:
        bound = "ground coverage" if allowed_footprint < envelope_area else "the setbacks"
        assumptions.append(
            Assumption(
                field="envelope.targetFootprintAreaMm2",
                value=effective_footprint,
                reason=(
                    "Each floor would have to cover %d m2 to reach the requested area, "
                    "but %s limit the footprint to %d m2. We shrank the footprint — "
                    "adding a floor would recover the space."
                    % (per_floor // 1_000_000, bound, effective_footprint // 1_000_000)
                ),
                cite=(
                    "Coverage %d%% (%s pack)" % (profile.coverage_percent, profile.city_pack)
                    if allowed_footprint < envelope_area
                    else None
                ),
                source="solver-envelope",
            )
        )
        target_footprint = effective_footprint

    log.info(
        "solver.envelope.derived",
        plot_area_mm2=plot_area,
        envelope_area_mm2=envelope_area,
        effective_footprint_mm2=effective_footprint,
        target_footprint_mm2=target_footprint,
        assumption_count=len(assumptions),
    )
    return BuildableEnvelope(
        polygon=envelope,
        area_mm2=envelope_area,
        allowed_footprint_mm2=allowed_footprint,
        effective_footprint_mm2=effective_footprint,
        allowed_built_up_mm2=allowed_built_up,
        target_footprint_mm2=target_footprint,
        assumptions=tuple(assumptions),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _validate(envelope: Polygon, plot: Polygon, setbacks: Sequence[int]) -> None:
    """Refuse a degenerate envelope loudly rather than solving inside nonsense."""
    largest = max(setbacks) if setbacks else 0
    if len(envelope) < 3:
        raise EnvelopeError(
            "These setbacks leave no room to build.",
            detail="offset collapsed the boundary to %d vertices (max setback %dmm)"
            % (len(envelope), largest),
        )
    if not is_simple(envelope):
        raise EnvelopeError(
            "These setbacks fold the buildable area in on itself.",
            action="Reduce a setback, or check the plot shape for a very narrow corner.",
            detail="offset polygon self-intersects (max setback %dmm)" % largest,
        )

    envelope_area = area_mm2(envelope)
    if envelope_area < MIN_ENVELOPE_AREA_MM2:
        raise EnvelopeError(
            "After setbacks there's only %.1f m2 left to build on." % (envelope_area / 1_000_000.0),
            action="Reduce the setbacks, or check the plot dimensions.",
            detail="envelope area %d mm2 < minimum %d mm2" % (envelope_area, MIN_ENVELOPE_AREA_MM2),
        )

    # An inward offset that escapes the plot means the maths went wrong, not that the
    # user did something odd — fail loudly rather than hand the solver a bad envelope.
    if not polygon_contains_polygon(plot, envelope):
        outside = [point for point in envelope if not point_in_polygon(point, plot)]
        raise EnvelopeError(
            "We couldn't work out the buildable area for this plot shape.",
            action="Try simplifying the boundary, or contact support with this plot.",
            detail="offset escaped the plot at %d vertex/vertices: %s"
            % (len(outside), outside[:4]),
        )


def _intersect(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> tuple[float, float] | None:
    """Intersect two lines given as (point, direction). ``None`` when parallel."""
    px, py, dx, dy = first
    qx, qy, ex, ey = second
    denominator = dx * ey - dy * ex
    if abs(denominator) < _PARALLEL_EPSILON:
        return None
    t = ((qx - px) * ey - (qy - py) * ex) / denominator
    return (px + t * dx, py + t * dy)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator) if denominator else numerator


def _far_text(far_x100: int) -> str:
    whole, fraction = divmod(far_x100, 100)
    return "%d.%02d" % (whole, fraction)


__all__ = [
    "MIN_ENVELOPE_AREA_MM2",
    "EnvelopeError",
    "derive_envelope",
    "offset_polygon_inward",
]
