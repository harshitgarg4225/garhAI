"""DXF layer convention (playbook §7). **Fully implemented.**

    DXF (ezdxf, mm units, layers A-WALL, A-WALL-PART, A-DOOR, A-WIND, A-STAIR, A-DIM,
    A-TEXT, A-AREA, A-TITL).

These nine names are a hard contract with the outside world: a municipal reviewer opens
the DXF in AutoCAD or LibreCAD and expects AIA-style layers. Renaming one silently
breaks every downstream consumer, so they are constants, and :data:`LAYERS` is the
single ordered source the DXF writer, the SVG renderer and the golden tests all read.

Colours are AutoCAD Color Index (ACI) values, not RGB — that is what CAD software
expects, and ACI 7 in particular means "black on white paper, white on a dark screen",
which is why every printable line uses it rather than an explicit colour.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# -- the nine §7 layer names ------------------------------------------------
A_WALL = "A-WALL"
A_WALL_PART = "A-WALL-PART"
A_DOOR = "A-DOOR"
A_WIND = "A-WIND"
A_STAIR = "A-STAIR"
A_DIM = "A-DIM"
A_TEXT = "A-TEXT"
A_AREA = "A-AREA"
A_TITL = "A-TITL"

#: AutoCAD Color Index values used below.
_ACI_RED = 1
_ACI_YELLOW = 2
_ACI_GREEN = 3
_ACI_CYAN = 4
_ACI_BLUE = 5
_ACI_MAGENTA = 6
_ACI_BLACK_WHITE = 7
_ACI_GREY = 8


@dataclass(frozen=True)
class LayerSpec:
    """One layer: its name, colour, linetype, and what belongs on it."""

    name: str
    color: int
    linetype: str
    #: Lineweight in 1/100 mm, ezdxf's unit. -3 means "by default".
    lineweight: int
    description: str
    #: Layers that carry no built geometry are not printed on a submission sheet.
    plottable: bool = True


#: Ordered so DXF layer creation, the layer table in docs, and golden files all agree.
LAYERS: tuple[LayerSpec, ...] = (
    LayerSpec(A_WALL, _ACI_BLACK_WHITE, "CONTINUOUS", 50, "Full-height wall outlines"),
    LayerSpec(A_WALL_PART, _ACI_GREY, "CONTINUOUS", 25, "Partial-height walls, parapets, sills"),
    LayerSpec(A_DOOR, _ACI_GREEN, "CONTINUOUS", 25, "Door leaves and swing arcs"),
    LayerSpec(A_WIND, _ACI_CYAN, "CONTINUOUS", 25, "Window frames and glazing lines"),
    LayerSpec(A_STAIR, _ACI_MAGENTA, "CONTINUOUS", 25, "Stair treads, nosing and up arrow"),
    LayerSpec(A_DIM, _ACI_RED, "CONTINUOUS", 13, "Dimension chains, witness and leader lines"),
    LayerSpec(A_TEXT, _ACI_BLUE, "CONTINUOUS", 18, "Room names, notes and callouts"),
    LayerSpec(A_AREA, _ACI_YELLOW, "DASHED", 13, "Room area boundaries and hatch outlines"),
    LayerSpec(A_TITL, _ACI_BLACK_WHITE, "CONTINUOUS", 35, "Sheet frame and title block"),
)

LAYER_NAMES: tuple[str, ...] = tuple(layer.name for layer in LAYERS)

LAYERS_BY_NAME: Mapping[str, LayerSpec] = {layer.name: layer for layer in LAYERS}

#: Linetypes the layer table needs defined before use. ezdxf ships these as standard
#: patterns; listing them keeps the DXF setup honest about its assumptions.
REQUIRED_LINETYPES: tuple[str, ...] = ("CONTINUOUS", "DASHED", "HIDDEN", "CENTER")


def layer_for(name: str) -> LayerSpec:
    """Look up a layer, failing loudly on a typo rather than creating a stray layer."""
    try:
        return LAYERS_BY_NAME[name]
    except KeyError:
        raise KeyError(
            "%r is not one of the nine §7 layers (%s). Adding a layer changes what a "
            "municipal reviewer sees — do it deliberately, in layers.py."
            % (name, ", ".join(LAYER_NAMES))
        ) from None


__all__ = [
    "A_AREA",
    "A_DIM",
    "A_DOOR",
    "A_STAIR",
    "A_TEXT",
    "A_TITL",
    "A_WALL",
    "A_WALL_PART",
    "A_WIND",
    "LAYERS",
    "LAYERS_BY_NAME",
    "LAYER_NAMES",
    "REQUIRED_LINETYPES",
    "LayerSpec",
    "layer_for",
]
