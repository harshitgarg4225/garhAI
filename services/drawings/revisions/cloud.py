"""Revision clouds and delta tags. **Exact integer geometry, no rounding at all.**

A revision cloud is the one annotation on a submission set that a reviewer uses as a
navigation aid: it says *look here, this is what moved since you last saw it*. It is a
closed chain of outward-bulging arcs around the changed area, with a triangular "delta"
tag carrying the revision number beside it.

THE ARCS ARE EXACT, AND THAT TOOK A DECISION
--------------------------------------------
Each scallop is a **semicircle on its own chord**: centre at the chord's midpoint, radius
half the chord. That makes every arc's centre and radius exact integer millimetres and
every start/end angle an exact multiple of 90° on an axis-aligned box — no trigonometry,
no rounding, and therefore no drift between the SVG and the DXF, which draw the same
:class:`~services.drawings.render.primitives.Arc` two different ways.

Exactness is bought by one small adjustment: the cloud rectangle is grown to **even**
width and height (:func:`_even`) before it is scalloped, so every chord can be an even
number of millimetres and every radius stays an integer. A cloud one millimetre larger
than asked for is invisible at any sheet scale; an arc radius rounded down by half a
millimetre leaves a visible nick where two scallops meet.

The alternative — CAD's ~90° arcs, which look slightly tighter — needs
``r = c / (2·sin(θ/2))`` and an off-chord centre, i.e. two irrational quantities rounded
per scallop, on a chain whose whole job is to close. Not worth it for a decoration.

WHICH LAYER, AND WHY NOT A TENTH
--------------------------------
Clouds are drawn on **A-TEXT** — ``services.drawings.layers`` describes it as "room names,
notes and callouts", and a revision cloud is a callout. AIA would give it A-ANNO-REVS, but
:mod:`services.drawings.layers` is explicit that the nine names are a contract with every
downstream consumer and that adding one changes what a municipal reviewer sees. Adding a
tenth layer to draw an annotation that an existing annotation layer already covers is not
a deliberate enough reason. The layer is a parameter on every function here, so a project
that does add one has a single place to point at it.

SCALE
-----
A cloud is *notation*: its scallops are ~4 mm on paper at every drawing scale, exactly like
ISO 3098 text and a north arrow. Every size in this module is therefore given in paper
millimetres and multiplied by the sheet's scale denominator on the way into model space —
the same rule ``blocks.base.paper_mm_to_model_mm`` states for the block library.
"""

from __future__ import annotations

from services.drawings.layers import A_TEXT
from services.drawings.render.primitives import (
    TEXT_HEIGHT_SMALL_PAPER_UM,
    Arc,
    Polyline,
    Primitive,
    Pt2,
    Text,
    div_round,
)
from services.drawings.revisions.diff import Box, ModelDiff, cluster_boxes

__all__ = [
    "CLOUD_BULGE_PAPER_MM",
    "CLOUD_MARGIN_PAPER_MM",
    "CLUSTER_GAP_PAPER_MM",
    "TAG_SIDE_PAPER_MM",
    "cloud_regions",
    "revision_cloud",
    "revision_marks",
    "revision_tag",
]

#: Chord length of one scallop, in paper mm. 4 mm is the CAD default and reads as a cloud
#: rather than as a wobbly rectangle at A2.
CLOUD_BULGE_PAPER_MM = 4
#: Clear gap between the changed geometry and the cloud, in paper mm. Without it the cloud
#: sits on the wall it is pointing at and hides the very edge the reviewer is checking.
CLOUD_MARGIN_PAPER_MM = 3
#: Side of the triangular revision tag, paper mm.
TAG_SIDE_PAPER_MM = 7
#: Two changes closer than this on paper get one cloud between them (see
#: :func:`~services.drawings.revisions.diff.cluster_boxes` for why).
CLUSTER_GAP_PAPER_MM = 12

#: ``round(sqrt(3)/2 * 1000)``. The triangle's height factor, as an integer permille so
#: the tag's vertices are exact for a given side length instead of float-rounded thrice.
_TRIANGLE_HEIGHT_PERMILLE = 866


