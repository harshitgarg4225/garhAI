# Rule fixtures

The gate on the rules engine. Playbook §16: **every rule has at least one passing and at least one
failing fixture.** 118 rules, 238 fixtures.

```
fixtures/rules/
├── index.json          # manifest — enumerate from HERE, never by globbing
├── nbc-core/           # 46 fixtures
├── blr/                # 66
├── ncr/                # 38
├── hyd/                # 68
├── vastu/              # 20 (18 + 2 behaviour cases)
└── _tools/
    ├── generate_fixtures.py   # rewrites the corpus from rulepacks/
    └── verify_fixtures.py     # CI gate, exit 1 with findings
```

Format: `rulepacks/schema/fixture.schema.json`. Its `$defs.evaluationContext` is also the **engine's
input contract**, so the engine's context builder and these fixtures are held to one shape.

## Naming

| file | `kind` | meaning |
|---|---|---|
| `<ruleId>.pass.json` | `pass` | the rule is satisfied, **exactly at the limit** |
| `<ruleId>.fail.json` | `fail` | the rule is violated, **by one unit** |
| `<ruleId>.extra-<slug>.json` | `extra` | an additional behaviour case |

`fixtureId` always equals the filename without `.json`.

Two extras exist today, both for Vastu behaviour that pass/fail cannot express:
`vastu.kitchen.zone.extra-fallback` (kitchen in NW → half score, `warn`) and
`vastu.toilet.never_ne.extra-advisory` (the same NE toilet that is `fail` in strict mode, clamped to
`warn` in advisory mode).

## What a fixture asserts

Exactly one result row — the rule named in the filename:

```json
"expected": { "status": "fail", "actual": 9496960, "limit": 9500000, "elements": ["room_bed2"] }
```

`actual` and `limit` are in the check's `resultUnit` (see the check-semantics table in
`rulepacks/README.md`). `elements` lists offenders and is empty on a pass. Scoring-pack fixtures also
carry `satisfaction` as an exact `{num, den}`.

**A fixture asserts that row only.** Other rules may pass or fail in the same context. Where a rule
genuinely cannot be violated in isolation, the fixture says so in `notes` rather than pretending —
e.g. adding the fourth floor to fail `blr.floors.road.lt9m` unavoidably also breaks the 11.5 m height
cap, because a storey cannot be added without height.

## Why the corpus is generated

238 fixtures written by hand would be inconsistent, and the interesting property is uniformity: a
passing fixture sits **on** the limit and a failing one misses it **by one unit**, for the single input
the rule measures, inside a context whose other values were resolved from the pack's own tables to be
compliant. `_tools/generate_fixtures.py` does that mechanically.

Read that as: the generator is a **second, independent statement of the check semantics**. The first is
the check-semantics table in `rulepacks/README.md`; the third will be the engine. If a fixture and the
engine disagree, that table is the tiebreaker and one of the other two has a bug.

The committed JSON is the artefact. Review the diff; do not trust the script.

## `status` on a failing fixture is not always `fail`

It is the rule's `severity`, clamped by any scoring-mode ceiling:

* `severity: fail` → `expected.status: "fail"` (most rules)
* `severity: warn` → `expected.status: "warn"` (the three RWH rules, most Vastu rules)
* Vastu in `advisory` mode → every severity clamps to `warn`

`verify_fixtures.py` checks this correspondence, so a fixture cannot quietly disagree with its rule.

## Honest limits of these contexts

* `context.model` is an **excerpt**, not a plausible plan. Rooms are laid out in a row with 200 mm gaps
  so their geometry is unambiguous and self-checking; they are not a house. Room-scope fixtures carry
  the full room programme, project-scope fixtures a reduced four-room set.
* Rooms exist only on the ground storey even when `storeyCount` is 3 or 4.
* Where a seed pack's coverage or FAR cap is not simultaneously reachable with its own setback table on
  a given plot size, `notes` says so and names the buildable envelope. That is a finding for the
  reviewing architect, not a bug in the fixture — on small plots the setback table is genuinely the
  binding constraint.
* Every context uses `northDeg: 0`, so no fixture depends on the float rotation step in the 3×3 zone
  derivation.

## Self-checking geometry

Every room carries `polygonMm` **and** the pre-derived `areaMm2`, `leastWidthMm`, `centroidMm`.
`verify_fixtures.py` recomputes all three from the polygon (shoelace area, bbox least width, polygon
centroid rounded half-up) and fails on any disagreement — a fixture cannot lie about its own geometry.
The engine reads the pre-derived scalars; that is what keeps it free of geometry work and inside the
100 ms budget.

## Running

```
python3 fixtures/rules/_tools/generate_fixtures.py   # after ANY pack edit
python3 fixtures/rules/_tools/verify_fixtures.py     # in CI, before the engine tests
```

Both need only the standard library — no dependencies, no network.
