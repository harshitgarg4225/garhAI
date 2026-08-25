/**
 * rooms.ts — planar-subdivision room detection with ID PRESERVATION.
 *
 * The playbook calls this load-bearing (§3), and it is: annotations anchor to
 * room ids, room locks drive solver partial re-solve, the copilot refers to
 * rooms by id, and the drawing set labels them. If a room id changes when a wall
 * moves 100mm, an architect loses their annotations. So:
 *
 *   1. Build a planar straight-line graph from the wall CENTRELINES for one
 *      storey: split every wall at every intersection and T-junction.
 *   2. Walk half-edges into faces. Bounded faces (positive area, interior on the
 *      left) are room candidates; the biggest negative-area face is the outside.
 *   3. Inset each face by HALF THE THICKNESS of the wall on each edge, so the
 *      room polygon is the CLEAR (inside-face) area — the number NBC checks.
 *   4. Match every new face to the previous room set by maximum Jaccard overlap,
 *      greedily, one-to-one. A matched face KEEPS the old room's id, type, name,
 *      tags, lock and targets. An unmatched face becomes a new room with a
 *      DETERMINISTIC id (derived from storey + polygon), so `replay(ops)` is
 *      reproducible. A room id dies only when no face matches it at all.
 *
 * ANGLE ORDERING IS EXACT (`compareAngleAround` uses integer cross products), so
 * face traversal has no floating-point input and the Python mirror produces the
 * same faces.
 *
 * KNOWN MVP LIMITS (documented, not hidden):
 *   - Holes are ignored: a free-standing island of walls inside a room does not
 *     punch a hole in that room's polygon.
 *   - Two non-orthogonal walls whose intersection is not an integer mm point are
 *     not split at that crossing (the rounded point would not lie exactly on
 *     either centreline). MVP walls are orthogonal (§7), so this cannot happen
 *     in practice; `nonIntegralCrossings` reports it if it ever does.
 */

import {
  bbox,
  canonicalRing,
  comparePt,
  compareAngleAround,
  cross,
  jaccard,
  offsetPolygon,
  polygonAreaMm2,
  polygonDoubledAreaMm2,
  polygonIsClosedRing,
  polygonIntersectionAreaMm2,
  polygonKey,
  pointOnSegment,
  ptEq,
  ptKey,
  reversePolygon,
  segmentIntersection,
} from './geometry';
import type { Polygon, Pt, Seg } from './geometry';
import { derivedIdUnique } from './ids';
import type { RoomId, StoreyId, WallId } from './ids';
import type { Room, Wall } from './model';
import { MIN_ROOM_AREA_MM2 } from './validate';

// ---------------------------------------------------------------------------
// Half-edge graph
// ---------------------------------------------------------------------------

/** Minimal wall input — anything with a centreline and a thickness works. */
export interface WallLike {
  readonly id: WallId;
  readonly a: Pt;
  readonly b: Pt;
  readonly thicknessMm: number;
}

export interface HalfEdge {
  /** Index into `HalfEdgeGraph.halfEdges`. */
  readonly id: number;
  /** Node index of the origin. */
  readonly from: number;
  /** Node index of the destination. */
  readonly to: number;
  /** Index of the opposite half-edge. */
  readonly twin: number;
  /** Wall this half-edge came from. */
  readonly wallId: WallId;
  readonly thicknessMm: number;
  /** Next half-edge of the same face (filled by `linkFaces`). */
  next: number;
  /** Face index (filled by `planarFaces`). */
  face: number;
}

export interface HalfEdgeGraph {
  readonly nodes: readonly Pt[];
  readonly halfEdges: readonly HalfEdge[];
  /** Outgoing half-edge ids per node, sorted counter-clockwise from +X. */
  readonly outgoing: readonly (readonly number[])[];
  /** Count of intersections that could not be split exactly (see module docs). */
  readonly nonIntegralCrossings: number;
}

