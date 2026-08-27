# Phase 4 verification — what was executed, what was not

Same discipline as `phase-0-verification.md`, `phase-2-verification.md` and
`phase-3-verification.md`. Three categories, and the value of this document is
that it does not blur them.

| Category       | Meaning                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------ |
| **EXECUTED**   | Actually run on this machine. Output quoted.                                                                 |
| **TRACED**     | Read end to end by hand, or checked by a script written for the purpose. No TypeScript compiler, no browser. |
| **UNVERIFIED** | Nobody ran it and nobody could. Stated plainly, with the command that settles it.                            |

## The headline

**No Phase 4 code has ever run.** Not one line. Phase 4 is ~24,000 lines of
TypeScript and TSX across `apps/web/src/features/canvas/**`,
`apps/web/src/pages/project/plan/**`, `PlanPage.tsx`, three stores and the
keyboard map — plus roughly 200 vitest specs and a 506-line Playwright spec.
This machine has no Node, no pnpm and no browser, so `tsc`, `vitest`, `eslint`,
`vite` and Playwright have never seen any of it.

That is a much weaker position than Phase 2 or 3, and it is worth being precise
about why. The rules engine (Phase 2) and the ortools-free solver (Phase 3) are
pure Python and were genuinely executed here. A WebGL canvas cannot be. So the
right expectation for Phase 4 is: **the first `pnpm --filter @garh/web typecheck`
will find things.** The work below reduces the class of things it will find; it
does not eliminate them.

---

## 1. EXECUTED — evidence, not claims

Everything in this section was run on this machine, on bare `python3` (3.9.6).

### 1.1 The dependency-free gates are still green

```
$ make bare
  ok    envelope inside plot — setbacks bite
  ...
  ok    circulation cap is enforced — cap 11% rejects 12%
all checks passed
==> tenancy audit: only the repository layer may touch tables
    ok — no direct session access outside repositories
==> secret audit: apps/web must only read import.meta.env.VITE_*
    ok — no secret names or non-VITE_ env reads
==> env audit: .env.example <-> Settings / WorkerSettings
env-audit: 134 documented names, 96 settings fields, 24 direct os.environ reads
env-audit: ok — no drift between .env.example and the settings classes
==> asset audit: every /public URL the web app names must exist
web-assets: 3 absolute asset URLs referenced, 1 allowlisted, 1 known gaps
WARN  /fonts/inter-medium.woff is MISSING — ... RELEASE BLOCKER ...
web-assets: ok — no unexpected missing assets (1 known gap outstanding)

  all dependency-free gates passed
```

- 26/26 solver smoke checks pass; 238/238 rule fixtures pass through the real
  `garh_rules.evaluate()`. Phase 2's and Phase 3's guarantees are intact — the
  canvas changed nothing they depend on.
- `make bare` gained a sixth gate, `asset-audit`. See §1.2.

One drift worth recording, unrelated to the canvas: `scripts/solver_smoke.py`
now reports `critique assembles a full breakdown — composite 85/100` where
`docs/phase-3-verification.md` quotes 70/100. The smoke still passes (the
composite is asserted equal to `composite_score(parts)`, so the number moving is
not a broken invariant), but the Phase-3 document's quoted figure is stale.

### 1.2 A new gate: `make asset-audit`

`scripts/check_web_assets.py` (new) scans `apps/web/src` and `index.html` for
absolute asset URLs and asserts each one exists under `apps/web/public`.

It exists because of one specific silent failure. `LABEL_FONT_URL` is
`/fonts/inter-medium.woff`, and every troika `Text` on the canvas — dimension
values, room names, room areas, compliance markers — is created with it. The
file **is not in the repository**. Nothing catches that: it is not a build
error, not a type error, not a test failure. troika falls back to fetching
Roboto from `fonts.gstatic.com`, which `apps/web/nginx.conf` blocks
(`font-src 'self' data:`), so in production the labels do not render _at all_
and nothing says why.

The gate was executed both ways:

- as-is → `exit=0`, with a WARNING naming the file, the licence, the URL to
  fetch it from, and the words RELEASE BLOCKER;
- with a stub file dropped in → `exit=1`, `stale KNOWN_GAPS entries` — so the
  baseline cannot rot into a permanent excuse;
- with a _different_ asset made missing → hard failure (that is the default
  path; `KNOWN_GAPS` is a one-entry allowlist, not a mode).

`make bare` therefore stays green **and** prints the blocker on every single
run. It is also wired into `make verify`.

### 1.3 Every import in the workspace resolves to a real export

`tscheck.py` (throwaway, in the scratchpad) parses every `.ts`/`.tsx` under
`apps/web/src`, `packages/model/src`, `packages/ui/src` and `e2e/`, resolves
each relative and `@garh/*` specifier through the real `tsconfig` path aliases,
and checks every named import against the module's actual exports —
transitively through `export *` barrels.

