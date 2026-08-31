# What a brand-new user actually gets

**Executed 2026-08-30** against a live local stack (Postgres 16, Redis, MinIO-compatible
S3, the API, the solver and drawings workers) by driving the real HTTP API as a new
account — not a fixture, not the seeded demo.

Everything in this file was run. Where something is unresolved it says so.

## The question

Every previous verification in this repository starts from the **seeded demo project**.
The `@smoke` suite signs in as `demo@garh.ai`, opens the demo project, and walks the six
tabs. `happy-path.spec.ts` — the file that describes the actual job, signup → plot →
brief → generate → edit → sheets → export → share — is seven tests, six of them
`test.skip`, and the bodies of those six are `expect(true).toBeTruthy()` placeholders.

So the journey an architect comes to the product to do had never been executed.

## What works

| Step                                       | Result               |
| ------------------------------------------ | -------------------- |
| Sign up a new practice                     | ✅ 201               |
| Sign in with the emailed code (dev echo)   | ✅ 200               |
| Create a project on the BBMP pack          | ✅ 201               |
| Draw a 30 × 40 ft plot with a 9 m road     | ✅                   |
| Paste a client's brief and have it parsed  | ✅                   |
| Save the brief                             | ✅                   |
| Press Generate — job accepted and runs     | ✅ 202 → `succeeded` |
| Compliance evaluates against the BBMP pack | ✅ 23 results        |

## What does not

**A brief a user typed produces zero plan options.** The solver finishes, reports
`succeeded`, and returns nothing. The Options screen handles this honestly — "No plan
cleared the quality checks" — but it is the first thing a new user sees after doing
everything right.

The seeded demo project, on the **same plot**, produces 3 options. The difference is
entirely in the brief, and bisecting the demo brief toward a parsed one showed three
independent breakages — change any one and generation drops from 3 options to 0:

| Mutation applied to the demo brief                         | Options |
| ---------------------------------------------------------- | ------- |
| none (control)                                             | 3       |
| `bedroom` count 2 instead of a distinct `guest_bedroom`    | 0       |
| storey pins removed                                        | 0       |
| room sizes stripped                                        | 0       |
| rooms reduced to `{type, count}` — what the parser emitted | 0       |

## Three defects found and fixed

All three were silent: no error, no log, a job that still reported success.

1. **Parsed rooms had no sizes at all.** The parser emitted `{"type": "bedroom",
"count": 2}`; the program layer read `int(raw.get("minAreaMm2") or 0)`. Every room
   reached Stage A as a zero-area, zero-width rectangle. Nothing in the product turned
   "3BHK" into dimensions — the seeded demo's sizes are written out by hand, which is
   why it was the only brief that ever generated anything.
   Fixed: `services/llm/room_defaults.py`, applied in the shared parse path so both the
   mock and the real provider get it. Minimums are read from `rulepacks/nbc-core.json`
   — the same numbers the compliance tab cites — and targets are practice defaults
   emitted as editable assumptions.

