"""The §7 door/window schedule. **Fully implemented; pure integer arithmetic.**

    Schedules & area statement: door/window schedule = group openings by (kind, w, h)
    → tags D1.., W1.., V1..; counts per storey.

Two things in here are load-bearing beyond "draw a table".

**1. Tags are a contract with a printed sheet.**
A tag is not a label, it is a reference: the plan says ``W2`` next to a window and the
schedule says what ``W2`` is. So the tag for a given group must be

* *stable across runs* — the same design always yields the same tag, whatever order the
  ops arrived in (see :func:`~services.drawings.schedules.openings.normalise_openings`,
  which sorts before this module ever sees an opening), and
* *stable across edits* — adding a window must not silently re-point ``W2`` at a
  different size on a sheet a contractor is already building from.

The first falls out of ordering the groups by content (kind, then widest first). The
second needs memory, and the model already has it: ``Opening.tag`` is a persisted field
whose docstring says "assigned by the schedule generator". :func:`build_schedule`
honours tags that are already there and mints only what is genuinely new, taking the
next free number in that series (``max + 1``, never a hole left by a deleted group — a
recycled tag is exactly the silent re-point we are avoiding). Pass
``carry_previous=False`` for the from-scratch numbering a fresh set wants.

**2. The plan projection and this schedule must agree.**
:func:`opening_tags` returns ``{opening_id: tag}`` for exactly that: the plan renderer
labels its openings from this mapping rather than deriving its own, so "the plan's
opening labels and the schedule agree" is true by construction rather than by review.
:func:`tagged_openings` gives the same thing as ``opening.tag`` op payloads when the
worker wants to persist the assignment back onto the model.

Grouping is by ``(kind, widthMm, heightMm)`` exactly as §7 says — *not* by sill. A
1200×1200 window at 900 sill and one at 750 sill are the same manufactured unit; the
schedule prints the sill it found and says so in the notes when they differ, which is
what a fabrication drawing needs and what §7's key implies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.drawings.schedules.openings import (
    OPENING_KIND_ORDER,
    ScheduleOpening,
    StoreyRef,
    normalise_openings,
    normalise_storeys,
    storey_ids_in_print_order,
    storey_labels,
)
from services.drawings.schedules.sheet_primitives import ScheduleRow
from services.drawings.schedules.table import Column, Table, TableStyle

__all__ = [
    "KIND_LABELS",
    "TAG_PREFIXES",
    "UNKNOWN_KIND_PREFIX",
    "DoorWindowSchedule",
    "GroupKey",
    "ScheduleGroup",
    "build_schedule",
    "group_key_of",
    "opening_tags",
    "schedule_columns",
    "tagged_openings",
]

#: §7's tag series. ``V`` for ventilator is the Indian convention (a ventilator is a
#: separate line item from a window because it is a different manufactured unit).
TAG_PREFIXES: Mapping[str, str] = {"door": "D", "window": "W", "ventilator": "V"}

#: Any kind the model grows later shares one clearly-marked series rather than
#: colliding with D/W/V or silently vanishing from the schedule.
UNKNOWN_KIND_PREFIX = "X"

KIND_LABELS: Mapping[str, str] = {
    "door": "Door",
    "window": "Window",
    "ventilator": "Ventilator",
}

#: ``(kind, width_mm, height_mm)`` — §7's grouping key, verbatim.
GroupKey = Tuple[str, int, int]


def group_key_of(opening: ScheduleOpening) -> GroupKey:
    return (opening.kind, opening.width_mm, opening.height_mm)


def _prefix_for(kind: str) -> str:
    return TAG_PREFIXES.get(kind, UNKNOWN_KIND_PREFIX)


def _kind_rank(kind: str) -> int:
    try:
        return OPENING_KIND_ORDER.index(kind)
    except ValueError:
        return len(OPENING_KIND_ORDER)


def _group_sort_key(key: GroupKey) -> Tuple[int, str, int, int]:
    """Doors then windows then ventilators; widest first, then tallest.

    Widest-first is not decoration: on an Indian residential set ``D1`` is expected to
    be the main entrance door and ``W1`` the largest window, so a reader who has seen
    one schedule can read the next one without hunting.
    """
    kind, width_mm, height_mm = key
    return (_kind_rank(kind), kind, -width_mm, -height_mm)


@dataclass(frozen=True)
class ScheduleGroup:
    """One (kind, w, h) group: its tag, its per-storey counts and its sills."""

    tag: str
    key: GroupKey
    counts_by_storey: Mapping[str, int]
    total: int
    #: Every distinct sill height in the group, ascending. One value in the normal case.
    sills_mm: Tuple[int, ...]
    opening_ids: Tuple[str, ...]
    #: True when the tag came from openings that already carried it.
    tag_carried: bool = False

    @property
    def kind(self) -> str:
        return self.key[0]

    @property
    def width_mm(self) -> int:
        return self.key[1]

    @property
    def height_mm(self) -> int:
        return self.key[2]

    @property
    def sill_mm(self) -> int:
        """The sill the schedule prints: the lowest in the group (see ``notes``)."""
        return self.sills_mm[0] if self.sills_mm else 0

    @property
    def size_label(self) -> str:
        """``1200 x 1200`` — millimetres, per §7's "all dim text in mm on drawings"."""
        return "%d x %d" % (self.width_mm, self.height_mm)

    @property
    def notes(self) -> str:
        if self.kind == "door" and self.sills_mm in ((0,), ()):
            return ""
        if len(self.sills_mm) <= 1:
            return "Sill %d" % self.sill_mm
        return "Sill %s (varies)" % "/".join(str(value) for value in self.sills_mm)

    def to_row(self) -> ScheduleRow:
        """The shared sheet primitive (``services.drawings.sheets.ScheduleRow``)."""
        return ScheduleRow(
            tag=self.tag,
            kind=self.kind,
            width_mm=self.width_mm,
            height_mm=self.height_mm,
            sill_mm=self.sill_mm,
            counts_by_storey=dict(self.counts_by_storey),
            total=self.total,
            notes=self.notes,
        )


