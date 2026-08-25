# Phase 5 verification — what was executed, what was not

Same discipline as the earlier ledgers: **EXECUTED** (ran on this machine, output
quoted), **TRACED** (read end to end by hand), **UNVERIFIED** (nobody ran it;
the settling command is named).

Provenance note: Phase 5 was built by a workflow whose final review agent
stalled six times and never ran. This close-out was performed directly by the
orchestrator on 2026-08-09. It is a *targeted* pass over the inherited risk
list from `phase-4-verification.md`, not the full adversarial sweep that agent
would have done — the difference is stated rather than papered over.

## 1. EXECUTED

- `make bare` — green end to end: 238/238 rule fixtures through the real
  engine, 26/26 solver smoke checks, tenancy/secret/env audits, and the web
  asset gate (1 known gap: the Inter font, unchanged, still a release blocker).
- Op-payload check against `packages/model/schema/ops.schema.json` for the
  three Phase-5 ops, field by field at every dispatch site:
  - `facade.apply_kit` requires `{kitId, seed, components}` — `facade/ops.ts`
    carries all three plus `colorwayId`; the documented null form
    (`clearFacadeOp`) also satisfies the schema.
  - `facade.edit_component` `{componentId, patch}` — matches; `patch` is
    walked by `assertIntegralJson`, so a float can never enter the hashed
    document through a facade edit.
  - `material.assign` `{id, target, materialId}` — matches.
- Python syntax + JSON parse sweeps: clean.

## 2. TRACED — the Phase-4 inherited risks, item by item

1. **Picking (the furniture bug class).** `FacadeLayer.tsx:289` registers every
   component mesh with `core.registry.register`. `ThreeDScene.tsx` registers
   through the sanctioned hook path (`usePickableResolver(resolver)` with
   `ref={pickRef}`, resolver mapping intersections to element ids); its
   degraded no-geometry state deliberately mounts a `NULL_RESOLVER` so no mesh
   is ever silently off the picker. The sun module produces lights, not
   pickable meshes. No unregistered mesh producer found in the delta.
2. **One canvas, no fork.** `ThreeDPage.tsx` is a deliberate re-export of
   `PlanPage`: both tabs mount the SAME lazy component, the `:tab` URL segment
   picks the camera mode, and the 2D↔3D switch swaps rig + layer set in place —
   same scene, same selection, same `PickRegistry`, no remount, no Manifold
   re-warm-up. This is §12 implemented as intended.
3. **Manifold laziness.** `three/booleans.ts` has a *type-only* top-level
   import (erased at compile) and loads the WASM via `await import('manifold-3d')`
   at line 96, behind one module, with a documented fallback that renders walls
   without holes and reports itself via `onEngineStatus` — honest degradation.
4. **Assets.** No new binary assets; colors are procedural. The asset gate
   confirms no new absolute URLs.
5. **Stairs.** Rendered from the model's single origin + direction + landing,
   limitation documented in the module header rather than invented geometry.

## 3. UNVERIFIED — and what settles each

| Item | Settles it |
|---|---|
| No 3D code has ever rendered — 55 feature files + integrator work, zero frames drawn | toolchain, then open the 3D tab on the demo project |
| The <100ms dirty-storey rebuild budget (§14) | Playwright perf spec in CI |
| NOAA sun numbers (tests exist against published solstice/equinox values; never run) | `pnpm --filter @garh/web test` |
| Facade generator determinism per (model, kit, seed) — vitest specs written, never run | same |
| Tab 2D↔3D selection round-trip | e2e spec in CI |
| TypeScript has NEVER compiled (now ~35k lines of canvas code under `exactOptionalPropertyTypes`) | `pnpm --filter @garh/web typecheck` |
| The full adversarial review this phase never received | re-run a review agent over the Phase-5 delta, or accept CI + first-render findings as the backstop |

## 4. Known open items carried forward

- Inter font: still the honest release blocker in the asset gate.
- `wall.split` still has no emitting tool (Phase-4 gap, unchanged).
- DXF fixtures still never loaded through real `ezdxf`.
