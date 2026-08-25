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
 * WALK PITCH IS CLAMPED, HONESTLY. `orbitEyeMm` clamps the polar angle to
 * [1°, 89°] (`MIN/MAX_ORBIT_POLAR_DEG` in core/constants), which means an
 * orbit camera can never sit below its target — and therefore a walk camera
 * expressed through it can look from level (89°) down to nearly straight down
 * (1°), but not UP. v1 accepts that and the HUD says so; lifting it is a
 * one-line widening of `MAX_ORBIT_POLAR_DEG` past 90 in the core, which the
 * integrator owns. Do not fork the camera to work around it — that is the
 * exact bug class fact 2 warns about.
 */

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

/** Pitch-down clamp, degrees below level. 1..89 ↔ polar 89..1 (see header). */
export const WALK_MIN_PITCH_DOWN_DEG = 1;
export const WALK_MAX_PITCH_DOWN_DEG = 89;

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
  /** Degrees looking below level, in [1, 89]. See the header for why not up. */
  readonly pitchDownDeg: number;
}

function clampPitch(pitchDownDeg: number): number {
  return Math.min(WALK_MAX_PITCH_DOWN_DEG, Math.max(WALK_MIN_PITCH_DOWN_DEG, pitchDownDeg));
}

/** Build the orbit that puts the eye at `pose` looking along its heading. */
export function orbitFromWalkPose(pose: WalkPose): Orbit3D {
  const pitch = clampPitch(pose.pitchDownDeg);
  const polarDeg = 90 - pitch; // level→89, straight down→1
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
 * Collision is OFF in v1 — the pose passes through walls, and the HUD says so.
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

/** Mouse-look: drag right looks right, drag up eases the pitch toward level. */
export function walkTurn(orbit: Orbit3D, dxPx: number, dyPx: number): Orbit3D {
  const pose = walkPoseOf(orbit);
  return orbitFromWalkPose({
    ...pose,
    headingDeg: pose.headingDeg - dxPx * WALK_TURN_DEG_PER_PX,
    pitchDownDeg: clampPitch(pose.pitchDownDeg + dyPx * WALK_TURN_DEG_PER_PX),
  });
}
