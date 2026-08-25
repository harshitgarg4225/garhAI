# Contributing to Garh AI

Read this once. It is short because most of the rules are enforced by tooling —
the parts that aren't are the parts that matter.

## Setup

```bash
cp .env.example .env
docker compose up            # the stack
pnpm install                 # host-side tooling and editor support
pip install pre-commit && pre-commit install
```

`make` lists everything. `make verify` runs every gate CI runs except e2e — run it
before you push and you will rarely be surprised.

---

## The rules that are not negotiable

These come from the locked architecture decisions. A change that breaks one of them
is not a tradeoff to discuss in review; it's a bug.

**1. Geometry is integer millimetres.** Every coordinate, length and thickness is an
`int` in mm. Areas are mm². Never a float for a length — floating-point drift is
how a dimension chain stops summing to its overall dimension, and how compliance
maths silently disagrees with the drawing. Parse user input (`12'6"`, `3.8m`,
`12 ft`) into mm at the boundary; format on the way out. One shared
`units.ts` / `units.py` pair, golden-tested to agree.

**2. The op is the atom.** The UI never mutates model state — it dispatches typed
ops. Model state is `fold(ops)`. If a feature can't be expressed as ops, redesign
the feature rather than reaching around the log; undo/redo, versions, diffs,
autosave and provenance all derive from it.

**3. LLMs never emit geometry.** An LLM may emit typed ops, parameters, or choices
from an enum. It may not emit coordinates. If you find yourself prompting for a
position, stop — the solver and rules engine produce all geometry, and validate it.

**4. Every query is tenant-scoped.** Route handlers never touch tables. Everything
goes through a repository that requires a `TenantCtx`, so `firm_id` scoping cannot
be forgotten. `make tenancy-audit` enforces this and CI runs it on every PR.

**5. Licences: Apache, MIT, BSD, MPL only. Never GPL or AGPL** in app code, and
never RPLAN-derived model weights. Check the licence table before adding anything,
and add a row to `DECISIONS.md` — that rule has no exceptions, including for dev
dependencies. `make license-check` fails on GPL/AGPL *and* on unknown licences.

**6. Secrets never reach the client bundle.** Browser code reads
`import.meta.env.VITE_*` and nothing else. `make secret-audit` enforces it.

**7. Golden files gate merges.** A golden diff is a build failure. If output changed
on purpose, regenerate the goldens **in the same commit** and explain why in the
message. Never regenerate to make a red build green without understanding the diff —
that is how a wrong dimension ships.

---

## Product principles that shape code review

Less mechanical, equally binding — these come from the golden rules and §15.

- **Feasible ≠ plausible.** A legal plan can be an unbuildable, ugly plan. Solver
  output must pass the critic gates before a user ever sees it. Never show a
  hard-fail plan.
- **Every AI action is previewable and reversible.** Copilot and solver results
  render as a before/after diff with apply/reject. Apply appends ops; reject means
  nothing happened.
- **Assumptions are visible.** Every default the AI used — room size, floor height,
  ₹/sq ft — is an editable chip, with a citation where one exists.
- **Compliance informs, it never blocks.** Violations are chips with the rule
  citation and a suggested fix. Architects may override anything; overrides are
  logged.
- **Errors say what to do next.** No raw exceptions in the UI. What happened, why if
  known, and one clear next action. Jobs retry with backoff and report their real
  state — never a fake progress bar.
- **Numbers are editable everywhere.** Any dimension or area label on the canvas is
  click-to-edit and dispatches an op. No dead text.
- **Indian defaults.** ft-in primary with gaj for plot area, ₹ grouped Indian-style
  (₹12,45,000), +91 phone fields, DD-MM-YYYY dates.

---

## Workflow

Branch from `main` (a pre-commit hook blocks committing directly to it).

```
feat(solver): stair-first placement for L-shaped envelopes
fix(drawings): outer chain must sum to overall extent
```

Conventional-commit prefixes, imperative mood, and a body that says **why** when the
change isn't self-evident.

A PR should say what changed, what you verified, and what you deliberately left
out. If you regenerated goldens, say which and why.

### Adding a dependency

1. Check the licence against the table in the research reference. Apache/MIT/BSD/MPL
   only.
2. Pin the exact version — the workspace stays self-consistent.
3. Add a row to `DECISIONS.md` with the version, licence and what it's for.
4. Run `make license-check`.

If a package is 🔴 copyleft and genuinely irreplaceable, it runs as an unmodified,
process-isolated container talking over HTTP or files — never linked into app code.

### Deviating from the playbook

You will hit something the playbook doesn't cover. Deviate when you must, and leave
a `DECISIONS.md` row: what, why, and which section it touches. Don't deviate on the
seven rules above.

---

## Conventions

**Python** — 3.11, `ruff` (line length 100), `mypy` strict on `garh_model` and
`services/*`. Pydantic models at every boundary. Structured logs via `structlog`;
never `print`.

**TypeScript** — `strict` plus `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes` and `noImplicitOverride`. No `any`, no non-null
assertions. Zustand stores are the only writers; components dispatch.

**Tests** — a bug fix comes with the test that would have caught it. Rules checks
need a passing *and* a failing fixture each. Model-core changes need property tests
(fold/replay determinism, undo/redo inverses).

**Performance budgets are features, not aspirations** (§14): 16ms canvas frames,
<10ms local op fold, ≤100ms compliance run, ≤60s for 3 solver options, <100ms 3D
rebuild, ≤5min for a G+1 sheet set. If a change blows a budget, that's a review
blocker.

---

## Where to look

| Question | File |
|---|---|
| Why is it done this way? | `DECISIONS.md` |
| What does this service do? | `docs/architecture.md` |
| What is this env var? | `docs/environment.md` |
| How do I run/regenerate goldens? | `docs/testing.md` |
| Is this phase done? | `docs/phases.md` |
| Something's broken locally | `docs/local-development.md` |
