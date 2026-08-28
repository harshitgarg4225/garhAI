#!/usr/bin/env python3
"""Expand parametric furniture families into catalogue entries.

Why families rather than a longer hand-written list
---------------------------------------------------
A block library gets its depth from *series*, not from invention. A hinged wardrobe
is one design at eight widths; a dining table is one design at four seat counts in
two shapes. Authoring each of those by hand is how a catalogue ends up with 229
items, inconsistent clearances and three spellings of "wardrobe". Declaring the
family once and expanding it is how it ends up with hundreds that agree.

Every dimension here is authored from Indian residential practice and IS sizing
conventions — nothing is scraped, so there is no licence question and no attribution
to carry. That matters: the two large CC0 libraries that would otherwise be worth
pulling (ambientCG, Poly Haven) are unreachable from the build sandbox, and every
"free" 2D symbol set worth having is attribution- or share-alike-licensed.

Append-only, and idempotent
---------------------------
The 229 hand-authored entries already in ``furniture.json`` are never rewritten: this
script reads the file, keeps every existing row byte-identical, and appends only ids
that are not already present. Running it twice changes nothing the second time, which
is what makes it safe to run in CI as a check rather than only as a generator.

    python3 scripts/expand_catalog.py            # report what would be added
    python3 scripts/expand_catalog.py --write    # add it
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CATALOG = os.path.join(_ROOT, "fixtures", "catalog", "furniture.json")

# Room-type shorthands, so a family declaration stays readable.
BEDROOMS = ["bedroom_master", "bedroom", "guest_bedroom"]
BEDROOMS_ALL = BEDROOMS + ["servant_room"]
LIVING = ["living", "living_dining"]
DINING = ["dining", "living_dining"]
BATHS = ["bath", "bath_wc"]
WCS = ["wc", "bath_wc"]
BATHS_ALL = ["bath", "wc", "bath_wc"]
PARKING = ["garage", "stilt", "porch"]
OUTDOOR = ["balcony", "terrace", "porch"]


def _slug(value: Any) -> str:
    """A catalogue id fragment: lowercase, digits and single hyphens only."""
    text = str(value).lower().replace("_", "-").replace(".", "-").replace(" ", "-")
    out = []
    for char in text:
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


class Family:
    """One design, expanded across a series of sizes.

    ``series`` is a list of ``(key, label, width, depth, height)``. ``key`` becomes
    the id suffix and ``label`` the name suffix, so both read naturally at every size
    without a second list to keep in step.
    """

    def __init__(
        self,
        *,
        stem: str,
        name: str,
        category: str,
        room_types: list[str],
        clearance: int,
        series: list[tuple[str, str, int, int, int]],
    ) -> None:
        self.stem = stem
        self.name = name
        self.category = category
        self.room_types = room_types
        self.clearance = clearance
        self.series = series

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for key, label, width, depth, height in self.series:
            suffix = _slug(key)
            out.append(
                {
                    "id": "%s-%s" % (self.stem, suffix) if suffix else self.stem,
                    "name": "%s %s" % (self.name, label) if label else self.name,
                    "category": self.category,
                    "widthMm": int(width),
                    "depthMm": int(depth),
                    "heightMm": int(height),
                    "clearanceMm": int(self.clearance),
                    "roomTypes": list(self.room_types),
                }
            )
        return out


def _widths(
    widths: list[int], depth: int, height: int, *, unit: str = "mm"
) -> list[tuple[str, str, int, int, int]]:
    """A series that varies only in width — the commonest case by far."""
    return [(str(w), "%d %s" % (w, unit), w, depth, height) for w in widths]


# ---------------------------------------------------------------------------
# The families
# ---------------------------------------------------------------------------
def families() -> list[Family]:  # noqa: PLR0915 - a catalogue is a long list by nature
    f: list[Family] = []

    # -- beds -------------------------------------------------------------
    f.append(
        Family(
            stem="cot-single-storage",
            name="Cot, single, with storage",
            category="bed",
            room_types=BEDROOMS_ALL,
            clearance=600,
            series=_widths([900, 1000, 1050], 1900, 600),
        )
    )
    f.append(
        Family(
            stem="cot-double-storage",
            name="Cot, double, with storage",
            category="bed",
            room_types=BEDROOMS,
            clearance=600,
            series=_widths([1350, 1500, 1650, 1800], 2000, 600),
        )
    )
    f.append(
        Family(
            stem="cot-hydraulic",
            name="Cot, hydraulic storage",
            category="bed",
            room_types=BEDROOMS,
            clearance=600,
            series=_widths([1500, 1650, 1800], 2000, 650),
        )
    )
    f.append(
        Family(
            stem="cot-poster",
            name="Cot, four-poster",
            category="bed",
            room_types=["bedroom_master"],
            clearance=700,
            series=_widths([1650, 1800], 2050, 2100),
        )
    )
    f.append(
        Family(
            stem="diwan",
            name="Diwan",
            category="bed",
            room_types=LIVING + ["guest_bedroom", "study"],
            clearance=450,
            series=_widths([900, 1050, 1200], 1900, 450),
        )
    )
    f.append(
        Family(
            stem="sofa-cum-bed",
            name="Sofa cum bed",
            category="bed",
            room_types=LIVING + ["guest_bedroom", "study"],
            clearance=900,
            series=_widths([1600, 1800, 2000], 900, 800),
        )
    )
    f.append(
        Family(
            stem="cot-kids",
            name="Cot, children's",
            category="bed",
            room_types=["bedroom"],
            clearance=550,
            series=_widths([750, 900], 1700, 550),
        )
    )
    f.append(
        Family(
            stem="cot-trundle",
            name="Cot, trundle",
            category="bed",
            room_types=["bedroom", "guest_bedroom"],
            clearance=600,
            series=_widths([900, 1000], 1900, 450),
        )
    )
    f.append(
        Family(
            stem="mattress",
            name="Mattress",
            category="bed",
            room_types=BEDROOMS_ALL,
            clearance=0,
            series=[
                ("single", "single", 900, 1900, 150),
                ("double", "double", 1350, 1900, 200),
                ("queen", "queen", 1500, 2000, 200),
                ("king", "king", 1800, 2000, 200),
            ],
        )
    )

    # -- storage ----------------------------------------------------------
    f.append(
        Family(
            stem="wardrobe-hinged",
            name="Wardrobe, hinged",
            category="storage",
            room_types=BEDROOMS_ALL + ["dress"],
            clearance=700,
            series=_widths([600, 900, 1200, 1500, 1800, 2100, 2400], 600, 2100),
        )
    )
    f.append(
        Family(
            stem="wardrobe-sliding",
            name="Wardrobe, sliding",
            category="storage",
            room_types=BEDROOMS + ["dress"],
            clearance=450,
            series=_widths([1200, 1500, 1800, 2100, 2400, 2700, 3000], 650, 2250),
        )
    )
    f.append(
        Family(
            stem="wardrobe-walkin",
            name="Wardrobe, walk-in run",
            category="storage",
            room_types=["dress", "bedroom_master"],
            clearance=900,
            series=_widths([1800, 2400, 3000], 600, 2400),
        )
    )
    f.append(
        Family(
            stem="loft-unit",
            name="Loft unit",
            category="storage",
            room_types=BEDROOMS_ALL + ["store", "passage"],
            clearance=0,
            series=_widths([900, 1200, 1500, 1800, 2100], 600, 600),
        )
    )
    f.append(
        Family(
            stem="chest-drawers",
            name="Chest of drawers",
            category="storage",
            room_types=BEDROOMS_ALL,
            clearance=750,
            series=[
                ("3", "3-drawer", 800, 450, 750),
                ("4", "4-drawer", 800, 450, 950),
                ("5", "5-drawer", 900, 480, 1200),
                ("6", "6-drawer", 1000, 480, 1350),
            ],
        )
    )
    f.append(
        Family(
            stem="bookshelf-open",
            name="Bookshelf, open",
            category="storage",
            room_types=["study", "living", "living_dining", "bedroom"],
            clearance=600,
            series=[
                ("900x1200", "900 × 1200", 900, 300, 1200),
                ("900x1800", "900 × 1800", 900, 300, 1800),
                ("1200x1800", "1200 × 1800", 1200, 350, 1800),
                ("1200x2100", "1200 × 2100", 1200, 350, 2100),
                ("1800x2100", "1800 × 2100", 1800, 350, 2100),
            ],
        )
    )
    f.append(
        Family(
            stem="tv-unit-wall",
            name="TV unit, wall-mounted",
            category="storage",
            room_types=LIVING + BEDROOMS,
            clearance=0,
            series=_widths([1200, 1500, 1800, 2100, 2400], 400, 500),
        )
    )
    f.append(
        Family(
            stem="crockery-unit",
            name="Crockery unit",
            category="storage",
            room_types=DINING + ["kitchen"],
            clearance=750,
            series=_widths([900, 1200, 1500], 450, 2100),
        )
    )
    f.append(
        Family(
            stem="shoe-rack",
            name="Shoe rack",
            category="storage",
            room_types=["foyer", "lobby", "passage"],
            clearance=600,
            series=_widths([600, 900, 1200], 350, 900),
        )
    )
    f.append(
        Family(
            stem="pooja-unit",
            name="Pooja unit",
            category="storage",
            room_types=["pooja", "living", "kitchen"],
            clearance=750,
            series=_widths([600, 900, 1200], 450, 2100),
        )
    )
    f.append(
        Family(
            stem="storage-rack-utility",
            name="Storage rack, utility",
            category="storage",
            room_types=["store", "utility", "servant_room"],
            clearance=600,
            series=_widths([600, 900, 1200, 1500], 450, 1800),
        )
    )
    f.append(
        Family(
            stem="wall-shelf",
            name="Wall shelf",
            category="storage",
            room_types=LIVING + BEDROOMS + ["study", "kitchen"],
            clearance=0,
            series=_widths([600, 900, 1200], 250, 250),
        )
    )
    f.append(
        Family(
            stem="sideboard",
            name="Sideboard",
            category="storage",
            room_types=DINING + LIVING,
            clearance=750,
            series=_widths([1200, 1500, 1800], 450, 850),
        )
    )

    # -- tables -----------------------------------------------------------
    f.append(
        Family(
            stem="dining-table-rect",
            name="Dining table, rectangular",
            category="table",
            room_types=DINING,
            clearance=900,
            series=[
                ("2", "2-seater", 750, 750, 750),
                ("4", "4-seater", 1200, 750, 750),
                ("6", "6-seater", 1650, 900, 750),
                ("8", "8-seater", 2100, 1000, 750),
                ("10", "10-seater", 2700, 1100, 750),
            ],
        )
    )
    f.append(
        Family(
            stem="dining-table-round",
            name="Dining table, round",
            category="table",
            room_types=DINING,
            clearance=900,
            series=[
                ("2", "2-seater", 750, 750, 750),
                ("4", "4-seater", 1050, 1050, 750),
                ("6", "6-seater", 1350, 1350, 750),
                ("8", "8-seater", 1500, 1500, 750),
            ],
        )
    )
    f.append(
        Family(
            stem="coffee-table-rect",
            name="Coffee table, rectangular",
            category="table",
            room_types=LIVING,
            clearance=450,
            series=[
                ("900", "900", 900, 500, 400),
                ("1050", "1050", 1050, 550, 400),
                ("1200", "1200", 1200, 600, 420),
            ],
        )
    )
    f.append(
        Family(
            stem="study-desk",
            name="Study desk",
            category="table",
            room_types=["study", "bedroom", "bedroom_master"],
            clearance=750,
            series=_widths([900, 1050, 1200, 1500, 1800], 600, 750),
        )
    )
    f.append(
        Family(
            stem="office-desk-l",
            name="Office desk, L-shaped",
            category="table",
            room_types=["study"],
            clearance=900,
            series=[
                ("1500", "1500 × 1500", 1500, 1500, 750),
                ("1800", "1800 × 1500", 1800, 1500, 750),
            ],
        )
    )
    f.append(
        Family(
            stem="console-table",
            name="Console table",
            category="table",
            room_types=["foyer", "lobby", "living", "passage"],
            clearance=600,
            series=_widths([900, 1050, 1200], 350, 800),
        )
    )
    f.append(
        Family(
            stem="bedside-cabinet",
            name="Bedside cabinet",
            category="table",
            room_types=BEDROOMS_ALL,
            clearance=450,
            series=_widths([400, 450, 500, 600], 400, 550),
        )
    )
    f.append(
        Family(
            stem="dressing-unit",
            name="Dressing unit",
            category="table",
            room_types=BEDROOMS + ["dress"],
            clearance=750,
            series=_widths([750, 900, 1050], 450, 1800),
        )
    )
    f.append(
        Family(
            stem="side-table",
            name="Side table",
            category="table",
            room_types=LIVING + BEDROOMS,
            clearance=400,
            series=[("400", "400", 400, 400, 500), ("500", "500", 500, 500, 550)],
        )
    )
    f.append(
        Family(
            stem="folding-table",
            name="Folding table",
            category="table",
            room_types=DINING + ["utility", "balcony", "terrace"],
            clearance=750,
            series=_widths([600, 900, 1200], 600, 750),
        )
    )

    # -- seating ----------------------------------------------------------
    f.append(
        Family(
            stem="sofa-straight",
            name="Sofa, straight",
            category="seating",
            room_types=LIVING,
            clearance=900,
            series=[
                ("1", "1-seater", 900, 900, 800),
                ("2", "2-seater", 1500, 900, 800),
                ("3", "3-seater", 2100, 900, 800),
                ("4", "4-seater", 2700, 900, 800),
            ],
        )
    )
    f.append(
        Family(
            stem="sofa-l",
            name="Sofa, L-shaped",
            category="seating",
            room_types=LIVING,
            clearance=900,
            series=[
                ("small", "compact", 2100, 1600, 800),
                ("medium", "medium", 2500, 1800, 800),
                ("large", "large", 2900, 2000, 800),
            ],
        )
    )
    f.append(
        Family(
            stem="recliner",
            name="Recliner",
            category="seating",
            room_types=LIVING,
            clearance=1100,
            series=[("single", "single", 900, 950, 1050), ("double", "twin", 1750, 950, 1050)],
        )
    )
    f.append(
        Family(
            stem="dining-chair-std",
            name="Dining chair",
            category="seating",
            room_types=DINING,
            clearance=750,
            series=[("std", "", 450, 500, 900), ("arm", "with arms", 550, 550, 900)],
        )
    )
    f.append(
        Family(
            stem="office-chair",
            name="Office chair",
            category="seating",
            room_types=["study"],
            clearance=900,
            series=[("task", "task", 600, 600, 1050), ("exec", "executive", 700, 700, 1200)],
        )
    )
    f.append(
        Family(
            stem="stool",
            name="Stool",
            category="seating",
            room_types=DINING + ["kitchen", "utility", "bath"],
            clearance=450,
            series=[
                ("low", "low", 350, 350, 450),
                ("counter", "counter height", 400, 400, 650),
                ("bar", "bar height", 400, 400, 750),
            ],
        )
    )
    f.append(
        Family(
            stem="bench",
            name="Bench",
            category="seating",
            room_types=DINING + ["foyer", "balcony", "terrace", "porch"],
            clearance=600,
            series=_widths([900, 1200, 1500, 1800], 400, 450),
        )
    )
    f.append(
        Family(
            stem="jhoola",
            name="Jhoola (swing)",
            category="seating",
            room_types=LIVING + OUTDOOR,
            clearance=900,
            series=[("single", "single", 900, 700, 2000), ("double", "double", 1500, 800, 2000)],
        )
    )
    f.append(
        Family(
            stem="bean-bag",
            name="Bean bag",
            category="seating",
            room_types=LIVING + ["bedroom", "study"],
            clearance=400,
            series=[("m", "medium", 750, 750, 750), ("l", "large", 900, 900, 900)],
        )
    )
    f.append(
        Family(
            stem="pouffe",
            name="Pouffe",
            category="seating",
            room_types=LIVING + BEDROOMS,
            clearance=350,
            series=[("round", "round", 450, 450, 400), ("square", "square", 500, 500, 400)],
        )
    )

    # -- kitchen ----------------------------------------------------------
    f.append(
        Family(
            stem="kitchen-base",
            name="Kitchen base unit",
            category="kitchen",
            room_types=["kitchen"],
            clearance=900,
            series=_widths([300, 450, 600, 750, 900, 1200], 600, 850),
        )
    )
    f.append(
        Family(
            stem="kitchen-wall",
            name="Kitchen wall unit",
            category="kitchen",
            room_types=["kitchen"],
            clearance=0,
            series=_widths([300, 450, 600, 750, 900, 1200], 350, 700),
        )
    )
    f.append(
        Family(
            stem="kitchen-tall",
            name="Kitchen tall unit",
            category="kitchen",
            room_types=["kitchen"],
            clearance=900,
            series=_widths([450, 600, 750, 900], 600, 2100),
        )
    )
    f.append(
        Family(
            stem="kitchen-corner",
            name="Kitchen corner unit",
            category="kitchen",
            room_types=["kitchen"],
            clearance=900,
            series=[("l", "L-corner", 900, 900, 850), ("carousel", "carousel", 1000, 1000, 850)],
        )
    )
    f.append(
        Family(
            stem="kitchen-sink-ss",
            name="Kitchen sink, stainless",
            category="kitchen",
            room_types=["kitchen", "utility"],
            clearance=750,
            series=[
                ("single", "single bowl", 600, 450, 200),
                ("single-drain", "single bowl with drainboard", 900, 450, 200),
                ("double", "double bowl", 1000, 500, 200),
                ("double-drain", "double bowl with drainboard", 1200, 500, 200),
            ],
        )
    )
    f.append(
        Family(
            stem="hob",
            name="Hob",
            category="kitchen",
            room_types=["kitchen"],
            clearance=750,
            series=[
                ("2", "2-burner", 600, 500, 100),
                ("3", "3-burner", 700, 500, 100),
                ("4", "4-burner", 800, 520, 100),
                ("5", "5-burner", 900, 520, 100),
            ],
        )
    )
    f.append(
        Family(
            stem="chimney",
            name="Chimney",
            category="kitchen",
            room_types=["kitchen"],
            clearance=0,
            series=_widths([600, 750, 900], 500, 600),
        )
    )
    f.append(
        Family(
            stem="breakfast-counter",
            name="Breakfast counter",
            category="kitchen",
            room_types=["kitchen", "living_dining"],
            clearance=900,
            series=_widths([1200, 1500, 1800], 600, 900),
        )
    )
    f.append(
        Family(
            stem="kitchen-island",
            name="Kitchen island",
            category="kitchen",
            room_types=["kitchen"],
            clearance=1000,
            series=[
                ("1200", "1200", 1200, 900, 900),
                ("1800", "1800", 1800, 900, 900),
                ("2400", "2400", 2400, 1000, 900),
            ],
        )
    )

    # -- appliances -------------------------------------------------------
    f.append(
        Family(
            stem="refrigerator-sd",
            name="Refrigerator, single door",
            category="appliance",
            room_types=["kitchen", "utility"],
            clearance=900,
            series=[("190l", "190 L", 550, 600, 1200), ("250l", "250 L", 600, 620, 1450)],
        )
    )
    f.append(
        Family(
            stem="refrigerator-dd",
            name="Refrigerator, double door",
            category="appliance",
            room_types=["kitchen", "utility"],
            clearance=900,
            series=[("300l", "300 L", 600, 650, 1700), ("400l", "400 L", 700, 700, 1800)],
        )
    )
    f.append(
        Family(
            stem="refrigerator-sbs",
            name="Refrigerator, side-by-side",
            category="appliance",
            room_types=["kitchen"],
            clearance=1000,
            series=[("600l", "600 L", 900, 750, 1800), ("700l", "700 L", 950, 750, 1800)],
        )
    )
    f.append(
        Family(
            stem="washing-machine-tl",
            name="Washing machine, top load",
            category="appliance",
            room_types=["utility", "bath", "servant_room"],
            clearance=750,
            series=[("6kg", "6 kg", 550, 570, 950), ("8kg", "8 kg", 600, 620, 1000)],
        )
    )
    f.append(
        Family(
            stem="washing-machine-fl",
            name="Washing machine, front load",
            category="appliance",
            room_types=["utility", "bath", "kitchen"],
            clearance=900,
            series=[("6kg", "6 kg", 600, 550, 850), ("8kg", "8 kg", 600, 600, 850)],
        )
    )
    f.append(
        Family(
            stem="dishwasher",
            name="Dishwasher",
            category="appliance",
            room_types=["kitchen", "utility"],
            clearance=900,
            series=[("600", "600", 600, 600, 850)],
        )
    )
    f.append(
        Family(
            stem="television",
            name="Television",
            category="appliance",
            room_types=LIVING + BEDROOMS,
            clearance=0,
            series=[
                ("43", '43"', 970, 80, 570),
                ("50", '50"', 1120, 80, 650),
                ("55", '55"', 1230, 80, 710),
                ("65", '65"', 1450, 80, 830),
                ("75", '75"', 1680, 80, 960),
            ],
        )
    )
    f.append(
        Family(
            stem="ac-split-indoor",
            name="AC, split indoor unit",
            category="appliance",
            room_types=BEDROOMS_ALL + LIVING + DINING + ["study"],
            clearance=0,
            series=[
                ("1t", "1.0 ton", 800, 200, 290),
                ("1.5t", "1.5 ton", 1000, 220, 300),
                ("2t", "2.0 ton", 1100, 240, 320),
            ],
        )
    )
    f.append(
        Family(
            stem="ac-window",
            name="AC, window unit",
            category="appliance",
            room_types=BEDROOMS_ALL + LIVING,
            clearance=0,
            series=[("1t", "1.0 ton", 660, 560, 400), ("1.5t", "1.5 ton", 700, 600, 430)],
        )
    )
    f.append(
        Family(
            stem="water-heater",
            name="Water heater",
            category="appliance",
            room_types=BATHS_ALL + ["kitchen", "utility"],
            clearance=0,
            series=[
                ("6l", "6 L", 340, 340, 340),
                ("15l", "15 L", 400, 400, 400),
                ("25l", "25 L", 450, 450, 450),
            ],
        )
    )
    f.append(
        Family(
            stem="water-purifier",
            name="Water purifier",
            category="appliance",
            room_types=["kitchen", "utility", "dining"],
            clearance=0,
            series=[("wall", "wall-mounted", 350, 230, 500)],
        )
    )
    f.append(
        Family(
            stem="microwave",
            name="Microwave oven",
            category="appliance",
            room_types=["kitchen"],
            clearance=0,
            series=[("20l", "20 L", 480, 380, 280), ("30l", "30 L", 540, 420, 320)],
        )
    )

    # -- sanitary ---------------------------------------------------------
    f.append(
        Family(
            stem="wc-floor-mounted",
            name="WC, floor mounted",
            category="sanitary",
            room_types=WCS,
            clearance=600,
            series=[("s-trap", "S-trap", 380, 680, 780), ("p-trap", "P-trap", 380, 700, 780)],
        )
    )
    f.append(
        Family(
            stem="wc-wall-mounted",
            name="WC, wall mounted",
            category="sanitary",
            room_types=WCS,
            clearance=600,
            series=[("std", "", 370, 540, 400)],
        )
    )
    f.append(
        Family(
            stem="wc-indian",
            name="WC, Indian pan",
            category="sanitary",
            room_types=WCS,
            clearance=600,
            series=[("580", "580", 440, 580, 200), ("680", "680", 480, 680, 200)],
        )
    )
    f.append(
        Family(
            stem="washbasin-counter",
            name="Washbasin, counter-top",
            category="sanitary",
            room_types=BATHS_ALL,
            clearance=600,
            series=[
                ("round", "round", 400, 400, 150),
                ("oval", "oval", 550, 400, 150),
                ("rect", "rectangular", 600, 400, 150),
            ],
        )
    )
    f.append(
        Family(
            stem="washbasin-pedestal",
            name="Washbasin, pedestal",
            category="sanitary",
            room_types=BATHS_ALL,
            clearance=600,
            series=[("550", "550", 550, 450, 850), ("600", "600", 600, 480, 850)],
        )
    )
    f.append(
        Family(
            stem="washbasin-wall",
            name="Washbasin, wall hung",
            category="sanitary",
            room_types=BATHS_ALL,
            clearance=600,
            series=[("450", "450", 450, 350, 200), ("550", "550", 550, 400, 200)],
        )
    )
    f.append(
        Family(
            stem="vanity-unit",
            name="Vanity unit",
            category="sanitary",
            room_types=BATHS_ALL,
            clearance=750,
            series=_widths([600, 750, 900, 1200], 500, 850),
        )
    )
    f.append(
        Family(
            stem="shower-enclosure",
            name="Shower enclosure",
            category="sanitary",
            room_types=BATHS,
            clearance=0,
            series=[
                ("900", "900 × 900", 900, 900, 2000),
                ("1000", "1000 × 1000", 1000, 1000, 2000),
                ("1200x900", "1200 × 900", 1200, 900, 2000),
            ],
        )
    )
    f.append(
        Family(
            stem="bathtub",
            name="Bathtub",
            category="sanitary",
            room_types=BATHS,
            clearance=750,
            series=[("1500", "1500", 1500, 750, 550), ("1700", "1700", 1700, 800, 550)],
        )
    )
    f.append(
        Family(
            stem="urinal",
            name="Urinal",
            category="sanitary",
            room_types=WCS,
            clearance=600,
            series=[("std", "", 350, 350, 600)],
        )
    )

    # -- vehicles ---------------------------------------------------------
    f.append(
        Family(
            stem="car",
            name="Car",
            category="vehicle",
            room_types=PARKING,
            clearance=600,
            series=[
                ("hatch-small", "small hatchback", 1500, 3600, 1500),
                ("hatch", "hatchback", 1700, 3900, 1550),
                ("sedan-compact", "compact sedan", 1700, 3990, 1500),
                ("sedan", "sedan", 1800, 4500, 1500),
                ("suv-compact", "compact SUV", 1800, 4000, 1650),
                ("suv", "SUV", 1900, 4700, 1800),
                ("muv", "MUV", 1850, 4600, 1800),
            ],
        )
    )
    f.append(
        Family(
            stem="two-wheeler-family",
            name="Two-wheeler",
            category="vehicle",
            room_types=PARKING + ["stilt"],
            clearance=400,
            series=[
                ("scooter", "scooter", 700, 1800, 1150),
                ("motorcycle", "motorcycle", 800, 2050, 1100),
            ],
        )
    )
    f.append(
        Family(
            stem="bicycle",
            name="Bicycle",
            category="vehicle",
            room_types=PARKING + ["balcony", "store"],
            clearance=400,
            series=[("std", "", 600, 1750, 1050)],
        )
    )

    # -- services ---------------------------------------------------------
    f.append(
        Family(
            stem="water-tank-ugt",
            name="Water tank, underground",
            category="service",
            room_types=["stilt", "porch", "garage"],
            clearance=600,
            series=[("5000l", "5000 L", 2000, 1500, 1700), ("10000l", "10000 L", 2500, 2000, 2000)],
        )
    )
    f.append(
        Family(
            stem="water-tank-loft",
            name="Water tank, overhead",
            category="service",
            room_types=["terrace"],
            clearance=600,
            series=[
                ("500l", "500 L", 900, 900, 900),
                ("1000l", "1000 L", 1100, 1100, 1150),
                ("1500l", "1500 L", 1250, 1250, 1300),
            ],
        )
    )
    f.append(
        Family(
            stem="septic-tank",
            name="Septic tank",
            category="service",
            room_types=["stilt", "porch"],
            clearance=600,
            series=[("2000l", "2000 L", 2000, 1000, 1500), ("3000l", "3000 L", 2500, 1200, 1500)],
        )
    )
    f.append(
        Family(
            stem="electrical-panel",
            name="Electrical distribution board",
            category="service",
            room_types=["foyer", "passage", "utility", "lobby"],
            clearance=750,
            series=[
                ("8way", "8-way", 300, 120, 400),
                ("12way", "12-way", 400, 120, 450),
                ("16way", "16-way", 500, 150, 500),
            ],
        )
    )
    f.append(
        Family(
            stem="water-pump",
            name="Water pump",
            category="service",
            room_types=["utility", "stilt", "terrace"],
            clearance=600,
            series=[("0.5hp", "0.5 HP", 400, 250, 300), ("1hp", "1.0 HP", 500, 300, 350)],
        )
    )
    f.append(
        Family(
            stem="ac-outdoor",
            name="AC outdoor unit",
            category="service",
            room_types=OUTDOOR + ["utility", "terrace"],
            clearance=500,
            series=[
                ("1t", "1.0 ton", 780, 300, 550),
                ("1.5t", "1.5 ton", 850, 320, 600),
                ("2t", "2.0 ton", 900, 350, 700),
            ],
        )
    )
    f.append(
        Family(
            stem="solar-heater",
            name="Solar water heater",
            category="service",
            room_types=["terrace"],
            clearance=900,
            series=[("100l", "100 L", 2000, 1300, 1500), ("200l", "200 L", 2400, 1600, 1700)],
        )
    )
    f.append(
        Family(
            stem="inverter-unit",
            name="Inverter with battery",
            category="service",
            room_types=["utility", "store", "passage"],
            clearance=600,
            series=[
                ("single", "single battery", 600, 400, 900),
                ("double", "double battery", 900, 400, 900),
            ],
        )
    )

    return f


MATERIALS = os.path.join(_ROOT, "fixtures", "catalog", "materials.json")


class MaterialFamily:
    """One material in a series of finishes, shades or sizes.

    ``texture`` names the procedural recipe this material *will* be rendered with
    once the generator lands (build item A-1): an Indian palette is mostly stone,
    tile, plaster and timber, all better described by a generator — grain
    direction, grout width, speckle density — than by a photograph we would have to
    licence, host and ship. It is carried on the family and deliberately NOT written
    into the catalogue row yet, because nothing reads it: a field no consumer looks
    at is dead data, and this catalogue's own validator says so about surfaceGroups.
    """

    def __init__(
        self,
        *,
        stem: str,
        name: str,
        category: str,
        surface_groups: list[str],
        texture: str,
        series: list[tuple[str, str, str, str, int]],
    ) -> None:
        self.stem = stem
        self.name = name
        self.category = category
        self.surface_groups = surface_groups
        self.texture = texture
        self.series = series

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for key, label, finish, color, price in self.series:
            out.append(
                {
                    "id": "%s-%s" % (self.stem, _slug(key)),
                    "name": "%s %s" % (self.name, label) if label else self.name,
                    "category": self.category,
                    "finish": finish,
                    "colorHex": color,
                    "priceInrPerSqm": int(price),
                    "surfaceGroups": list(self.surface_groups),
                }
            )
        return out


FLOOR_INT = ["floor.interior", "floor.bedroom"]
FLOOR_WET = ["floor.bath", "floor.utility"]
WALL_INT = ["wall.interior"]
WALL_EXT = ["wall.exterior", "facade.cladding"]


def material_families() -> list[MaterialFamily]:  # noqa: PLR0915
    m: list[MaterialFamily] = []

    m.append(
        MaterialFamily(
            stem="vitrified",
            name="Vitrified tile",
            category="floor",
            surface_groups=FLOOR_INT + ["floor.stair"],
            texture="tile",
            series=[
                ("ivory-600", "600 ivory", "glossy", "#EFEAE0", 780),
                ("beige-600", "600 beige", "glossy", "#E3D8C6", 800),
                ("grey-600", "600 grey", "matte", "#C9CACB", 820),
                ("charcoal-600", "600 charcoal", "matte", "#4A4E52", 860),
                ("carrara-800", "800 carrara", "polished", "#F1F1EE", 1150),
                ("statuario-800", "800 statuario", "polished", "#EDEDE8", 1280),
                ("wood-1200", "1200 wood-look", "wood-grain", "#B58A5C", 1050),
                ("concrete-800", "800 concrete-look", "matte", "#B7B5B0", 980),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="ceramic",
            name="Ceramic tile",
            category="floor",
            surface_groups=FLOOR_WET,
            texture="tile",
            series=[
                ("white-300", "300 white", "glossy", "#F4F4F2", 420),
                ("grey-300", "300 grey", "matte", "#C4C6C7", 440),
                ("anti-skid-300", "300 anti-skid", "textured", "#BFB9AE", 480),
                ("beige-450", "450 beige", "matte", "#DED2BE", 520),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="kota",
            name="Kota stone",
            category="floor",
            surface_groups=FLOOR_INT + ["floor.stair", "floor.utility", "floor.parking"],
            texture="stone",
            series=[
                ("blue-honed", "blue, honed", "honed", "#5B6B66", 520),
                ("blue-polished", "blue, polished", "polished", "#54655F", 620),
                ("brown-honed", "brown, honed", "honed", "#7A6A52", 540),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="granite",
            name="Granite",
            category="floor",
            surface_groups=FLOOR_INT + ["floor.stair", "wall.kitchen"],
            texture="speckle",
            series=[
                ("black-galaxy", "Black Galaxy", "polished", "#1A1A1C", 2400),
                ("absolute-black", "Absolute Black", "polished", "#141416", 1900),
                ("tan-brown", "Tan Brown", "polished", "#5A3A32", 1650),
                ("steel-grey", "Steel Grey", "polished", "#59595B", 1450),
                ("kashmir-white", "Kashmir White", "polished", "#D8D2C6", 1750),
                ("flamed-grey", "grey, flamed", "flamed", "#6E6E70", 1300),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="marble",
            name="Marble",
            category="floor",
            surface_groups=FLOOR_INT + ["floor.pooja", "wall.feature"],
            texture="vein",
            series=[
                ("makrana", "Makrana", "polished", "#F0EDE4", 2800),
                ("ambaji", "Ambaji", "polished", "#EDEAE1", 2200),
                ("italian-statuario", "Italian Statuario", "polished", "#F3F2EE", 6500),
                ("green-udaipur", "Udaipur green", "polished", "#4A5F4A", 1900),
                ("rainforest-brown", "Rainforest brown", "honed", "#6B5540", 3200),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="ips",
            name="IPS finish",
            category="floor",
            surface_groups=FLOOR_INT + ["floor.utility", "floor.terrace", "floor.parking"],
            texture="concrete",
            series=[
                ("grey", "grey", "sealed", "#A8A6A1", 320),
                ("red-oxide", "red oxide", "polished", "#8C4634", 380),
                ("black-oxide", "black oxide", "polished", "#3A3A3A", 390),
                ("yellow-oxide", "yellow oxide", "polished", "#B08A3E", 380),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="terrazzo",
            name="Terrazzo",
            category="floor",
            surface_groups=FLOOR_INT + ["floor.stair"],
            texture="speckle",
            series=[
                ("white", "white", "polished", "#E8E6DF", 950),
                ("grey", "grey", "polished", "#B4B4B0", 950),
                ("charcoal", "charcoal", "polished", "#4F5052", 1050),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="wood-floor",
            name="Wooden flooring",
            category="floor",
            surface_groups=FLOOR_INT,
            texture="wood",
            series=[
                ("laminate-oak", "laminate, oak", "laminated", "#C09A6B", 950),
                ("laminate-walnut", "laminate, walnut", "laminated", "#6E4A2E", 1050),
                ("engineered-teak", "engineered, teak", "natural", "#9A6B41", 2600),
                ("solid-teak", "solid, teak", "sealed", "#8F6238", 4500),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="paver",
            name="Paver block",
            category="floor",
            surface_groups=["floor.parking", "floor.terrace"],
            texture="tile",
            series=[
                ("grey-60", "60 mm grey", "textured", "#9E9E9C", 420),
                ("red-60", "60 mm red", "textured", "#9C5A44", 440),
                ("cobble-80", "80 mm cobble", "textured", "#8E8B84", 560),
            ],
        )
    )

    m.append(
        MaterialFamily(
            stem="paint-interior",
            name="Interior emulsion",
            category="wall",
            surface_groups=WALL_INT + ["ceiling.interior"],
            texture="plaster",
            series=[
                ("white", "white", "matte", "#F6F5F1", 90),
                ("ivory", "ivory", "matte", "#F1E9DA", 95),
                ("almond", "almond", "matte", "#E6D9C3", 95),
                ("sage", "sage", "matte", "#B9C4B1", 105),
                ("terracotta", "terracotta", "matte", "#C4785A", 105),
                ("indigo", "indigo", "matte", "#3E4C6B", 110),
                ("charcoal", "charcoal", "matte", "#4A4B4D", 110),
                ("silk-white", "white, silk", "glossy", "#F7F6F2", 130),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="paint-exterior",
            name="Exterior emulsion",
            category="wall",
            surface_groups=["wall.exterior"],
            texture="plaster",
            series=[
                ("white", "white", "textured", "#F2F1EC", 140),
                ("sand", "sand", "textured", "#DFCFB2", 145),
                ("grey", "grey", "textured", "#A9AAA8", 145),
                ("ochre", "ochre", "textured", "#C08F45", 150),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="brick-exposed",
            name="Exposed brick",
            category="wall",
            surface_groups=WALL_EXT + ["wall.feature"],
            texture="brick",
            series=[
                ("red", "red", "natural", "#9C5137", 950),
                ("wirecut", "wirecut", "natural", "#8A4A33", 1450),
                ("white-washed", "whitewashed", "coated", "#D7CDC2", 1100),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="stone-cladding",
            name="Stone cladding",
            category="wall",
            surface_groups=WALL_EXT + ["wall.feature"],
            texture="stone",
            series=[
                ("jaisalmer", "Jaisalmer", "natural", "#D8B87A", 1250),
                ("dholpur-beige", "Dholpur beige", "natural", "#D9C6A5", 1150),
                ("slate-black", "black slate", "split-face", "#3B3B3D", 1350),
                ("cudappah", "Cudappah", "honed", "#3E4244", 850),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="concrete-wall",
            name="Exposed concrete",
            category="wall",
            surface_groups=WALL_EXT + ["wall.feature", "ceiling.interior"],
            texture="concrete",
            series=[
                ("smooth", "smooth", "sealed", "#B0AEA9", 780),
                ("board-formed", "board-formed", "board-formed", "#A8A6A0", 1250),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="wall-tile",
            name="Wall tile",
            category="wall",
            surface_groups=["wall.bath", "wall.kitchen"],
            texture="tile",
            series=[
                ("white-subway", "white subway", "glossy", "#F5F5F3", 620),
                ("grey-300x600", "grey 300 × 600", "matte", "#C6C7C8", 680),
                ("beige-300x600", "beige 300 × 600", "glossy", "#E0D5C2", 680),
                ("patterned-300", "patterned 300", "glossy", "#C9D6D3", 820),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="wood-panel",
            name="Wood panelling",
            category="wall",
            surface_groups=["wall.feature", "wall.interior"],
            texture="wood",
            series=[
                ("teak-veneer", "teak veneer", "natural", "#93643C", 2200),
                ("walnut-veneer", "walnut veneer", "natural", "#5E4029", 2600),
                ("fluted-mdf", "fluted MDF", "coated", "#B9A184", 1800),
            ],
        )
    )

    m.append(
        MaterialFamily(
            stem="glazing-float",
            name="Float glass",
            category="glazing",
            surface_groups=["window.glazing", "facade.glazing"],
            texture="glass",
            series=[
                ("clear-6", "clear 6 mm", "clear", "#D6E4E8", 480),
                ("tinted-6", "tinted 6 mm", "tinted", "#8FA5A8", 620),
                ("frosted-6", "frosted 6 mm", "frosted", "#DCE3E3", 680),
                ("dgu-24", "DGU 24 mm", "clear", "#CFE0E5", 2200),
                ("toughened-10", "toughened 10 mm", "clear", "#D2E2E6", 1450),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="frame",
            name="Window frame",
            category="joinery",
            surface_groups=["window.frame"],
            texture="metal",
            series=[
                ("upvc-white", "uPVC, white", "coated", "#F0F0EE", 950),
                ("aluminium-anodised", "aluminium, anodised", "anodised", "#9EA1A3", 1250),
                ("aluminium-powder", "aluminium, powder-coated", "powder-coated", "#4A4C4E", 1350),
                ("teak", "teak", "sealed", "#8E6039", 3200),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="door-flush",
            name="Flush door",
            category="joinery",
            surface_groups=["door.internal"],
            texture="wood",
            series=[
                ("laminate-white", "laminate, white", "laminated", "#EFEEEA", 1450),
                ("laminate-walnut", "laminate, walnut", "laminated", "#6B4830", 1550),
                ("veneer-teak", "veneer, teak", "natural", "#8E6039", 2600),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="railing",
            name="Railing",
            category="railing",
            surface_groups=["railing.balcony", "railing.stair"],
            texture="metal",
            series=[
                ("ms-painted", "MS, painted", "coated", "#3D3F41", 1400),
                ("ss-brushed", "SS, brushed", "brushed", "#A9ACAE", 3200),
                ("glass-ss", "glass with SS", "clear", "#CBDCE0", 4500),
                ("wrought-iron", "wrought iron", "coated", "#2E2F31", 2200),
            ],
        )
    )
    m.append(
        MaterialFamily(
            stem="roof",
            name="Roofing",
            category="roof",
            surface_groups=["roof.pitched", "roof.flat"],
            texture="tile",
            series=[
                ("mangalore-tile", "Mangalore tile", "natural", "#A4593C", 850),
                ("clay-tile", "clay tile", "natural", "#9E5238", 950),
                ("metal-standing-seam", "metal standing seam", "coated", "#7E8285", 1650),
                ("china-mosaic", "china mosaic", "textured", "#E4E4DF", 420),
                ("brick-bat-coba", "brick bat coba", "textured", "#A9705A", 380),
            ],
        )
    )

    return m


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
def build(existing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(merged, added)``. Existing rows keep their identity and their order."""
    known = {row["id"] for row in existing}
    added: list[dict[str, Any]] = []
    for family in families():
        for row in family.rows():
            if row["id"] in known:
                continue
            known.add(row["id"])
            added.append(row)
    return existing + added, added


def build_materials(
    existing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    known = {row["id"] for row in existing}
    added: list[dict[str, Any]] = []
    for family in material_families():
        for row in family.rows():
            if row["id"] in known:
                continue
            known.add(row["id"])
            added.append(row)
    return existing + added, added


def _merge_file(path: str, builder: Any, label: str, *, write: bool) -> int:
    with open(path, encoding="utf-8") as handle:
        existing = json.load(handle)
    merged, added = builder(existing)
    print(
        "%-10s existing: %-4d generated: %-4d total: %d"
        % (label, len(existing), len(added), len(merged))
    )
    if not added:
        return 0
    if not write:
        for row in added[:4]:
            print("    + %-34s %s" % (row["id"], row["name"]))
        if len(added) > 4:
            print("    ... %d more" % (len(added) - 4))
        return 0
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("    wrote %s" % os.path.relpath(path, _ROOT))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the merged catalogues")
    args = parser.parse_args()

    _merge_file(CATALOG, build, "furniture", write=args.write)
    _merge_file(MATERIALS, build_materials, "materials", write=args.write)
    if not args.write:
        print("\nRe-run with --write to add them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
