/**
 * thumbnail.ts — kit-card previews, generated FROM THE GENERATOR.
 *
 * §15 and the phase brief are explicit: kit previews are "generated thumbnails
 * from the same generator — never static images that can drift from the
 * geometry". So a thumbnail is: run `generateFacadeComponents` on a model,
 * turn the resulting components into the SAME oriented boxes the 3D layer
 * extrudes (`componentBoxes.ts`), orthographically project the boxes that face
 * the chosen frontage onto its plane, and hand back flat rectangles for an
 * inline SVG. No canvas capture, no PNG, no asset — `check_web_assets.py` has
 * nothing to find (inherited fact 4).
 *
 * WHICH MODEL: the CURRENT plan when it has an external frontage — the card
 * then previews the user's own house — else the deterministic sample house
 * below, so the cards teach even on an empty project. Both paths are pure and
 * seeded: same (model, kit, seed, colorway) → identical SVG, which the spec
 * pins.
 */

import {
  derivedId,
  roundMm,
  segmentLengthMm,
  SCHEMA_VERSION,
  type FacadeComponent,
  type FacadeComponentSpec,
  type HouseModel,
  type Opening,
  type StoreyId,
  type Wall,
  type WallId,
} from '@garh/model';

import { boxesForComponent, externalCentroid, wallFrame } from './componentBoxes';
import { findEntryDoor, generateFacadeComponents, resolveColorway } from './generator';
import type { FacadeKitDef } from './types';

// ---------------------------------------------------------------------------
// Elevation projection
// ---------------------------------------------------------------------------

/** One rectangle of the elevation. Coordinates in mm; `y` grows UPWARD. */
export interface ElevationRect {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
  readonly fill: string;
}

export interface ThumbnailSpec {
  /** Frontage width in mm — the SVG viewBox width. */
  readonly widthMm: number;
  /** Total elevation height in mm. */
  readonly heightMm: number;
  /** Back-to-front rectangles: wall face, openings, then facade boxes. */
  readonly rects: readonly ElevationRect[];
}

/** Boxes further than this in front of the wall face are off this elevation. */
const ELEVATION_DEPTH_WINDOW_MM = 3000;

/** Glass and door leaf tints for the backdrop openings. Procedural, fixed. */
const GLASS_HEX = '#9FB6C8';
const DOOR_HEX = '#57493D';

/** The frontage: the wall with the entry door, else the longest external. */
export function pickFrontage(house: HouseModel): Wall | null {
  const ground = house.storeys[0];
  if (ground === undefined) return null;
  const entry = findEntryDoor(house, ground);
  if (entry !== null) return entry.wall;
  let best: Wall | null = null;
  let bestLen = -1;
  for (const w of house.walls) {
    if (w.storeyId !== ground.id || w.kind !== 'external') continue;
    const len = segmentLengthMm({ a: w.a, b: w.b });
    if (len > bestLen || (len === bestLen && best !== null && w.id < best.id)) {
      best = w;
      bestLen = len;
    }
  }
  return best;
}

/**
 * Project `components` onto the elevation of `frontage`.
 * Pure; deterministic ordering (back to front, then along the wall).
 */
