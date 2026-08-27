/**
 * DimensionLayer.tsx — dimension strings in the shared scene graph.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * HOW THE §14 FRAME BUDGET IS MET (state it, do not assume it)
 * ────────────────────────────────────────────────────────────────────────────
 * 1. **One draw call for every line.** All baselines, witness lines and ticks
 *    across every string are a single `LineSegments` over one `BufferAttribute`
 *    with one shared material. A 400-segment plan is one draw call, not 2 000.
 *
 * 2. **No allocation in the update path.** The position buffer is a `LineBuffer`
 *    that grows and never shrinks; camera commits rewrite it in place. The only
 *    allocations happen when the CHAIN SET changes, which is a document edit.
 *
 * 3. **No React render per pointer move or zoom frame.** Dimension geometry
 *    genuinely depends on zoom — the strings hang a constant number of PIXELS
 *    off the building — so it must update during a wheel gesture. It does so
 *    through `useViewportEffect`, which subscribes to the viewport controller
 *    and mutates buffers; React is not involved. `useMmPerPx()` would have been
 *    one reconciliation of this whole tree per frame.
 *
 * 4. **Labels scale, they do not re-lay-out.** Each label is a troika `Text` at
 *    `fontSize: 1` inside a group whose scale is written by `useScreenScale`.
 *    Setting `fontSize` per frame would re-run glyph shaping per label.
 *
 * 5. **One pick proxy mesh, instanced.** Every editable segment is an instance
 *    of one `InstancedMesh` registered through `usePickableInstances`, so the
 *    core's single raycaster sees one object and resolves an id per instance.
 *    There are no react-three-fiber pointer handlers here — §12's "one hit
 *    testing system" is the core's registry, and Phase 5 inherits it unchanged.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * CLICK-TO-EDIT
 * ────────────────────────────────────────────────────────────────────────────
 * A pick on this layer returns `{ kind: 'dimension', id: <segment id> }`. The
 * page hands that id to {@link DimensionHandleIndex.lookup} and gets back the
 * `DimensionEditTarget`, which `applyDimensionEdit` turns into ops. The id is
 * opaque on purpose: nothing outside this module parses it.
 */

import { Suspense, useEffect, useMemo, useRef } from 'react';
import { Text } from '@react-three/drei';
import {
  BufferAttribute,
  BufferGeometry,
  Matrix4,
  PlaneGeometry,
  Quaternion,
  Vector3,
} from 'three';
import type { InstancedMesh, LineSegments, Object3D } from 'three';

import type { UnitsDisplay } from '@garh/model';

import { useCanvasCore, usePickableInstances, WORLD_UNITS_PER_MM } from '../../core';
import { dimensionText } from '../format';
import { LineBuffer, pushTick } from '../render/lines';
import {
  DIMENSION_RENDER_ORDER,
  getOverlayMaterials,
  LABEL_FONT_SIZE_LOCAL,
  LABEL_FONT_URL,
} from '../render/overlayMaterials';
import { useScreenScale, useViewportEffect } from '../render/screenScale';
import { chainBaselineMm, type DimChain, type DimensionEditTarget, type DimSegment } from './chain';

// ---------------------------------------------------------------------------
// Screen-space geometry constants (CSS pixels)
// ---------------------------------------------------------------------------

/** Gap between the building edge and the first dimension string. */
export const DIM_OFFSET_PX = 26;
/** Gap between stacked strings. */
export const DIM_STEP_PX = 22;
/** How far a witness line overshoots its baseline. */
export const DIM_OVERSHOOT_PX = 5;
/** Gap between the element and the start of its witness line. */
export const DIM_WITNESS_GAP_PX = 4;
/** Half-length of the 45° tick slash. */
export const DIM_TICK_HALF_PX = 4;
/** Label height. Small — a plan is read at arm's length, not across a room. */
export const DIM_LABEL_PX = 11;
/** Click target height around a dimension string. Fitts' law, same as the core. */
export const DIM_PICK_HEIGHT_PX = 14;

