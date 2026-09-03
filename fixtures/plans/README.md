# `fixtures/plans/` — the ready-made plan library

Each `<id>.json` here is a **solved, compliant house captured from a real solver
run**, offered by the New-project dialog as a ready-made plan (kind `plan`, next
to the blank and starter templates). `<id>.svg` beside it is the thumbnail the
picker shows — the sheet renderer drawing that very recipe, fabric layers only.

Nothing in this directory is typed by hand. A plan gets in by running the solver
against the local stack and capturing what it produced:

```bash
# local stack up (api + solver worker), then:
GARH_API=http://127.0.0.1:8000/api/v1 python scripts/seed_plan_library.py <cell-id>
python scripts/flatten_plan_recipes.py          # idempotent: unwraps solver.apply_option
PYTHONPATH=.:apps/api python scripts/render_plan_previews.py   # <id>.svg through the sheet renderer
python scripts/sheet_goldens.py --regen         # this directory is also the sheet-golden corpus
```

`scripts/seed_plan_library.py` holds the cells (plot, city pack, storeys, rooms,
car parking, road width). A cell that yields no plan is not in the library, and
the script says why in a comment beside it.

## What a recipe contains

```jsonc
{
  "id": "blr-30x40-g1-3bhk",
  "name": "Bengaluru 30 × 40, G+1 3BHK",
  "kind": "plan",
  "ops": [ /* flat op log: plot, brief, storeys, walls, openings, stairs, room.assign */ ],
  "solver": { "jobId": "…", "optionsOffered": 3, "scores": { "composite": 73 } },
  "model": { "walls": 21, "rooms": 13, "openings": 19, "stairs": 1 },
  "brief": { "beds": 3, "baths": 2, "extras": ["utility", "pooja"] }
}
```

The op log is the plan. It folds through both `packages/model` and
`apps/api/garh_model`, so one fixture tests the library, the solver's output and
the cross-language contract at once. No `solver.apply_option` wrapper survives
capture: `dispatch_ops` refuses one, and the loader refuses one.

## The gates a plan must pass (`apps/api/tests/test_plan_library.py`)

- **It came from a real run.** `solver.jobId` and `optionsOffered ≥ 1`; the op
  counts equal what the capture recorded.
- **It folds to what was captured.** Wall, room, stair and storey counts match;
  bedrooms, living and kitchen carry names.
- **Every room can be walked to.** `garh_model.circulation` walks the door graph
  from the entrance (ground) or the stair (upper storeys), never through a bath.
  The first plan captured here had a front door into a dead-end vestibule and a
  kitchen entered through the bath; no rule caught it, so this gate exists.
- **No `fail` on the compliance tab.** Stricter than the solver's own gate, which
  blocks only `hard` rules. Two captures failed `nbc.ventilation.habitable.min` by
  under 0.06% because the solver sized windows on a polygon 1 mm narrower than
  the one the tab measures; the solver was fixed, not the test.
- **The thumbnail is the renderer's.** `<id>.svg` is byte-equal to a fresh render.

`scripts/sheet_goldens.py` reads this directory as its corpus, so every plan also
produces its nine municipal sheets, a DXF audit and an area statement on every
push, byte-compared against `fixtures/sheets/`.

## Why op logs and never geometry snapshots

1. **Room ids are history-dependent.** The same drawing built in a different
   wall order gets different derived room ids and a different `stateHash`. A
   snapshot would pin the accident of ordering; an op log pins the decision.
2. **A snapshot cannot be replayed.** An op log can be folded, diffed and edited
   in the product like any other project history.
