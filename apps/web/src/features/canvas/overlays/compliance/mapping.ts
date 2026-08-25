/**
 * mapping.ts — compliance results become chips, markers and zoom targets. PURE.
 *
 * Golden rule 5: "compliance never blocks, it informs." Nothing in this file
 * gates anything. It answers three questions and stops:
 *
 *   1. which chip comes first (severity, then a stable order)
 *   2. WHICH ELEMENT a chip is pointing at, in millimetres, so clicking the
 *      chip can select it and zoom to it
 *   3. where the on-canvas marker goes
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE MAPPING IS THE HARD PART
 * ────────────────────────────────────────────────────────────────────────────
 * The rules engine returns `elements: string[]` — element ids, no geometry,
 * because it is a pure function over the model and must stay one. Turning
 * "bedroom 2 is 8.9 m²" into "zoom here" means resolving those ids against the
 * document, and the resolution has to be total: a rule can name a room, a wall,
 * an opening, a stair, a balcony, a storey, or nothing at all (a FAR check is
 * about the whole building). Every one of those cases is handled below, and the
 * "nothing at all" case returns null rather than a bbox at the origin — a chip
 * that zooms you to the corner of the plot for a plot-wide rule is worse than a
 * chip that does not zoom.
 */

import { bbox, idType, type Bbox, type HouseModel, type Pt } from '@garh/model';

import type { ComplianceIssueVM, ComplianceResultStatus } from '../../../../components/types';
import { complianceIssueKey } from '../../../../components/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Where a chip points: a box in plan mm, on a storey. */
export interface ComplianceFocus {
  /** Null when the elements span storeys or none of them has one. */
  readonly storeyId: string | null;
  readonly bboxMm: Bbox;
  /** The ids that resolved — a subset of the issue's, missing ones dropped. */
  readonly elementIds: readonly string[];
}

export interface ComplianceChipVM {
  /** Stable list key. `complianceIssueKey` plus the index, per its own docs. */
  readonly key: string;
  readonly ruleId: string;
  readonly status: ComplianceResultStatus;
  /** The one-line human sentence, written by the rules layer. Never built here. */
  readonly message: string;
  readonly cite: string | undefined;
  readonly confidence: 'seed' | 'reviewed' | 'verified' | undefined;
  readonly fixAvailable: boolean;
  readonly fixHint: string | undefined;
  readonly elementIds: readonly string[];
  /** Null when nothing on the canvas corresponds to this result. */
  readonly focus: ComplianceFocus | null;
  /** Lower sorts first. */
  readonly rank: number;
}

/** Severity order: what an architect must look at first. */
const STATUS_RANK: Readonly<Record<ComplianceResultStatus, number>> = {
  fail: 0,
  warn: 1,
  pass: 2,
  not_applicable: 3,
};

/**
 * What the strip shows by default. Passes are not hidden information — the
 * Compliance tab lists all of them — but a strip full of green chips is a strip
 * nobody reads, and the one red chip in it disappears.
 */
export const DEFAULT_VISIBLE_STATUSES: readonly ComplianceResultStatus[] = ['fail', 'warn'];

// ---------------------------------------------------------------------------
// Element → geometry
// ---------------------------------------------------------------------------

/**
 * The plan footprint of one element, or null when it has none on this storey.
 *
 * Walls are inflated by half their thickness so the box covers the masonry and
 * not just the centreline — zooming to a "wall too thin" violation should frame
 * the wall, not a line through the middle of it.
 */
