/**
 * orbitOps.ts — pure 3D-navigation maths over the rig's own `Orbit3D`.
 *
 * WHY THIS LIVES UNDER `sun/`: Phase-5 path ownership split the canvas by
 * directory, and this agent owns `sun/**` and `materials/**` only. Navigation
 * shares nothing with the solar code except the 3D view it serves; move this
 * `nav/` folder to `features/canvas/nav/` unchanged when the integrator owns
 * that path (same pattern as the Phase-4 plan renderer under
 * `pages/project/plan/` — see the DECISIONS.md row).
 *
 * EVERYTHING HERE IS THE RIG'S VOCABULARY (inherited fact 2). There is no
 * second camera state: walk mode is *expressed as an `Orbit3D`* — a short
 * look-ahead target 2 m in front of the eye — so `CameraRig`, `mmPerPx`-based
 * pick tolerance, and the 2D↔3D Tab switch all keep working untouched.
 *
 * WALK CAN LOOK UP (since 2026-09-16). An orbit camera never goes under its
 * target (`MAX_ORBIT_POLAR_DEG` = 89°), and a walk camera expressed through
 * it could only look level-or-down. `Orbit3D.polarLimitDeg` is the rig's own
 * answer: `orbitFromWalkPose` stamps the walk limit (150° — 60° above level)
 * on the orbits it builds, `orbitEyeMm` honours it, and every orbit GESTURE
 * rebuilds through `clampOrbit`, which drops the field. Still one camera.
 *
 * WALK COLLIDES (since 2026-09-16). `walkStepAvoiding` refuses a step whose
 * eye would land inside a wall of the active storey (half thickness plus a
 * body radius), slides along the wall when only one axis is blocked, and
 * lets the walker through DOORS — a window's sill stops you, a door does
 * not. `walkStep` stays as the pure, unobstructed step for the maths specs.
 */

import type { HouseModel, Opening, Wall } from '@garh/model';

import { clampOrbit, orbitEyeMm, type Orbit3D, type PtF3 } from '../../core';

const DEG = Math.PI / 180;

// ---------------------------------------------------------------------------
// Constants (task contract: eye height 1600 mm, collision OFF in v1)
// ---------------------------------------------------------------------------

/** Standing eye height above the active storey's FFL, mm. */
export const WALK_EYE_HEIGHT_MM = 1600;

/** How far in front of the eye the walk camera's orbit target sits, mm. */
export const WALK_LOOK_DISTANCE_MM = 2000;

/** Walking speed. 1.6 m/s is a purposeful stroll. */
export const WALK_SPEED_MM_PER_S = 1600;

/** Shift multiplies the stroll into a site-visit stride. */
export const WALK_RUN_FACTOR = 3;

/** Mouse-look rate in walk mode. Gentler than orbit's 0.4°/px. */
export const WALK_TURN_DEG_PER_PX = 0.22;

/**
 * Pitch clamp, degrees below level: negative looks UP. −60 (a chajja from
 * the doorstep) to 89 (nearly at your feet) ↔ polar 150..1. Level is 0.
 */
export const WALK_MAX_PITCH_UP_DEG = 60;
export const WALK_MAX_PITCH_DOWN_DEG = 89;

/** The polar limit stamped on walk orbits: 90° + the look-up allowance. */
export const WALK_POLAR_LIMIT_DEG = 90 + WALK_MAX_PITCH_UP_DEG;

/** How close the eye may come to a wall face when walking, mm. A shoulder. */
export const WALK_BODY_RADIUS_MM = 300;

// ---------------------------------------------------------------------------
// Orbit-mode verbs
// ---------------------------------------------------------------------------

/**
 * Dolly the camera about a fixed anchor point — "zoom to cursor" for a
 * perspective camera. Scaling the whole eye/target pair about the anchor
 * preserves the view direction exactly, so the point under the cursor stays
 * under the cursor. `factor > 1` moves away (matches `wheelZoomFactor`).
 */
export function dollyOrbitAboutAnchor(orbit: Orbit3D, factor: number, anchorMm: PtF3): Orbit3D {
  const f = Math.max(0.01, factor);
  return clampOrbit({
    ...orbit,
    targetMm: {
      x: anchorMm.x + (orbit.targetMm.x - anchorMm.x) * f,
      y: anchorMm.y + (orbit.targetMm.y - anchorMm.y) * f,
      z: anchorMm.z + (orbit.targetMm.z - anchorMm.z) * f,
    },
    distanceMm: orbit.distanceMm * f,
  });
}

