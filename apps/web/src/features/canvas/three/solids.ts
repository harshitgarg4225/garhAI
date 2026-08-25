/**
 * solids.ts — the plan IS the model: HouseModel → typed solid specs, per
 * rebuild group. Pure, mm-space, no three.js — `geometryBuild.ts` turns these
 * into buffers, `ThreeDScene.tsx` puts them on screen.
 *
 * REBUILD GROUPS (§14 <100ms incremental budget): one group per storey plus
 * one `roof` group (terrace slab, parapet, mumty, OHT). A group is the unit of
 * caching and of rebuild — `dirty.ts` computes a signature per group, and only
 * groups whose signature changed are re-synthesised. Every element lives in
 * EXACTLY ONE group, so a stale-cache bug cannot leave a second copy behind.
 *
 * PICKING (inherited fact 1): every solid carries its pick contract here, in
 * data, so the scene cannot forget it:
 *
 *   walls          → kind 'wall',    the wall id
 *   opening panels → kind 'opening', the opening id
 *   stairs         → kind 'stair',   the stair id
 *   balcony + rail → kind 'balcony', the balcony id
 *   columns        → kind 'column',  the column id
 *   floor slabs    → `pickRoomByPoint`: the hit point resolves to the ROOM
 *                    under it (same id a 2D room-wash click produces — §12
 *                    "selection state common to both"). `PickKind` has no
 *                    'slab' member and `core/constants.ts` is not this
 *                    module's file to grow, and selecting "the room you
 *                    clicked the floor of" is the 2D behaviour anyway.
 *   mumty          → kind 'stair', the stair it covers (it has no id of its own)
 *   OHT            → kind 'room',  the shaft room it serves
 *   roof/parapet/plinth → pick: null. These derive from levels + envelope and
 *                    have NO model element to select. They are still
 *                    REGISTERED with the PickRegistry (with a null-resolving
 *                    target), so the decision is visible in the registry
 *                    rather than being a mesh that silently never registered
 *                    — the exact Phase-4 FurnitureLayer bug class.
 *
 * FACADE ISOLATION (§8): nothing in this file reads `house.facade`. Facade
 * kit components are a separate sub-model rendered by a separate module;
 * facade ops therefore cannot dirty any group this file produces (pinned in
 * `dirty.test.ts`).
 */

import {
  bbox,
  ensureCcw,
  stairFootprintPolygon,
  type HouseModel,
  type Opening,
  type Room,
  type SurfaceGroup,
  type Wall,
} from '@garh/model';

import type { PickTarget } from '../core';
import {
  MUMTY_HEIGHT_MM,
  OHT_HEIGHT_MM,
  balconyRailingFootprintsF,
  ensureCcwF,
  floorSlabOf,
  openingCutProfileF,
  openingPanelProfileF,
  parapetSegmentFootprintsF,
  regularPolygonF,
  stairSolidProfilesF,
  storeySpanMm,
  terraceLevelMm,
  wallFootprintF,
  type PrismProfileF,
} from './extrusion';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One solid to build: a prism, optional subtraction cuts, and its contracts. */
export interface SolidSpec {
  /** Stable debugging key, unique inside its group. */
  readonly key: string;
  readonly profile: PrismProfileF;
  /** Prisms to boolean-subtract (opening cuts, slab stair wells). Empty = none. */
  readonly cuts: readonly PrismProfileF[];
  /** Fixed pick target, or null for registered-but-unselectable structure. */
  readonly pick: PickTarget | null;
  /** True ⇒ resolve the pick by point-in-room lookup instead of `pick`. */
  readonly pickRoomByPoint: boolean;
  /** Surface group for the MaterialAssignment lookup (op 29). */
  readonly surface: SurfaceGroup;
  /** Element id for element-scoped material assignment, or null. */
  readonly elementId: string | null;
  /** Storey the solid belongs to, or null for roof-group solids. */
  readonly storeyId: string | null;
  /** Render as translucent glass (window glazing, glass railing). */
  readonly glass: boolean;
  /** Fixed colour override used when no material assignment matches (OHT). */
  readonly overrideColor: string | null;
}

/** Everything one rebuild group contains. */
export interface GroupSolids {
  readonly key: string;
  readonly storeyId: string | null;
  readonly solids: readonly SolidSpec[];
}

