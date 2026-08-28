/**
 * copyStorey.ts — "make the first floor the same as the ground floor".
 *
 * This is the single most common real action on an Indian G+2 job, and it is
 * expressed here as a PLAN: a pure function from a folded document to a list of
 * ops. Nothing in this file touches a store, dispatches anything or mutates
 * state (golden rule 1 — the UI dispatches ops, it never writes the document).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONLY OPS THAT ALREADY EXIST — AND WHY THAT MATTERS MORE THAN IT LOOKS
 * ════════════════════════════════════════════════════════════════════════════
 * The state hash is byte-identical across the TypeScript fold and the Python
 * twin (`apps/api/garh_model`), and the whole product rests on that: version
 * restore, the server's hash check on every append, share links, the drawing
 * pipeline. An op type that folds here and not there would break it silently.
 *
 * So a storey copy invents NOTHING. It emits exactly nine op types, every one
 * of them already in the §4 taxonomy and already exercised by the fold's own
 * tests and the copilot corpus:
 *
 *   storey.add · storey.set_height · wall.add · wall.delete · opening.add
 *   stair.add · stair.delete · column.set · furniture.set · balcony.set
 *   room.assign · room.set_target
 *
 * (`wall.delete` cascades to the openings hosted on the wall, which is why
 * there is no `opening.delete` in the list.)
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE ORDER IS THE CONTRACT
 * ════════════════════════════════════════════════════════════════════════════
 *   1. `storey.add`         — only when copying onto a NEW storey.
 *   2. `storey.set_height`  — only when asked, and only when it differs.
 *   3. clear the target     — furniture, columns, balconies, stairs, walls.
 *   4. add the copies       — walls FIRST (openings need a host that exists),
 *                             then openings, stairs, columns, furniture,
 *                             balconies.
 *   5. room metadata        — see below.
 *
 * Walls are cleared LAST and added FIRST for the same reason: an op group's
 * inverse is the reversed concatenation of the per-op inverses, so putting
 * `wall.delete` last puts `wall.add` first in the undo — the walls come back
 * before the openings that need them.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ROOMS ARE DERIVED, SO THEIR IDS CANNOT BE PREDICTED
 * ════════════════════════════════════════════════════════════════════════════
 * Room ids are `derivedIdUnique('room', `${storeyId}|${polygonKey}`)` — the
 * storey id is in the key, so the copied rooms get DIFFERENT ids from their
 * originals and there is no arithmetic that would tell us what they are. The
 * honest way to carry "Master Bedroom" up a floor is therefore to fold the
 * geometry ops on a fork, look at which rooms actually appeared, match them to
 * the source rooms by polygon, and emit `room.assign` for exactly those.
 *
 * That is not a new trick: `fold.ts`'s `withRoomMetadataRestore` does the same
 * thing to keep room names across an undo, and for the same reason. Ops are
 * only emitted for rooms PROVEN to exist after the fold, so the group cannot
 * fail on a room op.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS NOT COPIED, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 * Material assignments and facade components are not copied. Both are
 * building-scoped sub-models that happen to carry a storey reference (§3 keeps
 * the facade isolated from anything that affects areas), and silently
 * duplicating them would put a second assignment on a surface group with no way
 * for the architect to see it happened. The confirm dialog lists exactly what
 * IS copied, counted from this plan — so nothing is claimed that is not done.
 */

import {
  DEFAULTS,
  newId,
  tryFold,
  type HouseModel,
  type Op,
  type OpeningId,
  type Polygon,
  type ProjectDoc,
  type Room,
  type Storey,
  type StoreyId,
  type ValidationIssue,
  type WallId,
} from '@garh/model';

// ---------------------------------------------------------------------------
// What a storey holds
// ---------------------------------------------------------------------------

/** Element counts for one storey. Drives the confirm copy and the panel rows. */
export interface StoreyContentCounts {
  readonly walls: number;
  readonly openings: number;
  readonly stairs: number;
  readonly columns: number;
  readonly furniture: number;
  readonly balconies: number;
}

const EMPTY_COUNTS: StoreyContentCounts = {
  walls: 0,
  openings: 0,
  stairs: 0,
  columns: 0,
  furniture: 0,
  balconies: 0,
};

