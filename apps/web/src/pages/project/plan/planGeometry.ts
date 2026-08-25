/**
 * planGeometry — what a plan LOOKS like, derived from the model document.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS LIVES HERE
 * ════════════════════════════════════════════════════════════════════════════
 * The Phase-4 canvas arrived as four modules — `core` (scene, camera, picker),
 * `tools` (the state machines), `overlays` (dimensions, tags, chips, inspector)
 * and `furniture`. None of them draws a wall: `tools` explicitly disclaims "the
 * rendered walls, rooms" and `overlays` explicitly disclaims everything that is
 * not drawn ON TOP of the plan. So the plan itself — walls, openings, room
 * fills, stairs, balconies, columns — had no owner. This module and its two
 * sibling components are that owner, and they sit under `pages/project/plan/`
 * because the integrator owns `pages/**`.
 *
 * If a `features/canvas/scene/` module is ever created, move these three files
 * there unchanged: nothing in them knows it is inside a page.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CONVERSION BOUNDARY
 * ════════════════════════════════════════════════════════════════════════════
 * Everything in here is DERIVED, RENDER-ONLY geometry. Nothing it returns
 * becomes an op payload — ops come from `features/canvas/tools/editOps` and
 * from the overlays' `edit.ts`/`fields.ts`, and only from there.
 *
 * That is why two return types exist:
 *
 *   `Pt`   integer mm. Used where the value is handed to something that also
 *          accepts model geometry (`OutlinePolygon`, `bboxOfMm`), so the types
 *          line up and a stray float cannot leak into a `Polygon`.
 *   `PtF`  float mm, RENDER ONLY. Used for the merged buffers, because a wall
 *          face at ±57.5 mm off the centreline must not be rounded to 58 on one
 *          wall and 57 on the wall it joins — the seam is visible at 1:50.
 *
 * Float mm → world units happens once, in the components, via
 * `WORLD_UNITS_PER_MM`. There is no other conversion in this folder.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * PERFORMANCE (§14)
 * ════════════════════════════════════════════════════════════════════════════
 * Everything here is pure and is called from a `useMemo` keyed on the document
 * and the storey. A pan or a zoom does not touch any of it: the camera lives
 * outside React (`ViewportController`), so nothing in this file runs during a
 * gesture. The only per-frame work in the plan scene is the preview layer's
 * buffer rewrite, which is bounded by the size of one rubber band.
 */

import {
  bbox,
  pointAlongSeg,
  polygonCentroid,
  triangulate,
  type Bbox,
  type Balcony,
  type Column,
  type Direction4,
  type HouseModel,
  type Opening,
  type Polygon,
  type Pt,
  type Room,
  type Stair,
  type Wall,
} from '@garh/model';

/** Float millimetres. RENDER ONLY — never an op payload. See the header. */
export interface PtF {
  readonly x: number;
  readonly y: number;
}

/** A closed run of solid wall between openings, as an interval along the wall. */
export interface WallRun {
  readonly startMm: number;
  readonly endMm: number;
}

/** One quad of wall, as 4 float-mm corners in ring order. */
export type QuadF = readonly [PtF, PtF, PtF, PtF];

// ---------------------------------------------------------------------------
// Storey scoping
// ---------------------------------------------------------------------------

export function wallsOfStorey(house: HouseModel, storeyId: string | null): Wall[] {
  if (storeyId === null) return [];
  return house.walls.filter((w) => w.storeyId === storeyId);
}

export function roomsOfStorey(house: HouseModel, storeyId: string | null): Room[] {
  if (storeyId === null) return [];
  return house.rooms.filter((r) => r.storeyId === storeyId);
}

export function stairsOfStorey(house: HouseModel, storeyId: string | null): Stair[] {
  if (storeyId === null) return [];
  return house.stairs.filter((s) => s.storeyId === storeyId);
}

export function balconiesOfStorey(house: HouseModel, storeyId: string | null): Balcony[] {
  if (storeyId === null) return [];
  return house.balconies.filter((b) => b.storeyId === storeyId);
}