export function elevationSpec(
  house: HouseModel,
  components: readonly FacadeComponent[],
  frontage: Wall,
  wallFillHex: string,
): ThumbnailSpec | null {
  const inside = externalCentroid(house, frontage.storeyId);
  if (inside === null) return null;
  const frame = wallFrame(frontage, inside);
  if (frame === null) return null;

  const top = house.storeys[house.storeys.length - 1];
  const topElev = top === undefined ? 3000 : top.level.fflMm + top.heightMm;
  const heightMm = topElev + house.levels.parapetMm;

  const rects: ElevationRect[] = [];
  // Backdrop: the wall face itself.
  rects.push({ x: 0, y: 0, w: frame.lenMm, h: heightMm, fill: wallFillHex });

  // Openings on any storey of this bay: walls whose centreline lies on the
  // frontage line share its elevation (a multi-storey plan repeats the wall
  // per storey at the same plan position).
  const bayWallIds = new Set<string>();
  for (const w of house.walls) {
    if (w.kind !== 'external') continue;
    const near = (p: { x: number; y: number }): boolean => {
      const relX = p.x - frontage.a.x;
      const relY = p.y - frontage.a.y;
      const out = relX * frame.outX + relY * frame.outY;
      return Math.abs(out) <= frontage.thicknessMm;
    };
    if (near(w.a) && near(w.b)) bayWallIds.add(w.id);
  }
  for (const o of house.openings) {
    if (!bayWallIds.has(o.wallId)) continue;
    const host = house.walls.find((w) => w.id === o.wallId);
    if (host === undefined) continue;
    const storey = house.storeys.find((s) => s.id === host.storeyId);
    if (storey === undefined) continue;
    const hostLen = segmentLengthMm({ a: host.a, b: host.b });
    if (hostLen === 0) continue;
    // Along-the-frontage position of the opening centre.
    const centreX = host.a.x + ((host.b.x - host.a.x) / hostLen) * o.offsetMm;
    const centreY = host.a.y + ((host.b.y - host.a.y) / hostLen) * o.offsetMm;
    const along = (centreX - frontage.a.x) * frame.dirX + (centreY - frontage.a.y) * frame.dirY;
    rects.push({
      // roundMm: `along` is a float projection and widthMm/2 can be a half —
      // the spec goes through canonicalJson, which rejects non-integers.
      x: roundMm(along - o.widthMm / 2),
      y: storey.level.fflMm + o.sillMm,
      w: o.widthMm,
      h: o.heightMm,
      fill: o.kind === 'door' ? DOOR_HEX : GLASS_HEX,
    });
  }

  // Facade boxes that face this elevation, painted nearest-last.
  const projected: (ElevationRect & { readonly out: number })[] = [];
  for (const component of components) {
    for (const box of boxesForComponent(house, component)) {
      const parallel = Math.abs(box.dirX * frame.dirX + box.dirY * frame.dirY);
      if (parallel < 0.9) continue;
      const relX = box.cx - frontage.a.x;
      const relY = box.cy - frontage.a.y;
      const out = relX * frame.outX + relY * frame.outY;
      if (out < -frame.halfThicknessMm - 100 || out > ELEVATION_DEPTH_WINDOW_MM) continue;
      const along = relX * frame.dirX + relY * frame.dirY;
      projected.push({
        // roundMm on every field that passes through a float projection or a
        // halving — the spec is canonicalJson'd, which rejects non-integers.
        x: roundMm(along - box.lenMm / 2),
        y: roundMm(box.baseElevMm),
        w: roundMm(box.lenMm),
        h: roundMm(box.heightMm),
        fill: box.colorHex,
        out,
      });
    }
  }
  projected.sort((a, b) => a.out - b.out || a.x - b.x || a.y - b.y);
  for (const r of projected) rects.push({ x: r.x, y: r.y, w: r.w, h: r.h, fill: r.fill });

  return { widthMm: frame.lenMm, heightMm, rects };
}

// ---------------------------------------------------------------------------
// The whole pipeline, as one call the card component uses
// ---------------------------------------------------------------------------

/** True when the model has something a facade could dress. */
export function hasFrontage(house: HouseModel): boolean {
  return pickFrontage(house) !== null;
}

/**
 * Thumbnail for `kit` at `seed`: the user's own model when it has a frontage,
 * else the sample house. `null` only if even the sample fails to project —
 * which the spec proves it does not.
 */
export function kitThumbnailSpec(
  house: HouseModel | null,
  kit: FacadeKitDef,
  seed: number,
  colorwayId: string | null,
): ThumbnailSpec | null {
  const model = house !== null && hasFrontage(house) ? house : sampleHouseForThumbnails();
  const frontage = pickFrontage(model);
  if (frontage === null) return null;
  const specs: readonly FacadeComponentSpec[] = generateFacadeComponents(model, kit, seed, {
    colorwayId,
  });
  // Specs are structurally valid FacadeComponents once defaults are filled —
  // the same normalisation op 27's fold performs.
  const components: FacadeComponent[] = specs.map((s) => ({
    id: s.id,
    kind: s.kind,
    storeyId: s.storeyId ?? null,
    wallId: s.wallId ?? null,
    openingId: s.openingId ?? null,
    params: s.params,
  }));
  // The SAME resolution the generator used — one colour source, not two.
  const colorway = resolveColorway(kit, seed, colorwayId);
  return elevationSpec(model, components, frontage, colorway.base);
}

// ---------------------------------------------------------------------------
// The sample house — G+1, one door, four windows, a stair and a balcony
// ---------------------------------------------------------------------------

let sampleCache: HouseModel | null = null;

function sid(key: string): StoreyId {
  return derivedId('storey', `facade-sample|${key}`);
}
function wid(key: string): WallId {
  return derivedId('wall', `facade-sample|${key}`);
}

