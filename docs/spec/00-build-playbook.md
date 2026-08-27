---
name: garh-ai-builder
description: 'End-to-end build playbook for "Garh AI" — an India-first, AI-native house design platform for architects (brief → AI floor plans → 2D/3D editor → AI facades/renders → compliance → municipal drawing set → PDF/DXF export). Use this skill whenever building, scaffolding, extending, debugging, or reviewing ANY part of Garh AI: the monorepo, model core / op API, CP-SAT layout solver, NBC/Vastu rules engine, plan editor canvas, 3D/facade system, render pipeline, auto-dimensioning/sheet engine, LLM copilot, exports, auth/billing, or seed data. Also use it when the user says "Garh", "house design app", "floor plan generator", "NBC compliance", "Vastu engine", "auto-dimensioning", or references files in a garh-ai repo. The architecture decisions here are LOCKED — follow them literally and in order; do not re-litigate them.'
---

# Garh AI — Builder Playbook

You are building **Garh AI**: a web platform where an Indian architect enters a plot + client brief and gets compliant AI-generated floor plan options, edits them on a 2D/3D canvas (directly or via natural-language copilot), generates AI facades and renders, and exports a municipal submission drawing set (PDF/DXF). Business context, personas, and full feature specs live in the references — your job is to produce working, production-quality code.

## How to use this skill

1. Read this file fully before writing any code.
2. Read `references/engineering-playbook.md` — it contains the repo layout, DB schema, op taxonomy, solver algorithm, rules DSL + seeded NBC rules, auto-dim algorithm, API surface, and quality checklists. It is the implementation source of truth.
3. Consult `references/product-spec.md` when you need feature intent, acceptance criteria, or scope boundaries (what is MVP vs v1.1/v2 — build MVP only unless told otherwise).
4. Consult `references/market-research-and-oss-licenses.md` only when choosing/validating a dependency (license table) or when product questions reference competitors (Forma/Snaptrude/TestFit patterns).
5. Follow the build phases in order (§Build Phases). Complete each phase's Definition of Done, run its verification, and fix failures before moving on.

## Locked decisions — do not re-litigate

- **Stack:** pnpm monorepo · frontend Vite + React 18 + TypeScript strict + Three.js/react-three-fiber + Zustand · backend FastAPI (Python 3.11) + Postgres 15 + Redis · workers = Python processes consuming Redis queues · everything runs locally via one `docker compose up`.
- **Geometry lives in integer millimeters.** All model coordinates, lengths, thicknesses are `int` mm. Display layer converts to ft-in / m / gaj. Never store floats for lengths (floating-point drift breaks dimension chains and compliance math).
- **Model core is an op-log system** (§playbook 3–4): the model state = fold(ops). Every mutation — user drag, copilot command, solver output — is a typed op. Undo/redo, versions, diffs, autosave, and provenance all derive from this. Single writer per project (enforced by per-project lock), but ops are designed CRDT-compatible for future multiplayer.
- **LLMs never emit geometry.** LLMs emit typed ops / parameters / choices from enums; the solver and rules engine produce and validate all geometry. If you find yourself asking an LLM for coordinates, stop — you are building the wrong thing.
- **Determinism where trust matters:** solver (CP-SAT + heuristics), rules engine, dimensions, areas are deterministic and unit-tested against golden files. ML/LLM only for: brief parsing, copilot op emission, facade kit selection, render generation, option rationales.
- **Solver = two-stage:** coarse CP-SAT topology on a 300mm module → refinement snapped to the 115mm brick module. Envelope: rect/L/T plots only in MVP.
- **Licenses:** Apache/MIT/BSD/MPL dependencies only. Never GPL/AGPL in app code (OR-Tools ✅ Apache, ezdxf ✅ MIT, Manifold ✅ Apache, diffusers ✅ Apache). Never RPLAN-derived model weights. Full table in the research reference.
- **Every external AI/GPU service is behind a provider interface with a deterministic mock** (`PROVIDER_LLM=mock`, `PROVIDER_RENDER=mock`). The full app must run and be e2e-testable with zero API keys and zero GPUs. Real providers: Anthropic API (structured outputs) and a diffusers+ControlNet worker.
- **Multi-tenancy:** every table row carries `firm_id`; every query is firm-scoped through a repository layer that requires a tenant context — no raw table access from route handlers.
- **MVP cut lines (from the spec, binding):** municipal drawing set only (site plan, floor plans, 4 elevations, 1 section, door/window schedule, area statement); 3 city rule packs (Bengaluru, Delhi NCR, Hyderabad); 2 facade kits (Contemporary, Modern Minimal); exterior renders Precise+Explore, interior Explore only; no PDF-trace import, no map pick, no MEP layouts, no curved walls, no multiplayer.

