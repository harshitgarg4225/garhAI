# Rule packs

Regulatory rules as **data**. A pack is a JSON file listing rules; each rule is an applicability gate
(`when`) plus one typed measurement (`check`). Packs contain no code, no expressions and no
floating-point numbers. The rules engine implements a closed set of 18 check types and a closed set of
25 context fields, and **refuses to load a pack that names anything outside those sets** — a rule the
engine cannot evaluate must be a loud load error, never a silent pass.

> **Every value in every pack in this directory is marked `"confidence": "seed"`.** Seed means: drafted
> by the Garh AI team from secondary summaries, not transcribed from a primary document, not reviewed by
> a local architect. Seed numbers are a drafting aid. They are not authoritative and must never be
> presented as if they were — see [The confidence ladder](#the-confidence-ladder).

```
rulepacks/
├── schema/
│   ├── rulepack.schema.json     # the DSL (draft 2020-12). Also carries x-garh-check-meta:
│   │                            #   the machine-readable scope/resultUnit contract for the engine.
│   └── fixture.schema.json      # fixture format + $defs.evaluationContext = the engine's INPUT contract
├── index.json                   # manifest for GET /rulepacks
├── nbc-core.json                # 23 rules · national · dimensional minimums
├── blr.json                     # 33 rules · extends nbc-core · BBMP Building Bye-laws 2020
├── ncr.json                     # 19 rules · extends nbc-core · MPD-2021 / DDA Bye-laws 2016
├── hyd.json                     # 34 rules · extends nbc-core · Telangana Building Rules 2012
└── vastu.json                   #  9 rules · advisory scoring pack, 0–100 weighted score
```

118 rules total. Each has at least one passing and one failing fixture in `fixtures/rules/` — 238
fixtures, which are the gate on the engine.

---

## The model in 30 seconds

```json
{ "id": "blr.setback.front.plot.121-240",
  "severity": "fail",
  "title": "Front setback - plots 121-240 m2",
  "message": "The front setback is {actual} - a plot of this size needs at least {limit}.",
  "when":  { "zoneCategory": {"eq": "residential"},
             "buildingUse":  {"in": ["dwelling-single","dwelling-two","row-house"]},
             "plotAreaSqm":  {"gt": 120, "lte": 240} },
  "check": { "type": "setback_min", "edge": "front", "valueMm": 2000 },
  "cite":  "Table 6 - Setbacks for residential plots",
  "fix":   "Move the building line back so the front setback is at least 2 m.",
  "confidence": "seed" }
```

The engine takes `(model, plot, profile, packs)`, flattens it into an **EvaluationContext**
(`schema/fixture.schema.json` → `$defs.evaluationContext`), and returns one row per rule:

```
{ ruleId, status: pass|warn|fail|not_applicable, actual, limit, cite, fixHint, elements[], confidence }
```

Pure, deterministic, integer-only, under 100 ms for a house — safe to run debounced on every edit and
inside the solver's critic.

---

## Units and exactness

| Quantity | How it is written | Why |
|---|---|---|
| Length | integer millimetres (`valueMm`) | Locked project-wide. Floats drift, and drift breaks dimension chains and compliance math. |
| Area | integer square millimetres (`valueMm2`) | `9.5 m²` is `9500000`. |
| Ratio (FAR, coverage, ventilation, parking rate) | `{"num": 175, "den": 100}` | Exact rational. The engine cross-multiplies with integers, so `FAR 1.75` never becomes `1.7499999`. |
| Plot-size band | `plotAreaSqm`, integer whole m² | Bye-law tables are banded in whole m². The engine compares **exactly**: `{"lte": 360}` means `plot.areaMm2 <= 360 × 1 000 000`, so a 360.4 m² plot correctly falls *outside* the band. |
| Plot-size band that is not a whole m² | `plotAreaMm2` | e.g. Bengaluru's rainwater-harvesting threshold of 1 200 sq ft = `111480000`. |

**There is not one floating-point number in a valid pack.** `fixtures/rules/_tools/verify_fixtures.py`
fails the build if one appears.

Derived limits use documented rounding, always toward the stricter reading:

* `far_max` / `coverage_max` limit = `floor(num × plotAreaMm2 / den)`
* `ventilation_ratio_min` requirement = `max(ceil(num × roomAreaMm2 / den), minAreaMm2)`
* `parking_min` requirement = `max(ceil(rate × basisQuantity), minSpaces)`

---

## `when` — the closed context field set

`when` is a flat object of `field: {operator: value}`. It gates applicability. The field set is
**closed and `additionalProperties: false`** in the schema. That matters more than it looks: a typo
like `roadWidthmm` in an open schema would make the rule apply to every plot or to none, and the
engine would report a house as checked when it was not. Adding a field means bumping `schemaVersion`
and teaching the engine to bind it.

### Project-level fields — always bound

| Field | Type | Meaning |
|---|---|---|
| `cityPack` | enum `nbc-core \| blr \| ncr \| hyd \| custom` | The city preset in force. |
| `zoneCategory` | enum `residential \| mixed \| commercial \| institutional \| industrial` | Land-use zone. |
| `buildingUse` | enum `dwelling-single \| dwelling-two \| row-house \| apartment \| other` | |
| `plotAreaSqm` | int, whole m², exact ×10⁶ | Plot-size banding. |
| `plotAreaMm2` | int mm² | Plot-size banding for non-whole-m² thresholds. |
| `plotFrontageMm` | int mm, nullable | Width along the front edge. |
| `plotDepthMm` | int mm, nullable | |
| `roadWidthMm` | int mm, nullable | Road abutting the **front** edge (the primary access road). |
| `cornerPlot` | bool | Two or more edges abut roads that meet. |
| `abuttingRoadCount` | int | |
| `storeys` | int | Habitable storeys above ground; stilt and basement excluded. |
| `hasStilt` | bool | |
| `hasBasement` | bool | |
| `buildingHeightMm` | int mm | Ground to highest point, nothing excluded. |
| `builtUpAreaMm2` | int mm² | Gross, all storeys. |
| `farCountableAreaMm2` | int mm² | Gross minus `vocabulary.farExclusions`. |
| `dwellingUnits` | int | |
| `vastuMode` | enum `off \| advisory \| strict` | From the brief. |

### Scope-bound fields — bound only for the matching check scope

| Field | Type | Bound when the check scope is |
|---|---|---|
| `edgeRoadWidthMm` | int mm, nullable | `edge` — the road on *the edge under evaluation* |
| `storeyIndex` | int, 0 = ground | `storey`, `room`, `opening`, `stair`, `projection` |
| `roomType` | enum, 28 values | `room` |
| `roomIsHabitable` | bool | `room` — derived as `roomType ∈ vocabulary.habitableRoomTypes` |
| `roomIsInternal` | bool | `room` — no wall on the building envelope |
| `openingKind` | enum `door \| window \| ventilator` | `opening` |
| `openingRole` | enum `main-entrance \| internal \| bath \| balcony \| service \| garage` | `opening` |

Ten of the 25 fields are used by the seed packs: `zoneCategory`, `buildingUse`, `plotAreaSqm`,
`plotAreaMm2`, `roadWidthMm`, `roomType`, `roomIsHabitable`, `openingKind`, `openingRole`, `vastuMode`.
The other fifteen exist because real bye-laws reach for them (corner-plot relaxations, stilt rules,
frontage-based bands, storey-specific ceiling heights) and a reviewer must not have to change the
schema to express an ordinary rule.

### Operators, combination, nulls

Six operators, and only six: **`lt` `lte` `gt` `gte` `eq` `in`**.

* Several fields in one `when` are **AND**ed. Several operators on one field are **AND**ed.
* **There is no OR and no NOT.** Use `in` for set membership, or author two rules. This is deliberate:
  a predicate language with boolean nesting stops being reviewable by an architect, and reviewability
  is the whole point.
* Absent or empty `when` = the rule always applies.
* A numeric operator on a **null** field (`roadWidthMm` on a plot whose road is not set yet) is
  **false**, so the rule becomes `not_applicable`. It never silently passes.

---

## Check semantics

**This table is the tiebreaker.** Three things state these semantics — this table, the fixture
generator, and the engine. When two disagree, this table wins and one of the others has a bug.

`scope` = what the engine iterates to produce one evaluation. Several instances of a scope collapse
into one result row: **worst status wins**, and `elements[]` lists every offender. `resultUnit` = the
unit of `actual` and `limit` in that row.

| type | scope | params | `actual` | `limit` | unit |
|---|---|---|---|---|---|
| `setback_min` | edge | `edge`, `valueMm`, `measure?` | setback provided on the selected edge(s); min across them | `valueMm` | mm |
| `far_max` | project | `ratio`, `premium?` | `farCountableAreaMm2` | `floor(num × plotAreaMm2 / den)` | mm² |
| `coverage_max` | project | `ratio` | `footprintAreaMm2` | `floor(num × plotAreaMm2 / den)` | mm² |
| `height_max` | project | `valueMm`, `excludes?` | `buildingHeightMm − Σ heightComponentsMm[k]`, k ∈ `excludes` | `valueMm` | mm |
| `floors_max` | project | `value`, `counts?` | `storeyCount` + counted extras present | `value` | count |
| `room_area_min` | room | `valueMm2` | `room.areaMm2` | `valueMm2` | mm² |
| `room_width_min` | room | `valueMm` | `room.leastWidthMm` | `valueMm` | mm |
| `ceiling_height_min` | room | `valueMm` | `room.clearCeilingHeightMm` | `valueMm` | mm |
| `ventilation_ratio_min` | room | `ratio?`, `minAreaMm2?`, `countKinds?` | `room.ventilationOpeningAreaMm2` | `max(ceil(ratio × areaMm2), minAreaMm2)` | mm² |
| `stair_riser_max` | stair | `valueMm` | `stair.riserMm` | `valueMm` | mm |
| `stair_tread_min` | stair | `valueMm` | `stair.treadMm` | `valueMm` | mm |
| `stair_width_min` | stair | `valueMm` | `stair.widthMm` | `valueMm` | mm |
| `headroom_min` | stair | `valueMm` | `stair.headroomMm` | `valueMm` | mm |
| `projection_max` | projection | `element`, `valueMm`, `intoSetbackOnly?` | `projection.projectionMm` for matching elements | `valueMm` | mm |
| `parking_min` | project | `basis`, `rate`, `minSpaces?`, `spaceSizeMm?` | `parkingSpacesProvided` | `max(ceil(rate × basis), minSpaces)` | count |
| `opening_width_min` | opening | `valueMm` | `opening.widthMm` | `valueMm` | mm |
| `zone_check` | zone | `target`, `mode`, `allow?`, `deny?`, `fallback?` | sorted unique zone/facing labels of matched targets | `{allow, deny, fallback}` | zone |
| `custom` | per `scope` | `fn`, `scope`, `args` | per fn | per fn | per fn |

`opening_width_min` is **an addition to the list in playbook §6**: §6's seed values include door
minimums (main 900 / internal 800 / bath 750) but none of the 17 listed check types could express
them, and routing something as universal as a door width through `custom` would have hidden it from
the schema. Contract note for the engine: implement 18 types, not 17.

### `custom` — two registered functions, and the enum is closed

| `fn` | scope | args | semantics |
|---|---|---|---|
| `rwh_required` | project | `flag` | `actual = profile[flag]`, `limit = true`. **A declaration check, not a geometric one** — the MVP model has no rainwater-harvesting element, so the pack must not pretend to verify sump volume. That is why all three RWH rules are `severity: warn`. |
| `brahmasthan_open` | project | `maxEnclosedRatio`, `openRoomTypes?` | `actual = max` over non-open rooms of `floor(10000 × area(room ∩ centreCell) / area(centreCell))`; `limit = floor(10000 × maxEnclosedRatio)`. Fails when `actual > limit`. Unit: ten-thousandths. |

Registering a new `fn` means bumping `schemaVersion`. A pack can never name code that does not exist.

### Zone and facing derivation

`zone` (the 3×3 grid): rotate the plot boundary **counter-clockwise by `plot.northDeg`** so true north
becomes `+Y`; take the axis-aligned bounding box; split into 3 equal columns × 3 equal rows, cell
boundaries rounded half-up to whole mm. Top row `NW N NE`, middle `W C E`, bottom `SW S SE`. An
element's zone is the cell containing its centroid; a centroid exactly on a boundary belongs to the
more-north / more-east cell. `C` is the brahmasthan. Centroids are polygon centroids rounded half-up
to whole mm.

`facing` (8 sectors): `azimuth = (element.outwardNormalDeg − plot.northDeg) mod 360`, where
`outwardNormalDeg` is the plot-local bearing measured clockwise from `+Y`. Sectors are 45° wide and
centred on the cardinal: `N = [337.5, 22.5)`, `NE = [22.5, 67.5)`, `E = [67.5, 112.5)`, and so on.

Non-cardinal `northDeg` needs trigonometry for the rotation. Do it in float64, then round cell
boundaries half-up to whole mm before classifying — the *classification* is integer and therefore
reproducible. All Vastu fixtures use `northDeg: 0` so they never depend on that step.

### Statuses

| status | means |
|---|---|
| `pass` | satisfied |
| `warn` | violated, and the rule's severity is `warn` (or a scoring-mode ceiling clamped it) |
| `fail` | violated, and the rule's severity is `fail` |
| `not_applicable` | `when` unsatisfied, or the scope produced no instances. Excluded from scores; the API may omit these rows. |

`fail` means *hard rule*: the solver discards options that violate one (§5.6) and the UI shows a red
chip. Neither status ever blocks the user — compliance informs, the architect overrides, the override
is logged (SKILL.md golden rule 5).

### Two conventions that are easy to get wrong

**Minimums stack.** Two `*_min` rules on the same target both apply and the strictest governs. City
front-setback tables are indexed by plot size **and** road width, and the packs encode them as two
independent rule families whose maximum is the real requirement. Do not try to collapse them.

**A nil requirement produces NO rule.** Delhi allows nil setbacks on small plots in approved layouts;
`ncr.json` expresses that by having no setback rule for that band. Never write `valueMm: 0` — a zero
minimum can never be violated, so it would sit in the compliance report looking checked while
checking nothing. Absence of a rule means "no minimum", and the pack's `notes` says so out loud.

---

## Scoring packs (Vastu)

A pack with a `scoring` block produces a 0–100 score alongside its result rows, and every rule in it
must carry `weight` and `group` (the schema enforces this conditionally).

```
score = scale.max × Σ(weightᵢ × satisfactionᵢ) / Σ(weightᵢ)      over APPLICABLE rules only
```

* `satisfactionᵢ` is an exact rational in [0,1]: **1** in `allow`, **`fallback.scoreRatio`** in
  `fallback.allow`, **0** otherwise. With several matched elements it is their arithmetic mean, still
  exact.
* A rule with no matching element is `not_applicable` and drops out of **both** sums — a house with no
  pooja room is not penalised for its placement.
* Round half-up **once**, on the final score. Never on an intermediate.
* `vastu.json`'s nine weights sum to 100, so when every rule applies the weighted mean reads directly
  as the score. `verify_fixtures.py` enforces that sum.

One rule set serves both brief modes, so no rule id is duplicated:

| `vastuMode` | behaviour |
|---|---|
| `off` | the pack is **not loaded at all** |
| `advisory` | `severityCeiling: warn` — every severity clamps to `warn`; score only, solver does not constrain |
| `strict` | `severityCeiling: fail` — severities pass through; the solver may encode rules marked `hard: true` as CP-SAT constraints (§5.2) |

`vastu.toilet.never_ne` is the only `hard: true` rule. Its `deny: ["NE"]` is absolute: a target outside
`deny` passes even though the rule declares no `allow` list.

`scoring.groups` are the buckets the compass-wheel UI renders. Group weight is **derived** (the sum of
its member rule weights) and never duplicated in the pack.

---

## `extends`, overrides, vocabulary

Load the parent chain root-first, then this pack's rules. A cycle is a load error.

**Rule ids are globally unique.** A child pack never shadows a parent id — it disables the parent rule
and adds its own:

```json
"overrides": [
  { "ruleId": "nbc.ceiling.habitable.min", "action": "disable",
    "reason": "State amendment permits 2.6 m in this jurisdiction; see hyd.ceiling.habitable.min." }
]
```

`action` is `disable`, `relax-to-warn` (keep the check, clamp the severity) or `replace` (requires
`replacedBy`). `reason` is mandatory and shows up in the review record. Uniqueness is what lets a
compliance report from six months ago still be explained: an id means one thing forever. If a rule's
meaning changes, retire the id and add a new one.

`vocabulary` is merged **key by key** — a child key replaces the parent key wholesale, never
element-wise. It holds the classifications that are regulatory judgements rather than code constants:

| key | used by |
|---|---|
| `habitableRoomTypes` | derives the `roomIsHabitable` context field |
| `wetRoomTypes` | solver plumbing-stack score, ventilation defaults |
| `openRoomTypes` | `brahmasthan_open` |
| `farExclusions` | what the model layer subtracts to get `farCountableAreaMm2` |
| `coverageInclusions` | what the model layer adds to the ground slab to get `footprintAreaMm2` |

These live in the pack because "is a study a habitable room" and "does the stilt count toward FAR" are
questions the bye-law answers, not questions the code should hard-code.

---

## Authoring a city pack

1. **Copy the nearest existing city pack** and change `pack`, `idPrefix`, `version` (`YYYY.MM`),
   `title`, `authority`, `jurisdiction`, `citations_base`.
2. **List your sources honestly.** `sources[].obtained: false` means the authoring team did not have
   the primary document in hand. Do not quietly set it to `true`.
3. **Transcribe tables, do not invent them.** One rule per table cell, `when` reproducing the band
   exactly. Bands within one family must not overlap for `far_max`, `coverage_max`, `height_max`,
   `floors_max` or `parking_min` — exactly one must apply. Setback families are the exception; they
   stack.
4. **Never write a zero minimum.** A nil statutory requirement produces no rule, plus a line in
   `notes` saying which band it applies to.
5. **Write `title`, `message` and `fix` for a human.** `message` is the compliance chip: plain, warm,
   never blaming, with `{actual}` / `{limit}` placeholders. "Bedroom 2 is 8.9 m² — NBC needs 9.5 m²",
   not "ROOM_AREA_VIOLATION".
6. **Cite every rule.** `cite` is appended to `citations_base`; a rule with an empty `cite` fails
   `verify_fixtures.py`. If you genuinely cannot find the clause, that is a finding for the reviewer,
   not a blank field.
7. **Mark confidence honestly.** New authoring is `seed` unless a reviewer has signed it.
8. **Add `autofix` only where the fix is computable** from the model alone. A fix that needs a re-solve
   or a human decision gets `computable: false`, so the UI shows the hint without a button that lies.
9. **Regenerate and verify:**
   ```
   python3 fixtures/rules/_tools/generate_fixtures.py
   python3 fixtures/rules/_tools/verify_fixtures.py
   ```
   Then **read the fixture diff**. A generated fixture whose numbers look wrong means the rule is
   wrong.
10. **Register the pack** in `rulepacks/index.json` (that manifest is what `GET /rulepacks` serves) and
    add the pack id to the `cityPack` enum in `schema/rulepack.schema.json` and
    `schema/fixture.schema.json`.

### What is deliberately not modelled in `schemaVersion: 1`

Named here so nobody approximates them and calls it done:

* **Height as a formula** — Karnataka's `height ≤ 1.5 × (road width + front setback)` and Telangana's
  equivalent. Needs a check type that takes an expression over context fields.
* **Dwelling-unit caps per plot** (MPD-2021).
* **Setbacks jointly banded on plot area *and* building height** (Telangana Table III) — the seed packs
  band on plot area and road width only, and `hyd.json`'s `notes` says so.
* **Tot-lot / organised open space, fire-tender access, lift requirements, staircase count for
  higher occupancies.**

Approximating any of these would be worse than omitting them: an architect would see a green
compliance panel and reasonably conclude the check ran.

---

## The confidence ladder

Every rule carries `confidence`, and the pack carries `review.status`. The UI shows both next to every
result, and seed values render with a caution marker.

| `confidence` | What it means | What it takes to get here |
|---|---|---|
| **`seed`** | Drafted by the Garh AI team from secondary summaries. **Not authoritative.** Structure is probably right; numbers are plausible defaults. | Nothing. This is where authoring starts. |
| **`reviewed`** | An empanelled local architect checked the value and the citation against the primary bye-law document. | Reviewer holds the primary document, confirms value + clause reference, records their CoA number in `review.reviewers`. |
| **`verified`** | Reviewed **and** confirmed against real-world outcome — a sanctioned drawing or a municipal desk that accepted it. | A cited sanctioned project or a documented municipal confirmation. |

`review.status` is the pack-level position: `unreviewed` → `in-review` → `reviewed` → `verified`. A
pack is only as good as its weakest rule, so `reviewed` requires **every** rule at `reviewed` or
better.

Every pack also carries a `disclaimer` string. **The UI and every export must show it verbatim**
alongside results from that pack. Advisory, not approval; the architect of record remains responsible.

---

## Review workflow

1. **Assign.** One empanelled architect per city, with a named backup. Set `review.status: "in-review"`
   and open a review issue listing every rule id in the pack.
2. **Obtain primaries.** Purchase or download the actual bye-laws and NBC volumes. Update
   `sources[].obtained`. This is the step that cannot be skipped — everything downstream is secondary
   until it happens.
3. **Rule by rule**, the reviewer confirms or corrects: the value, the band in `when`, the `cite`
   string, the severity, and the `fix` wording. Corrections land as edits to the pack JSON with the
   rule's `confidence` raised to `reviewed`. Structural changes (a band that needs splitting, a rule
   that needs retiring) go through `overrides` or a new id — **never** by repurposing an existing id.