def _paper(paper_mm: int, scale_denominator: int) -> int:
    """Paper mm -> model mm at a scale. Exact: notation is sized on the print."""
    if scale_denominator <= 0:
        raise ValueError("scale denominator must be positive, got %d" % scale_denominator)
    if paper_mm <= 0:
        raise ValueError("paper size must be positive, got %d" % paper_mm)
    return paper_mm * scale_denominator


def _even(value: int) -> int:
    """The next even number at or above ``value`` — see the module docstring."""
    return value if value % 2 == 0 else value + 1


def _even_chords(length_mm: int, target_mm: int) -> tuple[int, ...]:
    """Split an **even** length into even chords, each as close to ``target`` as possible.

    Exactly, by construction: the halves are integer-partitioned and doubled, so the
    chords sum to ``length_mm`` with no remainder to hide. The §7 dimension chain makes
    the same promise for the same reason — a chain that nearly closes is a chain that is
    wrong somewhere you cannot see.
    """
    if length_mm <= 0 or length_mm % 2:
        raise ValueError("cloud side must be a positive even length, got %d" % length_mm)
    half = length_mm // 2
    count = max(1, min(half, div_round(length_mm, max(2, target_mm))))
    base, remainder = divmod(half, count)
    chords = tuple(2 * (base + (1 if index < remainder else 0)) for index in range(count))
    assert sum(chords) == length_mm, (chords, length_mm)
    return chords


#: ``(start_deg, end_deg)`` for a scallop on each side of a CCW-traversed box. The box is
#: walked anticlockwise, so "outward" is always to the right of travel, and each arc
#: sweeps CCW from its chord's start to its chord's end through the outward direction.
_SIDE_ANGLES = {
    "bottom": (180, 0),  # travels +x, bulges -y (through 270)
    "right": (270, 90),  # travels +y, bulges +x (through 0)
    "top": (0, 180),  # travels -x, bulges +y (through 90)
    "left": (90, 270),  # travels -y, bulges -x (through 180)
}


def revision_cloud(
    box: Box,
    *,
    scale_denominator: int,
    element_id: str,
    layer: str = A_TEXT,
    bulge_paper_mm: int = CLOUD_BULGE_PAPER_MM,
    margin_paper_mm: int = CLOUD_MARGIN_PAPER_MM,
) -> tuple[Arc, ...]:
    """A closed scallop chain enclosing ``box``, with ``margin`` of clear air around it.

    The chain closes exactly: every scallop's end point is the next one's start point,
    because they are cut from the same integer partition of each side.
    """
    if not element_id:
        raise ValueError(
            "a revision cloud needs an element_id — it is what maps a clicked arc back "
            "to the revision that raised it, and an unidentified cloud cannot be removed "
            "when its revision is superseded."
        )
    margin = _paper(margin_paper_mm, scale_denominator)
    bulge = _paper(bulge_paper_mm, scale_denominator)
    x0, y0, x1, y1 = box
    if x1 < x0 or y1 < y0:
        raise ValueError("cloud box is inside out: %r" % (box,))
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    # Grow to even dimensions so every chord — and therefore every radius — is an exact
    # integer. Growth is on the high side only, so the cloud never eats into the margin.
    x1 = x0 + _even(x1 - x0)
    y1 = y0 + _even(y1 - y0)

    arcs: list[Arc] = []

    def run(side: str, start: Pt2, along: Pt2, length: int) -> None:
        start_deg, end_deg = _SIDE_ANGLES[side]
        cursor = start
        for chord in _even_chords(length, bulge):
            radius = chord // 2
            centre = (cursor[0] + along[0] * radius, cursor[1] + along[1] * radius)
            arcs.append(
                Arc(
                    centre=centre,
                    radius_mm=radius,
                    start_deg=start_deg,
                    end_deg=end_deg,
                    layer=layer,
                    element_id=element_id,
                )
            )
            cursor = (cursor[0] + along[0] * chord, cursor[1] + along[1] * chord)

    run("bottom", (x0, y0), (1, 0), x1 - x0)
    run("right", (x1, y0), (0, 1), y1 - y0)
    run("top", (x1, y1), (-1, 0), x1 - x0)
    run("left", (x0, y1), (0, -1), y1 - y0)
    return tuple(arcs)


