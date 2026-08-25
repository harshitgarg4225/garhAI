/**
 * coords.ts — THE conversion boundary. Every mm↔world↔screen transform in the
 * canvas lives in this file and nowhere else.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE THREE SPACES
 * ────────────────────────────────────────────────────────────────────────────
 *
 * 1. MODEL SPACE — plot-local **integer millimetres** (`Pt` from `@garh/model`).
 *    Origin at the plot's SW corner, +X east, +Y north, +Z up (elevation above
 *    the plot datum: `Levels.plinthMm`, `LevelData.fflMm`, sills, lintels).
 *    This is the only space an op payload may be expressed in.
 *
 * 2. WORLD SPACE — Three.js **floats**, Y-up, 1 unit = 1 metre:
 *
 *        worldX = +mmX × 0.001
 *        worldY = +mmZ × 0.001      (elevation)
 *        worldZ = −mmY × 0.001      (north is −Z)
 *
 *    The Z flip is what makes a right-handed Y-up scene show north upwards on
 *    an orthographic top view whose `up` vector is (0, 0, −1) — see
 *    `CameraRig`. Getting it wrong mirrors every plan, and mirrored plans pass
 *    every unit test that does not check handedness, which is why
 *    `coords.test.ts` checks the sign explicitly.
 *
 * 3. SCREEN SPACE — CSS pixels, and normalised device coordinates (NDC) in
 *    [−1, 1] with +Y **up**, which is what `Raycaster.setFromCamera` wants.
 *    Browser pointer events give +Y down; the flip happens exactly once, in
 *    {@link ndcFromPointer}.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE ROUNDING RULE
 * ────────────────────────────────────────────────────────────────────────────
 * Float → mm always rounds **half away from zero**, delegating to
 * `roundMm` in `packages/model/src/units.ts` (`x >= 0 ? floor(x + 0.5) :
 * −floor(−x + 0.5)`). Never `Math.round` — it is half-*up*, so it rounds −0.5
 * to −0 and would make a wall drawn westwards land one millimetre off from the
 * same wall drawn eastwards.
 *
 * Because the rule is symmetric about zero, `round(−v) === −round(v)`, and so
 * the north-axis sign flip in {@link worldToMm} introduces no bias. That
 * identity is load-bearing and is asserted in the specs.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IS ALLOWED TO BE A FLOAT
 * ────────────────────────────────────────────────────────────────────────────
 * View state — camera centre, `mmPerPx`, orbit angles, tween positions — is
 * float millimetres and may stay float forever; it never reaches an op. The
 * moment a value is destined for an op payload it goes through
 * {@link pointerToMm} or {@link snapPtMm} and comes out an integer. If you find
 * a `Math.round` or a `/ 304.8` anywhere else under `features/canvas`, it is a
 * bug in that module, not a missing feature here.
 */

import { Plane, Raycaster, Vector2, Vector3, type Camera } from 'three';

import { bbox as bboxOfPoints, ptRound, type Bbox, type Pt } from '@garh/model';

import { roundMm, snapMm } from '../../../lib/units';
import {
  MM_PER_WORLD_UNIT,
  PICK_TOLERANCE_PX,
  SNAP_COARSE_MM,
  SNAP_FINE_MM,
  WORLD_UNITS_PER_MM,
} from './constants';

export { snapMm, roundMm, SNAP_COARSE_MM, SNAP_FINE_MM, WORLD_UNITS_PER_MM, MM_PER_WORLD_UNIT };

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Anything with x/y/z numbers — `THREE.Vector3` satisfies this structurally. */
export interface Vec3Like {
  x: number;
  y: number;
  z: number;
}

