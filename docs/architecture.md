# Architecture

How the pieces fit, and where each playbook section is implemented.

> **Phase 0 caveat:** this describes the target architecture the scaffold is built
> for. Most of it is not written yet — see [phases.md](./phases.md) for what exists.

---

## Service map

```
                      ┌──────────────────────────────────────┐
   browser ──────────▶│  web  ·  Vite + React 18 + R3F       │
                      │  one <Canvas>: 2D ortho + 3D persp.  │
                      └───────────────┬──────────────────────┘
                                      │ HTTPS · JSON · SSE
                      ┌───────────────▼──────────────────────┐
                      │  api  ·  FastAPI                      │
                      │  op sequencer · repositories · auth   │
                      └───┬───────────────┬──────────────┬────┘
                          │               │              │
            ┌─────────────▼──┐   ┌────────▼───────┐  ┌───▼──────────┐
            │ postgres 15    │   │ redis 7        │  │ minio (S3)   │
            │ op log + snaps │   │ queues · rate  │  │ renders,     │
            │ every row has  │   │ limits · SSE   │  │ exports,     │
            │ firm_id        │   │ fan-out        │  │ uploads      │
            └────────────────┘   └───┬────────────┘  └──────────────┘
                                     │ BRPOP
        ┌────────────────────────┬────┴─────────────┬───────────────────────┐
        │ worker-solver          │ worker-render     │ worker-drawings      │
        │ OR-Tools CP-SAT (§5)   │ mock | diffusers  │ auto-dim, sheets,    │
        │                        │ (§9)              │ ezdxf/PDF (§7)       │
        └────────────────────────┴───────────────────┴──────────────────────┘
```

All three workers run from **one image** (`services/Dockerfile`) with different
entrypoints, because they share the model core, the rules engine and the database
layer. They are stateless: nothing but the queue and the database coordinates them,
which is what allows the documented single-VM beta to become k8s later without
touching the images.

---

## Request paths that matter

### An edit (the hot path)

```
user drags a wall
  → tool state machine commits          (client, §12)
  → op applied optimistically to the local fold      <10ms
  → op queued to POST /projects/:id/ops
  → server validates → assigns idx → computes inverse → folds → persists
  → 409 on a stale baseIdx ⇒ client rebases and retries
  → autosave badge: "Saved · v214"
```

The optimistic apply is why the canvas feels instant, and the server-assigned `idx`
is why the log stays a single ordered truth. Ops are intent-level and carry no
derived geometry, which is what makes them CRDT-compatible later (D12) without a
rewrite now.

### A generation (the slow path)

```
POST /projects/:id/solve
  → solver_jobs row (queued)  → RPUSH garh:queue:solver
  → worker-solver: envelope → stair candidates → CP-SAT stage A
                 → refine to the 115mm module → doors/windows
                 → critic (hard rules must pass) → diversity filter
  → progress events → Redis → SSE → the client's staged, honest progress messages
  → 3–5 PlanOptions, each pre-validated
  → user picks one → solver.apply_option expands to ONE atomic op group
```

The user only ever sees options that already pass the hard rules — "never show a
hard-fail plan" is a gate in the worker, not a warning in the UI.

---

## Where each playbook section lives

| §   | Concern                         | Code                                                       |
| --- | ------------------------------- | ---------------------------------------------------------- |
| 1   | Repo layout, tooling            | root config, `Makefile`, `.github/workflows/ci.yml`        |
| 2   | Database schema                 | `apps/api/garh_api/models.py`, `apps/api/migrations/`      |
| 3   | Model core: geometry + document | `packages/model/src/`, `apps/api/garh_model/`              |
| 4   | Op taxonomy (~32 ops)           | `packages/model/src/ops/`, mirrored in `garh_model`        |
| 5   | Layout solver                   | `services/solver/`                                         |
| 6   | Rules engine + packs            | `apps/api/garh_model/rules/`, `rulepacks/`                 |
| 7   | Auto-dimensioning + sheets      | `services/drawings/`                                       |
| 8   | 3D + facade kits                | `apps/web/src/three/`, `apps/web/src/facade/`              |
| 9   | Render service                  | `services/render/`                                         |
| 10  | LLM: brief parse + copilot      | `apps/api/garh_api/llm/`                                   |
| 11  | API surface                     | `apps/api/garh_api/routers/`                               |
| 12  | Frontend architecture           | `apps/web/src/`                                            |
| 13  | Security                        | `garh_api/tenancy.py`, plus the `Makefile` guards          |
| 14  | Performance budgets             | asserted in the tests that own each budget                 |
| 15  | UX + delight                    | `apps/web/src/`, `packages/ui/`                            |
| 16  | Testing + goldens               | `fixtures/`, `e2e/`                                        |
| 17  | Seed data + demo project        | `apps/api/garh_api/seed/`                                  |
| 18  | Env, config, deployment         | `garh_api/config.py`, `.env.example`, `docker-compose.yml` |

