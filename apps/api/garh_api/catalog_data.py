"""The furniture, material and facade-kit catalogues — the data, not the route.

Two callers read this, and they must read the same bytes:

* ``GET /catalog/*`` (:mod:`garh_api.routers.catalog`) serves it to the canvas, so the
  architect places the bay the table describes.
* the compliance engine (:mod:`garh_api.compliance`) measures a placed parking bay
  against the same table, so the rectangle the rule measures is the rectangle the
  canvas drew. Two tables here would be two answers to "is this parking legal".

It lives outside ``routers/`` because that package imports FastAPI at module scope and
the compliance path has to run on a bare interpreter — ``make bare`` executes the rules,
the copilot corpus and the fixture derivations on a Python with no third-party packages.
A compliance import that reached into a router failed there with ``No module named
'fastapi'``, which is exactly what that gate exists to catch. Only the stdlib and
:mod:`garh_api.logging` (itself stubbable) may be imported here.

The router re-exports ``catalog_dir``, ``_load_catalog`` and ``_BUILTIN`` under their old
names, so nothing that already imported them from there had to change.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from garh_api.logging import get_logger
from garh_api.paths import repo_root

_log = get_logger(__name__)

#: Which of the two a payload came from, reported on every catalogue response.
SOURCE_FILES = "files"
SOURCE_BUILTIN = "builtin"


#: Where the JSON overrides live, in precedence order after ``GARH_CATALOG_DIR``.
#:
#: ``fixtures/catalog`` is in this list because that is where the files actually
#: are: ``python -m garh_api.seed`` validates them there and
#: ``apps/api/tests/test_catalog_fixtures.py`` asserts against them there. While
#: the only fallback was ``<repo>/catalog`` — a directory that has never
#: existed — a host-run API served the compiled-in table and the authored,
#: validated, tested files were decorative. docker-compose.yml now also sets
#: ``GARH_CATALOG_DIR`` explicitly, so both paths agree.
_CATALOG_DIR_CANDIDATES: tuple[str, ...] = ("catalog", os.path.join("fixtures", "catalog"))


def catalog_dir() -> str:
    override = os.environ.get("GARH_CATALOG_DIR")
    if override:
        # Relative overrides resolve against the repo root, never the cwd —
        # same rule as rulepack_dir above and garh_api.seed.catalog.
        return override if os.path.isabs(override) else os.path.join(repo_root(), override)
    root = repo_root()
    for candidate in _CATALOG_DIR_CANDIDATES:
        path = os.path.join(root, candidate)
        if os.path.isdir(path):
            return path
    # Nothing on disk: return the first candidate so the "not found" log line
    # names one definite path instead of a list of maybes.
    return os.path.join(root, _CATALOG_DIR_CANDIDATES[0])


def read_json(path: str) -> Any | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        _log.warning("catalog.read_failed", path=os.path.basename(path), error=str(exc))
        return None


@lru_cache(maxsize=4)
def load_catalog(name: str) -> tuple[str, tuple[Any, ...]]:
    """``(source, items)`` for one catalogue — files if present, else the built-in table."""
    path = os.path.join(catalog_dir(), "%s.json" % name)
    data = read_json(path) if os.path.isfile(path) else None
    items: list[Any] | None = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    if items:
        return SOURCE_FILES, tuple(items)
    return SOURCE_BUILTIN, tuple(BUILTIN[name])


def reset_catalog_cache() -> None:
    """Drop every cached catalogue. The router's ``reset_caches`` calls this."""
    load_catalog.cache_clear()


# ---------------------------------------------------------------------------
# Built-in tables (used when catalog/*.json is absent)
# ---------------------------------------------------------------------------
#
# Real Indian dimensions, from §17's seed list. Integer millimetres throughout.
# `clearanceMm` is the free space in front of an item for it to be usable — the number
# the furniture-fit gate actually tests, and the reason a 3.0m bedroom with a wardrobe
# on the wrong wall fails.

