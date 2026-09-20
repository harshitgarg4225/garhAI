/**
 * gltfExport.test.ts — the GLB round trip, executed: build the building +
 * facade headlessly (the live scene's names and structure), export it with
 * the real `GLTFExporter`, parse the bytes back with the real `GLTFLoader`,
 * and count what came out. With the real WASM engine the exported walls are
 * the CUT walls — the same ray-through-the-door check as booleans.test, on
 * the parsed file this time.
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { Group, Mesh, type Material, type Object3D } from 'three';
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';

import { applyGroup, fixedId, makeTwoRoomPlanWithOpenings, type ProjectDoc } from '@garh/model';

import { CONTEMPORARY_KIT } from '../facade/kits';
import { applyKitOp } from '../facade/ops';
import {
  __resetBooleanEngineForTests,
  ensureBooleanEngine,
  getPrismCutter,
  type PrismCutter,
} from './booleans';
import {
  countExportableMeshes,
  exportGlb,
  exportRootsOf,
  FACADE_GROUP_NAME,
  glbFileName,
  isExportRoot,
} from './gltfExport';
import { buildHouseObject } from './headlessScene';

const GLB_MAGIC = 0x46546c67; // 'glTF'

function dressedDoc(): ProjectDoc {
  const doc = makeTwoRoomPlanWithOpenings();
  return applyGroup(doc, [applyKitOp(doc.house, CONTEMPORARY_KIT, 7, null)]).model;
}

function parseGlb(bytes: ArrayBuffer): Promise<GLTF> {
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(bytes, '', resolve, (error: unknown) => {
      reject(error instanceof Error ? error : new Error(String(error)));
    });
  });
}

function meshesOf(object: Object3D): Mesh[] {
  const out: Mesh[] = [];
  object.traverse((o) => {
    if (o instanceof Mesh) out.push(o);
  });
  return out;
}

function materialsOf(meshes: readonly Mesh[]): Set<Material> {
  const out = new Set<Material>();
  for (const mesh of meshes) {
    const material = mesh.material;
    if (Array.isArray(material)) material.forEach((m) => out.add(m));
    else out.add(material);
  }
  return out;
}

/**
 * Möller–Trumbore hit count for one mesh's triangles, within `maxT` of the
 * origin — the wall bucket merges every external wall, so an unbounded ray
 * through the south door would go on to hit the north wall four metres on.
 */
function rayHits(
  mesh: Mesh,
  o: readonly [number, number, number],
  d: readonly [number, number, number],
  maxT = Infinity,
): number {
  const position = mesh.geometry.getAttribute('position');
  const index = mesh.geometry.getIndex();
  const triCount = index === null ? position.count / 3 : index.count / 3;
  const vertex = (k: number): [number, number, number] => {
    const i = index === null ? k : index.getX(k);
    return [position.getX(i), position.getY(i), position.getZ(i)];
  };
  let hits = 0;
  for (let t = 0; t < triCount; t += 1) {
    const [ax, ay, az] = vertex(t * 3);
    const [bx, by, bz] = vertex(t * 3 + 1);
    const [cx, cy, cz] = vertex(t * 3 + 2);
    const e1 = [bx - ax, by - ay, bz - az];
    const e2 = [cx - ax, cy - ay, cz - az];
    const h = [
      d[1] * e2[2]! - d[2] * e2[1]!,
      d[2] * e2[0]! - d[0] * e2[2]!,
      d[0] * e2[1]! - d[1] * e2[0]!,
    ];
    const det = e1[0]! * h[0]! + e1[1]! * h[1]! + e1[2]! * h[2]!;
    if (Math.abs(det) < 1e-12) continue;
    const inv = 1 / det;
    const s = [o[0] - ax, o[1] - ay, o[2] - az];
    const u = inv * (s[0]! * h[0]! + s[1]! * h[1]! + s[2]! * h[2]!);
    if (u < 0 || u > 1) continue;
    const q = [
      s[1]! * e1[2]! - s[2]! * e1[1]!,
      s[2]! * e1[0]! - s[0]! * e1[2]!,
      s[0]! * e1[1]! - s[1]! * e1[0]!,
    ];
    const v = inv * (d[0] * q[0]! + d[1] * q[1]! + d[2] * q[2]!);
    if (v < 0 || u + v > 1) continue;
    const tHit = inv * (e2[0]! * q[0]! + e2[1]! * q[1]! + e2[2]! * q[2]!);
    if (tHit > 1e-9 && tHit <= maxT) hits += 1;
  }
  return hits;
}

