# Garh AI — instructions for a coding agent

You are picking up a partly-built product. This file tells you where it stands,
what is actually proven, and what will bite you. Read it, then read
`docs/spec/README.md` and the two playbooks it points at, before writing code.

## What this is

An India-first, AI-native house-design platform for architects: plot + client
brief → compliant AI floor-plan options → 2D/3D editing (direct or by natural
language) → AI facades and renders → a municipal submission drawing set →
PDF/DXF export. The moat is the **drawings**, not the plan generation — an Indian
architect's fee is earned on drawings, and concept-only tools save ~10% of their
effort where the municipal set targets ~70%.

## First: run the one command that works anywhere

```bash
make bare
```

Seven gates, no dependencies, ~2 seconds, runs on a bare Python 3.9 interpreter.
It is the only verification available before a toolchain exists, and it is real:
rule fixtures through the actual engine, the copilot eval corpus and containment
suite through the actual fold, the solver's dependency-free half, plus the
tenancy, secret, env and web-asset audits.

If it is green, the proven core is intact. Run it after every change.

## The honest state of this repository

**Updated 2026-08-26 — the "never executed" era is over.** The repository now runs
under its real toolchain (Node 22, pnpm 9, Python 3.11, Postgres 16, Redis,
headless Chromium) and is deployed as an 8-service Railway stack built from these
Dockerfiles. The original state — ~180k lines authored on a machine with no Node,
no Docker, Python 3.9 — still explains the code's shape and its bug pattern
(below), but the split has moved:

|                              |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Executed and green**       | 1,948 api+rules+model Python tests, 405 services tests, 1,585 JS unit tests, strict `tsc`, the production Vite build, ruff/eslint/prettier at zero errors, mypy --strict on garh_model + services/common + services/llm. In a real browser against a live stack: the @smoke journey (login → demo project → six tabs → DXF import round trip → sign-out), the copilot DoD walk, the §13 share-link journey, the full Generate → 3 options → apply loop. The Phase-8 drawings pipeline: 9 municipal sheets, DXF audits clean, a 10-page vector PDF set. Backup + restore rehearsal and a load smoke (1,280 req/s, p95 17 ms) have each run once for real. |
| **Still never run (or red)** | The real Anthropic/Stability providers (adapters built and unit-tested; keys are a launch gate). The visual-regression suite (needs a CI-minted baseline). mypy debt outside the strict-clean trees (garh_api 56, garh_rules 44, solver 79, render 28, drawings 801 — counts in ci.yml). Everything else in this row's history has fallen: CI run 11 (2026-08-27) is fully green through all seven stages, and its e2e-smoke job runs `docker compose up` verbatim — build, boot to Healthy, seed, Playwright smoke in a real browser — so the compose wiring is proven on every push now.                                                               |

`docs/phase-*-verification.md` is one ledger per phase, each splitting its claims
into **EXECUTED / TRACED / UNVERIFIED** with the exact command that would settle
each unverified item. Trust those ledgers over any summary, including this one.

## Do this first, before building anything new

```bash
make lockfile      # needs Node 20 + pnpm 9. Six CI jobs fail at step one without it.
make up            # docker compose: postgres, redis, minio, api, web, 3 workers
make migrate seed  # schema + demo firm + demo project
make verify        # lint, typecheck, unit, golden — expect real failures, that is the point
```

Expect a wall of errors on the first `typecheck`. `tsconfig` sets
`exactOptionalPropertyTypes` and `noUncheckedIndexedAccess` and nothing has ever
compiled under them. **Fixing those is worth more than another feature**, because
every phase built on top of unexecuted code inherits its bugs silently.

## Bugs this repo has already had, so you can recognise the pattern

These are not trivia. Each is a class of failure that static review missed and
execution caught, and each will recur if you build fast without running things.

1. **A gate that silently never fires.** Circulation percentage was measured
   against a ground-floor-only denominator while every storey's rooms were passed
   in, driving it to zero and disabling the §5.6 circulation cap entirely. Every
   line read correctly. It only appeared when the code ran.
