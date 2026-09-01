"""A plot with a zero setback must still generate. Two shipped defects, both here.

Zero side setbacks are ordinary on small Indian plots — many city rules allow, and on
narrow plots effectively require, building to the boundary. Before these fixes the
product refused such a site outright, and the message it gave named the plot shape
rather than anything an architect could act on.

Both defects are the repository's usual shape: every line reads correctly, and only a
real input shows the fault.

1. ``_segments_properly_intersect`` compared orientation signs without first rejecting
   a ZERO determinant, so an endpoint lying exactly on the other segment's line was
   read as a crossing. A zero setback puts the envelope's corner exactly on the plot
   boundary, so ``polygon_contains_polygon`` said the envelope escaped its own plot.

2. The resulting ``EnvelopeError`` said "offset escaped the plot at 0 vertex/vertices:
   []" — reporting, in its own detail line, that nothing had escaped.
"""

from __future__ import annotations

import pytest

from services.solver.envelope import EnvelopeError, derive_envelope, offset_polygon_inward
from services.solver.geometry import (
    _segments_intersect,
    _segments_properly_intersect,
    polygon_contains_polygon,
)
from services.solver.types import PlotEdge, RegProfile

FT = 304
PROFILE = RegProfile(
    city_pack="blr", coverage_percent=60, far_x100=175, max_height_mm=11000, max_floors=3
)


def _plot(width_ft: int, depth_ft: int):
    w, h = int(width_ft * FT), int(depth_ft * FT)
    return ((0, 0), (w, 0), (w, h), (0, h))


def _edges(setbacks):
    roles = ("front", "side", "rear", "side")
    return tuple(PlotEdge(index=i, role=roles[i], setback_mm=s) for i, s in enumerate(setbacks))


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def test_touching_at_an_endpoint_is_not_a_proper_crossing() -> None:
    """The exact case a zero setback produces: a corner ON the boundary line.

    THIS is the assertion that was false in the shipped code.
    """
    assert not _segments_properly_intersect((0, 0), (10, 0), (10, -5), (10, 5))
    # ... and the mirror, so the fix cannot be a sign-flip that moves the bug.
    assert not _segments_properly_intersect((10, 0), (0, 0), (10, -5), (10, 5))


def test_collinear_overlap_is_not_a_proper_crossing() -> None:
    assert not _segments_properly_intersect((0, 0), (10, 0), (5, 0), (15, 0))


def test_a_real_crossing_is_still_a_crossing() -> None:
    """NEGATIVE CONTROL. Without this, `return False` would pass every test above."""
    assert _segments_properly_intersect((0, 0), (10, 10), (0, 10), (10, 0))
    assert _segments_properly_intersect((-5, 1), (5, 1), (0, -5), (0, 5))


def test_any_intersection_still_counts_touching() -> None:
    """`_segments_intersect` feeds the self-intersection check and must NOT change.

    Loosening the proper-crossing predicate would have quietly loosened this too if it
    had relied on it alone; it falls back to `_on_segment`, and this holds it there.
    """
    assert _segments_intersect((0, 0), (10, 0), (10, -5), (10, 5))
    assert _segments_intersect((0, 0), (10, 0), (5, 0), (15, 0))
    assert not _segments_intersect((0, 0), (1, 1), (5, 5), (6, 6))


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


def test_an_envelope_flush_with_the_boundary_is_contained() -> None:
    plot = _plot(30, 40)
    flush = offset_polygon_inward(plot, (1500, 0, 1500, 1000))
    assert all(
        0 <= x <= plot[1][0] and 0 <= y <= plot[2][1] for x, y in flush
    ), "sanity: the offset itself is inside"
    assert polygon_contains_polygon(plot, flush)


@pytest.mark.parametrize(
    "setbacks",
    [
        (1500, 0, 1500, 1000),  # one side on the boundary
        (1500, 0, 1500, 0),  # both sides on the boundary (a row house)
        (0, 1000, 1500, 1000),  # built to the front line
    ],
    ids=["one-side", "row-house", "front"],
)
def test_a_zero_setback_still_yields_a_buildable_envelope(setbacks) -> None:
    """The product-level assertion: these sites generate instead of being refused."""
    envelope = derive_envelope(_plot(30, 40), _edges(setbacks), PROFILE, storeys=2)
    assert envelope.polygon, "a zero setback must still produce an envelope"


def test_setbacks_that_really_do_consume_the_plot_are_still_refused() -> None:
    """NEGATIVE CONTROL for the parametrised case above.

    Without it, deleting the containment check entirely would satisfy every other test
    in this file — and the guard exists to catch offsets that genuinely went wrong.
    """
    with pytest.raises(EnvelopeError):
        derive_envelope(_plot(30, 40), _edges((6000, 6000, 6000, 6000)), PROFILE, storeys=1)
