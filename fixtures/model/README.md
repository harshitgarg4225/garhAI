# `fixtures/model/` — the cross-language contract for the model core

The model core exists twice: `packages/model/src/*.ts` (canvas, copilot preview,
3D) and `apps/api/garh_model/*.py` (API, solver, sheet engine, exports). They
must agree exactly, because the same design is folded on both sides and
`design_versions.snapshot_hash` is compared across them.

These two files are how the agreement is **tested** rather than assumed. They are
language-neutral JSON: no TypeScript, no Python, no test framework. Both suites
read them.

| File | What it pins | Read by |
| --- | --- | --- |
| `golden-units.json` | 67 `[input, expectedMm]` parse pairs + 16 inputs that MUST fail | `garh_model/units.py` (`load_golden_units`) · `packages/model/src/units.test.ts` |
| `golden-states.json` | 11 op logs and the `stateHash` each folds to | `garh_model/tests/test_fold.py` · `packages/model/src/fold.test.ts` |

## `golden-units.json`

Golden Rule 6 — *mm in, pretty out*. Every length an architect can type
(`12'6"`, `3.8m`, `12 ft 6 in`, `3,810`) has one integer-mm answer, and the two
parsers must give the same one. Rounding is **half away from zero**
(`x >= 0 ? floor(x+0.5) : -floor(-x+0.5)`) — not banker's rounding, which both
`Math.round` and Python's `round()` would give you by accident.

The `failures` list matters as much as the pairs: an input the parser cannot
understand must raise, never guess (`12 6` is not twelve foot six).

## `golden-states.json`

Each case is `{name, description, unitsDisplay, ops[], expectedStateHash}`.
Both languages apply `ops` to `emptyProjectDoc(unitsDisplay)` and assert
`stateHash(doc) == expectedStateHash`, where

```
stateHash(v) = lowercase_hex( sha256( utf8( canonicalJson(v) ) ) )
```

and `canonicalJson` is the spec at the top of `fold.ts` / `fold.py`
(`garh-canonical-json/v1`): integers only, keys sorted by Unicode code point,
minimal string escaping, no whitespace, arrays sorted by element id before
hashing.

The cases are chosen to cover the parts that can silently drift:

- `empty-document` — the defaults and the exact key set of a `ProjectDoc`.
- `plot-only`, `brief-merge-patch` — the non-geometric halves of the document,
  including RFC 7386 merge-patch deletion semantics.
- `two-room-plan`, `two-room-plan-with-openings`, `rooms-assigned` — the planar
  subdivision, the clear-area arithmetic, and the DERIVED room/slab ids (which
  are part of the hash, so they pin `derivedId()` and `polygonKey()` too).
- `wall-split-and-move` — opening re-hosting and room-id preservation.
- `storeys-stair-levels` — G+1, stair-in-slab cut-outs, FFL derivation.
- `furnishings-and-facade` — every "default applied by fold" path.
- `solver-apply-option` — an atomic expansion must equal applying its inner ops.
- `unicode-and-escapes` — Devanagari, ₹, an em dash, an emoji and C0 controls,
  because a document with a client's name in it must hash the same in Node and
  CPython.

### Regenerating

```sh
python3 fixtures/model/_tools/generate_golden_states.py          # rewrite
python3 fixtures/model/_tools/generate_golden_states.py --check  # CI: verify
```

**A failing row is never fixed by pasting the new hash.** It means the two
implementations disagree about what a design *is*. Work out which side moved. If
the change is intended, regenerate in the same commit as the behaviour change and
add a `DECISIONS.md` note — every stored `snapshot_hash` in the database becomes
wrong at that moment, so it is a migration, not a tweak.

### Known, deliberate property: room ids are history-dependent

A room keeps its id across edits (maximum-Jaccard match), which means the id a
room ends up with depends on the order the walls arrived in. Build the outer box
first and the whole floor is one room whose id one half then inherits when the
spine splits it; draw the spine first and both halves are minted from their own
polygons. Same drawing, same areas, different room ids — and therefore a
different `stateHash`.

That is why these fixtures pin an op **log** and not a picture. Both languages
implement the same rule, so they agree; a fixture that asserted "this geometry
hashes to X" would be wrong on both sides.
