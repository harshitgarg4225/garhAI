/**
 * stairSymbol.ts — a stair in plan, derived from the model's ONE flight.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THE MODEL STORES, AND WHAT IS DERIVED HERE
 * ════════════════════════════════════════════════════════════════════════════
 * A `Stair` is an origin, a direction of travel, one riser/tread pair, one
 * clear width, a riser COUNT and an optional landing block (§3). It does not
 * store a second flight. `stairFootprintPolygon` in `fold.ts` — the slab well
 * — already splits the count in two for every turning kind
 * (`ceil(risersCount / 2)` risers before the landing, the rest after), and the
 * flight solver in `stairFlight.ts` sizes the landing the same way. So the
 * second flight is not invented here; it is the remainder of the count, at the
 * same riser and tread, on the far side of the landing the model already
 * describes. This module writes that down as geometry, and this header says so
 * because an earlier version drew every kind as one straight run rather than
 * put a shape on a municipal drawing the model did not contain.
 *
 * The derivation, per kind (all in the stair's own frame — `forward` is the
 * direction of travel, `right` is 90° clockwise from it, exactly the frame
 * `stairFootprintPolygon` builds its rectangle in):
 *
 *   straight   one flight of `risersCount` risers, `[0, going] × [0, width]`.
 *
 *   dogleg, U  flight 1 up the left, `ceil(n/2)` risers; the landing across the
 *              whole footprint at the far end; flight 2 back DOWN the right
 *              side, antiparallel, with the remaining `n − ceil(n/2)` risers and
 *              its top riser landing on the floor above. The well between them
 *              is `landing.widthMm − 2·width` (115 mm from the solver). The
 *              model does not distinguish a U from a dogleg beyond the kind
 *              name — both carry the same landing block — so they draw alike.
 *
 *   L          flight 1 as above, a square landing at the far end, and the
 *              return flight turning RIGHT along the landing's far edge. The
 *              model has no `turn` field, so the hand is a convention — the same
 *              one `stairFootprintPolygon` implies by growing the footprint to
 *              the right of the first flight. The slab well the fold cuts stops
 *              `landing.widthMm` into the return flight (the footprint is
 *              `2·width` wide); a return flight longer than that is drawn in full
 *              here and extends past the well, which is a model limit stated
 *              rather than hidden by truncating the flight.
 *
 * Every coordinate is integer millimetres: direction vectors are 0/±1 and the
 * only divisions are halves floored, so nothing here rounds.
 *
 * NO REACT, NO THREE. The stair tool's preview and `PlanScene`'s drawing both
 * call this, which is what makes the preview honest: what you see before the
 * click is what the plan draws after it.
 */

import { bbox, stairFootprintPolygon, type Direction4, type Pt, type Stair } from '@garh/model';

/** One flight, in world integer mm — mostly for the specs. */
export interface StairFlightGeometry {
  /** Bottom-left corner of the flight in its own travel frame. */
  readonly origin: Pt;
  readonly direction: Direction4;
  readonly risers: number;
  /** Horizontal run, `(risers − 1) × tread`. */
  readonly goingMm: number;
  readonly widthMm: number;
}

export interface StairSymbol {
  readonly stairId: string;
  /** Pick / outline ring: the bounding box of everything drawn. Integer mm. */
  readonly ringMm: readonly Pt[];
  /** The slab well the fold cuts — `stairFootprintPolygon`, unchanged. */
  readonly footprintMm: readonly Pt[];
  /** One line per riser across each flight, in travel order. */
  readonly treads: readonly (readonly [Pt, Pt])[];
  /** Landing edges and the well line between antiparallel flights. */
  readonly edges: readonly (readonly [Pt, Pt])[];
  /** UP arrow, as a polyline through every flight, tail first. */
  readonly arrow: readonly Pt[];
  /** Two barbs at the arrow's head. */
  readonly arrowHead: readonly (readonly [Pt, Pt])[];
  readonly flights: readonly StairFlightGeometry[];
}

/** Forward = travel; right = 90° clockwise from forward. Mirrors `fold.ts`. */
const FRAME: Readonly<Record<Direction4, { fx: number; fy: number; rx: number; ry: number }>> = {
  N: { fx: 0, fy: 1, rx: 1, ry: 0 },
  E: { fx: 1, fy: 0, rx: 0, ry: -1 },
  S: { fx: 0, fy: -1, rx: -1, ry: 0 },
  W: { fx: -1, fy: 0, rx: 0, ry: 1 },
};

/** The direction a `right` turn from `d` faces, and the one opposite `d`. */
const TURN_RIGHT: Readonly<Record<Direction4, Direction4>> = { N: 'E', E: 'S', S: 'W', W: 'N' };
const OPPOSITE: Readonly<Record<Direction4, Direction4>> = { N: 'S', E: 'W', S: 'N', W: 'E' };

/** Risers in the first flight, split exactly as `stairFootprintPolygon` does. */
export function risersBeforeLanding(stair: Pick<Stair, 'kind' | 'risersCount'>): number {
  return stair.kind === 'straight' ? stair.risersCount : Math.ceil(stair.risersCount / 2);
}

/** Horizontal run of a flight of `risers` risers: one tread fewer than risers. */
function goingOf(risers: number, treadMm: number): number {
  return Math.max(1, risers - 1) * treadMm;
}

function half(value: number): number {
  return Math.floor(value / 2);
}

