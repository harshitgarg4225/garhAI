/**
 * componentBoxes.ts — from a facade component to renderable geometry, purely.
 *
 * ONE GEOMETRY SOURCE, TWO CONSUMERS. `boxesForComponent(house, component)`
 * turns a component + the model it is anchored to into a list of oriented
 * boxes in plan-mm space. `FacadeLayer` extrudes those boxes into the shared
 * R3F scene; `KitThumbnail` projects the SAME boxes onto a wall to draw the
 * kit card's SVG. The §15 requirement that previews "never drift from the
 * geometry" is satisfied structurally: there is no second description of what
 * a chajja looks like.
 *
 * PLACEMENT IS DERIVED, PARAMS ARE AUTHORED. A component's params carry the
 * kit's decisions (projection, thickness, style, colour); everything spatial —
 * where the host wall is, which side is outside, how high the lintel sits —
 * is read from the model AT RENDER TIME. Move a wall and its chajjas follow on
 * the next render, with no facade op and no regeneration. Delete the host
 * opening and the component quietly produces zero boxes: an orphaned component
 * renders as nothing rather than as garbage at the origin (the panel offers
 * regeneration for exactly this).
 *
 * FLOATS ARE FINE HERE — nothing in this module flows back toward an op.
 * The model stays integer mm; these boxes are render-only.
 *
 * WHICH SIDE IS OUTSIDE: the outward normal of an external wall is the
 * centreline perpendicular that points away from the length-weighted centroid
 * of the storey's external walls. Deterministic, allocation-light, correct for
 * the rectangular/L/T envelopes the MVP solver emits (the centroid is inside
 * the footprint). A future concave envelope that defeats this fails visibly —
 * a chajja indoors — never silently.
 */

import {
  distMm,
  polygonEdges,
  segmentLengthMm,
  type Balcony,
  type FacadeComponent,
  type HouseModel,
  type Opening,
  type Pt,
  type Seg,
  type Storey,
  type Wall,
} from '@garh/model';

import { enumParam, intParam, strParam, RAILING_STYLES, type RailingStyle } from './types';

// ---------------------------------------------------------------------------
// The box
// ---------------------------------------------------------------------------

/**
 * An oriented box in plan-mm space. `(cx, cy)` is the plan centre; `dir` is
 * the unit vector of the box's length axis; depth extends along the plan
 * normal `(dir.y, -dir.x)` symmetric about the centre; elevation is mm above
 * plot datum.
 */
export interface OrientedBoxMm {
  readonly cx: number;
  readonly cy: number;
  readonly dirX: number;
  readonly dirY: number;
  readonly lenMm: number;
  readonly depthMm: number;
  readonly baseElevMm: number;
  readonly heightMm: number;
  readonly colorHex: string;
}

/** Fallback paint when a patch deleted a component's colour. Neutral, visible. */
const FALLBACK_HEX = '#9AA0A6';

/** Board thickness for recessed-window reveal linings. */
const REVEAL_BOARD_MM = 20;

/** MS railing member sections. */
const MS_TOP_RAIL_MM = 40;
const MS_POST_MM = 25;
const MS_POST_SPACING_MM = 1200;

/** Glass railing panel + rail sections. */
const GLASS_PANEL_MM = 12;
const GLASS_TOP_RAIL_MM = 50;
const GLASS_FLOOR_GAP_MM = 50;

/** Masonry railing thickness. */
const MASONRY_RAIL_MM = 115;

/** A balcony edge this close to a wall centreline is attached, not open. */
const EDGE_ATTACHED_TOLERANCE_MM = 200;

/** How proud the parapet cap sits of the parapet face, per side. */
const PARAPET_CAP_OVERHANG_MM = 30;

/** The banded parapet's shadow band. */
const PARAPET_BAND_HEIGHT_MM = 150;
const PARAPET_BAND_PROUD_MM = 20;

/** How proud a cladding zone sits of the wall face. */
const CLADDING_PROUD_MM = 25;

// ---------------------------------------------------------------------------
// Model-derived frames and elevations
// ---------------------------------------------------------------------------

