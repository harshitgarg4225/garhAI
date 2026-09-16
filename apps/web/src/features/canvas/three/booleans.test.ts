/**
 * booleans.test.ts — the Manifold boundary, EXECUTED.
 *
 * Two things this file proves that nothing proved before 2026-09-09:
 *
 *  1. **The loader can find its binary.** `manifoldLocateFile` hands
 *     Emscripten the bundler-resolved asset URL, so the browser fetches the
 *     real `.wasm` instead of the SPA's `index.html` (the `<!do` magic-word
 *     CompileError the UAT recorded). The URL Vite resolves for the asset is
 *     asserted to end in `.wasm`; the negative control is a non-wasm request,
 *     which must keep Emscripten's default resolution.
 *
 *  2. **The cut is geometrically right.** The REAL WASM engine runs here (Node
 *     reads the binary through `fs`), cuts the fixture's south wall around its
 *     door, and the result is checked as geometry, not as a boolean flag: a
 *     ray through the door meets NO triangle, rays beside and above the door
 *     meet exactly two (in and out), the mesh is watertight, and it carries
 *     more triangles than the uncut prism. The uncut prism is the negative
 *     control — the same ray through it DOES hit twice.
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { fixedId, makeTwoRoomPlanWithOpenings } from '@garh/model';

import {
  __resetBooleanEngineForTests,
  booleanEngineStatus,
  ensureBooleanEngine,
  getPrismCutter,
  manifoldLocateFile,
  runningUnderNode,
  type CutMeshMm,
  type PrismCutter,
} from './booleans';
import type { PrismProfileF } from './extrusion';
import { storeySolids } from './solids';

const GF = fixedId('storey', 'GF');

// ---------------------------------------------------------------------------
// 1. Locating the binary
// ---------------------------------------------------------------------------

describe('manifoldLocateFile', () => {
  it('sends the .wasm request to the bundler-resolved asset URL, which really ends in .wasm', () => {
    const url = manifoldLocateFile('manifold.wasm', '/node_modules/.vite/deps/');
    expect(url.endsWith('.wasm')).toBe(true);
    // The whole point: NOT a sibling of the pre-bundled script.
    expect(url.startsWith('/node_modules/.vite/deps/')).toBe(false);
  });

  it('honours an explicit asset URL', () => {
    expect(manifoldLocateFile('manifold.wasm', '/x/', '/assets/manifold-abc123.wasm')).toBe(
      '/assets/manifold-abc123.wasm',
    );
  });

  it('negative control: any other file keeps Emscripten’s default resolution', () => {
    expect(manifoldLocateFile('manifold.worker.js', '/node_modules/.vite/deps/')).toBe(
      '/node_modules/.vite/deps/manifold.worker.js',
    );
  });

  it('vitest runs under Node, where the locator must stay out of the way', () => {
    expect(runningUnderNode()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. The real cut
// ---------------------------------------------------------------------------

interface Ray {
  readonly ox: number;
  readonly oy: number;
  readonly oz: number;
  readonly dx: number;
  readonly dy: number;
  readonly dz: number;
}

/** Möller–Trumbore, both windings, mm model space. Counts every hit t > 0. */
function rayHits(mesh: CutMeshMm, ray: Ray): number {
  const p = mesh.positionsMm;
  let hits = 0;
  for (let i = 0; i + 8 < p.length; i += 9) {
    const ax = p[i] ?? 0;
    const ay = p[i + 1] ?? 0;
    const az = p[i + 2] ?? 0;
    const e1x = (p[i + 3] ?? 0) - ax;
    const e1y = (p[i + 4] ?? 0) - ay;
    const e1z = (p[i + 5] ?? 0) - az;
    const e2x = (p[i + 6] ?? 0) - ax;
    const e2y = (p[i + 7] ?? 0) - ay;
    const e2z = (p[i + 8] ?? 0) - az;
    // h = d × e2
    const hx = ray.dy * e2z - ray.dz * e2y;
    const hy = ray.dz * e2x - ray.dx * e2z;
    const hz = ray.dx * e2y - ray.dy * e2x;
    const det = e1x * hx + e1y * hy + e1z * hz;
    if (Math.abs(det) < 1e-9) continue;
    const inv = 1 / det;
    const sx = ray.ox - ax;
    const sy = ray.oy - ay;
    const sz = ray.oz - az;
    const u = inv * (sx * hx + sy * hy + sz * hz);
    if (u < 0 || u > 1) continue;
    // q = s × e1
    const qx = sy * e1z - sz * e1y;
    const qy = sz * e1x - sx * e1z;
    const qz = sx * e1y - sy * e1x;
    const v = inv * (ray.dx * qx + ray.dy * qy + ray.dz * qz);
    if (v < 0 || u + v > 1) continue;
    const t = inv * (e2x * qx + e2y * qy + e2z * qz);
    if (t > 1e-6) hits += 1;
  }
  return hits;
}

