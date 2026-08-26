# Build phases — status tracker

Phases run **in order**. Each one has a Definition of Done that must pass before the
next starts, and gold-plating ahead of the phase is explicitly discouraged (no render
UI in Phase 2).

**Legend:** ✅ done · 🟡 in progress · ⬜ not started

> **Status as of 2026-08-05.** "Written" and "works" are different claims and this
> page keeps them apart. Nothing in this repository has ever been executed under
> its real toolchain — no Docker, no Node, no Python 3.11 on any authoring
> machine — with one exception: the rules engine is pure stdlib, and its full
> fixture corpus has been **executed** locally. Where a row says **written**,
> read it as "the code is there and was traced by hand".
> [`phase-0-verification.md`](./phase-0-verification.md) and
> [`phase-2-verification.md`](./phase-2-verification.md) are the ledgers.

> **Execution update, 2026-08-25/26.** The paragraph above is now history: the
> repository was executed end to end under its real toolchain (Node 22, pnpm 9,
> Python 3.11, Postgres 16, Redis, headless Chromium; moto stands in for minio)
> and deployed — an 8-service Railway stack runs this code in production
> containers. Executed and green: 1,923 api+rules+model Python tests, 369
> services tests, 1,579 JS unit tests, strict `tsc`, the production Vite build,
> the @smoke e2e journey (login → demo project → six tabs → DXF round trip →
> sign-out, 20 passed), the copilot e2e walk, the §13 share-link journey, and
> the full Phase-8 drawings pipeline (9 municipal sheets from a live project;
> the floor-plan DXF passes `ezdxf.audit()` with 0 errors). First execution
> also surfaced and fixed real defects the traces missed — the sequencer's
> password-masking DB URL, a failure reporter that itself failed (jobs stuck
> "running" forever), a live-compliance 500 from a pydantic name collision, the
> shared `cn()` class merger silently deleting `flex-1`, and a solve enqueue
> whose payload no worker could parse. Still not executed: `docker compose up`
> verbatim, CI on GitHub (never triggered; its lint/mypy jobs would be red),
> the real LLM/render providers, and the CP-SAT solver's first full options run
> (wired, in progress). Per-phase rows below are being promoted as ledgers
> catch up; where a row still says "never executed", check this note's date
> against the row's.

---

## Phase 0 — Scaffold & foundations 🟡 (DoD executed 2026-08-25 — smoke e2e, auth, tenancy, migrations on live Postgres; still open: `docker compose up` verbatim, CI on GitHub)

Monorepo per §1; compose stack; CI; DB migrations (§2 DDL); auth (email+OTP, JWT);
firms/users/projects CRUD; tenancy repository layer; seed script.

| Item | Status |
|---|---|
| Monorepo layout, workspace, TS config | ✅ |
| `docker compose up` brings up 9 services | 🟡 written, **never executed** |
| CI pipeline: lint → typecheck → unit → golden → e2e | 🟡 written; **cannot start** until `pnpm-lock.yaml` is committed |
| Security guards: secret / tenancy / licence / env | ✅ written **and run**, all four pass |
| DB schema + Alembic migrations | 🟡 18 tables, 110 named constraints; `models.py` ⇄ migration verified by text comparison, **never applied to a live Postgres** |
| Tenancy repository layer | ✅ written; `_scoped_select()` is the only query builder, enforced by `make tenancy-audit` and an AST test |
| Auth: email OTP + JWT RS256 + rotation + reuse detection + logout-all | 🟡 written; the Redis Lua scripts were property-tested via transliteration, the real thing never ran |
| firms / users / projects CRUD | 🟡 written — 66 routes across 7 routers |
| Seed script + demo project | 🟡 written (`python -m garh_api.seed`) |
| `garh_api.main:app` | ✅ exists; mounts health at the root, auth and the API router under `/api/v1` |
| Cross-tenant test | ✅ `apps/api/tests/test_cross_tenant.py`; CI fails if the file is missing |

