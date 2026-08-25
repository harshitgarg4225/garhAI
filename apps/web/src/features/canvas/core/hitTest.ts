/**
 * hitTest.ts — ONE picker, for both views.
 *
 * §12: "Shared raycast hit-testing; selection state common to both." A 2D click
 * and a 3D click enter {@link pickAt}, take the same path, and come out as the
 * same {@link PickHit}. The only difference between the modes is one number —
 * how deep a depth window priority is allowed to override distance in — and it
 * is a parameter, not a branch.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY PRIORITY, AND NOT JUST "NEAREST WINS"
 * ────────────────────────────────────────────────────────────────────────────
 * In the orthographic plan view every element is drawn on one plane. The ray
 * hits the room fill, the wall and the door leaf at the same distance, and the
 * order three returns them in is the order the objects happened to be
 * registered — i.e. mount order, i.e. arbitrary. Depth carries no information,
 * so it cannot be the rule. What the architect means is stated in
 * `PICK_PRIORITY`: the smaller and more deliberate the element, the more likely
 * it is the target. An opening beats its host wall; a dimension beats the room
 * it sits inside.
 *
 * In the perspective view depth *is* information — a near wall really should
 * beat a far room — so priority applies only inside a shallow depth window
 * (`DEPTH_EPSILON_WORLD_3D`, 50 mm) around the nearest hit. A door leaf and its
 * wall are inside that window. A room two metres behind is not.
 *
 * Setting the window to `Infinity` collapses the 3D rule into the 2D rule
 * exactly, which is why there is one implementation and not two.
 *
 * DETERMINISM. Ties break on distance, then on element id. Never on iteration
 * order: two coplanar rooms that flip which one is "on top" between frames
 * produce a selection that flickers under a still mouse, and that bug is
 * miserable to find later.
 */

import type { Camera, Intersection, Object3D } from 'three';

import type { Pt } from '@garh/model';

import {
  DEPTH_EPSILON_WORLD_3D,
  PICK_PRIORITY,
  PICK_TOLERANCE_PX,
  WORLD_UNITS_PER_MM,
  type CanvasMode,
  type PickKind,
} from './constants';
import {
  pickToleranceMm,
  pointerToMmRaw,
  raycasterFromNdc,
  worldToElevationMm,
  worldToMm,
  type Ndc,
} from './coords';
import { isEffectivelyVisible, type PickRegistry, type PickTarget } from './pickRegistry';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One resolved intersection, before the priority rules choose between them. */
export interface PickCandidate {
  readonly target: PickTarget;
  /** Ray distance in world units. Meaningless in 2D (everything is coplanar). */
  readonly distanceWorld: number;
  /** Where the ray met the element, in plot-local integer mm. */
  readonly pointMm: Pt;
  /** Elevation of that meeting point above the plot datum, integer mm. */
  readonly elevationMm: number;
  readonly object: Object3D | null;
  /** Instance slot for an `InstancedMesh` hit, else null. */
  readonly instanceId: number | null;
}

/**
 * What a pick returns. `kind: 'empty'` means the pointer is over the drawing
 * area but not over anything — which is a real answer, not a failure: it is how
 * the wall tool learns where to start a wall and how a click clears the
 * selection.
 */
export interface PickHit {
  readonly kind: PickKind | 'empty';
  /** Element id, or null for `'empty'`. */
  readonly id: string | null;
  readonly storeyId: string | null;
  /**
   * Plot-local integer mm. For `'empty'`, the point where the ray crosses the
   * reference plane. `null` only when the ray never reaches that plane, which
   * can only happen in 3D with the pointer above the horizon — tools must
   * check, and the null is deliberately not papered over with (0, 0).
   */
  readonly pointMm: Pt | null;
  readonly elevationMm: number;
  readonly distanceWorld: number;
  readonly object: Object3D | null;
  readonly instanceId: number | null;
}

export interface ResolveHitOptions {
  /**
   * How deep, in world units, priority may outrank distance. `Infinity` in 2D.
   */
  readonly depthEpsilonWorld: number;
}

export interface PickOptions {
  readonly registry: PickRegistry;
  readonly camera: Camera;
  readonly ndc: Ndc;
  readonly mode: CanvasMode;
  /** Elevation of the plane an `'empty'` pick reports. Usually the storey FFL. */
  readonly planeElevationMm?: number | undefined;
  /** Current zoom, so click slop is a constant number of screen pixels. */
  readonly mmPerPx?: number | undefined;
  /** Restrict to these kinds — the wall tool snapping to walls only. */
  readonly kinds?: readonly PickKind[] | undefined;
  /** Ignore these element ids — the thing currently being dragged. */
  readonly excludeIds?: ReadonlySet<string> | undefined;
  /** Restrict to one storey. `null`/omitted accepts every storey. */
  readonly storeyId?: string | null | undefined;
  /** Click slop in CSS pixels. Defaults to {@link PICK_TOLERANCE_PX}. */
  readonly tolerancePx?: number | undefined;
  /** Override the depth window. Defaults per mode. */
  readonly depthEpsilonWorld?: number | undefined;
}

// ---------------------------------------------------------------------------
// The rule
// ---------------------------------------------------------------------------

/** Priority of a candidate. Unknown kinds sort last rather than crash. */
export function pickPriority(kind: PickKind): number {
  return PICK_PRIORITY[kind] ?? 0;
}

/**
 * Total order over candidates: priority first, then nearer, then id. Exported
 * because it is the whole behavioural contract and the specs assert on it
 * directly. Returns <0 when `a` should win.
 */