/** Every undirected edge shared by exactly two triangles ⇒ closed surface. */
function isWatertight(mesh: CutMeshMm): boolean {
  const p = mesh.positionsMm;
  const edges = new Map<string, number>();
  const key = (i: number): string => `${String(p[i])},${String(p[i + 1])},${String(p[i + 2])}`;
  for (let i = 0; i + 8 < p.length; i += 9) {
    const a = key(i);
    const b = key(i + 3);
    const c = key(i + 6);
    for (const [u, v] of [
      [a, b],
      [b, c],
      [c, a],
    ] as const) {
      const e = u < v ? `${u}|${v}` : `${v}|${u}`;
      edges.set(e, (edges.get(e) ?? 0) + 1);
    }
  }
  for (const count of edges.values()) if (count !== 2) return false;
  return edges.size > 0;
}

describe('the real Manifold cut (WASM, executed)', () => {
  let cutter: PrismCutter;
  let wallProfile: PrismProfileF;
  let doorCuts: readonly PrismProfileF[];

  beforeAll(async () => {
    __resetBooleanEngineForTests();
    const status = await ensureBooleanEngine();
    expect(status.state, JSON.stringify(status)).toBe('ready');
    const ready = getPrismCutter();
    if (ready === null) throw new Error('engine ready but no cutter');
    cutter = ready;

    const { house } = makeTwoRoomPlanWithOpenings();
    const south = storeySolids(house, GF).find((s) => s.key === `wall:${fixedId('wall', 'WS')}`);
    if (south === undefined) throw new Error('fixture south wall missing');
    wallProfile = south.profile;
    doorCuts = south.cuts;
    expect(doorCuts).toHaveLength(1);
  }, 30_000);

  afterAll(() => {
    __resetBooleanEngineForTests();
  });

  // Fixture facts (packages/model/src/testing.ts): the south wall runs
  // (0,0)→(6000,0), 230 thick; the door is 900 wide × 2100 high at offset
  // 1500 with sill 0, and GF FFL is 600 ⇒ the hole spans x 1050..1950,
  // y −115..115, z 600..2700 in model mm.
  const THROUGH_DOOR: Ray = { ox: 1500, oy: -1000, oz: 1600, dx: 0, dy: 1, dz: 0 };
  const BESIDE_DOOR: Ray = { ox: 2500, oy: -1000, oz: 1600, dx: 0, dy: 1, dz: 0 };
  const ABOVE_DOOR: Ray = { ox: 1500, oy: -1000, oz: 2900, dx: 0, dy: 1, dz: 0 };

  it('reports ready once loaded', () => {
    expect(booleanEngineStatus().state).toBe('ready');
  });

  it('a ray through the door meets nothing; beside and above it, the wall (in and out)', () => {
    const cut = cutter.cut(wallProfile, doorCuts);
    expect(cut).not.toBeNull();
    if (cut === null) return;
    expect(rayHits(cut, THROUGH_DOOR)).toBe(0);
    expect(rayHits(cut, BESIDE_DOOR)).toBe(2);
    expect(rayHits(cut, ABOVE_DOOR)).toBe(2);
  });

  it('negative control: the same ray through the UNCUT wall hits it twice', () => {
    const solid = cutter.cut(wallProfile, []);
    expect(solid).not.toBeNull();
    if (solid === null) return;
    expect(rayHits(solid, THROUGH_DOOR)).toBe(2);
  });

  it('the cut mesh is watertight and carries more triangles than the plain box', () => {
    const solid = cutter.cut(wallProfile, []);
    const cut = cutter.cut(wallProfile, doorCuts);
    if (solid === null || cut === null) throw new Error('cut failed');
    expect(solid.positionsMm.length / 9).toBe(12); // a box: 6 quads
    expect(cut.positionsMm.length / 9).toBeGreaterThan(12);
    expect(isWatertight(solid)).toBe(true);
    expect(isWatertight(cut)).toBe(true);
  });

  it('a degenerate cut is skipped rather than crashing the whole wall', () => {
    const flat: PrismProfileF = { ...doorCuts[0]!, topMm: doorCuts[0]!.baseMm };
    const cut = cutter.cut(wallProfile, [flat]);
    expect(cut).not.toBeNull();
    if (cut === null) return;
    expect(rayHits(cut, THROUGH_DOOR)).toBe(2); // nothing was cut
  });
});