/**
 * A string is not drawn below this length on screen. Under ~18 px the label
 * cannot fit between its own ticks, and a plan zoomed to the site shows a grey
 * mat of unreadable numbers instead of a building.
 */
export const DIM_MIN_SEGMENT_PX = 18;

// ---------------------------------------------------------------------------
// Handle index — pick id → edit target
// ---------------------------------------------------------------------------

export interface DimensionHandle {
  readonly id: string;
  readonly chain: DimChain;
  readonly segment: DimSegment;
  readonly target: DimensionEditTarget;
}

export interface DimensionHandleIndex {
  readonly handles: readonly DimensionHandle[];
  /** `null` for an id from a previous chain set — never a stale target. */
  lookup: (id: string) => DimensionHandle | null;
}

export function buildHandleIndex(chains: readonly DimChain[]): DimensionHandleIndex {
  const handles: DimensionHandle[] = [];
  for (const chain of chains) {
    for (const segment of chain.segments) {
      if (segment.target === null) continue;
      handles.push({ id: segment.id, chain, segment, target: segment.target });
    }
  }
  const byId = new Map(handles.map((h) => [h.id, h]));
  return { handles, lookup: (id) => byId.get(id) ?? null };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface DimensionLayerProps {
  /** Chains for the active storey. Memoise upstream — see `buildDimensionChains`. */
  chains: readonly DimChain[];
  /** Elevation the plan is drawn at (the storey FFL). */
  elevationMm?: number | undefined;
  /** Storey the strings belong to, for the pick registry's storey filter. */
  storeyId?: string | null | undefined;
  display: UnitsDisplay;
  /** Segment currently open in the edit field — drawn in the brand colour. */
  activeSegmentId?: string | null | undefined;
  /** Rebuilt by the layer and handed back so the page can resolve picks. */
  onHandleIndex?: ((index: DimensionHandleIndex) => void) | undefined;
  /** Self-hosted font. See `LABEL_FONT_URL` before changing this. */
  fontUrl?: string | undefined;
  visible?: boolean | undefined;
}

// ---------------------------------------------------------------------------
// Layer
// ---------------------------------------------------------------------------

/** Scratch for instance matrices. Module-scoped: nothing here escapes. */
const scratchMatrix = /* @__PURE__ */ new Matrix4();
const scratchPosition = /* @__PURE__ */ new Vector3();
const scratchQuaternion = /* @__PURE__ */ new Quaternion();
const scratchScale = /* @__PURE__ */ new Vector3(1, 1, 1);
/** Flat on the plan: rotate the unit plane from the XY plane onto XZ. */
const FLAT = /* @__PURE__ */ new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -Math.PI / 2);

