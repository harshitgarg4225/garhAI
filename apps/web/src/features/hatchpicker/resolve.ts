/**
 * resolve.ts — what hatch a surface actually gets, and why.
 *
 * Three layers, most specific first:
 *
 *   1. THE ARCHITECT'S OVERRIDE. Always wins. A-9 exists because a mapping
 *      from materials to patterns is a good default and never a rule: a
 *      rubble-filled cavity wall faced in brick is drawn how the architect
 *      says it is drawn.
 *   2. THE MATERIAL'S IMPLICATION (A-10). Whatever `material.assign` (op 29)
 *      put on this surface, run through `materialHatch.ts`.
 *   3. THE SURFACE DEFAULT. What the drawings service already poachés this
 *      kind of surface with when nobody has said anything.
 *
 * WHOSE MATERIAL. The material comes from `resolveAssignment` in
 * `features/canvas/materials` — the SAME resolver the 3D view colours meshes
 * with, imported rather than re-derived. Two rules for "which material is on
 * this wall" is how the 3D view and the section come to disagree about the
 * wall, and this repo's standing rule is one source per number.
 *
 * The override follows the same specificity ladder as an assignment
 * (element beats storey beats building) so that "brick everywhere, stone on
 * the ground floor" behaves the way the same sentence behaves for materials.
 */

import type { MaterialAssignment, SurfaceGroup, SurfaceGroupRef } from '@garh/model';

import type { MaterialItem } from '../../lib/schemas';
import { resolveAssignment, type SurfaceContext } from '../canvas/materials';
import { hatchForMaterial, type MaterialHatch } from './materialHatch';
import type { HatchPatternKey } from './patterns';

/**
 * What each surface hatches as with no material assigned.
 *
 * The wall and slab rows are NOT a preference — they are what the drawings
 * service draws today, restated so the picker shows an architect the truth
 * about their own sheets before they touch anything:
 *
 *   `projection/walls.py:432`   cut walls  → PATTERN_MASONRY = ANSI31 = diagonal
 *   `sections/project.py:362`   cut slabs  → PATTERN_CONCRETE = ANSI37 = cross
 *   `projection/symbols.py:527` columns    → PATTERN_CONCRETE
 *
 * The rows for surfaces the section does not poché yet — door, window,
 * railing, cladding, ceiling — are this feature's choice, and they are chosen
 * to be obviously legible rather than clever: a door leaf reads as timber, a
 * window as glazing, a railing as metal. If the section starts drawing them,
 * this table is where the two must be reconciled.
 */
export const SURFACE_DEFAULTS: Readonly<Record<SurfaceGroup, HatchPatternKey>> = {
  external_wall: 'diagonal',
  internal_wall: 'diagonal',
  parapet: 'diagonal',
  floor: 'cross',
  ceiling: 'cross',
  roof: 'cross',
  plinth: 'cross',
  staircase: 'cross',
  cladding: 'diagonal',
  door: 'timber',
  window: 'glass',
  railing: 'steel',
};

/**
 * Human names for the model's twelve surface groups.
 *
 * Here rather than in the panel so that the panel file exports components and
 * nothing else — `react-refresh/only-export-components` is a real constraint,
 * and surface metadata sits naturally beside the surface defaults anyway.
 * `Record<SurfaceGroup, string>` makes it total at compile time.
 */
export const SURFACE_LABELS: Readonly<Record<SurfaceGroup, string>> = {
  external_wall: 'External walls',
  internal_wall: 'Internal walls',
  floor: 'Floors',
  ceiling: 'Ceilings',
  roof: 'Roof',
  parapet: 'Parapets',
  railing: 'Railings',
  door: 'Doors',
  window: 'Windows',
  cladding: 'Cladding',
  plinth: 'Plinth',
  staircase: 'Staircase',
};

/** Where a resolved hatch came from. */
export type HatchSource = 'override' | 'material' | 'surface-default';

export interface ResolvedHatch {
  readonly pattern: HatchPatternKey;
  readonly source: HatchSource;
  /** The material that implied it, when one did. */
  readonly materialId: string | null;
  /** The material binding, when `source` is `material` — carries its own why. */
  readonly binding: MaterialHatch | null;
  /** One line for the UI. Always populated. */
  readonly why: string;
}

