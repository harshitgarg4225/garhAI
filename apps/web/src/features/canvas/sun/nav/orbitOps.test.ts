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

import {
  dollyOrbitAboutAnchor,
  enterWalkOrbit,
  orbitFromWalkPose,
  walkPoseOf,
  walkStep,
  walkTurn,
  WALK_EYE_HEIGHT_MM,
  WALK_LOOK_DISTANCE_MM,
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

  it('pitch clamps to the rig window [1°, 89°] — walk v1 looks level or down', () => {
    const up = orbitFromWalkPose({ ...pose, pitchDownDeg: -30 }); // asks to look UP
    expect(up.polarDeg).toBeLessThanOrEqual(89);
    const down = orbitFromWalkPose({ ...pose, pitchDownDeg: 200 });
    expect(down.polarDeg).toBeGreaterThanOrEqual(1);
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
});
