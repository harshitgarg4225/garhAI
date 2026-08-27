/**
 * editOps.ts — THE COMMIT PATH, shared.
 *
 * Every op this directory emits is built here, by a pure function, so that:
 *
 *  - **The tools and the dimension-first overlay use the same code.** Dragging
 *    a wall's end and clicking its dimension label to type `3600` must produce
 *    byte-identical `wall.move` payloads, or the two produce different
 *    documents from the same intent. `setWallLengthOps` is that shared door,
 *    and it is exported for the overlays agent by name.
 *  - **The specs can assert payloads without a canvas.** `wallAddOp(...)` is a
 *    function call, not a gesture.
 *  - **Nothing reaches the server that `fold` would reject.** {@link dryRun}
 *    runs the REAL `applyGroup` from `@garh/model` — the same code path the
 *    model store and the API use — so an inline refusal quotes the same
 *    sentence the server's 422 would have carried.
 *
 * CONVERSION BOUNDARY: everything in and out of this module is integer
 * millimetres. The only rounding here is `roundMm` (half away from zero) and
 * `pointAtLengthMm`/`ptRound` from the canvas core; there is no other float
 * door. Callers that got their numbers from a pointer have already been
 * through `snapping.ts`.
 */

import {
  DEFAULTS,
  OpRejectedError,
  applyGroup,
  distMm,
  idType,
  polygonAreaMm2,
  type Direction4,
  type Id,
  type Op,
  type OpeningKind,
  type OpeningSwing,
  type Polygon,
  type ProjectDoc,
  type Pt,
  type RailingKind,
  type SizeMm,
  type StairKind,
  type StairLanding,
  type StoreyId,
  type ValidationIssue,
  type WallKind,
} from '@garh/model';

import { roundMm } from '../../../lib/units';
// From the module, not the `../core` barrel — see the note in `types.ts`.
import { pointAtLengthMm } from '../core/coords';
import { WALL_END_MARGIN_MM } from './constants';
import type { PreviewWall, ToolBlock } from './types';

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/**
 * Fold `ops` onto a copy of `doc` without keeping the result.
 *
 * Returns the issues, or an empty array when the group would apply cleanly.
 * This is the "say why inline rather than letting the server bounce it"
 * primitive: it is `applyGroup`, atomically, exactly as the store will run it.
 */
export function dryRun(doc: ProjectDoc, ops: readonly Op[]): readonly ValidationIssue[] {
  if (ops.length === 0) return [];
  try {
    applyGroup(doc, ops);
    return [];
  } catch (err) {
    if (err instanceof OpRejectedError) return err.issues;
    throw err;
  }
}

/** Issues → the tool's inline block, or null when the group is fine. */
export function toBlock(issues: readonly ValidationIssue[]): ToolBlock | null {
  const first = issues[0];
  if (first === undefined) return null;
  return { message: first.message, fix: first.fix ?? null, issues };
}

/** Convenience: dry-run and convert in one call. */
export function validateCommit(doc: ProjectDoc, ops: readonly Op[]): ToolBlock | null {
  return toBlock(dryRun(doc, ops));
}

// ---------------------------------------------------------------------------
// Geometry helpers the previews and the ops share
// ---------------------------------------------------------------------------

/** Bearing of `a`→`b` in integer degrees CCW from +X (east), 0–359. */
export function angleDeg(a: Pt, b: Pt): number {
  const raw = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
  const rounded = roundMm(raw);
  return ((rounded % 360) + 360) % 360;
}

/** Build the preview record for a wall-shaped thing. */
export function previewWall(a: Pt, b: Pt, thicknessMm: number, kind: WallKind): PreviewWall {
  return { a, b, thicknessMm, kind, lengthMm: distMm(a, b), angleDeg: angleDeg(a, b) };
}

// ---------------------------------------------------------------------------
// Walls
// ---------------------------------------------------------------------------

export interface WallAddInput {
  readonly id: Id<'wall'>;
  readonly storeyId: StoreyId;
  readonly a: Pt;
  readonly b: Pt;
  readonly thicknessMm: number;
  readonly kind: WallKind;
  readonly loadBearing?: boolean | undefined;
}