## Golden rules (read twice)

1. **The op is the atom.** UI never mutates state directly; it dispatches ops. The server is the op sequencer. If a feature can't be expressed as ops, redesign the feature.
2. **Feasible ≠ plausible.** A legal plan can be an ugly plan. Solver output must pass the critic score gates (§playbook 5.6) before a user ever sees it. Never show a hard-fail plan.
3. **Every AI action is previewable and reversible.** Copilot and solver results render as a diff (before/after) with apply/reject. Apply = ops appended; reject = nothing happened.
4. **Assumptions are visible.** Every default the AI used (room size, floor height, ₹/sqft) is an editable chip in the UI with a citation where one exists (NBC clause, city bye-law).
5. **Compliance never blocks, it informs.** Violations are red chips with the rule citation and an auto-fix suggestion. Architects can override anything; overrides are logged.
6. **mm in, pretty out.** Parse any user input ("12'6\"", "3.8m", "12 ft") into mm at the boundary; format on the way out per project units. One shared `units.ts` / `units.py` pair, golden-tested to agree.
7. **Latency budgets are features:** canvas interactions <16ms/frame, op round-trip <100ms perceived (optimistic apply + rollback), compliance re-check ≤500ms debounced, 3 solver options ≤60s, render ≤30s (mock: instant), sheet set ≤5min.
8. **Empty states teach.** Every screen's empty state shows what to do next and offers the seeded demo project. A new user must reach "generated plans for a real plot" in under 10 minutes without docs.
9. **Errors say what to do next.** No raw exceptions to the UI. Every user-facing error: what happened, why (if known), one-click next action. Workers retry with backoff; jobs are resumable; the UI shows job state honestly.
10. **Golden files gate merges.** Solver plans, dimension chains, DXF output, area statements have golden-file tests. If output changes intentionally, regenerate goldens in the same commit with a note.
11. **Security is not a phase** — tenancy scoping, input validation (Pydantic/zod at every boundary), signed share-link tokens, rate limits, and secrets hygiene are built into each phase's DoD (§playbook 13).
12. **Delight is specified, not vibes** — implement §playbook 15 exactly (optimistic ops, skeletons, keyboard-first tools, undo everywhere, autosave badge, Indian formatting: ft-in + gaj, ₹ grouping, +91, WhatsApp share).

## Build Phases

Work through phases in order. Each phase = branch, implement, verify, then move on. Don't gold-plate ahead of the phase (e.g., no render UI in Phase 2).

**Phase 0 — Scaffold & foundations.** Monorepo per playbook §1; docker-compose (postgres/redis/api/web/workers); CI (typecheck, lint, pytest, vitest, Playwright smoke); DB migrations (playbook §2 DDL); auth (email+OTP, JWT), firms/users/projects CRUD; tenancy repository layer; seed script with demo firm + demo project.
_DoD:_ `docker compose up` → login → create empty project; CI green; a cross-tenant access attempt test proves 404/403.

**Phase 1 — Model core + op engine.** Implement the model document (playbook §3), op taxonomy (§4), fold/replay, op validation, undo/redo stacks, version snapshots, provenance. Server op sequencer endpoint + optimistic client store.
_DoD:_ property-based test: any generated op sequence folds deterministically & replays to identical state hash; undo/redo round-trips 1,000 random ops; op rejected cleanly when invalid (e.g., opening wider than wall).

**Phase 2 — Plot, brief, rules engine.** Plot boundary editor (rect + vertex editor w/ edge lengths, north compass, roads per edge); DXF boundary import (ezdxf); regulatory profiles + rules engine + the 3 seeded city packs and Vastu pack (playbook §6 — implement the DSL exactly, seed all listed rules); brief form + completeness meter; LLM brief-parse behind provider interface (mock returns fixture briefs).
_DoD:_ rule fixtures all pass (each rule has ≥1 passing + ≥1 failing fixture); changing city preset re-validates live; brief → chips UI.

**Phase 3 — Layout solver.** Playbook §5 exactly: envelope derivation, stair/circulation pre-placement, CP-SAT stage A, refinement stage B, door/window auto-placement, critic scoring, diversity, partial re-solve with locked-room ID preservation. Solver runs as a worker job with progress events.
_DoD:_ 20-brief golden corpus solves ≤60s each with ≥3 options; all options pass hard rules; locked-room regen preserves IDs; plan JSON goldens stable; unit tests for each constraint builder.

