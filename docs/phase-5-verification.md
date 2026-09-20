# Phase 5 verification — what was executed, what was not

Same discipline as the earlier ledgers: **EXECUTED** (ran on this machine, output
quoted), **TRACED** (read end to end by hand), **UNVERIFIED** (nobody ran it;
the settling command is named).

Provenance note: Phase 5 was built by a workflow whose final review agent
stalled six times and never ran. This close-out was performed directly by the
orchestrator on 2026-08-09. It is a _targeted_ pass over the inherited risk
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
3. **Manifold laziness.** `three/booleans.ts` has a _type-only_ top-level
   import (erased at compile) and loads the WASM via `await import('manifold-3d')`
   at line 96, behind one module, with a documented fallback that renders walls
   without holes and reports itself via `onEngineStatus` — honest degradation.
4. **Assets.** No new binary assets; colors are procedural. The asset gate
   confirms no new absolute URLs.
5. **Stairs.** Rendered from the model's single origin + direction + landing,
   limitation documented in the module header rather than invented geometry.

## 3. UNVERIFIED — and what settles each

| Item                                                                                             | Settles it                                                                                         |
| ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| No 3D code has ever rendered — 55 feature files + integrator work, zero frames drawn             | toolchain, then open the 3D tab on the demo project                                                |
| The <100ms dirty-storey rebuild budget (§14)                                                     | Playwright perf spec in CI                                                                         |
| NOAA sun numbers (tests exist against published solstice/equinox values; never run)              | `pnpm --filter @garh/web test`                                                                     |
| Facade generator determinism per (model, kit, seed) — vitest specs written, never run            | same                                                                                               |
| Tab 2D↔3D selection round-trip                                                                  | e2e spec in CI                                                                                     |
| TypeScript has NEVER compiled (now ~35k lines of canvas code under `exactOptionalPropertyTypes`) | `pnpm --filter @garh/web typecheck`                                                                |
| The full adversarial review this phase never received                                            | re-run a review agent over the Phase-5 delta, or accept CI + first-render findings as the backstop |

## 4. Known open items carried forward

- Inter font: still the honest release blocker in the asset gate.
- `wall.split` still has no emitting tool (Phase-4 gap, unchanged).
- DXF fixtures still never loaded through real `ezdxf`.

---

# Addendum — 2026-09-20 (J06: "see and tune the building in 3D")

Everything in §3 above that concerned the 3D view has now been executed, and
executing it found five defects that reading never would. This addendum is the
new ledger for the 3D tree; the sections above stand as the record of what was
true in August.

## A. EXECUTED — in vitest, on this machine

| Claim                                                                                                                                                                                                                                                              | Command                                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| 979 tests across the whole canvas tree, green                                                                                                                                                                                                                      | `pnpm exec vitest run src/features/canvas` (from `apps/web`) |
| **Opening holes are geometrically right** — the REAL Manifold WASM cuts the fixture's south wall; a ray through the door meets 0 triangles, beside and above it exactly 2, the mesh is watertight, and the uncut prism is the negative control that DOES hit twice | `vitest run src/features/canvas/three/booleans.test.ts`      |
| The no-WASM fallback still SHOWS openings (254 mm proud plate vs the 40 mm panel), and a solid with no fallback builds identically either way                                                                                                                      | `vitest run src/features/canvas/three/solids.test.ts`        |
| The set-back terrace: identical floors, a rear set-back, the hole case, an overhang, no upper walls, an L-shaped set-back whose two faces sum exactly                                                                                                              | `vitest run src/features/canvas/three/terrace.test.ts`       |
| Box-mapped UVs are metres: one UV unit == one metre on every triangle of every bucket                                                                                                                                                                              | `vitest run src/features/canvas/three/solids.test.ts`        |
| Ten procedural texture families, each checked for what makes it that material, and every catalogue `texture` string is one the renderer draws                                                                                                                      | `vitest run src/features/canvas/three/textures3d.test.ts`    |
| The GLB round trip through the real `GLTFExporter` → `GLTFLoader`, including a ray through the door in the PARSED file                                                                                                                                             | `vitest run src/features/canvas/three/gltfExport.test.ts`    |
| Walk collision (walls stop you, doors let you through, a stride cannot tunnel) and the look-up clamp                                                                                                                                                               | `vitest run src/features/canvas/sun/nav`                     |
| Four facade kits, each instantiating components that all extrude, with four distinct geometry signatures                                                                                                                                                           | `vitest run src/features/canvas/facade`                      |

