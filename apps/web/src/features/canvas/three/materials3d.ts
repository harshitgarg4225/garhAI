/**
 * materials3d.ts — surface-group materials for the 3D synthesis, wired to the
 * model's MaterialAssignment list (op 29) with a procedural flat-colour
 * default palette.
 *
 * STILL NO BINARY ASSETS, but no longer flat (2026-09-20): a material whose
 * catalogue row names a texture family gets a generated `map` from
 * `textures3d.ts` — pixels computed from a hash, never a vendored image, so
 * `scripts/check_web_assets.py` has nothing to gate and there is no licence
 * file to keep beside anything (inherited fact 4 intact). HDRIs are still
 * out. A catalogue `textureUrl` is honoured when the CSP would allow it and
 * falls back to the family when it would not.
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
 * (colour × glassiness × texture), shared across every mesh, so a recolour is
 * a uniform write and never a shader recompile mid-interaction (§14). The
 * cache is bounded by the palette + catalogue size, and the TEXTURES behind
 * it are shared per family (one per family, not per colour) because the map
 * is white-based and multiplied by `color`.
 */

import { Color, DoubleSide, MeshStandardMaterial } from 'three';

import type { MaterialAssignment, SurfaceGroup } from '@garh/model';

import {
  getProceduralTexture,
  getUrlTexture,
  isLoadableTextureUrl,
  TEXTURE_TILE_MM,
  type TextureFamily,
} from './textures3d';

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

/**
 * The texture a mesh wears: the assignment's material, looked up in the
 * catalogue's texture map. Null when nothing is assigned or the material
 * declares no family — the flat default palette, as before.
 */
export function textureForScope(
  assignments: readonly MaterialAssignment[],
  scope: MaterialScope,
  materialTextures: Readonly<Record<string, SurfaceTextureSpec>> | undefined,
): SurfaceTextureSpec | null {
  if (materialTextures === undefined) return null;
  const materialId = resolveMaterialId(assignments, scope);
  if (materialId === null) return null;
  return materialTextures[materialId] ?? null;
}

/** A catalogue row's texture, as the page hands it down. */
export interface SurfaceTextureSpec {
  readonly family: TextureFamily | null;
  readonly url: string | null;
}

// ---------------------------------------------------------------------------
// Three materials (singletons)
// ---------------------------------------------------------------------------

const cache = new Map<string, MeshStandardMaterial>();

/**
 * Which texture a mesh wears, resolved from the catalogue by the caller:
 * a procedural `family` (the catalogue's own `texture` string) and/or a
 * `url` the CSP allows. Null on both ⇒ the flat colour of old.
 */
export type SurfaceTexture = SurfaceTextureSpec;

/** The tile size a `SurfaceTexture` repeats at, mm. */
function tileSizeMm(texture: SurfaceTexture): number {
  return texture.family === null ? 1000 : TEXTURE_TILE_MM[texture.family];
}

/**
 * The shared material for a colour. `glass` renders translucent with no
 * depthWrite so rooms stay readable through glazing and glass railings.
 *
 * `texture` (optional) maps the catalogue's texture family — or a
 * catalogue-declared `textureUrl` the CSP allows — onto the box-mapped UVs
 * `geometryBuild` emits in METRES, so `repeat` is just "how many tiles per
 * metre". A url that is not loadable falls back to the family, and no
 * family at all falls back to the flat colour: a material can only ever
 * look plainer than intended, never wrong.
 */
export function getSolidMaterial(
  hex: string,
  glass: boolean,
  texture: SurfaceTexture | null = null,
): MeshStandardMaterial {
  const family = texture?.family ?? null;
  const url = texture !== null && isLoadableTextureUrl(texture.url) ? texture.url : null;
  const key = `${hex}|${glass ? 'g' : 'o'}|${url ?? family ?? '-'}`;
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
  const map =
    url !== null
      ? getUrlTexture(url)
      : family !== null
        ? getProceduralTexture(family, '#FFFFFF')
        : null;
  if (map !== null) {
    // The map is WHITE-based and multiplied by `color`, so one generated
    // texture per family serves every colourway — the architect's hex is
    // still the thing on screen.
    const perMetre = 1000 / tileSizeMm({ family, url });
    map.repeat.set(perMetre, perMetre);
    material.map = map;
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
