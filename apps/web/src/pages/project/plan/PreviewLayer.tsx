/**
 * PreviewLayer — what the active tool is drawing, before it becomes an op.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ZERO REACT RENDERS PER POINTER MOVE (§14)
 * ════════════════════════════════════════════════════════════════════════════
 * The tools layer publishes a `ToolPreview` to `toolPreviewBus` on every
 * pointer move. Subscribing to that from React would reconcile an ancestor of
 * the whole scene sixty times a second, which is exactly what the bus exists to
 * avoid — its own header says so.
 *
 * So this component renders ONCE and then never again. It owns one
 * `LineSegments` with one grow-only vertex buffer, reads the bus inside
 * `useFrame`, and rewrites the buffer in place when the preview's `version`
 * changes. `frameloop="demand"` means the frame only happens because
 * `useToolController` already called `core.invalidate()` after publishing, so
 * an idle canvas with a tool armed still costs nothing.
 *
 * The writer allocates nothing in the steady state: one `Float32Array`, reused,
 * doubled only when a preview genuinely needs more segments than the last one
 * did (a long wall chain, a marquee over a whole floor).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CONVERSION BOUNDARY
 * ════════════════════════════════════════════════════════════════════════════
 * `PreviewShape` is integer millimetres, straight from the tool. This file
 * converts to world units on the way into the buffer and never converts back.
 * Nothing here produces an op — the commit path is the tool's, and the preview
 * is deliberately a picture of a decision that has not been taken yet.
 *
 * Screen-constant sizes (the crosshair, the snap marker) read `mmPerPx` off the
 * viewport controller inside the frame callback, which is where live camera
 * state is allowed to be read.
 */

