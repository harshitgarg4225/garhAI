/**
 * ════════════════════════════════════════════════════════════════════════════
 * THE CLICK TEST — bug pattern 4, cornered
 * ════════════════════════════════════════════════════════════════════════════
 * The furniture layer tagged its meshes for hit-testing, documented itself as
 * integrated, never called `PickRegistry`, and shipped: every placed item was
 * invisible to clicks with no compile-time signal. No unit test caught it
 * because nothing anyone wrote ever fired a ray.
 *
 * So this spec fires real rays. It builds a real `MeasureScene`, registers it
 * in a real `PickRegistry`, points a real `OrthographicCamera` at the plan the
 * way `CameraRig` does, and calls the product's own `pickAt` — the single
 * picker every click in the app goes through. If the registration is removed,
 * these tests go red; that is asserted here too, by leaving one scene detached
 * and checking the same click finds nothing.
 *
 * The three failure modes it covers, all of which have real precedent:
 *   · never registered            → `detach`ed scene finds nothing (control)
 *   · registered, wrong ranking   → the kind gate, below
 *   · registered, capacity capped → "the last three are not clickable"
 */

import { describe, expect, it } from 'vitest';
import { LineBasicMaterial, MeshBasicMaterial, OrthographicCamera, Vector3 } from 'three';

import type { Pt } from '@garh/model';

import {
  ORTHO_EYE_HEIGHT_MM,
  ORTHO_FAR,
  ORTHO_NEAR,
  PICK_KINDS,
  WORLD_UNITS_PER_MM,
} from '../canvas/core/constants';
import { mmToWorld } from '../canvas/core/coords';
import { pickAt, pickPriority } from '../canvas/core/hitTest';
import { PickRegistry } from '../canvas/core/pickRegistry';
import { MeasureScene, MEASURE_PICK_KIND, measurementSegments } from './scene';
import { midpointMm } from './geometry';
import type { MeasureSceneInput } from './scene';
import { isMeasureId, type Measurement } from './types';

// ---------------------------------------------------------------------------
// A camera over the plan, built the way CameraRig builds the 2D one
// ---------------------------------------------------------------------------

/** 20 m of plan across a 1000 px canvas — 20 mm/px, a normal working zoom. */
const VIEW_HALF_WIDTH_MM = 10_000;
const MM_PER_PX = 20;

