"""Label boxes, the one place a sheet's text is measured. **Pure integer arithmetic.**

Three things used to measure text independently — the pipeline's collision audit, the
golden harness and ``test_render`` — and each boxed only :class:`Text` primitives, so a
room name sitting on a dimension figure was invisible to all of them (the figure lives
inside a :class:`Dim` and is exploded at render time). This module is the single
measurer now:

* :func:`text_box_mm` — one text's box in **model mm** at a sheet scale, honouring
  anchor, baseline and the two rotations the sheets use. This is what the plan uses to
  keep an opening tag off a room label *before* anything is drawn (§7 step 4's greedy
  placer, in miniature).
* :func:`label_boxes` — every text on a sheet as paper-µm :class:`LabelBox` es,
  **dimension figures included**, for the collision audit. The pipeline, the harness
  and the test all call this, so they cannot disagree about what a collision is.

The width metric is the 0.58-em-per-character estimate the audit has always used. It is
generous on purpose: over-reporting a near miss is the right failure mode for a gate
whose job is to keep a municipal reviewer from squinting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from services.drawings.dimensions import LabelBox
from services.drawings.render.primitives import (
    Dim,
    Placement,
    SheetDrawing,
    Text,
    dim_geometry,
    div_round,
)

T = TypeVar("T")

__all__ = [
    "TEXT_WIDTH_PERCENT",
    "Box",
    "dim_label_texts",
    "first_free",
    "label_boxes",
    "paper_box",
    "paper_to_model_mm",
    "text_box_mm",
    "text_width_mm",
]

#: Estimated glyph advance as a percentage of the text height (0.58 em).
TEXT_WIDTH_PERCENT = 58


@dataclass(frozen=True)
class Box:
    """An axis-aligned box in model mm, Y-up. Touching is not overlapping."""

    x0: int
    y0: int
    x1: int
    y1: int

    def overlaps(self, other: Box) -> bool:
        return not (
            self.x1 <= other.x0 or other.x1 <= self.x0 or self.y1 <= other.y0 or other.y1 <= self.y0
        )

    def inside(self, outer: Box) -> bool:
        return (
            outer.x0 <= self.x0
            and self.x1 <= outer.x1
            and outer.y0 <= self.y0
            and self.y1 <= outer.y1
        )

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def paper_to_model_mm(paper_um: int, scale_denominator: int) -> int:
    """``2500 µm`` of paper at 1:100 is ``250 mm`` of building."""
    return div_round(paper_um * scale_denominator, 1000)


def text_width_mm(text: str, height_mm: int) -> int:
    return len(text) * height_mm * TEXT_WIDTH_PERCENT // 100


def text_box_mm(prim: Text, scale_denominator: int) -> Box:
    """The box a text occupies in **Y-up model mm**, at the scale it will be drawn at.

    For placing labels inside a building before it is put on paper (the plan's room
    labels and opening tags). Rotation is 0 or 90 — the only two the sheets emit; a 90°
    label reads bottom-to-top and its glyphs' "up" points to −x. A group placed with
    ``flip_y=False`` is paper space, not model space: measure that with
    :func:`paper_box` instead.
    """
    height = paper_to_model_mm(prim.height_paper_um, scale_denominator)
    width = text_width_mm(prim.text, height)
    x, y = prim.at
    if prim.rotation_deg % 180 == 0:
        if prim.anchor == "middle":
            x0 = x - width // 2
        elif prim.anchor == "end":
            x0 = x - width
        else:
            x0 = x
        if prim.baseline == "middle":
            y0 = y - height // 2
        elif prim.baseline == "hanging":
            y0 = y - height
        else:
            y0 = y
        return Box(x0, y0, x0 + width, y0 + height)
    # 90°: the run is along +y, the glyph height along −x.
    if prim.anchor == "middle":
        y0 = y - width // 2
    elif prim.anchor == "end":
        y0 = y - width
    else:
        y0 = y
    if prim.baseline == "middle":
        x0 = x - height // 2
    elif prim.baseline == "hanging":
        x0 = x
    else:
        x0 = x - height
    return Box(x0, y0, x0 + height, y0 + width)


def dim_label_texts(dim: Dim, scale_denominator: int) -> tuple[Text, ...]:
    """The figures a chain prints, as the very :class:`Text` s the SVG emitter draws."""
    geometry = dim_geometry(dim, scale_denominator=scale_denominator)
    return tuple(
        Text(
            at=at,
            text=label,
            layer=dim.layer,
            height_paper_um=dim.text_height_paper_um,
            anchor="middle",
            baseline="baseline",
            rotation_deg=rotation,
        )
        for at, label, rotation in geometry.labels
    )


def paper_box(prim: Text, placement: Placement, owner: str = "") -> LabelBox:
    """The box a text occupies on the sheet, in paper µm, Y-down from the top-left.

    Built in paper space rather than converted from :func:`text_box_mm`, because a
    title-block group is placed with ``flip_y=False`` (its "model" mm are already paper
    mm running downward) and a baseline text there extends *up* the page — the opposite
    of what a Y-up model box would say. A 90° label reads bottom-to-top on the page, so
    its run goes to smaller y and its glyphs' "up" points to −x.
    """
    x, y = placement.to_paper_um(prim.at)
    height = int(prim.height_paper_um)
    width = len(prim.text) * height * TEXT_WIDTH_PERCENT // 100
    if prim.rotation_deg % 180 == 0:
        if prim.anchor == "middle":
            x0 = x - width // 2
        elif prim.anchor == "end":
            x0 = x - width
        else:
            x0 = x
        if prim.baseline == "middle":
            y0 = y - height // 2
        elif prim.baseline == "hanging":
            y0 = y
        else:
            y0 = y - height
        return LabelBox(x_mm=x0, y_mm=y0, width_mm=width, height_mm=height, owner_id=owner)
    if prim.anchor == "middle":
        y0 = y - width // 2
    elif prim.anchor == "end":
        y0 = y
    else:
        y0 = y - width
    if prim.baseline == "middle":
        x0 = x - height // 2
    elif prim.baseline == "hanging":
        x0 = x
    else:
        x0 = x - height
    return LabelBox(x_mm=x0, y_mm=y0, width_mm=height, height_mm=width, owner_id=owner)


def label_boxes(drawing: SheetDrawing) -> tuple[LabelBox, ...]:
    """Every label on the sheet, in paper µm, dimension figures included.

    Owner ids are ``<group>#<index>`` for a text and ``<group>#<index>/<chain>`` for a
    dimension figure, so a reported collision names what hit what.
    """
    boxes: list[LabelBox] = []
    for group in drawing.groups:
        scale = group.placement.scale_denominator
        for index, primitive in enumerate(group.primitives):
            if isinstance(primitive, Text):
                if not primitive.text.strip():
                    continue
                boxes.append(paper_box(primitive, group.placement, "%s#%d" % (group.id, index)))
            elif isinstance(primitive, Dim):
                owner = "%s#%d/%s" % (group.id, index, primitive.chain.id)
                for figure in dim_label_texts(primitive, scale):
                    boxes.append(paper_box(figure, group.placement, owner))
    return tuple(boxes)


def first_free(candidates: Sequence[tuple[T, Box]], placed: Sequence[Box]) -> tuple[T, Box, bool]:
    """The first candidate whose box overlaps nothing already placed.

    Returns ``(item, box, found)``; when every candidate collides the first one is
    returned with ``found=False`` so the caller still draws the label — a tag that is
    missing is worse than a tag that touches something, and the audit reports the touch.
    """
    for item, box in candidates:
        if not any(box.overlaps(other) for other in placed):
            return (item, box, True)
    item, box = candidates[0]
    return (item, box, False)