def revision_tag(
    at: Pt2,
    number: str,
    *,
    scale_denominator: int,
    element_id: str,
    layer: str = A_TEXT,
    side_paper_mm: int = TAG_SIDE_PAPER_MM,
) -> tuple[Primitive, ...]:
    """The delta: an equilateral triangle centred on ``at`` with the revision number in it.

    A cloud without a number says something changed; a cloud with ``R2`` beside it says
    *which issue* changed it, which is what a reviewer holding two prints needs.
    """
    if not number:
        raise ValueError("a revision tag needs the revision number to print")
    side = _paper(side_paper_mm, scale_denominator)
    height = div_round(side * _TRIANGLE_HEIGHT_PERMILLE, 1000)
    cx, cy = at
    # Centroid at `at`: apex 2/3 of the height above it, base 1/3 below.
    apex = (cx, cy + div_round(2 * height, 3))
    base_left = (cx - side // 2, cy - div_round(height, 3))
    base_right = (base_left[0] + side, base_left[1])
    return (
        Polyline(
            vertices=(apex, base_right, base_left),
            layer=layer,
            closed=True,
            element_id=element_id,
        ),
        Text(
            at=(cx, cy),
            text=number,
            layer=layer,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            baseline="middle",
            element_id=element_id,
        ),
    )


def cloud_regions(
    diff: ModelDiff,
    storey_id: str,
    *,
    scale_denominator: int,
    gap_paper_mm: int = CLUSTER_GAP_PAPER_MM,
    include_derived: bool = False,
) -> tuple[Box, ...]:
    """The areas of one storey that need a cloud, merged so they do not overlap.

    Merging is in *paper* space (``gap_paper_mm`` x the scale), because whether two clouds
    read as one blob is a property of the print, not of the building: 1.2 m apart is two
    clear clouds at 1:50 and one smudge at 1:200.

    ``include_derived`` is off by default: a room polygon that only changed because a wall
    moved is not clouded separately, or a 500 mm partition move would put one cloud round
    the whole floor. The wall itself is clouded; see
    :data:`~services.drawings.revisions.diff._DERIVED_FIELDS`.
    """
    boxes = diff.boxes_for_storey(storey_id, include_derived=include_derived)
    if not boxes:
        return ()
    return cluster_boxes(boxes, gap_mm=_paper(gap_paper_mm, scale_denominator))


def revision_marks(
    diff: ModelDiff,
    storey_id: str,
    *,
    revision_number: str,
    scale_denominator: int,
    layer: str = A_TEXT,
    gap_paper_mm: int = CLUSTER_GAP_PAPER_MM,
    bulge_paper_mm: int = CLOUD_BULGE_PAPER_MM,
    margin_paper_mm: int = CLOUD_MARGIN_PAPER_MM,
    tag_side_paper_mm: int = TAG_SIDE_PAPER_MM,
    include_derived: bool = False,
) -> tuple[Primitive, ...]:
    """Every cloud and delta for one storey of one revision, ready to drop on a plan.

    Element ids are ``rev-<number>-<n>``, numbered in the deterministic order
    :func:`cloud_regions` returns, so re-running the same diff on the same model produces
    byte-identical output — a sheet whose clouds move between two runs of one input is not
    reviewable.
    """
    out: list[Primitive] = []
    regions = cloud_regions(
        diff,
        storey_id,
        scale_denominator=scale_denominator,
        gap_paper_mm=gap_paper_mm,
        include_derived=include_derived,
    )
    tag_offset = _paper(tag_side_paper_mm + margin_paper_mm, scale_denominator)
    for index, box in enumerate(regions, start=1):
        element_id = "rev-%s-%d" % (revision_number, index)
        out.extend(
            revision_cloud(
                box,
                scale_denominator=scale_denominator,
                element_id=element_id,
                layer=layer,
                bulge_paper_mm=bulge_paper_mm,
                margin_paper_mm=margin_paper_mm,
            )
        )
        # Outside the cloud's top-left corner, where a title-block-bottom-right sheet
        # convention leaves the most white space.
        out.extend(
            revision_tag(
                (box[0] - tag_offset, box[3] + tag_offset),
                revision_number,
                scale_denominator=scale_denominator,
                element_id=element_id,
                layer=layer,
                side_paper_mm=tag_side_paper_mm,
            )
        )
    return tuple(out)