function planCamera(): OrthographicCamera {
  const halfW = VIEW_HALF_WIDTH_MM * WORLD_UNITS_PER_MM;
  const camera = new OrthographicCamera(-halfW, halfW, halfW, -halfW, ORTHO_NEAR, ORTHO_FAR);
  // Centred on (5000, 5000) mm so nothing under test sits at the origin — an
  // off-centre view is what catches a sign error in the north flip.
  const centre = mmToWorld({ x: 5000, y: 5000 }, 0);
  camera.position.set(centre.x, ORTHO_EYE_HEIGHT_MM * WORLD_UNITS_PER_MM, centre.z);
  // North is −Z, so the top view's up vector is −Z (see `core/coords.ts`).
  camera.up.set(0, 0, -1);
  camera.lookAt(centre.x, 0, centre.z);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

/** Normalised device coordinates of a plan point, through the same camera. */
function ndcOf(camera: OrthographicCamera, ptMm: Pt): { x: number; y: number } {
  const world = mmToWorld(ptMm, 0);
  const v = new Vector3(world.x, world.y, world.z).project(camera);
  return { x: v.x, y: v.y };
}

function click(registry: PickRegistry, camera: OrthographicCamera, atMm: Pt) {
  return pickAt({
    registry,
    camera,
    ndc: ndcOf(camera, atMm),
    mode: '2d',
    planeElevationMm: 0,
    mmPerPx: MM_PER_PX,
  });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/**
 * Throwaway materials, so a spec never touches the shared overlay set (which
 * would drag react-three-fiber, and a second copy of three, into a test whose
 * whole point is that this class needs neither).
 *
 * `pickProxy` mirrors the real one's shape: fully transparent with `colorWrite`
 * off, and NOT `visible: false` — an invisible object is out of `Raycaster`'s
 * reach entirely, which is the difference between "you cannot see it" and "you
 * cannot click it".
 */
function testMaterials() {
  return {
    ink: new LineBasicMaterial(),
    active: new LineBasicMaterial(),
    pickProxy: new MeshBasicMaterial({ transparent: true, opacity: 0, colorWrite: false }),
  };
}

function distance(id: string, a: Pt, b: Pt): Measurement {
  return { id, kind: 'distance', points: [a, b], storeyId: 'storey_GF', createdAt: 0 };
}

function input(overrides: Partial<MeasureSceneInput> = {}): MeasureSceneInput {
  return {
    measurements: [],
    draft: null,
    mmPerPx: MM_PER_PX,
    elevationMm: 0,
    selectedId: null,
    visible: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// The gate on the kind (bug pattern 2 in a different hat)
// ---------------------------------------------------------------------------

describe('the pick kind', () => {
  it('is a MEMBER of the core’s closed kind union', () => {
    // `pickPriority` returns 0 for an unknown kind, and in the 2D view priority
    // decides outright — so a kind outside this list would make every
    // measurement lose its tie with the room fill and be silently unclickable.
    expect(PICK_KINDS).toContain(MEASURE_PICK_KIND);
  });

  it('outranks the room fill and the wall it is drawn over', () => {
    expect(pickPriority(MEASURE_PICK_KIND)).toBeGreaterThan(pickPriority('room'));
    expect(pickPriority(MEASURE_PICK_KIND)).toBeGreaterThan(pickPriority('wall'));
  });
});

// ---------------------------------------------------------------------------
// The click
// ---------------------------------------------------------------------------

describe('a measurement is clickable', () => {
  it('resolves a click on the line to the measurement’s id', () => {
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);

    const m = distance('measure:1', { x: 2000, y: 2000 }, { x: 8000, y: 2000 });
    scene.update(input({ measurements: [m] }));

    const hit = click(registry, camera, midpointMm(m.points[0]!, m.points[1]!));
    expect(hit.kind).toBe(MEASURE_PICK_KIND);
    expect(hit.id).toBe('measure:1');

    scene.dispose();
  });

  it('finds NOTHING when the scene was never attached — the control', () => {
    // Same scene, same camera, same click. The only difference is the one line
    // the furniture layer forgot. If this ever passes with an id, the test
    // above has stopped proving anything.
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());

    const m = distance('measure:1', { x: 2000, y: 2000 }, { x: 8000, y: 2000 });
    scene.update(input({ measurements: [m] }));

    expect(registry.size).toBe(0);
    const hit = click(registry, camera, { x: 5000, y: 2000 });
    expect(hit.kind).toBe('empty');
    expect(hit.id).toBeNull();

    scene.dispose();
  });

  it('stops being clickable after detach', () => {
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    const detach = scene.attach(registry);
    scene.update(
      input({ measurements: [distance('measure:1', { x: 2000, y: 2000 }, { x: 8000, y: 2000 })] }),
    );
    expect(click(registry, camera, { x: 5000, y: 2000 }).id).toBe('measure:1');

    detach();
    expect(registry.size).toBe(0);
    expect(click(registry, camera, { x: 5000, y: 2000 }).kind).toBe('empty');
    scene.dispose();
  });

  it('does not answer a click that is nowhere near it', () => {
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);
    scene.update(
      input({ measurements: [distance('measure:1', { x: 2000, y: 2000 }, { x: 8000, y: 2000 })] }),
    );

    // 2 m north of the line. The click target is 14 px ≈ 280 mm wide here.
    expect(click(registry, camera, { x: 5000, y: 4000 }).kind).toBe('empty');
    scene.dispose();
  });

  it('is clickable along a DIAGONAL, not just an axis-aligned line', () => {
    // The quad is rotated about world +Y by the plan bearing; a sign error there
    // leaves diagonals unclickable while every horizontal measurement works.
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);
    const m = distance('measure:diag', { x: 2000, y: 2000 }, { x: 8000, y: 8000 });
    scene.update(input({ measurements: [m] }));

    expect(click(registry, camera, { x: 5000, y: 5000 }).id).toBe('measure:diag');
    // …and NOT clickable at the mirror point, which is where a flipped bearing
    // would have put the quad.
    expect(click(registry, camera, { x: 5000, y: 3000 }).kind).toBe('empty');
    scene.dispose();
  });
});

describe('every drawn segment is clickable, including the ones only area has', () => {
  it('registers the implied closing edge of a region', () => {
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);

    const ring: Measurement = {
      id: 'measure:area',
      kind: 'area',
      points: [
        { x: 2000, y: 2000 },
        { x: 8000, y: 2000 },
        { x: 8000, y: 8000 },
        { x: 2000, y: 8000 },
      ],
      storeyId: null,
      createdAt: 0,
    };
    scene.update(input({ measurements: [ring] }));

    // Four points, four edges — the fourth is the one `types.ts` says is
    // implied and never stored.
    expect(measurementSegments('area', ring.points)).toHaveLength(4);
    expect(scene.pickCount).toBe(4);
    // The west edge is the implied closing one: click its middle.
    expect(click(registry, camera, { x: 2000, y: 5000 }).id).toBe('measure:area');
    scene.dispose();
  });

  it('leaves the in-progress draft unpickable', () => {
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);
    scene.update(
      input({
        draft: {
          kind: 'distance',
          points: [{ x: 2000, y: 2000 }],
          cursor: { x: 8000, y: 2000 },
          willClose: false,
        },
      }),
    );
    // A rubber band that could steal its own click would make the next point
    // unplaceable.
    expect(scene.pickCount).toBe(0);
    expect(click(registry, camera, { x: 5000, y: 2000 }).kind).toBe('empty');
    scene.dispose();
  });
});