const NO_CUTS: readonly PrismProfileF[] = [];

interface SolidInit {
  readonly key: string;
  readonly profile: PrismProfileF;
  readonly cuts?: readonly PrismProfileF[];
  readonly pick?: PickTarget | null;
  readonly pickRoomByPoint?: boolean;
  readonly surface: SurfaceGroup;
  readonly elementId?: string | null;
  readonly storeyId?: string | null;
  readonly glass?: boolean;
  readonly overrideColor?: string | null;
}

function solid(init: SolidInit): SolidSpec {
  return {
    key: init.key,
    profile: init.profile,
    cuts: init.cuts ?? NO_CUTS,
    pick: init.pick ?? null,
    pickRoomByPoint: init.pickRoomByPoint ?? false,
    surface: init.surface,
    elementId: init.elementId ?? null,
    storeyId: init.storeyId ?? null,
    glass: init.glass ?? false,
    overrideColor: init.overrideColor ?? null,
  };
}

// ---------------------------------------------------------------------------
// Per-storey synthesis
// ---------------------------------------------------------------------------

function wallSurface(wall: Wall): SurfaceGroup {
  if (wall.kind === 'external') return 'external_wall';
  if (wall.kind === 'parapet') return 'parapet';
  return 'internal_wall';
}

function openingSurface(opening: Opening): SurfaceGroup {
  return opening.kind === 'door' ? 'door' : 'window';
}

