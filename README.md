# Garh AI

**Brief in. Buildable house out.**

Garh AI takes an Indian architect from client brief to a compliant, client-approved,
submission-ready house design. Enter a plot and a brief; get compliant AI-generated
floor plan options, edit them on a 2D/3D canvas directly or by asking in plain
language, generate AI facades and photoreal renders, check against NBC + city
bye-laws + Vastu, and export a municipal submission drawing set as PDF and DXF —
in days instead of weeks. An Indian architect's fee is earned on drawings, not
concepts: concept-only tools save about 10% of the work, and the municipal set
targets ~70%.

---

## Start here

| If you are… | Read |
|---|---|
| **A coding agent picking this up** | [`CLAUDE.md`](CLAUDE.md) — state, conventions, the bug patterns this repo has already had, and what to run first |
| **Deciding what to build** | [`docs/spec/`](docs/spec/README.md) — the binding specification: build playbook, engineering playbook (§1–§18), product spec, licence table |
| **Checking whether something works** | `make bare` (7 gates, no dependencies, ~2s), then [`docs/phase-*-verification.md`](docs/) — one ledger per phase, splitting EXECUTED from TRACED from UNVERIFIED |
| **Wondering why a choice was made** | [`DECISIONS.md`](DECISIONS.md) — every deviation and every dependency, with reasons |

The spec in `docs/spec/` **wins over the code**. Where they disagree, that is a
bug to file, not a decision to re-make; the deviation protocol is a row in
`DECISIONS.md`.

---

## ⚠️ Status — written, never executed

*Last audited 2026-08-21, at the close of Phases 6 + 7.*

The code for Phases 0–7 is **written**: the compose stack, the DB schema and
migration, the tenancy repository layer, auth (OTP + RS256 + rotation), the API
routers, the model core in both TypeScript and Python, the rules engine with its
seeded packs, the web shell with its stores and typed client, the worker runtime,
the mock LLM/render providers; the SVG plot boundary editor, the
regulatory-profile panel, the brief form + completeness meter + LLM free-text
parse, and the DXF boundary-import pipeline (Phase 2); the layout solver, of
which the OR-Tools-free half executes today (Phase 3); the 2D editor canvas and
the 3D view — one Three.js scene, one hit-testing system, camera swap in place
(Phases 4–5); and the editing copilot plus the render pipeline, history and
client pack (Phases 6–7).

**Almost none of it has ever run under its real toolchain.** Every machine this
repository was authored on had no Docker, no Node and no Python 3.11 — only
Python 3.9.6. So `docker compose up`, `pnpm install`, `alembic`, `pytest`,
`vitest` and Playwright have never been executed against this tree, not once.
The exceptions are pure stdlib and all executed locally, and they are what
`make bare` runs:

- the **rules engine** — all 238 fixtures evaluate through the real
  `garh_rules.evaluate()` with their expected statuses, actuals and limits;
- the **OR-Tools-free half of the solver** — 26/26 smoke checks on a real
  30×40 ft Bengaluru plot;
- the **copilot, end to end** — all 40 eval-corpus commands through the real
  pipeline (real fold, real rules engine, mock provider): 28/28 in-scope commands
  produce diffs that `apply_group` cleanly, worst dry-run fold 1.3 ms against the
  §14 10 ms budget;
- the **§13 containment boundary** — 46 checks against a deliberately hostile
  provider: malformed ops never reach the fold, impossible ops never reach the
  caller, refusals never carry ops, seeded PII never reaches the prompt, exactly
  one self-correction round;
- the **render catalogue** — 19 checks that the API's and the web app's
  hand-written mirrors still match `services/render`, plus the mock provider's
  determinism-by-seed derivation.

`make bare` runs all of that plus the tenancy/secret/env/asset audits on a bare
interpreter. It is the first thing to run after any change. **When a claim matters
and its only proof needs Postgres, add a gate there** — that rule exists because a
§13 PII test that could not run locally turned out to be vacuous, with a real leak
behind it (see `docs/phase-6-7-verification.md` §3).

**The web app is the least-verified part of the repository.** Phases 4–7 add
~30,000 lines of TypeScript and TSX that no compiler and no browser has seen.
What is mechanically checked: every import in the workspace resolves to a real
export, and every op payload the canvas or the copilot emits matches
`ops.schema.json` field for field — which is the failure that would corrupt the
op log. Everything else on the client is traced by hand.