export function stairSymbol(stair: Stair): StairSymbol {
  const v = FRAME[stair.direction];
  const { x, y } = stair.origin;
  const at = (alongMm: number, acrossMm: number): Pt => ({
    x: x + v.fx * alongMm + v.rx * acrossMm,
    y: y + v.fy * alongMm + v.ry * acrossMm,
  });

  const w = stair.widthMm;
  const tread = stair.treadMm;
  const n = stair.risersCount;
  const r1 = risersBeforeLanding(stair);
  const r2 = n - r1;
  const going1 = goingOf(r1, tread);

  const treads: (readonly [Pt, Pt])[] = [];
  const edges: (readonly [Pt, Pt])[] = [];
  const arrow: Pt[] = [];
  const flights: StairFlightGeometry[] = [];
  const drawn: Pt[] = [];

  // ── flight 1: `[0, going1] × [0, w]` in every kind ───────────────────────
  for (let i = 1; i < r1; i += 1) {
    treads.push([at(i * tread, 0), at(i * tread, w)] as const);
  }
  flights.push({
    origin: stair.origin,
    direction: stair.direction,
    risers: r1,
    goingMm: going1,
    widthMm: w,
  });
  drawn.push(at(0, 0), at(going1, w));
  arrow.push(at(half(tread), half(w)));

  if (stair.kind === 'straight') {
    arrow.push(at(Math.max(tread, going1 - half(tread)), half(w)));
  } else if (stair.kind === 'L') {
    // Square landing at the far end, then the return flight turning right.
    const landingDepth = stair.landing?.depthMm ?? w;
    const landingWidth = stair.landing?.widthMm ?? w;
    const going2 = r2 > 0 ? goingOf(r2, tread) : 0;
    edges.push([at(going1, 0), at(going1, landingWidth)] as const);
    edges.push([at(going1, landingWidth), at(going1 + landingDepth, landingWidth)] as const);
    drawn.push(at(going1 + landingDepth, landingWidth + going2));
    for (let k = 1; k < r2; k += 1) {
      const across = landingWidth + k * tread;
      treads.push([at(going1, across), at(going1 + landingDepth, across)] as const);
    }
    if (r2 > 0) {
      flights.push({
        origin: at(going1, landingWidth),
        direction: TURN_RIGHT[stair.direction],
        risers: r2,
        goingMm: going2,
        widthMm: landingDepth,
      });
      const mid = going1 + half(landingDepth);
      arrow.push(at(mid, half(w)));
      arrow.push(at(mid, landingWidth + going2 - half(tread)));
    } else {
      arrow.push(at(going1 + half(landingDepth), half(w)));
    }
  } else {
    // dogleg / U: the landing spans the whole width; flight 2 comes back.
    const landingDepth = stair.landing?.depthMm ?? w;
    const landingWidth = stair.landing?.widthMm ?? 2 * w + 100;
    const going2 = r2 > 0 ? goingOf(r2, tread) : 0;
    edges.push([at(going1, 0), at(going1, landingWidth)] as const);
    // The two flight edges either side of the well.
    edges.push([at(0, w), at(going1, w)] as const);
    edges.push([at(going1 - going2, landingWidth - w), at(going1, landingWidth - w)] as const);
    drawn.push(at(going1 + landingDepth, landingWidth));
    for (let k = 1; k < r2; k += 1) {
      const along = going1 - k * tread;
      treads.push([at(along, landingWidth - w), at(along, landingWidth)] as const);
    }
    if (r2 > 0) {
      flights.push({
        origin: at(going1, landingWidth),
        direction: OPPOSITE[stair.direction],
        risers: r2,
        goingMm: going2,
        widthMm: w,
      });
      const mid = going1 + half(landingDepth);
      const across2 = landingWidth - half(w);
      arrow.push(at(mid, half(w)));
      arrow.push(at(mid, across2));
      arrow.push(at(going1 - going2 + half(tread), across2));
    } else {
      arrow.push(at(going1 + half(landingDepth), half(w)));
    }
  }

  const footprintMm = stairFootprintPolygon(stair);
  drawn.push(...footprintMm);
  const box = bbox(drawn);
  const ringMm: Pt[] = [
    { x: box.minX, y: box.minY },
    { x: box.maxX, y: box.minY },
    { x: box.maxX, y: box.maxY },
    { x: box.minX, y: box.maxY },
  ];

  return {
    stairId: stair.id,
    ringMm,
    footprintMm,
    treads,
    edges,
    arrow,
    arrowHead: arrowHeadOf(arrow, half(tread)),
    flights,
  };
}

/**
 * Two barbs at the head of a polyline, angled 45° back along its last leg.
 *
 * The last leg is always axial (every point above is `at(…)` of an axial
 * frame), so the barbs are exact integer offsets — no trigonometry.
 */
function arrowHeadOf(arrow: readonly Pt[], sizeMm: number): (readonly [Pt, Pt])[] {
  const head = arrow[arrow.length - 1];
  const prev = arrow[arrow.length - 2];
  if (head === undefined || prev === undefined) return [];
  const dx = Math.sign(head.x - prev.x);
  const dy = Math.sign(head.y - prev.y);
  if (dx === 0 && dy === 0) return [];
  const size = Math.max(1, sizeMm);
  // Perpendicular to the last leg.
  const px = -dy;
  const py = dx;
  return [
    [head, { x: head.x - dx * size + px * size, y: head.y - dy * size + py * size }] as const,
    [head, { x: head.x - dx * size - px * size, y: head.y - dy * size - py * size }] as const,
  ];
}