export function columnsOfStorey(house: HouseModel, storeyId: string | null): Column[] {
  if (storeyId === null) return [];
  return house.columns.filter((c) => c.storeyId === storeyId);
}

/** Openings hosted by walls on this storey, paired with their host. */
export function openingsOfStorey(
  house: HouseModel,
  storeyId: string | null,
): { opening: Opening; wall: Wall }[] {
  if (storeyId === null) return [];
  const walls = new Map(house.walls.filter((w) => w.storeyId === storeyId).map((w) => [w.id, w]));
  const out: { opening: Opening; wall: Wall }[] = [];
  for (const opening of house.openings) {
    const wall = walls.get(opening.wallId);
    if (wall !== undefined) out.push({ opening, wall });
  }
  return out;
}

/** Index of a storey in the ordered list, or -1. Ground floor is 0. */
export function storeyIndex(house: HouseModel, storeyId: string | null): number {
  if (storeyId === null) return -1;
  return house.storeys.findIndex((s) => s.id === storeyId);
}

/**
 * Finished floor level of a storey, in mm above the plot datum.
 *
 * `levels.fflPerStoreyMm` is index-aligned with `storeys` (§3). When it is
 * short — a storey added but not yet levelled — the running sum of storey
 * heights is the honest fallback rather than 0, which would stack two floors
 * on the same plane in the 3D view Phase 5 shares this with.
 */
export function storeyFflMm(house: HouseModel, storeyId: string | null): number {
  const index = storeyIndex(house, storeyId);
  if (index < 0) return 0;
  const stated = house.levels.fflPerStoreyMm[index];
  if (stated !== undefined) return stated;
  let ffl = house.levels.plinthMm;
  for (let i = 0; i < index; i += 1) ffl += house.storeys[i]?.heightMm ?? 0;
  return ffl;
}

// ---------------------------------------------------------------------------
// Walls
// ---------------------------------------------------------------------------

function wallLengthMm(wall: Wall): number {
  return Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y);
}

/**
 * Unit direction and left normal of a wall, in float mm space.
 *
 * Returns `null` for a degenerate wall. The model's own validation rejects
 * zero-length walls, but a document arriving from an older op log or a partial
 * rebase can still contain one, and a NaN in a vertex buffer poisons the whole
 * merged mesh — every wall on the storey vanishes, not just the bad one.
 */
function wallFrame(wall: Wall): { ux: number; uy: number; nx: number; ny: number; lenMm: number } | null {
  const dx = wall.b.x - wall.a.x;
  const dy = wall.b.y - wall.a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const ux = dx / len;
  const uy = dy / len;
  return { ux, uy, nx: -uy, ny: ux, lenMm: len };
}

/**
 * The four corners of a whole wall, as float mm.
 *
 * Ring order is a→b along the left face, then b→a along the right face, so the
 * quad is a simple (non-self-intersecting) ring for triangulation and for a
 * closed outline.
 */
export function wallQuadF(wall: Wall): QuadF | null {
  return wallSpanQuadF(wall, 0, wallLengthMm(wall));
}

/** The quad of one interval along a wall, in float mm. */
export function wallSpanQuadF(wall: Wall, fromMm: number, toMm: number): QuadF | null {
  const frame = wallFrame(wall);
  if (frame === null || toMm <= fromMm) return null;
  const half = wall.thicknessMm / 2;
  const { ux, uy, nx, ny } = frame;
  const ax = wall.a.x + ux * fromMm;
  const ay = wall.a.y + uy * fromMm;
  const bx = wall.a.x + ux * toMm;
  const by = wall.a.y + uy * toMm;
  return [
    { x: ax + nx * half, y: ay + ny * half },
    { x: bx + nx * half, y: by + ny * half },
    { x: bx - nx * half, y: by - ny * half },
    { x: ax - nx * half, y: ay - ny * half },
  ];
}

/**
 * The wall's rectangle as INTEGER mm — for selection outlines and bboxes,
 * where a half-millimetre is invisible and matching `Polygon` is worth more.
 */
export function wallRingMm(wall: Wall): Pt[] {
  const quad = wallQuadF(wall);
  if (quad === null) return [];
  return quad.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }));
}

