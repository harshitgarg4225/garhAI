/**
 * scene.ts — the measurement geometry, and the pick registration behind it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * BUG PATTERN 4, ANSWERED IN CODE RATHER THAN IN A COMMENT
 * ════════════════════════════════════════════════════════════════════════════
 * The furniture layer tagged its meshes for hit-testing, documented itself as
 * integrated, and never called `PickRegistry` — every placed item was invisible
 * to clicks, with no compile-time signal. The lesson taken here is not "be
 * careful"; it is that the registration has to be REACHABLE BY A TEST. So all
 * of it lives in this plain class:
 *
 *   · no React, no hooks, no `<Canvas>` — `new MeasureScene()` works in a spec;
 *   · `attach(registry)` is the only place a pick proxy is registered;
 *   · the pick quads are built from {@link measurementSegments}, the SAME
 *     function that emits the drawn lines, so "what you can click" and "what
 *     you can see" cannot drift apart;
 *   · `scene.test.ts` raycasts through the core's real `pickAt` and asserts the
 *     measurement's id comes back — and, negatively, that it does NOT when the
 *     registration is skipped.
 *
 * The React wrapper (`MeasureLayer.tsx`) owns nothing but lifecycle: it makes
 * one of these, attaches it, feeds it, and drops a `<primitive>` into the scene
 * graph. There is no second copy of any of this on the React side.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THE PICK KIND IS `'dimension'`
 * ════════════════════════════════════════════════════════════════════════════
 * `PickKind` is a CLOSED union in `canvas/core/constants.ts` and this module
 * may not extend it. That constraint is load-bearing rather than annoying:
 * `pickPriority` falls back to 0 for an unknown kind, and in the 2D view — where
 * every element is coplanar and priority decides outright — a measurement with
 * priority 0 would lose every tie to the room fill underneath it and be
 * silently unclickable. That is bug pattern 2 (a value outside the enum it is
 * compared against) wearing a different hat, so the kind is an in-enum constant
 * and `scene.test.ts` gates it: member of `PICK_KINDS`, and ranked above `room`
 * and `wall`.
 *
 * `'dimension'` is the honest borrow. A measurement IS an annotation drawn over
 * the plan, it wants exactly the dimension string's priority (90 — beats the
 * room it sits in, beats the wall it crosses), and the id namespaces are
 * disjoint by construction: dimension handles are `dim:…` and measurements are
 * `measure:…`. So a measure pick can never resolve to a dimension edit target
 * (`DimensionHandleIndex.lookup` returns null for an unknown id, and PlanPage
 * treats that as "ignore the click"), and until the page is wired to route
 * `measure:` ids here, the worst a click can do is nothing. See the handoff
 * note: a first-class `'measure'` kind is a two-line change to the core's kind
 * list and priority table, and this constant is the only thing that would move.
 */

import {
  BufferAttribute,
  BufferGeometry,
  Group,
  InstancedMesh,
  LineSegments,
  Matrix4,
  PlaneGeometry,
  Quaternion,
  Vector3,
} from 'three';
import type { LineBasicMaterial, MeshBasicMaterial } from 'three';

import type { Pt } from '@garh/model';

// From the modules rather than the `../canvas/core` barrel: the barrel is a
// runtime import of react-three-fiber, and this class has to be constructible
// in a spec with no renderer — which is the entire point of it being a class.
import { applyLayer, WORLD_UNITS_PER_MM, type PickKind } from '../canvas/core/constants';
import type { PickRegistry } from '../canvas/core/pickRegistry';
import { LineBuffer } from '../canvas/overlays/render/lines';
import { draftPolyline } from './geometry';
import type { MeasureDraft, Measurement } from './types';

// ---------------------------------------------------------------------------
// Contract constants
// ---------------------------------------------------------------------------

/** See the header. In-enum, and gated by `scene.test.ts`. */
export const MEASURE_PICK_KIND: PickKind = 'dimension';

/** Click target width across a measurement line, CSS px. Fitts' law, same as the core. */
export const MEASURE_PICK_WIDTH_PX = 14;

/** Half-length of the cross drawn at each measured point, CSS px. */
export const MEASURE_TICK_HALF_PX = 4;

/** Instances the pick mesh starts with. It grows; it never shrinks. */
const INITIAL_PICK_CAPACITY = 32;

// ---------------------------------------------------------------------------
// Segments — ONE definition of "the lines of a measurement"
// ---------------------------------------------------------------------------

export interface MeasureSegment {
  readonly a: Pt;
  readonly b: Pt;
}

/**
 * The line segments a measurement draws, in order.
 *
 * An `area` ring is stored open (`types.ts`), so its closing edge is added
 * here — once, in the one place both the renderer and the picker read.
 */