/**
 * Build the planar graph for ONE storey's walls. Overlapping duplicate edges are
 * collapsed to a single edge carrying the thickest wall.
 */
export function buildHalfEdgeGraph(walls: readonly WallLike[]): HalfEdgeGraph {
  const segs: (WallLike & { seg: Seg })[] = [];
  for (const w of walls) {
    if (ptEq(w.a, w.b)) continue;
    segs.push({ ...w, seg: { a: w.a, b: w.b } });
  }

  // --- 1. split points per segment
  let nonIntegralCrossings = 0;
  const splitPoints: Pt[][] = segs.map((s) => [s.seg.a, s.seg.b]);
  for (let i = 0; i < segs.length; i++) {
    const si = segs[i]!;
    for (let j = i + 1; j < segs.length; j++) {
      const sj = segs[j]!;
      const r = segmentIntersection(si.seg, sj.seg);
      if (r.kind === 'point') {
        if (!r.exact) nonIntegralCrossings += 1;
        if (pointOnSegment(r.point, si.seg)) splitPoints[i]!.push(r.point);
        if (pointOnSegment(r.point, sj.seg)) splitPoints[j]!.push(r.point);
      } else if (r.kind === 'collinear') {
        for (const p of [r.overlap.a, r.overlap.b]) {
          if (pointOnSegment(p, si.seg)) splitPoints[i]!.push(p);
          if (pointOnSegment(p, sj.seg)) splitPoints[j]!.push(p);
        }
      }
    }
  }

  // --- 2. nodes
  const nodeIndex = new Map<string, number>();
  const nodes: Pt[] = [];
  const nodeOf = (p: Pt): number => {
    const k = ptKey(p);
    const existing = nodeIndex.get(k);
    if (existing !== undefined) return existing;
    const idx = nodes.length;
    nodes.push(p);
    nodeIndex.set(k, idx);
    return idx;
  };

  // --- 3. edges: consecutive split points along each segment
  interface EdgeRec {
    u: number;
    v: number;
    wallId: WallId;
    thicknessMm: number;
  }
  const edgeByKey = new Map<string, EdgeRec>();
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i]!;
    const ordered = orderAlong(s.seg, splitPoints[i]!);
    for (let k = 0; k + 1 < ordered.length; k++) {
      const u = nodeOf(ordered[k]!);
      const v = nodeOf(ordered[k + 1]!);
      if (u === v) continue;
      const key = u < v ? `${String(u)}-${String(v)}` : `${String(v)}-${String(u)}`;
      const prev = edgeByKey.get(key);
      if (prev === undefined) {
        edgeByKey.set(key, { u, v, wallId: s.id, thicknessMm: s.thicknessMm });
      } else if (s.thicknessMm > prev.thicknessMm) {
        edgeByKey.set(key, { u, v, wallId: s.id, thicknessMm: s.thicknessMm });
      }
    }
  }

  // --- 4. half-edges
  const halfEdges: HalfEdge[] = [];
  for (const e of edgeByKey.values()) {
    const idA = halfEdges.length;
    const idB = idA + 1;
    halfEdges.push({
      id: idA,
      from: e.u,
      to: e.v,
      twin: idB,
      wallId: e.wallId,
      thicknessMm: e.thicknessMm,
      next: -1,
      face: -1,
    });
    halfEdges.push({
      id: idB,
      from: e.v,
      to: e.u,
      twin: idA,
      wallId: e.wallId,
      thicknessMm: e.thicknessMm,
      next: -1,
      face: -1,
    });
  }

  // --- 5. CCW-sorted outgoing lists
  const outgoing: number[][] = nodes.map(() => []);
  for (const he of halfEdges) outgoing[he.from]!.push(he.id);
  for (let n = 0; n < outgoing.length; n++) {
    const origin = nodes[n]!;
    outgoing[n]!.sort((x, y) => {
      const c = compareAngleAround(origin, nodes[halfEdges[x]!.to]!, nodes[halfEdges[y]!.to]!);
      return c !== 0 ? c : x - y;
    });
  }

  // --- 6. next pointers: at the destination, take the half-edge immediately
  // CLOCKWISE from the twin. This walks bounded faces counter-clockwise, i.e.
  // with the face interior on the left. (Verified on a unit square in the tests.)
  const positionInOutgoing = new Map<number, number>();
  for (let n = 0; n < outgoing.length; n++) {
    outgoing[n]!.forEach((heId, pos) => positionInOutgoing.set(heId, pos));
  }
  for (const he of halfEdges) {
    const list = outgoing[he.to];
    const twinPos = positionInOutgoing.get(he.twin);
    if (list === undefined || twinPos === undefined || list.length === 0) {
      he.next = he.twin;
      continue;
    }
    he.next = list[(twinPos - 1 + list.length) % list.length]!;
  }

  return { nodes, halfEdges, outgoing, nonIntegralCrossings };
}