That distinction is the single most important thing to know before you start.
Read every command below as *intended* behaviour, and read the verification
notes first — they trace the critical paths file by file, list what was fixed,
and give the exact command that settles each remaining claim:
[`phase-0`](./docs/phase-0-verification.md) ·
[`phase-2`](./docs/phase-2-verification.md) ·
[`phase-3`](./docs/phase-3-verification.md) ·
[`phase-4`](./docs/phase-4-verification.md) ·
[`phase-5`](./docs/phase-5-verification.md) ·
[`phase-6-7`](./docs/phase-6-7-verification.md).
Expect the first real test run to find things. It would be strange if it did not.

One known **release blocker** is tracked by a gate rather than a comment:
`apps/web/public/fonts/inter-medium.woff` is not in the repository, and without
it every dimension and room name on the plan silently disappears in production.
`make asset-audit` prints it on every run.

### Build phases (playbook §Build Phases)

| Phase | Scope | State |
|---|---|---|
| **0** | Monorepo, compose stack, CI, DB migrations, auth, tenancy repository layer, seed script | 🟡 **code complete, DoD unverified** — see `docs/phase-0-verification.md` |
| **1** | Model core + op engine — document, 32-op taxonomy, fold/replay, undo/redo, versions, provenance | 🟡 code complete in both languages; golden hashes were generated by Python and the TypeScript side has never folded them |
| **2** | Plot, brief, rules engine + 3 city packs + Vastu | 🟡 **code complete, DoD partially executed** — all 238 rule fixtures pass under the real engine (executed locally); plot editor, brief UI and DXF import are written but have never rendered in a browser. See `docs/phase-2-verification.md` |
| **3** | Layout solver — CP-SAT two-stage, critic, diversity | ⬜ scaffolding and gates exist; the CP-SAT stages raise `NotImplementedError` naming the phase |
| **4** | 2D editor canvas | 🟡 **code complete, nothing has ever run** — ~24,000 lines no compiler or browser has seen. See `docs/phase-4-verification.md` |
| **5** | 3D + facade kits | 🟡 code complete — one scene, one picker, camera swap in place; 2 facade kits. See `docs/phase-5-verification.md` |
| **6** | Copilot (LLM → typed ops only) | 🟡 **code complete, DoD executed on the mock** — route mounted, 40/40 corpus commands and 46 §13 containment checks run in `make bare` (28/28 in-scope applicable, worst fold 1.3 ms). Route wiring, metering and the 429 are traced, not run. See `docs/phase-6-7-verification.md` |
| **7** | Renders (mock + diffusers) | 🟡 **code complete, partially executed** — router, history, stale flag, 8-shot client pack, pack zip; catalogue mirrors and seed-determinism gated in `make bare`. **The pixels are unproven** (no Pillow here) and the diffusers path has never run. See `docs/phase-6-7-verification.md` |
| **8** | Drawings + exports — auto-dimensioning, 6 municipal sheets, PDF/DXF | ⬜ layers, sheet model and DXF setup exist; dimensioning and sheet writing are stubs |
| **9** | Polish, billing, share links | 🟡 share links are implemented server-side; billing is not started |

### Known blockers

1. **`pnpm-lock.yaml` does not exist**, so no JS job can run. CI uses
   `--frozen-lockfile` on purpose (§13: an absent lockfile is a supply-chain
   problem, not a convenience to paper over), and its first job now fails with an
   explicit message rather than six obscure ones. Fix it once, on a machine with
   Node 20 + pnpm 9.12.0:

   ```bash
   make lockfile     # == pnpm install at the repo root
   git add pnpm-lock.yaml && git commit
   ```

2. **No toolchain has ever validated the Python side either.** `pytest`, `mypy`,
   `ruff` and `alembic` are all unrun. `apps/api/pyproject.toml` now collects
   `tests`, `garh_model/tests` and `garh_rules/tests` — roughly 300 assertions that
   have only ever been exercised by a hand-rolled 3.9 shim.

3. **`.license-allowlist.txt` is intentionally absent.** The licence gate fails on
   unknown licences as well as GPL/AGPL; the first CI run should report the real
   set to review rather than ship a pre-blessed list. On the authoring machine
   `make license-check` reports **0 denied, 0 unknown** over 16 distributions,
   which is a smaller set than CI will see.