describe('the id namespace the borrowed kind depends on', () => {
  it('registers only `measure:` ids, which no dimension handle can collide with', () => {
    // `MEASURE_PICK_KIND` borrows `'dimension'`, so a measure pick and a
    // dimension-string pick arrive at PlanPage looking alike. What keeps them
    // apart is the id: dimension handles are built as `dim:<side>:<kind>|…`
    // (see `overlays/dimensions/chain.ts`) and measurements as `measure:…`.
    // If that ever stopped being true, a click on a measurement would open a
    // dimension edit field on some unrelated wall.
    const registry = new PickRegistry();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);
    scene.update(
      input({
        measurements: [
          distance('measure:1', { x: 2000, y: 2000 }, { x: 8000, y: 2000 }),
          distance('measure:2', { x: 2000, y: 4000 }, { x: 8000, y: 4000 }),
        ],
      }),
    );

    expect(scene.pickIds.length).toBeGreaterThan(0);
    for (const id of scene.pickIds) expect(isMeasureId(id)).toBe(true);
    expect(isMeasureId('dim:south:wall|0|3000')).toBe(false);
    scene.dispose();
  });
});

describe('capacity', () => {
  it('grows past its initial instance count, and the LAST one is still clickable', () => {
    // The failure this guards is precise: `InstancedMesh` cannot grow, so a
    // layer that ignores the limit silently clamps and the last measurements
    // stop answering clicks. 40 > the 32 slots the scene starts with.
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);

    const many: Measurement[] = [];
    for (let i = 0; i < 40; i++) {
      const y = 1000 + i * 200;
      many.push(distance(`measure:${String(i)}`, { x: 2000, y }, { x: 8000, y }));
    }
    scene.update(input({ measurements: many }));

    expect(scene.pickCount).toBe(40);
    expect(click(registry, camera, { x: 5000, y: 1000 }).id).toBe('measure:0');
    expect(click(registry, camera, { x: 5000, y: 1000 + 39 * 200 }).id).toBe('measure:39');
    scene.dispose();
  });

  it('stays registered across a growth rebuild', () => {
    // The mesh is REPLACED when it grows, and the registry keys on the object —
    // so a rebuild without re-registering is a layer that believes it is
    // registered. Exactly the shape of bug pattern 4.
    const registry = new PickRegistry();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);
    expect(registry.size).toBe(1);

    const many: Measurement[] = [];
    for (let i = 0; i < 50; i++) {
      const y = 1000 + i * 150;
      many.push(distance(`measure:${String(i)}`, { x: 2000, y }, { x: 8000, y }));
    }
    scene.update(input({ measurements: many }));

    expect(registry.size).toBe(1);
    expect(registry.objects()[0]).toBe(scene.root.children.at(-1));
    scene.dispose();
  });
});

describe('dismissing', () => {
  it('drops the click target with the measurement', () => {
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);

    const a = distance('measure:a', { x: 2000, y: 2000 }, { x: 8000, y: 2000 });
    const b = distance('measure:b', { x: 2000, y: 6000 }, { x: 8000, y: 6000 });
    scene.update(input({ measurements: [a, b] }));
    expect(click(registry, camera, { x: 5000, y: 6000 }).id).toBe('measure:b');

    scene.update(input({ measurements: [a] }));
    // A stale instance would still be in the matrix buffer; `count` is what
    // stops it being drawn AND raycast.
    expect(scene.pickCount).toBe(1);
    expect(click(registry, camera, { x: 5000, y: 6000 }).kind).toBe('empty');
    expect(click(registry, camera, { x: 5000, y: 2000 }).id).toBe('measure:a');
    scene.dispose();
  });

  it('keeps instance ids aligned with the matrices after a reorder', () => {
    // The live id array is rewritten in the same order as the matrices. If the
    // two ever drift, a click returns a DIFFERENT measurement's id — the
    // dimension layer's documented reason for reading the array live.
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);

    const a = distance('measure:a', { x: 2000, y: 2000 }, { x: 8000, y: 2000 });
    const b = distance('measure:b', { x: 2000, y: 6000 }, { x: 8000, y: 6000 });
    scene.update(input({ measurements: [a, b] }));
    scene.update(input({ measurements: [b, a] }));

    expect(scene.pickIds).toEqual(['measure:b', 'measure:a']);
    expect(click(registry, camera, { x: 5000, y: 2000 }).id).toBe('measure:a');
    expect(click(registry, camera, { x: 5000, y: 6000 }).id).toBe('measure:b');
    scene.dispose();
  });
});

describe('visibility', () => {
  it('hiding the layer also stops it answering clicks', () => {
    // `Raycaster` does not check `.visible` on the objects it is handed
    // directly — the core's `isEffectivelyVisible` does, by walking parents. A
    // measurement you cannot see must not be a measurement you can click.
    const registry = new PickRegistry();
    const camera = planCamera();
    const scene = new MeasureScene(testMaterials());
    scene.attach(registry);
    const m = distance('measure:1', { x: 2000, y: 2000 }, { x: 8000, y: 2000 });

    scene.update(input({ measurements: [m], visible: true }));
    expect(click(registry, camera, { x: 5000, y: 2000 }).id).toBe('measure:1');

    scene.update(input({ measurements: [m], visible: false }));
    expect(click(registry, camera, { x: 5000, y: 2000 }).kind).toBe('empty');
    scene.dispose();
  });
});
