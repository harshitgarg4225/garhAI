/**
 * plan.ts — the resolved hatch for every surface the document has an opinion
 * about, as a flat array.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS SHAPE, AND WHY IT LIVES HERE
 * ════════════════════════════════════════════════════════════════════════════
 * The drawings service currently poachés from constants:
 * `PATTERN_MASONRY` for a cut wall, `PATTERN_CONCRETE` for a cut slab
 * (`projection/walls.py`, `sections/project.py`). For A-10 to reach paper,
 * something has to tell it that THESE external walls are brick.
 *
 * There are two ways to do that and only one of them is safe. The unsafe way
 * is to re-author the material → pattern mapping in Python, which is a second
 * hand-kept table of exactly the kind that produced the three hatch defects
 * this feature is under orders not to repeat. The safe way is for the client
 * to send the ANSWER — group, scope, pattern — and for the renderer to draw
 * what it is told. This function builds that answer.
 *
 * The entries are ordered deterministically (group, then storey, then element)
 * so the same document always produces the same payload: a request body that
 * reshuffles itself defeats caching and makes a golden diff unreadable.
 *
 * Nothing sends it yet — `POST /projects/:id/sheets/generate` has no field for
 * it. The handoff in `index.ts` names the field and the two files that consume
 * it. Until then this is the exact array to attach, and it is under test.
 */

import { SURFACE_GROUPS, type MaterialAssignment, type SurfaceGroup } from '@garh/model';

import type { MaterialItem } from '../../lib/schemas';
import type { HatchPatternKey } from './patterns';
import { hatchTargetKey, resolveHatch, type HatchOverrides, type HatchSource } from './resolve';

export interface HatchPlanEntry {
  readonly group: SurfaceGroup;
  /** Null for a building-wide row. */
  readonly storeyId: string | null;
  /** Null unless one element was singled out. */
  readonly elementId: string | null;
  readonly pattern: HatchPatternKey;
  readonly source: HatchSource;
  /** The material behind it, when one is assigned — for the drawing's legend. */
  readonly materialId: string | null;
}

export interface HatchPlanInput {
  readonly materials: readonly MaterialAssignment[];
  readonly catalog: ReadonlyMap<string, MaterialItem>;
  readonly overrides: HatchOverrides;
}

/**
 * Every surface scope the document says something about, resolved.
 *
 * The twelve building-wide rows are always present — a renderer needs an
 * answer for every surface it might cut, and "no row" is not an answer.
 * Narrower rows are added for every storey or element that a material
 * assignment or an override actually names; enumerating every storey against
 * every group would emit rows nobody authored.
 */
export function hatchPlan({ materials, catalog, overrides }: HatchPlanInput): HatchPlanEntry[] {
  const scopes = new Map<
    string,
    { group: SurfaceGroup; storeyId: string | null; elementId: string | null }
  >();

  const add = (group: SurfaceGroup, storeyId: string | null, elementId: string | null): void => {
    scopes.set(hatchTargetKey({ group, storeyId, elementId }), { group, storeyId, elementId });
  };

  for (const group of SURFACE_GROUPS) add(group, null, null);
  for (const assignment of materials) {
    add(assignment.target.group, assignment.target.storeyId, assignment.target.elementId);
  }
  for (const override of overrides.values()) {
    add(override.target.group, override.target.storeyId, override.target.elementId);
  }

  return [...scopes.values()]
    .map(({ group, storeyId, elementId }) => {
      const resolved = resolveHatch({
        materials,
        catalog,
        overrides,
        group,
        ctx: { storeyId, elementId },
      });
      return {
        group,
        storeyId,
        elementId,
        pattern: resolved.pattern,
        source: resolved.source,
        materialId: resolved.materialId,
      };
    })
    .sort(
      (a, b) =>
        a.group.localeCompare(b.group) ||
        (a.storeyId ?? '').localeCompare(b.storeyId ?? '') ||
        (a.elementId ?? '').localeCompare(b.elementId ?? ''),
    );
}
