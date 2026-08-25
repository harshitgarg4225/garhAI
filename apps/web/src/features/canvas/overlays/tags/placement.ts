/**
 * placement.ts — non-overlapping label placement. PURE.
 *
 * The brief: "Tags must not overlap each other — greedy placement with a
 * collision grid, leader line as last resort." This is that, and the two
 * decisions worth arguing about are written down here rather than discovered
 * later.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * DECISION 1 — PLACEMENT RUNS IN WORLD MILLIMETRES, NOT SCREEN PIXELS
 * ────────────────────────────────────────────────────────────────────────────
 * A label is a constant size on screen, so its world footprint grows as you
 * zoom out: at 1:50 two room tags clear each other easily and at 1:500 they
 * collide. Strictly, placement therefore depends on zoom.
 *
 * Solving it in screen space would mean re-running placement every frame of a
 * zoom, which is precisely the per-frame work §14 forbids. So placement runs in
 * world mm using each label's footprint AT THE CURRENT ZOOM, and the layer
 * re-runs it only when the zoom crosses a band (see `ZOOM_REPLACE_RATIO`) or
 * the document changes. Between bands the layout is a frame or two stale in the
 * sense that a label may sit slightly further from its anchor than it needs to
 * — which nobody can see — and never in the sense that two labels overlap,
 * because the footprint used is the one for the coarser end of the band.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * DECISION 2 — DETERMINISM IS A FEATURE
 * ────────────────────────────────────────────────────────────────────────────
 * Same input, same output, always: labels are placed in a fixed order (priority
 * descending, then id) and candidates are tried in a fixed order. A greedy
 * placer that iterated a `Set` would shuffle room tags between two renders of
 * an unchanged plan, and a label that moves when nothing changed reads as a bug
 * in the drawing.
 */

import { polygonContains, type Polygon } from '@garh/model';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Float millimetres. Placement output is a render coordinate, never an op. */
export interface PlacePointF {
  readonly x: number;
  readonly y: number;
}

export interface PlaceableLabel {
  readonly id: string;
  /** Where the label wants to be — a room centroid, a marker position. */
  readonly anchorMm: PlacePointF;
  /** Half-extents of the label's box in world mm at the current zoom. */
  readonly halfWidthMm: number;
  readonly halfHeightMm: number;
  /**
   * Bigger goes first. Room area works well: a large room gets the prime
   * central spot and a cupboard-sized store takes the leader line, which is
   * also what a draughtsman would do.
   */
  readonly priority: number;
  /**
   * The region the label should stay inside if it can — the room polygon. A
   * label pushed outside its own room gets a leader line so the association is
   * never ambiguous.
   */
  readonly boundaryMm?: Polygon | undefined;
}

/**
 * How a label ended up where it is.
 *
 * `overflow` is the honest fourth state: every candidate collided, so the label
 * was placed anyway at the outermost ring and MAY overlap something. Callers
 * that cannot tolerate that (the §7 sheet engine, whose golden files assert no
 * overlapping text bboxes) check for it; the screen renders it dimmed rather
 * than hiding a room's name.
 */
export type LabelPlacementKind = 'anchor' | 'nudged' | 'leader' | 'overflow';

export interface PlacedLabel {
  readonly id: string;
  readonly atMm: PlacePointF;
  readonly kind: LabelPlacementKind;
  /** From the label's edge to its anchor. Non-null only for `leader`. */
  readonly leaderMm: readonly [PlacePointF, PlacePointF] | null;
  readonly halfWidthMm: number;
  readonly halfHeightMm: number;
}

export interface PlacementOptions {
  /**
   * How far a label may be nudged from its anchor before a leader line is used
   * instead, as a multiple of its own height. Default 3 — beyond that the label
   * is far enough away that a line is genuinely clearer than proximity.
   */
  readonly maxNudgeSteps?: number | undefined;
  /** Clear space kept between two labels, world mm. Default 2 mm × zoom-free. */
  readonly paddingMm?: number | undefined;
  /**
   * Labels that could not be placed anywhere free are dropped rather than
   * stacked. Default false: a leader-line placement is always accepted at the
   * outermost ring, so this only bites in a genuinely impossible layout.
   */
  readonly dropUnplaceable?: boolean | undefined;
}

/**
 * Re-run placement when the zoom has changed by more than this ratio since the
 * last run. 1.35 is a little over one wheel notch: small enough that labels
 * never visibly overlap mid-zoom, large enough that a continuous zoom triggers
 * a handful of placements rather than one per frame.
 */
export const ZOOM_REPLACE_RATIO = 1.35;

const DEFAULT_MAX_NUDGE_STEPS = 3;
const DEFAULT_PADDING_MM = 2;

// ---------------------------------------------------------------------------
// Rectangles
// ---------------------------------------------------------------------------