2. **83 rules going quietly inert.** The evaluation context defaulted
   `buildingUse` to `"residential"`, which is not a member of the packs' own enum,
   so every city setback, FAR, coverage and height rule reported
   `not_applicable` — while the compliance report still looked green.
3. **A test that could not fail, hiding a live data leak.** The test guarding the
   LLM prompt against PII seeded a field the summariser structurally never reads.
   It passed with the allowlist deleted, and it passed while a user-authored
   storey name (with a phone number in it) was being sent to the provider.
4. **A module that believed it was registered.** The furniture layer tagged its
   meshes for hit-testing and documented itself as integrated, but never called
   the registry, so every placed item was invisible to clicks. There is no
   compile-time signal for this — any new canvas layer must be click-tested.
5. **A limiter shared by two routes, and a fix that would have been worse.** The
   60-second OTP resend cooldown was keyed on the address alone, so a sign-in for an
   address with no account (a deliberate 202 that sends nothing) blocked the sign-up
   thirty seconds later — the first thing the first trial architect did. The obvious
   fix, "don't charge unknown addresses", opens an enumeration oracle. The key is now
   per route, and the test file carries a negative control in each direction.
6. **A production artifact nothing had ever executed.** The workers' Docker image
   omitted `fixtures/`; the solver opens the furniture catalogue from there after
   stage B. CI's compose e2e was green because it builds the `dev` stage and
   bind-mounts the repo, so the prod stage's COPY list ran for the first time on the
   first trial architect's first Generate — four retries, two credits burned, and an
   error card blaming the brief. A test that exercises a substitute for the real
   artifact proves the substitute. `test_catalog_in_image.py` now reads the
   Dockerfile itself.