/** Order points along a segment (dominant axis, from `a` to `b`), deduped. */
function orderAlong(seg: Seg, points: readonly Pt[]): Pt[] {
  const dx = seg.b.x - seg.a.x;
  const dy = seg.b.y - seg.a.y;
  const useX = Math.abs(dx) >= Math.abs(dy);
  const dir = useX ? Math.sign(dx) : Math.sign(dy);
  const seen = new Set<string>();
  const unique: Pt[] = [];
  for (const p of points) {
    const k = ptKey(p);
    if (seen.has(k)) continue;
    seen.add(k);
    unique.push(p);
  }
  unique.sort((p, q) => {
    const pv = useX ? p.x : p.y;
    const qv = useX ? q.x : q.y;
    if (pv !== qv) return dir >= 0 ? pv - qv : qv - pv;
    return comparePt(p, q);
  });
  return unique;
}

// ---------------------------------------------------------------------------
// Faces
// ---------------------------------------------------------------------------

export interface PlanarFace {
  readonly index: number;
  /** Half-edge ids in traversal order. */
  readonly halfEdgeIds: readonly number[];
  /** Ring vertices (one per half-edge origin), before any inset. */
  readonly ring: Polygon;
  /** Per-edge inset distance = half the thickness of the wall on that edge. */
  readonly insetMm: readonly number[];
  /** EXACT signed doubled area: positive = bounded interior face. */
  readonly doubledAreaMm2: number;
  readonly wallIds: readonly WallId[];
}

/** Walk every face of the graph. Bounded faces come back with positive area. */
export function planarFaces(graph: HalfEdgeGraph): PlanarFace[] {
  const { halfEdges, nodes } = graph;
  const visited = new Set<number>();
  const faces: PlanarFace[] = [];
  for (const start of halfEdges) {
    if (visited.has(start.id)) continue;
    const ids: number[] = [];
    let cursor = start.id;
    let guard = 0;
    const limit = halfEdges.length * 2 + 8;
    while (!visited.has(cursor) && guard < limit) {
      guard += 1;
      visited.add(cursor);
      ids.push(cursor);
      cursor = halfEdges[cursor]!.next;
      if (cursor < 0) break;
    }
    if (ids.length < 3) continue;
    const ring = ids.map((id) => nodes[halfEdges[id]!.from]!);
    const insetMm = ids.map((id) => Math.floor(halfEdges[id]!.thicknessMm / 2));
    const wallIds = Array.from(new Set(ids.map((id) => halfEdges[id]!.wallId)));
    faces.push({
      index: faces.length,
      halfEdgeIds: ids,
      ring,
      insetMm,
      doubledAreaMm2: polygonDoubledAreaMm2(ring),
      wallIds,
    });
  }
  return faces;
}

// ---------------------------------------------------------------------------
// Ring simplification that keeps per-edge distances aligned
// ---------------------------------------------------------------------------

interface RingEntry {
  pt: Pt;
  /** Inset for the edge leaving `pt`. */
  dist: number;
}

