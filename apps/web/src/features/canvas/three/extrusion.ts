/**
 * extrusion.ts — pure profile maths for the 3D synthesis. INTEGER-MM MODEL IN,
 * FLOAT-MM RENDER PROFILES OUT. No three.js, no React, no WASM — this file is
 * what the vitest specs exercise.
 *
 * THE CONVERSION BOUNDARY (same contract as `planGeometry.ts` states for 2D):
 * everything returned here is DERIVED, RENDER-ONLY geometry in float
 * millimetres. Nothing in this module ever becomes an op payload. Float mm →
 * world units happens exactly once, in `geometryBuild.ts`, via
 * `WORLD_UNITS_PER_MM`.
 *
 * ELEVATIONS. The model speaks §3's language: `levels.fflPerStoreyMm` is
 * index-aligned with `storeys`, `plinthMm` lifts the ground floor off the
 * datum, `heightMm` is floor-to-floor. This module turns that into spans:
 *
 *      storey i walls:  FFL(i)  →  FFL(i+1) − slabAbove.thickness
 *      storey i slab:   FFL(i) − slab.thickness  →  FFL(i)      (model-derived)
 *      roof slab:       terrace − t  →  terrace,  terrace = FFL(top) + height
 *      plinth:          0  →  FFL(0) − groundSlab.thickness
 *
 * Walls stop under the slab they carry — that is both architecturally honest
 * and what keeps a wall's top face from z-fighting the slab resting on it.
 *
 * STAIR HONESTY (inherited fact 3): the model stores ONE origin + direction +
 * landing for every stair kind, so `stairSolidProfiles` renders ONE straight
 * flight of `risersCount` steps plus the landing box at the top of that run —
 * even for `dogleg`/`L`/`U`. Inventing the turn would draw geometry the model
 * does not carry. The 2D symbol (`planGeometry.stairSymbol`) makes the same
 * choice with the same direction/perpendicular convention, so the 3D stair
 * stands exactly over its plan symbol.
 */

import {
  ensureCcw,
  polygonSignedAreaMm2,
  type Direction4,
  type HouseModel,
  type Opening,
  type Polygon,
  type Slab,
  type Stair,
  type Wall,
} from '@garh/model';

import type { PtF } from '../core';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A vertical prism: a CCW float-mm footprint extruded from base to top. */
export interface PrismProfileF {
  /** Simple CCW ring, float mm. */
  readonly polygon: readonly PtF[];
  /** Elevation of the underside, mm above the plot datum. */
  readonly baseMm: number;
  /** Elevation of the top, mm above the plot datum. Always > baseMm. */
  readonly topMm: number;
}

/** Vertical span of one storey's built volume. */
export interface StoreySpanMm {
  /** FFL of the storey — where its walls start. */
  readonly baseMm: number;
  /** Where its walls stop: underside of the slab above (or of the roof slab). */
  readonly wallTopMm: number;
  /** FFL of the storey above, or base + height for the top storey. */
  readonly ceilingMm: number;
  /** Thickness of the slab the walls carry (storey above's, or reused for roof). */
  readonly slabAboveThicknessMm: number;
}

// ---------------------------------------------------------------------------
// Constants (render conventions, not model data — all documented here)
// ---------------------------------------------------------------------------

/** Extra depth an opening cut extends past each wall face, so the boolean
 * subtraction never leaves a coplanar film. Render-only slack. */
export const OPENING_CUT_SLACK_MM = 10;

/** Thickness of the pickable panel drawn inside an opening (glazing / leaf). */
export const OPENING_PANEL_THICKNESS_MM = 40;

/** Parapet band thickness — DEFAULTS.parapetThicknessMm restated locally so
 * this module has no dependency on the defaults object's shape. */
export const PARAPET_THICKNESS_MM = 115;

/** Mumty box height over the terrace-arriving stair. Massing convention. */
export const MUMTY_HEIGHT_MM = 2400;

/** OHT cylinder height and the facet count of its prism approximation. */
export const OHT_HEIGHT_MM = 1200;
export const OHT_SEGMENTS = 24;

/** Balcony railing band thickness. */
export const RAILING_THICKNESS_MM = 50;

/** Landing slab thickness (matches the default structural slab). */
export const LANDING_THICKNESS_MM = 150;

/** Slop when testing whether a balcony edge sits against a wall. */
const WALL_ADJACENCY_SLACK_MM = 60;

// ---------------------------------------------------------------------------
// Storey elevations
// ---------------------------------------------------------------------------

/** Index of a storey, or -1. Ground floor is 0. */
export function storeyIndexOf(house: HouseModel, storeyId: string): number {
  return house.storeys.findIndex((s) => s.id === storeyId);
}

