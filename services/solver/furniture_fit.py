"""§5.4 furniture-fit test — can each room actually take its standard set?

This is the critic's buildability conscience. A room can satisfy every NBC
minimum and still be useless: 9.6 m² arranged 1.6 m wide passes ``room_area_min``
and fits no bed. §5.6 gates on this, so a plan that fails here never reaches an
architect.

Two deliberate properties:

**Integer millimetres, no floats.** Every dimension, clearance and offset is an
``int`` count of mm, matching the rest of the model core.

**The packer is conservative.** It is a deterministic shelf heuristic, not an
optimal 2D bin packer — optimal rectangle packing is NP-hard and a solver stage
cannot afford it per candidate. Being conservative means it can report "does not
fit" for a room where a cleverer arrangement would have worked, and it will
never report "fits" for a room where nothing fits. That asymmetry is the right
one for a gate: golden rule 2 says never show a hard-fail plan, so erring toward
rejection costs us a candidate, while erring toward acceptance costs an architect
their trust.

Clearance is modelled as an access strip in front of an item — a wardrobe needs
750 mm of standing room to open, a bed 600 mm to walk past — and the strip
rotates with the item.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.common.errors import PermanentError

#: Rooms people live in. §5.6 gates furniture fit on these; a store room that
#: cannot take a shelf is not a reason to discard an otherwise good plan.
HABITABLE_TYPES = frozenset(
    {
        "living",
        "dining",
        "living_dining",
        "bedroom",
        "bedroom_master",
        "guest_bedroom",
        "study",
        "kitchen",
        "kitchen_dining",
    }
)

#: The minimum credible contents of a room, by type, as catalogue item ids.
#:
#: These are the items an Indian architect would consider non-negotiable for the
#: room to be usable — not a full furnishing scheme. Keeping the set small keeps
#: the gate honest: it rejects rooms that cannot function, not rooms that are
#: merely tight. Ids are validated against the catalogue at load time, so a
#: renamed catalogue entry fails loudly instead of silently skipping a check.
REQUIRED_SETS: Mapping[str, tuple[str, ...]] = {
    "bedroom": ("bed-single", "wardrobe-2door"),
    "bedroom_master": ("bed-queen", "wardrobe-3door"),
    "guest_bedroom": ("bed-single", "wardrobe-2door"),
    "servant_room": ("bed-single",),
    "living": ("sofa-3seat", "coffee-table"),
    "living_dining": ("sofa-3seat", "coffee-table", "dining-4"),
    "dining": ("dining-6",),
    "kitchen": ("kitchen-counter", "kitchen-sink", "hob-4burner", "refrigerator"),
    "kitchen_dining": ("kitchen-counter", "kitchen-sink", "hob-4burner", "dining-4"),
    "study": ("study-table",),
    "pooja": ("pooja-unit",),
    "bath": ("wc-floor", "washbasin", "shower-area"),
    "bath_wc": ("wc-floor", "washbasin"),
    "wc": ("wc-floor",),
    "garage": ("car-hatchback",),
}


class CatalogError(ValueError):
    """The furniture catalogue is missing an id a required set names."""


class CatalogUnavailableError(PermanentError):
    """The catalogue file itself is not there — a deployment fault, not a brief fault.

    Permanent on purpose. The first deployed worker image shipped without
    ``fixtures/``: every generate job raised a bare ``FileNotFoundError``, which the
    runtime treated as retryable, so each job burned four attempts and its credit
    before dying with "something went wrong on our side". A missing data file will
    not appear on the next attempt; say so once, fast, and without a path (§13).
    """

    code = "catalog_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "The furniture catalogue isn't available on this worker, so plans can't be "
            "checked for fit.",
            action="This is a fault on our side and has been logged — try again in a few minutes.",
        )


@dataclass(frozen=True)
class CatalogItem:
    """One catalogue entry, all lengths integer mm."""

    id: str
    name: str
    category: str
    width_mm: int
    depth_mm: int
    clearance_mm: int

    @property
    def footprint_mm2(self) -> int:
        return self.width_mm * self.depth_mm


@dataclass(frozen=True)
class FitPlacement:
    """Where one item landed, in room-local mm from the room's SW corner."""

    item_id: str
    x_mm: int
    y_mm: int
    width_mm: int
    depth_mm: int
    rotated: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "xMm": self.x_mm,
            "yMm": self.y_mm,
            "widthMm": self.width_mm,
            "depthMm": self.depth_mm,
            "rotated": self.rotated,
        }