/** One hand-picked pattern, and the surface it was picked for. */
export interface HatchOverride {
  readonly target: SurfaceGroupRef;
  readonly pattern: HatchPatternKey;
}

/**
 * Overrides, keyed by `hatchTargetKey`.
 *
 * The value carries its own target rather than only the pattern, so a consumer
 * that needs the list of overridden surfaces — `hatchPlan()` builds exactly
 * that for the sheets request — reads it back instead of parsing the key
 * apart. A key parser would be one more place that has to agree about the
 * shape of a string.
 */
export type HatchOverrides = ReadonlyMap<string, HatchOverride>;

/**
 * Canonical key for an override target.
 *
 * Deliberately the same three fields, in the same order, as
 * `surfaceTargetKey` in `features/canvas/materials/assignOps.ts` — an override
 * targets exactly what an assignment targets. Its own prefix, because the two
 * live in different stores and a shared string would invite one to be read
 * with the other's map.
 */
export function hatchTargetKey(target: SurfaceGroupRef): string {
  return `hatch:${target.group}:${target.storeyId ?? '*'}:${target.elementId ?? '*'}`;
}

/**
 * The override governing `group` at `ctx`, or null.
 *
 * Element beats storey beats building, checked by construction rather than by
 * scanning: three lookups in specificity order, first hit wins.
 */
export function resolveOverride(
  overrides: HatchOverrides,
  group: SurfaceGroup,
  ctx: SurfaceContext,
): HatchPatternKey | null {
  const candidates: SurfaceGroupRef[] = [];
  if (ctx.elementId !== null) {
    candidates.push({ group, storeyId: ctx.storeyId, elementId: ctx.elementId });
    // An element-scoped override authored without a storey must still be found
    // by a mesh that knows its storey.
    candidates.push({ group, storeyId: null, elementId: ctx.elementId });
  }
  if (ctx.storeyId !== null) candidates.push({ group, storeyId: ctx.storeyId, elementId: null });
  candidates.push({ group, storeyId: null, elementId: null });
  for (const candidate of candidates) {
    const found = overrides.get(hatchTargetKey(candidate));
    if (found !== undefined) return found.pattern;
  }
  return null;
}

export interface ResolveHatchInput {
  /** `house.materials` — op 29's rows, in fold order. */
  readonly materials: readonly MaterialAssignment[];
  /** `materialId -> item`, from `useMaterialsCatalogue`. */
  readonly catalog: ReadonlyMap<string, MaterialItem>;
  readonly overrides: HatchOverrides;
  readonly group: SurfaceGroup;
  readonly ctx: SurfaceContext;
}

/** The one call the panel, the swatch and (via `hatchPlan`) the sheets make. */
export function resolveHatch({
  materials,
  catalog,
  overrides,
  group,
  ctx,
}: ResolveHatchInput): ResolvedHatch {
  const assignment = resolveAssignment(materials, group, ctx);
  const materialId = assignment?.materialId ?? null;

  const override = resolveOverride(overrides, group, ctx);
  if (override !== null) {
    return {
      pattern: override,
      source: 'override',
      materialId,
      binding: null,
      why: 'Chosen by hand for this surface.',
    };
  }

  if (materialId !== null) {
    const item = catalog.get(materialId);
    // A material the catalogue no longer carries still binds: its id is the
    // evidence, and `hatchForMaterial` reads ids. Dropping to the surface
    // default here would silently un-brick a wall whose material row is fine.
    const binding = hatchForMaterial(
      item === undefined
        ? { id: materialId }
        : {
            id: item.id,
            name: item.name,
            category: item.category,
            // `texture` is not on `MaterialItem` yet (see materialHatch.ts);
            // when the schema handoff lands, spreading it here is the change.
          },
    );
    return {
      pattern: binding.pattern,
      source: 'material',
      materialId,
      binding,
      why: `${item?.name ?? materialId}: ${binding.why}`,
    };
  }

  const fallback = SURFACE_DEFAULTS[group];
  return {
    pattern: fallback,
    source: 'surface-default',
    materialId: null,
    binding: null,
    why: `No material assigned — drawn as ${fallback}, the default for this surface.`,
  };
}