7. **A green report over an unlivable plan.** The first library plan's front door
   opened into a dead-end vestibule and its kitchen was entered through the bath;
   the compliance report was 0 fail because no loaded rule looks at doors. The solver
   had marked circulation rooms "reached" across a solid wall without emitting an
   opening, for every plan it had ever produced. The gate had to be written
   (`garh_model.circulation`, wired into the solver's own gates), and it was found
   only by an adversarial reader folding the recipe and walking it. When a check
   passes, ask what it does not look at.

The through-line: **a green check that cannot go red is worse than no check.**
When you add a gate, negative-test it — break the thing deliberately and confirm
the gate fails.

## Where things live

```
packages/model/      TS model core: 32 ops, fold/replay, state hash, room detection
apps/api/garh_model/ the Python twin — MUST stay byte-identical on state hashes
apps/api/garh_rules/ rules engine (pure stdlib — that is why it is provable)
apps/api/garh_api/   FastAPI: tenancy repos, auth, op sequencer, jobs, sheets
services/            solver · drawings · render · llm workers, all behind mocks
apps/web/src/features/  plot · brief · options · canvas (2D+3D) · copilot · renders ·
                     references (the inspiration board) · sheets
rulepacks/           nbc-core + blr/ncr/hyd + vastu, every value marked confidence:"seed"
fixtures/            briefs, plans, rules, catalog, llm corpora, sheet goldens
docs/spec/           THE BINDING SPEC — read before coding
docs/phase-*-verif*  what is proven, per phase
docs/*-verification.md  same split for features that are not a phase
                     (first-run, inspiration-board)
DECISIONS.md         every deviation and every dependency, with reasons
```

## Conventions that are load-bearing

- **Integer millimetres**, model-wide. Parse user input (`12'6"`, `3.8m`) to mm at
  the boundary via `packages/model/src/units.ts`; format on the way out. Rounding
  is half-away-from-zero, _not_ `Math.round`.
- **One picker, one scene.** 2D and 3D share a single R3F canvas, camera rig and
  `PickRegistry`. Never add a second `<Canvas>`; never raycast outside the
  registry.
- **One source for compliance numbers.** FAR, coverage and setbacks in the area
  statement come from the same rules-engine helper the UI calls. Two sources of
  truth for FAR is a liability bug in a product selling citable compliance.
- **Renderers consume shared primitives.** SVG, DXF and PDF all render the same
  primitive list, so geometry cannot drift between formats.
- **Mocks are the default.** `PROVIDER_LLM=mock`, `PROVIDER_RENDER=mock`,
  `PROVIDER_BILLING=mock`. The whole product must run and be e2e-testable with
  zero API keys and zero GPUs. Keep it that way.

## Known blockers, named plainly

1. **CI went fully green on 2026-08-27 (run 11), eleven runs after it first
   fired.** Every run in between failed on a REAL latent defect the runner was
   the first environment to execute: lint debt (~2,638 ruff + ~540 eslint,
   burned to zero), a licence-metadata gap, pip-audit CVEs in five pins (all
   bumped), a pytest marker selecting nothing, and — once `docker compose up`
   ran for the first time ever — three boot crashes compose had always carried:
   complex env fields json-decoded before their validators (NoDecode), dotenv
   reading inline comments as values, and cwd-relative data-dir overrides.
   Keep it green; a red run is a real finding, not noise.
2. **The Inter label font LANDED (2026-08-26).** Fetched from the rsms/inter
   v3.19 GitHub release with its OFL-1.1 licence text beside it; `make
asset-audit` runs clean with zero known gaps for the first time. Canvas and
   sheet labels now render in the real face.
3. **The rule-pack values are seeds, not law.** Every value is marked
   `"confidence": "seed"` and needs review by empaneled local architects per city
   before anyone submits a drawing to a municipality. The UI surfaces citation
   and confidence for exactly this reason.
4. **Phases 8–9 may be incomplete.** Check `docs/phases.md` and the newest
   `docs/phase-*-verification.md` for the true edge.

## The ready-made plan library, and how a plan gets in

`fixtures/plans/<id>.json` is a template the New-project dialog offers as a
**ready-made plan**: a solved, compliant house captured from a REAL solver run, never
typed by hand (the demo seed's `solved_plan_ops` docstring is the rule). To add one:

```bash
# local stack up (api + solver worker), then:
GARH_API=http://127.0.0.1:8000/api/v1 python scripts/seed_plan_library.py <cell-id>
python scripts/flatten_plan_recipes.py          # idempotent: unwraps solver.apply_option, adds the reg profile
PYTHONPATH=.:apps/api python scripts/render_plan_previews.py   # <id>.svg through the sheet renderer
```

Add the cell (plot, city pack, storeys, rooms, **carParking**) to `CELLS` in the seed
script first. `test_plan_library.py` then requires: no `solver.apply_option` wrapper,
fold counts equal to what was captured, the stored SVG byte-equal to a fresh render,
and a project created from it with no `fail` on the compliance tab. That last gate is
stricter than the solver's own (which blocks only on `hard: true` rules) — a plan can
pass Generate and still fail here, and that is the point: nobody should pick a
"ready-made" plan and see red.

## The inspiration board, and what it does not claim

A client sends pictures. Each one on the board carries four answers the **architect**
writes — where it applies, what to take, what to leave, how hard to push — and the
product never guesses any of them. It does not read the image or parse the filename; a
guess here is wrong exactly often enough to be untrustworthy, and its mistakes are
invisible in a render.

What the product contributes is the pre-render review: three deterministic questions
(two `match` intents on one scope, a scope this view cannot use, an empty annotation),
each stating what happens if the architect does nothing. Deliberately NOT a rule:
reading two `why` texts and deciding they disagree. That needs to understand English, it
would be wrong sometimes, and a wrong question is worse than no question.

`docs/inspiration-board-verification.md` is the ledger. The honest headline: the whole
loop is proven under `PROVIDER_RENDER=mock`, including a live 10-step journey ending
with a render that names the reference it followed. Whether a frontier model actually
follows an architect's phrasing needs the real provider and a human panel — the same
launch gate the copilot has.

## What "the copilot works" does and does not mean

The 40-command corpus passes against the **mock** provider, which answers from a
fixture keyed on command text. That proves the pipeline — schema gate, real fold
on a fork, rules diff, apply/reject — end to end. It says nothing about whether a
frontier model understands an architect's phrasing. That needs the real provider
and a human panel, and it is a launch gate, not a checkbox.

Same caution applies everywhere: read what a passing test actually asserts before
banking it.