import { useEffect, useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { BufferAttribute, BufferGeometry, type LineSegments } from 'three';

import type { Pt } from '@garh/model';

import {
  LAYER_RENDER_ORDER,
  WORLD_UNITS_PER_MM,
  useCanvasCore,
} from '../../../features/canvas/core';
import {
  toolPreviewBus,
  type PreviewShape,
  type ToolPreview,
} from '../../../features/canvas/tools';
import { getPlanMaterials } from './planMaterials';

/** Segments a fresh buffer holds before it has to grow. */
const INITIAL_SEGMENTS = 512;
/** Half-length of the cursor crosshair, in CSS pixels. */
const CROSSHAIR_PX = 9;
/** Half-side of the snap marker square, in CSS pixels. */
const SNAP_MARKER_PX = 5;

// ---------------------------------------------------------------------------
// The writer
// ---------------------------------------------------------------------------

/**
 * A grow-only line-segment buffer in world units.
 *
 * Deliberately a class with a mutable cursor rather than a functional builder:
 * this runs inside `useFrame`, and a `[]` per frame is a garbage-collection
 * pause per drag, which reads to the user as the canvas stuttering exactly when
 * they are being most precise.
 */
class SegmentWriter {
  positions: Float32Array;

  /** Vertices written so far (two per segment). */
  count = 0;

  /** Set when the array had to be replaced, so the attribute is swapped. */
  grew = false;

  private elevationWorld = 0;

  constructor(segments = INITIAL_SEGMENTS) {
    this.positions = new Float32Array(segments * 6);
  }

  begin(elevationMm: number): void {
    this.count = 0;
    this.grew = false;
    this.elevationWorld = elevationMm * WORLD_UNITS_PER_MM;
  }

  /** One segment, in millimetres. Grows the buffer if it is full. */
  push(axMm: number, ayMm: number, bxMm: number, byMm: number): void {
    const needed = (this.count + 2) * 3;
    if (needed > this.positions.length) {
      const next = new Float32Array(Math.max(needed, this.positions.length * 2));
      next.set(this.positions);
      this.positions = next;
      this.grew = true;
    }
    let v = this.count * 3;
    this.positions[v] = axMm * WORLD_UNITS_PER_MM;
    this.positions[v + 1] = this.elevationWorld;
    this.positions[v + 2] = -ayMm * WORLD_UNITS_PER_MM;
    v += 3;
    this.positions[v] = bxMm * WORLD_UNITS_PER_MM;
    this.positions[v + 1] = this.elevationWorld;
    this.positions[v + 2] = -byMm * WORLD_UNITS_PER_MM;
    this.count += 2;
  }

  pushPt(a: Pt, b: Pt): void {
    this.push(a.x, a.y, b.x, b.y);
  }

  /** Closed or open polyline. */
  pushRing(points: readonly Pt[], closed: boolean): void {
    for (let i = 0; i + 1 < points.length; i += 1) {
      this.pushPt(points[i] as Pt, points[i + 1] as Pt);
    }
    if (closed && points.length > 2) {
      this.pushPt(points[points.length - 1] as Pt, points[0] as Pt);
    }
  }

  pushRect(cx: number, cy: number, halfW: number, halfH: number): void {
    this.push(cx - halfW, cy - halfH, cx + halfW, cy - halfH);
    this.push(cx + halfW, cy - halfH, cx + halfW, cy + halfH);
    this.push(cx + halfW, cy + halfH, cx - halfW, cy + halfH);
    this.push(cx - halfW, cy + halfH, cx - halfW, cy - halfH);
  }
}

// ---------------------------------------------------------------------------
// Shape → segments
// ---------------------------------------------------------------------------

/** The rectangle of a preview wall, from its centreline and thickness. */
function pushWallOutline(w: SegmentWriter, a: Pt, b: Pt, thicknessMm: number): void {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return;
  const half = thicknessMm / 2;
  const nx = (-dy / len) * half;
  const ny = (dx / len) * half;
  const p1x = a.x + nx;
  const p1y = a.y + ny;
  const p2x = b.x + nx;
  const p2y = b.y + ny;
  const p3x = b.x - nx;
  const p3y = b.y - ny;
  const p4x = a.x - nx;
  const p4y = a.y - ny;
  w.push(p1x, p1y, p2x, p2y);
  w.push(p2x, p2y, p3x, p3y);
  w.push(p3x, p3y, p4x, p4y);
  w.push(p4x, p4y, p1x, p1y);
  // The centreline too: at 1:200 a 115 mm partition's two faces converge and
  // the preview would otherwise vanish exactly when you are placing it.
  w.push(a.x, a.y, b.x, b.y);
}

function writeShape(w: SegmentWriter, shape: PreviewShape): void {
  switch (shape.kind) {
    case 'wall-chain': {
      for (const segment of shape.segments) {
        pushWallOutline(w, segment.a, segment.b, segment.thicknessMm);
      }
      if (shape.rubber !== null) {
        pushWallOutline(w, shape.rubber.a, shape.rubber.b, shape.rubber.thicknessMm);
      }
      break;
    }
    case 'opening': {
      if (shape.axis !== null) {
        const [from, to] = shape.axis;
        w.pushPt(from, to);
      }
      if (shape.centreMm !== null) {
        const half = shape.widthMm / 2;
        w.pushRect(shape.centreMm.x, shape.centreMm.y, half, half);
      }
      break;
    }
    case 'stair': {
      w.pushRing(shape.footprint, true);
      for (const [from, to] of shape.treads) w.pushPt(from, to);
      if (shape.arrow !== null) w.pushPt(shape.arrow[0], shape.arrow[1]);
      break;
    }
    case 'polygon': {
      w.pushRing(shape.points, shape.closed);
      const last = shape.points[shape.points.length - 1];
      if (shape.rubber !== null && last !== undefined) w.pushPt(last, shape.rubber);
      break;
    }
    case 'furniture': {
      const halfW = shape.sizeMm.xMm / 2;
      const halfD = shape.sizeMm.yMm / 2;
      const rad = (shape.rotationDeg * Math.PI) / 180;
      const cos = Math.cos(rad);
      const sin = Math.sin(rad);
      const cx = shape.centreMm.x;
      const cy = shape.centreMm.y;
      const corner = (sx: number, sy: number): Pt => ({
        x: cx + sx * halfW * cos - sy * halfD * sin,
        y: cy + sx * halfW * sin + sy * halfD * cos,
      });
      const c1 = corner(-1, -1);
      const c2 = corner(1, -1);
      const c3 = corner(1, 1);
      const c4 = corner(-1, 1);
      w.pushPt(c1, c2);
      w.pushPt(c2, c3);
      w.pushPt(c3, c4);
      w.pushPt(c4, c1);
      // A tick on the front edge (+Y local) so the rotation is readable.
      w.pushPt({ x: (c3.x + c4.x) / 2, y: (c3.y + c4.y) / 2 }, { x: cx, y: cy });
      break;
    }
    case 'measure': {
      w.pushRing(shape.points, false);
      const last = shape.points[shape.points.length - 1];
      if (shape.rubber !== null && last !== undefined) w.pushPt(last, shape.rubber);
      break;
    }
    case 'transform': {
      for (const ghost of shape.ghosts) {
        pushWallOutline(w, ghost.a, ghost.b, ghost.thicknessMm);
      }
      break;
    }
    case 'marquee': {
      const { ax, ay, bx, by } = shape.rect;
      w.push(ax, ay, bx, ay);
      w.push(bx, ay, bx, by);
      w.push(bx, by, ax, by);
      w.push(ax, by, ax, ay);
      break;
    }
    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// The component
// ---------------------------------------------------------------------------

export interface PreviewLayerProps {
  /** Storey FFL, so the preview sits on the same plane as the drawing. */
  readonly elevationMm: number;
  readonly visible?: boolean | undefined;
}

export function PreviewLayer({ elevationMm, visible = true }: PreviewLayerProps): JSX.Element {
  const core = useCanvasCore();
  const materials = getPlanMaterials();

  const writer = useMemo(() => new SegmentWriter(), []);
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(new Float32Array(INITIAL_SEGMENTS * 6), 3));
    g.setDrawRange(0, 0);
    // The preview moves every frame; a bounding sphere computed once would
    // frustum-cull it the moment the rubber band leaves the first stroke.
    g.boundingSphere = null;
    return g;
  }, []);

  const meshRef = useRef<LineSegments>(null);
  /** Preview identity we last drew: tool + version + phase. */
  const lastKey = useRef<string>('');
  /** Elevation is a prop, but the frame callback must not close over a stale one. */
  const elevationRef = useRef(elevationMm);
  elevationRef.current = elevationMm;

  useEffect(() => () => geometry.dispose(), [geometry]);

  // A storey switch changes the plane the preview lives on without changing the
  // preview itself, so the version guard would skip the rewrite. Force one.
  useEffect(() => {
    lastKey.current = '';
    core.invalidate();
  }, [core, elevationMm]);

  useFrame(() => {
    const preview: ToolPreview | null = toolPreviewBus.get();
    const key =
      preview === null
        ? 'none'
        : `${preview.toolId}:${preview.phase}:${preview.version}:${elevationRef.current}`;
    if (key === lastKey.current) return;
    lastKey.current = key;

    writer.begin(elevationRef.current);

    if (preview !== null) {
      writeShape(writer, preview.shape);

      // Screen-constant marks. `mmPerPx` is live camera state and is read here,
      // inside the frame callback, which is the only place that is allowed.
      const mmPerPx = core.viewport.mmPerPx;
      if (preview.cursorMm !== null && preview.phase !== 'idle') {
        const armMm = CROSSHAIR_PX * mmPerPx;
        const { x, y } = preview.cursorMm;
        writer.push(x - armMm, y, x + armMm, y);
        writer.push(x, y - armMm, x, y + armMm);
      }
      if (preview.snap !== null) {
        const halfMm = SNAP_MARKER_PX * mmPerPx;
        writer.pushRect(preview.snap.pointMm.x, preview.snap.pointMm.y, halfMm, halfMm);
      }
    }

    const attribute = geometry.getAttribute('position') as BufferAttribute;
    if (writer.grew || attribute.array !== writer.positions) {
      geometry.setAttribute('position', new BufferAttribute(writer.positions, 3));
    } else {
      attribute.needsUpdate = true;
    }
    geometry.setDrawRange(0, writer.count);

    const mesh = meshRef.current;
    if (mesh !== null) mesh.visible = visible && writer.count > 0;
  });

  return (
    <lineSegments
      ref={meshRef}
      geometry={geometry}
      material={materials.previewLine}
      renderOrder={LAYER_RENDER_ORDER.preview}
      frustumCulled={false}
      visible={false}
    />
  );
}
