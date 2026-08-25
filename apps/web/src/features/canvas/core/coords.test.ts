/**
 * Spec for the coordinate boundary.
 *
 * This file exists to pin three things that are cheap to get wrong and
 * expensive to discover later:
 *
 *   1. **Handedness.** North must map to −Z. A sign error here mirrors every
 *      plan, and every other test in the product still passes.
 *   2. **The rounding rule.** Half away from zero, symmetric about zero, the
 *      same rule `packages/model/src/units.ts` is golden-tested against its
 *      Python twin with. `Math.round` would pass a naive positive-only test.
 *   3. **Snapping at negatives and at exact boundaries.** A plot-local
 *      coordinate can be negative (a projection outside the SW corner), and
 *      exactly-half-a-module is a real value on a 115 mm grid.
 */

import { describe, expect, it } from 'vitest';
import { OrthographicCamera, Vector3 } from 'three';

import {
  constrainOrtho,
  mmToWorld,
  mmToWorldScalar,
  mmToWorldXYZ,
  ndcFromPixel,
  pickToleranceMm,
  pixelFromNdc,
  pointAtLengthMm,
  pointerToMm,
  pointerToMmRaw,
  snapMm,
  snapPtMm,
  snapPtRelativeMm,
  worldToElevationMm,
  worldToMm,
  worldToMmScalar,
  WORLD_UNITS_PER_MM,
} from './coords';
import { SNAP_COARSE_MM, SNAP_FINE_MM } from './constants';
import { bboxRingMm, pointsMmToWorld, polygonFillGeometry } from './outlineGeometry';

describe('mm ↔ world', () => {
  it('scales by 1 world unit per metre', () => {
    expect(WORLD_UNITS_PER_MM).toBe(0.001);
    expect(mmToWorldScalar(1000)).toBeCloseTo(1, 12);
    expect(mmToWorldScalar(115)).toBeCloseTo(0.115, 12);
  });

  it('maps north (+Y) to −Z and elevation (+Z) to +Y', () => {
    const world = mmToWorld({ x: 3000, y: 4000 }, 2700);
    expect(world.x).toBeCloseTo(3, 12);
    expect(world.y).toBeCloseTo(2.7, 12);
    // THE handedness assertion. If this ever reads +4, every plan is mirrored.
    expect(world.z).toBeCloseTo(-4, 12);
  });

  it('round-trips a spread of coordinates, including negatives', () => {
    const samples = [
      { x: 0, y: 0 },
      { x: 115, y: 230 },
      { x: -115, y: -230 },
      { x: 1, y: -1 },
      { x: 30_480, y: 12_192 },
      { x: -999_999, y: 999_999 },
    ];
    for (const p of samples) {
      expect(worldToMm(mmToWorld(p, 0))).toEqual(p);
    }
  });

  it('round-trips elevation separately from the plan point', () => {
    const world = mmToWorld({ x: 500, y: -500 }, 3050);
    expect(worldToMm(world)).toEqual({ x: 500, y: -500 });
    expect(worldToElevationMm(world)).toBe(3050);
  });

  it('writes into a caller-supplied vector without allocating', () => {
    const out = new Vector3();
    const returned = mmToWorldXYZ(1000, 2000, 3000, out);
    expect(returned).toBe(out);
    expect(out.toArray()).toEqual([1, 3, -2]);
  });
});

describe('rounding: half away from zero, symmetric', () => {
  it('rounds exact halves away from zero, not up', () => {
    // 0.0005 world units = 0.5 mm.
    expect(worldToMmScalar(0.0005)).toBe(1);
    expect(worldToMmScalar(-0.0005)).toBe(-1);
    expect(worldToMmScalar(0.0015)).toBe(2);
    expect(worldToMmScalar(-0.0015)).toBe(-2);
  });

  it('is exactly antisymmetric, so the north flip introduces no bias', () => {
    for (const mm of [0.5, 1.5, 2.5, 114.5, -0.5, 1e-9, 1234.5]) {
      // `+ 0` folds the negated side's -0 into +0: roundMm normalises zeros
      // (no signed zero in the model contract), so `-f(-x)` at a zero result
      // is -0 while `f(x)` is +0 — same number, different IEEE sign bit.
      expect(worldToMmScalar(mm / 1000)).toBe(-worldToMmScalar(-mm / 1000) + 0);
    }
  });

  it('absorbs float error from the ×1000 conversion', () => {
    // 0.115 × 1000 is 114.99999999999999 in IEEE-754.
    expect(worldToMmScalar(0.115)).toBe(115);
    expect(worldToMmScalar(-0.115)).toBe(-115);
    expect(worldToMm({ x: 0.115, y: 0, z: -0.23 })).toEqual({ x: 115, y: 230 });
  });
});

