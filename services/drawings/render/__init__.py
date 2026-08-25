"""Sheet renderers (§7). Primitives in, drawings out.

=========================  ==============================================  ==========
``primitives``             the 2D vocabulary every format consumes          **real**
``svg``                    primitives -> print-true, sanitised SVG          **real**
``sanitize``               §13: no script, no foreignObject                 **real**
``frame``                  border, title block, revision table              **real**
``tables``                 schedule + area statement                        **real**
``layout``                 fit a model extent onto a sheet                  **real**
``adapt``                  ``projection``/``autodim`` stream -> primitives   **real**
``reference_sheets``       model -> sheets (renderer reference input)        **real**
=========================  ==============================================  ==========

:mod:`~services.drawings.render.adapt` is the seam with the §7 projection and
auto-dimensioning engine (``services.drawings.projection``,
``services.drawings.autodim``), which was built in parallel with its own primitive
vocabulary. The two turned out to be the same five shapes with the same arc convention;
the adapter resolves the four mechanical differences (text height in model mm vs paper µm,
``dashed`` bool vs style name, DXF vs semantic hatch names, align vs anchor) in one tested
place. Read its docstring before changing either vocabulary.

Everything in this package is pure Python with no third-party dependency, and that is a
deliberate structural decision rather than a happy accident. The DXF writer needs
``ezdxf`` and the PNG path needs Pillow; neither is installable on every machine that
has to build this repo. So all the geometry, all the dimension arithmetic and all the
text placement live here, where they can be executed and tested anywhere, and the
format-specific modules under ``services.drawings.export`` stay thin enough that their
untestability is survivable.

The SVG renderer is the reference implementation of that split: it produces the §16
golden files, and because it is pure string output those goldens can be generated and
byte-compared on any machine, including one with nothing installed.

Import note: importing anything here pulls in ``services.drawings.__init__``, which
imports ``services.drawings.dxf`` for its constants, which imports
``services.common.logging`` -> ``structlog``. On a machine without the worker
dependencies installed, bootstrap with ``services.dev_stubs.install_worker_dep_stubs()``
first — the pattern ``scripts/solver_smoke.py`` and ``scripts/sheet_goldens.py`` use.
"""

from __future__ import annotations

from services.drawings.render.adapt import AdaptError, from_projection, model_mm_to_paper_um
from services.drawings.render.frame import frame_group, title_block_primitives
from services.drawings.render.layout import (
    PREFERRED_SCALES,
    PaperRect,
    choose_scale,
    content_rect,
    fit_placement,
)
from services.drawings.render.primitives import (
    Arc,
    Circle,
    Dim,
    DrawingGroup,
    Hatch,
    Line,
    Placement,
    Polyline,
    Primitive,
    SheetDrawing,
    Text,
    dim_geometry,
    div_round,
    sort_by_layer,
)
from services.drawings.render.sanitize import SvgSanitizeError, assert_sanitary, escape_text
from services.drawings.render.svg import normalize_svg, render_group_svg, render_sheet_svg
from services.drawings.render.tables import (
    area_statement_group,
    area_statement_table,
    schedule_group,
    schedule_table,
    table_primitives,
)

__all__ = [
    "PREFERRED_SCALES",
    "AdaptError",
    "Arc",
    "Circle",
    "Dim",
    "DrawingGroup",
    "Hatch",
    "Line",
    "PaperRect",
    "Placement",
    "Polyline",
    "Primitive",
    "SheetDrawing",
    "SvgSanitizeError",
    "Text",
    "area_statement_group",
    "area_statement_table",
    "assert_sanitary",
    "choose_scale",
    "content_rect",
    "dim_geometry",
    "div_round",
    "escape_text",
    "fit_placement",
    "frame_group",
    "from_projection",
    "model_mm_to_paper_um",
    "normalize_svg",
    "render_group_svg",
    "render_sheet_svg",
    "schedule_group",
    "schedule_table",
    "sort_by_layer",
    "table_primitives",
    "title_block_primitives",
]