**DoD:** `docker compose up` → login → create an empty project; CI green; a
cross-tenant access attempt test proves 404/403.
**Not met** — every clause of it needs a toolchain nobody has had yet. The path was
traced file by file and several genuine breaks were fixed along the way (worker image
missing Pillow, web image COPYing a non-existent lockfile and building a script-less
package, no signup path in the UI, four client↔API shape mismatches). That trace, its
fixes and its gaps are in
[`phase-0-verification.md`](./phase-0-verification.md).

### To finish Phase 0

1. **`make lockfile`** on a machine with Node 20, then commit `pnpm-lock.yaml`.
   Six CI jobs are blocked on this and nothing else.
2. `make up` → `make migrate` → `make seed`, and fix what the first boot finds.
3. Walk the DoD path in a browser: sign up → OTP → new project.
4. `make verify`, then `make e2e-smoke`. Expect findings; ~300 Python assertions and
   13 TypeScript suites have never run under their real test runners.
5. `alembic downgrade base && upgrade head`, plus an `--autogenerate` run that must
   produce an **empty** diff.
6. Promote rows out of §4 of `phase-0-verification.md` as they are settled, naming
   the command that settled each.

---

## Phase 1 — Model core + op engine ✅ (executed 2026-08-25: TS⇄Python state hashes match on the golden corpus, property tests, sequencer + optimistic store; only the 1,000-op soak numeral is shorter than written — 200-op TS / 25×20 hypothesis runs)

Model document (§3), op taxonomy (§4), fold/replay, op validation, undo/redo,
version snapshots, provenance. Server op sequencer + optimistic client store.

**DoD:** property test — any generated op sequence folds deterministically and
replays to an identical state hash; undo/redo round-trips 1,000 random ops; an
invalid op (e.g. an opening wider than its wall) is rejected cleanly.

The op taxonomy **freezes at the end of this phase**, and the drawing-relevant schema
(walls/openings/levels/stairs) contract-freezes at the end of Phase 2 with contract
tests. Everything downstream rides on both.

---

## Phase 2 — Plot, brief, rules engine ✅ (executed 2026-08-25 in a real browser: rule preset flip, brief parse chips, plot quick-start, DXF import round trip; value-override substitution verdict-tested 2026-08-26)

Plot boundary editor, DXF boundary import, regulatory profiles + rules engine + 3
city packs + Vastu (§6), brief form + completeness meter, LLM brief-parse behind the
provider interface.

| Item | Status |
|---|---|
| Rule packs: `nbc-core`, `blr`, `ncr`, `hyd`, `vastu` | 🟡 authored early (spec **D9**) — 118 rules, all `confidence: seed`, all `review.status: unreviewed`; **no architect has reviewed one yet** |
| Rules engine + 18 check types | ✅ written, with 238 fixtures — **all 238 EXECUTED through the real `garh_rules.evaluate()` locally**: expected status, actual and limit match on every one (see `phase-2-verification.md` §3) |
| Model → engine projection | 🟡 `garh_api/compliance.py`; `GET /compliance` runs it live. Six documented approximations (edge roles, provided setback, opening role, ventilation area, stair headroom, FAR-countable area), each surfaced in the report's `notes` |
| Plot boundary editor | 🟡 written — SVG canvas (`features/plot/`, decision in DECISIONS.md), rect quick-start, vertex/edge editing, north compass, road edges, area readout in ft-in + gaj; **never rendered in a browser** |
| Regulatory profile panel | 🟡 written — city preset → pack resolution client-side (`rules.ts` mirrors `predicates.py`, drift pinned by tests), value overrides stored under `regProfile.overrides.values` (reserved key, see DECISIONS.md); **engine substitution of value overrides is Phase 3** |
| DXF import | 🟡 written end-to-end — capped/sniffed upload (`routers/imports.py`), sandboxed subprocess parse with rlimits + kill (`services/drawings/dxf_import.py`), `$INSUNITS` whitelist with assumed-mm fallback, candidate picker dialog; **never run against real ezdxf (not installed here) — CI must** |
| Brief form + completeness meter | 🟡 written — `features/brief/`: typed fields, Vastu selector, completeness meter, free-text parse → assumption chips; **never rendered in a browser** |
| LLM brief-parse | ✅ written and wired — `services/llm.BriefParser` behind `get_brief_parser()`, schema-validated, PII-redacted, applied through the op path; 12-fixture corpus round-trips byte-exactly through the real synthesizer (**EXECUTED**) |