/**
 * FFL of storey `index`, mm above datum. Reads `levels.fflPerStoreyMm` and
 * falls back to plinth + running storey heights when the array is short —
 * the same honest fallback `planGeometry.storeyFflMm` makes, restated here so
 * `features/canvas` does not import from `pages/**`.
 */
export function fflOfIndexMm(house: HouseModel, index: number): number {
  if (index < 0) return 0;
  const stated = house.levels.fflPerStoreyMm[index];
  if (stated !== undefined) return stated;
  let ffl = house.levels.plinthMm;
  for (let i = 0; i < index; i += 1) ffl += house.storeys[i]?.heightMm ?? 0;
  return ffl;
}

/**
 * The vertical span of one storey. Returns null for an unknown storey.
 *
 * The top storey's walls stop `slabThicknessMm` (its own level's value — the
 * roof slab reuses it, documented in `roofSolids`) under the terrace level.
 */
export function storeySpanMm(house: HouseModel, storeyId: string): StoreySpanMm | null {
  const index = storeyIndexOf(house, storeyId);
  const storey = house.storeys[index];
  if (index < 0 || storey === undefined) return null;

  const baseMm = fflOfIndexMm(house, index);
  const above = house.storeys[index + 1];
  const ceilingMm = above !== undefined ? fflOfIndexMm(house, index + 1) : baseMm + storey.heightMm;
  const slabAboveThicknessMm =
    above !== undefined ? above.level.slabThicknessMm : storey.level.slabThicknessMm;

  // A degenerate document (zero heights) must not produce an inverted prism.
  const wallTopMm = Math.max(baseMm + 100, ceilingMm - slabAboveThicknessMm);
  return { baseMm, wallTopMm, ceilingMm, slabAboveThicknessMm };
}

/** Terrace level: FFL of the top storey + its height. 0 when no storeys. */
export function terraceLevelMm(house: HouseModel): number {
  const top = house.storeys[house.storeys.length - 1];
  if (top === undefined) return 0;
  return fflOfIndexMm(house, house.storeys.length - 1) + top.heightMm;
}

/** The derived floor slab of a storey, or null. */
export function floorSlabOf(house: HouseModel, storeyId: string): Slab | null {
  return house.slabs.find((s) => s.storeyId === storeyId && s.kind === 'floor') ?? null;
}

// ---------------------------------------------------------------------------
// Wall frames and footprints
// ---------------------------------------------------------------------------

interface WallFrame {
  readonly ux: number;
  readonly uy: number;
  /** Left normal of the direction of travel a→b. */
  readonly nx: number;
  readonly ny: number;
  readonly lenMm: number;
}

/** Unit direction + left normal of a wall. Null for a degenerate wall —
 * the same guard `planGeometry.wallFrame` carries, for the same reason: a NaN
 * poisons the whole merged buffer, not just the bad wall. */
function wallFrame(wall: Wall): WallFrame | null {
  const dx = wall.b.x - wall.a.x;
  const dy = wall.b.y - wall.a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const ux = dx / len;
  const uy = dy / len;
  return { ux, uy, nx: -uy, ny: ux, lenMm: len };
}

export function wallLengthMm(wall: Wall): number {
  return Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y);
}

/**
 * The wall's plan footprint as a CCW float-mm quad (centreline ± half
 * thickness). CCW is load-bearing: `geometryBuild` derives the outward side
 * normals from the winding, and Manifold requires positive-area rings.
 */
export function wallFootprintF(wall: Wall): readonly PtF[] | null {
  return wallSpanFootprintF(wall, 0, wallLengthMm(wall), wall.thicknessMm / 2);
}

/** Footprint of one interval along a wall, widened to `halfMm` each side. */
export function wallSpanFootprintF(
  wall: Wall,
  fromMm: number,
  toMm: number,
  halfMm: number,
): readonly PtF[] | null {
  const frame = wallFrame(wall);
  if (frame === null || toMm <= fromMm) return null;
  const { ux, uy, nx, ny } = frame;
  const ax = wall.a.x + ux * fromMm;
  const ay = wall.a.y + uy * fromMm;
  const bx = wall.a.x + ux * toMm;
  const by = wall.a.y + uy * toMm;
  // Right face a→b, then left face b→a: CCW for any wall direction.
  return [
    { x: ax - nx * halfMm, y: ay - ny * halfMm },
    { x: bx - nx * halfMm, y: by - ny * halfMm },
    { x: bx + nx * halfMm, y: by + ny * halfMm },
    { x: ax + nx * halfMm, y: ay + ny * halfMm },
  ];
}

// ---------------------------------------------------------------------------
// Opening boxes
// ---------------------------------------------------------------------------

