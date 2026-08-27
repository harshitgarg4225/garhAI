/**
 * Pure-logic spec for the plot feature: polygon edit ops, edge-length
 * recompute, the self-intersection guard, road/edge-index bookkeeping and the
 * rulepack resolver. The op-building tests fold through the REAL model core —
 * if `fold`'s road-keeping behaviour ever changes, these fail here rather
 * than in a demo.
 */

import { describe, expect, it } from 'vitest';

import {
  emptyProjectDoc,
  fold,
  polygonAreaMm2,
  polygonDoubledAreaMm2,
  polygonsCongruent,
  type Op,
  type ProjectDoc,
  type Road,
} from '@garh/model';

import {
  checkBoundary,
  edgeLengthsMm,
  frontEdgeIndex,
  insertVertexOnEdge,
  moveVertex,
  rectBoundaryMm,
  remapRoadsAfterInsert,
  remapRoadsAfterRemove,
  removeVertex,
  setEdgeLengthMm,
} from './geometry';
import { boundaryGroupOps, boundaryOp, normalizeNorthDeg, roadOp } from './ops';
import {
  buildRegFacts,
  readValueOverrides,
  resolveRegValues,
  rulepackDocSchema,
  whenMatches,
  withValueOverride,
} from './rules';

// 30 × 40 ft — the classic site.
const RECT = rectBoundaryMm(9144, 12192);

const L_SHAPE = [
  { x: 0, y: 0 },
  { x: 9000, y: 0 },
  { x: 9000, y: 6000 },
  { x: 5000, y: 6000 },
  { x: 5000, y: 12000 },
  { x: 0, y: 12000 },
];

function road(edgeIndex: number, widthMm: number | null, name: string | null = null): Road {
  return { edgeIndex, widthMm, name };
}

function foldAll(doc: ProjectDoc, ops: readonly Op[]): ProjectDoc {
  let current = doc;
  for (const op of ops) current = fold(current, op, { computeInverse: false }).model;
  return current;
}

// ---------------------------------------------------------------------------
// Rect quick-start
// ---------------------------------------------------------------------------