export function measurementSegments(
  kind: Measurement['kind'],
  points: readonly Pt[],
): MeasureSegment[] {
  const out: MeasureSegment[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (a === undefined || b === undefined) continue;
    out.push({ a, b });
  }
  if (kind === 'area' && points.length >= 3) {
    const first = points[0];
    const last = points[points.length - 1];
    if (first !== undefined && last !== undefined) out.push({ a: last, b: first });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------

export interface MeasureSceneInput {
  /** Already filtered to the storey being drawn — this class does not filter. */
  readonly measurements: readonly Measurement[];
  readonly draft: MeasureDraft | null;
  /** Current zoom, so ticks and click targets stay constant on screen. */
  readonly mmPerPx: number;
  /** The plane the plan is drawn at (the storey FFL). */
  readonly elevationMm: number;
  readonly selectedId: string | null;
  readonly visible: boolean;
}

/**
 * The three materials this layer draws with, HANDED IN rather than imported.
 *
 * `overlayMaterials.getOverlayMaterials()` is the right source for them and
 * `MeasureLayer` passes exactly that — but importing it here would pull the
 * `canvas/core` barrel, and the barrel is a runtime import of
 * react-three-fiber. That would put a second copy of three in every spec that
 * constructs this class (three says so out loud: "Multiple instances of
 * Three.js being imported"), and the whole value of this class is that it is
 * constructible without a renderer. So the dependency points the other way.
 */
export interface MeasureSceneMaterials {
  /** Committed measurements. The dimension ink. */
  readonly ink: LineBasicMaterial;
  /** The draft and the selected measurement. The brand colour. */
  readonly active: LineBasicMaterial;
  /** Invisible pick geometry: transparent, `colorWrite` off, still raycastable. */
  readonly pickProxy: MeshBasicMaterial;
}

// ---------------------------------------------------------------------------
// Scratch — module-scoped, so an update pass allocates nothing
// ---------------------------------------------------------------------------

const scratchMatrix = /* @__PURE__ */ new Matrix4();
const scratchPosition = /* @__PURE__ */ new Vector3();
const scratchQuaternion = /* @__PURE__ */ new Quaternion();
const scratchScale = /* @__PURE__ */ new Vector3(1, 1, 1);
const AXIS_Y = /* @__PURE__ */ new Vector3(0, 1, 0);
/** Lay the unit plane (XY) flat onto the plan (XZ). */
const FLAT = /* @__PURE__ */ new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -Math.PI / 2);

// ---------------------------------------------------------------------------
// The scene
// ---------------------------------------------------------------------------

export class MeasureScene {
  /** Drop this into the R3F tree with `<primitive object={scene.root} />`. */
  readonly root = new Group();

  private readonly inkBuffer = new LineBuffer(64);

  private readonly activeBuffer = new LineBuffer(32);

  private readonly inkGeometry = new BufferGeometry();

  private readonly activeGeometry = new BufferGeometry();

  private readonly inkLines: LineSegments;

  private readonly activeLines: LineSegments;

  private readonly pickGeometry = new PlaneGeometry(1, 1);

  private readonly pickMaterial: MeshBasicMaterial;

  private pick: InstancedMesh;

  /**
   * Instance slot → measurement id, rewritten by every update pass in the same
   * order the matrices are written.
   *
   * Live rather than derived: a measurement contributes as many instances as it
   * has segments, so slot 7 is not the seventh measurement.
   * `registerInstanced` reads array forms live, so the array identity never
   * changes and the registration is never rebuilt for a content change.
   */
  private readonly pickIdsLive: string[] = [];

  private readonly inkAttribute: AttributeState = { array: null };

  private readonly activeAttribute: AttributeState = { array: null };

  private registry: PickRegistry | null = null;

  private unregister: (() => void) | null = null;

  constructor(materials: MeasureSceneMaterials) {
    this.pickMaterial = materials.pickProxy;

    this.inkLines = new LineSegments(this.inkGeometry, materials.ink);
    this.activeLines = new LineSegments(this.activeGeometry, materials.active);
    // Measurements are annotation, drawn over the building: same layer as the
    // dimension strings, so there is one draw-order table and not two.
    applyLayer(this.inkLines, 'dimension');
    applyLayer(this.activeLines, 'dimension');
    // The geometry is rewritten per camera commit and its bounds move with it;
    // frustum culling against a stale sphere is how an overlay vanishes mid-pan.
    this.inkLines.frustumCulled = false;
    this.activeLines.frustumCulled = false;

    this.pick = this.makePickMesh(INITIAL_PICK_CAPACITY);

    this.root.name = 'measure-layer';
    this.root.add(this.inkLines, this.activeLines, this.pick);
  }

  /** Live instance→id map. Read-only to callers; the specs assert on it. */
  get pickIds(): readonly string[] {
    return this.pickIdsLive;
  }

  /** Instances currently drawn — i.e. currently clickable. */
  get pickCount(): number {
    return this.pick.count;
  }

  /**
   * THE registration. Returns the detach function; bind it to the layer's
   * unmount so a scene can never outlive its place in the registry.
   */
  attach(registry: PickRegistry): () => void {
    this.detach();
    this.registry = registry;
    // `storeyId: null` — the caller has already filtered `measurements` to the
    // storey being drawn, and a second filter here could only disagree with it.
    this.unregister = registry.registerInstanced(
      this.pick,
      MEASURE_PICK_KIND,
      this.pickIdsLive,
      null,
    );
    return () => this.detach();
  }

  detach(): void {
    this.unregister?.();
    this.unregister = null;
    this.registry = null;
  }

  /**
   * Rebuild every buffer for the current state and zoom.
   *
   * Cheap enough to run on every camera commit: one pass over the measurements
   * (there are tens, not thousands), integer arithmetic, no allocation except
   * when a buffer grows.
   */
  update(input: MeasureSceneInput): void {
    const { measurements, draft, mmPerPx, elevationMm, selectedId, visible } = input;
    this.root.visible = visible;

    const tickHalfMm = MEASURE_TICK_HALF_PX * mmPerPx;
    const pickHalfMm = (MEASURE_PICK_WIDTH_PX * mmPerPx) / 2;

    const ink = this.inkBuffer;
    const active = this.activeBuffer;
    ink.begin();
    active.begin();

    // Worst case: every measurement selected, every point ticked (2 segments).
    let segmentBudget = 0;
    for (const m of measurements) segmentBudget += m.points.length * 3 + 2;
    if (draft !== null) segmentBudget += draft.points.length * 3 + 6;
    // Reserve up front so `push` never has to check for growth mid-pass. The
    // return value (did the backing array move?) is not read: `syncGeometry`
    // compares the array identity itself, which is also true after a growth
    // that happened in the OTHER buffer's pass.
    ink.reserve(segmentBudget);
    active.reserve(segmentBudget);

    const ids = this.pickIdsLive;
    ids.length = 0;

    let needed = 0;
    for (const m of measurements) needed += measurementSegments(m.kind, m.points).length;
    this.ensurePickCapacity(needed);

    let instance = 0;
    for (const m of measurements) {
      const target = m.id === selectedId ? active : ink;
      const segments = measurementSegments(m.kind, m.points);
      for (const seg of segments) {
        target.push(seg.a.x, seg.a.y, seg.b.x, seg.b.y, elevationMm);
        this.writePickQuad(instance, seg, pickHalfMm, elevationMm);
        ids.push(m.id);
        instance += 1;
      }
      for (const p of m.points) pushCross(target, p, tickHalfMm, elevationMm);
    }

    if (draft !== null) {
      // The draft is deliberately NOT pickable. It is under the pointer by
      // definition, and a rubber band that could steal its own click would
      // make the next point unplaceable.
      const chain = draftPolyline(draft.points, draft.cursor);
      for (let i = 0; i + 1 < chain.length; i++) {
        const a = chain[i];
        const b = chain[i + 1];
        if (a === undefined || b === undefined) continue;
        active.push(a.x, a.y, b.x, b.y, elevationMm);
      }
      // An area draft shows the closing edge as soon as it would be a ring, so
      // the shape being measured is the shape on screen.
      const first = draft.points[0];
      const lastChain = chain[chain.length - 1];
      if (
        draft.kind === 'area' &&
        chain.length >= 3 &&
        first !== undefined &&
        lastChain !== undefined
      ) {
        active.push(lastChain.x, lastChain.y, first.x, first.y, elevationMm);
      }
      for (const p of chain) pushCross(active, p, tickHalfMm, elevationMm);
    }

    this.pick.count = instance;
    this.pick.instanceMatrix.needsUpdate = true;
    this.pick.computeBoundingSphere();

    syncGeometry(this.inkGeometry, ink, this.inkAttribute);
    syncGeometry(this.activeGeometry, active, this.activeAttribute);
  }

  dispose(): void {
    this.detach();
    this.inkGeometry.dispose();
    this.activeGeometry.dispose();
    this.pickGeometry.dispose();
    this.pick.dispose();
    // Materials are the shared overlay set (or the caller's) — never ours to
    // dispose. Disposing them here would blank every dimension string too.
  }

  // ── internals ────────────────────────────────────────────────────────────

  private makePickMesh(capacity: number): InstancedMesh {
    const mesh = new InstancedMesh(this.pickGeometry, this.pickMaterial, Math.max(1, capacity));
    mesh.count = 0;
    mesh.frustumCulled = false;
    applyLayer(mesh, 'dimension');
    mesh.name = 'measure-pick';
    return mesh;
  }

  /**
   * Grow the pick mesh when there are more segments than slots.
   *
   * `InstancedMesh` cannot grow in place, and the failure mode of pretending
   * otherwise is precise and awful: the count clamps and the last few
   * measurements silently stop being clickable. `scene.test.ts` drives past the
   * initial capacity and clicks the last one.
   */
  private ensurePickCapacity(needed: number): void {
    if (needed <= this.pick.instanceMatrix.count) return;
    let capacity = Math.max(1, this.pick.instanceMatrix.count);
    while (capacity < needed) capacity = Math.ceil(capacity * 1.5);

    const registry = this.registry;
    this.detach();
    this.root.remove(this.pick);
    this.pick.dispose();

    this.pick = this.makePickMesh(capacity);
    this.root.add(this.pick);
    // Re-register the NEW object: the registry keys on the Object3D, so a
    // rebuild without this is a layer that believes it is registered.
    if (registry !== null) this.attach(registry);
  }

  private writePickQuad(
    instance: number,
    seg: MeasureSegment,
    halfWidthMm: number,
    elevationMm: number,
  ): void {
    const dx = seg.b.x - seg.a.x;
    const dy = seg.b.y - seg.a.y;
    const lengthMm = Math.sqrt(dx * dx + dy * dy);
    // A zero-length segment gets a square click target rather than a degenerate
    // quad — the session refuses to commit one, but a defensive NaN in an
    // instance matrix corrupts the whole mesh's bounding sphere.
    const along = lengthMm === 0 ? halfWidthMm * 2 : lengthMm;
    const bearing = lengthMm === 0 ? 0 : Math.atan2(dy, dx);

    scratchPosition.set(
      ((seg.a.x + seg.b.x) / 2) * WORLD_UNITS_PER_MM,
      elevationMm * WORLD_UNITS_PER_MM,
      -((seg.a.y + seg.b.y) / 2) * WORLD_UNITS_PER_MM,
    );
    // Plan (+x east, +y north) → world (+x east, −z north): a plan bearing of
    // `a` becomes a rotation of `a` about world +Y. FLAT first, then the spin.
    scratchQuaternion.setFromAxisAngle(AXIS_Y, bearing).multiply(FLAT);
    scratchScale.set(along * WORLD_UNITS_PER_MM, halfWidthMm * 2 * WORLD_UNITS_PER_MM, 1);
    scratchMatrix.compose(scratchPosition, scratchQuaternion, scratchScale);
    this.pick.setMatrixAt(instance, scratchMatrix);
  }
}

// ---------------------------------------------------------------------------
// Buffer helpers
// ---------------------------------------------------------------------------

/** A small cross at a measured point — "the measurement lands HERE", not near. */
function pushCross(buffer: LineBuffer, p: Pt, halfMm: number, elevationMm: number): void {
  buffer.push(p.x - halfMm, p.y, p.x + halfMm, p.y, elevationMm);
  buffer.push(p.x, p.y - halfMm, p.x, p.y + halfMm, elevationMm);
}

/**
 * The array a geometry's position attribute was last built over. Compared by
 * identity so the attribute is rebuilt only when `LineBuffer` replaces its
 * backing store, which happens on a growth and never in the hot path.
 */
interface AttributeState {
  array: Float32Array | null;
}

/**
 * Push a written buffer into its geometry.
 *
 * ONE WORKAROUND, and it is not cosmetic. `LineBuffer.reserve` grows by
 * `Math.ceil(length * 1.5)`, which can land on a float count that is NOT a
 * multiple of 3: from this layer's 32-segment start the chain runs
 * 192 → … → 2187 → **3281**. `new BufferAttribute(array, 3)` over that reports a
 * FRACTIONAL `count` (1093.67), `computeBoundingSphere` then reads one index
 * past the end, gets `undefined`, and the sphere's radius becomes NaN — which
 * frustum culling turns into "the layer vanished". Executed, not theorised:
 * three logged `Computed radius is NaN` on the 50-measurement case in
 * `scene.test.ts` before this existed.
 *
 * A `subarray` trimmed to a whole number of vertices shares the same memory, so
 * every later `push` still lands in it. The upstream growth arithmetic is in
 * `canvas/overlays/render/lines.ts` and reaches the same state (at 88,574
 * floats) for `DimensionLayer` — see the handoff note.
 */
function syncGeometry(geometry: BufferGeometry, buffer: LineBuffer, state: AttributeState): void {
  const raw = buffer.array;
  const existing = geometry.getAttribute('position') as BufferAttribute | undefined;
  if (existing === undefined || state.array !== raw) {
    const usableFloats = raw.length - (raw.length % 3);
    geometry.setAttribute('position', new BufferAttribute(raw.subarray(0, usableFloats), 3));
    state.array = raw;
  } else {
    existing.needsUpdate = true;
  }
  geometry.setDrawRange(0, buffer.vertexCount);
  geometry.computeBoundingSphere();
}