@dataclass(frozen=True)
class DoorWindowSchedule:
    """Every group, in print order, with the tag map the plan projection reads."""

    groups: Tuple[ScheduleGroup, ...]
    #: Count-column order: model storeys first, then any unresolved bucket in use.
    storey_ids: Tuple[str, ...]
    storey_headers: Mapping[str, str]
    storeys: Tuple[StoreyRef, ...]
    #: ``opening id -> tag``. What the plan labels its openings from.
    tag_by_opening_id: Mapping[str, str]
    warnings: Tuple[str, ...] = ()

    # -- totals ------------------------------------------------------------
    @property
    def total(self) -> int:
        return sum(group.total for group in self.groups)

    def total_for_storey(self, storey_id: str) -> int:
        return sum(group.counts_by_storey.get(storey_id, 0) for group in self.groups)

    def totals_by_kind(self) -> Mapping[str, int]:
        out: Dict[str, int] = {}
        for group in self.groups:
            out[group.kind] = out.get(group.kind, 0) + group.total
        return out

    def group_for_tag(self, tag: str) -> Optional[ScheduleGroup]:
        for group in self.groups:
            if group.tag == tag:
                return group
        return None

    def rows(self) -> Tuple[ScheduleRow, ...]:
        """The shared ``ScheduleRow`` primitives, in print order."""
        return tuple(group.to_row() for group in self.groups)

    # -- table -------------------------------------------------------------
    def table(
        self,
        *,
        title: str = "DOOR & WINDOW SCHEDULE",
        style: Optional[TableStyle] = None,
        origin_mm: Tuple[int, int] = (0, 0),
    ) -> Table:
        """The schedule as a :class:`~services.drawings.schedules.table.Table`."""
        columns = schedule_columns(self.storey_ids, self.storey_headers)
        rows: List[Tuple[str, ...]] = []
        for group in self.groups:
            cells = [
                group.tag,
                KIND_LABELS.get(group.kind, group.kind.title()),
                group.size_label,
                group.notes,
            ]
            cells.extend(str(group.counts_by_storey.get(sid, 0)) for sid in self.storey_ids)
            cells.append(str(group.total))
            rows.append(tuple(cells))
        totals = ["TOTAL", "", "", ""]
        totals.extend(str(self.total_for_storey(sid)) for sid in self.storey_ids)
        totals.append(str(self.total))
        rows.append(tuple(totals))
        return Table(
            title=title,
            columns=columns,
            rows=tuple(rows),
            style=style or TableStyle(),
            origin_mm=origin_mm,
            rule_after=(len(rows) - 2,) if len(rows) >= 2 else (),
            bold_rows=(len(rows) - 1,),
            footnotes=self._footnotes(),
        )

    def _footnotes(self) -> Tuple[str, ...]:
        notes = [
            "All sizes in mm (width x height). Sizes are structural opening sizes, "
            "excluding frame.",
        ]
        if any(len(group.sills_mm) > 1 for group in self.groups):
            notes.append(
                "Where a tag shows more than one sill, the schedule prints the lowest; "
                "refer to the plans for each location."
            )
        return tuple(notes)

    def to_json(self) -> Dict[str, Any]:
        return {
            "storeyIds": list(self.storey_ids),
            "storeyHeaders": dict(self.storey_headers),
            "rows": [row.to_json() for row in self.rows()],
            "totalsByKind": dict(sorted(self.totals_by_kind().items())),
            "totalsByStorey": {sid: self.total_for_storey(sid) for sid in self.storey_ids},
            "total": self.total,
            "tagByOpeningId": dict(sorted(self.tag_by_opening_id.items())),
            "warnings": list(self.warnings),
        }


