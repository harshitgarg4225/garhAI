/**
 * The CLICK TEST for the plan drawing (CLAUDE.md bug 4).
 *
 * `PlanScene` batches every element family into one merged mesh and resolves
 * a raycast back to an element id through `faceResolver`. Nothing about that
 * can be checked by reading the code: a builder that packs the right vertices
 * with the wrong ids, or a resolver that reads the wrong index, draws a
 * perfect plan that nobody can click. So this spec does what the pointer does:
 *
 *   1. build the exact buffer `PlanScene` draws, with the exact builder;
 *   2. wrap it in a `Mesh` and register it with the real `PickRegistry`,
 *      with the exact resolver `PlanScene` registers;
 *   3. frame a real `OrthographicCamera` the way `CameraRig` does;
 *   4. cast the ray `useCanvasControls` would for a click at a model point,
 *      through the one picker (`pickAt`), and assert the id that comes back.
 *
 * Every family has a NEGATIVE CONTROL: a click beside the element reports
 * `'empty'`, and the same click against an empty registry reports `'empty'`,
 * so a test that could not go red is not one of these.
 *
 * Three's `Raycaster` is pure JavaScript — no WebGL, no DOM — which is what
 * makes this runnable in vitest.
 */

import { describe, expect, it } from 'vitest';
import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  Mesh,
  MeshBasicMaterial,
  OrthographicCamera,
} from 'three';

import {
  FIXTURE_IDS,
  applyGroup,
  fixedId,
  makeTwoRoomPlan,
  makeTwoRoomPlanWithOpenings,
  type Op,
  type Pt,
  type ProjectDoc,
} from '@garh/model';

import { ORTHO_EYE_HEIGHT_MM, type PickKind } from '../../../features/canvas/core/constants';
import { mmToWorld, mmToWorldXYZ, ndcFromPixel } from '../../../features/canvas/core/coords';
import { mmToPixel, orthoFrustumWorld } from '../../../features/canvas/core/cameraMath';
import { pickAt, type PickHit } from '../../../features/canvas/core/hitTest';
import { PickRegistry } from '../../../features/canvas/core/pickRegistry';
import {
  buildBalconyBuffers,
  buildColumnBuffers,
  buildOpeningBuffers,
  buildRoomFaces,
  buildStairBuffers,
  buildWallFaces,
  faceResolver,
  packTriangles,
  type MergedFaces,
} from './planBuffers';

// ---------------------------------------------------------------------------
// The harness — a camera and a registry, exactly as the canvas core wires them
// ---------------------------------------------------------------------------

const VIEW = { centreMm: { x: 3000, y: 2000 }, mmPerPx: 5 };
const SIZE = { width: 1600, height: 1000 };
const STOREY = FIXTURE_IDS.groundStorey;
/** The south wall, named once for the split-mesh describe below. */
const SOUTH_ID = FIXTURE_IDS.wallSouth;

/** A plan camera framed like `CameraRig.sync` for the 2D view. */
function planCamera(planeElevationMm: number): OrthographicCamera {
  const frustum = orthoFrustumWorld(VIEW, SIZE);
  const camera = new OrthographicCamera(
    frustum.left,
    frustum.right,
    frustum.top,
    frustum.bottom,
    0.01,
    400,
  );
  camera.up.set(0, 0, -1);
  mmToWorldXYZ(VIEW.centreMm.x, VIEW.centreMm.y, ORTHO_EYE_HEIGHT_MM, camera.position);
  const target = mmToWorld(VIEW.centreMm, planeElevationMm);
  camera.lookAt(target.x, target.y, target.z);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return camera;
}