/**
 * Solid runs of a wall — the wall minus every opening hosted on it.
 *
 * This is what makes a door read as a door in plan: the wall fill genuinely
 * stops at the jamb instead of a paper-coloured patch being painted over it.
 * Openings are clamped to the wall and merged when they overlap, so a document
 * with two overlapping openings (which `validate` rejects but a rebase can
 * briefly produce) draws one gap rather than a negative-length run.
 */
export function wallRuns(wall: Wall, openings: readonly Opening[]): WallRun[] {
  const lenMm = wallLengthMm(wall);
  if (lenMm <= 0) return [];

  const gaps: WallRun[] = [];
  for (const opening of openings) {
    if (opening.wallId !== wall.id) continue;
    const half = opening.widthMm / 2;
    const start = Math.max(0, opening.offsetMm - half);
    const end = Math.min(lenMm, opening.offsetMm + half);
    if (end > start) gaps.push({ startMm: start, endMm: end });
  }
  if (gaps.length === 0) return [{ startMm: 0, endMm: lenMm }];

  gaps.sort((a, b) => a.startMm - b.startMm);
  const merged: { startMm: number; endMm: number }[] = [];
  for (const gap of gaps) {
    const last = merged[merged.length - 1];
    if (last !== undefined && gap.startMm <= last.endMm) {
      last.endMm = Math.max(last.endMm, gap.endMm);
    } else {
      merged.push({ startMm: gap.startMm, endMm: gap.endMm });
    }
  }

  const runs: WallRun[] = [];
  let cursor = 0;
  for (const gap of merged) {
    if (gap.startMm > cursor) runs.push({ startMm: cursor, endMm: gap.startMm });
    cursor = Math.max(cursor, gap.endMm);
  }
  if (cursor < lenMm) runs.push({ startMm: cursor, endMm: lenMm });
  return runs;
}

// ---------------------------------------------------------------------------
// Openings — the plan symbols
// ---------------------------------------------------------------------------

/** Line segments (float mm) that draw one opening's plan symbol. */
export interface OpeningSymbol {
  readonly openingId: string;
  readonly kind: Opening['kind'];
  /** Jamb lines across the wall, plus the leaf/glazing lines. */
  readonly lines: readonly (readonly [PtF, PtF])[];
  /** Door swing arc, already flattened to a polyline. Empty for windows. */
  readonly arc: readonly PtF[];
  /** Clickable rectangle of the opening, integer mm, for the selection outline. */
  readonly ringMm: readonly Pt[];
}

/** How many segments a 90° door arc is flattened into. Twelve reads smooth at 1:50. */
const ARC_STEPS = 12;

export function openingSymbol(wall: Wall, opening: Opening): OpeningSymbol | null {
  const frame = wallFrame(wall);
  if (frame === null) return null;

  const { ux, uy, nx, ny, lenMm } = frame;
  const half = opening.widthMm / 2;
  const startMm = Math.max(0, Math.min(lenMm, opening.offsetMm - half));
  const endMm = Math.max(0, Math.min(lenMm, opening.offsetMm + half));
  const t = wall.thicknessMm / 2;

  const at = (alongMm: number, acrossMm: number): PtF => ({
    x: wall.a.x + ux * alongMm + nx * acrossMm,
    y: wall.a.y + uy * alongMm + ny * acrossMm,
  });

  const lines: (readonly [PtF, PtF])[] = [
    // Jambs: the two lines that close the gap in the wall fill.
    [at(startMm, t), at(startMm, -t)],
    [at(endMm, t), at(endMm, -t)],
  ];

  const arc: PtF[] = [];

  if (opening.kind === 'door') {
    // Leaf hinged at the swing's hand, opening to the swing's side.
    const hingeAtStart = opening.swing === 'in-left' || opening.swing === 'out-left';
    const side = opening.swing === 'in-left' || opening.swing === 'in-right' ? 1 : -1;
    const hingeMm = hingeAtStart ? startMm : endMm;
    const leafMm = opening.widthMm;
    const hinge = at(hingeMm, 0);

    // The open leaf, drawn at 90°: perpendicular to the wall, on `side`.
    lines.push([hinge, at(hingeMm, side * leafMm)] as const);

    // Arc from the leaf's open position back to the closed position.
    const dirSign = hingeAtStart ? 1 : -1;
    for (let i = 0; i <= ARC_STEPS; i += 1) {
      const angle = (Math.PI / 2) * (i / ARC_STEPS);
      const across = Math.cos(angle) * leafMm * side;
      const along = Math.sin(angle) * leafMm * dirSign;
      arc.push({
        x: hinge.x + ux * along + nx * across,
        y: hinge.y + uy * along + ny * across,
      });
    }
  } else {
    // Window / ventilator: two glazing lines inside the reveal.
    const inset = wall.thicknessMm / 6;
    lines.push([at(startMm, t - inset), at(endMm, t - inset)] as const);
    lines.push([at(startMm, -t + inset), at(endMm, -t + inset)] as const);
  }

  const ring: Pt[] = [
    at(startMm, t),
    at(endMm, t),
    at(endMm, -t),
    at(startMm, -t),
  ].map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }));

  return { openingId: opening.id, kind: opening.kind, lines, arc, ringMm: ring };
}