export function elementBboxMm(
  house: HouseModel,
  elementId: string,
): { bboxMm: Bbox; storeyId: string | null } | null {
  const type = idType(elementId);
  if (type === null) return null;

  switch (type) {
    case 'wall': {
      const wall = house.walls.find((w) => w.id === elementId);
      if (wall === undefined) return null;
      const half = Math.ceil(wall.thicknessMm / 2);
      const box = bbox([wall.a, wall.b]);
      return {
        bboxMm: {
          minX: box.minX - half,
          minY: box.minY - half,
          maxX: box.maxX + half,
          maxY: box.maxY + half,
        },
        storeyId: wall.storeyId,
      };
    }
    case 'room': {
      const room = house.rooms.find((r) => r.id === elementId);
      if (room === undefined || room.polygon.length < 3) return null;
      return { bboxMm: bbox(room.polygon), storeyId: room.storeyId };
    }
    case 'opening': {
      const opening = house.openings.find((o) => o.id === elementId);
      if (opening === undefined) return null;
      const wall = house.walls.find((w) => w.id === opening.wallId);
      if (wall === undefined) return null;
      const centre = pointAlongWall(wall.a, wall.b, opening.offsetMm);
      const half = Math.ceil(opening.widthMm / 2);
      return {
        bboxMm: {
          minX: centre.x - half,
          minY: centre.y - half,
          maxX: centre.x + half,
          maxY: centre.y + half,
        },
        storeyId: wall.storeyId,
      };
    }
    case 'stair': {
      const stair = house.stairs.find((s) => s.id === elementId);
      if (stair === undefined) return null;
      // Footprint from the flight geometry: risers × tread along the direction
      // of travel, clear width across it, plus the landing when there is one.
      const runMm = Math.max(1, stair.risersCount - 1) * stair.treadMm;
      const alongMm = runMm + (stair.landing === null ? 0 : stair.landing.depthMm);
      const acrossMm = Math.max(stair.widthMm, stair.landing?.widthMm ?? 0);
      // `origin` is the first riser; `direction` is the way UP. The flight runs
      // `alongMm` that way and `acrossMm` to its left, which for the four
      // orthogonal cases is just a signed extent on each axis.
      const vertical = stair.direction === 'N' || stair.direction === 'S';
      const signAlong = stair.direction === 'S' || stair.direction === 'W' ? -1 : 1;
      const corner: Pt = {
        x: stair.origin.x + (vertical ? acrossMm : signAlong * alongMm),
        y: stair.origin.y + (vertical ? signAlong * alongMm : acrossMm),
      };
      return { bboxMm: bbox([stair.origin, corner]), storeyId: stair.storeyId };
    }
    case 'balcony': {
      const balcony = house.balconies.find((b) => b.id === elementId);
      if (balcony === undefined || balcony.polygon.length < 3) return null;
      return { bboxMm: bbox(balcony.polygon), storeyId: balcony.storeyId };
    }
    case 'furniture': {
      const item = house.furniture.find((f) => f.id === elementId);
      if (item === undefined) return null;
      // Catalogue footprints live in the API's furniture catalogue, not in the
      // model document. A 600 mm box round the placement point is enough to
      // frame the item when the camera zooms; it is never used as geometry.
      const pad = 600;
      return {
        bboxMm: {
          minX: item.pt.x - pad,
          minY: item.pt.y - pad,
          maxX: item.pt.x + pad,
          maxY: item.pt.y + pad,
        },
        storeyId: item.storeyId,
      };
    }
    case 'column': {
      const column = house.columns.find((c) => c.id === elementId);
      if (column === undefined) return null;
      const hx = Math.ceil(column.sizeMm.xMm / 2);
      const hy = Math.ceil(column.sizeMm.yMm / 2);
      return {
        bboxMm: {
          minX: column.pt.x - hx,
          minY: column.pt.y - hy,
          maxX: column.pt.x + hx,
          maxY: column.pt.y + hy,
        },
        storeyId: column.storeyId,
      };
    }
    case 'storey': {
      // A storey-scoped rule (ceiling height, storey count) frames that whole
      // floor plate.
      const walls = house.walls.filter((w) => w.storeyId === elementId);
      if (walls.length === 0) return null;
      const pts: Pt[] = [];
      for (const w of walls) pts.push(w.a, w.b);
      return { bboxMm: bbox(pts), storeyId: elementId };
    }
    default:
      return null;
  }
}

/** Point at `alongMm` from `a` towards `b`. Float — a render coordinate. */
function pointAlongWall(a: Pt, b: Pt, alongMm: number): Pt {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return a;
  return { x: Math.round(a.x + (dx / len) * alongMm), y: Math.round(a.y + (dy / len) * alongMm) };
}

/**
 * The union footprint of an issue's elements.
 *
 * Ids that no longer resolve are dropped, not fatal: compliance is evaluated on
 * the SERVER's op log while the canvas draws the optimistic document, so for a
 * few hundred milliseconds after a delete a chip can legitimately name an
 * element this client has already removed. Dropping it keeps the rest of the
 * chip useful; treating it as an error would flash a failure at the user for
 * something that is about to resolve itself.
 */
export function focusFor(house: HouseModel, elementIds: readonly string[]): ComplianceFocus | null {
  let box: Bbox | null = null;
  const resolved: string[] = [];
  const storeys = new Set<string>();

  for (const id of elementIds) {
    const hit = elementBboxMm(house, id);
    if (hit === null) continue;
    resolved.push(id);
    if (hit.storeyId !== null) storeys.add(hit.storeyId);
    box =
      box === null
        ? hit.bboxMm
        : {
            minX: Math.min(box.minX, hit.bboxMm.minX),
            minY: Math.min(box.minY, hit.bboxMm.minY),
            maxX: Math.max(box.maxX, hit.bboxMm.maxX),
            maxY: Math.max(box.maxY, hit.bboxMm.maxY),
          };
  }

  if (box === null) return null;
  const only = storeys.size === 1 ? Array.from(storeys)[0] : undefined;
  return { storeyId: only ?? null, bboxMm: box, elementIds: resolved };
}