export function DimensionLayer({
  chains,
  elevationMm = 0,
  storeyId = null,
  display,
  activeSegmentId = null,
  onHandleIndex,
  fontUrl = LABEL_FONT_URL,
  visible = true,
}: DimensionLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const materials = getOverlayMaterials();
  const scale = useScreenScale(DIM_LABEL_PX);

  // ── The flat segment list. Rebuilt only when the document changes. ───────
  const handleIndex = useMemo(() => buildHandleIndex(chains), [chains]);

  const items = useMemo(
    () =>
      chains.flatMap((chain) =>
        chain.segments.map((segment) => ({
          chain,
          segment,
          text: dimensionText(segment.valueMm, display),
        })),
      ),
    [chains, display],
  );

  useEffect(() => {
    onHandleIndex?.(handleIndex);
  }, [handleIndex, onHandleIndex]);

  // ── Line geometry ────────────────────────────────────────────────────────
  // Lazily initialised, not `useRef(new BufferGeometry())`: the argument to
  // `useRef` is evaluated on EVERY render and thrown away after the first, so
  // the eager form leaks one geometry and one buffer per render.
  const bufferRef = useRef<LineBuffer | null>(null);
  bufferRef.current ??= new LineBuffer(256);
  const geometryRef = useRef<BufferGeometry | null>(null);
  geometryRef.current ??= new BufferGeometry();
  const linesRef = useRef<LineSegments | null>(null);

  useEffect(() => {
    const geometry = geometryRef.current;
    return () => geometry?.dispose();
  }, []);

  // ── Label groups, indexed the same way as `items` ────────────────────────
  const labelRefs = useRef<(Object3D | null)[]>([]);
  labelRefs.current.length = items.length;

  // ── Pick proxies ─────────────────────────────────────────────────────────
  /**
   * Capacity of the instanced mesh: every editable segment could be drawn.
   * The number ACTUALLY drawn is smaller whenever a segment is too short to
   * read at the current zoom.
   */
  const pickCapacity = handleIndex.handles.length;

  /**
   * The live instance → element-id map, rewritten by the update pass in the
   * SAME order the matrices are written.
   *
   * It has to be live rather than derived from `handles`, and this is the bug
   * that motivated it: short segments are skipped for legibility, so instance
   * 7 is not necessarily the seventh handle. A static array would silently
   * return a different dimension's edit target — you type 3600 on one bay and
   * another one moves. `PickRegistry.registerInstanced` reads array forms live
   * for exactly this case, so the identity never changes and no re-register is
   * needed.
   */
  const pickIdsLive = useRef<string[]>([]);
  const pickRef = useRef<InstancedMesh | null>(null);
  const pickRegister = usePickableInstances('dimension', pickIdsLive.current, storeyId);
  const pickGeometry = useMemo(() => new PlaneGeometry(1, 1), []);
  useEffect(() => () => pickGeometry.dispose(), [pickGeometry]);

  /**
   * THE UPDATE PASS.
   *
   * Runs on every committed camera change and whenever the chain set changes.
   * Everything it touches is preallocated; the only branch that allocates is a
   * buffer growth, which happens when a plan gains dimension segments.
   */
  useViewportEffect(() => {
    const buffer = bufferRef.current;
    const geometry = geometryRef.current;
    const pick = pickRef.current;
    if (buffer === null || geometry === null) return;

    const liveIds = pickIdsLive.current;
    liveIds.length = 0;

    // `useViewportEffect` runs after the controller has committed, so this is
    // THIS frame's zoom, not last frame's.
    const currentMmPerPx = core.viewport.mmPerPx;
    const offsetMm = DIM_OFFSET_PX * currentMmPerPx;
    const stepMm = DIM_STEP_PX * currentMmPerPx;
    const overshootMm = DIM_OVERSHOOT_PX * currentMmPerPx;
    const gapMm = DIM_WITNESS_GAP_PX * currentMmPerPx;
    const tickHalfMm = DIM_TICK_HALF_PX * currentMmPerPx;
    const minSegmentMm = DIM_MIN_SEGMENT_PX * currentMmPerPx;
    const pickHalfMm = (DIM_PICK_HEIGHT_PX * currentMmPerPx) / 2;

    buffer.begin();
    // 5 line segments per dimension segment: baseline, two witness lines, two
    // ticks. Reserved once so `push` never has to check for growth mid-pass.
    const grew = buffer.reserve(items.length * 5);

    let instance = 0;
    items.forEach((item, index) => {
      const { chain, segment } = item;
      const baselineMm = chainBaselineMm(chain, offsetMm, stepMm);
      const group = labelRefs.current[index] ?? null;

      // Too short to read at this zoom: hide the label, skip the geometry.
      // The segment still exists — zoom in and it comes back — so this is a
      // level-of-detail rule, not a filter on what is dimensioned.
      const tooShort = segment.valueMm < minSegmentMm;
      if (group !== null) group.visible = !tooShort;
      if (tooShort) return;

      const outward = chain.kind === 'room' ? 0 : chain.outward;

      if (chain.axis === 'x') {
        const y = baselineMm;
        buffer.push(segment.startMm, y, segment.endMm, y, elevationMm);
        buffer.push(
          segment.startMm,
          chain.edgeMm + outward * gapMm,
          segment.startMm,
          y + outward * overshootMm,
          elevationMm,
        );
        buffer.push(
          segment.endMm,
          chain.edgeMm + outward * gapMm,
          segment.endMm,
          y + outward * overshootMm,
          elevationMm,
        );
        pushTick(buffer, segment.startMm, y, 'x', tickHalfMm, elevationMm);
        pushTick(buffer, segment.endMm, y, 'x', tickHalfMm, elevationMm);
        if (group !== null) {
          group.position.set(
            ((segment.startMm + segment.endMm) / 2) * WORLD_UNITS_PER_MM,
            elevationMm * WORLD_UNITS_PER_MM,
            -(y + outward * (overshootMm + tickHalfMm)) * WORLD_UNITS_PER_MM,
          );
          group.rotation.set(-Math.PI / 2, 0, 0);
        }
      } else {
        const x = baselineMm;
        buffer.push(x, segment.startMm, x, segment.endMm, elevationMm);
        buffer.push(
          chain.edgeMm + outward * gapMm,
          segment.startMm,
          x + outward * overshootMm,
          segment.startMm,
          elevationMm,
        );
        buffer.push(
          chain.edgeMm + outward * gapMm,
          segment.endMm,
          x + outward * overshootMm,
          segment.endMm,
          elevationMm,
        );
        pushTick(buffer, x, segment.startMm, 'y', tickHalfMm, elevationMm);
        pushTick(buffer, x, segment.endMm, 'y', tickHalfMm, elevationMm);
        if (group !== null) {
          group.position.set(
            (x + outward * (overshootMm + tickHalfMm)) * WORLD_UNITS_PER_MM,
            elevationMm * WORLD_UNITS_PER_MM,
            -((segment.startMm + segment.endMm) / 2) * WORLD_UNITS_PER_MM,
          );
          // Vertical strings read bottom-to-top, the drafting convention.
          group.rotation.set(-Math.PI / 2, 0, Math.PI / 2);
        }
      }

      // Pick proxy: a quad covering the string, `DIM_PICK_HEIGHT_PX` tall.
      if (pick !== null && segment.target !== null && instance < pickCapacity) {
        const midAlong = (segment.startMm + segment.endMm) / 2;
        const lengthMm = segment.endMm - segment.startMm;
        if (chain.axis === 'x') {
          scratchPosition.set(
            midAlong * WORLD_UNITS_PER_MM,
            elevationMm * WORLD_UNITS_PER_MM,
            -baselineMm * WORLD_UNITS_PER_MM,
          );
          scratchScale.set(lengthMm * WORLD_UNITS_PER_MM, pickHalfMm * 2 * WORLD_UNITS_PER_MM, 1);
          scratchQuaternion.copy(FLAT);
        } else {
          scratchPosition.set(
            baselineMm * WORLD_UNITS_PER_MM,
            elevationMm * WORLD_UNITS_PER_MM,
            -midAlong * WORLD_UNITS_PER_MM,
          );
          scratchScale.set(pickHalfMm * 2 * WORLD_UNITS_PER_MM, lengthMm * WORLD_UNITS_PER_MM, 1);
          scratchQuaternion.copy(FLAT);
        }
        scratchMatrix.compose(scratchPosition, scratchQuaternion, scratchScale);
        pick.setMatrixAt(instance, scratchMatrix);
        liveIds.push(segment.id);
        instance += 1;
      }
    });

    // Rebuild the attribute only when the backing array was replaced.
    const existing = geometry.getAttribute('position') as BufferAttribute | undefined;
    if (grew || existing === undefined || existing.array !== buffer.array) {
      geometry.setAttribute('position', new BufferAttribute(buffer.array, 3));
    } else {
      existing.needsUpdate = true;
    }
    geometry.setDrawRange(0, buffer.vertexCount);
    // The bounding sphere is what frustum culling uses; a stale one makes the
    // whole layer vanish when the camera moves past where the strings USED to
    // be. Recomputing per commit is one pass over a few thousand floats.
    geometry.computeBoundingSphere();

    if (pick !== null) {
      pick.count = instance;
      pick.instanceMatrix.needsUpdate = true;
      pick.computeBoundingSphere();
    }

    // Labels that mounted with this pass have not been through a camera commit
    // yet, so their scale is still 1. Sync now rather than letting them render
    // one frame at world size, which reads as a flash of enormous text.
    scale.sync();
    // `items` and `elevationMm` are inputs as much as the camera is: without
    // them here the strings would keep showing the previous edit's numbers
    // until the user happened to pan.
  }, [items, elevationMm, pickCapacity]);

  if (items.length === 0) return null;

  return (
    <group visible={visible} name="dimension-overlay">
      <lineSegments
        ref={linesRef}
        geometry={geometryRef.current ?? undefined}
        material={materials.dimensionLine}
        renderOrder={DIMENSION_RENDER_ORDER}
        frustumCulled={false}
      />

      {/*
        Pick proxies. `key` on the instance count so the mesh is rebuilt when the
        segment count changes — `InstancedMesh` cannot grow, and silently
        clipping picks at the old count is the kind of bug that presents as
        "the last three dimensions are not clickable".
      */}
      <instancedMesh
        key={`pick-${String(pickCapacity)}`}
        ref={(node) => {
          pickRef.current = node;
          pickRegister(node);
        }}
        args={[pickGeometry, materials.pickProxy, Math.max(1, pickCapacity)]}
        renderOrder={DIMENSION_RENDER_ORDER}
        frustumCulled={false}
      />

      {/*
        One container for every label. `useScreenScale` walks its children on
        each camera commit — see the note there on why a per-label registration
        leaks under React 18's callback-ref contract.

        THE SUSPENSE BOUNDARY IS LOAD-BEARING. drei's `<Text>` suspends until
        troika has loaded the label font. An uncaught suspension does not stop
        at the scene: `@react-three/fiber`'s `<Canvas>` re-throws it into the
        DOM tree, where the nearest boundary is the ROUTE-level one — which
        then hides the ENTIRE plan tab (`display: none !important`) until the
        font resolves. And with `inter-medium.woff` missing (the documented
        asset blocker), troika 0.49's font-load error path never invokes its
        callback, so "until" is FOREVER — the comment in `overlayMaterials.ts`
        used to claim troika falls back gracefully; execution proved it does
        not. Executed proof: plan-canvas.spec.ts went blank ~1.4 s after the
        first room closed, and every later pointer event landed on the tab
        skeleton. Caught here, a slow or broken font costs the LABELS only —
        lines, ticks and pick targets are outside the boundary and stay live.
      */}
      <group ref={scale.ref}>
        <Suspense fallback={null}>
          {items.map((item, index) => (
            <group
              key={item.segment.id}
              ref={(node) => {
                labelRefs.current[index] = node;
              }}
            >
              <Text
                font={fontUrl}
                fontSize={LABEL_FONT_SIZE_LOCAL}
                anchorX="center"
                anchorY="bottom"
                color={
                  item.segment.id === activeSegmentId
                    ? materials.dimensionActive.color.getStyle()
                    : materials.dimensionLine.color.getStyle()
                }
                renderOrder={DIMENSION_RENDER_ORDER + 1}
                // Coplanar with the plan in an orthographic top view, so depth
                // testing can only z-fight. `renderOrder` does the ordering,
                // which is what the core's layer table says overlays should do.
                material-depthTest={false}
                material-depthWrite={false}
              >
                {item.text}
              </Text>
            </group>
          ))}
        </Suspense>
      </group>
    </group>
  );
}
