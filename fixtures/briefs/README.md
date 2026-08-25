# `fixtures/briefs/` — the golden brief corpus

Playbook §16: *"Solver: 20-brief golden corpus (`fixtures/briefs/`) → assert gates (§5.6),
determinism per seed, time budget, locked-room preservation."* This directory is that
corpus. It exists **before** the solver so Phase 3 has a target it did not write itself.

20 briefs, stratified so the solver cannot pass by being good at one shape of problem:

| Axis | Spread |
|---|---|
| City pack | `blr` 7 · `ncr` 7 · `hyd` 6 |
| Plot shape | `rect` 13 · `L` 4 · `T` 3 (the MVP envelope set) |
| Floors above ground | G 2 · G+1 4 · G+2 12 · G+3 2 |
| Plot area band | ≤120 m² 6 · 121–240 6 · 241–500 7 · >500 1 |
| Bedrooms | 2–5 |
| Vastu | `off` 8 · `advisory` 8 · `strict` 4 |

`index.json` is the manifest and carries those counts. `apps/api/tests/test_brief_corpus.py`
asserts them, so adding a brief without updating the manifest fails CI.

## Shape of one fixture

The file is a **corpus envelope**, not a `ProjectDoc`. Deliberately:

* the `{data, vastuMode, completeness}` subset **is** a valid `BriefDoc`
  (`packages/model/schema/project-doc.schema.json#/$defs/BriefDoc`), so a fixture can be
  fed to `PUT /projects/:id/brief` unchanged;
* `plot` maps onto `PlotDoc` — `polygon` → `boundary`, `northDeg` → `northDeg`,
  `roads` → `roads`, `cityPack` → `regProfile.cityPack`;
* `stratum`, `expectations`, `assumptions`, `warnings` and `match` are corpus metadata
  that has no home in the model document.

```jsonc
{
  "id": "brief-01-blr-30x40-rect-g1",
  "match": ["brief-01-blr-30x40-rect-g1"],   // see "the mock parser" below
  "stratum": { "city": "blr", "shape": "rect", "floorsAboveGround": 1, … },
  "plot":    { "polygon": [{"x":0,"y":0}, …], "areaMm2": 111483648, "roads": [ … ] },
  "data":    { "bedrooms": 3, "rooms": [ … ], "adjacency": [ … ], … },
  "vastuMode": "advisory",
  "completeness": 70,
  "assumptions": [ { "field": "…", "value": …, "reason": "…", "cite": "…" } ],
  "expectations": { "minOptions": 3, "minCompositeScore": 55, … }
}
```

### Units

Every length is an integer millimetre, every area an integer **mm²**, money in whole
rupees (§3, golden rule 6). Plot dimensions are exact multiples of 5 ft (1 524 mm), which
is the only way `n ft → mm` stays an integer. `targetAreaMm2` is a whole number of m²
expressed in mm², and room targets scale by plot band (86 % / 100 % / 118 % / 135 %) —
nobody draws a 16 m² living room on a 92 m² site.

### `expectations` — the Phase 3 gate

Mirrors `services/solver/gates.py` exactly: `minOptions` 3, `minCompositeScore` 55,
`maxCirculationPercent` 18, `minFurnitureFit` 100, `maxSolveSeconds` 60 (120 in CI, which
runs 2 search workers). `test_brief_corpus.py` asserts these against the engine's own
constants, so if a gate moves, either the corpus moves with it or CI says so.

## Every brief is buildable — and that is checked

`test_brief_corpus.py::test_every_brief_fits_its_regulatory_envelope` resolves each
brief's plot against its **real rule pack** (`rulepacks/{blr,ncr,hyd}.json`) — stacked
setbacks, coverage cap, FAR cap, floors cap, height cap — and asserts the programme fits:

```
footprint = min(plot minus setbacks, coverage × plotArea)      (× 0.82 for L / T notches)
capacity  = min(footprint × floors, FAR × plotArea)
need      = Σ(room target areas, excluding porch/garage/balcony) × 1.08   (wall footprint)
assert capacity ≥ need × 1.10  and  floors ≤ floorsMax  and  height ≤ heightMax
```

A corpus containing an impossible brief would make the Phase 3 DoD ("≥3 options, all
passing hard rules") unreachable for reasons that have nothing to do with the solver. The
check is approximate on purpose — it is a *feasibility floor*, not a substitute for the
solver — and it is what forced four of the twenty plots to be resized while this corpus
was authored.

## The mock parser, and why `match` is only the fixture id

`garh_api/routers/projects.py::_MockBriefParser` scans this directory and returns the
first fixture whose `match` keyword appears in the submitted text. That is what lets
`POST /brief/parse` be deterministic in tests. Every fixture's `match` is **its own id and
nothing else**, so no realistic free-text brief can hit one by accident — a corpus entry
is returned only when a test asks for it by name. If you add a keyword like `"3bhk"`,
every 3BHK brief in the product starts returning this fixture instead of being parsed.

## Regenerating

The corpus is generated from a 20-row table (city, plot ft, shape, floors, bedrooms,
vastu, facing, roads, budget, extras) plus one programme template, so the stratification
is visible in one place instead of spread over 20 files. The generator is not kept in the
repo — the files are the artefact and are reviewed as such. To change the corpus, edit the
JSON and re-run:

```bash
cd apps/api && pytest tests/test_brief_corpus.py -q
```

## What these fixtures do *not* contain

No walls, no rooms, no coordinates beyond the plot boundary — a brief is an *input*. The
expected plans are golden files that Phase 3 will add next to the solver, keyed by
`(brief id, seed)`. Pinning a plan here now would pin whatever the first solver happened
to emit, which is the opposite of a golden test.