/** A merged layer as `PlanScene` mounts it: one mesh, registered with its resolver. */
function mountLayer(registry: PickRegistry, faces: MergedFaces, kind: PickKind): Mesh {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(faces.positions, 3));
  if (faces.positions.length > 0) geometry.computeBoundingSphere();
  // `DoubleSide`, as every material in `planMaterials.ts` is: three's raycaster
  // honours the material's side, and a wall quad winds clockwise seen from
  // above, so a `FrontSide` stand-in would cull exactly the faces the plan
  // draws double-sided. The harness must mirror the real material or it tests
  // a scene nobody mounts.
  const mesh = new Mesh(geometry, new MeshBasicMaterial({ side: DoubleSide }));
  mesh.updateMatrixWorld();
  registry.register(mesh, faceResolver(faces, kind, STOREY));
  return mesh;
}

/** The click: model point → pixel → NDC → the one picker. */
function click(registry: PickRegistry, ptMm: Pt, elevationMm = 0): PickHit {
  const camera = planCamera(elevationMm);
  const ndc = ndcFromPixel(mmToPixel(VIEW, ptMm, SIZE), SIZE);
  return pickAt({
    registry,
    camera,
    ndc,
    mode: '2d',
    planeElevationMm: elevationMm,
    mmPerPx: VIEW.mmPerPx,
    storeyId: STOREY,
  });
}

function withOps(doc: ProjectDoc, ops: readonly Op[]): ProjectDoc {
  return applyGroup(doc, ops).model;
}

// ---------------------------------------------------------------------------
// Walls
// ---------------------------------------------------------------------------

describe('walls: the merged poché resolves to the wall under the pointer', () => {
  const doc = makeTwoRoomPlan();
  const registry = new PickRegistry();
  mountLayer(registry, buildWallFaces(doc.house, STOREY, 0), 'wall');

  it('clicks the south wall on its centreline', () => {
    const hit = click(registry, { x: 1500, y: 0 });
    expect(hit.kind).toBe('wall');
    expect(hit.id).toBe(FIXTURE_IDS.wallSouth);
  });

  it('clicks the spine, not the south wall, at the spine', () => {
    const hit = click(registry, { x: 3000, y: 2000 });
    expect(hit.id).toBe(FIXTURE_IDS.wallSpine);
  });

  it('reports empty beside a wall (negative control)', () => {
    // 600 mm inside room A: nothing but paper in the wall layer.
    expect(click(registry, { x: 1500, y: 600 }).kind).toBe('empty');
  });

  it('reports empty against a registry nothing registered with (negative control)', () => {
    expect(click(new PickRegistry(), { x: 1500, y: 0 }).kind).toBe('empty');
  });

  it('carries the storey id the resolver was registered with', () => {
    expect(click(registry, { x: 1500, y: 0 }).storeyId).toBe(STOREY);
  });
});

describe('walls: a split wall is two click targets', () => {
  const doc = makeTwoRoomPlan();
  const newWallId = fixedId('wall', 'SPLIT');
  const split = withOps(doc, [
    { type: 'wall.split', payload: { wallId: FIXTURE_IDS.wallSouth, atMm: 2000, newWallId } },
  ]);
  const registry = new PickRegistry();
  mountLayer(registry, buildWallFaces(split.house, STOREY, 0), 'wall');

  it('resolves the near half to the original id', () => {
    expect(click(registry, { x: 1000, y: 0 }).id).toBe(FIXTURE_IDS.wallSouth);
  });

  it('resolves the far half to the new id', () => {
    expect(click(registry, { x: 4500, y: 0 }).id).toBe(newWallId);
  });

  it('would have been one id before the split (negative control)', () => {
    const before = new PickRegistry();
    mountLayer(before, buildWallFaces(doc.house, STOREY, 0), 'wall');
    expect(click(before, { x: 4500, y: 0 }).id).toBe(FIXTURE_IDS.wallSouth);
  });
});

// ---------------------------------------------------------------------------
// Openings — the reveal is the pick target, and it beats the wall
// ---------------------------------------------------------------------------

