/**
 * geometry3d.ts — oriented mm boxes → non-indexed triangle arrays, in WORLD
 * units, with procedurally shaded vertex colours.
 *
 * Deliberately imports nothing from three: the output is plain Float32Arrays,
 * so the whole facade geometry pipeline is unit-testable in node and the only
 * file that touches the GPU is `FacadeLayer.tsx`.
 *
 * COORDINATES — the same boundary as `core/coords.ts` (`mmToWorldXYZ`):
 * worldX = +mmX × 0.001, worldY = elevation × 0.001, worldZ = −mmY × 0.001,
 * 1 world unit = 1 m. Re-stated here rather than imported because this module
 * must stay three-free; `geometry3d.test.ts` pins agreement with the core's
 * constants so the two cannot drift.
 *
 * LIT, NOT BAKED (since 2026-09-16). Facade meshes used to be unlit
 * `MeshBasicMaterial` with a Lambert term pre-multiplied into the vertex
 * colours against one fixed "sun" — so chajjas, porches and cladding cast no
 * shadow and ignored the sun scrubber, on exactly the elements whose job is
 * to shade windows. The soup now carries per-vertex NORMALS and the RAW kit
 * colour; `material3d.ts` supplies a `MeshStandardMaterial` that the scene's
 * one sun lights and shadows like the building. Winding is CCW seen from
 * outside and agrees with the normals (`geometry3d.test.ts` pins the cross
 * product), so front-face culling and shadow maps both work.
 */

import type { OrientedBoxMm } from './componentBoxes';

/** mm → world units. MUST equal `core/constants.WORLD_UNITS_PER_MM`. */
export const WORLD_PER_MM = 0.001;

/** Extra multiplier for a selected component — "lit from within", not a hue. */
export const SELECTION_BOOST = 1.18;

/** `#RRGGBB` → linear-ish [r,g,b] in 0..1. Anything unparseable is grey. */
export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (m === null) return [0.6, 0.63, 0.65];
  const v = parseInt(m[1] ?? '9aa0a6', 16);
  return [((v >> 16) & 0xff) / 255, ((v >> 8) & 0xff) / 255, (v & 0xff) / 255];
}

export interface BoxTriangleData {
  /** 36 vertices per box, xyz interleaved, world units. */
  readonly positions: Float32Array;
  /** Matching rgb per vertex: the kit colour, times the selection scale. */
  readonly colors: Float32Array;
  /** Unit outward face normal per vertex, world units. */
  readonly normals: Float32Array;
}

/**
 * Triangulate `boxes` into one non-indexed soup. 12 triangles per box, flat
 * outward normals, raw colour — the scene's lights do the shading.
 */
export function buildBoxTriangles(
  boxes: readonly OrientedBoxMm[],
  colorScale = 1,
): BoxTriangleData {
  const positions = new Float32Array(boxes.length * 36 * 3);
  const colors = new Float32Array(boxes.length * 36 * 3);
  const normals = new Float32Array(boxes.length * 36 * 3);
  let cursor = 0;

  for (const box of boxes) {
    // Basis in world space. Length axis L, depth axis D (plan normal), up U.
    const lx = box.dirX * WORLD_PER_MM;
    const lz = -box.dirY * WORLD_PER_MM; // plan y → −world z
    const dxp = box.dirY; // plan-space depth axis = (dirY, −dirX)
    const dyp = -box.dirX;
    const dx = dxp * WORLD_PER_MM;
    const dz = -dyp * WORLD_PER_MM;

    const cx = box.cx * WORLD_PER_MM;
    const cz = -box.cy * WORLD_PER_MM;
    const y0 = box.baseElevMm * WORLD_PER_MM;
    const y1 = (box.baseElevMm + box.heightMm) * WORLD_PER_MM;

    const hl = box.lenMm / 2;
    const hd = box.depthMm / 2;

    // 8 corners: [±L][±D][y0|y1]
    const corner = (sl: number, sd: number, y: number): [number, number, number] => [
      cx + lx * hl * sl + dx * hd * sd,
      y,
      cz + lz * hl * sl + dz * hd * sd,
    ];
    const c000 = corner(-1, -1, y0);
    const c100 = corner(1, -1, y0);
    const c010 = corner(-1, 1, y0);
    const c110 = corner(1, 1, y0);
    const c001 = corner(-1, -1, y1);
    const c101 = corner(1, -1, y1);
    const c011 = corner(-1, 1, y1);
    const c111 = corner(1, 1, y1);

    // Unit face normals in world space.
    const lLen = Math.hypot(lx, lz) || 1;
    const dLen = Math.hypot(dx, dz) || 1;
    const nL: [number, number, number] = [lx / lLen, 0, lz / lLen];
    const nD: [number, number, number] = [dx / dLen, 0, dz / dLen];

    const [r, g, b] = hexToRgb(box.colorHex);

    const face = (
      a: readonly number[],
      bb: readonly number[],
      c: readonly number[],
      d: readonly number[],
      normal: readonly [number, number, number],
    ): void => {
      const fr = Math.min(r * colorScale, 1);
      const fg = Math.min(g * colorScale, 1);
      const fb = Math.min(b * colorScale, 1);
      // Two CCW triangles: a-b-c, a-c-d.
      for (const v of [a, bb, c, a, c, d]) {
        positions[cursor] = v[0] ?? 0;
        colors[cursor] = fr;
        normals[cursor] = normal[0];
        cursor += 1;
        positions[cursor] = v[1] ?? 0;
        colors[cursor] = fg;
        normals[cursor] = normal[1];
        cursor += 1;
        positions[cursor] = v[2] ?? 0;
        colors[cursor] = fb;
        normals[cursor] = normal[2];
        cursor += 1;
      }
    };

    // +D / −D (the long outward/inward faces)
    face(c010, c110, c111, c011, nD);
    face(c100, c000, c001, c101, [-nD[0], -nD[1], -nD[2]]);
    // +L / −L (ends)
    face(c110, c100, c101, c111, nL);
    face(c000, c010, c011, c001, [-nL[0], -nL[1], -nL[2]]);
    // top / bottom
    face(c001, c011, c111, c101, [0, 1, 0]);
    face(c010, c000, c100, c110, [0, -1, 0]);
  }

  return { positions, colors, normals };
}
