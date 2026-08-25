/**
 * generator.ts — the facade kit generator (§8).
 *
 * ONE PURE FUNCTION. `generateFacadeComponents(house, kit, seed, colorwayId)`
 * walks the external walls + openings of the model it is handed and returns
 * `FacadeComponentSpec[]` — the array `facade.apply_kit` (op 27) carries.
 * No store reads, no clock, no `Math.random`, no id-factory state: component
 * ids come from `derivedIdUnique` over semantic keys, and every variant choice
 * comes from `variation.ts` over `(seed, semantic key)`. Same (model, kit,
 * seed, colorway) in ⇒ byte-identical components out. That is the determinism
 * spec pins.
 *
 * THE ISOLATION INVARIANT (§8, and the model's own doc on `FacadeModel`):
 * this function READS walls/openings/stairs/balconies/levels and WRITES only
 * facade component specs. It emits no op other than what the caller wraps in
 * op 27, and op 27's fold touches `draft.facade` alone — so a facade
 * regeneration cannot dirty the plan, move a wall, or change a compliance
 * number. `generator.test.ts` folds an apply-kit op and asserts the rest of
 * the house is deep-equal.
 *
 * WHAT GOES IN `params` — parameters, not placement. A chajja's params say
 * "600 mm projection, 100 thick, flat"; they do not say where the chajja is.
 * Placement is derived at render time from the anchored wall/opening
 * (`componentBoxes.ts`), so when a wall moves the facade follows it without a
 * regeneration, and an op-28 patch ("make this chajja 750") edits exactly one
 * number. Lengths in params are integer mm (op validation rejects floats);
 * colours are hex strings resolved from the kit colorway at generation time so
 * rendering never needs the catalogue.
 */

import {
  derivedIdUnique,
  distSqMm2,
  roundHalfAwayFromZero,
  segmentLengthMm,
  type Balcony,
  type FacadeComponentSpec,
  type HouseModel,
  type JsonObject,
  type Opening,
  type Pt,
  type Stair,
  type Storey,
  type Wall,
} from '@garh/model';

import type { FacadeKitDef, KitColorway } from './types';
import { colorwayById } from './kits';
import { pickVariant } from './variation';

// ---------------------------------------------------------------------------
// Tunables that are generator policy, not kit data
// ---------------------------------------------------------------------------

/** Side overhang of a flat chajja past the opening, per side. */
export const CHAJJA_SIDE_OVERHANG_MM = 150;

/** Porch width margin past the entry door, per side. */
export const PORCH_SIDE_MARGIN_MM = 450;

/** How the contemporary cladding band may sit on its bay (both rule-legal). */
const CLADDING_ALIGN_VARIANTS = ['centre-on-anchor', 'flush-to-near-end'] as const;

// ---------------------------------------------------------------------------
// Deterministic walks over the model
// ---------------------------------------------------------------------------