export function wallAddOp(input: WallAddInput): Op {
  return {
    type: 'wall.add',
    payload: {
      id: input.id,
      storeyId: input.storeyId,
      a: { x: input.a.x, y: input.a.y },
      b: { x: input.b.x, y: input.b.y },
      thicknessMm: input.thicknessMm,
      kind: input.kind,
      ...(input.loadBearing === undefined ? {} : { loadBearing: input.loadBearing }),
    },
  };
}

export function wallMoveOp(wallId: Id<'wall'>, a: Pt, b: Pt): Op {
  return {
    type: 'wall.move',
    payload: { wallId, a: { x: a.x, y: a.y }, b: { x: b.x, y: b.y } },
  };
}

export function wallThicknessOp(wallId: Id<'wall'>, thicknessMm: number): Op {
  return { type: 'wall.set_thickness', payload: { wallId, thicknessMm } };
}

/**
 * Translate whole walls by a delta. The select tool's drag, and the copilot's
 * "move the kitchen wall 300 east" once it lands.
 */
export function translateWallsOps(doc: ProjectDoc, wallIds: readonly string[], deltaMm: Pt): Op[] {
  const ops: Op[] = [];
  for (const id of wallIds) {
    const wall = doc.house.walls.find((w) => w.id === id);
    if (wall === undefined) continue;
    ops.push(
      wallMoveOp(
        wall.id,
        { x: wall.a.x + deltaMm.x, y: wall.a.y + deltaMm.y },
        { x: wall.b.x + deltaMm.x, y: wall.b.y + deltaMm.y },
      ),
    );
  }
  return ops;
}

/** Which end of a wall a length edit holds still. */
export type WallAnchorEnd = 'a' | 'b';

/**
 * THE DIMENSION-FIRST EDIT (§15 "numbers editable everywhere").
 *
 * Set a wall's centreline length to exactly `lengthMm`, keeping `anchor`'s end
 * where it is and sliding the other along the existing direction. Both the
 * canvas drag and the click-to-edit dimension label route through here, which
 * is the entire reason it exists as a named export rather than as three lines
 * inside the select tool.
 *
 * Returns `[]` for a degenerate wall or a non-positive length rather than
 * emitting an op that `fold` will refuse with `WALL_ZERO_LENGTH`.
 */
export function setWallLengthOps(
  doc: ProjectDoc,
  wallId: string,
  lengthMm: number,
  anchor: WallAnchorEnd = 'a',
): Op[] {
  const wall = doc.house.walls.find((w) => w.id === wallId);
  if (wall === undefined) return [];
  if (!Number.isSafeInteger(lengthMm) || lengthMm <= 0) return [];
  if (wall.a.x === wall.b.x && wall.a.y === wall.b.y) return [];

  if (anchor === 'a') {
    const b = pointAtLengthMm(wall.a, wall.b, lengthMm);
    return [wallMoveOp(wall.id, wall.a, b)];
  }
  const a = pointAtLengthMm(wall.b, wall.a, lengthMm);
  return [wallMoveOp(wall.id, a, wall.b)];
}

// ---------------------------------------------------------------------------
// Openings
// ---------------------------------------------------------------------------

/**
 * The legal window for an opening centre on a wall, per the §3 invariant that
 * 115 mm of solid wall must remain at each end.
 *
 * Mirrors `openingFitIssue` in `packages/model/src/validate.ts` exactly,
 * including its `floor`/`ceil` asymmetry for odd widths — if the two ever
 * disagree, a door previews as legal and folds as rejected, which is the exact
 * failure mode this whole module exists to prevent. Returns null when the wall
 * is too short to host the opening at all.
 */
export function openingOffsetWindow(
  wallLengthMm: number,
  widthMm: number,
): { readonly minMm: number; readonly maxMm: number } | null {
  const usable = wallLengthMm - 2 * WALL_END_MARGIN_MM;
  if (widthMm > usable) return null;
  const minMm = WALL_END_MARGIN_MM + Math.floor(widthMm / 2);
  const maxMm = wallLengthMm - WALL_END_MARGIN_MM - Math.ceil(widthMm / 2);
  if (maxMm < minMm) return null;
  return { minMm, maxMm };
}

/** Clamp a desired centre offset into the legal window. Null if it cannot fit. */
export function clampOpeningOffset(
  offsetMm: number,
  wallLengthMm: number,
  widthMm: number,
): number | null {
  const window = openingOffsetWindow(wallLengthMm, widthMm);
  if (window === null) return null;
  return Math.min(window.maxMm, Math.max(window.minMm, offsetMm));
}