4. **Regenerate fixtures, read the diff.** Every changed value moves two fixtures. The diff is the
   review artefact: it shows exactly which boundary moved and by how much.
5. **Sign off.** Add the reviewer to `review.reviewers` with `name`, `role`, `coaNumber`, `signedAt`.
   Set `review.status`, `review.lastReviewedAt`, and `review.nextReviewDue`.
6. **Version and ship.** Bump `version` to the review month (`YYYY.MM`). Compliance reports pin pack
   versions, so an old report stays explainable by the exact rules that produced it.
7. **Re-review on a schedule and on amendment.** Bye-laws are amended; `nextReviewDue` is not
   decorative. A pack past its due date should surface in the UI as stale.

**Never**: silently raise a `confidence`, edit a value without moving its fixtures, or let a pack ship
with a rule that has no failing fixture. All three are caught by `verify_fixtures.py`; the point of
saying it here is that they are also review failures, not just build failures.

---

## Fixtures

`fixtures/rules/<packId>/<ruleId>.pass.json` and `.fail.json`, enumerated by
`fixtures/rules/index.json`. See `fixtures/rules/README.md`. Two commands:

```
python3 fixtures/rules/_tools/generate_fixtures.py   # rewrite the corpus from the packs
python3 fixtures/rules/_tools/verify_fixtures.py     # CI gate; exit 1 with findings
```

`verify_fixtures.py` runs **before** the engine tests in CI and checks the things a green engine suite
would otherwise hide: duplicate rule ids, a rule with no failing fixture, a fixture orphaned from the
manifest, an unknown `when` field or check type, a float in any pack or fixture, fixture room geometry
that disagrees with its own polygon, and a seed pack claiming a confidence it has not earned.

It also cross-checks tables that were transcribed separately: a `floors_max` rule whose floor count
cannot fit under an overlapping `height_max` rule at NBC's minimum floor-to-floor is a load error, not
a warning. That check found a real bug in the first draft of `hyd.json` (5 floors permitted on a 9–18 m
road against a 15 m cap for 9–12 m roads), which is why the Hyderabad floor bands are now aligned with
its height bands.