**Phase 4 — 2D editor canvas.** Orthographic Three.js scene (one scene graph + one hit-testing system shared with 3D); tools: select/wall/door/window/stair/balcony/measure (keyboard: V/W/D/N/S/B/M); 115mm snap default + fine-grid toggle; dimension-first editing (click dim → type value → op); room auto-detection (planar subdivision) with live name/area tags; live compliance chips; furniture placement (≥30 items to start, 3D assets, Indian sizes).
_DoD:_ Playwright: draw a 2-room plan from scratch, all ops sync, undo/redo works, compliance chip appears when a bedroom < 9.5m² and disappears on fix; 60fps pan/zoom on demo G+2 (measured).

**Phase 5 — 3D + facades.** Extrude storeys (per-floor heights), openings cut (Manifold), slabs/parapet/mumty/OHT; 2D↔3D synced selection; orbit/walk; sun widget (date/time → shadows, city-centroid lat/long); facade kit system + the 2 kits (playbook §8) applied as parametric geometry with per-element edit.
_DoD:_ plan edit reflects in 3D <100ms; facade kit applies/edits/exports consistently; screenshot-based visual regression on demo project.

**Phase 6 — Copilot.** LLM structured-output → candidate ops (playbook §10 schemas); validation loop (ops → dry-run fold → rules check → diff preview); apply/reject UX; ~25-op coverage + honest "can't do that yet" for out-of-scope asks (logged).
_DoD:_ 40-command eval fixture set: ≥90% of in-scope commands produce valid applicable diffs with mock LLM fixtures + prompt-contract tests for the real provider; zero ops bypass validation.

**Phase 7 — Renders.** Render worker behind provider interface: mock (instant stylized viewport composite) + real (diffusers + ControlNet depth/MLSD, SDXL or FLUX.1-schnell, Real-ESRGAN); Precise vs Explore; exterior presets + interior Explore; render history pinned to version, stale-flag on model change; client-pack batch.
_DoD:_ e2e with mock provider; real provider integration test behind env flag; renders carry version id; concurrent job limit + queue UI states.

**Phase 8 — Drawings + exports (the moat).** Playbook §7 exactly: sheet model, auto-dimensioning engine, the 6 municipal sheets, title block editor, annotation anchoring (persists across manual/copilot edits; solver re-runs → review tray); exports: vector PDF (print-true scales), DXF (layer convention, DIMSTYLE), glTF, PNG/WhatsApp preset; area statement generator.
_DoD:_ golden-file suite: 10 demo projects → sheets → SVG/DXF goldens diff-clean; dims on goldens ≥90% match hand-checked reference set; DXF opens in LibreCAD/ODA viewer without errors (CI check via `dxf audit` script); every sheet regenerates ≤5min for G+1 3BHK.

**Phase 9 — Polish, billing, share.** Client share links (signed scoped tokens, OTP-lite, pin comments); Razorpay behind provider interface (mock in dev) + credit metering events; onboarding tour + demo project; empty states; error/loading audit; §playbook 15 delight checklist walkthrough; §13 security checklist walkthrough; load test (50 concurrent solver jobs queue gracefully).
_DoD:_ full Playwright happy path: signup → plot → brief → generate → edit → copilot → 3D → facade → render(mock) → sheets → PDF+DXF download → share link opens read-only; Lighthouse ≥85 perf on dashboard; security checklist all ✅.

## When you deviate

You will hit situations the playbook doesn't cover. Deviate only when you must, and leave a `DECISIONS.md` entry: what, why, and which playbook section it touches. Never deviate on: locked decisions, license rules, LLM-never-emits-geometry, tenancy scoping, integer-mm geometry.

## References

- `references/engineering-playbook.md` — **read before coding; ToC at top.** Repo layout · DDL · model core · op taxonomy · solver spec · rules DSL + seeded rules · auto-dim/sheets · facade kits · render service · LLM integration · API surface · frontend architecture · security checklist · performance budgets · UX/delight rules · testing strategy · seed data · env/config.
- `references/product-spec.md` — full CPTO-approved product spec v2.0 (features F0–F10, acceptance criteria, MVP vs v1.1/v2/v3 waves, metrics, legal).
- `references/market-research-and-oss-licenses.md` — competitor JTBD patterns + verified OSS license table (consult before adding any dependency).
