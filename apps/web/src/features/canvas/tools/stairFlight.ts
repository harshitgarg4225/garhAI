/**
 * stairFlight.ts — turning a storey height into a flight that is both legal
 * and buildable, deterministically.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE HARD CONSTRAINT
 * ────────────────────────────────────────────────────────────────────────────
 * `packages/model/src/validate.ts` rejects `stair.add` unless
 *
 *     | risersCount × riserMm − storeyHeightMm | ≤ 10 mm
 *
 * That is not a guideline: a flight that does not land exactly on the floor
 * above is a drawing that cannot be built. Because risers are integer
 * millimetres, most riser counts CANNOT satisfy it — 3000 / 17 = 176.47, and
 * 17 × 176 = 2992 (8 mm short, just inside) while 17 × 177 = 3009 (9 mm over,
 * also inside), whereas 3010 / 17 gives 177 × 17 = 3009, one millimetre short.
 * So this module searches riser counts rather than assuming one.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE SOFT CONSTRAINTS (NBC, mirrored from rulepacks/nbc-core.json)
 * ────────────────────────────────────────────────────────────────────────────
 *   riser ≤ 190 mm     `nbc.stair.riser.max`     Part 4, Cl. 4.4.3
 *   tread ≥ 250 mm     `nbc.stair.tread.min`     Part 4, Cl. 4.4.3
 *   width ≥ 900 mm     `nbc.stair.width.min`     Part 4, Cl. 4.4.2
 *   headroom ≥ 2100 mm `nbc.stair.headroom.min`  Part 4, Cl. 4.4.5
 *
 * plus the tradesman's comfort rule `550 ≤ 2R + T ≤ 700`, which is advisory and
 * labelled as such — it is not in any code and must not be presented as one.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * HEADROOM — WHAT IS ACTUALLY CHECKED, AND WHAT IS NOT
 * ────────────────────────────────────────────────────────────────────────────
 * `fold` cuts the stairwell out of the slab above using the stair's FULL
 * footprint (`stairFootprintPolygon`), so in this model nobody ever walks under
 * a covered part of their own flight: the clear height under the soffit is
 *
 *     headroom = storeyHeight − slabThickness
 *
 * and that is what is checked. What is NOT checked here is the case where a
 * later edit shrinks the well below the footprint, or where a beam crosses the
 * flight — neither exists in the MVP model, and inventing a number for them
 * would be worse than saying so. When Phase 5 extrudes the real soffit, the 3D
 * clash check is the honest place for it.
 */

import type { StairKind, StairLanding } from '@garh/model';

import { roundMm } from '../../../lib/units';
import {
  COMFORT_2R_T_MAX_MM,
  COMFORT_2R_T_MIN_MM,
  MAX_RISERS,
  MIN_RISERS,
  NBC_HEADROOM_MIN_MM,
  NBC_RISER_MAX_MM,
  NBC_STAIR_WIDTH_MIN_MM,
  NBC_TREAD_MIN_MM,
  PREFERRED_RISER_MM,
  STAIR_RISE_TOLERANCE_MM,
} from './constants';

/** Clear gap between the two flights of a dogleg/U — one brick module. */
export const STAIR_WELL_GAP_MM = 115;

/** Treads are drawn and set out in 5 mm steps; a 291 mm tread is a fiction. */
const TREAD_STEP_MM = 5;

export interface FlightInput {
  readonly storeyHeightMm: number;
  readonly kind: StairKind;
  readonly widthMm: number;
  /** Riser the search aims for. Defaults to 165 mm. */
  readonly preferredRiserMm?: number | undefined;
  /** Explicit tread (from the inspector); otherwise derived from the comfort rule. */
  readonly treadMm?: number | undefined;
  /** Slab thickness above, for the headroom number. */
  readonly slabThicknessMm?: number | undefined;
}