/**
 * The subtraction box of an opening, positioned against its host wall's real
 * geometry: along the centreline at `offsetMm` (the CENTRE, per §3), clamped
 * to the wall's length, spanning the wall thickness plus cut slack, from
 * `base + sillMm` up `heightMm` (clamped under the wall top).
 *
 * Returns null when the opening's span collapses — a zero span cuts nothing,
 * and handing Manifold a degenerate tool is how a boolean stack falls over.
 */
export function openingCutProfileF(
  wall: Wall,
  opening: Opening,
  storeyBaseMm: number,
  wallTopMm: number,
): PrismProfileF | null {
  return openingProfileF(
    wall,
    opening,
    storeyBaseMm,
    wallTopMm,
    wall.thicknessMm / 2 + OPENING_CUT_SLACK_MM,
  );
}

/**
 * The visible, pickable panel inside an opening (glazing for windows and
 * ventilators, the leaf for doors): the same placement maths as the cut, but
 * only `OPENING_PANEL_THICKNESS_MM` deep. This panel is what makes an opening
 * clickable in 3D — a hole cannot register with the PickRegistry.
 */
export function openingPanelProfileF(
  wall: Wall,
  opening: Opening,
  storeyBaseMm: number,
  wallTopMm: number,
): PrismProfileF | null {
  return openingProfileF(wall, opening, storeyBaseMm, wallTopMm, OPENING_PANEL_THICKNESS_MM / 2);
}

function openingProfileF(
  wall: Wall,
  opening: Opening,
  storeyBaseMm: number,
  wallTopMm: number,
  halfDepthMm: number,
): PrismProfileF | null {
  const lenMm = wallLengthMm(wall);
  if (lenMm <= 0) return null;

  const half = opening.widthMm / 2;
  const fromMm = Math.max(0, Math.min(lenMm, opening.offsetMm - half));
  const toMm = Math.max(0, Math.min(lenMm, opening.offsetMm + half));
  if (toMm <= fromMm) return null;

  const baseMm = storeyBaseMm + opening.sillMm;
  const topMm = Math.min(baseMm + opening.heightMm, wallTopMm);
  if (topMm <= baseMm) return null;

  const polygon = wallSpanFootprintF(wall, fromMm, toMm, halfDepthMm);
  if (polygon === null) return null;
  return { polygon, baseMm, topMm };
}

// ---------------------------------------------------------------------------
// Stairs — straight flights + landing boxes, from the params, honestly
// ---------------------------------------------------------------------------

/** Same mapping as `planGeometry.DIRECTION_VECTOR` — restated so the 3D stair
 * stands exactly over its 2D symbol without importing from `pages/**`. */
const DIRECTION_VECTOR: Readonly<Record<Direction4, { x: number; y: number }>> = {
  N: { x: 0, y: 1 },
  E: { x: 1, y: 0 },
  S: { x: 0, y: -1 },
  W: { x: -1, y: 0 },
};

/**
 * The solid boxes of one stair: `risersCount` stepped boxes (each from the
 * storey base up to that step's tread level — a stepped concrete solid, not a
 * floating tread) followed by the landing platform at the top of the run,
 * when the model carries one.
 *
 * LIMITATION, RENDERED HONESTLY: one straight run for every `StairKind`. See
 * the module header.
 */