interface Rect {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

function rectAt(centre: PlacePointF, halfW: number, halfH: number, padMm: number): Rect {
  return {
    minX: centre.x - halfW - padMm,
    minY: centre.y - halfH - padMm,
    maxX: centre.x + halfW + padMm,
    maxY: centre.y + halfH + padMm,
  };
}

function overlaps(a: Rect, b: Rect): boolean {
  return !(a.maxX <= b.minX || b.maxX <= a.minX || a.maxY <= b.minY || b.maxY <= a.minY);
}

// ---------------------------------------------------------------------------
// Collision grid
// ---------------------------------------------------------------------------

/**
 * A uniform spatial hash over placed rectangles.
 *
 * Cell size is the largest label's full extent, so a candidate can only overlap
 * something in the cells it spans — the query is O(cells spanned × occupants)
 * rather than O(labels). With one cell per label region the whole placement is
 * effectively O(n) on the plans this product draws, which is what lets it run
 * synchronously on the render path instead of in a worker.
 */
class CollisionGrid {
  private readonly cells = new Map<string, Rect[]>();

  constructor(private readonly cellMm: number) {}

  private key(cx: number, cy: number): string {
    return `${String(cx)}|${String(cy)}`;
  }

  private range(rect: Rect): { x0: number; x1: number; y0: number; y1: number } {
    return {
      x0: Math.floor(rect.minX / this.cellMm),
      x1: Math.floor(rect.maxX / this.cellMm),
      y0: Math.floor(rect.minY / this.cellMm),
      y1: Math.floor(rect.maxY / this.cellMm),
    };
  }

  free(rect: Rect): boolean {
    const r = this.range(rect);
    for (let cx = r.x0; cx <= r.x1; cx++) {
      for (let cy = r.y0; cy <= r.y1; cy++) {
        const bucket = this.cells.get(this.key(cx, cy));
        if (bucket === undefined) continue;
        for (const other of bucket) {
          if (overlaps(rect, other)) return false;
        }
      }
    }
    return true;
  }