/** All solids of one storey. Empty array for an unknown storey id. */
export function storeySolids(house: HouseModel, storeyId: string): SolidSpec[] {
  const span = storeySpanMm(house, storeyId);
  if (span === null) return [];

  const out: SolidSpec[] = [];
  const storeyWalls = house.walls.filter((w) => w.storeyId === storeyId);
  const wallById = new Map(storeyWalls.map((w) => [w.id, w]));
  const index = house.storeys.findIndex((s) => s.id === storeyId);

  // ── walls, with their opening cuts ─────────────────────────────────────
  const cutsByWall = new Map<string, PrismProfileF[]>();
  for (const opening of house.openings) {
    const wall = wallById.get(opening.wallId);
    if (wall === undefined) continue;
    const cut = openingCutProfileF(wall, opening, span.baseMm, span.wallTopMm);
    if (cut === null) continue;
    const list = cutsByWall.get(wall.id);
    if (list) list.push(cut);
    else cutsByWall.set(wall.id, [cut]);
  }

  for (const wall of storeyWalls) {
    const footprint = wallFootprintF(wall);
    if (footprint === null) continue;
    out.push(
      solid({
        key: `wall:${wall.id}`,
        profile: { polygon: footprint, baseMm: span.baseMm, topMm: span.wallTopMm },
        cuts: cutsByWall.get(wall.id) ?? NO_CUTS,
        pick: { kind: 'wall', id: wall.id, storeyId },
        surface: wallSurface(wall),
        elementId: wall.id,
        storeyId,
      }),
    );
  }

  // ── opening panels: the pickable glazing / leaf inside each cut ────────
  for (const opening of house.openings) {
    const wall = wallById.get(opening.wallId);
    if (wall === undefined) continue;
    const panel = openingPanelProfileF(wall, opening, span.baseMm, span.wallTopMm);
    if (panel === null) continue;
    out.push(
      solid({
        key: `opening:${opening.id}`,
        profile: panel,
        pick: { kind: 'opening', id: opening.id, storeyId },
        surface: openingSurface(opening),
        elementId: opening.id,
        storeyId,
        glass: opening.kind !== 'door',
      }),
    );
  }

  // ── floor slab (model-derived envelope, with its stair-well cutouts) ───
  const slab = floorSlabOf(house, storeyId);
  if (slab !== null && slab.polygon.length >= 3) {
    const slabTop = span.baseMm;
    const slabBase = slabTop - slab.thicknessMm;
    out.push(
      solid({
        key: `slab:${slab.id}`,
        profile: { polygon: ensureCcw(slab.polygon), baseMm: slabBase, topMm: slabTop },
        cuts: slab.cutouts
          .filter((c) => c.length >= 3)
          .map((c) => ({
            polygon: ensureCcw(c),
            baseMm: slabBase - 10,
            topMm: slabTop + 10,
          })),
        pickRoomByPoint: true,
        surface: 'floor',
        elementId: slab.id,
        storeyId,
      }),
    );

    // ── plinth: ground envelope from datum up to the ground slab's underside
    if (index === 0 && slabBase > 0) {
      out.push(
        solid({
          key: 'plinth',
          profile: { polygon: ensureCcw(slab.polygon), baseMm: 0, topMm: slabBase },
          surface: 'plinth',
          storeyId,
        }),
      );
    }
  }

  // ── stairs: stepped solids + landing, straight-run honesty ─────────────
  for (const stair of house.stairs) {
    if (stair.storeyId !== storeyId) continue;
    const profiles = stairSolidProfilesF(stair, span.baseMm);
    profiles.forEach((profile, i) => {
      out.push(
        solid({
          key: `stair:${stair.id}:${String(i)}`,
          profile,
          pick: { kind: 'stair', id: stair.id, storeyId },
          surface: 'staircase',
          elementId: stair.id,
          storeyId,
        }),
      );
    });
  }

  // ── balconies: slab hung under the FFL + railing on the outer edges ────
  for (const balcony of house.balconies) {
    if (balcony.storeyId !== storeyId) continue;
    if (balcony.polygon.length < 3) continue;
    const ring = ensureCcw(balcony.polygon);
    out.push(
      solid({
        key: `balcony:${balcony.id}`,
        profile: {
          polygon: ring,
          baseMm: span.baseMm - balcony.slabThicknessMm,
          topMm: span.baseMm,
        },
        pick: { kind: 'balcony', id: balcony.id, storeyId },
        surface: 'floor',
        elementId: balcony.id,
        storeyId,
      }),
    );
    if (balcony.railingKind !== 'none' && balcony.railingHeightMm > 0) {
      const bands = balconyRailingFootprintsF(ring, storeyWalls);
      bands.forEach((band, i) => {
        out.push(
          solid({
            key: `railing:${balcony.id}:${String(i)}`,
            profile: {
              polygon: band,
              baseMm: span.baseMm,
              topMm: span.baseMm + balcony.railingHeightMm,
            },
            pick: { kind: 'balcony', id: balcony.id, storeyId },
            surface: 'railing',
            elementId: balcony.id,
            storeyId,
            glass: balcony.railingKind === 'glass' || balcony.railingKind === 'ms_glass',
          }),
        );
      });
    }
  }

  // ── columns ─────────────────────────────────────────────────────────────
  for (const column of house.columns) {
    if (column.storeyId !== storeyId) continue;
    const hx = column.sizeMm.xMm / 2;
    const hy = column.sizeMm.yMm / 2;
    out.push(
      solid({
        key: `column:${column.id}`,
        profile: {
          polygon: ensureCcwF([
            { x: column.pt.x - hx, y: column.pt.y - hy },
            { x: column.pt.x + hx, y: column.pt.y - hy },
            { x: column.pt.x + hx, y: column.pt.y + hy },
            { x: column.pt.x - hx, y: column.pt.y + hy },
          ]),
          baseMm: span.baseMm,
          topMm: span.wallTopMm,
        },
        pick: { kind: 'column', id: column.id, storeyId },
        // SURFACE_GROUPS has no column entry; internal_wall is the closest
        // honest bucket and keeps columns targetable through op 29.
        surface: 'internal_wall',
        elementId: column.id,
        storeyId,
      }),
    );
  }

  return out;
}

// ---------------------------------------------------------------------------
// Roof group: terrace slab, parapet, mumty, OHT
// ---------------------------------------------------------------------------

/** Dark HDPE — the one solid whose colour is not a material assignment. */
const OHT_COLOR = '#1F2937';