// ---------------------------------------------------------------------------
// Stairs
// ---------------------------------------------------------------------------

const DIRECTION_VECTOR: Readonly<Record<Direction4, { x: number; y: number }>> = {
  N: { x: 0, y: 1 },
  E: { x: 1, y: 0 },
  S: { x: 0, y: -1 },
  W: { x: -1, y: 0 },
};

export function directionVector(direction: Direction4): { x: number; y: number } {
  return DIRECTION_VECTOR[direction];
}

export interface StairSymbol {
  readonly stairId: string;
  /** Footprint ring, integer mm — pickable and outline-able. */
  readonly ringMm: readonly Pt[];
  /** One line per riser, across the flight. */
  readonly treads: readonly (readonly [Pt, Pt])[];
  /** UP arrow, [tail, head]. */
  readonly arrow: readonly [Pt, Pt];
}

/**
 * A stair in plan: footprint, riser lines, UP arrow.
 *
 * MVP honesty: the flight is drawn as ONE straight run of `risersCount` treads
 * even for `dogleg`/`L`/`U` kinds, because the model stores a single origin,
 * direction and landing block rather than a per-flight path. Drawing an
 * invented turn would put a shape on a municipal drawing that the model does
 * not contain. The landing, when present, is drawn as the tail of the run.
 */
export function stairSymbol(stair: Stair): StairSymbol {
  const dir = DIRECTION_VECTOR[stair.direction];
  // Right-hand perpendicular, so width runs across the direction of travel.
  const perp = { x: dir.y, y: -dir.x };

  const runMm = stair.risersCount * stair.treadMm;
  const landingMm = stair.landing === null ? 0 : stair.landing.depthMm;
  const totalMm = runMm + landingMm;
  const widthMm = stair.widthMm;

  const corner = (alongMm: number, acrossMm: number): Pt => ({
    x: Math.round(stair.origin.x + dir.x * alongMm + perp.x * acrossMm),
    y: Math.round(stair.origin.y + dir.y * alongMm + perp.y * acrossMm),
  });

  const ringMm: Pt[] = [
    corner(0, 0),
    corner(totalMm, 0),
    corner(totalMm, widthMm),
    corner(0, widthMm),
  ];

  const treads: (readonly [Pt, Pt])[] = [];
  for (let i = 1; i <= stair.risersCount; i += 1) {
    const alongMm = i * stair.treadMm;
    if (alongMm > runMm) break;
    treads.push([corner(alongMm, 0), corner(alongMm, widthMm)] as const);
  }

  const arrow: readonly [Pt, Pt] = [
    corner(stair.treadMm / 2, widthMm / 2),
    corner(Math.max(stair.treadMm, runMm - stair.treadMm / 2), widthMm / 2),
  ];

  return { stairId: stair.id, ringMm, treads, arrow };
}

// ---------------------------------------------------------------------------
// Columns
// ---------------------------------------------------------------------------