export interface WallFrame {
  /** Unit direction a→b. */
  readonly dirX: number;
  readonly dirY: number;
  /** Unit outward normal. */
  readonly outX: number;
  readonly outY: number;
  readonly lenMm: number;
  readonly halfThicknessMm: number;
}

/**
 * Length-weighted centroid of a storey's external wall centrelines — the
 * "inside" reference the outward normal points away from.
 */
export function externalCentroid(house: HouseModel, storeyId: string): Pt | null {
  let sx = 0;
  let sy = 0;
  let total = 0;
  for (const w of house.walls) {
    if (w.storeyId !== storeyId || w.kind !== 'external') continue;
    const len = segmentLengthMm({ a: w.a, b: w.b });
    if (len === 0) continue;
    sx += ((w.a.x + w.b.x) / 2) * len;
    sy += ((w.a.y + w.b.y) / 2) * len;
    total += len;
  }
  if (total === 0) return null;
  return { x: sx / total, y: sy / total };
}

/** The wall's render frame, or null for a zero-length wall. */
export function wallFrame(wall: Wall, inside: Pt): WallFrame | null {
  const dx = wall.b.x - wall.a.x;
  const dy = wall.b.y - wall.a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const dirX = dx / len;
  const dirY = dy / len;
  // Perpendicular; flipped to point away from the inside reference.
  let outX = dirY;
  let outY = -dirX;
  const mx = (wall.a.x + wall.b.x) / 2;
  const my = (wall.a.y + wall.b.y) / 2;
  if ((mx - inside.x) * outX + (my - inside.y) * outY < 0) {
    outX = -outX;
    outY = -outY;
  }
  return { dirX, dirY, outX, outY, lenMm: len, halfThicknessMm: wall.thicknessMm / 2 };
}

function storeyOf(house: HouseModel, storeyId: string | null): Storey | null {
  if (storeyId === null) return null;
  return house.storeys.find((s) => s.id === storeyId) ?? null;
}

/** FFL of a storey (mm above plot datum). */
function baseElev(storey: Storey): number {
  return storey.level.fflMm;
}

/** Top of the top storey — where the parapet starts. */
function buildingTopElev(house: HouseModel): number {
  const top = house.storeys[house.storeys.length - 1];
  if (top === undefined) return 0;
  return top.level.fflMm + top.heightMm;
}

/** A box on a wall face, expressed in the wall frame. Allocates the box only. */
function wallBox(
  wall: Wall,
  frame: WallFrame,
  alongCentreMm: number,
  outCentreMm: number,
  lenMm: number,
  depthMm: number,
  baseElevMm: number,
  heightMm: number,
  colorHex: string,
): OrientedBoxMm {
  return {
    cx: wall.a.x + frame.dirX * alongCentreMm + frame.outX * outCentreMm,
    cy: wall.a.y + frame.dirY * alongCentreMm + frame.outY * outCentreMm,
    dirX: frame.dirX,
    dirY: frame.dirY,
    lenMm,
    depthMm,
    baseElevMm,
    heightMm,
    colorHex,
  };
}

// ---------------------------------------------------------------------------
// Per-kind builders
// ---------------------------------------------------------------------------

interface OpeningAnchor {
  readonly wall: Wall;
  readonly frame: WallFrame;
  readonly opening: Opening;
  readonly storey: Storey;
}

/** Resolve a component's opening + wall + storey, or null when orphaned. */
function resolveOpeningAnchor(house: HouseModel, component: FacadeComponent): OpeningAnchor | null {
  if (component.openingId === null || component.wallId === null) return null;
  const opening = house.openings.find((o) => o.id === component.openingId);
  const wall = house.walls.find((w) => w.id === component.wallId);
  if (opening === undefined || wall === undefined || opening.wallId !== wall.id) return null;
  const storey = storeyOf(house, wall.storeyId);
  if (storey === null) return null;
  const inside = externalCentroid(house, wall.storeyId);
  if (inside === null) return null;
  const frame = wallFrame(wall, inside);
  if (frame === null) return null;
  return { wall, frame, opening, storey };
}

