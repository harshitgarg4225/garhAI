"""Shared machinery for §7's two **vertical** projections: elevations and the section.

Both drawings answer the same question — *what does the building look like on a vertical
plane?* — so they share one coordinate convention, one reading of the model's levels, one
level-marker emitter and one overall-height chain. That shared part lives here rather
than being written twice, because the failure mode of writing it twice is an elevation
and a section that disagree about where the first-floor FFL is, which is exactly the kind
of defect a municipal reviewer catches and an architect never forgives.

It lives under ``elevations/`` because four of the five vertical drawings in a submission
set are elevations; :mod:`services.drawings.sections` imports it. If the integrator wants
it beside :mod:`services.drawings.projection.primitives` (the other dependency-free
narrow-waist module) it moves with no edits — nothing here imports either package.

The coordinate convention (get this right once)
-----------------------------------------------
A vertical projection is named by the **outward normal** ``n̂`` of the plane, which is the
direction the viewer looks *from*: the "north elevation" is the facade you see standing
north of the house. The drawing's own axes are then

    ``u`` (horizontal, screen-right) = ``ẑ × n̂``
    ``z`` (vertical, screen-up)      = height above the plot datum

so standing north (``n̂ = +Y``) puts east on your **left** (``u = -X``), which is what a
correctly drawn north elevation shows. The four cases are tabulated in
:data:`U_AXES`; there is no trigonometry anywhere, and ``u``/``z`` are integer
millimetres like everything else in this repo.

``z`` is absolute — measured from the plot datum ``0``, so the number in a level marker
*is* the model's level value and nothing has to be reconstructed. ``u`` is shifted once,
by :func:`u_origin_of`, so the leftmost point of the building sits at ``u = 0``; the shift
is a single subtraction applied to every primitive in the drawing, which keeps golden
files readable and lets the sheet composer place the drawing without first hunting for
negative coordinates.

Sizes come from the two modules that already own them
----------------------------------------------------
Nothing here invents a size. §7 warns that confusing model millimetres with paper
millimetres "silently ruins every sheet", and the drawings engine already has one owner
for each kind:

:class:`~services.drawings.projection.style.Style`
    paper-relative sizes for drawn things — text heights, the level-marker tick, label
    gaps. The projection package's own docstring names elevations and sections as
    consumers of it.
:class:`~services.drawings.autodim.config.AutoDimConfig`
    the numbers that have to agree with the DXF DIMSTYLE and with the plan's chains: tick
    size, witness offset and extension, dimension text height, and §7's chain offsets (L1
    2400, L2 1800). A height chain drawn with different ticks from the plan chains on the
    facing sheet looks like two drawings by two people.

:class:`VerticalStyle` resolves both for one scale and is the only sizing object threaded
through this package. Sizes that are *real* — a 60mm window frame, a 115mm parapet, the
900mm foundation depth — stay plain model millimetres and never scale, because a window
frame does not get thicker when you print at 1:50.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from services.drawings.autodim.config import DEFAULT_CONFIG, AutoDimConfig
from services.drawings.dimensions import DimChain, DimSegment, LabelBox, assert_chains_sum
from services.drawings.layers import A_DIM, A_TEXT
from services.drawings.projection.primitives import (
    Line,
    Point,
    Polyline,
    Primitive,
    Text,
    bbox_of,
    count_by_kind,
    count_by_layer,
    primitives_to_json,
    sanitise_text,
)
from services.drawings.projection.style import Style, style_of

__all__ = [
    "DIRECTIONS_4",
    "DIRECTION_NAMES",
    "K_FOUNDATION_LABEL",
    "K_FOUNDATION_LINE",
    "K_HEIGHT_CHAIN",
    "K_HEIGHT_CHAIN_TEXT",
    "K_HEIGHT_CHAIN_TICK",
    "K_LEVEL_JOG",
    "K_MATERIAL_CALLOUT",
    "K_MATERIAL_LEADER",
    "K_MUMTY",
    "K_OPENING_FRAME",
    "K_PARAPET",
    "K_PLINTH",
    "K_SILHOUETTE",
    "K_SLAB_EDGE",
    "K_STAIR_PROFILE",
    "K_TITLE",
    "GROUND_OVERRUN_PAPER_MM",
    "LEVEL_TEXT_PITCH_FACTOR",
    "NORMALS",
    "PROBE_MM",
    "TITLE_PAPER_MM",
    "U_AXES",
    "Interval",
    "LevelMarker",
    "LevelSet",
    "StoreyLevels",
    "VerticalDrawing",
    "VerticalStyle",
    "build_levels",
    "contains",
    "depth_of",
    "format_level_mm",
    "half_lo",
    "height_chain",
    "height_chain_primitives",
    "level_marker_primitives",
    "merge_intervals",
    "normals_of",
    "point_in_ring",
    "rect_ring",
    "ring_line_intervals",
    "subtract_intervals",
    "u_of",
    "u_origin_of",
]

#: The four orthogonal projection planes. MVP is orthogonal-only (§7 step 1).
DIRECTIONS_4: tuple[str, ...] = ("N", "E", "S", "W")

#: Outward normal ``n̂`` per direction, in plot-local model space (+X east, +Y north).
NORMALS: dict[str, tuple[int, int]] = {
    "N": (0, 1),
    "E": (1, 0),
    "S": (0, -1),
    "W": (-1, 0),
}

#: Horizontal drawing axis ``u = ẑ × n̂`` per direction. Tabulated rather than computed
#: so the one thing that is easy to get backwards is visible and reviewable.
U_AXES: dict[str, tuple[int, int]] = {
    "N": (-1, 0),
    "E": (0, 1),
    "S": (1, 0),
    "W": (0, -1),
}

#: Human names for the sheet title.
DIRECTION_NAMES: dict[str, str] = {
    "N": "NORTH",
    "E": "EAST",
    "S": "SOUTH",
    "W": "WEST",
}

# ---------------------------------------------------------------------------
# Semantic kinds for the vertical drawings.
#
# ``kind`` is a hint, never a contract (see primitives.py) — a renderer must not make a
# geometry decision from it. These extend the vocabulary in
# :mod:`services.drawings.projection.primitives`, which owns the shared names; they are
# declared here because that module is not this workflow's to edit. Hoisting them into
# it later is a pure move.
# ---------------------------------------------------------------------------
K_SILHOUETTE = "elevation-silhouette"
K_SLAB_EDGE = "slab-edge"
K_PLINTH = "plinth"
K_PARAPET = "parapet"
K_MUMTY = "mumty"
K_OPENING_FRAME = "opening-frame"
K_STAIR_PROFILE = "stair-profile"
K_HEIGHT_CHAIN = "height-chain"
K_HEIGHT_CHAIN_TICK = "height-chain-tick"
K_HEIGHT_CHAIN_TEXT = "height-chain-text"
K_LEVEL_JOG = "level-label-jog"
K_MATERIAL_LEADER = "material-leader"
K_MATERIAL_CALLOUT = "material-callout"
K_FOUNDATION_LINE = "foundation-line"
K_FOUNDATION_LABEL = "foundation-label"
K_TITLE = "drawing-title"

# ---------------------------------------------------------------------------
# Paper-relative sizes for the things only a vertical drawing has. Everything else
# comes from Style / AutoDimConfig — see the module docstring.
# ---------------------------------------------------------------------------
#: Drawing title under the projection. §7 sheets title their sections and elevations at
#: the same size the plan titles its section marks (``style.TEXT_SECTION_MM``); this is
#: the one size a vertical drawing needs that Style has no name for.
TITLE_PAPER_MM = 5.0
#: How far the ground/NGL line runs past the building each side.
GROUND_OVERRUN_PAPER_MM = 12.0
#: Minimum vertical pitch between two level texts, as a multiple of text height.
LEVEL_TEXT_PITCH_FACTOR = 2


@dataclass(frozen=True)
class VerticalStyle:
    """Every size a vertical drawing needs, resolved for one scale, from two owners.

    ``style`` answers "how big is this on paper"; ``dim`` answers "what does a dimension
    look like here", and its numbers mirror the DXF DIMSTYLE
    (:func:`services.drawings.dxf.setup_dimstyle`) so a CAD viewer re-rendering a
    dimension from the style lands on top of our primitives rather than beside them.
    """

    style: Style
    dim: AutoDimConfig

    @classmethod
    def of(cls, scale: Any = 100) -> VerticalStyle:
        """Build from a ``Scale``, a :class:`Style`, or a bare denominator."""
        resolved = style_of(scale)
        return cls(
            style=resolved,
            dim=replace(DEFAULT_CONFIG, scale_denominator=resolved.scale_denominator),
        )

    @property
    def scale_denominator(self) -> int:
        return self.style.scale_denominator

    # -- text ------------------------------------------------------------
    @property
    def dim_text_mm(self) -> int:
        """Dimension and level text: the DIMSTYLE's 2.5mm ISO body size."""
        return self.dim.text_height_mm(0)

    @property
    def tag_text_mm(self) -> int:
        return self.style.tag_height_mm

    @property
    def title_text_mm(self) -> int:
        return self.style.paper_to_model_mm(TITLE_PAPER_MM)

    # -- marks -----------------------------------------------------------
    @property
    def level_tick_mm(self) -> int:
        return max(1, self.style.level_marker_size_mm)

    @property
    def level_text_gap_mm(self) -> int:
        return max(1, self.style.level_label_gap_mm)

    @property
    def dim_tick_mm(self) -> int:
        return max(1, self.dim.tick_size_mm())

    @property
    def dim_text_gap_mm(self) -> int:
        return max(1, self.dim.text_gap_mm())

    @property
    def witness_extend_mm(self) -> int:
        return self.dim.witness_extend_mm()

    # -- §7 offsets, shared with the plan's chains ------------------------
    @property
    def chain_offset_mm(self) -> int:
        """§7's level-1 offset (2400): where the overall height chain sits."""
        return self.dim.offset_for_level(1)

    @property
    def level_marker_offset_mm(self) -> int:
        """§7's level-2 offset (1800): how far the level markers stand off the facade."""
        return self.dim.offset_for_level(2)

    @property
    def ground_overrun_mm(self) -> int:
        return self.style.paper_to_model_mm(GROUND_OVERRUN_PAPER_MM)