describe('snapping', () => {
  it('snaps to the 115 mm brick module', () => {
    expect(snapMm(0, SNAP_COARSE_MM)).toBe(0);
    expect(snapMm(114, SNAP_COARSE_MM)).toBe(115);
    expect(snapMm(58, SNAP_COARSE_MM)).toBe(115);
    expect(snapMm(57, SNAP_COARSE_MM)).toBe(0);
    expect(snapMm(3000, SNAP_COARSE_MM)).toBe(2990); // 26 × 115
  });

  it('snaps the exact half-module away from zero, both signs', () => {
    // 57.5 is exactly half of 115.
    expect(snapMm(57.5, SNAP_COARSE_MM)).toBe(115);
    expect(snapMm(-57.5, SNAP_COARSE_MM)).toBe(-115);
    expect(snapMm(172.5, SNAP_COARSE_MM)).toBe(230);
    expect(snapMm(-172.5, SNAP_COARSE_MM)).toBe(-230);
  });

  it('is symmetric across zero at every boundary', () => {
    for (const v of [1, 57, 57.5, 58, 114, 115, 116, 1_000_000]) {
      // `+ 0` folds the negated side's -0 into +0 — see the antisymmetry
      // spec above for why the zero's sign bit is not part of the contract.
      expect(snapMm(v, SNAP_COARSE_MM)).toBe(-snapMm(-v, SNAP_COARSE_MM) + 0);
    }
  });

  it('still returns whole millimetres when the grid is off', () => {
    expect(snapMm(1234.6, 0)).toBe(1235);
    expect(snapMm(-1234.6, 0)).toBe(-1235);
    expect(Number.isInteger(snapMm(0.4, 0))).toBe(true);
  });

  it('snaps points on the fine grid too', () => {
    expect(snapPtMm({ x: 13, y: -13 }, SNAP_FINE_MM)).toEqual({ x: 25, y: -25 });
    expect(snapPtMm({ x: 12, y: -12 }, SNAP_FINE_MM)).toEqual({ x: 0, y: 0 });
  });

  it('snaps relative to an origin, preserving an off-grid offset', () => {
    // Dragging a wall that starts at x = 7: the drag should move in whole
    // modules, not jerk the wall onto the grid.
    const origin = { x: 7, y: 3 };
    expect(snapPtRelativeMm({ x: 7 + 114, y: 3 }, origin, SNAP_COARSE_MM)).toEqual({
      x: 7 + 115,
      y: 3,
    });
    expect(snapPtRelativeMm({ x: 7 + 40, y: 3 }, origin, SNAP_COARSE_MM)).toEqual(origin);
  });
});

describe('ortho constraint and typed lengths', () => {
  it('locks to the dominant axis', () => {
    const from = { x: 0, y: 0 };
    expect(constrainOrtho(from, { x: 3000, y: 200 })).toEqual({ x: 3000, y: 0 });
    expect(constrainOrtho(from, { x: 200, y: -3000 })).toEqual({ x: 0, y: -3000 });
  });

  it('breaks a perfect diagonal deterministically (X wins)', () => {
    expect(constrainOrtho({ x: 0, y: 0 }, { x: 1000, y: 1000 })).toEqual({ x: 1000, y: 0 });
    expect(constrainOrtho({ x: 0, y: 0 }, { x: -1000, y: 1000 })).toEqual({ x: -1000, y: 0 });
  });

  it('places a point at a typed length along the drawn direction', () => {
    // "3.6m" while dragging east.
    expect(pointAtLengthMm({ x: 0, y: 0 }, { x: 500, y: 0 }, 3600)).toEqual({ x: 3600, y: 0 });
    expect(pointAtLengthMm({ x: 0, y: 0 }, { x: 0, y: -50 }, 2400)).toEqual({ x: 0, y: -2400 });
  });

  it('refuses to invent a direction for a degenerate drag', () => {
    const from = { x: 100, y: 100 };
    expect(pointAtLengthMm(from, from, 3600)).toEqual(from);
  });
});

