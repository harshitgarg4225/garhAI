"""The seed catalogues as data (playbook §17) — no datastore needed.

``fixtures/catalog/*.json`` is read by the seeder, by ``GET /catalog/*``, and (from Phase 3)
by the solver's furniture-fit gate. It is therefore a cross-language contract, and the two
ways it can break are both silent:

* a **float** dimension — "does the bed fit in this bedroom" becomes a floating-point
  comparison, and §3 forbids that everywhere for exactly this reason;
* a **room type nobody stocks** — the furniture tool shows an empty palette for that room
  and the solver's fit score has nothing to place, with no error anywhere.

Both are asserted here, along with the §17 minimums and the real Indian dimensions the
playbook names item by item. These are the values an architect will recognise as right or
wrong at a glance, so they are pinned individually rather than as a count.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from garh_api.seed.catalog import (
    FACADE_KIT_IDS,
    MIN_FURNITURE_ITEMS,
    MIN_MATERIALS,
    REQUIRED_RULEPACKS,
    ROOM_TYPES_WITHOUT_FURNITURE,
    SeedDataError,
    fixtures_dir,
    load_catalog_bundle,
    load_rulepack_registry,
    validate_facade_kits,
    validate_furniture,
    validate_materials,
)
from garh_model.model import ROOM_TYPES

CATALOG_DIR = os.path.join(fixtures_dir(), "catalog")


def _load(name: str) -> list[dict[str, Any]]:
    with open(os.path.join(CATALOG_DIR, "%s.json" % name), encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload["items"] if isinstance(payload, dict) else payload


@pytest.fixture(scope="module")
def furniture() -> list[dict[str, Any]]:
    return _load("furniture")


@pytest.fixture(scope="module")
def materials() -> list[dict[str, Any]]:
    return _load("materials")


@pytest.fixture(scope="module")
def facade_kits() -> list[dict[str, Any]]:
    return _load("facade-kits")


# ---------------------------------------------------------------------------
# The files load and validate
# ---------------------------------------------------------------------------


def test_the_catalogue_the_seeder_will_read_validates() -> None:
    """``load_catalog_bundle`` is the seed's gate; running it here fails fast, not mid-seed."""
    bundle = load_catalog_bundle()
    assert bundle.source == "files", (
        "the catalogue files were not found; the seeder would fall back to the compiled-in "
        "table. Searched: %s" % (bundle.directory,)
    )
    assert bundle.counts["furniture"] >= MIN_FURNITURE_ITEMS
    assert bundle.counts["materials"] >= MIN_MATERIALS
    assert bundle.counts["facadeKits"] == 2
    assert len(bundle.digest()) == 32


def test_digest_is_stable_across_loads() -> None:
    """The digest lands in ``firms.settings``; if it is not deterministic it is noise."""
    assert load_catalog_bundle().digest() == load_catalog_bundle().digest()


def test_validators_accept_the_shipped_data(
    furniture: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    facade_kits: list[dict[str, Any]],
) -> None:
    assert len(validate_furniture(furniture, room_types=ROOM_TYPES)) == len(furniture)
    assert len(validate_materials(materials)) == len(materials)
    assert len(validate_facade_kits(facade_kits)) == 2


# ---------------------------------------------------------------------------
# §17's minimums and its named dimensions
# ---------------------------------------------------------------------------


def test_furniture_meets_the_minimum(furniture: list[dict[str, Any]]) -> None:
    assert len(furniture) >= 30, "§17: at least 30 furniture items"


def test_materials_meet_the_minimum(materials: list[dict[str, Any]]) -> None:
    assert len(materials) >= 20, "§17: at least 20 materials"


#: The items §17 names, with the dimensions it names them with. Orientation-insensitive:
#: a bed is 1900 x 1525 whichever way the catalogue lists it.
NAMED_DIMENSIONS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("queen bed", (1900, 1525)),
    ("3-seat sofa", (2100, 900)),
    ("6-seat dining table", (1500, 900)),
    ("WC", (700, 400)),
    ("washbasin", (550, 450)),
    ("car", (4800, 1800)),
)


@pytest.mark.parametrize(("label", "dimensions"), NAMED_DIMENSIONS, ids=[n for n, _ in NAMED_DIMENSIONS])
def test_playbook_named_items_exist_with_the_named_dimensions(
    furniture: list[dict[str, Any]], label: str, dimensions: tuple[int, int]
) -> None:
    """§17 lists these by number. An architect spots a wrong one instantly; a test should too."""
    wanted = {dimensions, (dimensions[1], dimensions[0])}
    matches = [
        item["id"] for item in furniture if (item["widthMm"], item["depthMm"]) in wanted
    ]
    assert matches, "no catalogue item is %s (%d x %d mm)" % (label, *dimensions)


