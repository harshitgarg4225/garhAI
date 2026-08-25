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

**~180k lines. Almost none of it has ever executed.** The machine it was built on
had no Node, no pnpm, no Docker, and Python 3.9 where the playbook wants 3.11.
That single fact explains the shape of everything here.

| | |
|---|---|
| **Genuinely executed** | The rules engine (5 packs, 118 rules, 238 fixtures). The copilot validation loop (40-command corpus, 46 containment checks). The solver's ortools-free half (envelope, furniture fit, critic, gates). The auto-dimensioning core if Phase 8 finished. |
| **Written, never run** | Every line of TypeScript (~120k) — never compiled, never rendered. The CP-SAT solver stage A (`services/solver/stage_a.py`, 1,314 lines, needs OR-Tools). Every Docker and Postgres path. All DXF output (needs `ezdxf`). |

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
apps/web/src/features/  plot · brief · options · canvas (2D+3D) · copilot · renders · sheets
rulepacks/           nbc-core + blr/ncr/hyd + vastu, every value marked confidence:"seed"
fixtures/            briefs, plans, rules, catalog, llm corpora, sheet goldens
docs/spec/           THE BINDING SPEC — read before coding
docs/phase-*-verif*  what is proven, per phase
DECISIONS.md         every deviation and every dependency, with reasons
```

## Conventions that are load-bearing

- **Integer millimetres**, model-wide. Parse user input (`12'6"`, `3.8m`) to mm at
  the boundary via `packages/model/src/units.ts`; format on the way out. Rounding
  is half-away-from-zero, *not* `Math.round`.
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

1. **`pnpm-lock.yaml` does not exist.** Six CI jobs fail at their first step.
   `make lockfile` on a machine with Node.
2. **`apps/web/public/fonts/inter-medium.woff` is missing.** OFL-1.1, a human
   must fetch it. Without it every dimension and room label renders in a fallback
   face — or not at all, since the CSP blocks the CDN. `make asset-audit` reports
   it as a release blocker on every run rather than letting it ship silently.
3. **The rule-pack values are seeds, not law.** Every value is marked
   `"confidence": "seed"` and needs review by empaneled local architects per city
   before anyone submits a drawing to a municipality. The UI surfaces citation
   and confidence for exactly this reason.
4. **Phases 8–9 may be incomplete.** Check `docs/phases.md` and the newest
   `docs/phase-*-verification.md` for the true edge.

## What "the copilot works" does and does not mean

The 40-command corpus passes against the **mock** provider, which answers from a
fixture keyed on command text. That proves the pipeline — schema gate, real fold
on a fork, rules diff, apply/reject — end to end. It says nothing about whether a
frontier model understands an architect's phrasing. That needs the real provider
and a human panel, and it is a launch gate, not a checkbox.

Same caution applies everywhere: read what a passing test actually asserts before
banking it.