_FURNITURE: list[dict[str, Any]] = [
    # -- bedroom -----------------------------------------------------------
    {
        "id": "bed-queen",
        "name": "Queen bed",
        "category": "bed",
        "widthMm": 1525,
        "depthMm": 1900,
        "heightMm": 600,
        "roomTypes": ["bedroom", "bedroom_master", "guest_bedroom"],
        "clearanceMm": 600,
    },
    {
        "id": "bed-king",
        "name": "King bed",
        "category": "bed",
        "widthMm": 1830,
        "depthMm": 2000,
        "heightMm": 600,
        "roomTypes": ["bedroom_master"],
        "clearanceMm": 600,
    },
    {
        "id": "bed-single",
        "name": "Single bed",
        "category": "bed",
        "widthMm": 900,
        "depthMm": 1900,
        "heightMm": 600,
        "roomTypes": ["bedroom", "guest_bedroom", "servant_room"],
        "clearanceMm": 600,
    },
    {
        "id": "bunk-bed",
        "name": "Bunk bed",
        "category": "bed",
        "widthMm": 900,
        "depthMm": 1900,
        "heightMm": 1700,
        "roomTypes": ["bedroom", "guest_bedroom"],
        "clearanceMm": 600,
    },
    {
        "id": "wardrobe-2door",
        "name": "Wardrobe (2 door)",
        "category": "storage",
        "widthMm": 1200,
        "depthMm": 600,
        "heightMm": 2100,
        "roomTypes": ["bedroom", "guest_bedroom", "dress"],
        "clearanceMm": 750,
    },
    {
        "id": "wardrobe-3door",
        "name": "Wardrobe (3 door)",
        "category": "storage",
        "widthMm": 1800,
        "depthMm": 600,
        "heightMm": 2100,
        "roomTypes": ["bedroom_master", "bedroom", "dress"],
        "clearanceMm": 750,
    },
    {
        "id": "bedside-table",
        "name": "Bedside table",
        "category": "table",
        "widthMm": 450,
        "depthMm": 400,
        "heightMm": 600,
        "roomTypes": ["bedroom", "bedroom_master", "guest_bedroom"],
        "clearanceMm": 300,
    },
    {
        "id": "dressing-table",
        "name": "Dressing table",
        "category": "table",
        "widthMm": 900,
        "depthMm": 450,
        "heightMm": 1800,
        "roomTypes": ["bedroom_master", "bedroom", "dress"],
        "clearanceMm": 750,
    },
    {
        "id": "study-table",
        "name": "Study table",
        "category": "table",
        "widthMm": 1200,
        "depthMm": 600,
        "heightMm": 750,
        "roomTypes": ["study", "bedroom", "guest_bedroom"],
        "clearanceMm": 750,
    },
    {
        "id": "bookshelf",
        "name": "Bookshelf",
        "category": "storage",
        "widthMm": 900,
        "depthMm": 350,
        "heightMm": 1800,
        "roomTypes": ["study", "living", "bedroom", "store"],
        "clearanceMm": 600,
    },
    # -- living / dining ---------------------------------------------------
    {
        "id": "sofa-3seat",
        "name": "Sofa (3 seat)",
        "category": "seating",
        "widthMm": 2100,
        "depthMm": 900,
        "heightMm": 800,
        "roomTypes": ["living", "living_dining"],
        "clearanceMm": 750,
    },
    {
        "id": "sofa-2seat",
        "name": "Sofa (2 seat)",
        "category": "seating",
        "widthMm": 1500,
        "depthMm": 900,
        "heightMm": 800,
        "roomTypes": ["living", "living_dining"],
        "clearanceMm": 750,
    },
    {
        "id": "armchair",
        "name": "Armchair",
        "category": "seating",
        "widthMm": 800,
        "depthMm": 850,
        "heightMm": 800,
        "roomTypes": ["living", "study", "balcony", "terrace"],
        "clearanceMm": 600,
    },
    {
        "id": "coffee-table",
        "name": "Coffee table",
        "category": "table",
        "widthMm": 1050,
        "depthMm": 600,
        "heightMm": 400,
        "roomTypes": ["living", "living_dining"],
        "clearanceMm": 450,
    },
    {
        "id": "tv-unit",
        "name": "TV unit",
        "category": "storage",
        "widthMm": 1800,
        "depthMm": 450,
        "heightMm": 500,
        "roomTypes": ["living", "living_dining", "bedroom_master"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-4",
        "name": "Dining table (4 seat)",
        "category": "table",
        "widthMm": 1200,
        "depthMm": 750,
        "heightMm": 750,
        "roomTypes": ["dining", "living_dining"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-6",
        "name": "Dining table (6 seat)",
        "category": "table",
        "widthMm": 1500,
        "depthMm": 900,
        "heightMm": 750,
        "roomTypes": ["dining", "living_dining"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-8",
        "name": "Dining table (8 seat)",
        "category": "table",
        "widthMm": 2100,
        "depthMm": 1000,
        "heightMm": 750,
        "roomTypes": ["dining"],
        "clearanceMm": 900,
    },
    {
        "id": "dining-chair",
        "name": "Dining chair",
        "category": "seating",
        "widthMm": 450,
        "depthMm": 500,
        "heightMm": 900,
        "roomTypes": ["dining", "living_dining"],
        "clearanceMm": 600,
    },
    {
        "id": "shoe-rack",
        "name": "Shoe rack",
        "category": "storage",
        "widthMm": 900,
        "depthMm": 350,
        "heightMm": 900,
        "roomTypes": ["foyer", "living", "lobby", "store"],
        "clearanceMm": 600,
    },
    # -- kitchen / utility -------------------------------------------------
    {
        "id": "kitchen-counter",
        "name": "Kitchen counter (1m module)",
        "category": "kitchen",
        "widthMm": 1000,
        "depthMm": 600,
        "heightMm": 900,
        "roomTypes": ["kitchen"],
        "clearanceMm": 1050,
    },
    {
        "id": "kitchen-sink",
        "name": "Kitchen sink",
        "category": "kitchen",
        "widthMm": 900,
        "depthMm": 550,
        "heightMm": 200,
        "roomTypes": ["kitchen"],
        "clearanceMm": 1050,
    },
    {
        "id": "hob-4burner",
        "name": "Hob (4 burner)",
        "category": "kitchen",
        "widthMm": 600,
        "depthMm": 520,
        "heightMm": 100,
        "roomTypes": ["kitchen"],
        "clearanceMm": 1050,
    },
    {
        "id": "refrigerator",
        "name": "Refrigerator",
        "category": "appliance",
        "widthMm": 700,
        "depthMm": 700,
        "heightMm": 1800,
        "roomTypes": ["kitchen", "utility", "store"],
        "clearanceMm": 900,
    },
    {
        "id": "washing-machine",
        "name": "Washing machine",
        "category": "appliance",
        "widthMm": 600,
        "depthMm": 600,
        "heightMm": 850,
        "roomTypes": ["utility", "bath_wc"],
        "clearanceMm": 750,
    },
    {
        "id": "water-heater",
        "name": "Geyser",
        "category": "appliance",
        "widthMm": 400,
        "depthMm": 400,
        "heightMm": 600,
        "roomTypes": ["bath", "bath_wc", "utility"],
        "clearanceMm": 0,
    },
    # -- bath --------------------------------------------------------------
    {
        "id": "wc-floor",
        "name": "WC (floor mounted)",
        "category": "sanitary",
        "widthMm": 700,
        "depthMm": 400,
        "heightMm": 780,
        "roomTypes": ["bath", "wc", "bath_wc"],
        "clearanceMm": 600,
    },
    {
        "id": "wc-wall-hung",
        "name": "WC (wall hung)",
        "category": "sanitary",
        "widthMm": 550,
        "depthMm": 360,
        "heightMm": 400,
        "roomTypes": ["bath", "wc", "bath_wc"],
        "clearanceMm": 600,
    },
    {
        "id": "washbasin",
        "name": "Washbasin",
        "category": "sanitary",
        "widthMm": 550,
        "depthMm": 450,
        "heightMm": 850,
        "roomTypes": ["bath", "wc", "bath_wc", "dining"],
        "clearanceMm": 600,
    },
    {
        "id": "shower-area",
        "name": "Shower area",
        "category": "sanitary",
        "widthMm": 900,
        "depthMm": 900,
        "heightMm": 2100,
        "roomTypes": ["bath", "bath_wc"],
        "clearanceMm": 0,
    },
    {
        "id": "bathtub",
        "name": "Bathtub",
        "category": "sanitary",
        "widthMm": 1700,
        "depthMm": 750,
        "heightMm": 600,
        "roomTypes": ["bath"],
        "clearanceMm": 750,
    },
    # -- other -------------------------------------------------------------
    {
        "id": "pooja-unit",
        "name": "Pooja unit",
        "category": "storage",
        "widthMm": 900,
        "depthMm": 450,
        "heightMm": 1800,
        "roomTypes": ["pooja", "living"],
        "clearanceMm": 900,
    },
    {
        "id": "car-hatchback",
        "name": "Car (hatchback)",
        "category": "vehicle",
        "widthMm": 1700,
        "depthMm": 3800,
        "heightMm": 1500,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 600,
    },
    {
        "id": "car-sedan",
        "name": "Car (sedan / SUV)",
        "category": "vehicle",
        "widthMm": 1800,
        "depthMm": 4800,
        "heightMm": 1500,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 600,
    },
    {
        # A car SPACE, not a car: the rectangle every seeded pack's parking rule
        # measures (spaceSizeMm 2500 x 5000). The solver places it, the site plan
        # draws a car in it, the rules engine counts it (garh_api.parking_geometry).
        "id": "parking-bay",
        "name": "Car parking bay (2.5 x 5 m)",
        "category": "vehicle",
        "widthMm": 2500,
        "depthMm": 5000,
        "heightMm": 50,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 0,
    },
    {
        "id": "two-wheeler",
        "name": "Scooter / motorcycle",
        "category": "vehicle",
        "widthMm": 700,
        "depthMm": 1800,
        "heightMm": 1100,
        "roomTypes": ["garage", "stilt", "porch"],
        "clearanceMm": 450,
    },
    {
        "id": "water-tank-oht",
        "name": "Overhead water tank (1000 L)",
        "category": "service",
        "widthMm": 1100,
        "depthMm": 1100,
        "heightMm": 1300,
        "roomTypes": ["terrace"],
        "clearanceMm": 450,
    },
]

_MATERIALS: list[dict[str, Any]] = [
    {
        "id": "vitrified-tile-600",
        "name": "Vitrified tile 600×600",
        "category": "floor",
        "finish": "glossy",
        "colorHex": "#E8E4DC",
        "priceInrPerSqm": 850,
        "surfaceGroups": ["floor.interior"],
    },
    {
        "id": "vitrified-tile-800",
        "name": "Vitrified tile 800×800",
        "category": "floor",
        "finish": "matte",
        "colorHex": "#DED8CE",
        "priceInrPerSqm": 1150,
        "surfaceGroups": ["floor.interior"],
    },
    {
        "id": "granite-flooring",
        "name": "Granite flooring",
        "category": "floor",
        "finish": "polished",
        "colorHex": "#4A4A4A",
        "priceInrPerSqm": 2400,
        "surfaceGroups": ["floor.interior", "floor.stair"],
    },
    {
        "id": "kota-stone",
        "name": "Kota stone",
        "category": "floor",
        "finish": "honed",
        "colorHex": "#6E7B6B",
        "priceInrPerSqm": 900,
        "surfaceGroups": ["floor.utility", "floor.terrace"],
    },
    {
        "id": "marble-italian",
        "name": "Italian marble",
        "category": "floor",
        "finish": "polished",
        "colorHex": "#F2F0EA",
        "priceInrPerSqm": 4500,
        "surfaceGroups": ["floor.interior"],
    },
    {
        "id": "wooden-laminate",
        "name": "Wooden laminate",
        "category": "floor",
        "finish": "textured",
        "colorHex": "#8B6A45",
        "priceInrPerSqm": 1300,
        "surfaceGroups": ["floor.bedroom"],
    },
    {
        "id": "cement-ips",
        "name": "IPS cement finish",
        "category": "floor",
        "finish": "matte",
        "colorHex": "#9C9C97",
        "priceInrPerSqm": 450,
        "surfaceGroups": ["floor.utility", "floor.parking"],
    },
    {
        "id": "anti-skid-tile",
        "name": "Anti-skid ceramic tile",
        "category": "floor",
        "finish": "matte",
        "colorHex": "#C9C4BA",
        "priceInrPerSqm": 650,
        "surfaceGroups": ["floor.bath", "floor.terrace"],
    },
    {
        "id": "ceramic-wall-tile",
        "name": "Ceramic wall tile",
        "category": "wall",
        "finish": "glossy",
        "colorHex": "#F5F5F0",
        "priceInrPerSqm": 600,
        "surfaceGroups": ["wall.bath", "wall.kitchen"],
    },
    {
        "id": "interior-emulsion",
        "name": "Interior emulsion paint",
        "category": "wall",
        "finish": "matte",
        "colorHex": "#F7F4EF",
        "priceInrPerSqm": 220,
        "surfaceGroups": ["wall.interior", "ceiling.interior"],
    },
    {
        "id": "exterior-texture",
        "name": "Exterior texture paint",
        "category": "wall",
        "finish": "textured",
        "colorHex": "#E3DDD2",
        "priceInrPerSqm": 380,
        "surfaceGroups": ["wall.exterior"],
    },
    {
        "id": "exposed-brick",
        "name": "Exposed brick",
        "category": "wall",
        "finish": "natural",
        "colorHex": "#9C4A2F",
        "priceInrPerSqm": 1400,
        "surfaceGroups": ["wall.exterior", "wall.feature"],
    },
    {
        "id": "exposed-concrete",
        "name": "Exposed concrete",
        "category": "wall",
        "finish": "board-formed",
        "colorHex": "#A8A8A3",
        "priceInrPerSqm": 1600,
        "surfaceGroups": ["wall.exterior", "wall.feature"],
    },
    {
        "id": "stone-cladding",
        "name": "Natural stone cladding",
        "category": "wall",
        "finish": "split-face",
        "colorHex": "#7A6E5D",
        "priceInrPerSqm": 2200,
        "surfaceGroups": ["wall.exterior", "wall.feature"],
    },
    {
        "id": "wpc-cladding",
        "name": "WPC wood-finish cladding",
        "category": "wall",
        "finish": "wood-grain",
        "colorHex": "#7A5230",
        "priceInrPerSqm": 2000,
        "surfaceGroups": ["wall.exterior", "facade.cladding"],
    },
    {
        "id": "acp-panel",
        "name": "ACP panel",
        "category": "wall",
        "finish": "matte",
        "colorHex": "#3C3C3C",
        "priceInrPerSqm": 1800,
        "surfaceGroups": ["facade.cladding"],
    },
    {
        "id": "glass-clear",
        "name": "Clear float glass",
        "category": "glazing",
        "finish": "clear",
        "colorHex": "#CFE3E8",
        "priceInrPerSqm": 1200,
        "surfaceGroups": ["window.glazing"],
    },
    {
        "id": "glass-tinted",
        "name": "Tinted glass",
        "category": "glazing",
        "finish": "tinted",
        "colorHex": "#7E9AA3",
        "priceInrPerSqm": 1600,
        "surfaceGroups": ["window.glazing", "facade.glazing"],
    },
    {
        "id": "upvc-window",
        "name": "uPVC window frame",
        "category": "joinery",
        "finish": "matte",
        "colorHex": "#FFFFFF",
        "priceInrPerSqm": 4200,
        "surfaceGroups": ["window.frame"],
    },
    {
        "id": "aluminium-window",
        "name": "Aluminium window frame",
        "category": "joinery",
        "finish": "anodised",
        "colorHex": "#6B6B6B",
        "priceInrPerSqm": 5200,
        "surfaceGroups": ["window.frame"],
    },
    {
        "id": "teak-door",
        "name": "Teak wood door",
        "category": "joinery",
        "finish": "polished",
        "colorHex": "#6B4423",
        "priceInrPerSqm": 9000,
        "surfaceGroups": ["door.main"],
    },
    {
        "id": "flush-door",
        "name": "Flush door",
        "category": "joinery",
        "finish": "laminated",
        "colorHex": "#B08A5E",
        "priceInrPerSqm": 2600,
        "surfaceGroups": ["door.internal"],
    },
    {
        "id": "ms-railing",
        "name": "MS railing",
        "category": "railing",
        "finish": "powder-coated",
        "colorHex": "#2E2E2E",
        "priceInrPerSqm": 2800,
        "surfaceGroups": ["railing.balcony", "railing.stair"],
    },
    {
        "id": "ss-railing",
        "name": "Stainless steel railing",
        "category": "railing",
        "finish": "brushed",
        "colorHex": "#B8BCC0",
        "priceInrPerSqm": 4500,
        "surfaceGroups": ["railing.balcony", "railing.stair"],
    },
    {
        "id": "glass-railing",
        "name": "Toughened glass railing",
        "category": "railing",
        "finish": "clear",
        "colorHex": "#D6E7EC",
        "priceInrPerSqm": 6500,
        "surfaceGroups": ["railing.balcony"],
    },
    {
        "id": "clay-roof-tile",
        "name": "Mangalore clay roof tile",
        "category": "roof",
        "finish": "natural",
        "colorHex": "#B5502F",
        "priceInrPerSqm": 1100,
        "surfaceGroups": ["roof.pitched"],
    },
    {
        "id": "waterproof-membrane",
        "name": "Waterproofing membrane",
        "category": "roof",
        "finish": "matte",
        "colorHex": "#8E8E8E",
        "priceInrPerSqm": 550,
        "surfaceGroups": ["roof.flat", "floor.terrace"],
    },
]

#: The two MVP kits, exactly as §8 specifies them. ``params`` are what op 28
#: (``facade.edit_component``) may patch; the generator reads nothing else.
_FACADE_KITS: list[dict[str, Any]] = [
    {
        "id": "contemporary",
        "name": "Contemporary",
        "description": "Flat chajjas, a full-height cladding band at the stair bay, "
        "slim MS railings — monochrome with a wood accent.",
        "components": {
            "windowTrim": {"style": "flush-band", "widthMm": 100, "projectionMm": 40},
            "chajja": {
                "style": "flat",
                "projectionMm": 600,
                "thicknessMm": 100,
                "allowedProjectionsMm": [600, 750],
            },
            "parapetProfile": {"style": "banded", "heightMm": 1050, "capThicknessMm": 75},
            "claddingZones": {
                "rule": "stack full-height at entry bay",
                "materialId": "wpc-cladding",
                "widthMm": 1200,
            },
            "porch": {"style": "cantilever", "projectionMm": 1800, "thicknessMm": 200},
            "railing": {"style": "ms-slim", "heightMm": 1050, "materialId": "ms-railing"},
        },
        "colorways": [
            {
                "id": "mono-wood",
                "name": "Monochrome + wood",
                "base": "#F2F0EB",
                "accent": "#7A5230",
                "trim": "#2E2E2E",
            },
            {
                "id": "warm-grey",
                "name": "Warm grey",
                "base": "#DAD5CC",
                "accent": "#8B6A45",
                "trim": "#3C3C3C",
            },
        ],
        "rules": {
            "minFacadeWidthMm": 4500,
            "chajjaOverOpenings": ["window", "door"],
            "claddingBayPickedBy": "stair-adjacent external wall",
        },
    },
    {
        "id": "modern-minimal",
        "name": "Modern Minimal",
        "description": "Recessed windows with a hidden chajja, a plain parapet and a "
        "glass railing — white and grey.",
        "components": {
            "windowTrim": {"style": "recessed", "widthMm": 0, "projectionMm": -75},
            "chajja": {
                "style": "hidden",
                "projectionMm": 600,
                "thicknessMm": 75,
                "allowedProjectionsMm": [600],
            },
            "parapetProfile": {"style": "plain", "heightMm": 1050, "capThicknessMm": 50},
            "claddingZones": {"rule": "none", "materialId": None, "widthMm": 0},
            "porch": {"style": "flush", "projectionMm": 1200, "thicknessMm": 150},
            "railing": {"style": "glass", "heightMm": 1050, "materialId": "glass-railing"},
        },
        "colorways": [
            {
                "id": "white-grey",
                "name": "White + grey",
                "base": "#FFFFFF",
                "accent": "#8E8E8E",
                "trim": "#6B6B6B",
            },
            {
                "id": "off-white",
                "name": "Off white",
                "base": "#F5F3EE",
                "accent": "#A8A8A3",
                "trim": "#3C3C3C",
            },
        ],
        "rules": {
            "minFacadeWidthMm": 4500,
            "chajjaOverOpenings": ["window"],
            "recessDepthMm": 150,
        },
    },
]

BUILTIN: dict[str, list[dict[str, Any]]] = {
    "furniture": _FURNITURE,
    "materials": _MATERIALS,
    "facade-kits": _FACADE_KITS,
}

__all__ = [
    "BUILTIN",
    "SOURCE_BUILTIN",
    "SOURCE_FILES",
    "catalog_dir",
    "load_catalog",
    "read_json",
    "reset_catalog_cache",
]
