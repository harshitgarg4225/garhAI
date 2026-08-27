# `fixtures/catalog/` — furniture, materials and facade kits

Playbook §17 asks the seed script for "furniture catalog (≥30 items with real Indian
dims …), material catalog (≥20), 2 facade kits". This directory is that data, held as
**language-neutral JSON** rather than as Python literals so the seeder, the API, the
solver's furniture-fit gate (§5.4) and any future TypeScript consumer all read the same
bytes.

## Files

| File | Shape | Count |
|---|---|---|
| `furniture.json` | array of `CatalogItemOut` | 229 (≥30 required) |
| `materials.json` | array of `MaterialOut` | 97 (≥20 required) |
| `facade-kits.json` | array of `FacadeKitOut` | 2 (exactly the MVP kits) |
| `index.json` | manifest + asserted counts | — |

The response models live in `apps/api/garh_api/routers/catalog.py`. They are the schema:
`widthMm`/`depthMm`/`heightMm`/`clearanceMm` are `StrictInt`, so `1524.0` fails at the
boundary rather than putting a float into a "does the bed fit" answer (§3, golden rule 6).

## Why there are two copies of this data, and how they are kept honest

`routers/catalog.py` also carries a **built-in table** so a fresh checkout with no files
still serves a real catalogue. That is a genuine second copy, so it is pinned:

* `apps/api/tests/test_catalog_fixtures.py::test_fixture_is_a_superset_of_the_builtin_table`
  asserts every built-in id exists here with **identical dimensions**. Change one, change
  both, or CI fails.
* These files are a *superset*: beyond the built-in table they carry the depth catalogue —
  bed/sofa/wardrobe/kitchen-module/appliance/sanitary variants at Indian-market sizes,
  vehicles, services, and a full Indian material palette (stones, marbles, tile shades,
  paints, claddings, roofing). The built-in ids keep their built-in dimensions.

## Serving these files from the API

`routers/catalog.py` reads `GARH_CATALOG_DIR`, falling back to `<repo>/catalog`. Point it
here to serve exactly what the seeder validated:

```bash
GARH_CATALOG_DIR="$PWD/fixtures/catalog"
```

Every catalogue response carries `source: "files" | "builtin"` so which table answered is
never a guess.

## Invariants (all asserted by the test suite)

1. Every dimension is a non-negative **integer** millimetre. No floats anywhere.
2. Ids are unique within a file and are lowercase kebab-case.
3. Every `roomTypes` entry is a member of `garh_model.model.ROOM_TYPES`.
4. Every room type that can physically hold furniture has at least one item. The four
   that cannot — `duct`, `shaft`, `void`, `unassigned` — are listed in `index.json` under
   `roomTypesWithoutFurniture`, so "no furniture" is a decision on the record rather than
   an oversight.
5. `priceInrPerSqm` is whole rupees or absent — never a float, never a range.
6. The two facade kit ids are exactly `contemporary` and `modern-minimal` (MVP cut line).

## Editing

Edit the JSON, then run `pytest apps/api/tests/test_catalog_fixtures.py`. If you change a
dimension that the built-in table also carries, change `routers/catalog.py` in the same
commit — the superset test exists to make that impossible to forget.
