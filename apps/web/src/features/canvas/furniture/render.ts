/**
 * What to draw, computed without a renderer.
 *
 * `FurnitureLayer.tsx` is Three.js plumbing: it uploads matrices and colours.
 * The arithmetic that decides WHERE each box goes lives here, in plain
 * functions over plain numbers, so it can be unit-tested and so Phase 5 can
 * reuse it for the perspective view without importing a 2D component.
 *
 * ## Perf contract (§14: <16 ms/frame on the G+2 demo)
 *
 * - {@link buildBoxInstances} runs on a DOCUMENT change, not on a frame. Its
 *   output is a flat array of primitives — no nesting, no per-frame lookups.
 * - {@link buildEdgePositions} allocates ONE Float32Array per rebuild and fills
 *   it with a loop that allocates nothing.
 * - Neither is called from `useFrame`. The render loop only reads buffers that
 *   are already on the GPU; a pan or zoom moves the camera and touches nothing
 *   here at all.
 * - The preview is a separate path: one box, updated imperatively from the
 *   placement controller's pose channel, never through React state.
 */

import { proxyCache } from './proxyMesh';
import { sceneScale, writeScenePosition, type PlanAxes } from './sceneAxes';
import type { CatalogueItem, FurnitureCategory, PlacedFurniture, Pose } from './types';

const DEG_TO_RAD = Math.PI / 180;

/**
 * One box of one placed item, already rotated into plan space.
 *
 * `px`/`py` are the box centre in plot-local mm (fractional after a non-right
 * angle — this is downstream of the op boundary, so floats are fine and
 * expected). `deg` is the item's rotation, carried so the renderer can build a
 * quaternion once per box instead of decomposing a matrix.
 */
export interface BoxInstance {
  readonly furnitureId: string;
  readonly catalogId: string;
  readonly category: FurnitureCategory;
  readonly boxKey: string;
  readonly px: number;
  readonly py: number;
  /** Height of the box CENTRE above the storey floor, mm. */
  readonly pz: number;
  readonly deg: number;
  readonly wMm: number;
  readonly dMm: number;
  readonly hMm: number;
}

/** Rotate a local offset into plan space and add the item's centre. */
function placeOffset(pose: Pose, cx: number, cy: number): { px: number; py: number } {
  const deg = pose.rotationDeg;
  switch (deg) {
    case 0:
      return { px: pose.pt.x + cx, py: pose.pt.y + cy };
    case 90:
      return { px: pose.pt.x - cy, py: pose.pt.y + cx };
    case 180:
      return { px: pose.pt.x - cx, py: pose.pt.y - cy };
    case 270:
      return { px: pose.pt.x + cy, py: pose.pt.y - cx };
    default: {
      const rad = deg * DEG_TO_RAD;
      const c = Math.cos(rad);
      const s = Math.sin(rad);
      return { px: pose.pt.x + cx * c - cy * s, py: pose.pt.y + cx * s + cy * c };
    }
  }
}

/**
 * Flatten placed furniture into per-box instances.
 *
 * Instances whose catalogue entry is missing are skipped and reported, so the
 * caller can show one honest "3 items are not in the catalogue" note instead of
 * silently drawing an empty floor.
 */
export function buildBoxInstances(placed: readonly PlacedFurniture[]): {
  instances: BoxInstance[];
  unknownCatalogIds: string[];
} {
  const instances: BoxInstance[] = [];
  const unknown = new Set<string>();

  for (const entry of placed) {
    if (entry.item === null) {
      unknown.add(entry.catalogId);
      continue;
    }
    const proxy = proxyCache(entry.item);
    for (const box of proxy.boxes) {
      const { px, py } = placeOffset(entry.pose, box.cx, box.cy);
      instances.push({
        furnitureId: entry.id,
        catalogId: entry.catalogId,
        category: entry.item.category,
        boxKey: box.key,
        px,
        py,
        pz: box.cz,
        deg: entry.pose.rotationDeg,
        wMm: box.wMm,
        dMm: box.dMm,
        hMm: box.hMm,
      });
    }
  }

  return { instances, unknownCatalogIds: [...unknown] };
}

/**
 * The plan-view footprint ring of one item, in millimetres, ready to stroke.
 *
 * Shared with the preview so a placed item and a hovering one are drawn by the
 * same code — an outline that shifts by a millimetre on commit is the kind of
 * detail that quietly erodes trust in the whole canvas.
 */
export function footprintRingMm(item: CatalogueItem, pose: Pose): { x: number; y: number }[] {
  const hw = item.widthMm / 2;
  const hd = item.depthMm / 2;
  return [
    placeOffset(pose, -hw, -hd),
    placeOffset(pose, hw, -hd),
    placeOffset(pose, hw, hd),
    placeOffset(pose, -hw, hd),
  ].map((p) => ({ x: p.px, y: p.py }));
}