/** Default size for an opening kind, from the §3 Indian residential defaults. */
export function defaultOpeningParams(kind: OpeningKind): {
  widthMm: number;
  heightMm: number;
  sillMm: number;
} {
  if (kind === 'door') {
    return { widthMm: DEFAULTS.doorWidthMm, heightMm: DEFAULTS.doorHeightMm, sillMm: 0 };
  }
  if (kind === 'window') {
    return {
      widthMm: DEFAULTS.windowWidthMm,
      heightMm: DEFAULTS.windowHeightMm,
      sillMm: DEFAULTS.sillDefaultMm,
    };
  }
  return {
    widthMm: DEFAULTS.ventilatorWidthMm,
    heightMm: DEFAULTS.ventilatorHeightMm,
    sillMm: DEFAULTS.ventilatorSillMm,
  };
}

export interface OpeningAddInput {
  readonly id: Id<'opening'>;
  readonly wallId: Id<'wall'>;
  readonly kind: OpeningKind;
  readonly widthMm: number;
  readonly heightMm: number;
  readonly sillMm: number;
  readonly offsetMm: number;
  readonly swing: OpeningSwing;
}

export function openingAddOp(input: OpeningAddInput): Op {
  return { type: 'opening.add', payload: { ...input } };
}

export function openingMoveOp(openingId: Id<'opening'>, offsetMm: number, wallId?: Id<'wall'>): Op {
  return {
    type: 'opening.move',
    payload: { openingId, offsetMm, ...(wallId === undefined ? {} : { wallId }) },
  };
}

export function openingResizeOp(
  openingId: Id<'opening'>,
  patch: { widthMm?: number; heightMm?: number; sillMm?: number },
): Op {
  return {
    type: 'opening.resize',
    payload: {
      openingId,
      ...(patch.widthMm === undefined ? {} : { widthMm: patch.widthMm }),
      ...(patch.heightMm === undefined ? {} : { heightMm: patch.heightMm }),
      ...(patch.sillMm === undefined ? {} : { sillMm: patch.sillMm }),
    },
  };
}

export function openingFlipOp(openingId: Id<'opening'>, swing: OpeningSwing): Op {
  return { type: 'opening.flip', payload: { openingId, swing } };
}

/** The four swings, in the order `X` cycles them. */
export const SWING_CYCLE: readonly OpeningSwing[] = [
  'in-left',
  'in-right',
  'out-right',
  'out-left',
];

export function nextSwing(swing: OpeningSwing): OpeningSwing {
  const i = SWING_CYCLE.indexOf(swing);
  return SWING_CYCLE[(i + 1) % SWING_CYCLE.length] ?? 'in-left';
}

// ---------------------------------------------------------------------------
// Stairs
// ---------------------------------------------------------------------------

export interface StairAddInput {
  readonly id: Id<'stair'>;
  readonly storeyId: StoreyId;
  readonly kind: StairKind;
  readonly origin: Pt;
  readonly direction: Direction4;
  readonly riserMm: number;
  readonly treadMm: number;
  readonly widthMm: number;
  readonly risersCount: number;
  readonly landing: StairLanding | null;
}

export function stairAddOp(input: StairAddInput): Op {
  return { type: 'stair.add', payload: { ...input } };
}

// ---------------------------------------------------------------------------
// Balconies
// ---------------------------------------------------------------------------

export interface BalconyAddInput {
  readonly id: Id<'balcony'>;
  readonly storeyId: StoreyId;
  readonly polygon: Polygon;
  readonly railingKind: RailingKind;
  readonly railingHeightMm: number;
  readonly projectionMm: number;
  readonly slabThicknessMm: number;
}

export function balconyAddOp(input: BalconyAddInput): Op {
  return {
    type: 'balcony.set',
    payload: {
      action: 'add',
      id: input.id,
      storeyId: input.storeyId,
      polygon: input.polygon.map((p) => ({ x: p.x, y: p.y })),
      railingKind: input.railingKind,
      railingHeightMm: input.railingHeightMm,
      projectionMm: input.projectionMm,
      slabThicknessMm: input.slabThicknessMm,
    },
  };
}

/** Area of a balcony ring, mm² — the readout while drawing. */
export function ringAreaMm2(points: readonly Pt[]): number {
  return points.length < 3 ? 0 : polygonAreaMm2(points);
}