## B. EXECUTED — in a real browser (Chromium + SwiftShader, live stack)

`e2e/tests/three-d.spec.ts`, green in 18 s against a seeded stack. It now
asserts, rather than annotates:

- the boolean engine reaches `ready` and `data-garh-holes` is `true`;
- the canvas DRAWS — frame statistics (distinct colours, dominant share, mean
  brightness), not a golden PNG, so it needs no baseline and holds on any GPU;
- 09:00 vs 16:00 changes 10.6% of pixels while both frames stay lit — the sun
  study, which the unlit facade layer used to break silently;
- a real click on the building selects a model element through the one picker;
- the GLB the Export panel produces: magic word, version, declared length, and
  the object NAMES read out of its JSON chunk;
- §14: the incremental rebuild after a real inspector edit, 14.4 ms of 100 ms.

CI runs the whole `@canvas` suite on every push (`e2e (smoke)` job).

## C. What executing it FOUND (five defects, all fixed)

1. **The Manifold WASM had never loaded, in any session.** No `locateFile`, so
   Emscripten resolved `manifold.wasm` against Vite's pre-bundled script, got
   `index.html`, and failed with `expected magic word 00 61 73 6d, found 3c 21
64 6f`. Every session silently ran the no-holes fallback, and the spec
   _excused_ it as "an environment fact". Also needed `'wasm-unsafe-eval'` in
   the SPA's CSP, or production would have fallen back the moment dev worked.
2. **Openings vanished in that fallback** — the 40 mm panel is centred in a
   230 mm wall, so it was buried inside the uncut prism.
3. **The facade cast no shadows and ignored the sun** — unlit `MeshBasicMaterial`
   with shading baked against a fixed direction, on exactly the elements whose
   job is to shade openings.
4. **Every exported GLB named its objects `mesh_0 … mesh_N`.** R3F sets no
   `name`; the unit test exported a headless fixture that named its own meshes.
   CLAUDE.md bug 6.
5. **The rebuild counter counted rebuilds that never happened**, because the
   same stats were re-reported whenever the callback changed identity — which
   made the §8 isolation claim fail on a kit apply that re-meshed nothing.

## D. STILL UNVERIFIED after this pass

| Item                                                                   | What settles it                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The golden-PNG baseline (`visual-regression.spec.ts`) is still skipped | One run on the CI runner class with `--update-snapshots`, committing `three-d-facade.png` in the same PR. It must NOT be minted on a developer machine: `toHaveScreenshot` self-blesses, and this box's SwiftShader output is not the runner's. The frame-statistics assertions in `three-d.spec.ts` are what covers the gap meanwhile — they catch a blank, flat or unlit view, but not a wrong shade. |
| Walk mode has never run in a browser                                   | A spec that enters Walk, presses W into a wall and asserts the pose stopped. The maths (collision, doors, the look-up clamp, tunnelling) is pinned in `orbitOps.test.ts`; what is unproven is the pointer/keyboard plumbing in `useNav3d`.                                                                                                                                                              |
| Textures have not been looked at by an architect                       | They are generated, not photographed: ten families, each pinned by what makes it that material. Whether "brick" reads as brick at 1:1 on a client's screen is a human judgement, like the rule-pack seeds.                                                                                                                                                                                              |
| `componentBoxes`' outward-normal rule on a concave envelope            | Unchanged from August: the centroid rule is documented for rectangular/L/T envelopes and fails visibly (a chajja indoors) rather than silently.                                                                                                                                                                                                                                                         |
