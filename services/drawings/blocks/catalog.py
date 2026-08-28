"""The registry: every block in this library, by name, with a valid sample call.

Why a registry rather than "import the function you want". Two reasons, and neither is
convenience:

1. **Nothing may be in the library without being in the list.** This repository has
   already shipped a layer that tagged its meshes for hit-testing, documented itself as
   integrated, and never called the registry — every placed item was invisible to
   clicks, and nothing could have caught it at compile time.
   ``test_blocks.py::test_every_public_block_is_registered`` walks each module's
   ``__all__`` and fails on a block that exists but is not here, so the list cannot
   quietly fall behind the code.
2. **A generic test can then exercise all of them.** ``sample`` is a *valid* parameter
   set for each block, so one test can build every block in the library and assert the
   properties that must hold for all of them — integer coordinates, an ``element_id`` on
   every primitive, and no primitive on a layer the block did not declare.

``layers`` is a declaration of what a block is allowed to draw on. A door on A-WALL
would be measured as a wall by a reviewer taking setbacks off the DXF; a bathtub on
A-DOOR would vanish when they froze the door layer. Both are real defects on a
submission drawing, and this is where they are caught.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from services.drawings.blocks import doors, electrical, sanitary, site, stairs, windows
from services.drawings.blocks.base import Insertion
from services.drawings.layers import (
    A_DIM,
    A_DOOR,
    A_STAIR,
    A_TEXT,
    A_WALL_PART,
    A_WIND,
    layer_for,
)
from services.drawings.render.primitives import Primitive

__all__ = ["BLOCK_NAMES", "BLOCK_REGISTRY", "BlockSpec", "block_spec", "build_block"]


@dataclass(frozen=True)
class BlockSpec:
    """One block: how to call it, what it is called, and where it may draw."""

    name: str
    category: str
    title: str
    factory: Callable[..., tuple[Primitive, ...]]
    #: A representative, valid parameter set — never ``element_id`` or ``insertion``.
    sample: Mapping[str, object] = field(default_factory=dict)
    #: The §7 layers this block may put primitives on. Checked, not documented.
    layers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for layer in self.layers:
            layer_for(layer)
        if not self.layers:
            raise ValueError("block %r must declare the layers it draws on" % self.name)

    def build(
        self,
        *,
        element_id: str,
        insertion: Insertion = Insertion(),
        **overrides: object,
    ) -> tuple[Primitive, ...]:
        """Build this block from its sample parameters, with any overrides applied."""
        params = dict(self.sample)
        params.update(overrides)
        return self.factory(element_id=element_id, insertion=insertion, **params)


_SPECS: tuple[BlockSpec, ...] = (
    # -- doors ----------------------------------------------------------
    BlockSpec(
        name="door-single-swing",
        category="door",
        title="Single-leaf swing door",
        factory=doors.door_single_swing,
        sample={"leaf_width_mm": 900, "wall_thickness_mm": 230},
        layers=(A_DOOR,),
    ),
    BlockSpec(
        name="door-double-swing",
        category="door",
        title="Double-leaf swing door",
        factory=doors.door_double_swing,
        sample={"leaf_width_mm": 1500, "wall_thickness_mm": 230},
        layers=(A_DOOR,),
    ),
    BlockSpec(
        name="door-sliding",
        category="door",
        title="Sliding door",
        factory=doors.door_sliding,
        sample={"leaf_width_mm": 1200, "wall_thickness_mm": 115},
        layers=(A_DOOR,),
    ),
    BlockSpec(
        name="door-folding",
        category="door",
        title="Folding (bi-fold) door",
        factory=doors.door_folding,
        sample={"leaf_width_mm": 1800, "wall_thickness_mm": 115, "panels": 4},
        layers=(A_DOOR,),
    ),
    # -- windows --------------------------------------------------------
    BlockSpec(
        name="window-casement",
        category="window",
        title="Casement window",
        factory=windows.window_casement,
        sample={"width_mm": 1200, "wall_thickness_mm": 230, "leaves": 2},
        layers=(A_WIND, A_WALL_PART),
    ),
    BlockSpec(
        name="window-sliding",
        category="window",
        title="Sliding window",
        factory=windows.window_sliding,
        sample={"width_mm": 1500, "wall_thickness_mm": 230},
        layers=(A_WIND, A_WALL_PART),
    ),
    BlockSpec(
        name="window-fixed",
        category="window",
        title="Fixed light",
        factory=windows.window_fixed,
        sample={"width_mm": 900, "wall_thickness_mm": 230},
        layers=(A_WIND, A_WALL_PART),
    ),
    BlockSpec(
        name="window-ventilator",
        category="window",
        title="Ventilator",
        factory=windows.window_ventilator,
        sample={"width_mm": 600, "wall_thickness_mm": 115},
        layers=(A_WIND, A_WALL_PART),
    ),
    # -- stairs ---------------------------------------------------------
    BlockSpec(
        name="stair-straight",
        category="stair",
        title="Straight flight",
        factory=stairs.stair_straight,
        sample={
            "tread_mm": 280,
            "riser_count": 16,
            "width_mm": 1000,
            "break_after_treads": 8,
        },
        layers=(A_STAIR, A_TEXT),
    ),
    BlockSpec(
        name="stair-dogleg",
        category="stair",
        title="Dog-leg stair",
        factory=stairs.stair_dogleg,
        sample={
            "tread_mm": 280,
            "riser_count": 18,
            "width_mm": 1000,
            "landing_depth_mm": 1000,
            "well_mm": 100,
        },
        layers=(A_STAIR, A_TEXT),
    ),
    BlockSpec(
        name="stair-spiral",
        category="stair",
        title="Spiral stair",
        factory=stairs.stair_spiral,
        sample={"outer_radius_mm": 750, "inner_radius_mm": 100, "riser_count": 13},
        layers=(A_STAIR, A_TEXT),
    ),
    # -- sanitary -------------------------------------------------------
    BlockSpec(
        name="sanitary-wc",
        category="sanitary",
        title="WC",
        factory=sanitary.wc,
        sample={},
        layers=(A_WALL_PART,),
    ),
    BlockSpec(
        name="sanitary-washbasin",
        category="sanitary",
        title="Washbasin",
        factory=sanitary.washbasin,
        sample={},
        layers=(A_WALL_PART,),
    ),
    BlockSpec(
        name="sanitary-shower",
        category="sanitary",
        title="Shower",
        factory=sanitary.shower,
        sample={},
        layers=(A_WALL_PART,),
    ),
    BlockSpec(
        name="sanitary-bathtub",
        category="sanitary",
        title="Bathtub",
        factory=sanitary.bathtub,
        sample={},
        layers=(A_WALL_PART,),
    ),
    BlockSpec(
        name="sanitary-sink",
        category="sanitary",
        title="Sink",
        factory=sanitary.sink,
        sample={"bowls": 2, "width_mm": 1000, "depth_mm": 500},
        layers=(A_WALL_PART,),
    ),
    # -- electrical -----------------------------------------------------
    BlockSpec(
        name="electrical-switch",
        category="electrical",
        title="Switch",
        factory=electrical.switch,
        sample={"gang": 2, "two_way": True},
        layers=(A_TEXT,),
    ),
    BlockSpec(
        name="electrical-socket",
        category="electrical",
        title="Socket outlet",
        factory=electrical.socket,
        sample={"label": "16A"},
        layers=(A_TEXT,),
    ),
    BlockSpec(
        name="electrical-light-point",
        category="electrical",
        title="Light point",
        factory=electrical.light_point,
        sample={},
        layers=(A_TEXT,),
    ),
    BlockSpec(
        name="electrical-fan-point",
        category="electrical",
        title="Fan point",
        factory=electrical.fan_point,
        sample={},
        layers=(A_TEXT,),
    ),
    BlockSpec(
        name="electrical-db",
        category="electrical",
        title="Distribution board",
        factory=electrical.distribution_board,
        sample={"label": "DB-1"},
        layers=(A_TEXT,),
    ),
    # -- site -----------------------------------------------------------
    BlockSpec(
        name="site-north-arrow",
        category="site",
        title="North arrow",
        factory=site.north_arrow,
        sample={"north_deg": 30},
        layers=(A_TEXT,),
    ),
    BlockSpec(
        name="site-tree",
        category="site",
        title="Tree",
        factory=site.tree,
        sample={"canopy_radius_mm": 2000, "style": "cloud"},
        layers=(A_WALL_PART,),
    ),
    BlockSpec(
        name="site-parked-car",
        category="site",
        title="Parked car",
        factory=site.parked_car,
        sample={},
        layers=(A_WALL_PART,),
    ),
    BlockSpec(
        name="site-scale-bar",
        category="site",
        title="Scale bar",
        factory=site.scale_bar,
        sample={"division_mm": 1000, "divisions": 5},
        layers=(A_DIM, A_TEXT),
    ),
)

BLOCK_REGISTRY: Mapping[str, BlockSpec] = {spec.name: spec for spec in _SPECS}
BLOCK_NAMES: tuple[str, ...] = tuple(spec.name for spec in _SPECS)


def block_spec(name: str) -> BlockSpec:
    """Look up a block, failing loudly on a name that is not in the library."""
    try:
        return BLOCK_REGISTRY[name]
    except KeyError:
        raise KeyError(
            "%r is not a block in this library. Have: %s" % (name, ", ".join(BLOCK_NAMES))
        ) from None


def build_block(
    name: str,
    *,
    element_id: str,
    insertion: Insertion = Insertion(),
    **overrides: object,
) -> tuple[Primitive, ...]:
    """Build a named block. The entry point for anything driven by data rather than code."""
    return block_spec(name).build(element_id=element_id, insertion=insertion, **overrides)
