"""The primitive contract this engine emits — **owned by the projection module.**

§7's pipeline has one narrow waist: ``model → 2D projection primitives → SVG / DXF``.
``services.drawings.projection.primitives`` defines it (``Line``, ``Arc``, ``Text``,
``Hatch``, ``Polyline``, integer-mm ``Point`` tuples), and the auto-dimensioning engine
is one producer on that stream, not a second dialect of it. So this module deliberately
defines **no types at all**: it re-exports theirs and adds the three things that are
specific to dimensioning.

Why re-export rather than import directly everywhere: one file names the dependency, so
if the contract moves there is one import to change, and a reader of ``render.py`` can
see at a glance which primitives a dimension is allowed to be made of.

What the engine emits, and nothing else:

* ``Line`` on ``A-DIM``, with ``kind`` one of :data:`DIM_KINDS` — the dimension line
  itself, a witness (extension) line, an oblique tick, or a leader.
* ``Text`` on ``A-DIM``, ``h_align="center"``, ``v_align="middle"``, rotation 0 or 90.

No arcs, no hatches, no polylines: a dimension is straight lines and numbers. And
because the primitive vocabulary cannot express a script or a ``foreignObject``, §13's
SVG sanitisation rule holds structurally rather than by review.

``kind`` is a hint, never a geometry decision (their rule, and it is a good one): a
renderer may give witness lines a lighter weight or a CSS class from it, but the geometry
it draws comes from the coordinates.
"""

from __future__ import annotations

from typing import Tuple

from services.drawings.projection.primitives import (
    Line,
    Point,
    Primitive,
    Text,
    bbox_of,
    canonical_json,
    point,
    primitives_digest,
    primitives_to_json,
    validate_primitives,
)

#: The dimension line between the chain's terminators.
KIND_DIM = "dim-line"
#: Witness (extension) line, from the measured feature out to the dimension line.
KIND_WITNESS = "dim-witness"
#: Architectural oblique tick at a breakpoint (``dimtsz`` in the DIMSTYLE).
KIND_TICK = "dim-tick"
#: Leader to a label that could not sit on its own dimension line.
KIND_LEADER = "dim-leader"
#: The dimension value itself.
KIND_TEXT = "dim-text"

DIM_KINDS: Tuple[str, ...] = (
    KIND_DIM,
    KIND_WITNESS,
    KIND_TICK,
    KIND_LEADER,
    KIND_TEXT,
)

__all__ = [
    "DIM_KINDS",
    "KIND_DIM",
    "KIND_LEADER",
    "KIND_TEXT",
    "KIND_TICK",
    "KIND_WITNESS",
    "Line",
    "Point",
    "Primitive",
    "Text",
    "bbox_of",
    "canonical_json",
    "point",
    "primitives_digest",
    "primitives_to_json",
    "validate_primitives",
]