function windowTrimBoxes(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  const anchor = resolveOpeningAnchor(house, component);
  if (anchor === null) return [];
  const { wall, frame, opening, storey } = anchor;
  const hex = strParam(component.params, 'colorHex', FALLBACK_HEX);
  const style = strParam(component.params, 'style', 'flush-band');
  const sillElev = baseElev(storey) + opening.sillMm;
  const openW = opening.widthMm;
  const openH = opening.heightMm;
  const along = opening.offsetMm;

  if (style === 'recessed') {
    // Reveal lining: four boards set into the opening from the outer face.
    const depth = Math.abs(intParam(component.params, 'projectionMm', -75));
    if (depth === 0) return [];
    const outCentre = frame.halfThicknessMm - depth / 2;
    return [
      // head
      wallBox(wall, frame, along, outCentre, openW, depth, sillElev + openH - REVEAL_BOARD_MM, REVEAL_BOARD_MM, hex),
      // sill board
      wallBox(wall, frame, along, outCentre, openW, depth, sillElev, REVEAL_BOARD_MM, hex),
      // jambs
      wallBox(wall, frame, along - openW / 2 + REVEAL_BOARD_MM / 2, outCentre, REVEAL_BOARD_MM, depth, sillElev, openH, hex),
      wallBox(wall, frame, along + openW / 2 - REVEAL_BOARD_MM / 2, outCentre, REVEAL_BOARD_MM, depth, sillElev, openH, hex),
    ];
  }

  // Flush band: a proud frame around the opening on the outer face.
  const bandW = intParam(component.params, 'widthMm', 100);
  const proud = intParam(component.params, 'projectionMm', 40);
  if (bandW <= 0 || proud <= 0) return [];
  const outCentre = frame.halfThicknessMm + proud / 2;
  return [
    // head band spans the jambs
    wallBox(wall, frame, along, outCentre, openW + 2 * bandW, proud, sillElev + openH, bandW, hex),
    // sill band
    wallBox(wall, frame, along, outCentre, openW + 2 * bandW, proud, sillElev - bandW, bandW, hex),
    // jamb bands
    wallBox(wall, frame, along - openW / 2 - bandW / 2, outCentre, bandW, proud, sillElev, openH, hex),
    wallBox(wall, frame, along + openW / 2 + bandW / 2, outCentre, bandW, proud, sillElev, openH, hex),
  ];
}

function chajjaBoxes(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  const anchor = resolveOpeningAnchor(house, component);
  if (anchor === null) return [];
  const { wall, frame, opening, storey } = anchor;
  const hex = strParam(component.params, 'colorHex', FALLBACK_HEX);
  const projection = intParam(component.params, 'projectionMm', 600);
  const thickness = intParam(component.params, 'thicknessMm', 100);
  const overhang = intParam(component.params, 'sideOverhangMm', 0);
  if (projection <= 0 || thickness <= 0) return [];
  const lintelTop = baseElev(storey) + opening.sillMm + opening.heightMm;
  return [
    wallBox(
      wall,
      frame,
      opening.offsetMm,
      frame.halfThicknessMm + projection / 2,
      opening.widthMm + 2 * overhang,
      projection,
      lintelTop,
      thickness,
      hex,
    ),
  ];
}

function porchBoxes(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  const anchor = resolveOpeningAnchor(house, component);
  if (anchor === null) return [];
  const { wall, frame, opening, storey } = anchor;
  const hex = strParam(component.params, 'colorHex', FALLBACK_HEX);
  const projection = intParam(component.params, 'projectionMm', 1500);
  const thickness = intParam(component.params, 'thicknessMm', 150);
  const width = intParam(component.params, 'widthMm', opening.widthMm + 900);
  if (projection <= 0 || thickness <= 0 || width <= 0) return [];
  const lintelTop = baseElev(storey) + opening.sillMm + opening.heightMm;
  return [
    wallBox(
      wall,
      frame,
      opening.offsetMm,
      frame.halfThicknessMm + projection / 2,
      width,
      projection,
      lintelTop,
      thickness,
      hex,
    ),
  ];
}