def schedule_columns(
    storey_ids: Sequence[str], headers: Mapping[str, str]
) -> Tuple[Column, ...]:
    """Tag · Type · Size · Remarks · one count column per storey · Total (F7-A)."""
    columns: List[Column] = [
        Column("tag", "TAG", "left"),
        Column("kind", "TYPE", "left"),
        Column("size", "SIZE (mm)", "right"),
        Column("notes", "REMARKS", "left"),
    ]
    for storey_id in storey_ids:
        columns.append(Column("count.%s" % storey_id, headers.get(storey_id, "?"), "right"))
    columns.append(Column("total", "TOTAL", "right"))
    return tuple(columns)


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------
def build_schedule(
    source: Any,
    *,
    previous_tags: Optional[Mapping[GroupKey, str]] = None,
    carry_previous: bool = True,
) -> DoorWindowSchedule:
    """Group, tag and count every opening in ``source``.

    ``source`` is a ``garh_model`` ``HouseModel``/``ProjectDoc``, a ``garh_rules``
    ``EvaluationContext``, or the JSON form of either.

    ``previous_tags`` maps a group key to the tag it already has on an issued sheet.
    When ``carry_previous`` is true (the default) the same information is also read off
    the openings themselves (``Opening.tag``), so a model that has been through this
    function once keeps its tags for good.
    """
    openings, warnings = normalise_openings(source)
    storeys = normalise_storeys(source)
    storey_ids = storey_ids_in_print_order(storeys, openings)
    headers = storey_labels(storeys, storey_ids)

    grouped: Dict[GroupKey, List[ScheduleOpening]] = {}
    for opening in openings:
        grouped.setdefault(group_key_of(opening), []).append(opening)

    inherited: Dict[GroupKey, str] = {}
    warn_list = list(warnings)
    if carry_previous:
        inherited.update(_tags_from_openings(grouped, warn_list))
    if previous_tags:
        # An explicit map wins over what is on the model: it is what the caller read
        # from the last *issued* sheet.
        inherited.update({tuple(key): value for key, value in previous_tags.items()})  # type: ignore[misc]

    ordered_keys = sorted(grouped, key=_group_sort_key)
    tags = assign_tags(ordered_keys, inherited)

    groups: List[ScheduleGroup] = []
    tag_by_opening: Dict[str, str] = {}
    for key in ordered_keys:
        members = grouped[key]
        counts: Dict[str, int] = {}
        for opening in members:
            counts[opening.storey_id] = counts.get(opening.storey_id, 0) + 1
            tag_by_opening[opening.id] = tags[key]
        groups.append(
            ScheduleGroup(
                tag=tags[key],
                key=key,
                counts_by_storey={sid: counts[sid] for sid in storey_ids if sid in counts},
                total=len(members),
                sills_mm=tuple(sorted({opening.sill_mm for opening in members})),
                opening_ids=tuple(opening.id for opening in members),
                tag_carried=key in inherited,
            )
        )

    return DoorWindowSchedule(
        groups=tuple(groups),
        storey_ids=tuple(storey_ids),
        storey_headers=headers,
        storeys=storeys,
        tag_by_opening_id=tag_by_opening,
        warnings=tuple(warn_list),
    )


