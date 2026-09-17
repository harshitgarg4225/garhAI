/**
 * Entering a plot the way an Indian sale deed states it.
 *
 * A registered sale deed almost never gives coordinates. It gives one of:
 *
 *   1. **Sides and a diagonal** — "East 40'-0", West 38'-6", North 30'-0",
 *      South 31'-0", diagonal 50'-2"". Four sides alone do not fix a
 *      quadrilateral; four sides plus one diagonal do (two triangles sharing
 *      the diagonal). For an N-gon it is N sides plus the N−3 diagonals from
 *      one corner — a fan of N−2 triangles, each solved by the cosine rule.
 *
 *   2. **A bearing/distance traverse** — the surveyor's field book, one leg per
 *      side: whole-circle bearing (clockwise from true north) and length. Legs
 *      are read off the deed and typed in; the ring is closed by construction
 *      only if every figure is exact, so the misclosure (how far the last leg
 *      misses the start) is REPORTED, and the ring is closed by the compass
 *      (Bowditch) rule — each vertex shifted in proportion to the distance
 *      travelled to reach it — the same adjustment a surveyor applies.
 *
 * Everything here is pure: lengths in, a polygon and a plain-language report
 * out. The construction uses floats (cosine rule, sin/cos of bearings) but
 * rounds ONCE, at the end, to integer millimetres (half away from zero), and
 * then re-measures the rounded ring so what is reported is what was stored —
 * never the request. No float ever reaches an op.
 */

import {
  distMm,
  polygonDoubledAreaMm2,
  roundHalfAwayFromZero,
  type Polygon,
  type Pt,
} from '@garh/model';

import { checkBoundary } from './geometry';

// ---------------------------------------------------------------------------
// Shared result vocabulary
// ---------------------------------------------------------------------------

export interface DiagonalMm {
  /** Zero-based corner indices on the RESULT ring. */
  readonly from: number;
  readonly to: number;
  readonly lengthMm: number;
}

/** A side as typed vs as it landed after rounding to whole millimetres. */
export interface SideCheck {
  readonly edgeIndex: number;
  readonly requestedMm: number;
  readonly achievedMm: number;
}

export type DeedResult =
  | {
      readonly ok: true;
      readonly polygon: Pt[];
      /** Every side, typed vs achieved. Differences are rounding, ≤ 1 mm per corner. */
      readonly sides: readonly SideCheck[];
      /** All the diagonals of the result ring, measured on the rounded integers. */
      readonly diagonals: readonly DiagonalMm[];
      readonly areaMm2: number;
      readonly perimeterMm: number;
    }
  | { readonly ok: false; readonly reason: string };

/** Every diagonal of a ring (corner pairs that are not an edge), in corner order. */
export function ringDiagonals(poly: Polygon): DiagonalMm[] {
  const out: DiagonalMm[] = [];
  const n = poly.length;
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 2; j < n; j += 1) {
      if (i === 0 && j === n - 1) continue; // that pair is an edge, not a diagonal
      const a = poly[i];
      const b = poly[j];
      if (a === undefined || b === undefined) continue;
      out.push({ from: i, to: j, lengthMm: distMm(a, b) });
    }
  }
  return out;
}

function perimeterOf(poly: Polygon): number {
  let total = 0;
  for (let i = 0; i < poly.length; i += 1) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    if (a !== undefined && b !== undefined) total += distMm(a, b);
  }
  return total;
}

/** Round every float vertex once, translate so the bbox minimum is the origin. */
function finishRing(float: readonly { x: number; y: number }[]): Pt[] {
  const rounded = float.map((p) => ({
    x: roundHalfAwayFromZero(p.x),
    y: roundHalfAwayFromZero(p.y),
  }));
  const minX = Math.min(...rounded.map((p) => p.x));
  const minY = Math.min(...rounded.map((p) => p.y));
  return rounded.map((p) => ({ x: p.x - minX, y: p.y - minY }));
}

function sidesReport(poly: Polygon, requested: readonly number[]): SideCheck[] {
  return requested.map((requestedMm, edgeIndex) => {
    const a = poly[edgeIndex];
    const b = poly[(edgeIndex + 1) % poly.length];
    return {
      edgeIndex,
      requestedMm,
      achievedMm: a === undefined || b === undefined ? 0 : distMm(a, b),
    };
  });
}