describe('openings: the reveal wins over its host wall', () => {
  const doc = makeTwoRoomPlanWithOpenings();
  const registry = new PickRegistry();
  mountLayer(registry, buildWallFaces(doc.house, STOREY, 0), 'wall');
  mountLayer(registry, buildOpeningBuffers(doc.house, STOREY, 0).faces, 'opening');

  it('clicks the door at its centre', () => {
    const door = doc.house.openings.find((o) => o.kind === 'door');
    expect(door).toBeDefined();
    const wall = doc.house.walls.find((w) => w.id === door?.wallId);
    expect(wall).toBeDefined();
    if (door === undefined || wall === undefined) return;
    const dx = wall.b.x - wall.a.x;
    const dy = wall.b.y - wall.a.y;
    const len = Math.hypot(dx, dy);
    const centre = {
      x: Math.round(wall.a.x + (dx / len) * door.offsetMm),
      y: Math.round(wall.a.y + (dy / len) * door.offsetMm),
    };
    const hit = click(registry, centre);
    expect(hit.kind).toBe('opening');
    expect(hit.id).toBe(door.id);
  });

  it('still clicks the wall away from the opening', () => {
    expect(click(registry, { x: 300, y: 0 }).kind).toBe('wall');
  });
});

// ---------------------------------------------------------------------------
// Rooms, stairs, balconies, columns
// ---------------------------------------------------------------------------

describe('rooms: the wash resolves to the room', () => {
  const doc = makeTwoRoomPlan();
  const registry = new PickRegistry();
  mountLayer(registry, buildRoomFaces(doc.house, STOREY, 0), 'room');

  it('clicks room A inside it and room B inside it', () => {
    const a = click(registry, { x: 1500, y: 2000 });
    const b = click(registry, { x: 4500, y: 2000 });
    expect(a.kind).toBe('room');
    expect(b.kind).toBe('room');
    expect(a.id).not.toBe(b.id);
    expect(doc.house.rooms.map((r) => r.id)).toEqual(expect.arrayContaining([a.id, b.id]));
  });

  it('reports empty outside the building (negative control)', () => {
    expect(click(registry, { x: -1000, y: 2000 }).kind).toBe('empty');
  });
});

describe('stairs: every flight of a dogleg is clickable, on the storey plane', () => {
  const stairId = fixedId('stair', 'DOG');
  const doc = withOps(makeTwoRoomPlan(), [
    {
      type: 'stair.add',
      payload: {
        id: stairId,
        storeyId: STOREY,
        kind: 'dogleg',
        origin: { x: 500, y: 500 },
        direction: 'N',
        riserMm: 167,
        treadMm: 250,
        widthMm: 1000,
        risersCount: 18,
        landing: { widthMm: 2115, depthMm: 1000 },
      },
    },
  ]);
  const ELEVATION = 3050;
  const registry = new PickRegistry();
  mountLayer(registry, buildStairBuffers(doc.house, STOREY, ELEVATION).faces, 'stair');

  it('clicks the first flight', () => {
    expect(click(registry, { x: 1000, y: 1000 }, ELEVATION).id).toBe(stairId);
  });

  it('clicks the second flight, which the old one-run symbol never drew', () => {
    // Across 1615–2615 (right of the well), along 500–2500.
    expect(click(registry, { x: 2200, y: 1500 }, ELEVATION).id).toBe(stairId);
  });

  it('clicks the landing', () => {
    expect(click(registry, { x: 1500, y: 3000 }, ELEVATION).id).toBe(stairId);
  });

  it('reports empty beyond the landing (negative control)', () => {
    expect(click(registry, { x: 1500, y: 3700 }, ELEVATION).kind).toBe('empty');
  });
});