export function stairSolidProfilesF(stair: Stair, storeyBaseMm: number): PrismProfileF[] {
  const dir = DIRECTION_VECTOR[stair.direction];
  // Right-hand perpendicular: width runs across travel, like `stairSymbol`.
  const perp = { x: dir.y, y: -dir.x };

  const corner = (alongMm: number, acrossMm: number): PtF => ({
    x: stair.origin.x + dir.x * alongMm + perp.x * acrossMm,
    y: stair.origin.y + dir.y * alongMm + perp.y * acrossMm,
  });

  const rect = (a0: number, a1: number, c0: number, c1: number): readonly PtF[] =>
    ensureCcwF([corner(a0, c0), corner(a1, c0), corner(a1, c1), corner(a0, c1)]);

  const out: PrismProfileF[] = [];
  for (let k = 1; k <= stair.risersCount; k += 1) {
    out.push({
      polygon: rect((k - 1) * stair.treadMm, k * stair.treadMm, 0, stair.widthMm),
      baseMm: storeyBaseMm,
      topMm: storeyBaseMm + k * stair.riserMm,
    });
  }

  if (stair.landing !== null) {
    const runMm = stair.risersCount * stair.treadMm;
    const flightTopMm = storeyBaseMm + stair.risersCount * stair.riserMm;
    out.push({
      polygon: rect(runMm, runMm + stair.landing.depthMm, 0, stair.landing.widthMm),
      baseMm: flightTopMm - LANDING_THICKNESS_MM,
      topMm: flightTopMm,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Parapet ring
// ---------------------------------------------------------------------------

/**
 * Per-edge parapet band footprints, inset INSIDE the perimeter ring. Each
 * segment is extended half a thickness at both ends so corners meet solid
 * instead of leaving a notch — overlap is harmless for opaque geometry and
 * avoids needing a boolean union on the hot path.
 */
export function parapetSegmentFootprintsF(
  perimeter: Polygon,
  thicknessMm: number = PARAPET_THICKNESS_MM,
): (readonly PtF[])[] {
  const ring = ensureCcw(perimeter);
  if (ring.length < 3) return [];

  const out: (readonly PtF[])[] = [];
  for (let i = 0; i < ring.length; i += 1) {
    const p = ring[i];
    const q = ring[(i + 1) % ring.length];
    if (p === undefined || q === undefined) continue;
    const dx = q.x - p.x;
    const dy = q.y - p.y;
    const len = Math.hypot(dx, dy);
    if (len === 0) continue;
    const ux = dx / len;
    const uy = dy / len;
    // CCW ring ⇒ interior is to the LEFT of the directed edge.
    const inx = -uy;
    const iny = ux;
    const ext = thicknessMm / 2;
    const a = { x: p.x - ux * ext, y: p.y - uy * ext };
    const b = { x: q.x + ux * ext, y: q.y + uy * ext };
    out.push(
      ensureCcwF([
        { x: a.x, y: a.y },
        { x: b.x, y: b.y },
        { x: b.x + inx * thicknessMm, y: b.y + iny * thicknessMm },
        { x: a.x + inx * thicknessMm, y: a.y + iny * thicknessMm },
      ]),
    );
  }
  return out;
}

// ---------------------------------------------------------------------------
// OHT cylinder (as an N-gon prism — procedural, no binary assets)
// ---------------------------------------------------------------------------

/** A regular polygon approximating the OHT cylinder's cross-section. CCW. */
export function regularPolygonF(
  centre: PtF,
  radiusMm: number,
  segments: number = OHT_SEGMENTS,
): readonly PtF[] {
  const out: PtF[] = [];
  for (let i = 0; i < segments; i += 1) {
    const angle = (2 * Math.PI * i) / segments;
    out.push({
      x: centre.x + radiusMm * Math.cos(angle),
      y: centre.y + radiusMm * Math.sin(angle),
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Balcony railing adjacency
// ---------------------------------------------------------------------------

/** Squared distance from a float point to a wall's centreline segment. */
function distPtToWallSq(px: number, py: number, wall: Wall): number {
  const ax = wall.a.x;
  const ay = wall.a.y;
  const dx = wall.b.x - ax;
  const dy = wall.b.y - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) {
    const ex = px - ax;
    const ey = py - ay;
    return ex * ex + ey * ey;
  }
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  const ex = px - cx;
  const ey = py - cy;
  return ex * ex + ey * ey;
}

/**
 * True when a balcony edge sits against one of the storey's walls — its
 * midpoint is within half that wall's thickness (plus slack) of the
 * centreline. Such edges get no railing: the building is the railing there.
 */
export function edgeTouchesWall(a: PtF, b: PtF, walls: readonly Wall[]): boolean {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  for (const wall of walls) {
    const reach = wall.thicknessMm / 2 + WALL_ADJACENCY_SLACK_MM;
    if (distPtToWallSq(mx, my, wall) <= reach * reach) return true;
  }
  return false;
}

/**
 * Railing band footprints for a balcony: one inset band per outer edge (edges
 * that touch a wall are skipped), same corner-extension trick as the parapet.
 */
export function balconyRailingFootprintsF(
  polygon: Polygon,
  walls: readonly Wall[],
  thicknessMm: number = RAILING_THICKNESS_MM,
): (readonly PtF[])[] {
  const ring = ensureCcw(polygon);
  if (ring.length < 3) return [];
  const out: (readonly PtF[])[] = [];
  const segments = parapetSegmentFootprintsF(ring, thicknessMm);
  // `parapetSegmentFootprintsF` walks ring edges in order; pair them back up
  // with the source edge to apply the adjacency filter.
  let edge = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const p = ring[i];
    const q = ring[(i + 1) % ring.length];
    if (p === undefined || q === undefined) continue;
    if (Math.hypot(q.x - p.x, q.y - p.y) === 0) continue;
    const footprint = segments[edge];
    edge += 1;
    if (footprint === undefined) continue;
    if (edgeTouchesWall(p, q, walls)) continue;
    out.push(footprint);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Small shared helpers
// ---------------------------------------------------------------------------

/** Ensure a float ring is CCW (shoelace on floats — orientation only). */
export function ensureCcwF(ring: readonly PtF[]): readonly PtF[] {
  // polygonSignedAreaMm2 is typed on integer `Polygon` but is pure shoelace
  // arithmetic; float points are structurally valid and orientation is all we
  // read from it.
  return polygonSignedAreaMm2(ring as Polygon) < 0 ? ring.slice().reverse() : ring;
}
