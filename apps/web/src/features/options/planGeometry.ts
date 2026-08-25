/**
 * Pure geometry for the options screen. NO React, NO stores, NO network —
 * everything here is deterministic integer/rational math over the option's own
 * JSON, which is what makes it provable with vitest on this machine.
 *
 * Coordinate convention: options arrive in plot-local integer millimetres with
 * +y pointing "up" the plot (north-ish before `plot.northDeg` rotation). SVG's
 * +y points down, so the viewBox transform below flips Y once, here, and
 * nowhere else. All outputs stay in mm units — the SVG viewBox does the
 * scaling, so no float pixel math ever touches a coordinate.
 */

import { ROOM_TYPE_LABELS } from '@garh/model';

import type { MiniPlan, OptionOp, Placement, PlanOption, PtMm } from './types';

// ---------------------------------------------------------------------------
// Wall extraction from the option's op expansion
// ---------------------------------------------------------------------------

export interface WallSeg {
  readonly a: PtMm;
  readonly b: PtMm;
  readonly thicknessMm: number;
  readonly kind: string;
  readonly storeyIndex: number;
}

export interface RoomLabel {
  readonly label: string;
  /** Label anchor (room centre), plot-local mm. */
  readonly x: number;
  readonly y: number;
  readonly storeyIndex: number;
  readonly areaMm2: number;
}

interface RawPt {
  x?: unknown;
  y?: unknown;
}

function readPt(raw: unknown): PtMm | null {
  const pt = raw as RawPt | null;
  if (pt === null || typeof pt !== 'object') return null;
  const { x, y } = pt;
  if (typeof x !== 'number' || typeof y !== 'number') return null;
  if (!Number.isInteger(x) || !Number.isInteger(y)) return null;
  return { x, y };
}

/**
 * Map each storey id minted by the solver to its floor index.
 *
 * Source of truth is the option's own `storey.add` ops (payload carries
 * `index`); a wall whose storey never appears falls back to first-seen order,
 * which keeps a partially-understood option renderable instead of blank.
 */
export function storeyIndexById(ops: readonly OptionOp[]): ReadonlyMap<string, number> {
  const byId = new Map<string, number>();
  for (const op of ops) {
    if (op.type !== 'storey.add') continue;
    const id = op.payload['id'];
    const index = op.payload['index'];
    if (typeof id === 'string' && typeof index === 'number' && Number.isInteger(index)) {
      byId.set(id, index);
    }
  }
  if (byId.size > 0) return byId;

  // No storey.add ops (partial re-solve reuses existing storeys): first-seen order.
  let next = 0;
  for (const op of ops) {
    if (op.type !== 'wall.add') continue;
    const storeyId = op.payload['storeyId'];
    if (typeof storeyId === 'string' && !byId.has(storeyId)) {
      byId.set(storeyId, next);
      next += 1;
    }
  }
  return byId;
}

/** Every wall the option's expansion draws, tagged with its floor index. */
export function extractWalls(ops: readonly OptionOp[]): WallSeg[] {
  const storeys = storeyIndexById(ops);
  const walls: WallSeg[] = [];
  for (const op of ops) {
    if (op.type !== 'wall.add') continue;
    const a = readPt(op.payload['a']);
    const b = readPt(op.payload['b']);
    if (a === null || b === null) continue;
    const thickness = op.payload['thicknessMm'];
    const storeyId = op.payload['storeyId'];
    const kind = op.payload['kind'];
    walls.push({
      a,
      b,
      thicknessMm:
        typeof thickness === 'number' && Number.isInteger(thickness) && thickness > 0
          ? thickness
          : 115,
      kind: typeof kind === 'string' ? kind : 'internal',
      storeyIndex: typeof storeyId === 'string' ? (storeys.get(storeyId) ?? 0) : 0,
    });
  }
  return walls;
}

/** Room labels from placements (the coordinated optional field). */
export function roomLabels(placements: readonly Placement[] | undefined): RoomLabel[] {
  if (!placements) return [];
  return placements.map((p) => ({
    label: labelForRoomType(p.roomType),
    x: p.xMm + Math.trunc(p.widthMm / 2),
    y: p.yMm + Math.trunc(p.depthMm / 2),
    storeyIndex: p.storeyIndex,
    areaMm2: p.widthMm * p.depthMm,
  }));
}

