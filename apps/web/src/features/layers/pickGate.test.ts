/**
 * THE "A LOCKED WALL IS NOT CLICKABLE" GATE.
 *
 * This does not test a boolean. It builds a real `Mesh` with real triangles,
 * registers it with the real `PickRegistry`, points a real
 * `OrthographicCamera` at it, and calls the real `pickAt` — the same function
 * every click, hover and marquee in the product goes through. Then it locks the
 * layer and asserts the very same raycast comes back `kind: 'empty'`.
 *
 * That is the only assertion worth making here. The bug this repository has
 * already shipped — the furniture layer that tagged its meshes, documented
 * itself as integrated, and never called the registry — had no compile-time
 * signal and would have passed any test that only checked state. A pick either
 * lands or it does not, and that has to be measured by picking.
 *
 * SCENE CONVENTIONS. `coords.ts` maps model mm to world as
 * `(x, elevation, −y) × 0.001`, and the 2D rig looks straight down with
 * `up = (0, 0, −1)`. The quad below is therefore a horizontal 4 m × 4 m square
 * on the datum, centred on the origin, and the camera sits above it — which is
 * exactly the plan view.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import {
  BufferAttribute,
  BufferGeometry,
  Mesh,
  MeshBasicMaterial,
  OrthographicCamera,
} from 'three';

import { makeTwoRoomPlanWithOpenings } from '@garh/model';

import { pickAt } from '../canvas/core/hitTest';
import { PickRegistry } from '../canvas/core/pickRegistry';
import { EMPTY_PICK_BLOCK, type LayerPickBlock } from './mapping';
import { defaultLayerState } from './persist';
import { hasLayerPickGate, installLayerPickGate } from './pickGate';
import { blockedPicksFor, useLayerStore } from './store';

/** A 4 m × 4 m horizontal quad on the datum, in the canvas's world convention. */
function quadMesh(): Mesh {
  const h = 2; // world units = metres
  // Two triangles, wound so their normal points up (+Y).
  const positions = new Float32Array([-h, 0, -h, -h, 0, h, h, 0, h, -h, 0, -h, h, 0, h, h, 0, -h]);
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.computeBoundingSphere();
  const mesh = new Mesh(geometry, new MeshBasicMaterial());
  mesh.updateMatrixWorld(true);
  return mesh;
}

/** The 2D rig: orthographic, straight down, `up` along −Z. */
function planCamera(): OrthographicCamera {
  const camera = new OrthographicCamera(-5, 5, 5, -5, 0.01, 400);
  camera.position.set(0, 100, 0);
  camera.up.set(0, 0, -1);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  return camera;
}

const WALL_ID = 'wall_01J0000000000000000000WS';
const STOREY_ID = 'storey_01J0000000000000000000GF';

interface Harness {
  readonly registry: PickRegistry;
  /** Pick dead centre — the pointer is over the quad. */
  readonly pick: () => ReturnType<typeof pickAt>;
}

function harness(): Harness {
  const registry = new PickRegistry();
  const mesh = quadMesh();
  registry.register(mesh, { kind: 'wall', id: WALL_ID, storeyId: STOREY_ID });
  const camera = planCamera();
  return {
    registry,
    pick: () =>
      pickAt({
        registry,
        camera,
        ndc: { x: 0, y: 0 },
        mode: '2d',
        planeElevationMm: 0,
        mmPerPx: 10,
      }),
  };
}

function block(ids: readonly string[] = [], kinds: readonly string[] = []): LayerPickBlock {
  return { ids: new Set(ids), kinds: new Set(kinds) as LayerPickBlock['kinds'] };
}

// ---------------------------------------------------------------------------
// The scene must be pickable before any of this means anything
// ---------------------------------------------------------------------------

describe('the harness (so no assertion below can pass vacuously)', () => {
  it('picks the wall with no gate installed at all', () => {
    const { pick } = harness();
    const hit = pick();
    expect(hit.kind).toBe('wall');
    expect(hit.id).toBe(WALL_ID);
  });

  it('picks the wall through an installed gate that blocks nothing', () => {
    const { registry, pick } = harness();
    installLayerPickGate(registry, () => EMPTY_PICK_BLOCK);
    const hit = pick();
    expect(hit.kind).toBe('wall');
    expect(hit.id).toBe(WALL_ID);
  });
});

// ---------------------------------------------------------------------------
// The gate
// ---------------------------------------------------------------------------