/** A read-only view of the same. */
export interface ReadonlyVec3 {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/** Normalised device coordinates: both in [−1, 1], +Y up. */
export interface Ndc {
  readonly x: number;
  readonly y: number;
}

/** A point in CSS pixels, relative to the canvas element's top-left. */
export interface PixelPoint {
  readonly x: number;
  readonly y: number;
}

/** The canvas element's size in CSS pixels. */
export interface ViewportSizePx {
  readonly width: number;
  readonly height: number;
}

/** A float millimetre point. View state only — never an op payload. */
export interface PtF {
  readonly x: number;
  readonly y: number;
}

// ---------------------------------------------------------------------------
// Scalars
// ---------------------------------------------------------------------------

/** Millimetres → world units. Exact scaling, no rounding. */
export function mmToWorldScalar(mm: number): number {
  return mm * WORLD_UNITS_PER_MM;
}

/** World units → **float** millimetres. Use when you are not storing the value. */
export function worldToMmScalarF(world: number): number {
  return world * MM_PER_WORLD_UNIT;
}

/** World units → **integer** millimetres, half away from zero. */
export function worldToMmScalar(world: number): number {
  return roundMm(world * MM_PER_WORLD_UNIT);
}

// ---------------------------------------------------------------------------
// Points
// ---------------------------------------------------------------------------

/**
 * Model point (+ elevation) → world position.
 *
 * PERF: pass `out` from the render loop. Every call without it allocates a
 * `Vector3`, and §14's frame budget does not survive an allocation per wall per
 * frame. Modules that build geometry once at mount may omit it.
 */
export function mmToWorld(p: Pt | PtF, elevationMm = 0, out?: Vec3Like): Vec3Like {
  const target = out ?? new Vector3();
  target.x = p.x * WORLD_UNITS_PER_MM;
  target.y = elevationMm * WORLD_UNITS_PER_MM;
  target.z = -p.y * WORLD_UNITS_PER_MM;
  return target;
}

/** Component form of {@link mmToWorld}, for tight loops over flat arrays. */
export function mmToWorldXYZ(
  xMm: number,
  yMm: number,
  elevationMm: number,
  out: Vec3Like,
): Vec3Like {
  out.x = xMm * WORLD_UNITS_PER_MM;
  out.y = elevationMm * WORLD_UNITS_PER_MM;
  out.z = -yMm * WORLD_UNITS_PER_MM;
  return out;
}

/**
 * World position → model point, integer mm. The elevation component is dropped
 * — ask {@link worldToElevationMm} for it — because a `Pt` is a plan point and
 * silently smuggling a third coordinate into one is how plan/section drift
 * starts.
 */
export function worldToMm(v: ReadonlyVec3): Pt {
  return ptRound(v.x * MM_PER_WORLD_UNIT, -v.z * MM_PER_WORLD_UNIT);
}

/** World position → model point without rounding. View maths only. */
export function worldToMmF(v: ReadonlyVec3): PtF {
  return { x: v.x * MM_PER_WORLD_UNIT, y: -v.z * MM_PER_WORLD_UNIT };
}

/** World Y → elevation above datum, integer mm. */
export function worldToElevationMm(v: ReadonlyVec3): number {
  return roundMm(v.y * MM_PER_WORLD_UNIT);
}

// ---------------------------------------------------------------------------
// Snapping
// ---------------------------------------------------------------------------

/**
 * Snap a point onto a grid module. Returns integer mm.
 *
 * `moduleMm <= 0` means "no grid": the value is still rounded to whole mm,
 * because a `Pt` is integer mm by contract even when snapping is off. Turning
 * the grid off does not license a float coordinate.
 */
export function snapPtMm(p: Pt | PtF, moduleMm: number): Pt {
  return { x: snapMm(p.x, moduleMm), y: snapMm(p.y, moduleMm) };
}

/**
 * Snap `p` relative to `origin` rather than to the absolute grid — what a tool
 * wants while dragging a selection that did not start on a grid line, so the
 * drag preserves the offset instead of jerking the whole thing onto the module.
 */
export function snapPtRelativeMm(p: Pt | PtF, origin: Pt | PtF, moduleMm: number): Pt {
  return {
    x: origin.x + snapMm(p.x - origin.x, moduleMm),
    y: origin.y + snapMm(p.y - origin.y, moduleMm),
  };
}

/**
 * The orthogonal constraint every drawing tool needs: lock `to` onto the axis
 * of `from` it is furthest along. MVP walls are orthogonal (§5), so this is the
 * default; tools that allow a free angle simply do not call it.
 *
 * A perfect diagonal (|dx| === |dy|) locks to X — arbitrary, but deterministic,
 * which matters more than which axis wins.
 */
export function constrainOrtho(from: Pt, to: Pt): Pt {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  return Math.abs(dx) >= Math.abs(dy) ? { x: to.x, y: from.y } : { x: from.x, y: to.y };
}

/**
 * Move `from` exactly `lengthMm` towards `to`, staying on the axis. This is how
 * "type a number while drawing" (§12) commits: the mouse chose the direction,
 * the keyboard chose the length, and the result is still integer mm.
 *
 * Returns `from` unchanged when `to` is degenerate — a zero-length wall is a
 * `WALL_ZERO_LENGTH` rejection, and producing one here just moves the error
 * further from its cause.
 */
export function pointAtLengthMm(from: Pt, to: Pt, lengthMm: number): Pt {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return from;
  return ptRound(from.x + (dx / len) * lengthMm, from.y + (dy / len) * lengthMm);
}

// ---------------------------------------------------------------------------
// Screen ↔ NDC
// ---------------------------------------------------------------------------

/**
 * Pointer event coordinates → NDC.
 *
 * `rect` is the canvas element's bounding rect. PERF: the caller caches it
 * (`useCanvasControls` re-reads it on resize and scroll, not on move) —
 * `getBoundingClientRect()` forces layout, and calling it per `pointermove` is
 * a reliable way to lose the frame budget to style recalculation.
 */
export function ndcFromPointer(clientX: number, clientY: number, rect: DOMRectReadOnly): Ndc {
  const x = ((clientX - rect.left) / rect.width) * 2 - 1;
  // Browser Y grows downwards, NDC Y grows upwards. This is the only flip.
  const y = -(((clientY - rect.top) / rect.height) * 2 - 1);
  return { x, y };
}

/** Canvas-relative pixels → NDC, for callers that already subtracted the rect. */
export function ndcFromPixel(px: PixelPoint, size: ViewportSizePx): Ndc {
  return {
    x: (px.x / size.width) * 2 - 1,
    y: -((px.y / size.height) * 2 - 1),
  };
}

/** NDC → canvas-relative pixels. The inverse of {@link ndcFromPixel}. */
export function pixelFromNdc(ndc: Ndc, size: ViewportSizePx): PixelPoint {
  return {
    x: ((ndc.x + 1) / 2) * size.width,
    y: ((1 - ndc.y) / 2) * size.height,
  };
}

// ---------------------------------------------------------------------------
// Pointer → model, via the reference plane
// ---------------------------------------------------------------------------

/**
 * Module-scoped scratch. Reused on every call so that hovering allocates
 * nothing; safe because none of it escapes — every public function copies out
 * into a fresh plain object before returning.
 */
const WORLD_UP = /* @__PURE__ */ new Vector3(0, 1, 0);
const scratchRaycaster = /* @__PURE__ */ new Raycaster();
const scratchPlane = /* @__PURE__ */ new Plane(new Vector3(0, 1, 0), 0);
const scratchHit = /* @__PURE__ */ new Vector3();
const scratchNdc = /* @__PURE__ */ new Vector2();

export interface PointerToMmOptions {
  /** Elevation of the plane the pointer is projected onto. Default: datum (0). */
  readonly planeElevationMm?: number | undefined;
  /**
   * Grid module to snap to, in mm. Default {@link SNAP_COARSE_MM}. Pass `0` to
   * snap to nothing (the result is still whole millimetres).
   */
  readonly snapModuleMm?: number | undefined;
}

/**
 * Pointer → the model point under it, **unsnapped** but already integer mm.
 *
 * Returns `null` when the ray never reaches the plane. In 2D that cannot
 * happen: the orthographic camera looks straight down. In 3D it happens
 * whenever the pointer is above the horizon, and a tool that ignores the null
 * places a wall at whatever garbage a parallel-ray intersection produces.
 */
export function pointerToMmRaw(
  ndc: Ndc,
  camera: Camera,
  planeElevationMm = 0,
): Pt | null {
  scratchNdc.set(ndc.x, ndc.y);
  scratchRaycaster.setFromCamera(scratchNdc, camera);
  // Plane y = elevation, normal +Y ⇒ constant = −elevation.
  scratchPlane.set(WORLD_UP, -mmToWorldScalar(planeElevationMm));
  const hit = scratchRaycaster.ray.intersectPlane(scratchPlane, scratchHit);
  if (hit === null) return null;
  return worldToMm(hit);
}

/**
 * THE tool-facing helper: pointer → snapped integer-mm model point, ready to be
 * an op payload. One call, no room for a module to invent its own snapping.
 */
export function pointerToMm(
  ndc: Ndc,
  camera: Camera,
  options: PointerToMmOptions = {},
): Pt | null {
  const raw = pointerToMmRaw(ndc, camera, options.planeElevationMm ?? 0);
  if (raw === null) return null;
  return snapPtMm(raw, options.snapModuleMm ?? SNAP_COARSE_MM);
}

/**
 * The world-space ray under the pointer. Exposed because the picker needs it;
 * the returned raycaster is the shared scratch instance and is valid only until
 * the next call into this module. Do not store it.
 */
export function raycasterFromNdc(ndc: Ndc, camera: Camera): Raycaster {
  scratchNdc.set(ndc.x, ndc.y);
  scratchRaycaster.setFromCamera(scratchNdc, camera);
  return scratchRaycaster;
}

// ---------------------------------------------------------------------------
// Pixels ↔ millimetres at a given zoom
// ---------------------------------------------------------------------------

/** CSS pixels → millimetres at a zoom level. */
export function pxToMm(px: number, mmPerPx: number): number {
  return px * mmPerPx;
}

/** Millimetres → CSS pixels at a zoom level. */
export function mmToPx(mm: number, mmPerPx: number): number {
  return mm / mmPerPx;
}

/**
 * Click slop in millimetres at the current zoom. Constant on screen, which is
 * the only definition of "close enough to click" that behaves the same at 1:20
 * and at 1:500.
 */
export function pickToleranceMm(mmPerPx: number, px: number = PICK_TOLERANCE_PX): number {
  return px * mmPerPx;
}

// ---------------------------------------------------------------------------
// Bounding boxes
// ---------------------------------------------------------------------------

/** Bbox of a point list, in mm. Re-exported so callers need one import. */
export const bboxOfMm = bboxOfPoints;

/** Union of two bboxes; either may be null. */
export function bboxUnion(a: Bbox | null, b: Bbox | null): Bbox | null {
  if (a === null) return b;
  if (b === null) return a;
  return {
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  };
}

/** Centre of a bbox, float mm (this is view maths, not geometry). */
export function bboxCentreMm(b: Bbox): PtF {
  return { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 };
}

/** True when the box has no extent in either axis. */
export function bboxIsEmpty(b: Bbox): boolean {
  return b.maxX <= b.minX && b.maxY <= b.minY;
}
