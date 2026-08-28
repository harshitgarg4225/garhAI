/**
 * `features/hatchpicker` — A-9 (the hatch pattern picker) and A-10 (material
 * to hatch binding).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT WAS WRONG
 * ════════════════════════════════════════════════════════════════════════════
 * `services/drawings/render/hatch_patterns.py` knows fifteen patterns — solid,
 * diagonal, cross, earth, brick, concrete, insulation, plaster, stone, steel,
 * glass, sand, timber, tile, grass — vendored from the standard ISO/ACAD
 * tables and drawn identically by the SVG, PDF and DXF writers. Nothing in the
 * UI exposed a single one of them. A feature that believes it shipped.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THE INTEGRATOR MOUNTS
 * ════════════════════════════════════════════════════════════════════════════
 *   <HatchBindingPanel />   surface → material → hatch, with the override.
 *   <HatchPatternPicker />  the fifteen-swatch grid on its own.
 *   <HatchSwatch />         one pattern, drawn in real SVG geometry.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT ANYTHING ELSE CALLS
 * ════════════════════════════════════════════════════════════════════════════
 *   resolveHatch(...)       the hatch a surface gets, and why
 *   hatchForMaterial(...)   A-10 on its own — material in, pattern out
 *   hatchPlan(...)          every resolved scope, ready to send with a sheet
 *                           generation request
 *   swatchGeometry(...)     pure geometry, if something else wants to draw one
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE DRIFT RULE, WHICH IS THE POINT OF THE WHOLE MODULE
 * ════════════════════════════════════════════════════════════════════════════
 * `patterns.ts` is a copy of a Python table, and `patterns.drift.test.ts`
 * parses `hatch_patterns.py` on every run and fails on any difference — key,
 * order, label, ACAD name, or one float of geometry. Add a pattern in Python
 * and forget the mirror: red. That spec carries its own negative controls (it
 * mutates the real Python source three ways and proves the comparison
 * notices), so the gate cannot rot into one that passes no matter what.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HANDOFF — three changes outside this directory, in dependency order
 * ════════════════════════════════════════════════════════════════════════════
 * 1. SERVE `texture` (small, high value). Catalogue rows may carry a curated
 *    `texture` (tile, plaster, wood, metal, speckle, stone, concrete, vein,
 *    glass, brick) — the single best signal for a hatch. `MaterialOut`
 *    (apps/api/garh_api/routers/catalog.py) does not serve it and
 *    `materialItemSchema` (apps/web/src/lib/schemas.ts) does not parse it. Add
 *    `texture: StrictStr | None` there and `texture: z.string().nullable()`
 *    here, then pass it through in `resolve.ts` where the comment marks the
 *    spot. Everything keeps working without it — `materialHatch.ts` reads ids
 *    and names instead, and its spec holds that reading to an answer key and,
 *    for every row that has one, to the curated texture — but the curated
 *    field is the better input.
 *
 * 2. CARRY THE PLAN TO THE RENDERER. `api.sheets.generate(projectId, input)`
 *    (apps/web/src/lib/api.ts) takes `kinds`, `scaleDenominator`,
 *    `titleBlock`… — add `hatches?: HatchPlanEntry[]` and pass
 *    `hatchPlan({materials, catalog, overrides})`. Server side:
 *    `POST /projects/{id}/sheets/generate` (garh_api/routers/jobs.py) accepts
 *    it and hands it to the drawings worker; `services/drawings` looks a
 *    surface up in it instead of using its constants at
 *    `projection/walls.py:432` (PATTERN_MASONRY), `sections/project.py:327,
 *    362, 383, 410, 425, 724, 734` and `projection/symbols.py:527`. Do NOT
 *    re-author the material → pattern mapping in Python: it lives here, in one
 *    place, and a second copy is the failure this whole module is shaped
 *    around.
 *
 * 3. PERSIST THE OVERRIDE. `store.ts` keeps overrides in memory on purpose —
 *    see its header: a hatch override is a property of the drawing, not of
 *    this browser, so `localStorage` would be a private second truth about
 *    what a sheet prints. It wants a model op (`hatch.assign`, mirroring op 29
 *    `material.assign`: same `SurfaceGroupRef` target, a `pattern` string, a
 *    null pattern to clear) in `packages/model` and its Python twin, after
 *    which `store.ts` becomes a thin reader over `house.hatches` and
 *    `hatchTargetKey` keeps working unchanged.
 */

export {
  HATCH_PATTERNS,
  HATCH_PATTERN_KEYS,
  hatchPattern,
  isHatchPatternKey,
  isSolidPattern,
} from './patterns';
export type { HatchLine, HatchPatternDef, HatchPatternKey } from './patterns';

export {
  baseAngleDeg,
  baseSpacing,
  hatchFamilies,
  perpSpacing,
  DOT_FRACTION,
  MAX_HATCH_LINES,
} from './geometry';
export type { BBox, HatchFamiliesOptions, HatchFamily, Segment } from './geometry';

export {
  familyPath,
  swatchGeometry,
  swatchSpacing,
  SWATCH_TARGET_LINES,
  SWATCH_UNITS,
} from './swatch';
export type { SwatchGeometry, SwatchOptions } from './swatch';

export { drawnLengthInside } from './ink';

export {
  CATEGORY_HATCH,
  FALLBACK_PATTERN,
  MATERIAL_HATCH_OVERRIDES,
  TEXTURE_HATCH,
  TOKEN_HATCH,
  hatchForMaterial,
  hatchFromTokens,
  materialSegments,
} from './materialHatch';
export type { HatchBindingSource, MaterialHatch, MaterialLike } from './materialHatch';

export {
  SURFACE_DEFAULTS,
  SURFACE_LABELS,
  hatchTargetKey,
  resolveHatch,
  resolveOverride,
} from './resolve';
export type {
  HatchOverride,
  HatchOverrides,
  HatchSource,
  ResolveHatchInput,
  ResolvedHatch,
} from './resolve';

export { hatchPlan } from './plan';
export type { HatchPlanEntry, HatchPlanInput } from './plan';

export { useHatchOverrideStore, useHatchOverrides } from './store';
export type { HatchOverrideState } from './store';

export { HatchSwatch } from './HatchSwatch';
export type { HatchSwatchProps } from './HatchSwatch';
export { HatchPatternPicker } from './HatchPatternPicker';
export type { HatchPatternPickerProps } from './HatchPatternPicker';
export { HatchBindingPanel } from './HatchBindingPanel';
export type { HatchBindingPanelProps } from './HatchBindingPanel';