function finish(
  float: readonly { x: number; y: number }[],
  requestedSides: readonly number[],
): DeedResult {
  const polygon = finishRing(float);
  const check = checkBoundary(polygon);
  if (!check.ok) return { ok: false, reason: check.reason };
  return {
    ok: true,
    polygon,
    sides: sidesReport(polygon, requestedSides),
    diagonals: ringDiagonals(polygon),
    areaMm2: Math.abs(polygonDoubledAreaMm2(polygon)) / 2,
    perimeterMm: perimeterOf(polygon),
  };
}

function isPositiveIntMm(v: number): boolean {
  return Number.isSafeInteger(v) && v > 0;
}

/** Corner label the way a deed reads: A, B, C … */
export function cornerLabel(index: number): string {
  return String.fromCharCode(65 + (index % 26));
}

// ---------------------------------------------------------------------------
// 1. Sides + diagonals
// ---------------------------------------------------------------------------

/**
 * Build the ring from N side lengths (in corner order A→B→C→…) and the N−3
 * diagonals from corner A to corners C, D, … (A–C, A–D, …). For a
 * quadrilateral that is four sides and the single diagonal A–C.
 *
 * Corner A lands at the origin with side A–B along +X (so the first side you
 * type is the bottom edge, left to right — mark it as the road afterwards if
 * that is what it is). Corners sweep counter-clockwise around A, which makes
 * the ring CCW as the model requires. A corner that bends INWARD at A cannot
 * be described this way; the bearing traverse can.
 *
 * Refuses, naming the triangle, when any triangle of the fan violates the
 * triangle inequality — the deed's figures are inconsistent, and guessing
 * which one is wrong is not this function's job.
 */
export function ringFromSidesAndDiagonals(
  sidesMm: readonly number[],
  diagonalsMm: readonly number[],
): DeedResult {
  const n = sidesMm.length;
  if (n < 3) return { ok: false, reason: 'A plot needs at least three sides.' };
  if (n > 12) return { ok: false, reason: 'Up to twelve sides can be entered this way.' };
  if (diagonalsMm.length !== n - 3) {
    return {
      ok: false,
      reason: `${String(n)} sides need ${String(n - 3)} diagonal${n - 3 === 1 ? '' : 's'} from corner A (A–C${n > 4 ? ', A–D…' : ''}).`,
    };
  }
  for (const [i, s] of sidesMm.entries()) {
    if (!isPositiveIntMm(s)) {
      return {
        ok: false,
        reason: `Side ${cornerLabel(i)}–${cornerLabel(i + 1)} needs a positive length.`,
      };
    }
  }
  for (const [i, d] of diagonalsMm.entries()) {
    if (!isPositiveIntMm(d)) {
      return { ok: false, reason: `Diagonal A–${cornerLabel(i + 2)} needs a positive length.` };
    }
  }

  // Distances from A to every corner: d[1] = side AB, d[n-1] = side (last)A,
  // the rest are the typed diagonals.
  const fromA: number[] = new Array<number>(n).fill(0);
  fromA[1] = sidesMm[0] ?? 0;
  for (let k = 2; k <= n - 2; k += 1) fromA[k] = diagonalsMm[k - 2] ?? 0;
  fromA[n - 1] = sidesMm[n - 1] ?? 0;

  const verts: { x: number; y: number }[] = [
    { x: 0, y: 0 },
    { x: fromA[1] ?? 0, y: 0 },
  ];
  let sweep = 0; // running angle at A, radians, from the +X axis
  for (let i = 1; i <= n - 2; i += 1) {
    const dPrev = fromA[i] ?? 0; // A → corner i
    const dNext = fromA[i + 1] ?? 0; // A → corner i+1
    const side = sidesMm[i] ?? 0; // corner i → corner i+1
    const longest = Math.max(dPrev, dNext, side);
    if (2 * longest >= dPrev + dNext + side) {
      const tri = `A–${cornerLabel(i)}–${cornerLabel(i + 1)}`;
      return {
        ok: false,
        reason:
          `Those figures cannot close triangle ${tri}: ` +
          `side ${cornerLabel(i)}–${cornerLabel(i + 1)} and the two distances from A ` +
          `(${String(dPrev)} mm, ${String(dNext)} mm, ${String(side)} mm) break the triangle rule. ` +
          'Check the deed for a mis-read digit.',
      };
    }
    // Cosine rule for the angle at A between A→i and A→i+1.
    const cosA = (dPrev * dPrev + dNext * dNext - side * side) / (2 * dPrev * dNext);
    sweep += Math.acos(Math.max(-1, Math.min(1, cosA)));
    verts.push({ x: dNext * Math.cos(sweep), y: dNext * Math.sin(sweep) });
  }
  if (sweep >= 2 * Math.PI) {
    return {
      ok: false,
      reason: 'The corners sweep more than a full turn around corner A — a diagonal is too short.',
    };
  }
  return finish(verts, sidesMm);
}