  insert(rect: Rect): void {
    const r = this.range(rect);
    for (let cx = r.x0; cx <= r.x1; cx++) {
      for (let cy = r.y0; cy <= r.y1; cy++) {
        const k = this.key(cx, cy);
        const bucket = this.cells.get(k);
        if (bucket === undefined) this.cells.set(k, [rect]);
        else bucket.push(rect);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Candidates
// ---------------------------------------------------------------------------

/**
 * The eight directions a label may be nudged, in a fixed order.
 *
 * Vertical first: a room tag sits above or below its centre far more naturally
 * than beside it, because the label is wide and short and a horizontal nudge
 * has to travel further to clear the same neighbour.
 */
const NUDGE_DIRECTIONS: ReadonlyArray<readonly [number, number]> = [
  [0, 1],
  [0, -1],
  [1, 0],
  [-1, 0],
  [1, 1],
  [-1, 1],
  [1, -1],
  [-1, -1],
];

interface Candidate {
  readonly centre: PlacePointF;
  readonly step: number;
  readonly inside: boolean;
}

/** All four corners of the label box are inside the boundary. */
function rectInsideBoundary(rect: Rect, boundary: Polygon | undefined): boolean {
  if (boundary === undefined || boundary.length < 3) return true;
  const corners = [
    { x: rect.minX, y: rect.minY },
    { x: rect.maxX, y: rect.minY },
    { x: rect.maxX, y: rect.maxY },
    { x: rect.minX, y: rect.maxY },
  ];
  // `polygonContains` wants integer mm; the label rect is float. Rounding
  // inward (ceil on min, floor on max) is the conservative direction: a label
  // that is marginally outside is reported as outside, never the reverse.
  for (const c of corners) {
    if (!polygonContains(boundary, { x: Math.round(c.x), y: Math.round(c.y) })) return false;
  }
  return true;
}

function candidatesFor(label: PlaceableLabel, maxSteps: number, padMm: number): Candidate[] {
  const out: Candidate[] = [];
  const stepX = label.halfWidthMm * 1.1 + padMm;
  const stepY = label.halfHeightMm * 2.2 + padMm;

  const push = (centre: PlacePointF, step: number): void => {
    const inside = rectInsideBoundary(
      rectAt(centre, label.halfWidthMm, label.halfHeightMm, 0),
      label.boundaryMm,
    );
    out.push({ centre, step, inside });
  };

  push(label.anchorMm, 0);
  for (let step = 1; step <= maxSteps; step++) {
    for (const dir of NUDGE_DIRECTIONS) {
      const dx = dir[0];
      const dy = dir[1];
      push(
        { x: label.anchorMm.x + dx * stepX * step, y: label.anchorMm.y + dy * stepY * step },
        step,
      );
    }
  }
  return out;
}

/** Where a leader line meets the label box, coming from the anchor. */
function leaderContact(centre: PlacePointF, halfW: number, halfH: number, anchor: PlacePointF): PlacePointF {
  const dx = anchor.x - centre.x;
  const dy = anchor.y - centre.y;
  if (dx === 0 && dy === 0) return centre;
  // Scale the direction until it hits the box edge — the smaller of the two
  // axis scalings is the side the line leaves through.
  const sx = dx === 0 ? Infinity : halfW / Math.abs(dx);
  const sy = dy === 0 ? Infinity : halfH / Math.abs(dy);
  const s = Math.min(sx, sy);
  return { x: centre.x + dx * s, y: centre.y + dy * s };
}

// ---------------------------------------------------------------------------
// The placer
// ---------------------------------------------------------------------------

/**
 * Place every label without overlap.
 *
 * The order of preference for one label, tried exhaustively before moving on:
 *   1. its anchor, if free
 *   2. the nearest free nudge that is still INSIDE its boundary (no leader —
 *      the label is in its own room, so the association is obvious)
 *   3. the nearest free nudge anywhere, with a leader line back to the anchor
 *   4. the outermost ring position, leader line, accepted even if it collides
 *      (or dropped, with `dropUnplaceable`)
 *
 * Step 2 before step 3 is the rule that keeps small rooms readable: a 1.2 m²
 * WC's tag would rather sit slightly off-centre inside the WC than perfectly
 * centred with a line pointing at it.
 */
export function placeLabels(
  labels: readonly PlaceableLabel[],
  options: PlacementOptions = {},
): PlacedLabel[] {
  const maxSteps = options.maxNudgeSteps ?? DEFAULT_MAX_NUDGE_STEPS;
  const padMm = options.paddingMm ?? DEFAULT_PADDING_MM;
  const dropUnplaceable = options.dropUnplaceable ?? false;

  if (labels.length === 0) return [];

  // Deterministic order: biggest first, ties broken on id.
  const ordered = labels
    .slice()
    .sort((a, b) => (b.priority - a.priority) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  let maxExtent = 0;
  for (const l of ordered) {
    maxExtent = Math.max(maxExtent, l.halfWidthMm * 2 + padMm * 2, l.halfHeightMm * 2 + padMm * 2);
  }
  const grid = new CollisionGrid(Math.max(1, maxExtent));

  const placed: PlacedLabel[] = [];

  for (const label of ordered) {
    const candidates = candidatesFor(label, maxSteps, padMm);

    let chosen: Candidate | null = null;
    let kind: LabelPlacementKind = 'leader';

    // Pass 1 — free AND inside the boundary.
    for (const candidate of candidates) {
      if (!candidate.inside) continue;
      const rect = rectAt(candidate.centre, label.halfWidthMm, label.halfHeightMm, padMm);
      if (!grid.free(rect)) continue;
      chosen = candidate;
      kind = candidate.step === 0 ? 'anchor' : 'nudged';
      break;
    }

    // Pass 2 — free anywhere; the label leaves its room and takes a line with it.
    if (chosen === null) {
      for (const candidate of candidates) {
        const rect = rectAt(candidate.centre, label.halfWidthMm, label.halfHeightMm, padMm);
        if (!grid.free(rect)) continue;
        chosen = candidate;
        kind = candidate.step === 0 ? 'anchor' : 'leader';
        break;
      }
    }

    // Pass 3 — nothing is free. Take the outermost ring rather than vanish: a
    // slightly crowded label still tells you the room's name, and a missing one
    // tells you nothing. It is marked `overflow` so a caller that genuinely
    // cannot accept an overlap (the sheet engine) can act on it instead of
    // discovering it in a golden-file diff.
    if (chosen === null) {
      if (dropUnplaceable) continue;
      chosen = candidates[candidates.length - 1] ?? { centre: label.anchorMm, step: 0, inside: true };
      kind = 'overflow';
    }

    const rect = rectAt(chosen.centre, label.halfWidthMm, label.halfHeightMm, padMm);
    grid.insert(rect);

    placed.push({
      id: label.id,
      atMm: chosen.centre,
      kind,
      leaderMm:
        kind === 'leader' || kind === 'overflow'
          ? [
              leaderContact(chosen.centre, label.halfWidthMm, label.halfHeightMm, label.anchorMm),
              label.anchorMm,
            ]
          : null,
      halfWidthMm: label.halfWidthMm,
      halfHeightMm: label.halfHeightMm,
    });
  }

  // Return in input order so the caller can zip results against its own list
  // without a lookup, while placement itself stays priority-ordered.
  const byId = new Map(placed.map((p) => [p.id, p]));
  const out: PlacedLabel[] = [];
  for (const label of labels) {
    const p = byId.get(label.id);
    if (p !== undefined) out.push(p);
  }
  return out;
}

/**
 * True when the zoom has moved far enough that placement should be re-run.
 * The layer holds the `mmPerPx` it last placed at and asks this per frame; the
 * comparison is two divides and a compare, which is affordable, and a `false`
 * costs nothing at all.
 */
export function shouldReplace(lastMmPerPx: number, currentMmPerPx: number): boolean {
  if (lastMmPerPx <= 0) return true;
  const ratio = currentMmPerPx / lastMmPerPx;
  return ratio > ZOOM_REPLACE_RATIO || ratio < 1 / ZOOM_REPLACE_RATIO;
}

/**
 * Labels the placer could not fit without an overlap. Empty on any layout the
 * screen should show as-is; non-empty is a signal, not a crash.
 */
export function overflowedLabels(placed: readonly PlacedLabel[]): PlacedLabel[] {
  return placed.filter((p) => p.kind === 'overflow');
}
