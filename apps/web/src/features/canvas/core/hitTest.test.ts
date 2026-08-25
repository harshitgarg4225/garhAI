/**
 * Spec for the picker's decision rules and for the registry behind them.
 *
 * `resolveHit` is pure, so the whole §12 contract — "openings beat their host
 * wall, dimensions beat rooms", one implementation for 2D and 3D — is testable
 * without a GPU, a camera or a scene. The raycasting half of `pickAt` is a thin
 * wrapper around it; what is worth pinning is the rule, and the rule is here.
 */

import { describe, expect, it } from 'vitest';
import { Object3D } from 'three';

import { DEPTH_EPSILON_WORLD_3D, PICK_PRIORITY, type PickKind } from './constants';
import {
  comparePickCandidates,
  depthEpsilonForMode,
  emptyHit,
  pickPriority,
  resolveHit,
  sameHitTarget,
  type PickCandidate,
  type PickHit,
} from './hitTest';
import { isEffectivelyVisible, PickRegistry } from './pickRegistry';

function candidate(kind: PickKind, id: string, distanceWorld: number): PickCandidate {
  return {
    target: { kind, id, storeyId: 'storey_A' },
    distanceWorld,
    pointMm: { x: 0, y: 0 },
    elevationMm: 0,
    object: null,
    instanceId: null,
  };
}

const PLAN = { depthEpsilonWorld: depthEpsilonForMode('2d') };
const VIEW3D = { depthEpsilonWorld: depthEpsilonForMode('3d') };

describe('priority table', () => {
  it('states the two rules §12 names', () => {
    expect(PICK_PRIORITY.opening).toBeGreaterThan(PICK_PRIORITY.wall);
    expect(PICK_PRIORITY.dimension).toBeGreaterThan(PICK_PRIORITY.room);
  });

  it('puts the room fill at the bottom, so it never steals a click', () => {
    for (const kind of Object.keys(PICK_PRIORITY) as PickKind[]) {
      if (kind === 'room') continue;
      expect(pickPriority(kind)).toBeGreaterThan(pickPriority('room'));
    }
  });
});

describe('resolveHit — 2D (everything coplanar, priority decides)', () => {
  it('returns null for nothing', () => {
    expect(resolveHit([], PLAN)).toBeNull();
  });

  it('picks the opening over its host wall', () => {
    // Registration order deliberately puts the wall first: in a plan view the
    // returned order is mount order, and mount order must not decide anything.
    const hits = [candidate('wall', 'wall_1', 100), candidate('opening', 'opening_1', 100)];
    expect(resolveHit(hits, PLAN)?.target.id).toBe('opening_1');
  });

  it('picks the opening even when the wall is nominally nearer', () => {
    const hits = [candidate('wall', 'wall_1', 99.9), candidate('opening', 'opening_1', 100.1)];
    expect(resolveHit(hits, PLAN)?.target.id).toBe('opening_1');
  });

  it('picks the dimension over the room it sits in', () => {
    const hits = [candidate('room', 'room_1', 100), candidate('dimension', 'annotation_1', 100)];
    expect(resolveHit(hits, PLAN)?.target.id).toBe('annotation_1');
  });

  it('picks the wall over the room, so a party wall is clickable', () => {
    const hits = [candidate('room', 'room_1', 100), candidate('wall', 'wall_1', 100)];
    expect(resolveHit(hits, PLAN)?.target.kind).toBe('wall');
  });

  it('picks furniture over the room it stands on', () => {
    const hits = [candidate('room', 'room_1', 100), candidate('furniture', 'furniture_1', 100)];
    expect(resolveHit(hits, PLAN)?.target.kind).toBe('furniture');
  });

  it('breaks equal-priority ties by distance, then deterministically by id', () => {
    const nearer = candidate('room', 'room_z', 10);
    const further = candidate('room', 'room_a', 20);
    expect(resolveHit([further, nearer], PLAN)?.target.id).toBe('room_z');

    const tieA = candidate('room', 'room_a', 10);
    const tieB = candidate('room', 'room_b', 10);
    // Same answer whichever order they arrive in — a selection that flickers
    // under a still mouse is the bug this prevents.
    expect(resolveHit([tieA, tieB], PLAN)?.target.id).toBe('room_a');
    expect(resolveHit([tieB, tieA], PLAN)?.target.id).toBe('room_a');
  });
});

describe('resolveHit — 3D (depth matters, priority only within the window)', () => {
  it('lets the opening win against its coplanar host wall', () => {
    const hits = [
      candidate('wall', 'wall_1', 5),
      // Inside the 50 mm window: a door leaf and its wall really are this close.
      candidate('opening', 'opening_1', 5 + DEPTH_EPSILON_WORLD_3D / 2),
    ];
    expect(resolveHit(hits, VIEW3D)?.target.id).toBe('opening_1');
  });

  it('does not let a far high-priority element steal a near click', () => {
    const hits = [
      candidate('wall', 'wall_1', 5),
      // Two metres behind the wall: a dimension over there is not what you meant.
      candidate('dimension', 'annotation_1', 7),
    ];
    expect(resolveHit(hits, VIEW3D)?.target.id).toBe('wall_1');
  });

  it('still prefers the nearer of two equals', () => {
    const hits = [candidate('wall', 'wall_far', 9), candidate('wall', 'wall_near', 3)];
    expect(resolveHit(hits, VIEW3D)?.target.id).toBe('wall_near');
  });

  it('collapses to the 2D rule when the window is infinite', () => {
    const hits = [candidate('wall', 'wall_1', 1), candidate('opening', 'opening_1', 900)];
    expect(resolveHit(hits, VIEW3D)?.target.kind).toBe('wall');
    expect(resolveHit(hits, { depthEpsilonWorld: Infinity })?.target.kind).toBe('opening');
  });
});