export function storeyContentCounts(house: HouseModel, storeyId: string): StoreyContentCounts {
  const wallIds = new Set<string>();
  for (const wall of house.walls) if (wall.storeyId === storeyId) wallIds.add(wall.id);
  let openings = 0;
  for (const opening of house.openings) if (wallIds.has(opening.wallId)) openings += 1;
  return {
    walls: wallIds.size,
    openings,
    stairs: house.stairs.filter((s) => s.storeyId === storeyId).length,
    columns: house.columns.filter((c) => c.storeyId === storeyId).length,
    furniture: house.furniture.filter((f) => f.storeyId === storeyId).length,
    balconies: house.balconies.filter((b) => b.storeyId === storeyId).length,
  };
}

export function totalElements(counts: StoreyContentCounts): number {
  return (
    counts.walls +
    counts.openings +
    counts.stairs +
    counts.columns +
    counts.furniture +
    counts.balconies
  );
}

export function isStoreyEmpty(counts: StoreyContentCounts): boolean {
  return totalElements(counts) === 0;
}

/** "9 walls, 5 openings and 1 stair" — only the families that are present. */
export function describeCounts(counts: StoreyContentCounts): string {
  const parts: string[] = [];
  const add = (n: number, one: string, many: string): void => {
    if (n > 0) parts.push(`${String(n)} ${n === 1 ? one : many}`);
  };
  add(counts.walls, 'wall', 'walls');
  add(counts.openings, 'opening', 'openings');
  add(counts.stairs, 'stair', 'stairs');
  add(counts.columns, 'column', 'columns');
  add(counts.furniture, 'furniture item', 'furniture items');
  add(counts.balconies, 'balcony', 'balconies');
  if (parts.length === 0) return 'nothing';
  if (parts.length === 1) return parts[0] as string;
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1] as string}`;
}

// ---------------------------------------------------------------------------
// Inputs and outcomes
// ---------------------------------------------------------------------------

/** Copy onto a storey that exists, or onto a new one added above the top. */
export type StoreyCopyTarget =
  | { readonly kind: 'existing'; readonly storeyId: string }
  | { readonly kind: 'new' };

export interface StoreyCopyInput {
  readonly sourceStoreyId: string;
  readonly target: StoreyCopyTarget;
  /**
   * Also set the target's floor-to-floor height to the source's.
   *
   * Not cosmetic: `stair.add` is rejected when risers × riser height misses the
   * storey height by more than 10 mm, and `opening.add` is rejected when sill +
   * height exceeds it. Copying a floor onto a shorter one without this is a
   * refusal, which the panel offers this as the fix for.
   */
  readonly matchHeight?: boolean;
}

/** Why a copy cannot be planned. Every one of these is shown to the user. */
export type StoreyCopyRefusalReason =
  | 'same-storey'
  | 'unknown-storey'
  | 'empty-source'
  | 'rejected';

export interface StoreyCopyRefusal {
  readonly reason: StoreyCopyRefusalReason;
  readonly message: string;
  /** The fold's own issues when `reason` is 'rejected'; empty otherwise. */
  readonly issues: readonly ValidationIssue[];
}

export interface StoreyCopyPlan {
  /** Dispatch these as ONE group. One gesture, one undo (§4). */
  readonly ops: readonly Op[];
  readonly sourceStoreyId: string;
  /** The storey copied onto — minted here when the target is a new storey. */
  readonly targetStoreyId: string;
  readonly sourceName: string;
  /** The target's name AFTER the fold (a new storey is named by the fold). */
  readonly targetName: string;
  /** What is copied up. */
  readonly copied: StoreyContentCounts;
  /** What is deleted from the target to make room. Zero when it was empty. */
  readonly replaced: StoreyContentCounts;
  /** Rooms whose name/type/lock/target travelled with the geometry. */
  readonly roomsCarried: number;
  /** `[fromMm, toMm]` when the plan changes the target's height, else null. */
  readonly heightChangeMm: readonly [number, number] | null;
  /** Undo-toast copy: "Ground Floor copied to First Floor". */
  readonly label: string;
}

export type StoreyCopyPlanResult =
  | { readonly ok: true; readonly plan: StoreyCopyPlan }
  | { readonly ok: false; readonly refusal: StoreyCopyRefusal };

// ---------------------------------------------------------------------------
// Fold helpers
// ---------------------------------------------------------------------------

type FoldAllResult =
  | { readonly ok: true; readonly doc: ProjectDoc }
  | { readonly ok: false; readonly issues: readonly ValidationIssue[] };

/**
 * Fold a whole list on a fork, stopping at the first refusal.
 *
 * `computeInverse: false` because nobody reads the inverse here — the real
 * dispatch computes its own when the group is applied for real.
 */
function foldAll(doc: ProjectDoc, ops: readonly Op[]): FoldAllResult {
  let current = doc;
  for (const op of ops) {
    const outcome = tryFold(current, op, { computeInverse: false });
    if (!outcome.ok) return { ok: false, issues: outcome.issues };
    current = outcome.model;
  }
  return { ok: true, doc: current };
}

/** Human copy for a refusal: what happened, then what to do about it. */
function issuesToMessage(issues: readonly ValidationIssue[]): string {
  const first = issues[0];
  if (first === undefined) return 'That copy is not valid here.';
  const more = issues.length > 1 ? ` (+${String(issues.length - 1)} more)` : '';
  return first.fix === undefined
    ? `${first.message}${more}`
    : `${first.message} ${first.fix}${more}`;
}

// ---------------------------------------------------------------------------
// Op builders
// ---------------------------------------------------------------------------

/**
 * Delete everything on a storey, leaf elements first and walls last.
 *
 * `wall.delete` takes the openings hosted on the wall with it (see the fold),
 * so listing openings here would be a second delete of something already gone.
 */
function clearOps(house: HouseModel, storeyId: string): Op[] {
  const ops: Op[] = [];
  for (const item of house.furniture) {
    if (item.storeyId === storeyId) {
      ops.push({ type: 'furniture.set', payload: { action: 'delete', id: item.id } });
    }
  }
  for (const column of house.columns) {
    if (column.storeyId === storeyId) {
      ops.push({ type: 'column.set', payload: { action: 'delete', id: column.id } });
    }
  }
  for (const balcony of house.balconies) {
    if (balcony.storeyId === storeyId) {
      ops.push({ type: 'balcony.set', payload: { action: 'delete', id: balcony.id } });
    }
  }
  for (const stair of house.stairs) {
    if (stair.storeyId === storeyId) {
      ops.push({ type: 'stair.delete', payload: { stairId: stair.id } });
    }
  }
  for (const wall of house.walls) {
    if (wall.storeyId === storeyId) {
      ops.push({ type: 'wall.delete', payload: { wallId: wall.id } });
    }
  }
  return ops;
}

/**
 * Copy every element of `sourceId` onto `targetId` with fresh ids.
 *
 * Ids are minted HERE, by the op producer, exactly as `ops.ts` golden rule 1
 * requires — never inside the fold, so that replaying the log is deterministic.
 */
function copyOps(house: HouseModel, sourceId: string, targetId: string): Op[] {
  const ops: Op[] = [];
  const target = targetId as StoreyId;

  // Walls first, and remember the mapping: the openings below are re-hosted
  // onto the copies, not onto the originals a floor down.
  const wallIdMap = new Map<string, WallId>();
  for (const wall of house.walls) {
    if (wall.storeyId !== sourceId) continue;
    const id = newId('wall');
    wallIdMap.set(wall.id, id);
    ops.push({
      type: 'wall.add',
      payload: {
        id,
        storeyId: target,
        a: wall.a,
        b: wall.b,
        thicknessMm: wall.thicknessMm,
        kind: wall.kind,
        loadBearing: wall.loadBearing,
      },
    });
  }

  for (const opening of house.openings) {
    const host = wallIdMap.get(opening.wallId);
    if (host === undefined) continue;
    const id: OpeningId = newId('opening');
    ops.push({
      type: 'opening.add',
      payload: {
        id,
        wallId: host,
        kind: opening.kind,
        widthMm: opening.widthMm,
        heightMm: opening.heightMm,
        sillMm: opening.sillMm,
        offsetMm: opening.offsetMm,
        swing: opening.swing,
        tag: opening.tag,
      },
    });
  }

  for (const stair of house.stairs) {
    if (stair.storeyId !== sourceId) continue;
    ops.push({
      type: 'stair.add',
      payload: {
        id: newId('stair'),
        storeyId: target,
        kind: stair.kind,
        origin: stair.origin,
        direction: stair.direction,
        riserMm: stair.riserMm,
        treadMm: stair.treadMm,
        widthMm: stair.widthMm,
        risersCount: stair.risersCount,
        landing: stair.landing,
      },
    });
  }

  for (const column of house.columns) {
    if (column.storeyId !== sourceId) continue;
    ops.push({
      type: 'column.set',
      payload: {
        action: 'add',
        id: newId('column'),
        storeyId: target,
        pt: column.pt,
        sizeMm: column.sizeMm,
      },
    });
  }

  for (const item of house.furniture) {
    if (item.storeyId !== sourceId) continue;
    ops.push({
      type: 'furniture.set',
      payload: {
        action: 'place',
        id: newId('furniture'),
        storeyId: target,
        catalogId: item.catalogId,
        pt: item.pt,
        rotationDeg: item.rotationDeg,
      },
    });
  }

  for (const balcony of house.balconies) {
    if (balcony.storeyId !== sourceId) continue;
    ops.push({
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: newId('balcony'),
        storeyId: target,
        polygon: balcony.polygon,
        railingKind: balcony.railingKind,
        railingHeightMm: balcony.railingHeightMm,
        projectionMm: balcony.projectionMm,
        slabThicknessMm: balcony.slabThicknessMm,
      },
    });
  }

  return ops;
}

/**
 * A room's polygon as an order-independent signature.
 *
 * The copied walls are geometrically identical to the source's, so the detector
 * produces identical polygons on the target storey — but nothing promises the
 * ring starts at the same vertex, so the vertices are sorted before joining.
 * Coordinates are absolute integer mm, so two different rooms on one storey can
 * never collide here.
 */
function roomSignature(polygon: Polygon): string {
  return polygon
    .map((p) => `${String(p.x)},${String(p.y)}`)
    .sort()
    .join(' ');
}

/** True when a room carries anything a copy would lose by not saying so. */
function roomHasMetadata(room: Room): boolean {
  return room.type !== 'unassigned' || room.name !== '' || room.tags.length > 0 || room.locked;
}

function roomHasTarget(room: Room): boolean {
  return room.targetAreaMm2 !== null || room.mustFace !== null;
}

/**
 * Carry room names, types, locks and solver targets onto the copied rooms.
 *
 * `after` is the document as it will be once the geometry ops land, so every id
 * referenced here is proven to exist — the ops cannot fail.
 */
function roomMetadataOps(
  before: HouseModel,
  after: HouseModel,
  sourceId: string,
  targetId: string,
): { ops: Op[]; carried: number } {
  const bySignature = new Map<string, Room[]>();
  for (const room of before.rooms) {
    if (room.storeyId !== sourceId) continue;
    if (!roomHasMetadata(room) && !roomHasTarget(room)) continue;
    const key = roomSignature(room.polygon);
    const bucket = bySignature.get(key);
    if (bucket === undefined) bySignature.set(key, [room]);
    else bucket.push(room);
  }
  if (bySignature.size === 0) return { ops: [], carried: 0 };

  const ops: Op[] = [];
  let carried = 0;
  for (const room of after.rooms) {
    if (room.storeyId !== targetId) continue;
    // `shift`, not `get`: two rooms cannot share a signature, but consuming the
    // match keeps this honest if the detector ever merges two into one.
    const source = bySignature.get(roomSignature(room.polygon))?.shift();
    if (source === undefined) continue;
    carried += 1;
    if (roomHasMetadata(source)) {
      ops.push({
        type: 'room.assign',
        payload: {
          roomId: room.id,
          type: source.type,
          name: source.name,
          tags: source.tags,
          locked: source.locked,
        },
      });
    }
    if (roomHasTarget(source)) {
      ops.push({
        type: 'room.set_target',
        payload: {
          roomId: room.id,
          targetAreaMm2: source.targetAreaMm2,
          mustFace: source.mustFace,
        },
      });
    }
  }
  return { ops, carried };
}

// ---------------------------------------------------------------------------
// The planner
// ---------------------------------------------------------------------------

function refuse(
  reason: StoreyCopyRefusalReason,
  message: string,
  issues: readonly ValidationIssue[] = [],
): StoreyCopyPlanResult {
  return { ok: false, refusal: { reason, message, issues } };
}

/**
 * Plan a storey copy against `doc`. Pure: no store, no dispatch, no mutation.
 *
 * The returned ops are verified by folding them on a fork before they are
 * handed back, so a plan that comes back `ok` is a plan the real dispatch will
 * accept — the confirm dialog cannot promise a copy that then fails.
 */
export function planStoreyCopy(doc: ProjectDoc, input: StoreyCopyInput): StoreyCopyPlanResult {
  const house = doc.house;
  const source = house.storeys.find((s) => s.id === input.sourceStoreyId);
  if (source === undefined) {
    return refuse('unknown-storey', 'That storey is no longer part of this design.');
  }

  const copied = storeyContentCounts(house, source.id);
  if (isStoreyEmpty(copied)) {
    return refuse('empty-source', `${source.name} is empty — there is nothing to copy.`);
  }

  // Hoisted so the discriminant narrows: TypeScript will not carry a narrowing
  // of `input.target` into the `find` callback below.
  const wanted = input.target;
  const target: Storey | undefined =
    wanted.kind === 'existing' ? house.storeys.find((s) => s.id === wanted.storeyId) : undefined;
  if (wanted.kind === 'existing' && target === undefined) {
    return refuse('unknown-storey', 'That storey is no longer part of this design.');
  }
  if (target !== undefined && target.id === source.id) {
    return refuse('same-storey', 'Choose a different storey to copy onto.');
  }

  const ops: Op[] = [];
  let targetStoreyId: string;
  let heightChangeMm: readonly [number, number] | null = null;

  if (target === undefined) {
    // A new storey on top. `name` is deliberately omitted so the fold's own
    // `defaultStoreyName(index)` names it — one source of truth for "First
    // Floor", shared with the Python twin. `level` is omitted for the same
    // reason: FFLs re-derive from the storey heights.
    targetStoreyId = newId('storey');
    ops.push({
      type: 'storey.add',
      payload: {
        id: targetStoreyId,
        index: house.storeys.length,
        heightMm: source.heightMm,
      },
    });
  } else {
    targetStoreyId = target.id;
    if ((input.matchHeight ?? false) && target.heightMm !== source.heightMm) {
      heightChangeMm = [target.heightMm, source.heightMm];
      ops.push({
        type: 'storey.set_height',
        payload: { storeyId: target.id, heightMm: source.heightMm },
      });
    }
    ops.push(...clearOps(house, target.id));
  }

  ops.push(...copyOps(house, source.id, targetStoreyId));

  // Fold the geometry on a fork: this is the only way to learn the derived
  // room ids, and it is also the honest way to find out whether the copy is
  // valid at all (a stair that does not fit the target's height, an opening
  // taller than it) without asking the rules a second time in our own words.
  const geometryFold = foldAll(doc, ops);
  if (!geometryFold.ok) {
    return refuse('rejected', issuesToMessage(geometryFold.issues), geometryFold.issues);
  }

  const rooms = roomMetadataOps(house, geometryFold.doc.house, source.id, targetStoreyId);
  ops.push(...rooms.ops);

  // Belt and braces: fold the FULL list, room ops included. If this ever fails
  // the plan is wrong, and a refusal the user can read beats a rejected
  // dispatch after a confirm dialog said it would work.
  const finalFold = foldAll(doc, ops);
  if (!finalFold.ok) {
    return refuse('rejected', issuesToMessage(finalFold.issues), finalFold.issues);
  }

  const targetName =
    finalFold.doc.house.storeys.find((s) => s.id === targetStoreyId)?.name ?? 'the storey above';
  const replaced = target === undefined ? EMPTY_COUNTS : storeyContentCounts(house, target.id);

  return {
    ok: true,
    plan: {
      ops,
      sourceStoreyId: source.id,
      targetStoreyId,
      sourceName: source.name,
      targetName,
      copied,
      replaced,
      roomsCarried: rooms.carried,
      heightChangeMm,
      label: `${source.name} copied to ${targetName}`,
    },
  };
}

/**
 * The op that adds an empty storey on top.
 *
 * Same omissions as the copy's `storey.add`, for the same reason: the fold owns
 * the default name and the derived FFL.
 */
export function addStoreyOp(doc: ProjectDoc): { op: Op; storeyId: string } {
  const storeys = doc.house.storeys;
  const top = storeys[storeys.length - 1];
  const storeyId = newId('storey');
  return {
    op: {
      type: 'storey.add',
      payload: {
        id: storeyId,
        index: storeys.length,
        // A new floor matches the one below it — that is what an architect
        // means by "add a floor". `DEFAULTS.storeyHeightMm` only applies to the
        // very first storey, where there is nothing below to match.
        heightMm: top?.heightMm ?? DEFAULTS.storeyHeightMm,
      },
    },
    storeyId,
  };
}