@pytest.mark.parametrize(
    ("category_hint", "depth"),
    [("kitchen counter", 600), ("wardrobe", 600)],
)
def test_playbook_named_depths(
    furniture: list[dict[str, Any]], category_hint: str, depth: int
) -> None:
    """§17 names two depths rather than full footprints (they are run lengths)."""
    matches = [
        item["id"]
        for item in furniture
        if item["depthMm"] == depth and category_hint.split()[0] in (item["id"] + item["name"].lower())
    ]
    assert matches, "nothing resembling a %s at %d mm deep" % (category_hint, depth)


# ---------------------------------------------------------------------------
# Integer millimetres, everywhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["widthMm", "depthMm", "heightMm"])
def test_every_dimension_is_a_positive_integer(
    furniture: list[dict[str, Any]], field: str
) -> None:
    """§3: geometry is integer millimetres. ``True`` is an int in Python and is not a length."""
    offenders = [
        (item["id"], item[field])
        for item in furniture
        if isinstance(item[field], bool) or not isinstance(item[field], int) or item[field] < 1
    ]
    assert not offenders, offenders


def test_no_float_anywhere_in_the_catalogue_files() -> None:
    """Belt and braces: scan every value, not just the fields we thought to check.

    A float in ``params`` or a colourway would not fail the seeder's field-by-field
    validation, but it would break ``canonicalJson`` the moment it reached an op payload.
    """
    offenders: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, float):
            offenders.append("%s = %r" % (path, value))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, "%s.%s" % (path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "%s[%d]" % (path, index))

    for name in ("furniture", "materials", "facade-kits", "index"):
        with open(os.path.join(CATALOG_DIR, "%s.json" % name), encoding="utf-8") as handle:
            walk(json.load(handle), name)

    assert not offenders, "float(s) in the catalogue: %s" % offenders


# ---------------------------------------------------------------------------
# Room-type coverage — the furniture-fit gate's input
# ---------------------------------------------------------------------------


def test_every_room_type_that_should_hold_furniture_does(
    furniture: list[dict[str, Any]]
) -> None:
    """A room type with no furniture is an empty palette and a silent zero fit score."""
    stocked = {room_type for item in furniture for room_type in item["roomTypes"]}
    expected = set(ROOM_TYPES) - set(ROOM_TYPES_WITHOUT_FURNITURE)
    missing = sorted(expected - stocked)
    assert not missing, (
        "no furniture for room type(s) %s. Add an item, or list the type in "
        "ROOM_TYPES_WITHOUT_FURNITURE with a reason." % missing
    )


def test_no_furniture_names_a_room_type_the_model_does_not_have(
    furniture: list[dict[str, Any]]
) -> None:
    """A typo here makes an item unreachable from the tool, with no error."""
    unknown = sorted(
        {room_type for item in furniture for room_type in item["roomTypes"]} - set(ROOM_TYPES)
    )
    assert not unknown, "unknown room type(s) %s (allowed: garh_model.model.ROOM_TYPES)" % unknown


def test_room_types_without_furniture_are_all_real_room_types() -> None:
    """The exclusion list must not drift from the model's enum either."""
    assert set(ROOM_TYPES_WITHOUT_FURNITURE) <= set(ROOM_TYPES), ROOM_TYPES_WITHOUT_FURNITURE


def test_ids_are_unique_and_kebab_case(
    furniture: list[dict[str, Any]], materials: list[dict[str, Any]]
) -> None:
    """Ids reach op payloads (``furniture.set``) and DXF layer names."""
    for rows, label in ((furniture, "furniture"), (materials, "materials")):
        ids = [row["id"] for row in rows]
        assert len(ids) == len(set(ids)), "duplicate %s id" % label
        for item_id in ids:
            assert item_id == item_id.lower(), item_id
            assert " " not in item_id and "_" not in item_id, item_id


# ---------------------------------------------------------------------------
# Materials and facade kits
# ---------------------------------------------------------------------------


def test_materials_are_an_indian_palette(materials: list[dict[str, Any]]) -> None:
    """§17 asks for an Indian palette; these are the families a local spec sheet lists."""
    blob = json.dumps(materials).lower()
    for expected in ("brick", "stone", "texture", "louver", "railing", "kota", "granite"):
        assert expected in blob, "no material mentions %r" % expected


def test_every_material_can_be_assigned_to_something(materials: list[dict[str, Any]]) -> None:
    """A material with no surface group is dead data the UI can never offer."""
    for material in materials:
        assert material["surfaceGroups"], material["id"]


def test_material_prices_are_whole_rupees(materials: list[dict[str, Any]]) -> None:
    """₹ amounts are integers (delight rule: ₹ digit grouping, never 1234.56)."""
    for material in materials:
        price = material.get("priceInrPerSqm")
        if price is None:
            continue
        assert isinstance(price, int) and not isinstance(price, bool) and price > 0, material["id"]