describe('comparison is a total order', () => {
  it('is antisymmetric', () => {
    const a = candidate('wall', 'wall_1', 4);
    const b = candidate('opening', 'opening_1', 4);
    expect(Math.sign(comparePickCandidates(a, b))).toBe(-Math.sign(comparePickCandidates(b, a)));
  });

  it('is reflexive-zero', () => {
    const a = candidate('wall', 'wall_1', 4);
    expect(comparePickCandidates(a, a)).toBe(0);
  });
});

describe('empty hits', () => {
  it('carries the plane point so a tool knows where to start drawing', () => {
    const hit = emptyHit({ x: 1150, y: -2300 }, 3050);
    expect(hit.kind).toBe('empty');
    expect(hit.id).toBeNull();
    expect(hit.pointMm).toEqual({ x: 1150, y: -2300 });
    expect(hit.elevationMm).toBe(3050);
  });

  it('admits it has no point when the ray misses the plane', () => {
    // 3D, pointer above the horizon. Reporting (0, 0) here would place a wall
    // at the plot origin — the null is the honest answer.
    expect(emptyHit(null, 0).pointMm).toBeNull();
  });
});

describe('hover de-duplication', () => {
  const hitOf = (kind: PickHit['kind'], id: string | null): PickHit => ({
    kind,
    id,
    storeyId: null,
    pointMm: { x: 0, y: 0 },
    elevationMm: 0,
    distanceWorld: 1,
    object: null,
    instanceId: null,
  });

  it('ignores the point moving within the same element', () => {
    const a = { ...hitOf('wall', 'wall_1'), pointMm: { x: 0, y: 0 } };
    const b = { ...hitOf('wall', 'wall_1'), pointMm: { x: 900, y: 40 } };
    expect(sameHitTarget(a, b)).toBe(true);
  });

  it('notices a different element, a different kind, or a different instance', () => {
    expect(sameHitTarget(hitOf('wall', 'wall_1'), hitOf('wall', 'wall_2'))).toBe(false);
    expect(sameHitTarget(hitOf('wall', 'wall_1'), hitOf('room', 'wall_1'))).toBe(false);
    expect(
      sameHitTarget(
        { ...hitOf('furniture', 'f_1'), instanceId: 3 },
        { ...hitOf('furniture', 'f_1'), instanceId: 4 },
      ),
    ).toBe(false);
  });

  it('treats two empties as the same', () => {
    expect(sameHitTarget(hitOf('empty', null), hitOf('empty', null))).toBe(true);
    expect(sameHitTarget(null, null)).toBe(true);
    expect(sameHitTarget(null, hitOf('wall', 'wall_1'))).toBe(false);
  });
});

describe('PickRegistry', () => {
  it('resolves a fixed target', () => {
    const registry = new PickRegistry();
    const object = new Object3D();
    registry.register(object, { kind: 'wall', id: 'wall_1', storeyId: 'storey_A' });
    expect(registry.resolve({ object } as never)?.id).toBe('wall_1');
    expect(registry.size).toBe(1);
  });

  it('resolves instances through the id array, read live', () => {
    const registry = new PickRegistry();
    const mesh = new Object3D();
    const ids = ['furniture_1', 'furniture_2'];
    registry.registerInstanced(mesh, 'furniture', ids, 'storey_A');

    expect(registry.resolve({ object: mesh, instanceId: 1 } as never)?.id).toBe('furniture_2');
    // Recycling an instance slot must not require re-registering the mesh —
    // that is the whole point of instancing.
    ids[1] = 'furniture_9';
    expect(registry.resolve({ object: mesh, instanceId: 1 } as never)?.id).toBe('furniture_9');
    // A slot that is not in use is not pickable.
    expect(registry.resolve({ object: mesh, instanceId: 7 } as never)).toBeNull();
    expect(registry.resolve({ object: mesh } as never)).toBeNull();
  });

  it('unregisters through the returned disposer', () => {
    const registry = new PickRegistry();
    const object = new Object3D();
    const dispose = registry.register(object, { kind: 'room', id: 'room_1', storeyId: null });
    expect(registry.objects()).toContain(object);
    dispose();
    expect(registry.size).toBe(0);
    expect(registry.objects()).not.toContain(object);
    expect(registry.resolve({ object } as never)).toBeNull();
  });

  it('reuses the objects array until membership changes', () => {
    const registry = new PickRegistry();
    registry.register(new Object3D(), { kind: 'wall', id: 'wall_1', storeyId: null });
    const first = registry.objects();
    // No allocation on the hot path: the same array comes back.
    expect(registry.objects()).toBe(first);
    registry.register(new Object3D(), { kind: 'wall', id: 'wall_2', storeyId: null });
    expect(registry.objects()).not.toBe(first);
    expect(registry.objects()).toHaveLength(2);
  });

  it('never returns a resolver for an object it does not know', () => {
    const registry = new PickRegistry();
    expect(registry.resolve({ object: new Object3D() } as never)).toBeNull();
  });
});

describe('visibility', () => {
  it('walks up the parent chain', () => {
    const storey = new Object3D();
    const wall = new Object3D();
    storey.add(wall);
    expect(isEffectivelyVisible(wall)).toBe(true);

    // Hiding a storey must make its walls unclickable, not merely invisible —
    // three does not check `.visible` on objects handed to it directly, so
    // without this you can delete a wall you cannot see.
    storey.visible = false;
    expect(isEffectivelyVisible(wall)).toBe(false);

    storey.visible = true;
    wall.visible = false;
    expect(isEffectivelyVisible(wall)).toBe(false);
  });
});