// ---------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------

export function furniturePlaceOp(input: {
  readonly id: Id<'furniture'>;
  readonly storeyId: StoreyId;
  readonly catalogId: string;
  readonly pt: Pt;
  readonly rotationDeg: number;
}): Op {
  return { type: 'furniture.set', payload: { action: 'place', ...input } };
}

export function furnitureTransformOp(
  id: Id<'furniture'>,
  patch: { pt?: Pt; rotationDeg?: number },
): Op {
  return {
    type: 'furniture.set',
    payload: {
      action: 'transform',
      id,
      ...(patch.pt === undefined ? {} : { pt: patch.pt }),
      ...(patch.rotationDeg === undefined ? {} : { rotationDeg: patch.rotationDeg }),
    },
  };
}

/** Footprint of a catalogue item at a rotation, as a size in plan. */
export function furnitureFootprintMm(
  widthMm: number,
  depthMm: number,
  rotationDeg: number,
): SizeMm {
  const quarter = (((Math.round(rotationDeg / 90) % 4) + 4) % 4) as 0 | 1 | 2 | 3;
  return quarter === 1 || quarter === 3
    ? { xMm: depthMm, yMm: widthMm }
    : { xMm: widthMm, yMm: depthMm };
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

/**
 * Ops that delete a mixed selection, in an order `fold` accepts.
 *
 * Openings go first. `wall.delete` cascades to the openings it hosts, so a
 * group that deleted the wall first and the opening second would be rejected
 * with `OPENING_UNKNOWN` — and because groups are atomic, that would refuse the
 * whole delete rather than half of it. Rooms are derived and are silently
 * skipped: there is no `room.delete`, and there should not be one.
 */
export function deleteOps(doc: ProjectDoc, ids: readonly string[]): Op[] {
  const openings: Op[] = [];
  const others: Op[] = [];
  const walls: Op[] = [];

  for (const id of ids) {
    switch (idType(id)) {
      case 'opening':
        if (doc.house.openings.some((o) => o.id === id)) {
          openings.push({ type: 'opening.delete', payload: { openingId: id } });
        }
        break;
      case 'wall':
        if (doc.house.walls.some((w) => w.id === id)) {
          walls.push({ type: 'wall.delete', payload: { wallId: id } });
        }
        break;
      case 'stair':
        if (doc.house.stairs.some((s) => s.id === id)) {
          others.push({ type: 'stair.delete', payload: { stairId: id } });
        }
        break;
      case 'furniture':
        if (doc.house.furniture.some((f) => f.id === id)) {
          others.push({ type: 'furniture.set', payload: { action: 'delete', id } });
        }
        break;
      case 'balcony':
        if (doc.house.balconies.some((b) => b.id === id)) {
          others.push({ type: 'balcony.set', payload: { action: 'delete', id } });
        }
        break;
      case 'column':
        if (doc.house.columns.some((c) => c.id === id)) {
          others.push({ type: 'column.set', payload: { action: 'delete', id } });
        }
        break;
      default:
        break;
    }
  }

  // Openings hosted on a wall that is also being deleted would be deleted
  // twice; the wall's cascade is enough, so drop them from the explicit list.
  const doomedWallIds = new Set(
    walls.map((op) => (op.type === 'wall.delete' ? op.payload.wallId : '')),
  );
  const keptOpenings = openings.filter((op) => {
    if (op.type !== 'opening.delete') return true;
    const opening = doc.house.openings.find((o) => o.id === op.payload.openingId);
    return opening === undefined ? false : !doomedWallIds.has(opening.wallId);
  });

  return [...keptOpenings, ...others, ...walls];
}

/** Undo-toast copy for a delete: "Wall deleted", "3 things deleted". */
export function deleteLabel(ops: readonly Op[]): string {
  if (ops.length === 1) {
    const op = ops[0];
    if (op?.type === 'wall.delete') return 'Wall deleted';
    if (op?.type === 'opening.delete') return 'Opening deleted';
    if (op?.type === 'stair.delete') return 'Stair deleted';
    if (op?.type === 'furniture.set') return 'Furniture deleted';
    if (op?.type === 'balcony.set') return 'Balcony deleted';
    if (op?.type === 'column.set') return 'Column deleted';
  }
  return `${String(ops.length)} things deleted`;
}
