"""Drawings: the sheet model, layer convention and auto-dimensioning (playbook §7).

§7 is described in the product spec as the moat, and the parts of it that are pure data
or pure invariant are real here today:

=================================  ================================================
``layers``      the nine §7 DXF layers            **real**
``sheets``      sheet / frame / viewport / scale  **real**
``dxf``         mm units, layer table, DIMSTYLE   **real** (export is Phase 8)
``dimensions``  chain types + the sum invariant   **real** (the engine is Phase 8)
=================================  ================================================

The one rule worth restating: **dimension chains must sum exactly**. Not to a
tolerance — exactly. :func:`services.drawings.dimensions.assert_chains_sum` enforces it
and §16 runs it over the golden corpus, because a contractor builds from those numbers.

Layer names, paper sizes and DIMSTYLE values are contracts with software this project
does not control (AutoCAD, LibreCAD, the ODA viewer). Change them deliberately.
"""

from __future__ import annotations

from services.drawings.dimensions import (
    ChainConsistencyError,
    DimChain,
    DimSegment,
    LabelBox,
    assert_chains_sum,
    find_label_collisions,
)
from services.drawings.dxf import DIMSTYLE_NAME, new_document, setup_dimstyle, setup_layers
from services.drawings.layers import LAYER_NAMES, LAYERS, LayerSpec, layer_for
from services.drawings.sheets import (
    DEFAULT_SCALE,
    DEFAULT_SHEET_PLAN,
    PAPER_SIZES,
    SHEET_KINDS,
    AreaStatementRow,
    Frame,
    Scale,
    ScheduleRow,
    Sheet,
    SheetAnnotation,
    TitleBlock,
    Viewport,
    default_frame,
)

__all__ = [
    "DEFAULT_SCALE",
    "DEFAULT_SHEET_PLAN",
    "DIMSTYLE_NAME",
    "LAYERS",
    "LAYER_NAMES",
    "PAPER_SIZES",
    "SHEET_KINDS",
    "AreaStatementRow",
    "ChainConsistencyError",
    "DimChain",
    "DimSegment",
    "Frame",
    "LabelBox",
    "LayerSpec",
    "Scale",
    "ScheduleRow",
    "Sheet",
    "SheetAnnotation",
    "TitleBlock",
    "Viewport",
    "assert_chains_sum",
    "default_frame",
    "find_label_collisions",
    "layer_for",
    "new_document",
    "setup_dimstyle",
    "setup_layers",
]
