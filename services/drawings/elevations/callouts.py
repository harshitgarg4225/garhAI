"""Material callout leaders, read off the facade kit's component metadata (§7).

    Elevations: ... material callout leaders from facade kit metadata.

The facade sub-model already carries everything a callout needs. ``facade.apply_kit``
stores the components the kit generator produced — kind, host wall/opening/storey, and a
``params`` bag holding the style, projection, thickness and material id that the generator
chose (see ``apps/web/src/features/canvas/facade/generator.ts``, which is the twin that
writes them). So a callout is a *read*, never a decision: this module never picks a
material, a projection or a finish, it reports the one the facade model records. If the
facade is empty the elevation gets no callouts and says so in its notes, which is the
truthful outcome for a project where nobody applied a kit yet.

Material *names* come from the catalogue and are optional: pass ``material_names`` (id →
display name, e.g. from ``fixtures/catalog/materials.json``) and callouts read "WPC
cladding"; omit it and they read the id. A missing catalogue must never silence a callout —
a contractor can look up an id, but cannot act on a label that was never drawn.

Placement is deliberately dumb: callouts stack in a single column outboard of the height
chain, at a fixed pitch, sorted by kind then component id. Fixed pitch is collision-free
by construction, and §7's greedy collision grid (step 4) belongs to the plan projector,
which has hundreds of labels to fit rather than a handful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.drawings.layers import A_DIM, A_TEXT
from services.drawings.projection.primitives import Line, Point, Primitive, Text, sanitise_text
from services.drawings.elevations.facade import FacadeFace, FacadeOpening, ProjectedBalcony
from services.drawings.elevations.vertical import (
    K_MATERIAL_CALLOUT,
    K_MATERIAL_LEADER,
    VerticalStyle,
    u_of,
)

__all__ = [
    "CALLOUT_COLUMN_GAP_PAPER_MM",
    "CALLOUT_SHOULDER_PAPER_MM",
    "Callout",
    "build_callouts",
    "callout_primitives",
    "callout_text",
    "surface_material_notes",
]

#: Gap between the height chain and the callout column, in **paper** mm: the callouts
#: stack outboard of the chain, so the chain's own offset plus this is where they land.
CALLOUT_COLUMN_GAP_PAPER_MM = 24.0
#: Length of the horizontal shoulder before the text, paper mm.
CALLOUT_SHOULDER_PAPER_MM = 9.0
#: Vertical pitch between stacked callouts, as a multiple of text height.
CALLOUT_PITCH_FACTOR = 3

#: How each facade component kind reads on a drawing. Kinds come from
#: ``garh_model.model.FACADE_COMPONENT_KINDS``; anything not listed falls back to the
#: kind itself, upper-cased, so a kit that grows a component still gets a callout.
_KIND_LABELS: Mapping[str, str] = {
    "window_trim": "WINDOW TRIM",
    "chajja": "CHAJJA",
    "parapet_profile": "PARAPET",
    "cladding_zone": "CLADDING",
    "porch": "PORCH",
    "railing": "RAILING",
    "band": "BAND",
    "louver": "LOUVER",
    "entry_feature": "ENTRY FEATURE",
}

#: Which params are worth printing, per kind, in print order. Values are
#: ``(param name, format)``; ``mm`` prints "600 PROJ." style, ``text`` prints as-is.
_KIND_PARAMS: Mapping[str, Tuple[Tuple[str, str], ...]] = {
    "window_trim": (("style", "style"), ("widthMm", "width"), ("recessDepthMm", "recess")),
    "chajja": (("style", "style"), ("projectionMm", "projection"), ("thicknessMm", "thickness")),
    "parapet_profile": (("style", "style"), ("heightMm", "height")),
    "cladding_zone": (("widthMm", "width"),),
    "porch": (("style", "style"), ("projectionMm", "projection")),
    "railing": (("style", "style"), ("heightMm", "height")),
}

_PARAM_SUFFIX: Mapping[str, str] = {
    "projection": "PROJ.",
    "thickness": "THK.",
    "height": "HT.",
    "width": "WD.",
    "recess": "RECESS",
}


@dataclass(frozen=True)
class Callout:
    """One material callout: what it says, what it points at, where the text sits."""

    component_id: str
    kind: str
    text: str
    anchor: Point
    label: Point

    def to_json(self) -> Dict[str, Any]:
        return {
            "componentId": self.component_id,
            "kind": self.kind,
            "text": self.text,
            "anchorMm": list(self.anchor),
            "labelMm": list(self.label),
        }


def _material_name(material_id: Optional[str], names: Mapping[str, str]) -> Optional[str]:
    if not material_id:
        return None
    return names.get(material_id, material_id)


def callout_text(
    kind: str,
    params: Mapping[str, Any],
    *,
    material_names: Optional[Mapping[str, str]] = None,
) -> str:
    """Render one component's metadata as a drawing callout.

    Only integers and enum-ish strings from ``params`` are printed, and lengths keep their
    millimetres (§7: dim text is mm regardless of display units). A component whose params
    are empty still gets its kind printed — "CHAJJA" alone is a useful callout; silence is
    not.
    """
    names = material_names or {}
    head = _KIND_LABELS.get(kind, kind.replace("_", " ").upper())
    parts: List[str] = []
    for name, role in _KIND_PARAMS.get(kind, ()):
        value = params.get(name)
        if value is None or value == "":
            continue
        if role == "style":
            parts.append(str(value).replace("-", " ").replace("_", " ").upper())
        elif isinstance(value, int) and not isinstance(value, bool):
            suffix = _PARAM_SUFFIX.get(role, "")
            parts.append(("%d %s" % (value, suffix)).strip())
    material = _material_name(
        params.get("materialId") if isinstance(params.get("materialId"), str) else None,
        names,
    )
    if material:
        parts.append(material.upper())
    if not parts:
        return head
    return "%s — %s" % (head, ", ".join(parts))


def _point_along_wall(wall: Any, offset_mm: int, u_axis: Tuple[int, int]) -> Optional[int]:
    """``u`` of a point ``offset_mm`` along a wall from its ``a`` end."""
    a = (int(wall.a.x), int(wall.a.y))
    b = (int(wall.b.x), int(wall.b.y))
    length = abs(b[0] - a[0]) + abs(b[1] - a[1])
    if length <= 0:
        return None
    step = ((b[0] - a[0]) // length, (b[1] - a[1]) // length)
    clamped = max(0, min(offset_mm, length))
    return u_of(a[0] + step[0] * clamped, a[1] + step[1] * clamped, u_axis)


def build_callouts(
    house: Any,
    *,
    faces: Sequence[FacadeFace],
    openings: Sequence[FacadeOpening],
    balconies: Sequence[ProjectedBalcony],
    u_axis: Tuple[int, int],
    u_origin_mm: int,
    column_u_mm: int,
    top_z_mm: int,
    terrace_mm: int,
    parapet_top_mm: int,
    sizes: VerticalStyle,
    material_names: Optional[Mapping[str, str]] = None,
) -> Tuple[Tuple[Callout, ...], Tuple[str, ...]]:
    """Callouts for the facade components that appear on **this** elevation.

    A component is on this elevation when its host is: a visible wall face, a visible
    opening, a visible balcony, or the building as a whole (a parapet profile has no wall).
    Anything hosted elsewhere belongs to another sheet — putting it here would label a
    facade the reader cannot see.
    """
    facade = getattr(house, "facade", None)
    components = list(getattr(facade, "components", ()) or ())
    if not components:
        return (), ("No facade kit applied — no material callouts on this elevation.",)

    face_by_wall = {face.wall_id: face for face in faces}
    opening_by_id = {item.opening_id: item for item in openings}
    balcony_by_id = {item.balcony_id: item for item in balconies}
    walls_by_id = {str(w.id): w for w in house.walls}

    height = sizes.dim_text_mm
    pitch = height * CALLOUT_PITCH_FACTOR
    chosen: List[Tuple[str, str, Point]] = []  # (component id, text, anchor)
    skipped = 0

    for component in sorted(components, key=lambda c: (str(c.kind), str(c.id))):
        kind = str(component.kind)
        params = dict(getattr(component, "params", {}) or {})
        opening_id = getattr(component, "opening_id", None)
        wall_id = getattr(component, "wall_id", None)
        anchor: Optional[Point] = None

        if opening_id and str(opening_id) in opening_by_id:
            item = opening_by_id[str(opening_id)]
            # A chajja sits on the lintel; a trim reads best off the jamb.
            anchor = (
                (item.u_centre - u_origin_mm, item.z_hi)
                if kind == "chajja"
                else (item.u_lo - u_origin_mm, (item.z_lo + item.z_hi) // 2)
            )
        elif kind == "railing":
            balcony_id = params.get("balconyId")
            item_b = balcony_by_id.get(str(balcony_id)) if balcony_id else None
            if item_b is not None:
                anchor = (
                    (item_b.u_lo + item_b.u_hi) // 2 - u_origin_mm,
                    item_b.railing_top_mm,
                )
        elif wall_id and str(wall_id) in face_by_wall:
            face = face_by_wall[str(wall_id)]
            wall = walls_by_id.get(str(wall_id))
            offset = params.get("offsetMm")
            u_raw: Optional[int] = None
            if wall is not None and isinstance(offset, int) and not isinstance(offset, bool):
                u_raw = _point_along_wall(wall, offset, u_axis)
            if u_raw is None:
                u_raw = (face.u_lo + face.u_hi) // 2
            anchor = (u_raw - u_origin_mm, (face.z_lo + face.z_hi) // 2)
        elif not wall_id and not opening_id:
            # Building-wide: the parapet profile is the archetype.
            anchor = (
                column_u_mm - sizes.style.paper_to_model_mm(CALLOUT_SHOULDER_PAPER_MM) * 2,
                (terrace_mm + parapet_top_mm) // 2,
            )

        if anchor is None:
            skipped += 1
            continue
        chosen.append(
            (str(component.id), callout_text(kind, params, material_names=material_names), anchor)
        )

    callouts: List[Callout] = []
    label_z = top_z_mm
    for component_id, text, anchor in chosen:
        callouts.append(
            Callout(
                component_id=component_id,
                kind=text.split(" — ")[0],
                text=text,
                anchor=anchor,
                label=(column_u_mm, label_z),
            )
        )
        label_z -= pitch

    notes: List[str] = []
    if skipped:
        notes.append(
            "%d facade component(s) belong to another elevation and are not called out here."
            % skipped
        )
    return tuple(callouts), tuple(notes)


def callout_primitives(
    callouts: Sequence[Callout], *, sizes: VerticalStyle
) -> Tuple[Primitive, ...]:
    """Leader (two segments) plus left-aligned text, per callout."""
    out: List[Primitive] = []
    height = sizes.dim_text_mm
    shoulder = sizes.style.paper_to_model_mm(CALLOUT_SHOULDER_PAPER_MM)
    for callout in callouts:
        elbow = (callout.label[0] - shoulder, callout.label[1])
        out.append(
            Line(
                A_DIM,
                callout.anchor,
                elbow,
                owner_id=callout.component_id,
                kind=K_MATERIAL_LEADER,
            )
        )
        out.append(
            Line(A_DIM, elbow, callout.label, owner_id=callout.component_id, kind=K_MATERIAL_LEADER)
        )
        out.append(
            Text(
                A_TEXT,
                (callout.label[0] + height // 2, callout.label[1]),
                sanitise_text(callout.text),
                height,
                h_align="left",
                v_align="middle",
                owner_id=callout.component_id,
                kind=K_MATERIAL_CALLOUT,
            )
        )
    return tuple(out)


def surface_material_notes(
    house: Any, *, material_names: Optional[Mapping[str, str]] = None
) -> Tuple[str, ...]:
    """Building-wide finishes from ``material.assign``, as sheet notes rather than leaders.

    A surface-group assignment ("every external wall is texture paint") has no single point
    on the facade to point a leader at, so it belongs in the notes block. Scoped
    assignments (one wall, one component) keep their element id in the note so the reader
    can find them.
    """
    names = material_names or {}
    out: List[str] = []
    for assignment in sorted(getattr(house, "materials", ()) or (), key=lambda m: str(m.id)):
        target = assignment.target
        group = str(target.group).replace("_", " ").upper()
        name = _material_name(str(assignment.material_id), names) or ""
        scope = ""
        if getattr(target, "element_id", None):
            scope = " (element %s)" % target.element_id
        elif getattr(target, "storey_id", None):
            scope = " (storey %s)" % target.storey_id
        out.append("%s%s: %s" % (group, scope, name))
    return tuple(out)
