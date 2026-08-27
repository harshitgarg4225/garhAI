/**
 * frame.ts — pure derivation: sun state → everything the lights need.
 *
 * `SunLight.tsx` is a thin copier of these numbers onto THREE objects; every
 * decision (north rotation, direction vector, intensities, tint, the
 * below-horizon rule) is here, where vitest can reach it without a GPU.
 *
 * NORTH. `plot.northDeg` is "rotation of TRUE north from model +Y, measured
 * clockwise" (model.ts). Solar azimuth is measured clockwise from TRUE north.
 * So the sun's azimuth *in model axes, clockwise from +Y* is simply
 * `azimuthDeg + northDeg` — rotating the plot's north rotates the light with
 * it, which is the §8 requirement. `direction.test.ts` pins both identities.
 */

import type { PtF3 } from '../core';
import { istToUtcMs, solarPosition, type CalendarDate, type SolarPosition } from './solar';

const DEG = Math.PI / 180;

/** Everything `SunLight` writes to the scene. Plain data, no three. */
export interface SunFrame {
  /** Unit vector TOWARD the sun, model axes (x east, y north, z up). */
  readonly dirModel: PtF3;
  readonly aboveHorizon: boolean;
  /** Directional light intensity (0 when below horizon). */
  readonly sunIntensity: number;
  /** Hemisphere fill intensity — never 0, so night is dim, not void. */
  readonly hemiIntensity: number;
  /** Sun tint, linear rgb 0..1 — warms as the sun drops. */
  readonly sunColor: readonly [number, number, number];
  /** Pass-through of the raw solar answer, for the compass readout. */
  readonly solar: SolarPosition;
  /** Azimuth in model axes (clockwise from model +Y), for the compass needle. */
  readonly modelAzimuthDeg: number;
}

/**
 * Unit vector toward the sun in model axes for a solar azimuth/elevation and
 * a plot north rotation. Exported separately because it is the piece the
 * direction spec exercises hardest.
 */
export function sunDirectionModel(
  azimuthDeg: number,
  elevationDeg: number,
  northDeg: number,
): PtF3 {
  const am = (azimuthDeg + northDeg) * DEG; // clockwise from model +Y (north)
  const el = elevationDeg * DEG;
  const cosEl = Math.cos(el);
  return {
    x: Math.sin(am) * cosEl,
    y: Math.cos(am) * cosEl,
    z: Math.sin(el),
  };
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

/**
 * Presentation curve: intensities and tint from the apparent elevation.
 * These are lighting values, not physics — chosen so noon reads bright with
 * legible soft shadows and dusk reads warm and long-shadowed. They are pinned
 * loosely by the spec (monotonic in elevation, zero sun below horizon).
 */
export function computeSunFrame(
  day: CalendarDate,
  minutesOfDay: number,
  latDeg: number,
  lonDeg: number,
  northDeg: number,
): SunFrame {
  const solar = solarPosition(istToUtcMs(day, minutesOfDay), latDeg, lonDeg);
  const aboveHorizon = solar.apparentElevationDeg > 0;
  const h = clamp01(Math.sin(Math.max(0, solar.apparentElevationDeg) * DEG));

  // Warmth: white overhead → amber at the horizon.
  const warm = 1 - clamp01(solar.apparentElevationDeg / 30);
  const sunColor: [number, number, number] = [1, 0.95 - 0.3 * warm, 0.88 - 0.5 * warm];

  return {
    dirModel: sunDirectionModel(solar.azimuthDeg, solar.elevationDeg, northDeg),
    aboveHorizon,
    sunIntensity: aboveHorizon ? 0.6 + 2.0 * Math.pow(h, 0.6) : 0,
    hemiIntensity: 0.25 + 0.45 * h,
    sunColor,
    solar,
    modelAzimuthDeg: (((solar.azimuthDeg + northDeg) % 360) + 360) % 360,
  };
}

/** 8-way compass label for a TRUE-north azimuth: 0→N, 90→E, 225→SW… */
export function compassLabel(azimuthDeg: number): string {
  const names = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
  const idx = Math.round((((azimuthDeg % 360) + 360) % 360) / 45) % 8;
  return names[idx] ?? 'N';
}