export function columnRingMm(column: Column): Pt[] {
  const halfW = Math.round(column.sizeMm.xMm / 2);
  const halfD = Math.round(column.sizeMm.yMm / 2);
  return [
    { x: column.pt.x - halfW, y: column.pt.y - halfD },
    { x: column.pt.x + halfW, y: column.pt.y - halfD },
    { x: column.pt.x + halfW, y: column.pt.y + halfD },
    { x: column.pt.x - halfW, y: column.pt.y + halfD },
  ];
}

// ---------------------------------------------------------------------------
// Triangulation helper shared by rooms, balconies and columns
// ---------------------------------------------------------------------------

/**
 * Triangulate a ring into a flat `[x,y, x,y, …]` list of float-mm vertices,
 * three per triangle.
 *
 * `triangulate` from the model core is the same ear-clipping routine the area
 * statement uses, so a room's drawn shape and its printed area can never come
 * from two different interpretations of the same polygon.
 */
export function triangleVerticesMm(polygon: Polygon): number[] {
  if (polygon.length < 3) return [];
  const out: number[] = [];
  for (const [a, b, c] of triangulate(polygon)) {
    out.push(a.x, a.y, b.x, b.y, c.x, c.y);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Extents
// ---------------------------------------------------------------------------

/**
 * What "fit to screen" should frame: the storey's geometry, or the plot when
 * the storey is still empty.
 *
 * Returns `null` when there is nothing at all — the caller then leaves the
 * camera where it is instead of framing an empty box, which would zoom to a
 * meaningless magnification on a brand-new project.
 */
export function planExtentMm(
  house: HouseModel,
  storeyId: string | null,
  plotBoundary: Polygon,
): Bbox | null {
  const points: Pt[] = [];
  for (const wall of wallsOfStorey(house, storeyId)) {
    points.push(wall.a, wall.b);
  }
  for (const balcony of balconiesOfStorey(house, storeyId)) points.push(...balcony.polygon);
  for (const stair of stairsOfStorey(house, storeyId)) points.push(...stairSymbol(stair).ringMm);
  if (points.length === 0 && plotBoundary.length >= 3) points.push(...plotBoundary);
  if (points.length === 0) return null;
  return bbox(points);
}

/**
 * Bounding box of a set of element ids on the active storey — what a
 * compliance chip's "zoom to it" needs.
 *
 * Unknown ids are skipped rather than treated as the origin: a chip that cites
 * a plot edge would otherwise drag the camera to (0,0).
 */
export function elementsExtentMm(house: HouseModel, ids: readonly string[]): Bbox | null {
  if (ids.length === 0) return null;
  const wanted = new Set(ids);
  const points: Pt[] = [];

  for (const wall of house.walls) {
    if (wanted.has(wall.id)) points.push(...wallRingMm(wall));
  }
  for (const room of house.rooms) {
    if (wanted.has(room.id)) points.push(...room.polygon);
  }
  for (const stair of house.stairs) {
    if (wanted.has(stair.id)) points.push(...stairSymbol(stair).ringMm);
  }
  for (const balcony of house.balconies) {
    if (wanted.has(balcony.id)) points.push(...balcony.polygon);
  }
  for (const column of house.columns) {
    if (wanted.has(column.id)) points.push(...columnRingMm(column));
  }
  for (const furniture of house.furniture) {
    if (wanted.has(furniture.id)) points.push(furniture.pt);
  }
  for (const opening of house.openings) {
    if (!wanted.has(opening.id)) continue;
    const wall = house.walls.find((w) => w.id === opening.wallId);
    if (wall === undefined) continue;
    const symbol = openingSymbol(wall, opening);
    if (symbol !== null) points.push(...symbol.ringMm);
  }

  return points.length === 0 ? null : bbox(points);
}

/** Centre of a room polygon — where a "you are here" marker sits. */
export function roomCentreMm(room: Room): Pt {
  return polygonCentroid(room.polygon);
}

/** Point a fraction along a wall's centreline. Re-exported for the readouts. */
export function wallPointMm(wall: Wall, alongMm: number): Pt {
  return pointAlongSeg({ a: wall.a, b: wall.b }, alongMm);
}