// ---------------------------------------------------------------------------
// Walk mode, expressed as Orbit3D
// ---------------------------------------------------------------------------

/** A walk pose in human terms; converted to/from `Orbit3D` losslessly. */
export interface WalkPose {
  /** Eye position, model mm. `z` is the eye, not the floor. */
  readonly eyeMm: PtF3;
  /** Facing direction, degrees CCW from +X (the orbit azimuth convention). */
  readonly headingDeg: number;
  /** Degrees looking below level, in [−60, 89]: negative looks UP. */
  readonly pitchDownDeg: number;
}

function clampPitch(pitchDownDeg: number): number {
  return Math.min(WALK_MAX_PITCH_DOWN_DEG, Math.max(-WALK_MAX_PITCH_UP_DEG, pitchDownDeg));
}

/** Build the orbit that puts the eye at `pose` looking along its heading. */
export function orbitFromWalkPose(pose: WalkPose): Orbit3D {
  const pitch = clampPitch(pose.pitchDownDeg);
  const polarDeg = 90 - pitch; // 60° up→150, level→90, straight down→1
  const azimuthDeg = (((pose.headingDeg - 180) % 360) + 360) % 360;
  const sinP = Math.sin(polarDeg * DEG);
  const cosP = Math.cos(polarDeg * DEG);
  const cosA = Math.cos(azimuthDeg * DEG);
  const sinA = Math.sin(azimuthDeg * DEG);
  // eye = target + R·L  ⇒  target = eye − R·L, R = (sinP·cosA, sinP·sinA, cosP)
  return {
    targetMm: {
      x: pose.eyeMm.x - sinP * cosA * WALK_LOOK_DISTANCE_MM,
      y: pose.eyeMm.y - sinP * sinA * WALK_LOOK_DISTANCE_MM,
      z: pose.eyeMm.z - cosP * WALK_LOOK_DISTANCE_MM,
    },
    distanceMm: WALK_LOOK_DISTANCE_MM,
    azimuthDeg,
    polarDeg,
    // The walk allowance: past 90 the eye sits under its look-ahead target,
    // i.e. it is looking up. Dropped by the next orbit gesture (clampOrbit).
    polarLimitDeg: WALK_POLAR_LIMIT_DEG,
  };
}

/** Read the walk pose back out of an orbit. Inverse of {@link orbitFromWalkPose}. */
export function walkPoseOf(orbit: Orbit3D): WalkPose {
  return {
    eyeMm: orbitEyeMm(orbit),
    headingDeg: (((orbit.azimuthDeg + 180) % 360) + 360) % 360,
    pitchDownDeg: clampPitch(90 - orbit.polarDeg),
  };
}

/**
 * Enter walk mode from wherever the orbit camera is: keep the horizontal
 * facing, drop the eye to standing height above the active storey's floor.
 */
export function enterWalkOrbit(orbit: Orbit3D, floorElevationMm: number): Orbit3D {
  const eye = orbitEyeMm(orbit);
  return orbitFromWalkPose({
    eyeMm: { x: eye.x, y: eye.y, z: floorElevationMm + WALK_EYE_HEIGHT_MM },
    headingDeg: orbit.azimuthDeg + 180, // keep looking the way we were
    pitchDownDeg: 4, // just under level: reads as "standing", not "staring at feet"
  });
}

/**
 * WASD step: move the eye in the ground plane along the current heading.
 * The PURE step — no obstacles; `walkStepAvoiding` is what the hook uses.
 */
export function walkStep(orbit: Orbit3D, forwardMm: number, rightMm: number): Orbit3D {
  const pose = walkPoseOf(orbit);
  const h = pose.headingDeg * DEG;
  const fx = Math.cos(h);
  const fy = Math.sin(h);
  // Right of the facing direction: heading − 90°.
  const rx = Math.sin(h);
  const ry = -Math.cos(h);
  return orbitFromWalkPose({
    ...pose,
    eyeMm: {
      x: pose.eyeMm.x + fx * forwardMm + rx * rightMm,
      y: pose.eyeMm.y + fy * forwardMm + ry * rightMm,
      z: pose.eyeMm.z,
    },
  });
}