export function comparePickCandidates(a: PickCandidate, b: PickCandidate): number {
  const pa = pickPriority(a.target.kind);
  const pb = pickPriority(b.target.kind);
  if (pa !== pb) return pb - pa;
  if (a.distanceWorld !== b.distanceWorld) return a.distanceWorld - b.distanceWorld;
  return a.target.id < b.target.id ? -1 : a.target.id > b.target.id ? 1 : 0;
}

/**
 * Choose the winning candidate. Pure — no camera, no scene, no three.
 *
 * 1. Find the nearest hit.
 * 2. Take every candidate within `depthEpsilonWorld` of it.
 * 3. Inside that band, {@link comparePickCandidates} decides.
 *
 * Candidates outside the band never compete, however high their priority — a
 * dimension on the far side of the building does not steal a click from the
 * wall you are pointing at.
 */
export function resolveHit(
  candidates: readonly PickCandidate[],
  options: ResolveHitOptions,
): PickCandidate | null {
  if (candidates.length === 0) return null;

  let nearest = Infinity;
  for (const c of candidates) {
    if (c.distanceWorld < nearest) nearest = c.distanceWorld;
  }
  const limit = nearest + options.depthEpsilonWorld;

  let best: PickCandidate | null = null;
  for (const c of candidates) {
    if (c.distanceWorld > limit) continue;
    if (best === null || comparePickCandidates(c, best) < 0) best = c;
  }
  return best;
}

/** The depth window for a mode. 2D is coplanar, so priority decides outright. */
export function depthEpsilonForMode(mode: CanvasMode): number {
  return mode === '2d' ? Infinity : DEPTH_EPSILON_WORLD_3D;
}

// ---------------------------------------------------------------------------
// The pick
// ---------------------------------------------------------------------------

/**
 * Reused across calls so that hovering allocates nothing of ours. (Three still
 * allocates one `Vector3` per intersection inside `raycast()`; that is its API
 * and there is no way around it, but the count is intersections-under-the-
 * cursor, not objects-in-the-scene.)
 */
const scratchIntersections: Intersection[] = [];
const scratchCandidates: PickCandidate[] = [];

/** An `'empty'` hit at a known point. */
export function emptyHit(pointMm: Pt | null, elevationMm: number): PickHit {
  return {
    kind: 'empty',
    id: null,
    storeyId: null,
    pointMm,
    elevationMm,
    distanceWorld: Infinity,
    object: null,
    instanceId: null,
  };
}

/**
 * THE picker. Every selection, hover, snap query and tool commit in Phase 4 and
 * Phase 5 comes through here.
 */
export function pickAt(options: PickOptions): PickHit {
  const {
    registry,
    camera,
    ndc,
    mode,
    planeElevationMm = 0,
    mmPerPx = 1,
    kinds,
    excludeIds,
    storeyId,
    tolerancePx = PICK_TOLERANCE_PX,
  } = options;

  const raycaster = raycasterFromNdc(ndc, camera);
  // Slop for zero-area geometry (dimension lines, snap points) expressed in
  // world units, derived from a constant number of screen pixels so it feels
  // the same at 1:20 and at 1:500.
  const toleranceWorld = pickToleranceMm(mmPerPx, tolerancePx) * WORLD_UNITS_PER_MM;
  raycaster.params.Line = { threshold: toleranceWorld };
  raycaster.params.Points = { threshold: toleranceWorld };

  scratchIntersections.length = 0;
  raycaster.intersectObjects(registry.objects(), false, scratchIntersections);

  scratchCandidates.length = 0;
  const kindFilter = kinds === undefined ? null : new Set<PickKind>(kinds);

  for (const intersection of scratchIntersections) {
    if (!isEffectivelyVisible(intersection.object)) continue;
    const target = registry.resolve(intersection);
    if (target === null) continue;
    if (kindFilter !== null && !kindFilter.has(target.kind)) continue;
    if (excludeIds !== undefined && excludeIds.has(target.id)) continue;
    if (
      storeyId !== undefined &&
      storeyId !== null &&
      target.storeyId !== null &&
      target.storeyId !== storeyId
    ) {
      continue;
    }
    scratchCandidates.push({
      target,
      distanceWorld: intersection.distance,
      pointMm: worldToMm(intersection.point),
      elevationMm: worldToElevationMm(intersection.point),
      object: intersection.object,
      instanceId: intersection.instanceId ?? null,
    });
  }

  const winner = resolveHit(scratchCandidates, {
    depthEpsilonWorld: options.depthEpsilonWorld ?? depthEpsilonForMode(mode),
  });

  if (winner === null) {
    // Nothing under the cursor — report where the reference plane is instead.
    // NOTE: this re-runs `setFromCamera` on the shared scratch raycaster, which
    // is why it happens *after* the intersection loop has been drained.
    return emptyHit(pointerToMmRaw(ndc, camera, planeElevationMm), planeElevationMm);
  }

  return {
    kind: winner.target.kind,
    id: winner.target.id,
    storeyId: winner.target.storeyId,
    pointMm: winner.pointMm,
    elevationMm: winner.elevationMm,
    distanceWorld: winner.distanceWorld,
    object: winner.object,
    instanceId: winner.instanceId,
  };
}

/**
 * Do two hits point at the same thing? The hover path compares with this and
 * writes to the selection store only when it changes — the difference between
 * one Zustand write per changed element and one per `pointermove`.
 */
export function sameHitTarget(a: PickHit | null, b: PickHit | null): boolean {
  if (a === null || b === null) return a === b;
  return a.kind === b.kind && a.id === b.id && a.instanceId === b.instanceId;
}