describe('installLayerPickGate', () => {
  it('makes a locked element unpickable — the same raycast now hits nothing', () => {
    const { registry, pick } = harness();
    installLayerPickGate(registry, () => block([WALL_ID]));
    const hit = pick();
    expect(hit.kind).toBe('empty');
    expect(hit.id).toBeNull();
    // An empty pick is a real answer, not a failure: it still reports where the
    // reference plane is, which is what the wall tool needs.
    expect(hit.pointMm).not.toBeNull();
  });

  it('refuses by kind, for targets whose ids are synthetic (dimensions)', () => {
    const { registry, pick } = harness();
    installLayerPickGate(registry, () => block([], ['wall']));
    expect(pick().kind).toBe('empty');
  });

  it('leaves other elements alone', () => {
    const { registry, pick } = harness();
    installLayerPickGate(registry, () => block(['wall_someone_else'], ['dimension']));
    expect(pick().id).toBe(WALL_ID);
  });

  it('reads the state per pick, so unlocking takes effect on the next click', () => {
    const { registry, pick } = harness();
    let locked = true;
    installLayerPickGate(registry, () => (locked ? block([WALL_ID]) : EMPTY_PICK_BLOCK));
    expect(pick().kind).toBe('empty');
    locked = false;
    expect(pick().id).toBe(WALL_ID);
  });

  it('restores the original resolve on uninstall', () => {
    const { registry, pick } = harness();
    const uninstall = installLayerPickGate(registry, () => block([WALL_ID]));
    expect(hasLayerPickGate(registry)).toBe(true);
    expect(pick().kind).toBe('empty');

    uninstall();
    expect(hasLayerPickGate(registry)).toBe(false);
    expect(pick().id).toBe(WALL_ID);
    // The own property is gone, so the prototype method is doing the work
    // again — not a copy of it kept alive by this module.
    expect(Object.prototype.hasOwnProperty.call(registry, 'resolve')).toBe(false);
  });

  it('is idempotent — a second install replaces the first, it does not stack', () => {
    const { registry, pick } = harness();
    let firstReads = 0;
    installLayerPickGate(registry, () => {
      firstReads += 1;
      return block([WALL_ID]);
    });
    const uninstallSecond = installLayerPickGate(registry, () => EMPTY_PICK_BLOCK);

    expect(pick().id).toBe(WALL_ID);
    expect(firstReads, 'the replaced gate is still being consulted').toBe(0);

    uninstallSecond();
    expect(hasLayerPickGate(registry)).toBe(false);
    expect(pick().id).toBe(WALL_ID);
  });

  it('a stale uninstall does not strip the gate that replaced it', () => {
    const { registry, pick } = harness();
    const uninstallFirst = installLayerPickGate(registry, () => EMPTY_PICK_BLOCK);
    installLayerPickGate(registry, () => block([WALL_ID]));
    uninstallFirst();
    expect(hasLayerPickGate(registry)).toBe(true);
    expect(pick().kind).toBe('empty');
  });

  it('cannot invent a pick — it only ever turns a hit into nothing', () => {
    // The registry answers null for an unregistered object; the gate must not
    // change that, whatever its state says.
    const registry = new PickRegistry();
    const camera = planCamera();
    installLayerPickGate(registry, () => EMPTY_PICK_BLOCK);
    const hit = pickAt({ registry, camera, ndc: { x: 0, y: 0 }, mode: '2d' });
    expect(hit.kind).toBe('empty');
  });
});

// ---------------------------------------------------------------------------
// The whole chain, with no stand-ins
// ---------------------------------------------------------------------------

/**
 * Panel action → store → `blockedPicksFor` → gate → `pickAt`, over a real wall
 * from the shared model fixture. The tests above each prove one link; this one
 * proves they are actually joined, which is exactly what the furniture-layer
 * bug got wrong (every piece correct, nothing calling the registry).
 */
describe('store → gate → picker, end to end', () => {
  beforeEach(() => {
    useLayerStore.setState({
      scope: null,
      ...defaultLayerState(),
      isolated: null,
      preIsolate: null,
    });
  });

  function realWallHarness(): { registry: PickRegistry; pick: Harness['pick']; wallId: string } {
    const house = makeTwoRoomPlanWithOpenings().house;
    const wall = house.walls[0];
    expect(wall, 'the shared fixture has no walls').toBeDefined();
    const wallId = (wall as NonNullable<typeof wall>).id;

    const registry = new PickRegistry();
    registry.register(quadMesh(), { kind: 'wall', id: wallId, storeyId: wall?.storeyId ?? null });
    installLayerPickGate(registry, () => blockedPicksFor(house, useLayerStore.getState()));

    const camera = planCamera();
    return {
      registry,
      wallId,
      pick: () =>
        pickAt({ registry, camera, ndc: { x: 0, y: 0 }, mode: '2d', planeElevationMm: 0 }),
    };
  }

  it('locking A-WALL in the store makes a real wall unpickable, and unlocking gives it back', () => {
    const { pick, wallId } = realWallHarness();
    expect(pick().id, 'the wall must be pickable before the lock').toBe(wallId);

    useLayerStore.getState().setLocked('A-WALL', true);
    expect(pick().kind).toBe('empty');

    useLayerStore.getState().setLocked('A-WALL', false);
    expect(pick().id).toBe(wallId);
  });

  it('hiding A-WALL also makes it unpickable — nothing invisible stays clickable', () => {
    const { pick, wallId } = realWallHarness();
    useLayerStore.getState().setVisible('A-WALL', false);
    expect(pick().kind).toBe('empty');
    useLayerStore.getState().showAll();
    expect(pick().id).toBe(wallId);
  });

  it('locking a DIFFERENT layer leaves the wall alone', () => {
    const { pick, wallId } = realWallHarness();
    useLayerStore.getState().setLocked('A-DIM', true);
    useLayerStore.getState().setLocked('A-STAIR', true);
    expect(pick().id).toBe(wallId);
  });
});