/** Roof-group solids. Empty when there are no storeys or no top envelope. */
export function roofSolids(house: HouseModel): SolidSpec[] {
  const top = house.storeys[house.storeys.length - 1];
  if (top === undefined) return [];
  const slab = floorSlabOf(house, top.id);
  if (slab === null || slab.polygon.length < 3) return [];

  const terraceMm = terraceLevelMm(house);
  const thicknessMm = top.level.slabThicknessMm;
  const out: SolidSpec[] = [];

  // Terrace slab: the top storey's envelope, its slab thickness re-used —
  // the model derives no roof slab of its own (§3 derives floor slabs only).
  const topStairs = house.stairs.filter((s) => s.storeyId === top.id);
  out.push(
    solid({
      key: 'roof-slab',
      profile: {
        polygon: ensureCcw(slab.polygon),
        baseMm: terraceMm - thicknessMm,
        topMm: terraceMm,
      },
      cuts: topStairs.map((s) => ({
        polygon: ensureCcw(stairFootprintPolygon(s)),
        baseMm: terraceMm - thicknessMm - 10,
        topMm: terraceMm + 10,
      })),
      surface: 'roof',
    }),
  );

  // Parapet ring on the terrace perimeter.
  for (const [i, band] of parapetSegmentFootprintsF(slab.polygon).entries()) {
    out.push(
      solid({
        key: `parapet:${String(i)}`,
        profile: {
          polygon: band,
          baseMm: terraceMm,
          topMm: terraceMm + house.levels.parapetMm,
        },
        surface: 'parapet',
      }),
    );
  }

  // Mumty: a massing box over each terrace-arriving stair. It covers the
  // stair well cut out of the roof slab; picking it selects the stair it
  // serves, which is the nearest real element.
  for (const stair of topStairs) {
    const footprint = stairFootprintPolygon(stair);
    if (footprint.length < 3) continue;
    out.push(
      solid({
        key: `mumty:${stair.id}`,
        profile: {
          polygon: ensureCcw(footprint),
          baseMm: terraceMm,
          topMm: terraceMm + MUMTY_HEIGHT_MM,
        },
        pick: { kind: 'stair', id: stair.id, storeyId: stair.storeyId },
        surface: 'external_wall',
        elementId: stair.id,
      }),
    );
  }

  // OHT cylinder over the shaft, if the top storey has one.
  for (const room of house.rooms) {
    if (room.storeyId !== top.id || room.type !== 'shaft') continue;
    if (room.polygon.length < 3) continue;
    const box = bbox(room.polygon);
    const w = box.maxX - box.minX;
    const d = box.maxY - box.minY;
    if (w <= 0 || d <= 0) continue;
    const radius = Math.min(1200, Math.min(w, d) / 2);
    if (radius < 150) continue;
    out.push(
      solid({
        key: `oht:${room.id}`,
        profile: {
          polygon: regularPolygonF(
            { x: (box.minX + box.maxX) / 2, y: (box.minY + box.maxY) / 2 },
            radius,
          ),
          baseMm: terraceMm,
          topMm: terraceMm + OHT_HEIGHT_MM,
        },
        pick: { kind: 'room', id: room.id, storeyId: room.storeyId },
        surface: 'roof',
        elementId: room.id,
        overrideColor: OHT_COLOR,
      }),
    );
  }

  return out;
}

// ---------------------------------------------------------------------------
// Group enumeration
// ---------------------------------------------------------------------------

export const ROOF_GROUP_KEY = 'roof';

export function storeyGroupKey(storeyId: string): string {
  return `storey:${storeyId}`;
}

/** Every rebuild group of a document, in draw order (ground → top → roof). */
export function groupKeysOf(house: HouseModel): string[] {
  const keys = house.storeys.map((s) => storeyGroupKey(s.id));
  if (house.storeys.length > 0) keys.push(ROOF_GROUP_KEY);
  return keys;
}

/** Synthesize one group by key. */
export function solidsOfGroup(house: HouseModel, groupKey: string): GroupSolids {
  if (groupKey === ROOF_GROUP_KEY) {
    return { key: groupKey, storeyId: null, solids: roofSolids(house) };
  }
  const storeyId = groupKey.startsWith('storey:') ? groupKey.slice('storey:'.length) : groupKey;
  return { key: groupKey, storeyId, solids: storeySolids(house, storeyId) };
}

/** Rooms of one storey — the slab pick resolver's lookup table. */
export function roomsOfStoreyId(house: HouseModel, storeyId: string): Room[] {
  return house.rooms.filter((r) => r.storeyId === storeyId);
}