export function labelForRoomType(roomType: string): string {
  const label = (ROOM_TYPE_LABELS as Readonly<Record<string, string>>)[roomType];
  if (label !== undefined) return label;
  // "bedroom_2" → "Bedroom 2"; unknown types stay readable, never blank.
  const words = roomType.replace(/[_-]+/g, ' ').trim();
  return words === '' ? 'Room' : words.charAt(0).toUpperCase() + words.slice(1);
}

// ---------------------------------------------------------------------------
// Bounds + viewBox (the mm → SVG scaling contract; unit-tested)
// ---------------------------------------------------------------------------

export interface BoundsMm {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

export function boundsOfWalls(walls: readonly WallSeg[]): BoundsMm | null {
  if (walls.length === 0) return null;
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const w of walls) {
    // Half the thickness sticks out past each centreline endpoint.
    const half = Math.ceil(w.thicknessMm / 2);
    minX = Math.min(minX, w.a.x - half, w.b.x - half);
    minY = Math.min(minY, w.a.y - half, w.b.y - half);
    maxX = Math.max(maxX, w.a.x + half, w.b.x + half);
    maxY = Math.max(maxY, w.a.y + half, w.b.y + half);
  }
  return { minX, minY, maxX, maxY };
}

export function boundsOfPolygon(polygon: readonly PtMm[]): BoundsMm | null {
  if (polygon.length === 0) return null;
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const p of polygon) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  return { minX, minY, maxX, maxY };
}

export function unionBounds(a: BoundsMm | null, b: BoundsMm | null): BoundsMm | null {
  if (a === null) return b;
  if (b === null) return a;
  return {
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  };
}

export interface PlanViewBox {
  /** `viewBox` attribute value, mm units, already padded. */
  readonly viewBox: string;
  /** Width/height of the padded box in mm (drives font sizing). */
  readonly widthMm: number;
  readonly heightMm: number;
  /**
   * Map a plot-local point into viewBox space. The Y flip lives here:
   * plot +y (up) → SVG +y (down).
   */
  readonly toView: (p: PtMm) => { x: number; y: number };
  /** Stroke width for a wall of the given thickness, in viewBox (mm) units. */
  readonly strokeFor: (thicknessMm: number) => number;
  /** A font size (mm units) that keeps labels legible at any plan size. */
  readonly labelFontMm: number;
}

/**
 * Compute the mm-unit viewBox for a plan.
 *
 * `padRatio` is per-mille of the larger dimension (integer math — no float
 * creep into a contract that everything else asserts on exactly).
 */
export function planViewBox(bounds: BoundsMm, padPerMille = 60): PlanViewBox {
  const spanX = Math.max(1, bounds.maxX - bounds.minX);
  const spanY = Math.max(1, bounds.maxY - bounds.minY);
  const pad = Math.max(60, Math.trunc((Math.max(spanX, spanY) * padPerMille) / 1000));

  const widthMm = spanX + pad * 2;
  const heightMm = spanY + pad * 2;

  // After the Y flip, plot maxY maps to viewBox minY.
  const toView = (p: PtMm): { x: number; y: number } => ({
    x: p.x - bounds.minX + pad,
    y: bounds.maxY - p.y + pad,
  });

  const larger = Math.max(widthMm, heightMm);
  return {
    viewBox: `0 0 ${widthMm} ${heightMm}`,
    widthMm,
    heightMm,
    toView,
    // Walls read as lines, not slabs, at card size: floor at 60mm so a 115mm
    // partition survives rasterisation of a 12m plan into a 160px card.
    strokeFor: (thicknessMm: number) => Math.max(60, thicknessMm),
    labelFontMm: Math.max(220, Math.trunc(larger / 18)),
  };
}

// ---------------------------------------------------------------------------
// One entry point the components use
// ---------------------------------------------------------------------------

export interface MiniPlanGeometry {
  readonly walls: readonly WallSeg[];
  readonly labels: readonly RoomLabel[];
  readonly bounds: BoundsMm | null;
  /** Floor indices that actually have geometry, ascending. */
  readonly storeyIndices: readonly number[];
}