/** The clearance strip ring, or `null` when the item needs no access space. */
export function clearanceRingMm(
  item: CatalogueItem,
  pose: Pose,
): { x: number; y: number }[] | null {
  if (item.clearanceMm <= 0) return null;
  const hw = item.widthMm / 2;
  const near = item.depthMm / 2;
  const far = near + item.clearanceMm;
  return [
    placeOffset(pose, -hw, near),
    placeOffset(pose, hw, near),
    placeOffset(pose, hw, far),
    placeOffset(pose, -hw, far),
  ].map((p) => ({ x: p.px, y: p.py }));
}

// ---------------------------------------------------------------------------
// Wireframe
// ---------------------------------------------------------------------------

/** 12 edges of a unit cube, as pairs of corner indices. */
const CUBE_EDGES: readonly (readonly [number, number])[] = [
  [0, 1],
  [1, 3],
  [3, 2],
  [2, 0], // bottom
  [4, 5],
  [5, 7],
  [7, 6],
  [6, 4], // top
  [0, 4],
  [1, 5],
  [2, 6],
  [3, 7], // verticals
];

/** Corner sign pattern, indexed to match {@link CUBE_EDGES}. */
const CUBE_CORNERS: readonly (readonly [number, number, number])[] = [
  [-1, -1, -1],
  [1, -1, -1],
  [-1, 1, -1],
  [1, 1, -1],
  [-1, -1, 1],
  [1, -1, 1],
  [-1, 1, 1],
  [1, 1, 1],
];

/**
 * A merged wireframe for every box, as one packed position buffer.
 *
 * One `LineSegments` with one draw call, rather than an outline object per
 * item. Rebuilt only when the furniture set changes; a G+2 demo storey with 60
 * items comes to roughly 15 000 floats, which uploads in well under a frame.
 *
 * Outlines matter more in this feature than they look: a plan of solid filled
 * rectangles with no edges reads as a colour blob, and an architect checking a
 * bedroom wants to see where the wardrobe stops and the bed starts.
 */
export function buildEdgePositions(
  instances: readonly BoxInstance[],
  axes: PlanAxes,
  unitsPerMm: number,
): Float32Array {
  const out = new Float32Array(instances.length * CUBE_EDGES.length * 2 * 3);
  let offset = 0;

  for (const inst of instances) {
    const rad = inst.deg * DEG_TO_RAD;
    const c = Math.cos(rad);
    const s = Math.sin(rad);
    const hw = inst.wMm / 2;
    const hd = inst.dMm / 2;
    const hh = inst.hMm / 2;

    for (const edge of CUBE_EDGES) {
      for (const cornerIndex of edge) {
        const corner = CUBE_CORNERS[cornerIndex];
        if (corner === undefined) continue;
        const lx = corner[0] * hw;
        const ly = corner[1] * hd;
        const lz = corner[2] * hh;
        writeScenePosition(
          out,
          offset,
          inst.px + lx * c - ly * s,
          inst.py + lx * s + ly * c,
          inst.pz + lz,
          axes,
          unitsPerMm,
        );
        offset += 3;
      }
    }
  }

  return out;
}

/** Scene-space extents of one instance's box, for the matrix upload. */
export function instanceScale(
  inst: BoxInstance,
  axes: PlanAxes,
  unitsPerMm: number,
): [number, number, number] {
  return sceneScale(inst.wMm, inst.dMm, inst.hMm, axes, unitsPerMm);
}

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------

/**
 * Category fills, muted on purpose.
 *
 * Furniture is context on an architectural drawing, not the subject: it must be
 * legible under the walls, dimensions and compliance chips that sit above it
 * and never compete with them. These are low-saturation hues that stay distinct
 * from each other and from the plan's black-and-white line work, and they hold
 * up in both the light and dark themes the app ships.
 */
export const CATEGORY_COLOR: Readonly<Record<FurnitureCategory, string>> = {
  bed: '#8fa8c8',
  seating: '#9ec0ae',
  table: '#c9b48e',
  storage: '#b7a3c4',
  kitchen: '#d0a894',
  sanitary: '#93bcc6',
  appliance: '#a8adb8',
  vehicle: '#b0b6a0',
  service: '#c0b3a0',
  other: '#b3b3b3',
};

/** Preview fills by advisory tone. Amber informs; it never means "refused". */
export const PREVIEW_COLOR: Readonly<Record<'ok' | 'info' | 'warn', string>> = {
  ok: '#4f8ef7',
  info: '#4f8ef7',
  warn: '#e0912f',
};

/** The clearance strip: always the same, always faint, always translucent. */
export const CLEARANCE_COLOR = '#7aa7d9';
export const CLEARANCE_OPACITY = 0.18;
