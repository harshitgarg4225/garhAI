/**
 * materials3d.ts — surface-group materials for the 3D synthesis, wired to the
 * model's MaterialAssignment list (op 29) with a procedural flat-colour
 * default palette. NO textures, NO HDRIs, NO binary assets (inherited fact 4:
 * `scripts/check_web_assets.py` gates absolute asset URLs, and the cheapest
 * way to never fail that gate is to reference nothing) — every material here
 * is a flat-colour `MeshStandardMaterial`.
 *
 * RESOLUTION ORDER for a mesh's colour (most specific wins):
 *   1. an assignment targeting the ELEMENT (`target.elementId`)
 *   2. an assignment targeting the surface group ON THIS STOREY
 *   3. a building-wide assignment for the surface group
 *   4. the solid's `overrideColor` (OHT's HDPE black)
 *   5. the default palette for the surface group
 * Among equally specific assignments the LAST one in the document wins —
 * the model keeps `materials` id-sorted, and op 29 replaces by id, so "last"
 * is deterministic across folds.
 *
 * A materialId is turned into a colour through the `materialColors` map the
 * page passes down (materialId → hex, sourced from `GET /catalog/materials`).
 * An assignment naming a material the map cannot colour falls through to the
 * defaults rather than painting magenta — the assignment still shows in the
 * inspector; the 3D view just cannot honour it yet, and honouring it wrong
 * would be worse.
 *
 * SINGLETON CACHE, LIKE THE PLAN'S: one `MeshStandardMaterial` per distinct
 * (colour × glassiness), shared across every mesh, so a recolour is a uniform
 * write and never a shader recompile mid-interaction (§14). The cache is
 * bounded by the palette + catalogue size.
 */

import { Color, DoubleSide, MeshStandardMaterial } from 'three';

import type { MaterialAssignment, SurfaceGroup } from '@garh/model';

// ---------------------------------------------------------------------------
// Default palette — procedural flat colours, one per surface group
// ---------------------------------------------------------------------------

export const DEFAULT_SURFACE_COLORS: Readonly<Record<SurfaceGroup, string>> = {
  external_wall: '#E3DDD2',
  internal_wall: '#F4F1EA',
  floor: '#DED8CE',
  ceiling: '#F7F4EF',
  roof: '#B9B4AB',
  parapet: '#D8D2C8',
  railing: '#2E2E2E',
  door: '#B08A5E',
  window: '#CFE3E8',
  cladding: '#7A5230',
  plinth: '#9C9C97',
  staircase: '#C4BEB4',
};

// ---------------------------------------------------------------------------
// Assignment resolution (pure — exercised by the specs)
// ---------------------------------------------------------------------------

export interface MaterialScope {
  readonly surface: SurfaceGroup;
  readonly storeyId: string | null;
  readonly elementId: string | null;
}

/**
 * Resolve the materialId op 29 assigned to this scope, or null. Specificity:
 * element > storey > building; ties broken by document order (last wins).
 */
export function resolveMaterialId(
  assignments: readonly MaterialAssignment[],
  scope: MaterialScope,
): string | null {
  let best: { specificity: number; materialId: string } | null = null;
  for (const a of assignments) {
    const t = a.target;
    let specificity: number;
    if (t.elementId !== null) {
      // An element-scoped assignment still names a surface group, so a
      // balcony's railing and its slab (same elementId, different groups)
      // can be assigned independently.
      if (scope.elementId === null || t.elementId !== scope.elementId) continue;
      if (t.group !== scope.surface) continue;
      specificity = 2;
    } else if (t.group !== scope.surface) {
      continue;
    } else if (t.storeyId !== null) {
      if (scope.storeyId === null || t.storeyId !== scope.storeyId) continue;
      specificity = 1;
    } else {
      specificity = 0;
    }
    // >= : later assignments of equal specificity win.
    if (best === null || specificity >= best.specificity) {
      best = { specificity, materialId: a.materialId };
    }
  }
  return best?.materialId ?? null;
}

/** Element ids that carry an element-scoped assignment — the bucket splitter. */
export function elementScopedAssignmentIds(
  assignments: readonly MaterialAssignment[],
): Set<string> {
  const out = new Set<string>();
  for (const a of assignments) {
    if (a.target.elementId !== null) out.add(a.target.elementId);
  }
  return out;
}

/**
 * Final colour for a mesh, hex string. `materialColors` maps catalogue
 * materialId → colorHex; missing entries fall through (see module header).
 */
export function colorForScope(
  assignments: readonly MaterialAssignment[],
  scope: MaterialScope,
  materialColors: Readonly<Record<string, string>> | undefined,
  overrideColor: string | null,
): string {
  const materialId = resolveMaterialId(assignments, scope);
  if (materialId !== null && materialColors !== undefined) {
    const hex = materialColors[materialId];
    if (hex !== undefined) return hex;
  }
  if (overrideColor !== null) return overrideColor;
  return DEFAULT_SURFACE_COLORS[scope.surface];
}

// ---------------------------------------------------------------------------
// Three materials (singletons)
// ---------------------------------------------------------------------------

const cache = new Map<string, MeshStandardMaterial>();

/**
 * The shared material for a colour. `glass` renders translucent with no
 * depthWrite so rooms stay readable through glazing and glass railings.
 */
export function getSolidMaterial(hex: string, glass: boolean): MeshStandardMaterial {
  const key = `${hex}|${glass ? 'g' : 'o'}`;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;

  const material = new MeshStandardMaterial({
    color: new Color(hex),
    roughness: glass ? 0.15 : 0.85,
    metalness: 0,
    side: DoubleSide,
  });
  if (glass) {
    material.transparent = true;
    material.opacity = 0.35;
    material.depthWrite = false;
  }
  cache.set(key, material);
  return material;
}

/** The ground plane's material — soft, non-reflective, shadow-receiving. */
export function getGroundMaterial(): MeshStandardMaterial {
  return getSolidMaterial('#CDC9C0', false);
}

/** Dispose every cached material. For tests and full canvas teardown only. */
export function disposeSolidMaterials(): void {
  for (const material of cache.values()) material.dispose();
  cache.clear();
}