// ---------------------------------------------------------------------------
// 2. Bearing / distance traverse
// ---------------------------------------------------------------------------

export interface TraverseLeg {
  /** Whole-circle bearing, degrees clockwise from true north. Any real number. */
  readonly bearingDeg: number;
  readonly lengthMm: number;
}

export interface TraverseClosure {
  /** How far the last leg missed the starting corner, before adjustment. */
  readonly misclosureMm: number;
  /** Surveyor's precision: "1 in N" (perimeter ÷ misclosure). null when it closed exactly. */
  readonly precisionDenominator: number | null;
  /** True when the ring was closed by the compass rule (any misclosure > 0). */
  readonly adjusted: boolean;
  /** True when the typed legs ran clockwise and were reversed to the model's CCW. */
  readonly reversed: boolean;
}

export type TraverseResult =
  | (Extract<DeedResult, { ok: true }> & { readonly closure: TraverseClosure })
  | { readonly ok: false; readonly reason: string; readonly closure?: TraverseClosure };

/**
 * Coarsest closure this function will adjust away without complaint: a
 * misclosure beyond one part in fifty of the perimeter almost always means a
 * mistyped leg, and spreading a metre of error around the ring would hide it.
 */
export const TRAVERSE_MAX_MISCLOSURE_RATIO = 1 / 50;

/**
 * Walk the legs from the origin with north up (+Y), report the misclosure,
 * close the ring by the Bowditch rule, and hand back a CCW integer ring with
 * its bbox at the origin. The ring is built in a TRUE-NORTH-UP frame, so the
 * caller should also set `plot.northDeg` to 0.
 */
export function ringFromTraverse(legs: readonly TraverseLeg[]): TraverseResult {
  if (legs.length < 3) return { ok: false, reason: 'A traverse needs at least three legs.' };
  if (legs.length > 24) return { ok: false, reason: 'Up to twenty-four legs can be entered.' };
  for (const [i, leg] of legs.entries()) {
    if (!isPositiveIntMm(leg.lengthMm)) {
      return { ok: false, reason: `Leg ${String(i + 1)} needs a positive length.` };
    }
    if (!Number.isFinite(leg.bearingDeg)) {
      return { ok: false, reason: `Leg ${String(i + 1)} needs a bearing.` };
    }
  }

  const raw: { x: number; y: number }[] = [{ x: 0, y: 0 }];
  const cumulative: number[] = [0];
  let perimeter = 0;
  for (const leg of legs) {
    const rad = (leg.bearingDeg * Math.PI) / 180;
    const prev = raw[raw.length - 1] ?? { x: 0, y: 0 };
    raw.push({
      x: prev.x + leg.lengthMm * Math.sin(rad),
      y: prev.y + leg.lengthMm * Math.cos(rad),
    });
    perimeter += leg.lengthMm;
    cumulative.push(perimeter);
  }
  const end = raw[raw.length - 1] ?? { x: 0, y: 0 };
  const misclosureExact = Math.hypot(end.x, end.y);
  const misclosureMm = roundHalfAwayFromZero(misclosureExact);
  const precisionDenominator =
    misclosureMm === 0 ? null : Math.max(1, Math.round(perimeter / misclosureExact));

  const closureBase = { misclosureMm, precisionDenominator, adjusted: misclosureMm > 0 };

  if (misclosureExact > perimeter * TRAVERSE_MAX_MISCLOSURE_RATIO) {
    return {
      ok: false,
      reason:
        `The traverse misses its starting corner by ${String(misclosureMm)} mm ` +
        `(1 in ${String(precisionDenominator ?? 0)}) — too far to distribute. ` +
        'A bearing or a length is probably mis-read; check each leg against the deed.',
      closure: { ...closureBase, reversed: false },
    };
  }

  // Bowditch / compass rule: shift each vertex by the misclosure in proportion
  // to the distance walked to reach it. The last vertex (= the start) closes.
  const adjusted = raw.slice(0, -1).map((p, k) => {
    const share = perimeter === 0 ? 0 : (cumulative[k] ?? 0) / perimeter;
    return { x: p.x - end.x * share, y: p.y - end.y * share };
  });

  // Deeds are read either way round; the model stores CCW.
  const twiceArea = adjusted.reduce((sum, p, i) => {
    const q = adjusted[(i + 1) % adjusted.length] ?? p;
    return sum + (p.x * q.y - q.x * p.y);
  }, 0);
  const reversed = twiceArea < 0;
  const ordered = reversed
    ? [adjusted[0] ?? { x: 0, y: 0 }, ...adjusted.slice(1).reverse()]
    : adjusted;
  const requestedSides = reversed
    ? [
        legs[legs.length - 1]?.lengthMm ?? 0,
        ...legs
          .slice(0, -1)
          .map((l) => l.lengthMm)
          .reverse(),
      ]
    : legs.map((l) => l.lengthMm);

  const result = finish(ordered, requestedSides);
  if (!result.ok)
    return { ok: false, reason: result.reason, closure: { ...closureBase, reversed } };
  return { ...result, closure: { ...closureBase, reversed } };
}