def test_facade_kits_are_exactly_the_mvp_two(facade_kits: list[dict[str, Any]]) -> None:
    """MVP cut line (§8): Contemporary and Modern Minimal. Not one, not three."""
    assert [kit["id"] for kit in facade_kits] == list(FACADE_KIT_IDS)
    for kit in facade_kits:
        assert kit["components"], kit["id"]
        assert kit["rules"], kit["id"]
        assert kit["colorways"], kit["id"]


def test_both_kits_expose_the_same_component_slots(facade_kits: list[dict[str, Any]]) -> None:
    """Switching kits must not silently drop a component the model already placed (§8)."""
    slots = [set(kit["components"]) for kit in facade_kits]
    assert slots[0] == slots[1], slots[0] ^ slots[1]


def test_a_float_dimension_is_rejected() -> None:
    """The validator must actually refuse what the docstrings promise it refuses."""
    with pytest.raises(SeedDataError, match="integer millimetre"):
        validate_furniture(
            [
                {
                    "id": "bad-bed",
                    "name": "Float bed",
                    "category": "bed",
                    "widthMm": 1900.5,
                    "depthMm": 1525,
                    "heightMm": 600,
                    "roomTypes": ["bedroom"],
                }
            ],
            room_types=ROOM_TYPES,
        )


def test_an_unknown_room_type_is_rejected() -> None:
    with pytest.raises(SeedDataError, match="unknown room type"):
        validate_furniture(
            [
                {
                    "id": "bad-bed",
                    "name": "Bed",
                    "category": "bed",
                    "widthMm": 1900,
                    "depthMm": 1525,
                    "heightMm": 600,
                    "roomTypes": ["ballroom"],
                }
            ],
            room_types=ROOM_TYPES,
        )


def test_a_third_facade_kit_is_rejected(facade_kits: list[dict[str, Any]]) -> None:
    """The cut line is enforced, not merely documented."""
    with pytest.raises(SeedDataError, match="MVP cut line"):
        validate_facade_kits(facade_kits + [dict(facade_kits[0], id="art-deco")])


# ---------------------------------------------------------------------------
# Rule packs
# ---------------------------------------------------------------------------


def test_all_five_rulepacks_are_registered() -> None:
    """§17: "3 rule packs + nbc-core + vastu"."""
    registry = load_rulepack_registry()
    assert set(registry.ids) >= set(REQUIRED_RULEPACKS)
    for pack_id, version in registry.versions().items():
        assert version and version != "0", "%s has no version" % pack_id
    for pack in registry.packs:
        assert pack["ruleCount"] > 0, pack["id"]
        assert pack["reviewStatus"], pack["id"]


def test_seed_packs_do_not_claim_review_they_have_not_had() -> None:
    """Golden rule 4: a citation must show its confidence. Seed values are unreviewed.

    When an architect reviews a pack, its ``review.status`` changes and this assertion is
    what tells the next reader that the confidence ladder is real.
    """
    registry = load_rulepack_registry()
    for pack in registry.packs:
        if pack.get("confidence") == "seed":
            assert pack["reviewStatus"] != "reviewed", (
                "%s claims confidence 'seed' but review status 'reviewed' — pick one"
                % pack["id"]
            )


def test_catalog_index_documents_the_files_it_ships() -> None:
    """``index.json`` promises its counts are asserted here. This is that assertion.

    Without it the manifest is a comment that rots: someone adds a sofa, the count says 45,
    and the next reader trusts the wrong number.
    """
    with open(os.path.join(CATALOG_DIR, "index.json"), encoding="utf-8") as handle:
        index = json.load(handle)
    assert index["schemaVersion"] == 1
    assert set(index["facadeKitIds"]) == set(FACADE_KIT_IDS)
    assert set(index["roomTypesWithoutFurniture"]) == set(ROOM_TYPES_WITHOUT_FURNITURE), (
        "the manifest and garh_api.seed.catalog disagree about which rooms hold no furniture"
    )

    declared = {entry["name"]: entry for entry in index["files"]}
    assert set(declared) == {"furniture.json", "materials.json", "facade-kits.json"}
    for name, entry in declared.items():
        path = os.path.join(CATALOG_DIR, name)
        assert os.path.isfile(path), path
        rows = _load(name[: -len(".json")])
        assert len(rows) == entry["count"], (
            "%s holds %d rows; index.json says %d — update the manifest in the same commit"
            % (name, len(rows), entry["count"])
        )
        assert len(rows) >= entry["minimum"], name
        for key in entry["requiredKeys"]:
            missing = [row.get("id", "?") for row in rows if key not in row]
            assert not missing, "%s: %d row(s) missing the required key %r" % (
                name,
                len(missing),
                key,
            )
