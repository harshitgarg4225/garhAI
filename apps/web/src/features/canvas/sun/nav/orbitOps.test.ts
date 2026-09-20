/**
 * Spec for the 3D navigation maths — the first real exerciser of the rig's
 * orbit helpers (inherited fact 2): `orbitEyeMm`, `fitOrbitToBbox`,
 * `mmPerPxAtDistance` and `clampOrbit` all run under assertions here.
 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_ORBIT_3D,
  fitOrbitToBbox,
  mmPerPxAtDistance,
  orbitByPx,
  orbitEyeMm,
  type Orbit3D,
} from '../../core';

import { fixedId, makeTwoRoomPlanWithOpenings, type Opening, type Wall } from '@garh/model';

import {
  dollyOrbitAboutAnchor,
  enterWalkOrbit,
  orbitFromWalkPose,
  pointBlockedByWalls,
  walkObstaclesOf,
  walkPoseOf,
  walkStep,
  walkStepAvoiding,
  walkTurn,
  WALK_BODY_RADIUS_MM,
  WALK_EYE_HEIGHT_MM,
  WALK_LOOK_DISTANCE_MM,
  WALK_MAX_PITCH_UP_DEG,
  WALK_POLAR_LIMIT_DEG,
  type WalkObstacles,
} from './orbitOps';

const ORBIT: Orbit3D = {
  targetMm: { x: 6000, y: 4500, z: 1500 },
  distanceMm: 20_000,
  azimuthDeg: 225,
  polarDeg: 60,
};

function len3(v: { x: number; y: number; z: number }): number {
  return Math.hypot(v.x, v.y, v.z);
}

describe('dollyOrbitAboutAnchor — zoom to cursor', () => {
  const anchor = { x: 9000, y: 2000, z: 0 };

  it('keeps the eye on the eye→anchor ray and scales its distance by the factor', () => {
    const eyeBefore = orbitEyeMm(ORBIT);
    const after = dollyOrbitAboutAnchor(ORBIT, 0.5, anchor);
    const eyeAfter = orbitEyeMm(after);

    const before = {
      x: eyeBefore.x - anchor.x,
      y: eyeBefore.y - anchor.y,
      z: eyeBefore.z - anchor.z,
    };
    const now = { x: eyeAfter.x - anchor.x, y: eyeAfter.y - anchor.y, z: eyeAfter.z - anchor.z };
    // Same direction from the anchor…
    expect(now.x / len3(now)).toBeCloseTo(before.x / len3(before), 9);
    expect(now.y / len3(now)).toBeCloseTo(before.y / len3(before), 9);
    expect(now.z / len3(now)).toBeCloseTo(before.z / len3(before), 9);
    // …at exactly half the range: the point under the cursor stays put.
    expect(len3(now)).toBeCloseTo(len3(before) * 0.5, 6);
  });

  it('view direction is unchanged — a dolly never re-aims the camera', () => {
    const after = dollyOrbitAboutAnchor(ORBIT, 1.7, anchor);
    expect(after.azimuthDeg).toBeCloseTo(ORBIT.azimuthDeg, 9);
    expect(after.polarDeg).toBeCloseTo(ORBIT.polarDeg, 9);
    expect(after.distanceMm).toBeCloseTo(ORBIT.distanceMm * 1.7, 6);
  });

  it('honours the rig clamp — distance never collapses below 500 mm', () => {
    const after = dollyOrbitAboutAnchor(ORBIT, 1e-9, anchor);
    expect(after.distanceMm).toBeGreaterThanOrEqual(500);
  });

  it('keeps the mmPerPx equivalence meaningful (rig helper exercised)', () => {
    const before = mmPerPxAtDistance(ORBIT.distanceMm, 800);
    const after = mmPerPxAtDistance(dollyOrbitAboutAnchor(ORBIT, 0.5, anchor).distanceMm, 800);
    expect(after).toBeCloseTo(before * 0.5, 6);
  });
});

describe('walk pose ↔ orbit round trip', () => {
  const pose = {
    eyeMm: { x: 3000, y: -2000, z: 1600 },
    headingDeg: 30,
    pitchDownDeg: 5,
  };

  it('orbitFromWalkPose puts the eye exactly where the pose says', () => {
    const orbit = orbitFromWalkPose(pose);
    const eye = orbitEyeMm(orbit);
    expect(eye.x).toBeCloseTo(pose.eyeMm.x, 6);
    expect(eye.y).toBeCloseTo(pose.eyeMm.y, 6);
    expect(eye.z).toBeCloseTo(pose.eyeMm.z, 6);
    expect(orbit.distanceMm).toBe(WALK_LOOK_DISTANCE_MM);
  });

  it('walkPoseOf inverts orbitFromWalkPose', () => {
    const back = walkPoseOf(orbitFromWalkPose(pose));
    expect(back.eyeMm.x).toBeCloseTo(pose.eyeMm.x, 6);
    expect(back.eyeMm.y).toBeCloseTo(pose.eyeMm.y, 6);
    expect(back.eyeMm.z).toBeCloseTo(pose.eyeMm.z, 6);
    expect(back.headingDeg).toBeCloseTo(pose.headingDeg, 6);
    expect(back.pitchDownDeg).toBeCloseTo(pose.pitchDownDeg, 6);
  });

  it('can look UP: −30° pitch is polar 120 under the walk limit, and the eye sits below its target', () => {
    const up = orbitFromWalkPose({ ...pose, pitchDownDeg: -30 });
    expect(up.polarDeg).toBe(120);
    expect(up.polarLimitDeg).toBe(WALK_POLAR_LIMIT_DEG);
    const eye = orbitEyeMm(up);
    expect(eye.z).toBeLessThan(up.targetMm.z); // looking up = target above the eye
    expect(walkPoseOf(up).pitchDownDeg).toBeCloseTo(-30, 6);
    // Clamped at the allowance both ways.
    expect(orbitFromWalkPose({ ...pose, pitchDownDeg: -200 }).polarDeg).toBe(
      90 + WALK_MAX_PITCH_UP_DEG,
    );
    expect(orbitFromWalkPose({ ...pose, pitchDownDeg: 200 }).polarDeg).toBe(1);
  });

  it('negative control: an ORBIT built without the walk limit still cannot go under its target', () => {
    const eye = orbitEyeMm({ ...ORBIT, polarDeg: 120 });
    expect(eye.z).toBeGreaterThan(ORBIT.targetMm.z);
  });
});

describe('enterWalkOrbit', () => {
  it('drops the eye to 1600 mm above the storey floor and keeps the heading', () => {
    const floorMm = 3150; // first floor FFL
    const walk = enterWalkOrbit(ORBIT, floorMm);
    const eye = orbitEyeMm(walk);
    expect(eye.z).toBeCloseTo(floorMm + WALK_EYE_HEIGHT_MM, 4);
    expect(walkPoseOf(walk).headingDeg).toBeCloseTo((ORBIT.azimuthDeg + 180) % 360, 6);
  });
});

describe('walkStep — WASD in the ground plane', () => {
  const start = orbitFromWalkPose({
    eyeMm: { x: 0, y: 0, z: WALK_EYE_HEIGHT_MM },
    headingDeg: 90, // facing model north (+Y)
    pitchDownDeg: 4,
  });

  it('forward moves along the heading, strafe moves perpendicular', () => {
    const fwd = walkPoseOf(walkStep(start, 1000, 0));
    expect(fwd.eyeMm.x).toBeCloseTo(0, 6);
    expect(fwd.eyeMm.y).toBeCloseTo(1000, 6);

    const right = walkPoseOf(walkStep(start, 0, 1000));
    expect(right.eyeMm.x).toBeCloseTo(1000, 6); // right of north is east
    expect(right.eyeMm.y).toBeCloseTo(0, 6);
  });

  it('eye height never drifts through steps and turns — no stair-climbing lie', () => {
    let orbit = start;
    for (let i = 0; i < 50; i++) {
      orbit = walkStep(orbit, 137, -59);
      orbit = walkTurn(orbit, 13, -7);
    }
    expect(orbitEyeMm(orbit).z).toBeCloseTo(WALK_EYE_HEIGHT_MM, 4);
  });
});

describe('walkTurn — mouse look', () => {
  const start = orbitFromWalkPose({
    eyeMm: { x: 500, y: 700, z: WALK_EYE_HEIGHT_MM },
    headingDeg: 90,
    pitchDownDeg: 10,
  });

  it('drag right looks right (heading falls), the eye stays planted', () => {
    const after = walkTurn(start, 100, 0);
    const pose = walkPoseOf(after);
    expect(pose.headingDeg).toBeLessThan(90);
    const eye = orbitEyeMm(after);
    expect(eye.x).toBeCloseTo(500, 4);
    expect(eye.y).toBeCloseTo(700, 4);
    expect(eye.z).toBeCloseTo(WALK_EYE_HEIGHT_MM, 4);
  });

  it('drag down looks down, clamped at nearly-straight-down', () => {
    const pose = walkPoseOf(walkTurn(start, 0, 100_000));
    expect(pose.pitchDownDeg).toBe(89);
  });
});

describe('rig helpers exercised end-to-end', () => {
  it('fitOrbitToBbox frames a building box above the ground', () => {
    const box = { minX: 0, minY: 0, maxX: 9_000, maxY: 12_000 };
    const fitted = fitOrbitToBbox(DEFAULT_ORBIT_3D, box, 7_200, 16 / 9);
    expect(fitted.targetMm.x).toBeCloseTo(4_500, 6);
    expect(fitted.targetMm.y).toBeCloseTo(6_000, 6);
    expect(fitted.targetMm.z).toBeCloseTo(3_600, 6);
    // The whole diagonal fits inside the frustum from the fitted distance.
    const radius = 0.5 * Math.hypot(9_000, 12_000, 7_200);
    expect(fitted.distanceMm).toBeGreaterThan(radius);
    // And the eye ends up above the ground plane, looking down at the box.
    expect(orbitEyeMm(fitted).z).toBeGreaterThan(0);
  });

  it('orbitByPx stays inside the polar clamp under wild drags', () => {
    let orbit = DEFAULT_ORBIT_3D;
    for (let i = 0; i < 100; i++) orbit = orbitByPx(orbit, 500, 500);
    expect(orbit.polarDeg).toBeGreaterThanOrEqual(1);
    expect(orbit.polarDeg).toBeLessThanOrEqual(89);
    expect(orbit.azimuthDeg).toBeGreaterThanOrEqual(0);
    expect(orbit.azimuthDeg).toBeLessThan(360);
  });

  it('an orbit drag after a walk pose drops the walk limit — back under the orbit rule', () => {
    const walked = orbitFromWalkPose({
      eyeMm: { x: 0, y: 0, z: 1600 },
      headingDeg: 90,
      pitchDownDeg: -40,
    });
    expect(walked.polarLimitDeg).toBeDefined();
    const dragged = orbitByPx(walked, 0, 0);
    expect(dragged.polarLimitDeg).toBeUndefined();
    expect(dragged.polarDeg).toBeLessThanOrEqual(89);
  });
});

// ---------------------------------------------------------------------------
// Collision — walls stop you, doors let you through
// ---------------------------------------------------------------------------

describe('walk collision', () => {
  /** One 230 wall along the x axis from (0,0) to (6000,0), a door at 3000. */
  const WALL: Wall = {
    id: fixedId('wall', 'W1'),
    storeyId: fixedId('storey', 'GF'),
    a: { x: 0, y: 0 },
    b: { x: 6000, y: 0 },
    thicknessMm: 230,
    kind: 'external',
    loadBearing: true,
  };
  const DOOR: Opening = {
    id: fixedId('opening', 'D1'),
    wallId: WALL.id,
    kind: 'door',
    widthMm: 1200,
    heightMm: 2100,
    sillMm: 0,
    offsetMm: 3000,
    swing: 'in-left',
    tag: null,
  };
  const WINDOW: Opening = { ...DOOR, id: fixedId('opening', 'W1'), kind: 'window', sillMm: 900 };
  const solid: WalkObstacles = { walls: [WALL], openings: [] };
  const withDoor: WalkObstacles = { walls: [WALL], openings: [DOOR] };
  const withWindow: WalkObstacles = { walls: [WALL], openings: [WINDOW] };

  /** Standing 1 m south of the wall at x, facing north. */
  const facingNorth = (x: number, y = -1000) =>
    orbitFromWalkPose({ eyeMm: { x, y, z: 1600 }, headingDeg: 90, pitchDownDeg: 4 });

  it('pointBlockedByWalls: inside the wall band (half thickness + shoulder) blocks', () => {
    const reach = 115 + WALK_BODY_RADIUS_MM; // 415
    expect(pointBlockedByWalls({ x: 1000, y: -(reach + 1) }, solid)).toBe(false);
    expect(pointBlockedByWalls({ x: 1000, y: -(reach - 1) }, solid)).toBe(true);
    expect(pointBlockedByWalls({ x: 1000, y: 0 }, solid)).toBe(true);
    // Past the wall's end it is open floor.
    expect(pointBlockedByWalls({ x: 7000, y: 0 }, solid)).toBe(false);
  });

  it('a door lets the body through; a window (sill) does not', () => {
    expect(pointBlockedByWalls({ x: 3000, y: 0 }, withDoor)).toBe(false);
    expect(pointBlockedByWalls({ x: 3000, y: 0 }, withWindow)).toBe(true);
    // The body must FIT the leaf: 1200 wide, 300 shoulder ⇒ 2700..3300 clears.
    expect(pointBlockedByWalls({ x: 2750, y: 0 }, withDoor)).toBe(false);
    expect(pointBlockedByWalls({ x: 2650, y: 0 }, withDoor)).toBe(true);
  });

  it('walkStepAvoiding refuses a step into the wall and keeps the walker where it stood', () => {
    const start = facingNorth(1000);
    const blocked = walkStepAvoiding(start, 1000, 0, solid); // would land at y = 0
    expect(walkPoseOf(blocked).eyeMm).toEqual(walkPoseOf(start).eyeMm);
    // Negative control: the same step with no obstacles goes through.
    expect(walkPoseOf(walkStep(start, 1000, 0)).eyeMm.y).toBeCloseTo(0, 6);
    // A short step that stays clear of the band is taken.
    const clear = walkStepAvoiding(start, 400, 0, solid); // y = −600 > −415 band
    expect(walkPoseOf(clear).eyeMm.y).toBeCloseTo(-600, 6);
  });

  it('walks THROUGH the door', () => {
    const start = facingNorth(3000);
    const inside = walkStepAvoiding(start, 2000, 0, withDoor); // lands at y = +1000
    expect(walkPoseOf(inside).eyeMm.y).toBeCloseTo(1000, 6);
    const stopped = walkStepAvoiding(start, 2000, 0, withWindow);
    expect(walkPoseOf(stopped).eyeMm.y).toBeCloseTo(-1000, 6);
  });

  it('a long stride cannot tunnel: the PATH is checked, not just the landing point', () => {
    // Shift-stride over a 115 wall: the end point (y = +1000) is clear floor,
    // the wall band (±358) lies between. Sampled every body radius ⇒ refused.
    const thin: WalkObstacles = { walls: [{ ...WALL, thicknessMm: 115 }], openings: [] };
    const start = facingNorth(1000);
    expect(pointBlockedByWalls({ x: 1000, y: 1000 }, thin)).toBe(false); // landing is clear…
    expect(walkPoseOf(walkStepAvoiding(start, 2000, 0, thin)).eyeMm.y).toBeCloseTo(-1000, 6);
  });

  it('slides along a wall: the blocked axis is dropped, the free one survives', () => {
    // Facing north-east into the wall: forward is blocked, so the step keeps
    // only its sideways component (walking right = east along the wall).
    const start = orbitFromWalkPose({
      eyeMm: { x: 1000, y: -500, z: 1600 },
      headingDeg: 90,
      pitchDownDeg: 4,
    });
    const slid = walkStepAvoiding(start, 600, 600, solid);
    const eye = walkPoseOf(slid).eyeMm;
    expect(eye.y).toBeCloseTo(-500, 6); // did not enter the band
    expect(eye.x).toBeCloseTo(1600, 6); // moved along it
  });

  it('walkObstaclesOf takes one storey’s walls and their openings only', () => {
    const { house } = makeTwoRoomPlanWithOpenings();
    const gf = walkObstaclesOf(house, fixedId('storey', 'GF'));
    expect(gf.walls).toHaveLength(5);
    expect(gf.openings).toHaveLength(2);
    expect(walkObstaclesOf(house, null)).toEqual({ walls: [], openings: [] });
    expect(walkObstaclesOf(house, 'storey_nope').walls).toEqual([]);
  });
});
