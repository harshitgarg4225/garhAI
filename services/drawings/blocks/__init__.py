"""Parametric 2D block library — the drawn symbols a plan is read by.

A door on a plan is not a gap in a wall: it is a leaf and the quarter circle it sweeps.
A window is a double line with a sill. A stair is treads, a break line and ``UP 18R``.
Without them the drawing set is a set of outlines, and outlines are not what an Indian
architect's fee is earned on.

**These are conventions, not artwork.** A door symbol is a rectangle plus an arc; the
convention is published, unownable, and older than CAD. So this library *generates*
every symbol from its real dimensions instead of sourcing a block set, and there is no
licence question to answer, no file to keep in step with, and no size a block does not
come in.

How it fits together
--------------------
Everything here emits the §7 primitive vocabulary from
:mod:`services.drawings.render.primitives` — ``Line``, ``Polyline``, ``Arc``,
``Circle``, ``Text``, ``Hatch`` — in **integer millimetres**, on one of the nine §7
layers. That is what makes a block drawable identically in SVG, PDF and DXF: it is the
same primitive list the rest of the drawing engine consumes, so a door cannot swing one
way in the browser and the other in AutoCAD.

Three rules hold across every block:

* **Local frame, placed once.** A block is authored around the origin in its own natural
  orientation and moved by :func:`~services.drawings.blocks.base.place`, which is also
  the only thing that rotates. Rounding therefore happens once per block, not once per
  primitive.
* **Every primitive carries an ``element_id``.** ``place`` stamps it, so a block cannot
  emit an unclickable primitive even by accident.
* **Layer discipline is checked.** Each block declares in
  :mod:`services.drawings.blocks.catalog` which of the nine layers it may draw on, and
  the tests assert it. Doors are not on the furniture layer, sills are on A-WALL-PART
  because that is what ``layers.py`` says A-WALL-PART is for, and a bathtub is never on
  A-WALL, where a reviewer measures setbacks.

Not yet wired into the sheet pipeline: ``services/drawings/pipeline.py`` and
``projection/`` are another tree. This package is standalone, pure, dependency-free and
provable on its own; wiring is a separate, deliberate step.
"""

from __future__ import annotations

from services.drawings.blocks.base import (
    Insertion,
    arc_endpoint,
    arrow,
    block_extent,
    label_text,
    paper_mm_to_model_mm,
    place,
    readable_rotation,
    round_half_away,
    span,
)
from services.drawings.blocks.doors import (
    HAND_LEFT,
    HAND_RIGHT,
    SWING_IN,
    SWING_OUT,
    door_double_swing,
    door_folding,
    door_single_swing,
    door_sliding,
)
from services.drawings.blocks.electrical import (
    distribution_board,
    fan_point,
    light_point,
    socket,
    switch,
)
from services.drawings.blocks.sanitary import bathtub, shower, sink, washbasin, wc
from services.drawings.blocks.site import north_arrow, parked_car, scale_bar, tree
from services.drawings.blocks.stairs import (
    DIRECTION_DN,
    DIRECTION_UP,
    stair_dogleg,
    stair_spiral,
    stair_straight,
    straight_flight_run_mm,
    tread_count,
)
from services.drawings.blocks.windows import (
    window_casement,
    window_fixed,
    window_sliding,
    window_ventilator,
)

# Imported last: catalog.py reaches back into the six modules above, and importing it
# after them means the package is already carrying those attributes when it does.
from services.drawings.blocks.catalog import (  # isort: skip
    BLOCK_NAMES,
    BLOCK_REGISTRY,
    BlockSpec,
    block_spec,
    build_block,
)

__all__ = [
    "BLOCK_NAMES",
    "BLOCK_REGISTRY",
    "DIRECTION_DN",
    "DIRECTION_UP",
    "HAND_LEFT",
    "HAND_RIGHT",
    "SWING_IN",
    "SWING_OUT",
    "BlockSpec",
    "Insertion",
    "arc_endpoint",
    "arrow",
    "bathtub",
    "block_extent",
    "block_spec",
    "build_block",
    "distribution_board",
    "door_double_swing",
    "door_folding",
    "door_single_swing",
    "door_sliding",
    "fan_point",
    "label_text",
    "light_point",
    "north_arrow",
    "paper_mm_to_model_mm",
    "parked_car",
    "place",
    "readable_rotation",
    "round_half_away",
    "scale_bar",
    "shower",
    "sink",
    "socket",
    "span",
    "stair_dogleg",
    "stair_spiral",
    "stair_straight",
    "straight_flight_run_mm",
    "switch",
    "tread_count",
    "tree",
    "washbasin",
    "wc",
    "window_casement",
    "window_fixed",
    "window_sliding",
    "window_ventilator",
]