2. **`count` was read by nothing.** `_parse_rooms` in the solver's payload parser keyed
   rooms by type and ignored `count` entirely, so two bedrooms became one. Two entries
   of the same type collided on the key and lost a room. The seed's own docstring
   documents the workaround ("which is also why the ground bedroom is a
   `guest_bedroom`") — a brief authored around a bug rather than the bug being fixed.

3. **Storey pins were discarded.** The parser read `storeyIndex`; the brief document,
   which the API forwards verbatim, writes `storey`. Every pin an architect set on the
   Brief tab went nowhere — including the demo's own two.

Six gates cover these, each broken on purpose and observed to fail.

## A fourth defect, found with the diagnostic

Stage A now says _why_ a storey did not tile (`services/solver/diagnose.py`), and the
first thing it said settled the question:

> The rooms on this floor need about 62.8 m² once circulation is allowed for, and the
> buildable area after setbacks is 55.9 m² — about 6.9 m² short.

62.8 m² is the _whole_ programme. On a G+1 the ground floor should carry roughly half —
so every room was landing on one storey.

4. **The brief's `storeys` was read by nothing.** `_resolve_storeys` checked the
   request, the model, then `floorsAboveGround`, then fell back to 1. `floorsAboveGround`
   is G+n, which the seeded demo writes; `storeys` is the total, which the brief parser
   emits for the same house. A typed brief therefore resolved to ONE storey and the whole
   3BHK was piled onto the ground floor. The demo escaped it by writing the other
   spelling — which is why the demo was the only project that generated anything.

That is the same class as the other three, and the fourth instance of it: a field
written under one name and read under another, failing silently.

## Where it stands now — the journey runs end to end

**Executed 2026-08-31.** A brand-new practice, its own brief, no seed and no fixture:

```
signup → sign in → project → 30 x 40 ft plot → brief typed as a client says it
       → Generate → 2 options → apply → 21 walls, 13 rooms
       → compliance (23 results) → 10 municipal sheets
```

`scripts/first_run_journey.py` is that run, executable against any stack:

```
python scripts/first_run_journey.py            # local compose
GARH_API=https://host/api/v1 python scripts/first_run_journey.py
```

It exits non-zero on the first thing an architect could not do, so this cannot go
quietly back to zero.

## Six defects, all silent, all fixed

Each failed with no error, no log line, and a job that still reported `succeeded`.

| #   | Defect                                                  | Why the demo survived it                     |
| --- | ------------------------------------------------------- | -------------------------------------------- |
| 1   | Parsed rooms carried no sizes                           | the seed writes sizes by hand                |
| 2   | `count` read by nothing — 2 bedrooms became 1           | the seed gives each room a distinct type     |
| 3   | Storey pins discarded (`storey` vs `storeyIndex`)       | the seed's pins were dropped too, harmlessly |
| 4   | `storeys` read by nothing — a G+1 planned as one floor  | the seed writes `floorsAboveGround`          |
| 5   | Every bedroom forced upstairs; the floor would not tile | the seed pins a bedroom downstairs by hand   |
| 6   | `parkingCount` (web + parser) vs `carParking` (API)     | the seed hard-codes the API's spelling       |

Five of the six are the same shape: **a field written under one name and read under
another.** The seeded demo escaped every one of them, which is precisely why it was the
only project in this product that ever generated anything — and why testing against it
proved nothing about a real user.

Defect 5 is the exception and the interesting one. Stage A now says _why_ a storey
failed (`services/solver/diagnose.py`), separating "does not fit by area" — which it can
prove — from "fits, but no arrangement satisfied every constraint". On an `arrangement`
failure it moves a bedroom and its bath downstairs and tries again, which is the
ordinary Indian G+1 arrangement and exactly what the demo brief does by hand. Measured:
**0 of 6 stair anchors solved before, 3 of 6 after.** The move is chipped as an
assumption the architect can override.

## The gate that closes the class

`apps/api/tests/test_brief_aliases.py` asserts that every brief field the API reads is
one the web app can actually write. It found two more the moment it was written:
`dwellingUnits` (genuinely derived — one house is one dwelling) and
`rainwaterHarvesting`, which **no user could declare**, so every city pack's
`rwh.required` warning fired on every project forever with no way to clear it. A warning
that can never go green teaches people to ignore warnings. The Brief form now carries it.

## Still not production grade

The journey works. These remain, and none is code:

- **Every regulatory value is `seed` / `unreviewed`** — 118 rules across 5 packs and all
  4 submission templates. Sheets generated from them are not submittable until
  empanelled architects review them per city.
- **The real providers have never run.** `PROVIDER_LLM=mock`, `PROVIDER_RENDER=mock`.
  The copilot corpus passes against a fixture; that proves the pipeline, not that a
  frontier model reads an architect's phrasing.
- **mypy debt** outside the strict trees, and a visual-regression suite with no baseline.