/** External walls of a storey, sorted by id — array order is not a contract. */
export function externalWallsOf(house: HouseModel, storeyId: string): Wall[] {
  return house.walls
    .filter((w) => w.storeyId === storeyId && w.kind === 'external')
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** Openings hosted on `wall`, sorted along it (offset, then id). */
function openingsAlong(house: HouseModel, wall: Wall): Opening[] {
  return house.openings
    .filter((o) => o.wallId === wall.id)
    .sort((a, b) => a.offsetMm - b.offsetMm || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** Squared distance from a point to a wall's centreline segment. */
function distSqToWall(p: Pt, wall: Wall): number {
  const ax = wall.a.x;
  const ay = wall.a.y;
  const dx = wall.b.x - ax;
  const dy = wall.b.y - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return distSqMm2(p, wall.a);
  let t = ((p.x - ax) * dx + (p.y - ay) * dy) / lenSq;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return distSqMm2(p, { x: ax + t * dx, y: ay + t * dy });
}

/** Distance along `wall` (from `a`) of the point on it nearest to `p`, integer mm. */
function alongWallNearest(p: Pt, wall: Wall): number {
  const ax = wall.a.x;
  const ay = wall.a.y;
  const dx = wall.b.x - ax;
  const dy = wall.b.y - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return 0;
  let t = ((p.x - ax) * dx + (p.y - ay) * dy) / lenSq;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return roundHalfAwayFromZero(t * Math.sqrt(lenSq));
}

/** Centre point of a stair footprint's origin — good enough as an anchor. */
function stairAnchor(stair: Stair): Pt {
  return stair.origin;
}

/**
 * The entry door: widest ground-storey door on an external wall, ties broken
 * by id. `null` when the plan has no external door yet — the porch and the
 * entry-bay fallback both handle that honestly.
 */
export function findEntryDoor(
  house: HouseModel,
  groundStorey: Storey,
): { door: Opening; wall: Wall } | null {
  let best: { door: Opening; wall: Wall } | null = null;
  for (const wall of externalWallsOf(house, groundStorey.id)) {
    for (const o of openingsAlong(house, wall)) {
      if (o.kind !== 'door') continue;
      if (
        best === null ||
        o.widthMm > best.door.widthMm ||
        (o.widthMm === best.door.widthMm && o.id < best.door.id)
      ) {
        best = { door: o, wall };
      }
    }
  }
  return best;
}

/**
 * The cladding bay wall (contemporary): the external ground-storey wall
 * nearest the stair ("stair-adjacent external wall"), falling back to the
 * entry-door wall, falling back to the longest external wall. Ties break on
 * wall id so the pick is stable across refolds.
 */
export function findCladdingWall(house: HouseModel, groundStorey: Storey): Wall | null {
  const walls = externalWallsOf(house, groundStorey.id);
  if (walls.length === 0) return null;

  const stair = house.stairs
    .filter((s) => s.storeyId === groundStorey.id)
    .sort((a, b) => (a.id < b.id ? -1 : 1))[0];
  if (stair !== undefined) {
    const anchor = stairAnchor(stair);
    let best: Wall | null = null;
    let bestD = Infinity;
    for (const w of walls) {
      const d = distSqToWall(anchor, w);
      if (d < bestD || (d === bestD && best !== null && w.id < best.id)) {
        best = w;
        bestD = d;
      }
    }
    return best;
  }

  const entry = findEntryDoor(house, groundStorey);
  if (entry !== null) return entry.wall;

  let longest: Wall | null = null;
  let longestLen = -1;
  for (const w of walls) {
    const len = segmentLengthMm({ a: w.a, b: w.b });
    if (len > longestLen || (len === longestLen && longest !== null && w.id < longest.id)) {
      longest = w;
      longestLen = len;
    }
  }
  return longest;
}

// ---------------------------------------------------------------------------
// The generator
// ---------------------------------------------------------------------------

export interface GenerateOptions {
  /** Explicit colorway; `null`/absent lets the seed pick among the kit's. */
  readonly colorwayId?: string | null;
}

/**
 * The colorway a generation run paints with: the explicit one when given,
 * else a seed-picked one (§8 seeded variation). Exported because the
 * thumbnail's backdrop must resolve the SAME colorway as the components it
 * draws — two resolutions would be two colour sources.
 */
export function resolveColorway(
  kit: FacadeKitDef,
  seed: number,
  colorwayId?: string | null,
): KitColorway {
  if (colorwayId !== undefined && colorwayId !== null) return colorwayById(kit, colorwayId);
  if (kit.colorways.length === 0) return colorwayById(kit, null); // throws with a named kit
  return colorwayById(
    kit,
    pickVariant(
      seed,
      'colorway',
      kit.colorways.map((c) => c.id),
      kit.colorways[0]?.id ?? null,
    ),
  );
}

/**
 * Instantiate `kit` over `house`. Pure; see the module header for the
 * determinism and isolation contracts.
 */
export function generateFacadeComponents(
  house: HouseModel,
  kit: FacadeKitDef,
  seed: number,
  options: GenerateOptions = {},
): FacadeComponentSpec[] {
  const out: FacadeComponentSpec[] = [];
  const taken = new Set<string>();
  const mint = (kind: FacadeComponentSpec['kind'], anchorKey: string): FacadeComponentSpec['id'] => {
    const id = derivedIdUnique(
      'facadecomp',
      `facade|${kit.id}|${String(seed)}|${kind}|${anchorKey}`,
      taken,
    );
    taken.add(id);
    return id;
  };

  const storeys = house.storeys;
  const ground = storeys[0];
  if (ground === undefined) return out; // no storeys, no facade — honest empty

  // Seed-picked, building-wide variants (§8 "seeded variation").
  const colorway: KitColorway = resolveColorway(kit, seed, options.colorwayId);
  const chajjaProjectionMm = pickVariant(
    seed,
    'chajja-projection',
    kit.components.chajja.allowedProjectionsMm,
    kit.components.chajja.projectionMm,
  );

  const entry = findEntryDoor(house, ground);

  // ── window trims + chajjas: walk external walls storey by storey ────────
  for (const storey of storeys) {
    for (const wall of externalWallsOf(house, storey.id)) {
      for (const opening of openingsAlong(house, wall)) {
        const isEntryDoor = entry !== null && opening.id === entry.door.id;

        if (opening.kind === 'window' || opening.kind === 'ventilator') {
          const trim = kit.components.windowTrim;
          const params: JsonObject = {
            style: trim.style,
            widthMm: trim.widthMm,
            projectionMm: trim.projectionMm,
            colorHex: colorway.trim,
          };
          if (kit.rules.recessDepthMm !== undefined) {
            params.recessDepthMm = kit.rules.recessDepthMm;
          }
          out.push({
            id: mint('window_trim', opening.id),
            kind: 'window_trim',
            storeyId: storey.id,
            wallId: wall.id,
            openingId: opening.id,
            params,
          });
        }

        // The entry door gets the porch, not a chajja — one canopy per door.
        if (kit.rules.chajjaOverOpenings.includes(opening.kind) && !isEntryDoor) {
          out.push({
            id: mint('chajja', opening.id),
            kind: 'chajja',
            storeyId: storey.id,
            wallId: wall.id,
            openingId: opening.id,
            params: {
              style: kit.components.chajja.style,
              projectionMm: chajjaProjectionMm,
              thicknessMm: kit.components.chajja.thicknessMm,
              sideOverhangMm: kit.components.chajja.style === 'flat' ? CHAJJA_SIDE_OVERHANG_MM : 0,
              colorHex: colorway.trim,
            },
          });
        }
      }
    }
  }

  // ── porch over the entry door ────────────────────────────────────────────
  if (entry !== null) {
    out.push({
      id: mint('porch', entry.door.id),
      kind: 'porch',
      storeyId: ground.id,
      wallId: entry.wall.id,
      openingId: entry.door.id,
      params: {
        style: kit.components.porch.style,
        projectionMm: kit.components.porch.projectionMm,
        thicknessMm: kit.components.porch.thicknessMm,
        widthMm: entry.door.widthMm + 2 * PORCH_SIDE_MARGIN_MM,
        colorHex: colorway.accent,
      },
    });
  }

  // ── cladding zone (kit rule; 'none' disables) ────────────────────────────
  const cladding = kit.components.claddingZones;
  if (cladding.rule !== 'none' && cladding.widthMm > 0) {
    const bayWall = findCladdingWall(house, ground);
    if (bayWall !== null) {
      const wallLen = segmentLengthMm({ a: bayWall.a, b: bayWall.b });
      // Anchor point along the wall: the stair if there is one, else the
      // entry door's offset, else the wall middle.
      const stair = house.stairs
        .filter((s) => s.storeyId === ground.id)
        .sort((a, b) => (a.id < b.id ? -1 : 1))[0];
      let anchorAlongMm: number;
      if (stair !== undefined) {
        anchorAlongMm = alongWallNearest(stairAnchor(stair), bayWall);
      } else if (entry !== null && entry.wall.id === bayWall.id) {
        anchorAlongMm = entry.door.offsetMm;
      } else {
        anchorAlongMm = roundHalfAwayFromZero(wallLen / 2);
      }
      // Seeded, rule-legal alignment: centred on the anchor, or flushed to the
      // wall end nearest it. Both keep the band on the picked bay.
      const align = pickVariant(seed, 'cladding-align', CLADDING_ALIGN_VARIANTS, 'centre-on-anchor');
      const half = Math.floor(cladding.widthMm / 2);
      let centreMm: number;
      if (align === 'flush-to-near-end') {
        centreMm = anchorAlongMm < wallLen / 2 ? half : wallLen - half;
      } else {
        centreMm = anchorAlongMm;
      }
      // Clamp the band inside the wall; a wall narrower than the band gets a
      // centred band the renderer will truncate to the wall.
      const lo = Math.min(half, Math.floor(wallLen / 2));
      const hi = Math.max(wallLen - half, Math.ceil(wallLen / 2));
      centreMm = roundHalfAwayFromZero(Math.min(Math.max(centreMm, lo), hi));

      const params: JsonObject = {
        rule: cladding.rule,
        materialId: cladding.materialId,
        widthMm: cladding.widthMm,
        offsetMm: centreMm,
        colorHex: colorway.accent,
      };
      out.push({
        id: mint('cladding_zone', bayWall.id),
        kind: 'cladding_zone',
        storeyId: null, // full-height: the zone spans every storey on this bay
        wallId: bayWall.id,
        openingId: null,
        params,
      });
    }
  }

  // ── parapet profile on the terrace ───────────────────────────────────────
  const top = storeys[storeys.length - 1];
  if (top !== undefined) {
    out.push({
      id: mint('parapet_profile', top.id),
      kind: 'parapet_profile',
      storeyId: top.id,
      wallId: null,
      openingId: null,
      params: {
        style: kit.components.parapetProfile.style,
        heightMm: kit.components.parapetProfile.heightMm,
        capThicknessMm: kit.components.parapetProfile.capThicknessMm,
        colorHex: colorway.trim,
        bandColorHex: colorway.base,
      },
    });
  }

  // ── railings on balconies ────────────────────────────────────────────────
  const balconies: Balcony[] = house.balconies
    .slice()
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const balcony of balconies) {
    out.push({
      id: mint('railing', balcony.id),
      kind: 'railing',
      storeyId: balcony.storeyId,
      wallId: null,
      openingId: null,
      params: {
        balconyId: balcony.id,
        style: kit.components.railing.style,
        heightMm: kit.components.railing.heightMm,
        materialId: kit.components.railing.materialId,
        colorHex: colorway.trim,
      },
    });
  }

  return out;
}

// ---------------------------------------------------------------------------
// Fit diagnostics — the panel's honest empty/warning states (§15)
// ---------------------------------------------------------------------------

export interface KitFitIssue {
  readonly severity: 'blocker' | 'note';
  readonly text: string;
}

/**
 * What this kit can and cannot do on this model, in words the panel shows
 * BEFORE the user applies. A `blocker` means "applying produces nothing".
 */
export function kitFitIssues(house: HouseModel, kit: FacadeKitDef): KitFitIssue[] {
  const issues: KitFitIssue[] = [];
  const ground = house.storeys[0];
  if (ground === undefined) {
    return [{ severity: 'blocker', text: 'Add a storey and draw external walls first — the facade dresses them.' }];
  }
  const walls = externalWallsOf(house, ground.id);
  if (walls.length === 0) {
    return [{ severity: 'blocker', text: 'Draw external walls first — the facade dresses them.' }];
  }

  let widest = 0;
  for (const w of walls) widest = Math.max(widest, segmentLengthMm({ a: w.a, b: w.b }));
  if (widest < kit.rules.minFacadeWidthMm) {
    issues.push({
      severity: 'note',
      text: `The widest frontage is ${String(widest)} mm; this kit is drawn for ${String(kit.rules.minFacadeWidthMm)} mm or more.`,
    });
  }

  if (findEntryDoor(house, ground) === null) {
    issues.push({ severity: 'note', text: 'No door on an external wall yet — the porch will be skipped.' });
  }

  if (
    kit.components.claddingZones.rule !== 'none' &&
    !house.stairs.some((s) => s.storeyId === ground.id)
  ) {
    issues.push({
      severity: 'note',
      text: 'No stair on the ground floor — the cladding band falls back to the entry bay.',
    });
  }

  return issues;
}
