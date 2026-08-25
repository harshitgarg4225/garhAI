"""Turn a schedule/area-statement table into the projection pipeline's primitives.

A table sheet has to sit next to drawing sheets in the same DXF, the same PDF and the
same SVG, which means it has to arrive at the renderers as the *same* primitive stream
the plan and elevation projectors emit — ``services.drawings.projection.primitives``'s
``Text`` and ``Line`` — not as a second kind of thing every renderer has to learn.

Two conversions happen here and nowhere else:

* **paper mm → model mm.** :mod:`services.drawings.schedules.table` lays a table out in
  paper millimetres (3 mm text, 7 mm rows), because that is how a table is designed. The
  projection stream is model space, where a 3 mm paper letter is 300 mm tall at 1:100.
  The multiply is exact — integers times an integer scale denominator — so no rounding
  enters a table that was laid out on whole millimetres.
* **alignment vocabulary.** ``left/centre/right`` → the projection module's
  ``left/center/right``.

The import is lazy and guarded: the schedules package is deliberately runnable with no
projection module present (its own tests and goldens need only strings), so a missing
or renamed projection API raises a message that says what to do rather than breaking
``import services.drawings.schedules``.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from services.drawings.schedules.table import LineItem, Table, TextItem

__all__ = ["ALIGN_TO_PROJECTION", "primitive_counts", "table_to_primitives"]

#: Our table alignment names → the projection module's ``H_ALIGNS``.
ALIGN_TO_PROJECTION = {
    "left": "left",
    "start": "left",
    "centre": "center",
    "middle": "center",
    "right": "right",
    "end": "right",
}


def _projection_module() -> Any:
    try:
        from services.drawings.projection import primitives  # noqa: WPS433
    except ImportError as error:  # pragma: no cover - depends on integration order
        raise ImportError(
            "services.drawings.projection.primitives is not importable, so a table "
            "cannot be converted into the shared primitive stream. Render the table "
            "with Table.primitives()/to_svg() instead, or point this adapter at the "
            "projection module's new location. (%s)" % error
        ) from error
    for name in ("Text", "Line"):
        if not hasattr(primitives, name):
            raise ImportError(
                "services.drawings.projection.primitives has no %s; the primitive "
                "contract moved and services/drawings/schedules/projection_adapter.py "
                "needs updating with it." % name
            )
    return primitives


def table_to_primitives(
    table: Table,
    *,
    scale_denominator: int = 100,
    origin_model_mm: Tuple[int, int] = (0, 0),
    owner_id: Optional[str] = None,
    kind: str = "schedule",
    validate: bool = True,
) -> Tuple[Any, ...]:
    """The table as projection ``Text`` / ``Line`` primitives, in **model** mm.

    ``scale_denominator`` is the sheet's scale (1:100 → 100): paper millimetres are
    multiplied by it so the table reads at its designed size once the sheet is plotted.
    ``origin_model_mm`` places the table block in the model-space stream.

    ``owner_id`` rides along on every primitive so a click on a rendered schedule row
    can find its way back to the sheet that owns it — the same anchoring mechanism §7
    uses for annotations.
    """
    if scale_denominator <= 0:
        raise ValueError("scale_denominator must be positive, got %d" % scale_denominator)
    primitives = _projection_module()
    text_cls = primitives.Text
    line_cls = primitives.Line
    sanitise = getattr(primitives, "sanitise_text", None)

    def to_model(x_mm: int, y_mm: int) -> Tuple[int, int]:
        return (
            origin_model_mm[0] + x_mm * scale_denominator,
            origin_model_mm[1] + y_mm * scale_denominator,
        )

    out: List[Any] = []
    for item in table.emit():
        if isinstance(item, TextItem):
            text = sanitise(item.text) if callable(sanitise) else item.text
            out.append(
                text_cls(
                    layer=item.layer,
                    position=to_model(item.x_mm, item.y_mm),
                    text=text,
                    height_mm=item.height_mm * scale_denominator,
                    h_align=ALIGN_TO_PROJECTION.get(item.align, "left"),
                    v_align="baseline",
                    owner_id=owner_id,
                    kind=kind,
                )
            )
        elif isinstance(item, LineItem):
            out.append(
                line_cls(
                    layer=item.layer,
                    a=to_model(item.x1_mm, item.y1_mm),
                    b=to_model(item.x2_mm, item.y2_mm),
                    owner_id=owner_id,
                    kind=kind,
                )
            )
    stream = tuple(out)
    if validate:
        validator = getattr(primitives, "validate_primitives", None)
        if callable(validator):
            validator(stream)
    return stream


def primitive_counts(stream: Sequence[Any]) -> Tuple[int, int]:
    """``(text, line)`` counts — what the smoke run prints and the tests assert."""
    text = sum(1 for item in stream if type(item).__name__ == "Text")
    line = sum(1 for item in stream if type(item).__name__ == "Line")
    return text, line