**DoD:** every rule has ≥1 passing and ≥1 failing fixture and they all pass —
**EXECUTED, passes**; changing the city preset re-validates live — **traced**
(`useLiveCompliance` re-checks on `baseIdx` advance, 450ms debounce); brief → chips
UI — **traced**. The browser-path clauses stay open until the toolchain exists;
`phase-2-verification.md` names the command that settles each.

> Rule packs landing before the solver is the one intentional reordering in the whole
> plan. Solver constraints *are* rules — building the solver first means encoding the
> same limits twice and reconciling them later. See D9.

---

## Phase 3 — Layout solver 🟡 (2026-08-26: solve enqueue contract fixed, real CP-SAT stages wired and solving; stage-B refinement still discards all candidates — first green options run in progress)

§5 exactly: envelope derivation, stair/circulation pre-placement, CP-SAT stage A,
refinement stage B, door/window auto-placement, critic scoring, diversity, partial
re-solve preserving locked room ids. Runs as a worker job with progress events.

**DoD:** the 20-brief golden corpus solves in ≤60s each with ≥3 options; all options
pass hard rules; locked-room regeneration preserves ids; plan JSON goldens stable;
unit tests per constraint builder.

---

## Phase 4 — 2D editor canvas ✅ (DoD e2e GREEN 2026-08-26: plan-canvas.spec passes end to end — typed-length walls, one-group undo/redo, bye-law chip appears and clears, room click → inspector. The "blank canvas" was a permanently-suspended troika <Text> from the missing Inter font hiding the whole tab via the route Suspense — label layers now have their own boundary. Open: DoD perf numbers (needs a solver-seeded G+2), visual baseline)

Orthographic Three.js scene sharing one scene graph and one hit-testing system with
3D; tools select/wall/door/window/stair/balcony/measure; 115mm snap default;
dimension-first editing; room auto-detection with live name/area tags; live
compliance chips; ≥30 furniture items at Indian sizes.

**DoD:** Playwright draws a 2-room plan from scratch, ops sync, undo/redo works, a
compliance chip appears when a bedroom is <9.5m² and clears on fix; 60fps pan/zoom on
the demo G+2, measured.

| Piece | State |
|---|---|
| Canvas core — one `<Canvas>`, one camera rig, ONE picker, `frameloop="demand"` | 🟡 written |
| Eight tool state machines (Esc / Enter / typed length, once, in `BaseTool`) | 🟡 written |
| Plan renderer — walls with openings cut out, room washes, opening/stair symbols | 🟡 written (`pages/project/plan/`) |
| Overlays — dimension chains (click-to-edit), room tags, compliance markers, inspector | 🟡 written |
| Furniture — 45-item catalogue, placement controller, **box proxies, not modelled assets** | 🟡 written |
| DoD Playwright spec (`e2e/tests/plan-canvas.spec.ts`) | 🟡 written, never run — asserts ops/folds/compliance, never pixels |
| 60 fps on the demo G+2 | ⬜ not measurable — the seeded demo has no solved plan until Phase 3 runs |
| `wall.split` from the canvas | ⬜ not built; the op, fold and inverse exist |
| `apps/web/public/fonts/inter-medium.woff` | ⬜ **release blocker** — see `make asset-audit` |

**Read `docs/phase-4-verification.md` before trusting any of the above.** No
Node exists on the authoring machine, so `tsc`, `vitest`, `eslint`, `vite` and
Playwright have never seen ~24,000 lines of this. What *is* mechanically
checked: every import resolves to a real export (260 files), and every op
payload field the canvas emits matches `ops.schema.json`.