```
0 problems across 260 files
```

Negative-tested: an earlier revision of the script missed `export async
function` and reported 19 false positives, all of which were real exports. The
final run is clean.

**What this does and does not prove.** It proves no import will fail to
resolve, and no `import { thing }` names something that is not exported. It
proves nothing about _types_. `tsc` remains the authority.

### 1.4 No op-payload field drift against the JSON Schema

Every `{ type: '<op>', payload: { … } }` literal in
`features/canvas/**` and `pages/project/plan/**` was parsed, its top-level
payload keys extracted, and each key checked against
`packages/model/schema/ops.schema.json`:

```
balcony.set: ok  emitted=[action, id, polygon, projectionMm, railingHeightMm,
                          railingKind, slabThicknessMm, storeyId]
furniture.set: ok  emitted=[action, catalogId, id, rotationDeg, storeyId]
opening.resize: ok  emitted=[heightMm, openingId, sillMm, widthMm]
room.assign: ok  emitted=[locked, name, roomId, tags, type]
room.set_target: ok  emitted=[mustFace, roomId, targetAreaMm2]
wall.add: ok  emitted=[a, b, id, kind, storeyId, thicknessMm]
wall.move: ok  emitted=[a, b, wallId]
...
no op-field drift against ops.schema.json
```

Two builders use object spread (`openingAddOp`, `stairAddOp`) so their keys came
from the input interface instead; `OpeningAddInput` and `StairAddInput` were
compared field-by-field against `OpeningAddPayload` / `StairAddPayload` by hand
and match exactly, including `landing: StairLanding | null`.

This is the worst failure class in the phase — a wrong field name corrupts the
op log irreversibly — and it is the one thing about Phase 4 that is now
mechanically checked rather than asserted.

### 1.5 The op the canvas emits is `furniture.set`, not three ops

Confirmed against `packages/model/src/ops.ts` op 25. The playbook's
`furniture.place/transform/delete` are the three values of the `action`
discriminator. The canvas codes against the real file. Same for `column.set`
(op 24) and `balcony.set` (op 26).

### 1.6 Icon names and JSX props

- Every `<Icon name="…">` and every `icon:`/`icon=` string literal in
  `apps/web/src` was checked against the 60 names in `packages/ui/src/icons.tsx`.
  No misses.
- A JSX prop checker matched 260 usages of 93 locally-declared components
  against their `*Props` interfaces. One report, `Tooltip tabIndex` in
  `AppShell.tsx`, is a tag-scanner artifact (the attribute belongs to a
  `<main>`), not a finding.

---

## 2. Defects found and fixed in this review

### 2.1 Furniture was completely unpickable — BLOCKER, fixed

`FurnitureLayer.tsx` tagged its `InstancedMesh` with
`userData.garhPick` and its header documented the integration as:

```ts
const hit = raycaster.intersectObjects(scene.children, true)[0];
const pick = hit?.object.userData.garhPick;
```

**The core does no such thing.** `hitTest.pickAt` raycasts
`registry.objects()` — a flat array of _explicitly registered_ objects, walked
non-recursively (`intersectObjects(objects, false, …)`). A mesh that is merely
in the scene is never tested. The furniture layer never called
`registry.register`, so:

- clicking a placed sofa selected nothing;
- hover never highlighted furniture;
- the select tool could not drag or delete it;
- the inspector's entire furniture panel was unreachable by pointer;
- `⌘A` put furniture ids in the selection that no click could ever produce.

Fixed by registering the mesh with the core's one registry, with a resolver that
maps `intersection.instanceId` → `furnitureId` through a ref (so adding a chair
does not re-register the mesh) and stamps the active storey. `useCanvasCoreOptional`
is used so the layer still renders in a scene with no core — picking is simply
off there, which is honest and is not the same as picking silently not working.
The `userData` tag is kept as a debugging label and the header now says plainly
that **registration is the contract**.

This is the §12 "one hit-testing system" rule failing in the _quiet_ direction:
not a second picker, but a module that believed it was on the shared one and was
not.

### 2.2 `Math.round` on four op-payload paths — fixed

`core/coords.ts` states the rule in its header: float → mm always rounds **half
away from zero** via `roundMm`, never `Math.round`, because `Math.round` is
half-_up_ and would make a wall drawn westwards land one millimetre off from the
same wall drawn eastwards. Four call sites broke it, all of them producing
values that go straight into a payload:

| File                           | What it produced                                           |
| ------------------------------ | ---------------------------------------------------------- |
| `tools/selectTool.ts` (×2)     | the drag delta for `wall.move` when a length is typed      |
| `overlays/inspector/fields.ts` | the new `b` endpoint for `wall.move` from the length field |
| `pages/project/PlanPage.tsx`   | the drop point for `furniture.set`                         |