// ---------------------------------------------------------------------------
// Bearings as a surveyor writes them
// ---------------------------------------------------------------------------

/**
 * Parse a bearing. Accepts whole-circle decimal ("135", "135.5", "135°"),
 * degrees-minutes-seconds ("135°30'", "135°30'15\"", "135 30 15", "135d30m15s",
 * "135-30-15") and quadrant bearings ("N45°30'E", "S 12 W"). Returns degrees
 * clockwise from north in [0, 360), or null when the text is not a bearing.
 */
export function parseBearingDeg(raw: string): number | null {
  let s = raw
    .trim()
    .toUpperCase()
    .replace(/DEG(REES?)?/g, '°')
    .replace(/[º˚]/g, '°')
    .replace(/[′’]/g, "'")
    .replace(/[″”]/g, '"')
    .replace(/MIN(UTES?)?/g, "'")
    .replace(/SEC(ONDS?)?/g, '"')
    .replace(/D(?=\s*\d|\s*$)/g, '°')
    .replace(/M(?=\s*\d|\s*$)/g, "'")
    .replace(/S(?=\s*$)/g, '"');
  if (s === '') return null;

  let quadrant: { ns: 'N' | 'S'; ew: 'E' | 'W' } | null = null;
  const quad = /^([NS])\s*(.*?)\s*([EW])$/.exec(s);
  if (quad !== null) {
    quadrant = { ns: quad[1] as 'N' | 'S', ew: quad[3] as 'E' | 'W' };
    s = quad[2] ?? '';
  }

  const parts = s
    .replace(/°|'|"|-/g, ' ')
    .trim()
    .split(/\s+/)
    .filter((p) => p !== '');
  if (parts.length === 0 || parts.length > 3) return null;
  const nums = parts.map((p) => (/^\d+(?:\.\d+)?$/.test(p) ? Number(p) : NaN));
  if (nums.some((v) => Number.isNaN(v))) return null;
  const [d = 0, m = 0, sec = 0] = nums;
  if (parts.length > 1 && (m >= 60 || sec >= 60)) return null;
  let deg = d + m / 60 + sec / 3600;

  if (quadrant !== null) {
    if (deg > 90) return null;
    if (quadrant.ns === 'S') deg = quadrant.ew === 'E' ? 180 - deg : 180 + deg;
    else if (quadrant.ew === 'W') deg = 360 - deg;
  }
  deg = ((deg % 360) + 360) % 360;
  return deg;
}

/** `135°30'15"` — degrees, minutes, seconds (seconds omitted when zero). */
export function formatBearingDms(deg: number): string {
  const total = Math.round((((deg % 360) + 360) % 360) * 3600) % (360 * 3600);
  const d = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return s === 0
    ? `${String(d)}°${String(m).padStart(2, '0')}'`
    : `${String(d)}°${String(m).padStart(2, '0')}'${String(s).padStart(2, '0')}"`;
}

/**
 * The whole-circle bearing of edge i of an existing ring, in a north-up frame
 * corrected for the plot's `northDeg` — what a deed would say for that side.
 * Float, display only.
 */
export function edgeBearingDeg(poly: Polygon, edgeIndex: number, northDeg: number): number | null {
  const n = poly.length;
  if (n < 2) return null;
  const a = poly[((edgeIndex % n) + n) % n];
  const b = poly[(((edgeIndex + 1) % n) + n) % n];
  if (a === undefined || b === undefined) return null;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return null;
  const cwFromPlusY = (Math.atan2(dx, dy) * 180) / Math.PI;
  return (((cwFromPlusY - northDeg) % 360) + 360) % 360;
}