export interface FlightSolution {
  readonly risersCount: number;
  readonly riserMm: number;
  readonly treadMm: number;
  /** `risersCount × riserMm` — what the model invariant compares. */
  readonly totalRiseMm: number;
  /** Signed error against the storey height, mm. Always within ±10. */
  readonly riseErrorMm: number;
  /** Horizontal run of one flight, mm. */
  readonly goingMm: number;
  readonly landing: StairLanding | null;
  /** Risers in the first flight (before the landing). */
  readonly risersToLanding: number;
  readonly headroomMm: number;
  readonly comfort2rt: number;
}

/** Why a flight could not be found — surfaced as an inline block, not a crash. */
export interface FlightFailure {
  readonly reason: string;
  readonly fix: string;
}

export type FlightResult =
  | { readonly ok: true; readonly flight: FlightSolution }
  | { readonly ok: false; readonly failure: FlightFailure };

/** Tread implied by the comfort rule for a given riser, rounded to 5 mm. */
export function comfortTreadMm(riserMm: number): number {
  const target = (COMFORT_2R_T_MIN_MM + COMFORT_2R_T_MAX_MM) / 2 - 2 * riserMm;
  const stepped = roundMm(target / TREAD_STEP_MM) * TREAD_STEP_MM;
  return Math.max(NBC_TREAD_MIN_MM, stepped);
}

/**
 * Find the flight for a storey height.
 *
 * Deterministic: the candidate riser counts are visited in ascending order and
 * a candidate replaces the incumbent only on a STRICT improvement, so the same
 * inputs always produce the same flight — which matters because this feeds an
 * op payload, and an op payload that varies run to run breaks replay.
 */
export function solveFlight(input: FlightInput): FlightResult {
  const height = input.storeyHeightMm;
  if (!Number.isSafeInteger(height) || height <= 0) {
    return {
      ok: false,
      failure: {
        reason: 'This floor has no height set, so the stair cannot be worked out.',
        fix: 'Set the floor-to-floor height on the storey first.',
      },
    };
  }

  const preferred = input.preferredRiserMm ?? PREFERRED_RISER_MM;
  let best: { risersCount: number; riserMm: number; error: number; score: number } | null = null;

  for (let n = MIN_RISERS; n <= MAX_RISERS; n++) {
    const riser = roundMm(height / n);
    if (riser <= 0) continue;
    if (riser > NBC_RISER_MAX_MM) continue;
    const error = n * riser - height;
    if (Math.abs(error) > STAIR_RISE_TOLERANCE_MM) continue;

    // Comfort first (a riser near the preferred one), exactness second. The
    // weight of 2 is arbitrary but fixed: what matters is that the comparison
    // is strict and the loop ascends, so the choice never depends on
    // iteration order or on floating-point luck.
    const score = Math.abs(riser - preferred) * 2 + Math.abs(error);
    if (best === null || score < best.score) {
      best = { risersCount: n, riserMm: riser, error, score };
    }
  }

  if (best === null) {
    return {
      ok: false,
      failure: {
        reason: `No flight fits a ${String(height)} mm floor height with risers of 190 mm or less.`,
        fix: 'Adjust the storey height by a few millimetres, or set the risers by hand.',
      },
    };
  }

  const treadMm = input.treadMm ?? comfortTreadMm(best.riserMm);
  const risersToLanding =
    input.kind === 'straight' ? best.risersCount : Math.ceil(best.risersCount / 2);
  // A flight of N risers has N−1 treads: the last riser lands on the floor above.
  const goingMm = Math.max(1, risersToLanding - 1) * treadMm;
  const slab = input.slabThicknessMm ?? 0;

  return {
    ok: true,
    flight: {
      risersCount: best.risersCount,
      riserMm: best.riserMm,
      treadMm,
      totalRiseMm: best.risersCount * best.riserMm,
      riseErrorMm: best.error,
      goingMm,
      landing: landingFor(input.kind, input.widthMm),
      risersToLanding,
      headroomMm: height - slab,
      comfort2rt: 2 * best.riserMm + treadMm,
    },
  };
}