@dataclass(frozen=True)
class RoomFit:
    """The verdict for one room."""

    room_key: str
    room_type: str
    fits: bool
    placed: tuple[FitPlacement, ...]
    missing: tuple[str, ...]
    #: Fraction of the room's floor taken by furniture footprints, 0-100.
    utilisation_percent: int
    #: True when the type carries no requirement — trivially fits, not evidence.
    unchecked: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "roomKey": self.room_key,
            "roomType": self.room_type,
            "fits": self.fits,
            "placed": [item.to_json() for item in self.placed],
            "missing": list(self.missing),
            "utilisationPercent": self.utilisation_percent,
            "unchecked": self.unchecked,
        }


def default_catalog_path() -> str:
    """Repo-relative path to the seeded catalogue (§17)."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", ".."))
    return os.path.join(root, "fixtures", "catalog", "furniture.json")


def load_catalog(path: str | None = None) -> dict[str, CatalogItem]:
    """Load the furniture catalogue, keyed by id.

    Raises :class:`CatalogError` when a :data:`REQUIRED_SETS` id is absent — a
    silently-skipped requirement would turn the gate into decoration.
    """
    target = path or default_catalog_path()
    try:
        with open(target, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise CatalogUnavailableError() from exc
    entries: Iterable[Mapping[str, Any]] = raw["items"] if isinstance(raw, dict) else raw

    catalog: dict[str, CatalogItem] = {}
    for entry in entries:
        item = CatalogItem(
            id=str(entry["id"]),
            name=str(entry.get("name", entry["id"])),
            category=str(entry.get("category", "")),
            width_mm=int(entry["widthMm"]),
            depth_mm=int(entry["depthMm"]),
            clearance_mm=int(entry.get("clearanceMm", 0) or 0),
        )
        catalog[item.id] = item

    missing = sorted(
        {
            item_id
            for required in REQUIRED_SETS.values()
            for item_id in required
            if item_id not in catalog
        }
    )
    if missing:
        raise CatalogError(
            "furniture catalogue {} is missing ids named by REQUIRED_SETS: {}".format(
                target, ", ".join(missing)
            )
        )
    return catalog


def required_items_for(room_type: str) -> tuple[str, ...]:
    """The item ids a room of this type must accommodate ( ``()`` when none)."""
    return REQUIRED_SETS.get(room_type, ())


def _orientations(item: CatalogItem) -> tuple[tuple[int, int, bool], ...]:
    """Effective (width, depth, rotated) footprints, clearance included.

    The clearance strip sits in front of the item along its depth axis and turns
    with it. Unrotated is tried first so identical inputs always pack identically.
    """
    upright = (item.width_mm, item.depth_mm + item.clearance_mm, False)
    turned = (item.depth_mm + item.clearance_mm, item.width_mm, True)
    if upright[:2] == turned[:2]:
        return (upright,)
    return (upright, turned)


def _sort_key(item: CatalogItem) -> tuple[int, int, str]:
    """Largest-first, ties broken by id so packing is deterministic."""
    return (-max(item.width_mm, item.depth_mm), -item.footprint_mm2, item.id)


def pack_room(
    room_width_mm: int, room_depth_mm: int, items: Sequence[CatalogItem]
) -> tuple[tuple[FitPlacement, ...], tuple[str, ...]]:
    """Shelf-pack items into a room rectangle. Returns (placed, missing ids).

    Deterministic: same room and same items always give the same layout.
    """
    if room_width_mm <= 0 or room_depth_mm <= 0:
        return (), tuple(item.id for item in items)

    placed: list[FitPlacement] = []
    missing: list[str] = []

    shelf_y = 0
    shelf_height = 0
    cursor_x = 0

    for item in sorted(items, key=_sort_key):
        seated = False
        for width, depth, rotated in _orientations(item):
            if width > room_width_mm:
                continue
            # Current shelf, if the item fits beside what is already there.
            if (
                cursor_x + width <= room_width_mm
                and shelf_y + max(shelf_height, depth) <= room_depth_mm
            ):
                placed.append(FitPlacement(item.id, cursor_x, shelf_y, width, depth, rotated))
                cursor_x += width
                shelf_height = max(shelf_height, depth)
                seated = True
                break
            # Otherwise open a new shelf above the current one.
            next_y = shelf_y + shelf_height
            if next_y + depth <= room_depth_mm:
                shelf_y = next_y
                shelf_height = depth
                cursor_x = width
                placed.append(FitPlacement(item.id, 0, shelf_y, width, depth, rotated))
                seated = True
                break
        if not seated:
            missing.append(item.id)

    return tuple(placed), tuple(missing)


def fit_room(
    room_key: str,
    room_type: str,
    width_mm: int,
    depth_mm: int,
    catalog: Mapping[str, CatalogItem],
) -> RoomFit:
    """Test one room against its required set."""
    required = required_items_for(room_type)
    if not required:
        return RoomFit(
            room_key=room_key,
            room_type=room_type,
            fits=True,
            placed=(),
            missing=(),
            utilisation_percent=0,
            unchecked=True,
        )

    items = [catalog[item_id] for item_id in required]
    placed, missing = pack_room(width_mm, depth_mm, items)

    floor = width_mm * depth_mm
    occupied = sum(catalog[p.item_id].footprint_mm2 for p in placed)
    utilisation = 0 if floor <= 0 else min(100, (occupied * 100) // floor)

    return RoomFit(
        room_key=room_key,
        room_type=room_type,
        fits=not missing,
        placed=placed,
        missing=missing,
        utilisation_percent=utilisation,
    )


def fit_all(placements: Sequence[Any], catalog: Mapping[str, CatalogItem]) -> tuple[RoomFit, ...]:
    """Test every placed room. ``placements`` are ``RoomPlacement``-shaped."""
    return tuple(
        fit_room(
            placement.room_key,
            placement.room_type,
            placement.width_mm,
            placement.depth_mm,
            catalog,
        )
        for placement in placements
    )


def score(fits: Sequence[RoomFit]) -> int:
    """0-100 furniture-fit score over the habitable rooms only.

    §5.6 requires *every* habitable room to pass, so this is deliberately harsh:
    it is the share of checked habitable rooms that fit, meaning one failing
    bedroom in four drops the score to 75 and the gate (which demands 100)
    rejects the option. Rooms with no requirement are excluded rather than
    counted as passes, so a plan cannot inflate its score with store rooms.
    """
    checked = [item for item in fits if not item.unchecked and item.room_type in HABITABLE_TYPES]
    if not checked:
        return 100
    passing = sum(1 for item in checked if item.fits)
    return (passing * 100) // len(checked)


def failing_rooms(fits: Sequence[RoomFit]) -> tuple[RoomFit, ...]:
    """Habitable rooms that could not take their set — for discard reasons."""
    return tuple(
        item
        for item in fits
        if not item.unchecked and item.room_type in HABITABLE_TYPES and not item.fits
    )


__all__ = [
    "HABITABLE_TYPES",
    "REQUIRED_SETS",
    "CatalogError",
    "CatalogItem",
    "FitPlacement",
    "RoomFit",
    "default_catalog_path",
    "failing_rooms",
    "fit_all",
    "fit_room",
    "CatalogUnavailableError",
    "load_catalog",
    "pack_room",
    "required_items_for",
    "score",
]