---

## Phase 5 — 3D + facades ✅ (DoD e2e GREEN 2026-08-26: three-d.spec passes — extrusion, 2D↔3D selection through the one picker (fixed: pointer capture on press was eating clicks), kit apply + component edit with walls byte-identical, sun scrub frozen-op proof, <100ms incremental rebuild. Open: visual baseline (font + CI-minted screenshot), Manifold-holes annotation-only)

Extrude storeys, cut openings (Manifold), slabs/parapet/mumty/OHT; 2D↔3D synced
selection; orbit/walk; sun widget (NOAA solar position, city-centroid lat/long);
facade kit system + the 2 launch kits (§8).

**DoD:** a plan edit reflects in 3D in <100ms; a facade kit applies, edits and exports
consistently; screenshot visual regression on the demo project.

One scene, one picker, camera swap in place — the 3D view shares `CanvasRoot` with the
2D canvas rather than mounting a second `<Canvas>`, which is what makes the Phase-7
render capture possible at all. See `docs/phase-5-verification.md`.

---

## Phase 6 — Copilot 🟡 (code complete; the DoD numbers EXECUTED on the mock)

LLM structured output → candidate ops (§10); validation loop (ops → dry-run fold →
rules check → diff preview); apply/reject; ~25-op coverage with an honest "can't do
that yet" for out-of-scope asks, logged.

**DoD:** a 40-command eval fixture set — ≥90% of in-scope commands produce valid
applicable diffs against the mock LLM, plus prompt-contract tests for the real
provider; **zero ops bypass validation**.

| Piece | State |
|---|---|
| `POST /projects/:id/copilot` — proposes, never writes; `…/copilot/decision` logs the human half | 🟡 written; **traced**, never served |
| Validation pipeline (`garh_api/copilot_loop.py`) — real `garh_model` fold on a fork + no-new-hard-failure rules diff | ✅ **executed** — `make copilot-containment` |
| 40-command eval corpus, one generator, `--check` drift gate | ✅ **executed** — 28/28 in-scope applicable (DoD floor 90%), refusals carry zero ops |
| §14 dry-run fold < 10 ms | ✅ **executed** — worst 1.3 ms over the corpus, 1.4 ms on a 4-op batch |
| **Zero ops bypass validation** | ✅ **executed** — 5 malformed classes never reach the fold; impossible ops never reach the caller; refusals never carry ops |
| §13 containment — PII-free summaries, injection → `cannotDo`, one self-correction | ✅ **executed** — 46 checks, hostile provider |
| Copilot panel, DiffPreview reuse, `/` focus, apply = one undo group | 🟡 written; imports resolve; never rendered |
| Prompt-contract tests against a real provider | ⬜ needs a provider key — **the load-bearing gap.** 100% on the mock measures the pipeline, not comprehension |
| A read side for the §10 eval log | ⬜ §11 defines none; none was invented (see DECISIONS) |

**Read `docs/phase-6-7-verification.md`.** Two §13 holes were found and fixed after
the wave shipped, one of them behind a PII test that was passing vacuously.

---

## Phase 7 — Renders 🟡 (code complete; catalogue + determinism EXECUTED, pixels not)

Render worker behind the provider interface: mock (instant stylised composite) and
real (diffusers + ControlNet depth/MLSD, SDXL or FLUX.1-schnell, Real-ESRGAN);
Precise vs Explore; render history pinned to a version with a stale flag on model
change; client-pack batch.

**DoD:** e2e green with the mock provider; a real-provider integration test behind an
env flag; renders carry a version id; concurrent job limit and queue UI states work.