4. **Compliance is wired but unproven.** `garh_api/compliance.py` projects the model
   document into the rules engine and `GET /compliance` runs it live. The
   projection makes six documented approximations (edge roles, provided setback,
   opening role, ventilation area, stair headroom, FAR-countable area); each is
   named in the module docstring and surfaces in the report's `notes`. Nobody has
   compared a single result against a real sanctioned drawing yet.

5. **Four audited actions have no write site yet.** `compliance.overridden`,
   `user.role_changed`, `user.removed` and `firm.settings_changed` are declared in
   the audit registry, but the route that would emit them does not exist (team and
   firm management are Phase 9; the compliance-override control is Phase 2/4). They
   are enumerated in `apps/api/tests/test_audit_actions.py::PENDING_ACTIONS` with
   the phase that fills each, and the test fails if a *new* action goes un-emitted
   or if an entry goes stale — so this list cannot quietly grow.

### The §13 security checklist

Walked item by item, with the evidence for each row, in
[`docs/phase-0-verification.md` §3b](./docs/phase-0-verification.md). Every item is
green except the audit-trail row above (🟡, four pending actions) and the
lockfile/`pip-audit` row (🟡, blocked on blocker 1). Two gaps were found and closed
during that walk: the LLM route had no rate limit and no `credit_events` row, and
the DXF upload cap was enforced against two independent numbers.

---

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker + Compose | **v2.24+** | the whole stack (`required: false` on `env_file` needs 2.24) |
| Node | **20** | web app, editor tooling |
| pnpm | **9.12.0** | workspace package manager (`corepack enable` picks it up) |
| Python | **3.11** | API and workers when run outside Docker |
| make | any | the entrypoints below |

Docker alone is enough to *run* the stack — Node and Python are only needed for
host-side tooling, editor support and running tests outside containers.

> **The checkout path contains a space** (`Garh AI`). Everything here quotes paths
> accordingly, and `docker-compose.yml` sets `name: garh-ai` because Compose cannot
> derive a legal project name from the directory. If you re-clone, either keep the
> quoting or use a space-free directory name.

---

## Quickstart

```bash
cp .env.example .env      # or: make env
docker compose up         # or: make up
```

That is the whole thing. No API keys, no GPU, no secrets to obtain:

- the LLM, render and billing providers all default to deterministic **mocks**
- a throwaway **RS256 dev keypair** is minted on first boot
- **migrations** are applied before the API serves
- the **MinIO bucket** is created by a one-shot init container

| Service | URL |
|---|---|
| Web (Vite dev server) | http://localhost:5173 |
| API | http://localhost:8000 · health at `/healthz` |
| MinIO console | http://localhost:9001 |
| Postgres | `localhost:5432` |
| Redis | `localhost:6379` |

Then load the demo project — a 30×40 ft Bengaluru plot, 9m road to the south,
G+1 3BHK, which doubles as the fixture behind the tours, goldens, perf budgets
and screenshots (§17):

```bash
make seed
```

### Common tasks

```bash
make            # list every target
make logs       # tail all services
make migrate    # alembic upgrade head
make dev-keys   # stable JWT keypair in .keys/ + .env (instead of the throwaway one)
make verify     # everything CI runs except e2e
make down       # stop (keeps your data)
make reset      # DESTRUCTIVE: also delete volumes
```

---

## Repo map

