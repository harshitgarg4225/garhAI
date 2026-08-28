/**
 * THE "THE STOREY BELOW IS NOT CLICKABLE" GATE, and the buffers behind it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS IS MEASURED AND NOT ASSERTED
 * ════════════════════════════════════════════════════════════════════════════
 * The furniture layer once tagged its meshes for hit-testing, documented itself
 * as integrated, and never called `PickRegistry` — so every placed item was
 * invisible to clicks, with no compile-time signal. `StoreyGhostLayer` is the
 * mirror image of that bug: it is a full set of WALLS drawn under the walls
 * being edited, and if it were ever registered, a click meant for the first
 * floor would sometimes select a ground-floor wall.
 *
 * "It does not call the registry" is not a thing a unit test can assert by
 * looking at state, so this file does three things in order, and the middle one
 * is what makes the other two mean something:
 *
 *   1. the ghost genuinely has geometry under the test point (measured, not
 *      assumed — an empty buffer would make an "empty pick" pass for the wrong
 *      reason);
 *   2. registering that same buffer — the mistake — makes the very same
 *      raycast return a hit. The assertion CAN go the other way;
 *   3. as the product actually builds it, unregistered, the same raycast comes
 *      back `kind: 'empty'` while a real wall at another point still picks.
 *
 * Everything is real: a real `Mesh` from the layer's real builder, a real
 * `PickRegistry`, a real `OrthographicCamera` in the 2D rig's own convention,
 * and `pickAt` — the same function every click, hover and marquee goes through.
 * `features/layers/pickGate.test.ts` set this pattern; this follows it.
 *
 * The fourth gate is the source scan at the bottom: no file in this feature may
 * CALL `usePickable*` at all. That is the compile-time signal the furniture bug
 * did not have. It scans code with the comments stripped, because the comments
 * are where this feature explains at length what it refuses to call — a gate
 * that cannot tell an argument from a call would force those comments out, and
 * they are worth more than the two lines of regex.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';
import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  Mesh,
  MeshBasicMaterial,
  OrthographicCamera,
  Vector3,
} from 'three';

import {
  applyGroup,
  DEFAULTS,
  FIXTURE_IDS,
  emptyProjectDoc,
  makeTwoRoomPlan,
  twoRoomPlanOps,
  type HouseModel,
  type Op,
} from '@garh/model';

import { WORLD_UNITS_PER_MM } from '../canvas/core';
import { pickAt } from '../canvas/core/hitTest';
import { PickRegistry } from '../canvas/core/pickRegistry';
import { buildStoreyGhost, storeyBelow, type StoreyGhostGeometry } from './ghostGeometry';

const GF = FIXTURE_IDS.groundStorey;
const FF = FIXTURE_IDS.firstStorey;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A G+1: the two-room plan downstairs, one lone wall 8 m north upstairs. */
function makeG1(): HouseModel {
  const ops: Op[] = [
    ...twoRoomPlanOps(),
    {
      type: 'storey.add',
      payload: { id: FF, index: 1, name: 'First Floor', heightMm: DEFAULTS.storeyHeightMm },
    },
    {
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallNorth.replace('WN', 'F1'),
        storeyId: FF,
        a: { x: 0, y: 8000 },
        b: { x: 6000, y: 8000 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
  ];
  return applyGroup(emptyProjectDoc(), ops).model.house;
}

/** The 2D rig: orthographic, straight down, `up` along −Z (see `coords.ts`). */
function planCamera(): OrthographicCamera {
  const camera = new OrthographicCamera(-10, 10, 10, -10, 0.01, 400);
  camera.position.set(0, 100, 0);
  camera.up.set(0, 0, -1);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

/** Model mm → the NDC the picker takes, through the camera's own projection. */
function ndcAtMm(camera: OrthographicCamera, xMm: number, yMm: number): { x: number; y: number } {
  const world = new Vector3(xMm * WORLD_UNITS_PER_MM, 0, -yMm * WORLD_UNITS_PER_MM);
  const projected = world.project(camera);
  return { x: projected.x, y: projected.y };
}

/** A `Mesh` over the ghost's fill buffer — exactly what the layer renders. */
function meshOf(ghost: StoreyGhostGeometry): Mesh {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(ghost.fillPositions, 3));
  geometry.computeBoundingSphere();
  // DoubleSide, as the layer's own fill material is: a wall quad's winding
  // follows the direction it was drawn in.
  const mesh = new Mesh(geometry, new MeshBasicMaterial({ side: DoubleSide }));
  mesh.updateMatrixWorld(true);
  return mesh;
}

// ---------------------------------------------------------------------------
// Which storey is the ghost
// ---------------------------------------------------------------------------

describe('storeyBelow', () => {
  const house = makeG1();

  it('is the storey under the active one', () => {
    expect(storeyBelow(house, FF)?.id).toBe(GF);
  });

  it('is null on the ground floor — there is nothing below it', () => {
    expect(storeyBelow(house, GF)).toBeNull();
  });

  it('is null for no storey and for a storey that is not in the document', () => {
    expect(storeyBelow(house, null)).toBeNull();
    expect(storeyBelow(house, 'storey_01J0000000000000000000ZZ')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The buffers
// ---------------------------------------------------------------------------

describe('buildStoreyGhost', () => {
  it('draws nothing for an empty or absent storey', () => {
    const house = makeG1();
    expect(buildStoreyGhost(house, null, 0).triangleCount).toBe(0);
    expect(buildStoreyGhost(house, 'storey_01J0000000000000000000ZZ', 0).segmentCount).toBe(0);
  });

  it('puts every vertex on the elevation it was asked for', () => {
    const ghost = buildStoreyGhost(makeG1(), GF, 3600);
    const expected = 3600 * WORLD_UNITS_PER_MM;
    expect(ghost.triangleCount).toBeGreaterThan(0);
    // Six places, not nine: these are Float32Arrays, and 3.6 m is 3.5999999
    // once stored. That is 0.1 micrometre of drift on a plan drawn in
    // millimetres — the reason the MODEL is integer mm and only the render
    // buffers are float.
    for (let i = 1; i < ghost.fillPositions.length; i += 3) {
      expect(ghost.fillPositions[i]).toBeCloseTo(expected, 6);
    }
    for (let i = 1; i < ghost.linePositions.length; i += 3) {
      expect(ghost.linePositions[i]).toBeCloseTo(expected, 6);
    }
  });

  it('maps model mm to world exactly as `coords.ts` does (y becomes −z)', () => {
    const ghost = buildStoreyGhost(makeTwoRoomPlan().house, GF, 0);
    // The two-room plan spans x 0…6000, y 0…4000, walls 230 thick — so the
    // extremes are ±115 mm outside that box, and NOTHING may be at +z.
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < ghost.fillPositions.length; i += 3) {
      minX = Math.min(minX, ghost.fillPositions[i] as number);
      maxX = Math.max(maxX, ghost.fillPositions[i] as number);
      minZ = Math.min(minZ, ghost.fillPositions[i + 2] as number);
      maxZ = Math.max(maxZ, ghost.fillPositions[i + 2] as number);
    }
    expect(minX).toBeCloseTo(-115 * WORLD_UNITS_PER_MM, 6);
    expect(maxX).toBeCloseTo(6115 * WORLD_UNITS_PER_MM, 6);
    expect(maxZ).toBeCloseTo(115 * WORLD_UNITS_PER_MM, 6);
    expect(minZ).toBeCloseTo(-4115 * WORLD_UNITS_PER_MM, 6);
  });

  it('cuts the openings out of the poché, exactly as the plan does', () => {
    const plain = makeTwoRoomPlan();
    const withDoor = applyGroup(plain, [
      {
        type: 'opening.add',
        payload: {
          id: FIXTURE_IDS.doorMain,
          wallId: FIXTURE_IDS.wallSouth,
          kind: 'door',
          widthMm: DEFAULTS.doorWidthMm,
          heightMm: DEFAULTS.doorHeightMm,
          sillMm: 0,
          offsetMm: 1500,
          swing: 'in-left',
        },
      },
    ]).model;

    const before = buildStoreyGhost(plain.house, GF, 0);
    const after = buildStoreyGhost(withDoor.house, GF, 0);

    // One wall becomes two solid runs: one extra quad, so two extra triangles.
    // (A ghost that ignored openings would be equal here — which is the whole
    // point of measuring it rather than trusting the shared helper.)
    expect(after.triangleCount).toBe(before.triangleCount + 2);
    // …and the door's jambs, leaf and swing arc are new linework.
    expect(after.segmentCount).toBeGreaterThan(before.segmentCount);
  });
});

// ---------------------------------------------------------------------------
// THE PICK GATE
// ---------------------------------------------------------------------------

describe('the ghost is not a pick target', () => {
  const house = makeG1();
  const ghost = buildStoreyGhost(house, GF, 0);
  const camera = planCamera();

  /** Dead centre of the ground floor's south wall. Nothing upstairs is here. */
  const OVER_GHOST = { xMm: 3000, yMm: 0 };
  /** Dead centre of the first floor's only wall. */
  const OVER_REAL = { xMm: 3000, yMm: 8000 };

  const FIRST_FLOOR_WALL = FIXTURE_IDS.wallNorth.replace('WN', 'F1');

  /**
   * The scene as `PlanPage` composes it: the ACTIVE storey's plan registered
   * (that is what `PlanScene.MergedLayer` does), the ghost merely drawn.
   * `registerGhost` is the mistake this gate exists to catch.
   */
  function harness(registerGhost: boolean): PickRegistry {
    const registry = new PickRegistry();
    const active = buildStoreyGhost(house, FF, 0);
    registry.register(meshOf(active), {
      kind: 'wall',
      id: FIRST_FLOOR_WALL,
      storeyId: FF,
    });
    if (registerGhost) {
      registry.register(meshOf(ghost), { kind: 'wall', id: FIXTURE_IDS.wallSouth, storeyId: GF });
    }
    return registry;
  }

  function pick(
    registry: PickRegistry,
    at: { xMm: number; yMm: number },
  ): ReturnType<typeof pickAt> {
    return pickAt({
      registry,
      camera,
      ndc: ndcAtMm(camera, at.xMm, at.yMm),
      mode: '2d',
      planeElevationMm: 0,
      mmPerPx: 10,
    });
  }

  it('has real geometry under the test point (a gate over nothing is not a gate)', () => {
    expect(ghost.triangleCount).toBeGreaterThan(0);
    // Registering the ghost — the bug — makes the very same raycast land on it.
    // If this ever stops hitting, the "empty" assertion below is worthless.
    const hit = pick(harness(true), OVER_GHOST);
    expect(hit.kind).toBe('wall');
    expect(hit.id).toBe(FIXTURE_IDS.wallSouth);
  });

  it('answers empty over the storey below, as the layer actually builds it', () => {
    const hit = pick(harness(false), OVER_GHOST);
    expect(hit.kind).toBe('empty');
    expect(hit.id).toBeNull();
    // An empty pick still reports where the ray crossed the plan — that is what
    // makes "click on nothing" mean "deselect here", not "nothing happened".
    expect(hit.pointMm).not.toBeNull();
  });

  it('still picks the storey being edited at the same zoom', () => {
    const hit = pick(harness(false), OVER_REAL);
    expect(hit.kind).toBe('wall');
    expect(hit.id).toBe(FIRST_FLOOR_WALL);
    expect(hit.storeyId).toBe(FF);
  });
});

// ---------------------------------------------------------------------------
// Source gate — the compile-time signal the furniture bug did not have
// ---------------------------------------------------------------------------

/**
 * Comments out, code in. Not a parser: the files in this directory contain no
 * string literal with `//` in it, so this is exact for what it is used on — and
 * a mistake here makes the gate LOUDER (it scans less prose), never quieter
 * about a real call, because a call is never inside a comment.
 */
function codeOf(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('no file in this feature may reach for the picker (source gate)', () => {
  // `dirname(fileURLToPath(import.meta.url))`, NOT `new URL('.', import.meta.url)`:
  // Vite statically rewrites the latter idiom and hands the spec a URL that is
  // not a file: URL, as the other source-reading specs in this app note.
  const dir = dirname(fileURLToPath(import.meta.url));
  const sources = readdirSync(dir).filter(
    (f) => (f.endsWith('.ts') || f.endsWith('.tsx')) && !f.endsWith('.test.ts'),
  );

  it('scans a non-empty set of files', () => {
    expect(sources.length).toBeGreaterThan(4);
  });

  it.each([
    ['usePickable', 'the ghost must never become a pick candidate — see StoreyGhostLayer'],
    ['onPointerDown', 'no react-three-fiber pointer handlers (§12: one hit-testing system)'],
    ['<Canvas', 'there is ONE canvas, and this feature mounts into it'],
  ])('no source file contains %s (%s)', (needle) => {
    const offenders = sources.filter((f) =>
      codeOf(readFileSync(join(dir, f), 'utf8')).includes(needle),
    );
    expect(offenders).toEqual([]);
  });
});