def assign_tags(
    ordered_keys: Sequence[GroupKey], inherited: Mapping[GroupKey, str]
) -> Dict[GroupKey, str]:
    """Tags for every group: inherited ones kept, new ones numbered ``max + 1``.

    Deterministic in both directions. With no inherited tags this is simply
    ``D1, D2, …`` down ``ordered_keys``; with inherited tags the new groups take numbers
    above every number already used in their series, so no tag on an issued sheet ever
    changes meaning and no retired tag is recycled.
    """
    tags: Dict[GroupKey, str] = {}
    used: Dict[str, set] = {}
    for key in ordered_keys:
        tag = inherited.get(key)
        if tag is None:
            continue
        prefix, number = _split_tag(tag)
        if prefix != _prefix_for(key[0]):
            # A carried tag from a different series would put a window in the D column.
            continue
        tags[key] = tag
        used.setdefault(prefix, set()).add(number)
    # Numbers claimed by inherited tags of groups no longer present still block reuse.
    for key, tag in inherited.items():
        prefix, number = _split_tag(tag)
        used.setdefault(prefix, set()).add(number)

    next_number: Dict[str, int] = {
        prefix: (max(numbers) + 1 if numbers else 1) for prefix, numbers in used.items()
    }
    for key in ordered_keys:
        if key in tags:
            continue
        prefix = _prefix_for(key[0])
        number = next_number.get(prefix, 1)
        tags[key] = "%s%d" % (prefix, number)
        next_number[prefix] = number + 1
    return tags


def _split_tag(tag: str) -> Tuple[str, int]:
    """``"W12" -> ("W", 12)``. A tag we cannot parse claims number 0, never a crash."""
    prefix = "".join(ch for ch in tag if not ch.isdigit())
    digits = "".join(ch for ch in tag if ch.isdigit())
    return prefix, int(digits) if digits else 0


def _tags_from_openings(
    grouped: Mapping[GroupKey, Sequence[ScheduleOpening]], warnings: List[str]
) -> Dict[GroupKey, str]:
    """Tags already persisted on the model, one per group.

    Two openings of one group carrying different tags is a real (if rare) state — a
    manual edit, or a group that used to be two. The lowest-numbered tag wins so the
    result is deterministic, and the disagreement is reported rather than smoothed over.
    """
    out: Dict[GroupKey, str] = {}
    for key in sorted(grouped, key=_group_sort_key):
        found = sorted(
            {opening.existing_tag for opening in grouped[key] if opening.existing_tag},
            key=lambda tag: (_split_tag(tag)[0], _split_tag(tag)[1], tag),
        )
        if not found:
            continue
        out[key] = found[0]
        if len(found) > 1:
            warnings.append(
                "Openings of the %s %d x %d group carry different tags (%s); the "
                "schedule uses %s. Re-tag them on the plan to clear this."
                % (key[0], key[1], key[2], ", ".join(found), found[0])
            )
    return out


def opening_tags(
    source: Any,
    *,
    previous_tags: Optional[Mapping[GroupKey, str]] = None,
    carry_previous: bool = True,
) -> Dict[str, str]:
    """``{opening_id: tag}`` — the mapping the plan projection labels openings from.

    This is the whole reason the tagger lives in its own module: the plan sheet and the
    schedule sheet are rendered by different code, and they must not derive tags
    independently. Plan projection: call this, label each opening from it, and never
    compute a tag locally.
    """
    return dict(
        build_schedule(
            source, previous_tags=previous_tags, carry_previous=carry_previous
        ).tag_by_opening_id
    )


def tagged_openings(schedule: DoorWindowSchedule) -> Tuple[Dict[str, str], ...]:
    """``[{openingId, tag}, …]`` for persisting tags back onto the model.

    The worker turns these into ``opening.set_tag``-shaped payloads; this module does
    not mint ops, because ops are the model layer's vocabulary.
    """
    return tuple(
        {"openingId": opening_id, "tag": tag}
        for opening_id, tag in sorted(schedule.tag_by_opening_id.items())
    )
