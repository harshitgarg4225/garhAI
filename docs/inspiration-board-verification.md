# Inspiration board (§11) — what is proven, and how

Same split as every `docs/phase-*-verification.md`: **EXECUTED** means a command was run
and its output read; **TRACED** means the code was followed by hand; **UNVERIFIED**
means nobody has run it, and the command that would settle it is named.

Trust this file over any summary, including the commit messages.

---

## What the feature is

A per-project board of the pictures a client sent. Each one carries four answers the
architect writes:

| Question              | Field    | Why it exists                                                                      |
| --------------------- | -------- | ---------------------------------------------------------------------------------- |
| Where does it apply?  | `scope`  | "Use this kitchen" cannot inform a street elevation.                               |
| What should we take?  | `why`    | A picture alone is ambiguous — cabinets, island, or light?                         |
| What should we leave? | `ignore` | "Not like this" is what clients say most, and no tool records it.                  |
| How hard to push?     | `intent` | `match` / `guide` / `avoid`. `avoid` is the opposite of `guide`, not a weaker one. |

The product does **not** annotate. It does not read the image, parse the filename, or
infer a scope. What it contributes is the pre-render review: the questions it can
justify, all deterministic, each stating what happens if the architect does nothing.

---

## EXECUTED

### The rules, in isolation

`pytest services/render/tests/test_references.py` — 29 tests. Scope filtering, the three
conflict rules, prompt fragment assembly, and the property that an empty board produces
byte-identically the prompt this product produced before the board existed.
**4 negative controls**, each applied and reverted.

### The wiring, from a job payload to an observable result

`pytest services/render/tests/test_reference_wiring.py` — 11 tests. Every case starts
from the payload shape the API actually enqueues and ends at the prompt string a
provider receives or the credit list a finished render carries. Nothing is asserted
about an intermediate object that could be built correctly and then dropped — that is
CLAUDE.md's fourth bug class, and it is what this file exists to catch.

### The API

`pytest apps/api/tests/test_references_routes.py` — 19 tests: upload (sniffed, capped,
415/413/400), the four answers round-tripping, partial PATCH, the vocabulary refusal,
wrong-project 404, delete, the review's three question kinds, and the credit on a
finished render.

`pytest apps/api/tests/test_cross_tenant.py` — 5 new `Case` rows against a **real**
firm-A reference, so their 404s prove tenancy and not absence. The route-coverage
walker passes, meaning no board route is reachable without a tenancy case.

`pytest apps/api/tests/test_reference_vocabulary.py` — `models.REFERENCE_SCOPES` equals
`services.render.references.SCOPES`, and the migration's CHECK lists the same values.
`references.test.tsx` closes the triangle from the web side.

### The web

`vitest run src/features/references/` — 17 tests on a real DOM: the annotation calls,
the unannotated count, the review panel, and the launcher hook. **4 negative controls**,
each applied, measured and reverted (1, 2, 1 and 1 failures respectively).

### End to end, against a live stack

`python scripts/reference_journey.py` — **10/10 steps, run 2026-08-31** against
Postgres 16, Redis, moto S3, a live API and a live render worker on `PROVIDER_RENDER=mock`:

```
 1. signed in
 2. Sharma Residence
 3. pinned (640x480), unannotated as expected
 4. asked: What should Reference 1 contribute? …
    default: It is skipped — an unannotated picture cannot steer a render.
 5. where: facade · take: a deep shaded verandah with slender teak columns
 6. will draw:  closely following a deep shaded verandah with slender teak columns
    will avoid: the mirror-glass balustrade
 7. interior-living: not used here, and it says so
 8. job queued
 9. render succeeded, and it followed: Client's verandah photo
10. removed
```

Step 9 is the whole point. Everything before it can be green while the board
contributes nothing.

---

## What the live run found that every unit test missed

**The credit was computed, logged, and dropped one layer before the architect.**

`render_jobs` kept only `output_url` from a worker's result, so
`JobResult.data["references"]` — which the unit tests asserted — never reached the API
response. The board looked wired end to end, the review approved the reference, the
render followed it, and "did this render use my reference?" still had no answer on the
render.

Fixed by migration `0009_render_references_used`, a column of its own rather than a key
in `params`: `params` is the REQUEST (the board as it stood at enqueue) and
`references_used` is what the prompt consumed. A render can carry a reference it could
not apply, and putting both facts in one place is how they end up disagreeing.

Two negative controls confirm the fix cannot rot: removing the carry in the lifecycle
consumer fails the credit test, and hard-coding the schema field non-empty fails both it
and its control.

Four smaller defects, each caught by a check rather than by reading:

1. The lazy `services.render` import raised an unhandled `ImportError` instead of the
   copilot route's honest 503.
2. The payload parser accepted an id-less entry — a reference that can never be
   credited, showing as a blank chip.
3. Four Tailwind classes did not exist in the design system. Tailwind drops unknown
   classes silently, so the board would have shipped with unreadable text and no error.
4. The web test helper dispatched `blur`, which does not bubble; React 18 listens for
   `focusout`. Every annotation assertion was passing against a component that could
   have had no handler at all.

---

## UNVERIFIED

| Claim                                                                                        | The command that would settle it                                                                                                                                                                                  |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A real diffusion model follows an architect's phrasing well enough to be worth the question. | `PROVIDER_RENDER=stability STABILITY_API_KEY=… python scripts/reference_journey.py`, then a human panel comparing renders with and without the board. The mock provider proves the pipeline, never the judgement. |
| The board survives a Railway deploy.                                                         | `alembic upgrade head` on the deployed stack, then `GARH_API=https://… python scripts/reference_journey.py`.                                                                                                      |
| The board renders correctly in a browser.                                                    | It is covered by jsdom tests and `tsc`, not by a screenshot. `pnpm playwright test` with a board step, or the visual-regression baseline, would settle it.                                                        |
| The conflict rules match how architects actually think.                                      | Nothing in this repository can answer that. It needs the empanelled-architect review already named as a launch gate.                                                                                              |

## Deliberately not built

**Reading the `why` text of two references and deciding they disagree.** That needs to
understand English, it would be wrong sometimes, and a wrong question is worse than no
question — the architect stops reading them. The three rules that exist are structural:
two `match` intents on one scope, a scope this view cannot use, and an empty annotation.