/** Drop out-and-back spurs (v → w → v) left by dangling walls. */
function dropSpurs(entries: RingEntry[]): RingEntry[] {
  let list = entries.slice();
  let changed = true;
  while (changed && list.length >= 3) {
    changed = false;
    for (let i = 0; i < list.length; i++) {
      const n = list.length;
      const a = list[i]!;
      const c = list[(i + 2) % n]!;
      if (ptEq(a.pt, c.pt)) {
        a.dist = c.dist;
        const drop = new Set([(i + 1) % n, (i + 2) % n]);
        list = list.filter((_, idx) => !drop.has(idx));
        changed = true;
        break;
      }
    }
  }
  return list;
}

/** Merge consecutive collinear edges, keeping the LARGER inset (thicker wall). */
function mergeCollinear(entries: RingEntry[]): RingEntry[] {
  let list = entries.slice();
  let changed = true;
  while (changed && list.length > 3) {
    changed = false;
    for (let i = 0; i < list.length; i++) {
      const n = list.length;
      const prev = list[(i - 1 + n) % n]!;
      const cur = list[i]!;
      const next = list[(i + 1) % n]!;
      if (cross(prev.pt, cur.pt, next.pt) === 0) {
        prev.dist = Math.max(prev.dist, cur.dist);
        list = list.filter((_, idx) => idx !== i);
        changed = true;
        break;
      }
    }
  }
  return list;
}

// ---------------------------------------------------------------------------
// Room candidates
// ---------------------------------------------------------------------------

export interface RoomCandidate {
  /** Clear (inside-face) polygon, CCW, integer mm. */
  readonly polygon: Polygon;
  /** Area of `polygon` in mm². */
  readonly areaMm2: number;
  /** Walls bounding this room. */
  readonly wallIds: readonly WallId[];
  /** True when the inset failed and the centreline face was used instead. */
  readonly insetFailed: boolean;
}

export interface RoomDetectionOptions {
  /** Faces smaller than this are ignored. Default `MIN_ROOM_AREA_MM2` (0.5m²). */
  readonly minRoomAreaMm2?: number;
  /** Minimum Jaccard overlap to consider a face "the same room". Default 0.30. */
  readonly jaccardThreshold?: number;
}

export const DEFAULT_JACCARD_THRESHOLD = 0.3;

export interface RoomCandidatesResult {
  readonly candidates: readonly RoomCandidate[];
  /**
   * Outer boundary of the wall network, offset OUTWARD by half thickness — the
   * per-storey slab footprint. Null when the walls do not enclose anything.
   */
  readonly outline: Polygon | null;
  readonly nonIntegralCrossings: number;
}

/**
 * Faces of the wall network for one storey, inset to clear polygons.
 * Deterministic: candidates are returned sorted by `polygonKey`.
 */
