/**
 * types.ts — what a measurement IS.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * A MEASUREMENT IS NOT AN ELEMENT OF THE MODEL
 * ────────────────────────────────────────────────────────────────────────────
 * It appends no op, changes no geometry, produces no undo entry and never
 * reaches the server. `features/canvas/tools/measureTool.ts` already states the
 * reason for the ephemeral case and it holds for the persisted one: a
 * measurement in the version timeline is noise in the one place a project's
 * history has to stay readable.
 *
 * What this module adds on top of that tool is PERSISTENCE — the number stays
 * on the drawing until it is dismissed — plus the two readings an architect
 * asks for that a polyline cannot answer: an angle at a corner and the area of
 * a region.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THE ID CARRIES A PREFIX
 * ────────────────────────────────────────────────────────────────────────────
 * Measurements are pick targets (see `scene.ts`), and `PickTarget.id` is shared
 * with every other clickable thing on the canvas. `{@link MEASURE_ID_PREFIX}`
 * keeps this namespace disjoint from the model's `{type}_{ulid}` element ids
 * AND from the dimension layer's `dim:…` handles, so a measure pick can never
 * be mistaken for either — see `MEASURE_PICK_KIND` in `scene.ts` for the full
 * argument, and `scene.test.ts` for the gate that keeps it true.
 *
 * Coordinates are plot-local integer millimetres, like everything else that
 * crosses this app's boundaries.
 */

import type { Pt } from '@garh/model';

/**
 * The three readings.
 *
 * `distance` covers both the two-point case and the chain: a chain is the same
 * measurement with more points, so there is one kind rather than two that would
 * have to agree about their per-segment arithmetic.
 */
export type MeasureKind = 'distance' | 'angle' | 'area';

export const MEASURE_KINDS: readonly MeasureKind[] = ['distance', 'angle', 'area'];

/** Every measurement id starts with this. Disjoint from ids by construction. */
export const MEASURE_ID_PREFIX = 'measure:';

/** True for an id minted by this feature. The pick router's only question. */
export function isMeasureId(id: string): boolean {
  return id.startsWith(MEASURE_ID_PREFIX);
}

/**
 * A committed measurement, kept on the drawing until dismissed.
 *
 * `points` is validated by construction, not by trust:
 *   distance  ≥ 2 points, in click order
 *   angle     exactly 3 — arm, VERTEX, arm (the middle point is the corner)
 *   area      ≥ 3, an open ring; the closing edge is implied, never stored
 *     twice, so `points[0] !== points[n-1]` and the area helpers may assume it.
 */
export interface Measurement {
  readonly id: string;
  readonly kind: MeasureKind;
  readonly points: readonly Pt[];
  /** Storey it was taken on. Measurements are hidden on other storeys. */
  readonly storeyId: string | null;
  /** Epoch ms, for stable ordering in the panel. */
  readonly createdAt: number;
}

/**
 * The in-progress measurement — what the rubber band draws.
 *
 * Separate from {@link Measurement} on purpose: a draft may be degenerate (one
 * point, no cursor) and a committed measurement may not, so the layer that
 * renders both cannot be handed one type that lies about which invariants hold.
 */
export interface MeasureDraft {
  readonly kind: MeasureKind;
  /** Points already clicked. */
  readonly points: readonly Pt[];
  /** Snapped pointer position, or null before the pointer has moved. */
  readonly cursor: Pt | null;
  /** True when the cursor is on the first point and a click would close (area). */
  readonly willClose: boolean;
}
