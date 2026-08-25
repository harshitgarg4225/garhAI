/**
 * resolve.ts — which material an assignment gives a mesh, and what colour
 * that is on screen. Pure; pinned by `mapping.test.ts`.
 *
 * SPECIFICITY (op 29's `SurfaceGroupRef` narrows by storey and by element):
 *   element-level  (target.elementId === the mesh's element)   beats
 *   storey-level   (target.storeyId === the mesh's storey)     beats
 *   building-wide  (both null).
 * Within one specificity tier, the LAST assignment in the document wins —
 * `house.materials` is in fold order, so "later op wins" is exactly undo's
 * and replay's notion of later.
 *
 * SWATCHES ARE PROCEDURAL (inherited fact 4): the colour comes from the
 * catalogue's declared `colorHex`; when a material declares none, a
 * deterministic colour is derived from its id — never a texture fetch, never
 * a binary asset, so `check_web_assets.py` has nothing to gate here. The
 * catalogue's `textureUrl` field is deliberately ignored in Phase 5.
 */

import type { MaterialAssignment, SurfaceGroup } from '@garh/model';
import type { MaterialItem } from '../../../lib/schemas';

/** Where the querying mesh lives; both narrow, both optional. */
export interface SurfaceContext {
  readonly storeyId: string | null;
  readonly elementId: string | null;
}

/**
 * The assignment that governs `group` for a mesh at `ctx`, or null when the
 * surface wears its default.
 */
export function resolveAssignment(
  materials: readonly MaterialAssignment[],
  group: SurfaceGroup,
  ctx: SurfaceContext,
): MaterialAssignment | null {
  let best: MaterialAssignment | null = null;
  let bestRank = -1;
  for (const assignment of materials) {
    const t = assignment.target;
    if (t.group !== group) continue;
    let rank: number;
    if (t.elementId !== null) {
      if (ctx.elementId === null || t.elementId !== ctx.elementId) continue;
      rank = 2;
    } else if (t.storeyId !== null) {
      if (ctx.storeyId === null || t.storeyId !== ctx.storeyId) continue;
      rank = 1;
    } else {
      rank = 0;
    }
    // `>=` — equal rank prefers the later entry (fold order = op order).
    if (rank >= bestRank) {
      bestRank = rank;
      best = assignment;
    }
  }
  return best;
}

// ---------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------

const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** A deterministic, mid-saturation colour from a string — the no-data swatch. */
export function fallbackColorFromId(id: string): string {
  // FNV-1a over UTF-16 code units; cheap, stable across sessions and clients.
  let hash = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  const hue = hash % 360;
  return hslToHex(hue, 0.28, 0.62);
}

/** The swatch (and mesh tint) for a catalogue material. Always a valid hex. */
export function swatchHex(item: Pick<MaterialItem, 'id' | 'colorHex'>): string {
  if (item.colorHex !== null && HEX_RE.test(item.colorHex)) {
    return normalizeHex(item.colorHex);
  }
  return fallbackColorFromId(item.id);
}

/**
 * One call the 3D scene makes per mesh: the resolved colour for a group at a
 * context, or null for "no assignment — use the scene default".
 */
export function resolvedColorHex(
  materials: readonly MaterialAssignment[],
  catalog: ReadonlyMap<string, MaterialItem>,
  group: SurfaceGroup,
  ctx: SurfaceContext,
): string | null {
  const assignment = resolveAssignment(materials, group, ctx);
  if (assignment === null) return null;
  const item = catalog.get(assignment.materialId);
  if (item === undefined) {
    // Assignment names a material the catalogue no longer carries. Honest
    // fallback: a stable colour derived from the id, never an invisible skip.
    return fallbackColorFromId(assignment.materialId);
  }
  return swatchHex(item);
}

function normalizeHex(hex: string): string {
  if (hex.length === 7) return hex.toUpperCase();
  const r = hex.charAt(1);
  const g = hex.charAt(2);
  const b = hex.charAt(3);
  return `#${r}${r}${g}${g}${b}${b}`.toUpperCase();
}

function hslToHex(hDeg: number, s: number, l: number): string {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = hDeg / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let r = 0;
  let g = 0;
  let b = 0;
  if (hp < 1) [r, g, b] = [c, x, 0];
  else if (hp < 2) [r, g, b] = [x, c, 0];
  else if (hp < 3) [r, g, b] = [0, c, x];
  else if (hp < 4) [r, g, b] = [0, x, c];
  else if (hp < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  const m = l - c / 2;
  const to2 = (v: number): string =>
    Math.round((v + m) * 255)
      .toString(16)
      .padStart(2, '0');
  return `#${to2(r)}${to2(g)}${to2(b)}`.toUpperCase();
}