describe('screen space', () => {
  it('flips Y exactly once between pixels and NDC', () => {
    const size = { width: 800, height: 600 };
    expect(ndcFromPixel({ x: 400, y: 300 }, size)).toEqual({ x: 0, y: 0 });
    // Top of the screen is +1 in NDC.
    expect(ndcFromPixel({ x: 0, y: 0 }, size)).toEqual({ x: -1, y: 1 });
    expect(ndcFromPixel({ x: 800, y: 600 }, size)).toEqual({ x: 1, y: -1 });
  });

  it('round-trips pixels through NDC', () => {
    const size = { width: 1024, height: 768 };
    for (const px of [
      { x: 0, y: 0 },
      { x: 512, y: 384 },
      { x: 1024, y: 768 },
      { x: 137, y: 601 },
    ]) {
      const back = pixelFromNdc(ndcFromPixel(px, size), size);
      expect(back.x).toBeCloseTo(px.x, 9);
      expect(back.y).toBeCloseTo(px.y, 9);
    }
  });

  it('keeps click slop constant in pixels, not in millimetres', () => {
    expect(pickToleranceMm(4, 6)).toBe(24);
    expect(pickToleranceMm(0.5, 6)).toBe(3);
  });
});

describe('pointer → model, through a real camera', () => {
  /** A plan camera framing 4 m across an 800 × 600 viewport (5 mm/px). */
  function planCamera(): OrthographicCamera {
    const mmPerPx = 5;
    const halfW = ((800 / 2) * mmPerPx) / 1000;
    const halfH = ((600 / 2) * mmPerPx) / 1000;
    const camera = new OrthographicCamera(-halfW, halfW, halfH, -halfH, 0.01, 400);
    camera.up.set(0, 0, -1);
    camera.position.set(0, 100, 0);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
    return camera;
  }

  it('puts the viewport centre at the camera centre', () => {
    const point = pointerToMmRaw({ x: 0, y: 0 }, planCamera(), 0);
    expect(point).toEqual({ x: 0, y: 0 });
  });

  it('puts +X (east) to the right and +Y (north) up', () => {
    const camera = planCamera();
    const right = pointerToMmRaw({ x: 1, y: 0 }, camera, 0);
    const up = pointerToMmRaw({ x: 0, y: 1 }, camera, 0);
    // Half the viewport is 400 px × 5 mm/px = 2000 mm.
    expect(right).toEqual({ x: 2000, y: 0 });
    expect(up).toEqual({ x: 0, y: 1500 });
  });

  it('applies the snap module on the way out', () => {
    const camera = planCamera();
    const raw = pointerToMmRaw({ x: 0.5, y: 0 }, camera, 0);
    expect(raw).toEqual({ x: 1000, y: 0 });
    // 1000 mm is 8.7 modules; the nearest module is 9 × 115 = 1035.
    expect(pointerToMm({ x: 0.5, y: 0 }, camera, { snapModuleMm: SNAP_COARSE_MM })).toEqual({
      x: 1035,
      y: 0,
    });
    expect(pointerToMm({ x: 0.5, y: 0 }, camera, { snapModuleMm: 0 })).toEqual({ x: 1000, y: 0 });
  });

  it('projects onto the storey plane, not the datum', () => {
    // Straight down: the plan point is the same at any elevation, but the hit
    // must be reported on the requested plane.
    const camera = planCamera();
    expect(pointerToMmRaw({ x: 0, y: 0 }, camera, 3050)).toEqual({ x: 0, y: 0 });
  });
});

describe('outline geometry uses the same flip as the rest of the boundary', () => {
  it('emits world triples with north on −Z', () => {
    const points = pointsMmToWorld([{ x: 1000, y: 2000 }], 500);
    expect(points).toEqual([[1, 0.5, -2]]);
  });

  it('closes a ring by repeating the first point', () => {
    const ring = bboxRingMm({ minX: 0, minY: 0, maxX: 1000, maxY: 2000 });
    expect(ring).toHaveLength(4);
    const open = pointsMmToWorld(ring, 0, false);
    const closed = pointsMmToWorld(ring, 0, true);
    expect(open).toHaveLength(4);
    expect(closed).toHaveLength(5);
    expect(closed[4]).toEqual(closed[0]);
  });

  it('triangulates a rectangle into two triangles at the right elevation', () => {
    const geometry = polygonFillGeometry(
      bboxRingMm({ minX: 0, minY: 0, maxX: 3000, maxY: 4000 }),
      2700,
    );
    const position = geometry.getAttribute('position');
    // Two triangles, three vertices each.
    expect(position.count).toBe(6);
    for (let i = 0; i < position.count; i++) {
      expect(position.getY(i)).toBeCloseTo(2.7, 6);
      // North maps to −Z, so every Y coordinate in the plan is ≤ 0 in world Z.
      expect(position.getZ(i)).toBeLessThanOrEqual(0);
    }
    geometry.dispose();
  });
});