/** Mouse-look: drag right looks right, drag up looks up (past level, to −60°). */
export function walkTurn(orbit: Orbit3D, dxPx: number, dyPx: number): Orbit3D {
  const pose = walkPoseOf(orbit);
  return orbitFromWalkPose({
    ...pose,
    headingDeg: pose.headingDeg - dxPx * WALK_TURN_DEG_PER_PX,
    pitchDownDeg: clampPitch(pose.pitchDownDeg + dyPx * WALK_TURN_DEG_PER_PX),
  });
}

// ---------------------------------------------------------------------------
// Collision — walls stop you, doors let you through
// ---------------------------------------------------------------------------

/** What a walker can bump into: one storey's walls and their openings. */
export interface WalkObstacles {
  readonly walls: readonly Wall[];
  readonly openings: readonly Opening[];
}

/** The obstacles of `storeyId` (or none for `null`) — the active storey's. */
export function walkObstaclesOf(house: HouseModel, storeyId: string | null): WalkObstacles {
  if (storeyId === null) return { walls: [], openings: [] };
  const walls = house.walls.filter((w) => w.storeyId === storeyId);
  const wallIds = new Set(walls.map((w) => w.id));
  return { walls, openings: house.openings.filter((o) => wallIds.has(o.wallId)) };
}

/**
 * Is a plan point inside a wall's body (half thickness + the body radius)?
 * A DOOR span on that wall — sill 0, the leaf swung open in the mind's eye —
 * is passable; windows and ventilators keep their sill, so they still block.
 */
export function pointBlockedByWalls(
  p: { readonly x: number; readonly y: number },
  obstacles: WalkObstacles,
  radiusMm: number = WALK_BODY_RADIUS_MM,
): boolean {
  for (const wall of obstacles.walls) {
    const dx = wall.b.x - wall.a.x;
    const dy = wall.b.y - wall.a.y;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) continue;
    const t = ((p.x - wall.a.x) * dx + (p.y - wall.a.y) * dy) / lenSq;
    const tc = Math.max(0, Math.min(1, t));
    const nx = wall.a.x + tc * dx - p.x;
    const ny = wall.a.y + tc * dy - p.y;
    const reach = wall.thicknessMm / 2 + radiusMm;
    if (nx * nx + ny * ny > reach * reach) continue;
    // Inside the wall band. A door here lets the walker through when the
    // whole body fits in the leaf's clear width.
    const alongMm = tc * Math.sqrt(lenSq);
    const throughDoor = obstacles.openings.some(
      (o) =>
        o.wallId === wall.id &&
        o.kind === 'door' &&
        alongMm >= o.offsetMm - o.widthMm / 2 + radiusMm &&
        alongMm <= o.offsetMm + o.widthMm / 2 - radiusMm,
    );
    if (!throughDoor) return true;
  }
  return false;
}

/**
 * Does the straight path `from`→`to` pass through a wall? Sampled every
 * body radius (plus the end point), so a long stride cannot tunnel through
 * a 115 mm wall whose band is narrower than the step.
 */
export function pathBlockedByWalls(
  from: { readonly x: number; readonly y: number },
  to: { readonly x: number; readonly y: number },
  obstacles: WalkObstacles,
  radiusMm: number = WALK_BODY_RADIUS_MM,
): boolean {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / Math.max(1, radiusMm)));
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps;
    if (pointBlockedByWalls({ x: from.x + dx * t, y: from.y + dy * t }, obstacles, radiusMm)) {
      return true;
    }
  }
  return false;
}

/**
 * `walkStep`, refused or slid when a wall is in the way: the full step if
 * its path is clear; else forward-only, else sideways-only (sliding along
 * the wall you are brushing); else stay put. The eye never enters a wall.
 */
export function walkStepAvoiding(
  orbit: Orbit3D,
  forwardMm: number,
  rightMm: number,
  obstacles: WalkObstacles,
  radiusMm: number = WALK_BODY_RADIUS_MM,
): Orbit3D {
  const from = walkPoseOf(orbit).eyeMm;
  const attempts: readonly [number, number][] = [
    [forwardMm, rightMm],
    [forwardMm, 0],
    [0, rightMm],
  ];
  for (const [f, r] of attempts) {
    if (f === 0 && r === 0) continue;
    const next = walkStep(orbit, f, r);
    if (!pathBlockedByWalls(from, walkPoseOf(next).eyeMm, obstacles, radiusMm)) return next;
  }
  return orbit;
}