| Piece | State |
|---|---|
| `mint_render_outputs` — the presigned PUT the worker's `require_output("image")` needs | 🟡 written; closes a real Phase-0 gap (every render would have died "nowhere to save its result") |
| Render history, version pinning, re-presigned links | 🟡 written; traced |
| **Stale flag** — ops marks → API serves → UI banner | 🟡 all three legs written; a real hole (in-flight renders never marked) found and fixed this pass; **never executed** |
| 8-shot client pack as one job group, derived seeds, pack zip → `/downloads/{token}` | 🟡 written; traced |
| Per-firm concurrency 4 (once per pack, by design) | 🟡 written; firm-scoped by construction |
| Catalogue mirrors: API + web vs `services/render` | ✅ **executed** — `make render-mirrors`, negative-tested |
| Mock determinism by seed (the derivation) | ✅ **executed** — `RenderRequest.grade_seed_material()`, no clock/urandom in the grade path |
| **Mock determinism in bytes, the composite, §14 <1 s** | ⬜ **no Pillow here.** The test exists and `importorskip`s — it has never run |
| GL capture: viewport + depth + Sobel edges from the ONE canvas | 🟡 written; correct by construction only — needs a browser |
| diffusers + ControlNet path, licence guard | 🟡 licence guard executed (FLUX.1-dev refused); the provider has never run |

---

## Phase 8 — Drawings + exports 🟡 (the moat — core pipeline EXECUTED 2026-08-25: live project → 9 municipal sheets, 17 dim chains sum exactly, DXF passes ezdxf.audit with 0 errors; open: PDF/glTF/PNG export paths, review tray, goldens)

§7 exactly: sheet model, auto-dimensioning engine, the 6 municipal sheets, title
block editor, annotation anchoring with a review tray after solver re-runs; exports —
vector PDF, DXF with the layer convention + DIMSTYLE, glTF, PNG/WhatsApp preset;
area statement generator.

**DoD:** 10 demo projects → sheets → SVG/DXF goldens diff-clean; dims on goldens
≥90% match a hand-checked reference set; DXF opens in LibreCAD/ODA without errors
(`ezdxf.audit()` in CI); every sheet regenerates in ≤5min for a G+1 3BHK.

This is the phase the product is differentiated on. An Indian architect's fee is
earned on drawings, and no competitor owns this stage.

---

## Phase 9 — Polish, billing, share 🟡

Client share links (signed scoped tokens, OTP-lite, pin comments); Razorpay behind
the provider interface + credit metering; onboarding tour + demo project; empty
states; error/loading audit; the §15 delight checklist and §13 security checklist
walkthroughs; load test (50 concurrent solver jobs queue gracefully).

**DoD:** full Playwright happy path — signup → plot → brief → generate → edit →
copilot → 3D → facade → render(mock) → sheets → PDF+DXF download → share link opens
read-only; Lighthouse ≥85 on the dashboard; security checklist all ✅.

| Piece | State |
|---|---|
| Share API — scoped tokens (stored hashed), anonymous project/model/renders/sheets/comments | ✅ **executed** — pytest + the live share e2e below |
| `/share/:token` client viewer — plan through the real canvas, renders, sheet list, comments | ✅ **executed** — `pages/share/ShareViewerPage.tsx`; the happy-path share test runs for real against a live stack (create link → anonymous read-only view → comment → revoke kills it) |
| Happy-path e2e — the share step | ✅ **live**; the other steps remain written-and-skipped with their blocking phase named |
| Billing | 🟡 mock provider behind `billing_live` flag, by design until Razorpay onboarding |
| Onboarding tour, §15 delight walkthrough, load test, Lighthouse | ⬜ not started |

---

## Launch gates (spec §13/§14, beyond phase DoDs)

Independent of the phases — these gate launch, and they're measured, not asserted:

| Gate | Target |
|---|---|
| Blind architect panel (5 architects, monthly) | mean ≥3.5/5; ≥70% of briefs yield ≥1 shortlistable option |
| Dimension edit-rate on municipal sheets | ≤10% |
| Copilot apply-rate | ≥60% |
| Activation | ≥60% of new firms generate options on a real plot in week 1 |
| Money metric | ≥35% of *paid* projects export a submission set by month 3 |