/**
 * The landing block for a stair kind.
 *
 * The widths are exactly what `stairFootprintPolygon` in `fold.ts` expects:
 * for a dogleg/U it reads `landing.widthMm` as the OVERALL width across both
 * flights, and for an L as the width of the return leg. Getting this wrong does
 * not fail a test — it silently produces a slab cut-out of the wrong size.
 */
export function landingFor(kind: StairKind, widthMm: number): StairLanding | null {
  if (kind === 'straight') return null;
  if (kind === 'L') return { widthMm, depthMm: widthMm };
  return { widthMm: 2 * widthMm + STAIR_WELL_GAP_MM, depthMm: widthMm };
}

// ---------------------------------------------------------------------------
// Code checks — the advisory chips the stair tool shows while you place
// ---------------------------------------------------------------------------

export interface FlightIssue {
  readonly id: string;
  readonly severity: 'info' | 'warning' | 'error';
  readonly text: string;
  readonly cite: string | null;
  readonly fix: string | null;
}

/**
 * NBC + comfort checks on a solved flight. Every one is advisory at this point
 * — compliance never blocks (golden rule 5), it informs — and the same rules
 * run authoritatively in the rules engine once the op lands.
 */
export function flightIssues(flight: FlightSolution, widthMm: number): FlightIssue[] {
  const out: FlightIssue[] = [];

  if (flight.riserMm > NBC_RISER_MAX_MM) {
    out.push({
      id: 'nbc.stair.riser.max',
      severity: 'error',
      text: `Risers are ${String(flight.riserMm)} mm — the limit for a dwelling is ${String(NBC_RISER_MAX_MM)} mm.`,
      cite: 'Part 4, Cl. 4.4.3',
      fix: 'Add a riser to the flight so each one is 190 mm or less.',
    });
  }
  if (flight.treadMm < NBC_TREAD_MIN_MM) {
    out.push({
      id: 'nbc.stair.tread.min',
      severity: 'error',
      text: `Treads are ${String(flight.treadMm)} mm — a dwelling stair needs at least ${String(NBC_TREAD_MIN_MM)} mm.`,
      cite: 'Part 4, Cl. 4.4.3',
      fix: 'Lengthen the flight so each tread is at least 250 mm.',
    });
  }
  if (widthMm < NBC_STAIR_WIDTH_MIN_MM) {
    out.push({
      id: 'nbc.stair.width.min',
      severity: 'error',
      text: `The flight is ${String(widthMm)} mm wide — a dwelling stair needs at least ${String(NBC_STAIR_WIDTH_MIN_MM)} mm.`,
      cite: 'Part 4, Cl. 4.4.2',
      fix: 'Widen the stair to at least 900 mm clear.',
    });
  }
  if (flight.headroomMm > 0 && flight.headroomMm < NBC_HEADROOM_MIN_MM) {
    out.push({
      id: 'nbc.stair.headroom.min',
      severity: 'warning',
      text: `Only ${String(flight.headroomMm)} mm of headroom under the floor above — at least ${String(NBC_HEADROOM_MIN_MM)} mm is needed.`,
      cite: 'Part 4, Cl. 4.4.5',
      fix: 'Raise the storey height, or thin the slab, to clear 2.10 m above the nosing line.',
    });
  }
  if (flight.comfort2rt < COMFORT_2R_T_MIN_MM || flight.comfort2rt > COMFORT_2R_T_MAX_MM) {
    out.push({
      id: 'comfort.2r-t',
      severity: 'info',
      text: `2 × riser + tread is ${String(flight.comfort2rt)} mm; stairs feel best between ${String(COMFORT_2R_T_MIN_MM)} and ${String(COMFORT_2R_T_MAX_MM)} mm.`,
      cite: 'Rule of thumb, not a code requirement',
      fix: 'Adjust the tread depth a little.',
    });
  }
  return out;
}