describe('rectBoundaryMm', () => {
  it('builds a CCW integer rectangle from the SW origin', () => {
    expect(RECT).toHaveLength(4);
    expect(polygonDoubledAreaMm2(RECT)).toBeGreaterThan(0); // CCW
    expect(polygonAreaMm2(RECT)).toBe(9144 * 12192);
    expect(RECT[0]).toEqual({ x: 0, y: 0 });
    expect(edgeLengthsMm(RECT)).toEqual([9144, 12192, 9144, 12192]);
  });

  it('refuses zero, negative and fractional sizes', () => {
    expect(() => rectBoundaryMm(0, 1000)).toThrow();
    expect(() => rectBoundaryMm(1000, -5)).toThrow();
    expect(() => rectBoundaryMm(9144.5, 12192)).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Edge-length recompute (click a dimension, type a value)
// ---------------------------------------------------------------------------

describe('setEdgeLengthMm', () => {
  it('resizes a rectangle and keeps it rectangular (edge 0)', () => {
    const result = setEdgeLengthMm(RECT, 0, 12192);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(edgeLengthsMm(result.polygon)).toEqual([12192, 12192, 12192, 12192]);
    expect(polygonAreaMm2(result.polygon)).toBe(12192 * 12192);
    for (const p of result.polygon) {
      expect(Number.isSafeInteger(p.x)).toBe(true);
      expect(Number.isSafeInteger(p.y)).toBe(true);
    }
  });

  it('resizes symmetrically when the edited edge runs the other way (edge 2)', () => {
    const result = setEdgeLengthMm(RECT, 2, 10000);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(edgeLengthsMm(result.polygon)).toEqual([10000, 12192, 10000, 12192]);
    expect(checkBoundary(result.polygon).ok).toBe(true);
  });

  it('stretches an L-shape without breaking rectilinearity', () => {
    expect(checkBoundary(L_SHAPE).ok).toBe(true);
    const result = setEdgeLengthMm(L_SHAPE, 0, 10000);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    // Everything beyond the edge midpoint slides +1000 in x, notch included.
    expect(result.polygon).toEqual([
      { x: 0, y: 0 },
      { x: 10000, y: 0 },
      { x: 10000, y: 6000 },
      { x: 6000, y: 6000 },
      { x: 6000, y: 12000 },
      { x: 0, y: 12000 },
    ]);
    expect(polygonAreaMm2(result.polygon)).toBe(96_000_000);
  });

  it('refuses non-positive and fractional lengths with a reason', () => {
    for (const bad of [0, -100, 1234.5]) {
      const result = setEdgeLengthMm(RECT, 0, bad);
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.reason.length).toBeGreaterThan(0);
    }
  });

  it('is a no-op when the requested length is the current length', () => {
    const result = setEdgeLengthMm(RECT, 1, 12192);
    expect(result.ok).toBe(true);
    if (result.ok) expect(polygonsCongruent(result.polygon, RECT)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Vertex editing + the self-intersection guard
// ---------------------------------------------------------------------------

describe('moveVertex', () => {
  it('moves a corner when the ring stays simple', () => {
    const result = moveVertex(RECT, 2, { x: 10000, y: 13000 });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.polygon[2]).toEqual({ x: 10000, y: 13000 });
  });

  it('rejects a move that makes the boundary cross itself', () => {
    // Dragging v1 far above the top edge makes edge v0→v1 cross edge v2→v3.
    const result = moveVertex(RECT, 1, { x: 9144, y: 20000 });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(/cross/i);
  });

  it('rejects a move onto another vertex', () => {
    const result = moveVertex(RECT, 2, { x: 0, y: 12192 });
    expect(result.ok).toBe(false);
  });
});

describe('insertVertexOnEdge / removeVertex', () => {
  it('round-trips: insert a midpoint, remove it, get the same ring back', () => {
    const inserted = insertVertexOnEdge(RECT, 0);
    expect(inserted.ok).toBe(true);
    if (!inserted.ok) return;
    expect(inserted.polygon).toHaveLength(5);
    expect(inserted.polygon[1]).toEqual({ x: 4572, y: 0 });
    expect(polygonAreaMm2(inserted.polygon)).toBe(polygonAreaMm2(RECT));

    const removed = removeVertex(inserted.polygon, 1);
    expect(removed.ok).toBe(true);
    if (removed.ok) expect(polygonsCongruent(removed.polygon, RECT)).toBe(true);
  });

  it('never lets the ring drop below a triangle', () => {
    const tri = [
      { x: 0, y: 0 },
      { x: 6000, y: 0 },
      { x: 0, y: 6000 },
    ];
    const result = removeVertex(tri, 0);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(/3 corners/);
  });
});

// ---------------------------------------------------------------------------
// Road ↔ edge-index bookkeeping
// ---------------------------------------------------------------------------

describe('road remapping', () => {
  it('insert: the split edge keeps its road on both halves, later edges shift', () => {
    const roads = [road(0, 9000, 'Main Road'), road(2, 6000)];
    expect(remapRoadsAfterInsert(roads, 0)).toEqual([
      road(0, 9000, 'Main Road'),
      road(1, 9000, 'Main Road'),
      road(3, 6000),
    ]);
  });

  it('remove: merging edges keeps the wider road; later edges shift down', () => {
    // 5-ring: removing vertex 1 merges edges 0 and 1.
    const roads = [road(0, 6000, 'Narrow'), road(1, 9000, 'Wide'), road(3, 4500)];
    expect(remapRoadsAfterRemove(roads, 1, 5)).toEqual([road(0, 9000, 'Wide'), road(2, 4500)]);
  });

  it('remove vertex 0: the wrap-around edges merge into the last edge', () => {
    const roads = [road(0, 6000), road(4, 9000, 'Ring Rd')];
    expect(remapRoadsAfterRemove(roads, 0, 5)).toEqual([road(3, 9000, 'Ring Rd')]);
  });

  it('frontEdgeIndex: widest road wins, ties break to the lowest index', () => {
    expect(frontEdgeIndex([])).toBe(null);
    expect(frontEdgeIndex([road(2, 6000), road(1, 9000)])).toBe(1);
    expect(frontEdgeIndex([road(3, 9000), road(1, 9000)])).toBe(1);
    expect(frontEdgeIndex([road(2, null)])).toBe(null);
  });
});

// ---------------------------------------------------------------------------
// Op construction, folded through the real model core
// ---------------------------------------------------------------------------

describe('boundaryGroupOps', () => {
  it('carries roads across a vertex insert (folded through @garh/model)', () => {
    let doc = emptyProjectDoc();
    doc = foldAll(doc, [boundaryOp(RECT), roadOp(1, 9000, 'Main Road')]);
    expect(doc.plot.roads).toEqual([road(1, 9000, 'Main Road')]);

    const inserted = insertVertexOnEdge(doc.plot.boundary, 0);
    expect(inserted.ok).toBe(true);
    if (!inserted.ok) return;
    const nextRoads = remapRoadsAfterInsert(doc.plot.roads, 0);
    const ops = boundaryGroupOps(doc.plot.roads, inserted.polygon, nextRoads);

    doc = foldAll(doc, ops);
    expect(doc.plot.boundary).toHaveLength(5);
    // The road moved from edge 1 to edge 2 — nothing dangling, nothing lost.
    expect(doc.plot.roads).toEqual([road(2, 9000, 'Main Road')]);
  });

  it('emits no road ops when nothing about the roads changes', () => {
    const moved = moveVertex(RECT, 2, { x: 9500, y: 12500 });
    expect(moved.ok).toBe(true);
    if (!moved.ok) return;
    const roads = [road(0, 9000)];
    const ops = boundaryGroupOps(roads, moved.polygon, roads);
    expect(ops).toHaveLength(1);
    expect(ops[0]?.type).toBe('plot.set_boundary');
  });

  it('clears a road the caller dropped', () => {
    const roads = [road(0, 9000, 'Main Road')];
    const ops = boundaryGroupOps(roads, RECT, []);
    expect(ops).toHaveLength(2);
    expect(ops[1]).toMatchObject({
      type: 'plot.set_road',
      payload: { edgeIndex: 0, widthMm: null },
    });
    const doc = foldAll(
      foldAll(emptyProjectDoc(), [boundaryOp(RECT), roadOp(0, 9000, 'Main Road')]),
      ops,
    );
    expect(doc.plot.roads).toEqual([]);
  });
});

describe('normalizeNorthDeg', () => {
  it('rounds and wraps into 0–359', () => {
    expect(normalizeNorthDeg(0)).toBe(0);
    expect(normalizeNorthDeg(360)).toBe(0);
    expect(normalizeNorthDeg(-45)).toBe(315);
    expect(normalizeNorthDeg(359.6)).toBe(0);
    expect(normalizeNorthDeg(12.4)).toBe(12);
    expect(normalizeNorthDeg(-0.4)).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Rulepack resolution (mirror of garh_rules predicate semantics)
// ---------------------------------------------------------------------------

/** A trimmed, faithful subset of the seeded Bengaluru pack. */
const PACK = rulepackDocSchema.parse({
  pack: 'blr',
  version: '2026.07',
  title: 'Bengaluru (test subset)',
  extends: 'nbc-core',
  citations_base: 'BBMP Building Bye-laws 2020',
  confidenceDefault: 'seed',
  rules: [
    {
      id: 'blr.setback.front.plot.le120',
      severity: 'fail',
      title: 'Front setback - plots up to 120 m2',
      when: {
        zoneCategory: { eq: 'residential' },
        buildingUse: { in: ['dwelling-single', 'dwelling-two', 'row-house'] },
        plotAreaSqm: { lte: 120 },
      },
      check: { type: 'setback_min', edge: 'front', valueMm: 1500 },
      cite: 'Table 6 - Setbacks for residential plots',
    },
    {
      id: 'blr.setback.front.road.9-18m',
      severity: 'fail',
      title: 'Front setback - road 9 to 18 m',
      when: {
        zoneCategory: { eq: 'residential' },
        buildingUse: { in: ['dwelling-single', 'dwelling-two', 'row-house'] },
        roadWidthMm: { gte: 9000, lt: 18000 },
      },
      check: { type: 'setback_min', edge: 'front', valueMm: 3000 },
      cite: 'Table 6a',
    },
    {
      id: 'blr.setback.rear.plot.le120',
      severity: 'fail',
      title: 'Rear setback - plots up to 120 m2',
      when: { plotAreaSqm: { lte: 120 } },
      check: { type: 'setback_min', edge: 'rear', valueMm: 1000 },
      cite: 'Table 6',
    },
    {
      id: 'blr.setback.side.plot.le120',
      severity: 'fail',
      title: 'Side setback - plots up to 120 m2',
      when: { plotAreaSqm: { lte: 120 } },
      check: { type: 'setback_min', edge: 'sides', valueMm: 1000 },
      cite: 'Table 6',
    },
    {
      id: 'blr.coverage.plot.le120',
      severity: 'fail',
      title: 'Coverage - plots up to 120 m2',
      when: { plotAreaSqm: { lte: 120 } },
      check: { type: 'coverage_max', ratio: { num: 70, den: 100 } },
      cite: 'Table 5',
    },
    {
      id: 'blr.coverage.plot.121-240',
      severity: 'fail',
      title: 'Coverage - plots 121 to 240 m2',
      when: { plotAreaSqm: { gt: 120, lte: 240 } },
      check: { type: 'coverage_max', ratio: { num: 65, den: 100 } },
      cite: 'Table 5',
    },
    {
      id: 'blr.far.road.9-18m',
      severity: 'fail',
      title: 'FAR - road 9 to 18 m',
      when: { roadWidthMm: { gte: 9000, lt: 18000 } },
      check: { type: 'far_max', ratio: { num: 225, den: 100 } },
      cite: 'Table 5',
    },
    {
      id: 'blr.height.road.9-18m',
      severity: 'fail',
      title: 'Height - road 9 to 18 m',
      when: { roadWidthMm: { gte: 9000, lt: 18000 } },
      check: { type: 'height_max', valueMm: 15000 },
      cite: 'Table 4',
    },
    {
      id: 'blr.floors.road.9-18m',
      severity: 'fail',
      title: 'Floors - road 9 to 18 m',
      when: { roadWidthMm: { gte: 9000, lt: 18000 } },
      check: { type: 'floors_max', value: 4, counts: [] },
      cite: 'Table 4',
    },
  ],
});

const AREA_30x40 = 9144 * 12192; // 111,483,648 mm² ≈ 111.5 m² -> ≤120 m² band

describe('rulepack resolution', () => {
  it('bands by plot size and road width, taking the tightest match', () => {
    const facts = buildRegFacts({
      boundaryAreaMm2: AREA_30x40,
      roads: [road(0, 9000, '9m Road')],
    });
    const resolved = resolveRegValues(PACK, facts);

    // Two front bands match: plot ≤120 m² (1500) and road 9–18 m (3000).
    // Setbacks are minimums, so the LARGER binds.
    expect(resolved.values.setbackFrontMm).toMatchObject({
      value: 3000,
      ruleId: 'blr.setback.front.road.9-18m',
      confidence: 'seed',
      overridden: false,
    });
    expect(resolved.values.setbackRearMm?.value).toBe(1000);
    expect(resolved.values.setbackSideMm?.value).toBe(1000);
    expect(resolved.values.coveragePct?.value).toBe(70);
    expect(resolved.values.farX100?.value).toBe(225);
    expect(resolved.values.heightMaxMm?.value).toBe(15000);
    expect(resolved.values.floorsMax?.value).toBe(4);
    expect(resolved.missing).toEqual([]);
  });

  it('reports road-banded values as missing (never the generous band) without a road', () => {
    const facts = buildRegFacts({ boundaryAreaMm2: AREA_30x40, roads: [] });
    const resolved = resolveRegValues(PACK, facts);

    expect(resolved.values.setbackFrontMm?.value).toBe(1500); // plot band still applies
    expect(resolved.values.farX100).toBeUndefined();
    const missingFar = resolved.missing.find((m) => m.key === 'farX100');
    expect(missingFar?.reason).toMatch(/road/i);
  });

  it('scales plotAreaSqm thresholds exactly — a 120.000001 m² plot leaves the band', () => {
    const inBand = buildRegFacts({ boundaryAreaMm2: 120_000_000, roads: [] });
    const outOfBand = buildRegFacts({ boundaryAreaMm2: 120_000_001, roads: [] });
    expect(resolveRegValues(PACK, inBand).values.coveragePct?.value).toBe(70);
    expect(resolveRegValues(PACK, outOfBand).values.coveragePct?.value).toBe(65);
  });

  it('scales eq and in thresholds on plotAreaSqm like predicates.py (int-only)', () => {
    // Synthetic — no shipped pack uses eq/in on plotAreaSqm, but the mirror
    // contract covers every operator, so this pins the scaling rule.
    const eqPack = rulepackDocSchema.parse({
      pack: 'test',
      rules: [
        {
          id: 'test.coverage.eq120',
          title: 'Coverage — exactly 120 m²',
          when: { plotAreaSqm: { eq: 120 } },
          check: { type: 'coverage_max', ratio: { num: 60, den: 100 } },
        },
        {
          id: 'test.floors.in-bands',
          title: 'Floors — plot in {60, 120} m²',
          when: { plotAreaSqm: { in: [60, 120] } },
          check: { type: 'floors_max', value: 2, counts: [] },
        },
      ],
    });
    const exact = buildRegFacts({ boundaryAreaMm2: 120_000_000, roads: [] });
    const off = buildRegFacts({ boundaryAreaMm2: 120_000_001, roads: [] });
    expect(resolveRegValues(eqPack, exact).values.coveragePct?.value).toBe(60);
    expect(resolveRegValues(eqPack, exact).values.floorsMax?.value).toBe(2);
    expect(resolveRegValues(eqPack, off).values.coveragePct).toBeUndefined();
    expect(resolveRegValues(eqPack, off).values.floorsMax).toBeUndefined();
    // Python's _scaled skips non-int thresholds; the mirror must too.
    expect(whenMatches({ plotAreaSqm: { eq: 120.5 } }, exact)).toBe(false);
  });

  it('treats null facts as not-applicable for every operator', () => {
    const noPlot = buildRegFacts({ boundaryAreaMm2: null, roads: [] });
    expect(whenMatches({ plotAreaSqm: { lte: 120 } }, noPlot)).toBe(false);
    expect(whenMatches({ roadWidthMm: { lt: 9000 } }, noPlot)).toBe(false);
    expect(whenMatches({ zoneCategory: { eq: 'residential' } }, noPlot)).toBe(true);
  });

  it('uses the widest road as the front road', () => {
    const facts = buildRegFacts({
      boundaryAreaMm2: AREA_30x40,
      roads: [road(3, 6000), road(1, 12000)],
    });
    expect(facts.roadWidthMm).toBe(12000);
  });
});

describe('value overrides', () => {
  it('round-trips through the overrides object and wins the resolution', () => {
    let overrides = withValueOverride({}, 'setbackFrontMm', 1200);
    overrides = withValueOverride(overrides, 'farX100', 175);
    expect(readValueOverrides(overrides)).toEqual({ setbackFrontMm: 1200, farX100: 175 });

    const facts = buildRegFacts({ boundaryAreaMm2: AREA_30x40, roads: [road(0, 9000)] });
    const resolved = resolveRegValues(PACK, facts, overrides);
    expect(resolved.values.setbackFrontMm).toMatchObject({
      value: 1200,
      overridden: true,
      // The losing rule stays attached so the citation is still shown.
      ruleId: 'blr.setback.front.road.9-18m',
    });
    expect(resolved.values.farX100?.value).toBe(175);
  });

  it('clears with null and preserves unrelated keys', () => {
    const start = { someRuleAck: { reason: 'checked on site' } };
    let overrides = withValueOverride(start, 'coveragePct', 60);
    overrides = withValueOverride(overrides, 'coveragePct', null);
    expect(overrides).toEqual(start);
  });

  it('refuses non-integers (the op validator would too)', () => {
    expect(() => withValueOverride({}, 'farX100', 1.75)).toThrow();
  });

  it('ignores malformed stored values instead of guessing', () => {
    expect(
      readValueOverrides({ values: { setbackFrontMm: 'wide', farX100: 2.25, coveragePct: 60 } }),
    ).toEqual({ coveragePct: 60 });
    expect(readValueOverrides({ values: [1, 2, 3] })).toEqual({});
    expect(readValueOverrides({})).toEqual({});
  });
});