function claddingBoxes(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  if (component.wallId === null) return [];
  const wall = house.walls.find((w) => w.id === component.wallId);
  if (wall === undefined) return [];
  const inside = externalCentroid(house, wall.storeyId);
  if (inside === null) return [];
  const frame = wallFrame(wall, inside);
  if (frame === null) return [];
  const hex = strParam(component.params, 'colorHex', FALLBACK_HEX);
  let width = intParam(component.params, 'widthMm', 1200);
  const offset = intParam(component.params, 'offsetMm', Math.round(frame.lenMm / 2));
  if (width <= 0) return [];
  // Truncate to the wall rather than sticking out past a corner.
  width = Math.min(width, frame.lenMm);
  const half = width / 2;
  const along = Math.min(Math.max(offset, half), frame.lenMm - half);
  // Full height: grade to the top of the model's parapet ("stack full-height").
  const topElev = buildingTopElev(house) + house.levels.parapetMm;
  return [
    wallBox(
      wall,
      frame,
      along,
      frame.halfThicknessMm + CLADDING_PROUD_MM / 2,
      width,
      CLADDING_PROUD_MM,
      0,
      topElev,
      hex,
    ),
  ];
}

function parapetProfileBoxes(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  const storeyId = component.storeyId;
  if (storeyId === null) return [];
  const inside = externalCentroid(house, storeyId);
  if (inside === null) return [];
  const hex = strParam(component.params, 'colorHex', FALLBACK_HEX);
  const bandHex = strParam(component.params, 'bandColorHex', hex);
  const style = strParam(component.params, 'style', 'plain');
  const height = intParam(component.params, 'heightMm', house.levels.parapetMm);
  const cap = intParam(component.params, 'capThicknessMm', 75);
  if (height <= 0 || cap <= 0) return [];
  const topElev = buildingTopElev(house);
  const out: OrientedBoxMm[] = [];
  for (const wall of house.walls) {
    if (wall.storeyId !== storeyId || wall.kind !== 'external') continue;
    const frame = wallFrame(wall, inside);
    if (frame === null) continue;
    // Cap: proud both sides of the parapet line.
    out.push(
      wallBox(
        wall,
        frame,
        frame.lenMm / 2,
        0,
        frame.lenMm,
        wall.thicknessMm + 2 * PARAPET_CAP_OVERHANG_MM,
        topElev + height - cap,
        cap,
        hex,
      ),
    );
    if (style === 'banded') {
      out.push(
        wallBox(
          wall,
          frame,
          frame.lenMm / 2,
          0,
          frame.lenMm,
          wall.thicknessMm + 2 * PARAPET_BAND_PROUD_MM,
          topElev + height - cap - PARAPET_BAND_HEIGHT_MM,
          PARAPET_BAND_HEIGHT_MM,
          bandHex,
        ),
      );
    }
  }
  return out;
}

/** Open (not wall-attached) edges of a balcony polygon. */
export function balconyOpenEdges(house: HouseModel, balcony: Balcony): Seg[] {
  const walls = house.walls.filter((w) => w.storeyId === balcony.storeyId);
  const open: Seg[] = [];
  for (const edge of polygonEdges(balcony.polygon)) {
    const mid: Pt = { x: (edge.a.x + edge.b.x) / 2, y: (edge.a.y + edge.b.y) / 2 };
    const attached = walls.some((w) => {
      const dx = w.b.x - w.a.x;
      const dy = w.b.y - w.a.y;
      const lenSq = dx * dx + dy * dy;
      if (lenSq === 0) return distMm(mid, w.a) <= w.thicknessMm / 2 + EDGE_ATTACHED_TOLERANCE_MM;
      let t = ((mid.x - w.a.x) * dx + (mid.y - w.a.y) * dy) / lenSq;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const nearest: Pt = { x: w.a.x + t * dx, y: w.a.y + t * dy };
      return distMm(mid, nearest) <= w.thicknessMm / 2 + EDGE_ATTACHED_TOLERANCE_MM;
    });
    if (!attached) open.push(edge);
  }
  return open;
}