describe('balconies and columns resolve to themselves', () => {
  const balconyId = fixedId('balcony', 'B1');
  const columnId = fixedId('column', 'C1');
  const doc = withOps(makeTwoRoomPlan(), [
    {
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: balconyId,
        storeyId: STOREY,
        polygon: [
          { x: 0, y: 4115 },
          { x: 2000, y: 4115 },
          { x: 2000, y: 5115 },
          { x: 0, y: 5115 },
        ],
        railingKind: 'ms',
        railingHeightMm: 1000,
        projectionMm: 1000,
        slabThicknessMm: 125,
      },
    },
    {
      type: 'column.set',
      payload: { action: 'add', id: columnId, storeyId: STOREY, pt: { x: 3000, y: 4000 } },
    },
  ]);
  const registry = new PickRegistry();
  mountLayer(registry, buildBalconyBuffers(doc.house, STOREY, 0).faces, 'balcony');
  mountLayer(registry, buildColumnBuffers(doc.house, STOREY, 0).faces, 'column');

  it('clicks the balcony slab', () => {
    const hit = click(registry, { x: 1000, y: 4600 });
    expect(hit.kind).toBe('balcony');
    expect(hit.id).toBe(balconyId);
  });

  it('clicks the column at its centre and 80 mm off it (inside a 230 square)', () => {
    expect(click(registry, { x: 3000, y: 4000 }).id).toBe(columnId);
    expect(click(registry, { x: 3080, y: 4080 }).id).toBe(columnId);
  });

  it('reports empty 200 mm from the column (negative control)', () => {
    expect(click(registry, { x: 3200, y: 4200 }).kind).toBe('empty');
  });
});

// ---------------------------------------------------------------------------
// The packing itself
// ---------------------------------------------------------------------------

describe('packTriangles', () => {
  it('writes one id per triangle, in order, and north to −Z', () => {
    const faces = packTriangles(
      [
        { id: 'a', tris: [0, 0, 1000, 0, 1000, 1000] },
        { id: 'b', tris: [0, 0, 1000, 1000, 0, 1000, 5, 5, 6, 5, 6, 6] },
      ],
      2700,
    );
    expect(faces.faceIds).toEqual(['a', 'b', 'b']);
    expect(faces.positions.length).toBe(9 * 3);
    // Vertex 1 of triangle 0: (1000, 0) mm at 2700 mm → (1, 2.7, 0) world.
    expect(faces.positions[3]).toBeCloseTo(1, 5);
    expect(faces.positions[4]).toBeCloseTo(2.7, 5);
    // Vertex 2: (1000, 1000) → z = −1.
    expect(faces.positions[8]).toBeCloseTo(-1, 5);
  });
});

// ---------------------------------------------------------------------------
// Hatched walls are a SECOND merged mesh — and must stay clickable (bug 4)
// ---------------------------------------------------------------------------

describe('walls split across two meshes (hatched and flat) stay clickable', () => {
  const doc = makeTwoRoomPlan();
  // Exactly what `PlanScene` does when a binding exists: the bound walls are
  // built into their own buffer and the rest into another. A resolver built
  // from the wrong faces array would resolve a click to the wrong wall, and
  // nothing about that is visible to the compiler.
  const hatchedIds = new Set([SOUTH_ID]);
  const flatHouse = { ...doc.house, walls: doc.house.walls.filter((w) => !hatchedIds.has(w.id)) };
  const patternedHouse = {
    ...doc.house,
    walls: doc.house.walls.filter((w) => hatchedIds.has(w.id)),
  };

  const registry = new PickRegistry();
  mountLayer(registry, buildWallFaces(flatHouse, STOREY, 0), 'wall');
  mountLayer(registry, buildWallFaces(patternedHouse, STOREY, 0), 'wall');

  it('clicks the hatched wall and gets the hatched wall', () => {
    const hit = click(registry, { x: 1500, y: 0 });
    expect(hit.kind).toBe('wall');
    expect(hit.id).toBe(SOUTH_ID);
  });

  it('clicks a flat wall and gets that one, not the hatched one', () => {
    const hit = click(registry, { x: 3000, y: 2000 });
    expect(hit.id).toBe(FIXTURE_IDS.wallSpine);
  });

  it('still reports empty off every wall (negative control)', () => {
    expect(click(registry, { x: 1500, y: 600 }).kind).toBe('empty');
  });
});