/** Geometry for one option, optionally filtered to one floor. */
export function miniPlanFromOption(option: PlanOption): MiniPlanGeometry {
  const walls = extractWalls(option.ops);
  const labels = roomLabels(option.placements);
  const indices = [...new Set([...walls.map((w) => w.storeyIndex), ...labels.map((l) => l.storeyIndex)])].sort(
    (a, b) => a - b,
  );
  return { walls, labels, bounds: boundsOfWalls(walls), storeyIndices: indices };
}

/** Geometry from a theater `miniPlan` event payload (silhouette contract). */
export function miniPlanFromEvent(payload: MiniPlan): MiniPlanGeometry {
  const walls: WallSeg[] = payload.walls.map((w) => ({
    a: w.a,
    b: w.b,
    thicknessMm: w.thicknessMm,
    kind: w.kind ?? 'internal',
    storeyIndex: payload.storeyIndex,
  }));
  const labels: RoomLabel[] = payload.rooms.map((r) => ({
    label: r.label,
    x: r.x,
    y: r.y,
    storeyIndex: payload.storeyIndex,
    areaMm2: 0,
  }));
  return {
    walls,
    labels,
    bounds: boundsOfWalls(walls),
    storeyIndices: [payload.storeyIndex],
  };
}

export function onStorey(geometry: MiniPlanGeometry, storeyIndex: number): MiniPlanGeometry {
  const walls = geometry.walls.filter((w) => w.storeyIndex === storeyIndex);
  const labels = geometry.labels.filter((l) => l.storeyIndex === storeyIndex);
  return { walls, labels, bounds: boundsOfWalls(walls), storeyIndices: [storeyIndex] };
}

// ---------------------------------------------------------------------------
// Vastu compass wheel geometry (pure; the component only assembles paths)
// ---------------------------------------------------------------------------

export const COMPASS_SECTORS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
export type CompassSector = (typeof COMPASS_SECTORS)[number];
/** The centre cell — brahmasthan. */
export type VastuZone = CompassSector | 'C';

/**
 * SVG path for one 45° annulus sector. Angles measured clockwise from north
 * (up), matching compass convention; the sector for direction D is centred on
 * D's bearing.
 */
export function sectorPath(
  cx: number,
  cy: number,
  rInner: number,
  rOuter: number,
  sector: CompassSector,
): string {
  const bearing = COMPASS_SECTORS.indexOf(sector) * 45;
  const start = ((bearing - 22.5) * Math.PI) / 180;
  const end = ((bearing + 22.5) * Math.PI) / 180;
  // Bearing 0 = up = (0, -1); clockwise positive.
  const px = (r: number, t: number): number => cx + r * Math.sin(t);
  const py = (r: number, t: number): number => cy - r * Math.cos(t);
  const fmt = (v: number): string => (Math.round(v * 100) / 100).toString();

  return [
    `M ${fmt(px(rInner, start))} ${fmt(py(rInner, start))}`,
    `L ${fmt(px(rOuter, start))} ${fmt(py(rOuter, start))}`,
    `A ${fmt(rOuter)} ${fmt(rOuter)} 0 0 1 ${fmt(px(rOuter, end))} ${fmt(py(rOuter, end))}`,
    `L ${fmt(px(rInner, end))} ${fmt(py(rInner, end))}`,
    `A ${fmt(rInner)} ${fmt(rInner)} 0 0 0 ${fmt(px(rInner, start))} ${fmt(py(rInner, start))}`,
    'Z',
  ].join(' ');
}

/** Label anchor for a sector, at radius `r`. */
export function sectorLabelPoint(
  cx: number,
  cy: number,
  r: number,
  sector: CompassSector,
): { x: number; y: number } {
  const t = (COMPASS_SECTORS.indexOf(sector) * 45 * Math.PI) / 180;
  return {
    x: Math.round((cx + r * Math.sin(t)) * 100) / 100,
    y: Math.round((cy - r * Math.cos(t)) * 100) / 100,
  };
}

/** Type guard used by the stats layer when reading zone strings off rules. */
export function isCompassSector(value: string): value is CompassSector {
  return (COMPASS_SECTORS as readonly string[]).includes(value);
}