function railingBoxes(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  const balconyId = strParam(component.params, 'balconyId', '');
  const balcony = house.balconies.find((b) => b.id === balconyId);
  if (balcony === undefined) return [];
  const storey = storeyOf(house, balcony.storeyId);
  if (storey === null) return [];
  const hex = strParam(component.params, 'colorHex', FALLBACK_HEX);
  const style: RailingStyle = enumParam(component.params, 'style', RAILING_STYLES, 'ms-slim');
  const height = intParam(component.params, 'heightMm', balcony.railingHeightMm);
  if (height <= 0) return [];
  const floor = baseElev(storey);

  const out: OrientedBoxMm[] = [];
  for (const edge of balconyOpenEdges(house, balcony)) {
    const len = segmentLengthMm(edge);
    if (len === 0) continue;
    const dirX = (edge.b.x - edge.a.x) / len;
    const dirY = (edge.b.y - edge.a.y) / len;
    const cx = (edge.a.x + edge.b.x) / 2;
    const cy = (edge.a.y + edge.b.y) / 2;
    // dir is a length axis and every member is symmetric about the edge line,
    // so no outward flip is needed here.

    if (style === 'glass') {
      out.push({
        cx, cy, dirX, dirY,
        lenMm: len,
        depthMm: GLASS_PANEL_MM,
        baseElevMm: floor + GLASS_FLOOR_GAP_MM,
        heightMm: Math.max(height - GLASS_FLOOR_GAP_MM - GLASS_TOP_RAIL_MM, 0),
        colorHex: '#B8D4DA', // glass tint — procedural, deliberately not the trim hex
      });
      out.push({
        cx, cy, dirX, dirY,
        lenMm: len,
        depthMm: GLASS_TOP_RAIL_MM,
        baseElevMm: floor + height - GLASS_TOP_RAIL_MM,
        heightMm: GLASS_TOP_RAIL_MM,
        colorHex: hex,
      });
    } else if (style === 'masonry') {
      out.push({
        cx, cy, dirX, dirY,
        lenMm: len,
        depthMm: MASONRY_RAIL_MM,
        baseElevMm: floor,
        heightMm: height,
        colorHex: hex,
      });
    } else {
      // ms-slim: top rail + posts.
      out.push({
        cx, cy, dirX, dirY,
        lenMm: len,
        depthMm: MS_TOP_RAIL_MM,
        baseElevMm: floor + height - MS_TOP_RAIL_MM,
        heightMm: MS_TOP_RAIL_MM,
        colorHex: hex,
      });
      const bays = Math.max(1, Math.ceil(len / MS_POST_SPACING_MM));
      for (let i = 0; i <= bays; i += 1) {
        const along = (len * i) / bays;
        out.push({
          cx: edge.a.x + dirX * along,
          cy: edge.a.y + dirY * along,
          dirX, dirY,
          lenMm: MS_POST_MM,
          depthMm: MS_POST_MM,
          baseElevMm: floor,
          heightMm: height - MS_TOP_RAIL_MM,
          colorHex: hex,
        });
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * All boxes for one component. An unresolvable anchor (deleted wall/opening/
 * balcony) yields `[]` — orphaned components render as nothing.
 */
export function boxesForComponent(house: HouseModel, component: FacadeComponent): OrientedBoxMm[] {
  switch (component.kind) {
    case 'window_trim':
      return windowTrimBoxes(house, component);
    case 'chajja':
      return chajjaBoxes(house, component);
    case 'porch':
      return porchBoxes(house, component);
    case 'cladding_zone':
      return claddingBoxes(house, component);
    case 'parapet_profile':
      return parapetProfileBoxes(house, component);
    case 'railing':
      return railingBoxes(house, component);
    // Kinds the model allows but no launch kit emits: nothing to draw yet.
    case 'band':
    case 'louver':
    case 'entry_feature':
      return [];
  }
}