/**
 * A deterministic 7.2 m × 5.4 m G+1 house, built as data (never dispatched as
 * ops — it exists only to be READ by the generator). Ids are `derivedId`s so
 * two thumbnails never disagree about the model they drew.
 */
export function sampleHouseForThumbnails(): HouseModel {
  if (sampleCache !== null) return sampleCache;

  const g = sid('ground');
  const f = sid('first');
  const south = wid('south');
  const east = wid('east');
  const north = wid('north');
  const west = wid('west');
  const southF = wid('south-first');
  const eastF = wid('east-first');
  const northF = wid('north-first');
  const westF = wid('west-first');

  const mkWall = (
    id: WallId,
    storeyId: StoreyId,
    a: { x: number; y: number },
    b: { x: number; y: number },
  ): Wall => ({ id, storeyId, a, b, thicknessMm: 230, kind: 'external', loadBearing: true });

  const mkOpening = (
    key: string,
    wallId: WallId,
    kind: Opening['kind'],
    widthMm: number,
    heightMm: number,
    sillMm: number,
    offsetMm: number,
  ): Opening => ({
    id: derivedId('opening', `facade-sample|${key}`),
    wallId,
    kind,
    widthMm,
    heightMm,
    sillMm,
    offsetMm,
    swing: 'in-left',
    tag: null,
  });

  const house: HouseModel = {
    schemaVersion: SCHEMA_VERSION,
    storeys: [
      {
        id: g,
        name: 'Ground Floor',
        level: { fflMm: 600, slabThicknessMm: 150, sillDefaultMm: null, lintelDefaultMm: null },
        heightMm: 3000,
      },
      {
        id: f,
        name: 'First Floor',
        level: { fflMm: 3600, slabThicknessMm: 150, sillDefaultMm: null, lintelDefaultMm: null },
        heightMm: 3000,
      },
    ],
    walls: [
      mkWall(south, g, { x: 0, y: 0 }, { x: 7200, y: 0 }),
      mkWall(east, g, { x: 7200, y: 0 }, { x: 7200, y: 5400 }),
      mkWall(north, g, { x: 7200, y: 5400 }, { x: 0, y: 5400 }),
      mkWall(west, g, { x: 0, y: 5400 }, { x: 0, y: 0 }),
      mkWall(southF, f, { x: 0, y: 0 }, { x: 7200, y: 0 }),
      mkWall(eastF, f, { x: 7200, y: 0 }, { x: 7200, y: 5400 }),
      mkWall(northF, f, { x: 7200, y: 5400 }, { x: 0, y: 5400 }),
      mkWall(westF, f, { x: 0, y: 5400 }, { x: 0, y: 0 }),
    ],
    openings: [
      mkOpening('door-main', south, 'door', 1000, 2100, 0, 1500),
      mkOpening('win-g-1', south, 'window', 1500, 1200, 900, 4000),
      mkOpening('win-g-2', south, 'window', 1200, 1200, 900, 6100),
      mkOpening('win-f-1', southF, 'window', 1500, 1200, 900, 1500),
      mkOpening('win-f-2', southF, 'window', 1500, 1200, 900, 4300),
      mkOpening('win-e-1', east, 'window', 1200, 1200, 900, 2700),
    ],
    rooms: [],
    stairs: [
      {
        id: derivedId('stair', 'facade-sample|stair'),
        storeyId: g,
        kind: 'straight',
        origin: { x: 6300, y: 4200 },
        direction: 'N',
        riserMm: 165,
        treadMm: 275,
        widthMm: 900,
        risersCount: 18,
        landing: null,
      },
    ],
    slabs: [],
    columns: [],
    furniture: [],
    facade: { kitId: null, seed: 0, colorwayId: null, components: [] },
    materials: [],
    levels: {
      plinthMm: 600,
      fflPerStoreyMm: [600, 3600],
      sillDefaultMm: 900,
      lintelDefaultMm: 2100,
      parapetMm: 1000,
    },
    balconies: [
      {
        id: derivedId('balcony', 'facade-sample|balcony'),
        storeyId: f,
        polygon: [
          { x: 3800, y: 0 },
          { x: 3800, y: -1200 },
          { x: 6200, y: -1200 },
          { x: 6200, y: 0 },
        ],
        railingKind: 'ms',
        railingHeightMm: 1000,
        projectionMm: 1200,
        slabThicknessMm: 150,
      },
    ],
    meta: { unitsDisplay: 'ft-in', regProfileRef: null, briefRef: null },
  };

  sampleCache = house;
  return house;
}
