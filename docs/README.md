# Garh AI docs

Engineering documentation for the repo. Product intent, feature specs and the
engineering playbook live outside the repo (in the builder skill references); these
pages cover **this codebase** — how it's wired, how to run it, and what's actually
done versus not.

| Doc | Read it when |
|---|---|
| [phase-0-verification.md](./phase-0-verification.md) | **first.** It is the honest ledger of what has been executed (little), what was traced by hand (most), and what is unproven — plus the bootstrap sequence for the first machine with a real toolchain |
| [phase-2-verification.md](./phase-2-verification.md) | same ledger for the Phase 2 delta (plot, brief, DXF import, rules surface) — includes the one big EXECUTED claim: all 238 rule fixtures pass under the real engine |
| [phase-3-verification.md](./phase-3-verification.md) · [phase-4](./phase-4-verification.md) · [phase-5](./phase-5-verification.md) | the same ledger for the solver, the 2D canvas and the 3D view |
| [phase-6-7-verification.md](./phase-6-7-verification.md) | the copilot and the render pipeline — and read it before touching either. It carries the §13 containment trace, the ten defects an adversarial pass found (two of them containment holes hiding behind a vacuous test), and the three new `make bare` gates that now execute what pytest could not run here |
| [architecture.md](./architecture.md) | you need the service map, the data flow, or where a playbook section is implemented |
| [local-development.md](./local-development.md) | you're setting up, or something is broken locally |
| [environment.md](./environment.md) | you're adding or debugging an env var |
| [testing.md](./testing.md) | you're writing tests or a golden file changed |
| [security-checklist.md](./security-checklist.md) | you're closing out a phase, or touching auth/tenancy/uploads |
| [deployment.md](./deployment.md) | you're deploying the beta, or setting up backups/observability |
| [phases.md](./phases.md) | you want to know what's done and what's next |

Two files at the repo root are load-bearing and worth reading before either:

- **[../DECISIONS.md](../DECISIONS.md)** — every deviation from the playbook and
  every dependency, with licences. Read before adding a package.
- **[../CONTRIBUTING.md](../CONTRIBUTING.md)** — the seven non-negotiable rules.

## The short version

Garh AI is a pnpm + Python monorepo. One command is meant to run everything:

```bash
cp .env.example .env && docker compose up
```

> It has never been run. Nothing in this repository has. See
> [phase-0-verification.md](./phase-0-verification.md) before you assume any page
> here describes observed behaviour — and note that `pnpm-lock.yaml` must be
> generated and committed (`make lockfile`) before any JavaScript job works.

Nine services: Postgres, Redis, MinIO (plus a one-shot bucket init), the FastAPI
API, the Vite dev server, and three Python workers (solver, render, drawings) that
consume Redis queues.

Two invariants explain most of the design:

1. **Geometry is integer millimetres.** Floats drift; drifting geometry breaks
   dimension chains and compliance maths.
2. **State is `fold(ops)`.** Every mutation is a typed op in an append-only log.
   Undo/redo, versions, diffs, autosave and provenance are all derived, not
   separately implemented.