```
garh-ai/
├── docker-compose.yml      # postgres · redis · minio(+init) · api · web · 3 workers
├── Makefile                # developer entrypoints; also owns the §13 security guards
├── DECISIONS.md            # deviation log + pinned dependency set (read before adding a dep)
├── apps/
│   ├── api/                # FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2
│   │   ├── garh_api/       #   app: config, db, models, tenancy repository layer
│   │   ├── garh_model/     #   Python mirror of the model core (kept in lockstep with packages/model)
│   │   └── migrations/     #   Alembic
│   └── web/                # Vite + React 18 + TS strict + R3F + Zustand + Tailwind
├── packages/
│   ├── model/              # TS model + op types, fold/replay, units, geometry
│   │                       #   schema/ holds the JSON Schema that IS the TS↔Python contract
│   └── ui/                 # shared primitives (buttons, chips, dialogs, toasts)
├── services/               # workers, one image, three entrypoints
│   ├── solver/             #   OR-Tools CP-SAT layout engine        (§5)
│   ├── drawings/           #   auto-dim, sheets, ezdxf/PDF export   (§7)
│   └── render/             #   provider interface: mock | diffusers (§9)
├── rulepacks/              # JSON rule packs: nbc-core, blr, ncr, hyd, vastu (§6)
├── fixtures/               # golden corpora — see fixtures/README.md for what is
│                           #   populated (briefs, rules, model, catalog) and what
│                           #   waits on a phase (plans, sheets, copilot-commands)
├── e2e/                    # Playwright: smoke on every PR, happy path nightly
├── scripts/                # repo-level guards that outgrew a Makefile one-liner
└── docs/                   # architecture, local dev, env reference, security, testing,
                            #   phase-0-verification (start here)
```

`apps/api/garh_rules/` is not in the sketch above because §1 does not name it: the
rules engine lives beside the API rather than in `services/`, because the editor
re-runs it in-process on every edit against a ≤100 ms budget (§14) and a queue hop
would blow that. `apps/api/garh_api/compliance.py` is the projection between the
model document and the engine.

### The two ideas worth knowing before reading the code

**Geometry is integer millimetres, everywhere.** Every coordinate, length and
thickness is an `int` in mm; areas are mm². Floats are how dimension chains stop
summing to the overall dimension and compliance maths drifts. Display conversion
to ft-in / m / gaj happens only at the boundary, through one shared
`units.ts` / `units.py` pair that is golden-tested to agree.

**The op is the atom.** Model state is `fold(ops)`. Every mutation — a user drag, a
copilot command, solver output — is a typed op appended to a log. Undo/redo,
versions, diffs, autosave and provenance all derive from that. The UI never mutates
state directly, it dispatches ops. And **LLMs never emit geometry** — they emit
typed ops chosen from the taxonomy; the solver and rules engine produce and
validate all geometry.

---

## Running tests

```bash
make test        # unit: pytest + vitest
make golden      # golden files: plan JSON, dimension chains, SVG/DXF sheets
make e2e-smoke   # Playwright smoke
make verify      # lint → typecheck → unit → golden + the security guards
```

CI runs the same stages in the playbook's order —
`lint → typecheck → unit → golden → e2e(smoke)` — plus a `supply-chain` job in
parallel. The security guards are Makefile targets rather than workflow-only
scripts, so a CI failure reproduces locally with the identical command:

```bash
make secret-audit    # no non-VITE_ secret name may reach the browser bundle (§13)
make tenancy-audit   # only the repository layer may touch tables (§13)
make license-check   # Apache/MIT/BSD/MPL only — fail on GPL/AGPL/unknown
```

**Golden files gate merges.** A golden diff is a build failure, not a warning. When
output changes on purpose, regenerate the goldens in the same commit and say why.

---

## Docs

| Doc | What's in it |
|---|---|
| [docs/architecture.md](./docs/architecture.md) | services, data flow, where each playbook section lives |
| [docs/local-development.md](./docs/local-development.md) | day-to-day workflow and troubleshooting |
| [docs/environment.md](./docs/environment.md) | every env var, who reads it, what's client-public |
| [docs/testing.md](./docs/testing.md) | test strategy and the golden-file workflow |
| [docs/security-checklist.md](./docs/security-checklist.md) | §13 checklist as a live tracker |
| [docs/deployment.md](./docs/deployment.md) | single-VM beta, backups, observability |
| [docs/phases.md](./docs/phases.md) | phase-by-phase DoD tracker |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | conventions, the rules that aren't negotiable |

---

## Legal posture (spec §15)

Outputs are **instruments of service authored by the architect**, not an approval.
Every project carries an **architect of record** (Architects Act 1972 / COA
registration — only registered architects may sign and submit), and the
advisory-not-approval disclaimer is surfaced **at export**, not buried in terms.
Rule-pack values are versioned and cited with a confidence level, and every value
is overridable — overrides are logged.

Briefs contain family composition, budget, and religious inference (pooja, Vastu),
which is sensitive personal data under the **DPDP Act 2023**: explicit consent,
retention limits, deletion rights. Designs belong to the firm, and **training
consent defaults to OFF**.