All four now use `ptRound` from `@garh/model` (which is `roundHalfAwayFromZero`
per component). `tools/useToolSettings.ts` was also switched to `roundMm` for
wall thickness — positive-only, so the distinction cannot bite there, but having
one answer to "how does a float become mm" is the point.

Re-grepped afterwards: every surviving `Math.round` under `features/canvas` and
`pages/project/plan` is a degree normalisation, a pixel tolerance, a display
readout, or render-only geometry. None reaches an op.

### 2.3 The missing canvas font could ship silently — gated

See §1.2. Not fixed (the binary needs a human), but it can no longer be
forgotten.

---

## 3. Rejected — read as findings, not defects

| Reported/suspected                                                                                           | Why it is not a defect                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PlanPage` spreads `{...tools.canvasHandlers}` and then sets `onClick`/`onHoverChange`, overriding the tools | `useToolController.canvasHandlers` contains neither. It supplies `onPointerDown/Move/Up`, `onDoubleClick`, `onContextMenu`, `onPointerLeave`, `onNavigatingChange`. Checked key by key. `onDoubleClick` _is_ in both and is explicitly forwarded first.                                                                                                                                     |
| `Math.round` in `overlays/compliance/mapping.ts`, `overlays/tags/placement.ts`, `plan/planGeometry.ts`       | Render-only. Marker positions, label collision boxes, stair/column symbol rings. Nothing there becomes an op.                                                                                                                                                                                                                                                                               |
| `view.grid` (⇧G) collides with `snap.toggle` (G); `view.dimensions` (⇧D) with `tool.door` (D)                | `matchBinding` compares the modifier spec, and the unmodified branch explicitly `continue`s when `event.shiftKey`. Traced through the real function; the shifted bindings are reachable and the unshifted ones are not shadowed.                                                                                                                                                            |
| A tool's `blocked` field is a compliance chip that blocks                                                    | It is not compliance. `blocked` carries `validateOpAgainstDoc` rejections (zero-length wall, opening past the 115 mm end margin, an unsolvable flight) — model validity, which must block. Compliance issues reach the canvas only through `useComplianceOverlay` → chips and markers, and `commit()` never reads them. `furniture` advisories are asserted non-blocking by their own spec. |
| Shared plan materials will be disposed by R3F when a `<mesh material={…}>` unmounts                          | R3F v8 does not dispose objects passed as props, only ones it created. `PlanPage` disposes both material sets explicitly on unmount and re-creates them lazily.                                                                                                                                                                                                                             |
| `MergedLayer`/`LineLayer` return `null` conditionally                                                        | Every hook runs before the return. Hook order is stable.                                                                                                                                                                                                                                                                                                                                    |
| `<FurniturePlacementProvider>` sits outside `<Canvas>` so the layer inside cannot see it                     | `@react-three/fiber` is pinned at 8.17.9 and auto-bridges parent context (`its-fine`, since 8.8). No `useContextBridge` needed.                                                                                                                                                                                                                                                             |

---

## 4. TRACED — read carefully, believed, not executed

- **The coordinate boundary.** `worldX = +mmX × 0.001`, `worldY = elevation`,
  `worldZ = −mmY × 0.001`, ortho `up = (0,0,−1)`. The Z flip is what makes north
  point up on screen; because `roundHalfAwayFromZero` is symmetric, the flip
  introduces no rounding bias. Every mm↔world↔screen transform lives in
  `core/coords.ts` and nowhere else — grepped to confirm.
- **One picker.** `pickAt` is the only raycast in the delta. There is no R3F
  `onClick`/`onPointerOver` on any mesh anywhere under `features/canvas` or
  `pages/project/plan` (grepped). Batched geometry stays pickable through
  `usePickableResolver` + a `faceIndex → id` array. After §2.1, furniture is on
  the same path.
- **§12's three guarantees are implemented once.** `BaseTool` owns Esc (a
  three-rung ladder: clear the buffer → cancel the drawing → decline the key),
  Enter, and the numeric-entry buffer, plus `wantsKey` — the capture-phase
  listener that lets `3` mean 3000 mm mid-draw and the second floor while idle.
  No tool reimplements them.
- **No React render per pointer move.** `useCanvasControls` coalesces moves to
  one per animation frame, caches the `DOMRect`, hit-tests lazily, and fires
  `onHoverChange` only when the hovered element changes. `useToolController`
  keeps the tool in a ref and reads stores through `getState()`. Camera state
  lives in `ViewportController`, outside React. `ui.setCanvasZoom` no-ops unless
  the printed scale changed.
- **DOM nodes per dimension label: none.** Dimension values and room tags are
  troika `Text` inside the GL context; the only DOM in the overlay is the single
  click-to-edit field, the HUD and the scale readout.
- **§15 "no dead text".** A dimension opens an editor on click; a room's area
  opens `room.set_target` (and says so — typing a number cannot move four
  walls); a room's name opens `room.assign` on double-click. The double-click
  ambiguity between the room tag and the room floor (both register `kind:'room'`
  with the same id) is handled deliberately, not guessed.
- **Empty state.** `PlanEmpty` floats over a live canvas, teaches the `W`
  shortcut and the typed-length trick, and offers "Start from the plot and
  brief". It is not `@garh/ui`'s `EmptyState`, on purpose.

---

## 5. UNVERIFIED — and the command that settles each

| Item                                                          | Why                                                                                                                                                                                                                                     | Settles it                                                                                                                                  |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **The whole phase typechecks**                                | No Node. `exactOptionalPropertyTypes` + `noUncheckedIndexedAccess` are strict enough that hand-tracing types is not a substitute                                                                                                        | `pnpm --filter @garh/web typecheck`                                                                                                         |
| **~200 vitest specs pass**                                    | Written, never run. Numeric expectations were hand-computed against the real `roundHalfAwayFromZero`/`parseLengthMm`, and one (`dimensionText(1,'ft-in') === "0'-0\""`) was found wrong by a Python port and pinned as actual behaviour | `pnpm --filter @garh/web test`                                                                                                              |
| **Anything renders at all**                                   | No browser has loaded the canvas. A single throw in `CameraRig` or the grid shader is a blank tab                                                                                                                                       | `pnpm --filter @garh/web dev`, open the Plan tab                                                                                            |
| **The grid shader compiles**                                  | GLSL1 `gl_FragColor` + `#include <colorspace_fragment>` + `fwidth`, written against three r169, never handed to a GPU                                                                                                                   | as above; a shader error prints to the console                                                                                              |
| **drei `<Line>` accepts `depthTest`/`opacity`/`renderOrder`** | drei 9.114.3 spreads `...rest` onto both object and material; believed correct, never compiled                                                                                                                                          | first typecheck                                                                                                                             |
| **`camera.manual = true`**                                    | Load-bearing: without it R3F rewrites the ortho frustum on resize and discards `mmPerPx`. Typed through a local intersection because `manual` is not in `@types/three`                                                                  | first render + a window resize                                                                                                              |
| **§14: <16 ms/frame on a G+2**                                | No renderer, no demo plan with three storeys                                                                                                                                                                                            | `e2e/tests/performance.spec.ts` once Phase 3 can seed a solved G+2                                                                          |
| **`e2e/tests/plan-canvas.spec.ts` (the Phase-4 DoD)**         | Needs the stack, a browser and a seeded project                                                                                                                                                                                         | `pnpm test:canvas`                                                                                                                          |
| **Furniture picking actually works after the §2.1 fix**       | The fix is correct against the registry's API as read, but no click has been dispatched                                                                                                                                                 | Plan tab: place a chair, click it, check the inspector shows it                                                                             |
| **The canvas label font**                                     | Not in the repo                                                                                                                                                                                                                         | drop `Inter-Medium.woff` into `apps/web/public/fonts/`, then `make asset-audit` must go from WARN to clean and `KNOWN_GAPS` must be emptied |