export function roomCandidates(
  walls: readonly WallLike[],
  opts: RoomDetectionOptions = {},
): RoomCandidatesResult {
  const minArea = opts.minRoomAreaMm2 ?? MIN_ROOM_AREA_MM2;
  const graph = buildHalfEdgeGraph(walls);
  const faces = planarFaces(graph);

  const candidates: RoomCandidate[] = [];
  let outerFace: PlanarFace | null = null;

  for (const face of faces) {
    if (face.doubledAreaMm2 <= 0) {
      // negative-area faces are outside the walls; the largest is THE outside
      if (
        outerFace === null ||
        Math.abs(face.doubledAreaMm2) > Math.abs(outerFace.doubledAreaMm2)
      ) {
        outerFace = face;
      }
      continue;
    }
    const entries: RingEntry[] = face.ring.map((p, i) => ({ pt: p, dist: face.insetMm[i]! }));
    const simplified = mergeCollinear(dropSpurs(entries));
    if (simplified.length < 3) continue;
    const ring = simplified.map((e) => e.pt);
    if (!polygonIsClosedRing(ring)) continue;
    const inset = offsetPolygon(
      ring,
      simplified.map((e) => e.dist),
    );
    const polygon = canonicalRing(inset ?? ring);
    if (polygon.length < 3) continue;
    const areaMm2 = polygonAreaMm2(polygon);
    if (areaMm2 < minArea) continue;
    candidates.push({
      polygon,
      areaMm2,
      wallIds: face.wallIds.slice().sort(),
      insetFailed: inset === null,
    });
  }

  candidates.sort((a, b) => {
    const ka = polygonKey(a.polygon);
    const kb = polygonKey(b.polygon);
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });

  let outline: Polygon | null = null;
  const outer: PlanarFace | null = outerFace;
  if (outer) {
    const entries: RingEntry[] = outer.ring.map((p, i) => ({
      pt: p,
      dist: outer.insetMm[i]!,
    }));
    const simplified = mergeCollinear(dropSpurs(entries));
    if (simplified.length >= 3) {
      // The outer face is CW; reverse to CCW and offset OUTWARD (negative inset)
      const ccw = reversePolygon(simplified.map((e) => e.pt));
      const dists = reverseEdgeAligned(simplified.map((e) => -e.dist));
      const grown = offsetPolygon(ccw, dists);
      outline = canonicalRing(grown ?? ccw);
      if (outline.length < 3) outline = null;
    }
  }

  return { candidates, outline, nonIntegralCrossings: graph.nonIntegralCrossings };
}

/** Edge distances follow the ring when it is reversed: edge i -> edge n-2-i. */
function reverseEdgeAligned(distances: readonly number[]): number[] {
  const n = distances.length;
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(distances[(((n - 2 - i) % n) + n) % n]!);
  return out;
}

// ---------------------------------------------------------------------------
// Matching (this is the part that keeps ids alive)
// ---------------------------------------------------------------------------

export interface RoomMatch {
  readonly candidateIndex: number;
  /** Existing room whose id the candidate inherits, or null for a new room. */
  readonly roomId: RoomId | null;
  readonly jaccard: number;
}

/**
 * Greedy maximum-Jaccard one-to-one matching between new faces and existing
 * rooms.
 *
 * DETERMINISTIC TIE-BREAKING, in order: higher Jaccard, larger intersection
 * area, SMALLER EXISTING POLYGON KEY, lower candidate index, lower id.
 *
 * The polygon-key tie-break is not cosmetic. A symmetric split (one room
 * becomes two equal halves) and the merge that undoes it both produce exact
 * ties, and the two directions must agree or an undo silently renames a room:
 * on a split, candidates are already sorted by polygon key, so the spatially
 * first half inherits the id; breaking the merge tie the same way sends the id
 * back to the same room. Breaking it on the id instead would pick an arbitrary
 * winner and make `wall.delete` + undo lossy.
 */