// ---------------------------------------------------------------------------
// Chips
// ---------------------------------------------------------------------------

/**
 * Map the debounced compliance report onto chips, resolved and sorted.
 *
 * Sort order is severity, then rule id, then the list key — total and stable,
 * so a chip does not hop position when an unrelated rule flips. React keys come
 * from `complianceIssueKey` (rule + elements) because `ruleId` alone is not
 * unique: `room_area_min` yields one result per room.
 */
export function mapComplianceChips(
  issues: readonly ComplianceIssueVM[],
  house: HouseModel,
  options: { readonly statuses?: readonly ComplianceResultStatus[] | undefined } = {},
): ComplianceChipVM[] {
  const allowed = new Set(options.statuses ?? DEFAULT_VISIBLE_STATUSES);

  const chips: ComplianceChipVM[] = [];
  issues.forEach((issue, index) => {
    if (!allowed.has(issue.status)) return;
    chips.push({
      key: `${complianceIssueKey(issue)}#${String(index)}`,
      ruleId: issue.ruleId,
      status: issue.status,
      message: issue.message,
      cite: issue.cite,
      confidence: issue.confidence,
      fixAvailable: issue.fixAvailable,
      fixHint: issue.fixHint,
      elementIds: issue.elementIds,
      focus: focusFor(house, issue.elementIds),
      rank: STATUS_RANK[issue.status],
    });
  });

  chips.sort(
    (a, b) =>
      a.rank - b.rank ||
      (a.ruleId < b.ruleId ? -1 : a.ruleId > b.ruleId ? 1 : 0) ||
      (a.key < b.key ? -1 : a.key > b.key ? 1 : 0),
  );
  return chips;
}

/** Counts for the strip header: "2 to fix · 1 to check". */
export function complianceCounts(issues: readonly ComplianceIssueVM[]): {
  fail: number;
  warn: number;
  pass: number;
} {
  let fail = 0;
  let warn = 0;
  let pass = 0;
  for (const issue of issues) {
    if (issue.status === 'fail') fail += 1;
    else if (issue.status === 'warn') warn += 1;
    else if (issue.status === 'pass') pass += 1;
  }
  return { fail, warn, pass };
}

// ---------------------------------------------------------------------------
// Markers
// ---------------------------------------------------------------------------

export interface ComplianceMarker {
  readonly key: string;
  readonly status: ComplianceResultStatus;
  /** Plan point the marker pin sits at, float mm (render coordinate). */
  readonly atMm: { readonly x: number; readonly y: number };
  readonly storeyId: string | null;
  readonly elementIds: readonly string[];
  readonly message: string;
}

/**
 * One marker per chip that resolved to geometry on the active storey.
 *
 * Storey-filtered here rather than in the renderer: a violation on the first
 * floor drawn over the ground-floor plan is an assertion that the ground floor
 * is wrong, which it is not.
 */
export function markersFor(
  chips: readonly ComplianceChipVM[],
  activeStoreyId: string | null,
): ComplianceMarker[] {
  const out: ComplianceMarker[] = [];
  for (const chip of chips) {
    const focus = chip.focus;
    if (focus === null) continue;
    if (focus.storeyId !== null && activeStoreyId !== null && focus.storeyId !== activeStoreyId) {
      continue;
    }
    out.push({
      key: chip.key,
      status: chip.status,
      atMm: {
        x: (focus.bboxMm.minX + focus.bboxMm.maxX) / 2,
        y: (focus.bboxMm.minY + focus.bboxMm.maxY) / 2,
      },
      storeyId: focus.storeyId,
      elementIds: focus.elementIds,
      message: chip.message,
    });
  }
  return out;
}

/**
 * Padding to leave around a focus box when zooming to it, as a fraction of the
 * box. A violation framed edge-to-edge gives no context about what it is next
 * to, which is usually the thing you have to change.
 */
export const FOCUS_PADDING_RATIO = 0.6;

/** The box to hand `viewport.fitBbox`, padded and never degenerate. */
export function focusFitBbox(focus: ComplianceFocus): Bbox {
  const w = focus.bboxMm.maxX - focus.bboxMm.minX;
  const h = focus.bboxMm.maxY - focus.bboxMm.minY;
  // A point-like focus (a column, a furniture item) has no extent to scale, so
  // it gets a fixed 2 m window instead of a zero-size box the camera cannot fit.
  const padX = Math.max(w * FOCUS_PADDING_RATIO, 1000);
  const padY = Math.max(h * FOCUS_PADDING_RATIO, 1000);
  return {
    minX: focus.bboxMm.minX - padX,
    minY: focus.bboxMm.minY - padY,
    maxX: focus.bboxMm.maxX + padX,
    maxY: focus.bboxMm.maxY + padY,
  };
}