---

## The model core, and why it's duplicated

The model core exists twice on purpose:

- **`packages/model/`** (TypeScript) — the client folds ops locally for the
  optimistic path and renders from that state.
- **`apps/api/garh_model/`** (Python) — the server validates and folds
  authoritatively; the solver, rules engine and drawing engine all read it.

They must agree exactly, or the client and server disagree about what a plan _is_.
The contract that keeps them honest is the **JSON Schema in
`packages/model/schema/`**, plus golden tests that run the same inputs through both
implementations and compare — most importantly the units conversions, where a
rounding difference would surface as a wrong dimension on a drawing rather than as
a crash.

A `snapshot_hash` (sha256 of canonical JSON) makes state comparison cheap: fast load
is the latest snapshot plus tail ops, and equality of hashes is how the sync checks
and property tests assert determinism.

### Element identity is load-bearing

Ids are `{type}_{ulid}`. Room ids survive edits: after any wall op, rooms are
re-derived by planar subdivision and matched to existing rooms by maximum polygon
overlap, so an id dies only when a room genuinely disappears. Annotations, locks and
copilot references all point at those ids — which is why partial re-solve must
return locked rooms untouched, and why that matching gets tested hard.

---

## Determinism boundary

The split between "must be reproducible" and "may be creative" is the core of the
trust model, and it's worth being explicit about:

| Deterministic — unit-tested against goldens | ML / LLM — validated before display             |
| ------------------------------------------- | ----------------------------------------------- |
| Layout solver (CP-SAT + heuristics)         | Brief parsing (free text → Brief + assumptions) |
| Rules engine (NBC, city bye-laws, Vastu)    | Copilot (NL → typed ops)                        |
| Dimensions and dimension chains             | Facade kit selection                            |
| Areas, FAR, coverage                        | Render generation                               |
| DXF/PDF/SVG output                          | Option rationales (verbalise given facts only)  |

Everything on the right is validated by something on the left before a user sees it.
That's the whole creator–critic idea: the LLM proposes, the deterministic engine
decides.

---

## Multi-tenancy

Every tenant-owned row carries `firm_id`, indexed. Route handlers **never** touch
tables — all access goes through a repository that requires a `TenantCtx`, so
scoping cannot be forgotten by omission. `make tenancy-audit` greps for direct
session access outside the repository layer and fails the build; CI additionally
requires a test proving a cross-tenant fetch returns 404/403, and treats "no such
test" as a failure rather than a pass.

The client share surface is a **separate read-only router** that imports no write
dependencies, so a bug in the viewer cannot become a write path.

---

## Provider interfaces

Every external AI/GPU service sits behind an interface with a deterministic mock:

| Provider           | `mock` (default)                                                               | Real                                                       |
| ------------------ | ------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| `PROVIDER_LLM`     | fixture-driven, deterministic                                                  | Anthropic API, structured outputs                          |
| `PROVIDER_RENDER`  | composites the viewport + preset tint + watermark, instant, seed-deterministic | diffusers + ControlNet, SDXL / FLUX.1-schnell, Real-ESRGAN |
| `PROVIDER_BILLING` | in-memory                                                                      | Razorpay                                                   |

This is not a testing nicety — it's what makes the entire product runnable and
e2e-testable with **zero API keys and zero GPUs**, which in turn is what keeps CI
honest and onboarding a single command.