export function matchRooms(
  candidates: readonly RoomCandidate[],
  existing: readonly Room[],
  opts: RoomDetectionOptions = {},
): RoomMatch[] {
  const threshold = opts.jaccardThreshold ?? DEFAULT_JACCARD_THRESHOLD;
  interface Pair {
    ci: number;
    ei: number;
    j: number;
    inter: number;
  }
  const pairs: Pair[] = [];
  for (let ci = 0; ci < candidates.length; ci++) {
    const cand = candidates[ci]!;
    const cb = bbox(cand.polygon);
    for (let ei = 0; ei < existing.length; ei++) {
      const room = existing[ei]!;
      if (room.polygon.length < 3) continue;
      const rb = bbox(room.polygon);
      if (cb.maxX < rb.minX || rb.maxX < cb.minX || cb.maxY < rb.minY || rb.maxY < cb.minY) continue;
      const j = jaccard(cand.polygon, room.polygon);
      if (j < threshold) continue;
      pairs.push({ ci, ei, j, inter: polygonIntersectionAreaMm2(cand.polygon, room.polygon) });
    }
  }
  const existingKeys = existing.map((r) => polygonKey(r.polygon));
  pairs.sort((a, b) => {
    if (a.j !== b.j) return b.j - a.j;
    if (a.inter !== b.inter) return b.inter - a.inter;
    const ka = existingKeys[a.ei]!;
    const kb = existingKeys[b.ei]!;
    if (ka !== kb) return ka < kb ? -1 : 1;
    if (a.ci !== b.ci) return a.ci - b.ci;
    const ida = existing[a.ei]!.id;
    const idb = existing[b.ei]!.id;
    return ida < idb ? -1 : ida > idb ? 1 : 0;
  });

  const takenCandidates = new Set<number>();
  const takenExisting = new Set<number>();
  const matched = new Map<number, Pair>();
  for (const pair of pairs) {
    if (takenCandidates.has(pair.ci) || takenExisting.has(pair.ei)) continue;
    takenCandidates.add(pair.ci);
    takenExisting.add(pair.ei);
    matched.set(pair.ci, pair);
  }

  const out: RoomMatch[] = [];
  for (let ci = 0; ci < candidates.length; ci++) {
    const pair = matched.get(ci);
    out.push({
      candidateIndex: ci,
      roomId: pair ? existing[pair.ei]!.id : null,
      jaccard: pair ? pair.j : 0,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// detectRooms — the function fold() calls
// ---------------------------------------------------------------------------

export interface RoomDetectionResult {
  /** The new room set for this storey (ids preserved where possible). */
  readonly rooms: readonly Room[];
  /** Per-storey slab footprint (outer wall face). */
  readonly outline: Polygon | null;
  readonly candidates: readonly RoomCandidate[];
  readonly matches: readonly RoomMatch[];
  /** Ids of rooms that genuinely disappeared (nothing matched them). */
  readonly removedRoomIds: readonly RoomId[];
  readonly nonIntegralCrossings: number;
}

/**
 * Recompute the rooms of ONE storey from its walls, preserving ids.
 *
 * @param walls        walls of this storey (others are ignored defensively)
 * @param storeyId     the storey being recomputed
 * @param existingRooms rooms currently recorded for this storey
 * @param takenIds     every id already in the document, so derived ids stay unique
 */
export function detectRooms(
  walls: readonly Wall[],
  storeyId: StoreyId,
  existingRooms: readonly Room[],
  takenIds: ReadonlySet<string> = new Set<string>(),
  opts: RoomDetectionOptions = {},
): RoomDetectionResult {
  const mine = walls.filter((w) => w.storeyId === storeyId);
  const priorRooms = existingRooms.filter((r) => r.storeyId === storeyId);
  const { candidates, outline, nonIntegralCrossings } = roomCandidates(mine, opts);
  const matches = matchRooms(candidates, priorRooms, opts);
  const priorById = new Map(priorRooms.map((r) => [r.id, r]));

  const used = new Set<string>(takenIds);
  const rooms: Room[] = [];
  const matchedIds = new Set<string>();

  for (const match of matches) {
    const cand = candidates[match.candidateIndex]!;
    const prior = match.roomId === null ? undefined : priorById.get(match.roomId);
    if (prior) {
      matchedIds.add(prior.id);
      used.add(prior.id);
      rooms.push({
        ...prior,
        polygon: cand.polygon,
        areaMm2: cand.areaMm2,
      });
    } else {
      const id = derivedIdUnique('room', `${storeyId}|${polygonKey(cand.polygon)}`, used);
      used.add(id);
      rooms.push({
        id,
        storeyId,
        type: 'unassigned',
        name: '',
        polygon: cand.polygon,
        areaMm2: cand.areaMm2,
        tags: [],
        locked: false,
        targetAreaMm2: null,
        mustFace: null,
      });
    }
  }

  const removedRoomIds = priorRooms.filter((r) => !matchedIds.has(r.id)).map((r) => r.id);

  return { rooms, outline, candidates, matches, removedRoomIds, nonIntegralCrossings };
}
