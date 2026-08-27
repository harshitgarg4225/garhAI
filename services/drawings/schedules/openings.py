"""One opening shape for the schedule, from whichever source the caller has.

The schedule has to run in three places with three different inputs:

============================================  ==========================================
``garh_model.HouseModel`` / ``ProjectDoc``    the sheet worker, after a fold
``garh_rules.EvaluationContext``              the compliance panel and the area statement
plain JSON                                    fixtures, the API, a job payload
============================================  ==========================================

They disagree on one thing that matters: a model ``Opening`` is hosted on a **wall** and
learns its storey from that wall, while a rules ``OpeningSummary`` carries ``storeyId``
directly. Resolving that in one place — here — is what lets
:mod:`services.drawings.schedules.door_window` be a pure function of
``(kind, width, height, sill, storey)`` tuples and therefore runnable, and testable, on
a bare interpreter with no model or rules import at all.

Nothing in this module derives geometry: it reads fields, resolves the wall→storey
mapping, and reports what it could not resolve instead of guessing. An opening whose
host wall is missing lands in :data:`UNKNOWN_STOREY` with a warning — it still appears
on the schedule, because an opening silently dropped from a schedule is an opening the
contractor never orders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "OPENING_KIND_ORDER",
    "UNKNOWN_STOREY",
    "ScheduleOpening",
    "StoreyRef",
    "normalise_openings",
    "normalise_storeys",
    "storey_ids_in_print_order",
    "storey_labels",
]

#: Storey bucket for an opening whose host wall could not be resolved. Visible on
#: purpose: it prints as a row and raises a warning rather than vanishing.
UNKNOWN_STOREY = "(unresolved storey)"

#: The §3 opening kinds, in the order a schedule prints them.
OPENING_KIND_ORDER: tuple[str, ...] = ("door", "window", "ventilator")


@dataclass(frozen=True)
class StoreyRef:
    """A storey, reduced to what a schedule column needs."""

    id: str
    index: int
    #: "Ground Floor" / "First Floor" — the model's own name where there is one.
    name: str

    @property
    def short_label(self) -> str:
        """A narrow column header: ``GF``, ``FF``, ``2F``, ``TER``.

        Municipal schedules are printed with one count column per floor on an A2
        sheet, so the header has to be short. Derived from the index, not the name,
        because "Ground Floor" and "Ground floor" must not produce two headers.
        """
        if self.index == 0:
            return "GF"
        if self.index == 1:
            return "FF"
        return "%dF" % self.index


@dataclass(frozen=True)
class ScheduleOpening:
    """One opening, normalised. Integer millimetres, always."""

    id: str
    storey_id: str
    kind: str
    width_mm: int
    height_mm: int
    sill_mm: int
    #: The tag already printed on an issued drawing, if any (``Opening.tag``). The
    #: tagger honours it so a re-issue never re-points D2 at a different door.
    existing_tag: str | None = None
    wall_id: str | None = None
    #: Rooms this opening serves, when the source knows (rules context does).
    room_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("width_mm", "height_mm", "sill_mm"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(
                    "Opening %s.%s must be an integer of millimetres, got %r (%s). §3: "
                    "lengths are integer mm, never floats."
                    % (self.id, name, value, type(value).__name__)
                )
        if self.width_mm <= 0 or self.height_mm <= 0:
            raise ValueError(
                "Opening %s has a non-positive size (%d x %d mm); it cannot be scheduled."
                % (self.id, self.width_mm, self.height_mm)
            )
        if self.sill_mm < 0:
            raise ValueError("Opening %s has a negative sill (%d mm)." % (self.id, self.sill_mm))


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------
def _house_of(source: Any) -> Any:
    """The house-shaped object inside whatever was passed.

    ``ProjectDoc`` exposes it as ``.house``; a ``HouseModel`` *is* it. Duck-typed on
    purpose: importing ``garh_model`` here would put ``apps/api`` on the drawings
    worker's import path for no reason.
    """
    house = getattr(source, "house", None)
    if house is not None and hasattr(house, "openings"):
        return house
    return source


def _storey_name(storey: Any, index: int) -> str:
    name = getattr(storey, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(storey, Mapping):
        raw = storey.get("name")
        if isinstance(raw, str) and raw:
            return raw
    return _default_storey_name(index)


def _default_storey_name(index: int) -> str:
    if index == 0:
        return "Ground Floor"
    if index == 1:
        return "First Floor"
    ordinals = ("Second", "Third", "Fourth", "Fifth", "Sixth")
    if index - 2 < len(ordinals):
        return "%s Floor" % ordinals[index - 2]
    return "Floor %d" % index


def normalise_storeys(source: Any) -> tuple[StoreyRef, ...]:
    """Storeys in index order, from a model, a rules context, or JSON.

    Order is the print order of the schedule's count columns and of the area
    statement's per-floor rows, so it is taken from the explicit ``index`` where the
    source has one (rules context) and from array position where it does not (the model
    keeps ``storeys`` ground-first, per §3).
    """
    summary = _rules_model(source)
    if summary is not None:
        # a rules EvaluationContext: ModelSummary storeys carry an explicit index
        return tuple(
            sorted(
                (
                    StoreyRef(id=s.id, index=int(s.index), name=_default_storey_name(int(s.index)))
                    for s in summary.storeys
                ),
                key=lambda ref: (ref.index, ref.id),
            )
        )
    if not isinstance(source, Mapping):
        house = _house_of(source)
        storeys = getattr(house, "storeys", None)
        if storeys is None:
            raise TypeError(
                "Cannot read storeys from %r. Pass a garh_model HouseModel/ProjectDoc, a "
                "garh_rules EvaluationContext, or their JSON form." % type(source).__name__
            )
        # §3 keeps ``storeys`` ground-first, so array position IS the index.
        return tuple(
            StoreyRef(id=s.id, index=index, name=_storey_name(s, index))
            for index, s in enumerate(storeys)
        )
    if isinstance(source, Mapping):
        raw = _mapping_model(source).get("storeys") or ()
        refs: list[StoreyRef] = []
        for position, item in enumerate(raw):
            index = item.get("index")
            index = position if index is None else int(index)
            refs.append(
                StoreyRef(
                    id=str(item["id"]),
                    index=index,
                    name=_storey_name(item, index),
                )
            )
        return tuple(sorted(refs, key=lambda ref: (ref.index, ref.id)))
    raise TypeError(
        "Cannot read storeys from %r. Pass a garh_model HouseModel/ProjectDoc, a "
        "garh_rules EvaluationContext, or their JSON form." % type(source).__name__
    )


def _rules_model(source: Any) -> Any:
    """The ``ModelSummary`` of a rules ``EvaluationContext``, or ``None``.

    Identified structurally: a context's ``.model`` has storeys *and* openings but no
    walls, because the rules engine is given a projection, not geometry. Neither
    ``HouseModel`` nor ``ProjectDoc`` has a ``.model`` attribute at all, so this cannot
    misfire on them.
    """
    if isinstance(source, Mapping):
        return None
    summary = getattr(source, "model", None)
    if summary is None:
        return None
    if (
        hasattr(summary, "storeys")
        and hasattr(summary, "openings")
        and not hasattr(summary, "walls")
    ):
        return summary
    return None


def _mapping_model(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """JSON may be a whole context (``{model: {...}}``) or the model itself."""
    inner = raw.get("model")
    if isinstance(inner, Mapping):
        return inner
    house = raw.get("house")
    if isinstance(house, Mapping):
        return house
    return raw


def normalise_openings(source: Any) -> tuple[tuple[ScheduleOpening, ...], tuple[str, ...]]:
    """``(openings, warnings)`` from any supported source, in a deterministic order.

    The returned order is ``(storey index, opening id)``: source array order is *not*
    used, because two folds of the same op log in a different wall order would then
    produce a different order here — and the tag table would follow it. Tags must
    depend on the design, never on the history that built it.
    """
    warnings: list[str] = []
    storeys = normalise_storeys(source)
    storey_index = {ref.id: ref.index for ref in storeys}

    if isinstance(source, Mapping):
        raw_openings = list(_mapping_model(source).get("openings") or ())
        walls = list(_mapping_model(source).get("walls") or ())
        wall_storey = {str(w["id"]): str(w["storeyId"]) for w in walls if "storeyId" in w}
        items: list[ScheduleOpening] = []
        for raw in raw_openings:
            wall_id = raw.get("wallId")
            storey_id = raw.get("storeyId") or (wall_storey.get(str(wall_id)) if wall_id else None)
            items.append(
                _make(
                    id=str(raw["id"]),
                    storey_id=storey_id,
                    kind=str(raw["kind"]),
                    width_mm=int(raw["widthMm"]),
                    height_mm=int(raw["heightMm"]),
                    sill_mm=int(raw.get("sillMm") or 0),
                    existing_tag=raw.get("tag"),
                    wall_id=str(wall_id) if wall_id else None,
                    room_ids=tuple(str(r) for r in (raw.get("roomIds") or ())),
                    warnings=warnings,
                )
            )
        return _ordered(items, storey_index), tuple(warnings)

    summary = _rules_model(source)
    if summary is not None:
        # rules EvaluationContext: storeyId is first-class, no wall lookup needed
        items = [
            _make(
                id=o.id,
                storey_id=o.storey_id,
                kind=o.kind,
                width_mm=o.width_mm,
                height_mm=o.height_mm,
                sill_mm=0 if o.sill_mm is None else int(o.sill_mm),
                existing_tag=None,
                wall_id=o.wall_id,
                room_ids=tuple(o.room_ids),
                warnings=warnings,
            )
            for o in summary.openings
        ]
        return _ordered(items, storey_index), tuple(warnings)

    house = _house_of(source)
    if not hasattr(house, "openings"):
        raise TypeError(
            "Cannot read openings from %r. Pass a garh_model HouseModel/ProjectDoc, a "
            "garh_rules EvaluationContext, or their JSON form." % type(source).__name__
        )
    wall_storey = {w.id: w.storey_id for w in getattr(house, "walls", ())}
    items = []
    for opening in house.openings:
        items.append(
            _make(
                id=opening.id,
                storey_id=wall_storey.get(opening.wall_id),
                kind=opening.kind,
                width_mm=opening.width_mm,
                height_mm=opening.height_mm,
                sill_mm=opening.sill_mm,
                existing_tag=getattr(opening, "tag", None),
                wall_id=opening.wall_id,
                room_ids=(),
                warnings=warnings,
                host_wall_id=opening.wall_id,
            )
        )
    return _ordered(items, storey_index), tuple(warnings)


def _make(
    *,
    id: str,
    storey_id: str | None,
    kind: str,
    width_mm: int,
    height_mm: int,
    sill_mm: int,
    existing_tag: Any,
    wall_id: str | None,
    room_ids: tuple[str, ...],
    warnings: list[str],
    host_wall_id: str | None = None,
) -> ScheduleOpening:
    if storey_id is None:
        storey_id = UNKNOWN_STOREY
        if host_wall_id:
            warnings.append(
                "Opening %s is hosted on wall %s, which is not in the model — it is "
                "scheduled under %r rather than dropped." % (id, host_wall_id, UNKNOWN_STOREY)
            )
        else:
            warnings.append(
                "Opening %s names no storey and no resolvable host wall — scheduled "
                "under %r." % (id, UNKNOWN_STOREY)
            )
    tag = existing_tag if isinstance(existing_tag, str) and existing_tag else None
    return ScheduleOpening(
        id=id,
        storey_id=storey_id,
        kind=kind,
        width_mm=width_mm,
        height_mm=height_mm,
        sill_mm=sill_mm,
        existing_tag=tag,
        wall_id=wall_id,
        room_ids=room_ids,
    )


def _ordered(
    items: Iterable[ScheduleOpening], storey_index: Mapping[str, int]
) -> tuple[ScheduleOpening, ...]:
    # Unresolved storeys sort last (large index), then by id — total and deterministic.
    return tuple(
        sorted(items, key=lambda o: (storey_index.get(o.storey_id, 10_000), o.storey_id, o.id))
    )


def storey_ids_in_print_order(
    storeys: Sequence[StoreyRef], openings: Sequence[ScheduleOpening]
) -> tuple[str, ...]:
    """Count-column order: the model's storeys, then any unresolved bucket in use."""
    ordered = [ref.id for ref in storeys]
    extra = sorted({o.storey_id for o in openings} - set(ordered))
    return tuple(ordered + extra)


def storey_labels(storeys: Sequence[StoreyRef], storey_ids: Sequence[str]) -> dict[str, str]:
    """``storey id -> short column header``, including the unresolved bucket."""
    by_id = {ref.id: ref for ref in storeys}
    out: dict[str, str] = {}
    for storey_id in storey_ids:
        ref = by_id.get(storey_id)
        out[storey_id] = ref.short_label if ref is not None else "?"
    return out