# ---------------------------------------------------------------------------
# Axes
# ---------------------------------------------------------------------------
def normals_of(direction: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """``(n̂, û)`` for a direction, validated."""
    try:
        return NORMALS[direction], U_AXES[direction]
    except KeyError:
        raise ValueError(
            "%r is not one of the four elevation directions (%s)."
            % (direction, ", ".join(DIRECTIONS_4))
        ) from None


def u_of(x: int, y: int, u_axis: tuple[int, int]) -> int:
    """Horizontal drawing coordinate of a model point (before the origin shift)."""
    return x * u_axis[0] + y * u_axis[1]


def depth_of(x: int, y: int, normal: tuple[int, int]) -> int:
    """Depth toward the viewer. Larger is **nearer** — the hidden-line ordering key."""
    return x * normal[0] + y * normal[1]


def u_origin_of(points: Iterable[tuple[int, int]], u_axis: tuple[int, int]) -> int:
    """The single ``u`` shift for a drawing: the leftmost model point becomes ``u = 0``.

    Computed from the building's whole extent — never per element, or two primitives in
    one drawing would land in two different coordinate systems.
    """
    values = [u_of(x, y, u_axis) for x, y in points]
    return min(values) if values else 0


# ---------------------------------------------------------------------------
# Levels — read straight off the model's first-class level fields
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StoreyLevels:
    """One storey's levels, all absolute above the plot datum except where named."""

    storey_id: str
    name: str
    index: int
    ffl_mm: int
    height_mm: int
    slab_thickness_mm: int
    sill_above_ffl_mm: int
    lintel_above_ffl_mm: int

    @property
    def sill_mm(self) -> int:
        return self.ffl_mm + self.sill_above_ffl_mm

    @property
    def lintel_mm(self) -> int:
        return self.ffl_mm + self.lintel_above_ffl_mm

    @property
    def top_mm(self) -> int:
        """Top of this storey = the FFL of the storey above / the terrace."""
        return self.ffl_mm + self.height_mm


@dataclass(frozen=True)
class LevelSet:
    """Every level a vertical drawing needs, derived from ``house.levels`` + storeys.

    Nothing in here is invented: ``plinth_mm``, ``ffl_per_storey_mm``, ``sill_default_mm``,
    ``lintel_default_mm`` and ``parapet_mm`` are first-class model fields
    (``garh_model.model.Levels``), per-storey sill/lintel overrides come from
    ``Storey.level``, and the two derived numbers (:attr:`terrace_mm`,
    :attr:`parapet_top_mm`) are sums of those.
    """

    plinth_mm: int
    parapet_height_mm: int
    storeys: tuple[StoreyLevels, ...]
    datum_mm: int = 0

    @property
    def terrace_mm(self) -> int:
        """Top of the topmost storey — the terrace slab level."""
        return self.storeys[-1].top_mm if self.storeys else self.plinth_mm

    @property
    def parapet_top_mm(self) -> int:
        return self.terrace_mm + self.parapet_height_mm

    def storey(self, storey_id: str) -> StoreyLevels | None:
        for item in self.storeys:
            if item.storey_id == storey_id:
                return item
        return None

    def markers(self) -> tuple[LevelMarker, ...]:
        """Every §7 level marker, merged by value and sorted bottom-up.

        §7 is explicit that an elevation carries *markers*, not chains: "plinth/FFL/
        lintel/parapet levels as level markers, not chains". Two levels that coincide —
        the ground FFL and the plinth top always do — merge into one marker carrying both
        labels, because drawing two ticks at the same height is how a sheet starts
        looking careless.
        """
        raw: list[tuple[int, str]] = [(self.datum_mm, "GROUND LVL (NGL)")]
        for storey in self.storeys:
            name = storey.name.upper() if storey.name else "STOREY %d" % (storey.index + 1)
            raw.append((storey.ffl_mm, "%s FFL" % name))
            raw.append((storey.sill_mm, "SILL %s" % name))
            raw.append((storey.lintel_mm, "LINTEL %s" % name))
        raw.append((self.plinth_mm, "PLINTH LVL"))
        raw.append((self.terrace_mm, "TERRACE LVL"))
        raw.append((self.parapet_top_mm, "PARAPET TOP"))

        merged: dict[int, list[str]] = {}
        for level_mm, label in raw:
            merged.setdefault(level_mm, [])
            if label not in merged[level_mm]:
                merged[level_mm].append(label)
        return tuple(
            LevelMarker(level_mm=level_mm, labels=tuple(labels))
            for level_mm, labels in sorted(merged.items())
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "datumMm": self.datum_mm,
            "plinthMm": self.plinth_mm,
            "parapetMm": self.parapet_height_mm,
            "terraceMm": self.terrace_mm,
            "parapetTopMm": self.parapet_top_mm,
            "storeys": [
                {
                    "storeyId": s.storey_id,
                    "name": s.name,
                    "fflMm": s.ffl_mm,
                    "heightMm": s.height_mm,
                    "sillMm": s.sill_mm,
                    "lintelMm": s.lintel_mm,
                }
                for s in self.storeys
            ],
        }


@dataclass(frozen=True)
class LevelMarker:
    """A level marker: one height, one or more labels, in millimetres."""

    level_mm: int
    labels: tuple[str, ...]

    def text(self) -> str:
        """§7: dim text is millimetres regardless of the project's display units."""
        return "%s %s" % (format_level_mm(self.level_mm), " / ".join(self.labels))

    def to_json(self) -> dict[str, Any]:
        return {"levelMm": self.level_mm, "labels": list(self.labels), "text": self.text()}


def format_level_mm(level_mm: int) -> str:
    """``+3600`` / ``-300`` / ``±0`` — the drawing convention, in whole millimetres."""
    if level_mm == 0:
        return "±0"
    return "%+d" % level_mm


def build_levels(house: Any) -> LevelSet:
    """Read a :class:`~garh_model.model.HouseModel`'s levels. Duck-typed on purpose.

    The model core is *not* imported: this module runs on a bare interpreter, and the
    only thing it needs from a house is attribute access. ``tests/test_elevations.py``
    folds a real op log through ``garh_model`` and checks the numbers agree field by
    field, which is the assertion that keeps the duck typing honest.

    Per-storey ``sillDefaultMm``/``lintelDefaultMm`` overrides fall back to the
    building-wide defaults — the same coalesce as
    ``garh_model.model.effective_sill_mm``/``effective_lintel_mm``, cross-checked in the
    tests rather than imported.
    """
    levels = house.levels
    ffls = tuple(int(v) for v in getattr(levels, "ffl_per_storey_mm", ()) or ())
    storeys: list[StoreyLevels] = []
    for index, storey in enumerate(house.storeys):
        level = storey.level
        ffl_mm = ffls[index] if index < len(ffls) else int(level.ffl_mm)
        sill = level.sill_default_mm
        lintel = level.lintel_default_mm
        storeys.append(
            StoreyLevels(
                storey_id=str(storey.id),
                name=str(storey.name),
                index=index,
                ffl_mm=int(ffl_mm),
                height_mm=int(storey.height_mm),
                slab_thickness_mm=int(level.slab_thickness_mm),
                sill_above_ffl_mm=int(levels.sill_default_mm if sill is None else sill),
                lintel_above_ffl_mm=int(levels.lintel_default_mm if lintel is None else lintel),
            )
        )
    return LevelSet(
        plinth_mm=int(levels.plinth_mm),
        parapet_height_mm=int(levels.parapet_mm),
        storeys=tuple(storeys),
    )


# ---------------------------------------------------------------------------
# The one dimension chain a vertical drawing carries
# ---------------------------------------------------------------------------
def height_chain(
    levels: LevelSet,
    *,
    chain_id: str,
    offset_mm: int,
    storey_id: str | None = None,
) -> DimChain:
    """The single overall height chain: plinth + every storey height + parapet.

    §7 gives an elevation exactly one chain ("overall height chain") on top of its level
    markers, and §7 step 5 makes the invariant non-negotiable: **Σ segments == overall,
    exactly**. That is checked here, at construction, by
    :func:`~services.drawings.dimensions.assert_chains_sum` — not left to a test to
    notice later — because the numbers are integer millimetres precisely so this can be
    an equality.
    """
    segments: list[DimSegment] = []
    cursor = levels.datum_mm
    if levels.plinth_mm != levels.datum_mm:
        segments.append(
            DimSegment(start_mm=cursor, length_mm=levels.plinth_mm - cursor, label_override=None)
        )
        cursor = levels.plinth_mm
    for storey in levels.storeys:
        segments.append(
            DimSegment(
                start_mm=cursor,
                length_mm=storey.height_mm,
                anchor_element_id=storey.storey_id,
            )
        )
        cursor += storey.height_mm
    if levels.parapet_height_mm > 0:
        segments.append(DimSegment(start_mm=cursor, length_mm=levels.parapet_height_mm))
        cursor += levels.parapet_height_mm

    chain = DimChain(
        id=chain_id,
        orientation="vertical",
        level=1,
        offset_mm=offset_mm,
        origin_mm=levels.datum_mm,
        segments=tuple(segments),
        overall_mm=cursor - levels.datum_mm,
        storey_id=storey_id,
    )
    assert_chains_sum([chain])
    return chain


def height_chain_primitives(
    chain: DimChain,
    *,
    sizes: VerticalStyle,
    witness_from_u_mm: int,
) -> tuple[Primitive, ...]:
    """Draw a vertical chain: dimension line, oblique ticks, witness lines, segment texts.

    The :class:`~services.drawings.dimensions.DimChain` is the data a golden file pins;
    these are the marks a renderer puts on paper. Both are returned by a projection so the
    sheet is complete without a second pass and the numbers stay machine readable.

    The mark geometry deliberately mirrors
    :func:`services.drawings.autodim.render.chain_primitives` — same tick, same witness
    offsets, same text gap — so the plan's chains and this one look like one drawing set.
    That package renders into its own primitive vocabulary for the plan's placement
    engine, which is why this is a sibling implementation rather than a call into it; the
    numbers are shared, and they are shared through
    :class:`~services.drawings.autodim.config.AutoDimConfig` rather than by being copied.
    """
    out: list[Primitive] = []
    height = sizes.dim_text_mm
    tick = max(1, sizes.dim_tick_mm // 2)
    u = chain.offset_mm
    lo = chain.origin_mm
    hi = chain.origin_mm + chain.overall_mm
    out.append(Line(A_DIM, (u, lo), (u, hi), kind=K_HEIGHT_CHAIN, owner_id=chain.id))
    breakpoints: list[int] = [lo]
    cursor = lo
    for segment in chain.segments:
        cursor += segment.length_mm
        breakpoints.append(cursor)
    for z in breakpoints:
        out.append(
            Line(
                A_DIM,
                (u - tick, z - tick),
                (u + tick, z + tick),
                kind=K_HEIGHT_CHAIN_TICK,
                owner_id=chain.id,
            )
        )
        out.append(
            Line(
                A_DIM,
                (witness_from_u_mm, z),
                (u + sizes.witness_extend_mm, z),
                dashed=True,
                kind=K_HEIGHT_CHAIN_TICK,
                owner_id=chain.id,
            )
        )
    cursor = lo
    for segment in chain.segments:
        mid = cursor + segment.length_mm // 2
        out.append(
            Text(
                A_DIM,
                (u + sizes.dim_text_gap_mm, mid),
                sanitise_text(segment.label()),
                height,
                rotation_deg=90,
                h_align="center",
                v_align="bottom",
                owner_id=segment.anchor_element_id,
                kind=K_HEIGHT_CHAIN_TEXT,
            )
        )
        cursor += segment.length_mm
    return tuple(out)


# ---------------------------------------------------------------------------
# Level markers as primitives
# ---------------------------------------------------------------------------
def level_marker_primitives(
    markers: Sequence[LevelMarker],
    *,
    u_left_mm: int,
    sizes: VerticalStyle,
) -> tuple[Primitive, ...]:
    """Marker line + filled tick + text, stacked left of the building, collision-free.

    The text is nudged upward when two levels are closer together than a label is tall,
    and a short jog line keeps it visibly attached to its own tick. That is §7 step 4's
    rule ("never overlap; leader as last resort") applied to the one label column this
    drawing owns — :func:`~services.drawings.dimensions.find_label_collisions` is the
    assertion, and the tests run it.
    """
    out: list[Primitive] = []
    height = sizes.dim_text_mm
    stick = sizes.level_marker_offset_mm
    gap = sizes.level_text_gap_mm
    tick = sizes.level_tick_mm
    pitch = height * LEVEL_TEXT_PITCH_FACTOR
    text_u = u_left_mm - stick - gap
    last_text_z: int | None = None
    for marker in sorted(markers, key=lambda m: m.level_mm):
        z = marker.level_mm
        out.append(
            Line(
                A_DIM,
                (u_left_mm - stick, z),
                (u_left_mm, z),
                kind="level-marker",
                owner_id=None,
            )
        )
        # The level tick: a small open triangle sitting on the level line.
        out.append(
            Polyline(
                A_DIM,
                (
                    (u_left_mm - stick + tick, z),
                    (u_left_mm - stick, z + tick),
                    (u_left_mm - stick, z - tick),
                ),
                closed=True,
                kind="level-marker",
            )
        )
        text_z = z if last_text_z is None else max(z, last_text_z + pitch)
        if text_z != z:
            out.append(
                Line(
                    A_DIM,
                    (u_left_mm - stick, z),
                    (u_left_mm - stick - gap // 2, text_z),
                    kind=K_LEVEL_JOG,
                )
            )
        out.append(
            Text(
                A_TEXT,
                (text_u, text_z),
                sanitise_text(marker.text()),
                height,
                h_align="right",
                v_align="middle",
                kind="level-label",
            )
        )
        last_text_z = text_z
    return tuple(out)


# ---------------------------------------------------------------------------
# Interval arithmetic — the whole of the hidden-line and cut-extent maths
# ---------------------------------------------------------------------------
#: A closed ``[lo, hi]`` span in drawing ``u`` (or in ``z``), integer millimetres.
Interval = tuple[int, int]


def merge_intervals(intervals: Sequence[Interval]) -> tuple[Interval, ...]:
    """Sort and union touching/overlapping spans. Degenerate spans are dropped."""
    clean = sorted((lo, hi) for lo, hi in intervals if hi > lo)
    out: list[Interval] = []
    for lo, hi in clean:
        if out and lo <= out[-1][1]:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return tuple(out)


def subtract_intervals(base: Sequence[Interval], holes: Sequence[Interval]) -> tuple[Interval, ...]:
    """``base`` minus ``holes`` — how a stair well is taken out of a cut slab."""
    result = list(merge_intervals(base))
    for h_lo, h_hi in merge_intervals(holes):
        next_result: list[Interval] = []
        for lo, hi in result:
            if h_hi <= lo or h_lo >= hi:
                next_result.append((lo, hi))
                continue
            if lo < h_lo:
                next_result.append((lo, h_lo))
            if h_hi < hi:
                next_result.append((h_hi, hi))
        result = next_result
    return merge_intervals(result)


def contains(outer: Interval, inner: Interval) -> bool:
    return outer[0] <= inner[0] and outer[1] >= inner[1]


def point_in_ring(ring: Sequence[tuple[int, int]], x: int, y: int) -> bool:
    """Even-odd ray cast, integer arithmetic, for a closed ring without a repeated end.

    Deliberately a local implementation rather than an import of
    ``garh_model.geometry.polygon_contains``: this module has no dependencies by design
    (see the module docstring), and the same trade-off is made — with the same mitigation
    — in :func:`services.drawings.projection.primitives.round_half_away`. The mitigation
    is a test: ``tests/test_elevations.py`` asserts this function agrees with the model
    core's on the fixture's footprints, so the copy cannot drift in silence.

    A point exactly on an edge is not defined either way. Callers probe from a point
    offset by :data:`PROBE_MM` from any face, so it never comes up in practice.
    """
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            # Crossing number test written as a cross-product comparison so it stays in
            # integers: no division, therefore no rounding to get wrong.
            side = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if (side > 0) == (y2 > y1):
                inside = not inside
    return inside


def ring_line_intervals(
    ring: Sequence[tuple[int, int]],
    *,
    axis: str,
    position_mm: int,
    u_axis: tuple[int, int],
) -> tuple[Interval, ...]:
    """Where an axis-aligned cut line crosses a ring, as spans in drawing ``u``.

    ``axis="x"`` is a cut plane at constant ``x`` (the line runs along ``+Y``);
    ``axis="y"`` is the transpose. Only edges that actually straddle the line contribute,
    and because MVP geometry is orthogonal every straddling edge is perpendicular to the
    line and therefore contributes an exact integer coordinate. A non-orthogonal edge
    would need a division: it is reported by raising, rather than rounded quietly, since
    §7's MVP is orthogonal-only and a slanted wall here means an upstream bug.
    """
    if axis not in ("x", "y"):
        raise ValueError("axis must be 'x' or 'y', got %r" % (axis,))
    crossings: list[int] = []
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if axis == "x":
            near, far, other_a, other_b = x1, x2, y1, y2
        else:
            near, far, other_a, other_b = y1, y2, x1, x2
        lo, hi = (near, far) if near <= far else (far, near)
        if not (lo <= position_mm < hi):
            continue
        if other_a != other_b:
            raise ValueError(
                "ring edge (%d,%d)-(%d,%d) is not axis-aligned; §7's MVP projection is "
                "orthogonal-only." % (x1, y1, x2, y2)
            )
        crossings.append(other_a)
    crossings.sort()
    spans: list[Interval] = []
    for index in range(0, len(crossings) - 1, 2):
        a, b = crossings[index], crossings[index + 1]
        if axis == "x":
            ua, ub = u_of(position_mm, a, u_axis), u_of(position_mm, b, u_axis)
        else:
            ua, ub = u_of(a, position_mm, u_axis), u_of(b, position_mm, u_axis)
        spans.append((min(ua, ub), max(ua, ub)))
    return merge_intervals(spans)


# ---------------------------------------------------------------------------
# The drawing a projector returns
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VerticalDrawing:
    """One elevation or one section: primitives plus the numbers behind them.

    A renderer needs only :attr:`primitives`. Everything else is what makes the drawing
    *checkable*: :attr:`level_markers` and :attr:`chains` are the numbers a golden file
    pins and a reviewer audits, and :attr:`notes` is where a projection says out loud
    what it could not draw — the honest half of "draw what the model actually carries".
    """

    kind: str
    name: str
    direction: str
    primitives: tuple[Primitive, ...]
    level_markers: tuple[LevelMarker, ...]
    chains: tuple[DimChain, ...]
    levels: LevelSet
    notes: tuple[str, ...] = ()
    #: The scale the paper-relative sizes were resolved at (1:``scale_denominator``).
    scale_denominator: int = 100

    #: Advance width per character as a fraction of text height, for
    #: :meth:`label_boxes`. Mirrors ``autodim.config.CHAR_ADVANCE_NUM/DEN`` so the two
    #: collision checks in the drawings engine bound text the same way.
    CHAR_ADVANCE_NUM: ClassVar[int] = 7
    CHAR_ADVANCE_DEN: ClassVar[int] = 10

    def extent_mm(self) -> tuple[int, int, int, int] | None:
        return bbox_of(self.primitives)

    def counts(self) -> dict[str, int]:
        return {
            "primitives": len(self.primitives),
            "levelMarkers": len(self.level_markers),
            "chains": len(self.chains),
            "notes": len(self.notes),
        }

    def label_boxes(self) -> tuple[LabelBox, ...]:
        """Text bounding boxes for §16's collision assertion.

        Width is the same monospaced-digit bound ``autodim.config.text_width_mm`` uses
        (0.7 × height per character). An estimate is the right tool here: the assertion it
        feeds asks whether two labels are in the same place, not how wide a glyph is, and
        it must never be *narrower* than the truth.
        """
        boxes: list[LabelBox] = []
        for index, item in enumerate(self.primitives):
            if not isinstance(item, Text):
                continue
            width = max(
                1,
                (len(item.text) * item.height_mm * self.CHAR_ADVANCE_NUM) // self.CHAR_ADVANCE_DEN,
            )
            height = item.height_mm
            x, y = item.position
            if item.rotation_deg % 180 == 90:
                width, height = height, width
            if item.h_align == "center":
                x -= width // 2
            elif item.h_align == "right":
                x -= width
            if item.v_align == "middle":
                y -= height // 2
            elif item.v_align == "top":
                y -= height
            boxes.append(
                LabelBox(
                    x_mm=x,
                    y_mm=y,
                    width_mm=width,
                    height_mm=height,
                    owner_id="%s#%d:%s" % (item.kind or "text", index, item.text[:24]),
                )
            )
        return tuple(boxes)

    def to_json(self) -> dict[str, Any]:
        extent = self.extent_mm()
        return {
            "kind": self.kind,
            "name": self.name,
            "direction": self.direction,
            "scale": self.scale_denominator,
            "extentMm": list(extent) if extent else None,
            "levels": self.levels.to_json(),
            "levelMarkers": [m.to_json() for m in self.level_markers],
            "chains": [c.to_json() for c in self.chains],
            "notes": list(self.notes),
            "primitives": primitives_to_json(self.primitives),
        }

    def by_layer(self) -> dict[str, int]:
        return count_by_layer(self.primitives)

    def by_kind(self) -> dict[str, int]:
        return count_by_kind(self.primitives)

    def with_primitives(self, primitives: Sequence[Primitive]) -> VerticalDrawing:
        return replace(self, primitives=tuple(primitives))


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
#: How far past a wall face a probe point sits when deciding which side is outdoors.
PROBE_MM = 10


def rect_ring(u_lo: int, z_lo: int, u_hi: int, z_hi: int) -> tuple[Point, ...]:
    """A closed CCW ring for a ``(u, z)`` rectangle, no repeated last vertex."""
    return ((u_lo, z_lo), (u_hi, z_lo), (u_hi, z_hi), (u_lo, z_hi))


def half_lo(thickness_mm: int) -> int:
    """Offset from a centreline to the near face. ``lo + thickness`` is the far face.

    Split this way — ``lo = c - t // 2``, ``hi = lo + t`` — an odd thickness (115mm brick)
    still spans *exactly* its thickness. Rounding both ends independently is how a
    115mm wall becomes 114 or 116 in a drawing, and how a dimension chain stops summing.
    """
    return thickness_mm // 2