describe('export roots', () => {
  it('takes the building groups and the facade group, nothing else by name', () => {
    expect(isExportRoot(named('three-d:storey:x'))).toBe(true);
    expect(isExportRoot(named('three-d:roof'))).toBe(true);
    expect(isExportRoot(named(FACADE_GROUP_NAME))).toBe(true);
    expect(isExportRoot(named('three-d'))).toBe(false); // the parent, not a root
    expect(isExportRoot(named('grid'))).toBe(false);
    expect(isExportRoot(named(''))).toBe(false);
  });

  it('finds every root under the headless scene: one per rebuild group plus the facade', () => {
    const { house } = dressedDoc();
    const root = buildHouseObject(house);
    const roots = exportRootsOf(root);
    // GF + roof + facade.
    expect(roots.map((r) => r.name).sort()).toEqual(
      ['garh-facade', `three-d:roof`, `three-d:storey:${fixedId('storey', 'GF')}`].sort(),
    );
  });
});

describe('the GLB round trip (no engine: honest fallback geometry)', () => {
  let bytes: ArrayBuffer;
  let sourceMeshes = 0;
  let sourceMaterials = 0;

  beforeAll(async () => {
    const { house } = dressedDoc();
    const root = buildHouseObject(house, { cutter: null });
    const roots = exportRootsOf(root);
    sourceMeshes = countExportableMeshes(roots);
    sourceMaterials = materialsOf(meshesOf(root)).size;
    bytes = await exportGlb(roots);
  }, 30_000);

  it('is a binary glTF the real loader accepts', async () => {
    const view = new DataView(bytes);
    expect(view.getUint32(0, true)).toBe(GLB_MAGIC);
    expect(view.getUint32(4, true)).toBe(2);
    expect(view.getUint32(8, true)).toBe(bytes.byteLength);
    const gltf = await parseGlb(bytes);
    expect(gltf.scene).toBeDefined();
  });

  it('carries every mesh and every distinct material the scene had', async () => {
    const gltf = await parseGlb(bytes);
    const meshes = meshesOf(gltf.scene);
    expect(sourceMeshes).toBeGreaterThan(5); // walls, panels, slab, plinth, roof, facade…
    expect(meshes).toHaveLength(sourceMeshes);
    expect(materialsOf(meshes).size).toBe(sourceMaterials);
    // The facade came along, as its own meshes with vertex colours.
    const facadeMeshes = meshes.filter((m) => m.name.startsWith('facade_'));
    expect(facadeMeshes.length).toBeGreaterThan(0);
    expect(facadeMeshes[0]!.geometry.getAttribute('color')).toBeDefined();
  });

  it('honours visibility: a hidden storey group is left out (the storey filter)', async () => {
    const { house } = dressedDoc();
    const root = buildHouseObject(house, { cutter: null });
    const roots = exportRootsOf(root);
    const storey = roots.find((r) => r.name.startsWith('three-d:storey:'));
    if (storey === undefined) throw new Error('no storey root');
    storey.visible = false;
    const gltf = await parseGlb(await exportGlb(roots));
    const names = meshesOf(gltf.scene).map((m) => m.name);
    expect(names.some((n) => n.startsWith('external_wall'))).toBe(false); // walls live in the storey
    expect(names.some((n) => n.startsWith('facade_'))).toBe(true); // facade still there
  });
});

describe('the GLB round trip with the REAL engine: the exported walls have their holes', () => {
  let cutter: PrismCutter;

  beforeAll(async () => {
    __resetBooleanEngineForTests();
    const status = await ensureBooleanEngine();
    expect(status.state).toBe('ready');
    const ready = getPrismCutter();
    if (ready === null) throw new Error('no cutter');
    cutter = ready;
  }, 30_000);

  afterAll(() => {
    __resetBooleanEngineForTests();
  });

  it('a ray through the door in the PARSED file meets no wall; beside it, the wall', async () => {
    const { house } = dressedDoc();
    const root = buildHouseObject(house, { cutter, facade: false });
    const gltf = await parseGlb(await exportGlb(exportRootsOf(root)));
    const walls = meshesOf(gltf.scene).filter((m) => m.name.startsWith('external_wall'));
    expect(walls.length).toBeGreaterThan(0);
    // World = metres, Y-up, north = −Z. The south wall stands on model y = 0
    // (world z = 0, ±0.115 thick); its door spans x 1050..1950 mm, z 600..2700
    // mm ⇒ world x 1.05..1.95, y 0.6..2.7. Cast from 1 m south of the wall
    // toward −Z, and stop 2 m on so the north wall (4 m away) is not counted.
    const through = walls.reduce((n, m) => n + rayHits(m, [1.5, 1.6, 1], [0, 0, -1], 2), 0);
    const beside = walls.reduce((n, m) => n + rayHits(m, [2.5, 1.6, 1], [0, 0, -1], 2), 0);
    expect(through).toBe(0);
    expect(beside).toBe(2);
  });
});

describe('glbFileName', () => {
  it('slugs the project name and never returns an empty stem', () => {
    expect(glbFileName('Studio Demo House')).toBe('studio-demo-house.glb');
    expect(glbFileName('  Mr. Rao — 30×40 ')).toBe('mr-rao-30-40.glb');
    expect(glbFileName('')).toBe('garh-model.glb');
    expect(glbFileName('###')).toBe('garh-model.glb');
  });
});

function named(name: string): Object3D {
  const group = new Group();
  group.name = name;
  return group;
}