### Known functional gaps (built as designed, not defects)

- **`wall.split` is in the taxonomy and no tool emits it.** Splitting a wall is
  not reachable from the canvas. The op, its fold and its inverse all exist.
- **Stairs draw as one straight run** even for dogleg/L/U. The model stores a
  single origin + direction + landing; inventing a turn would put geometry on a
  municipal drawing that the model does not contain. Documented in `stairSymbol`.
- **`SetbackContext.maxProjectionMm` is always `null`** — the seeded packs
  express projections as rules, not as a resolvable value key. One line here
  once `REG_VALUE_KEYS` grows `projectionMaxMm`.
- **Only axis-aligned walls are dimensioned**; skew walls are reported through
  `DimensionChainSet.skewWallIds` rather than silently dropped.
- **`plan-canvas.spec.ts` asserts ops, folds and compliance against the server —
  never pixels.** It would pass against a renderer that drew nothing. It does
  measure `mmPerPx` from a calibration wall rather than assuming it, so a
  viewport or DPR change cannot make it lie, and it `test.skip`s with a named
  reason when the compliance engine returns `evaluated: false`. Visual
  regression is Phase 5's problem.

---

## 6. What to do first when a toolchain exists

1. `pnpm install && pnpm --filter @garh/web typecheck` — expect real errors.
   Start with `CameraRig`'s `manual` intersection type and drei's `LineProps`.
2. `pnpm --filter @garh/web test`.
3. Drop the font in. Until then the plan has no text on it in production.
4. `pnpm dev`, open the Plan tab, draw a wall, place a chair, click the chair.
   Step 4's last clause is the §2.1 regression check and there is no cheaper one.
