import { describe, expect, it } from 'vitest';

import {
  bbox,
  bboxAreaMm2,
  bboxIntersects,
  collinearOverlap,
  compareAngleAround,
  containmentRatio,
  cross,
  dedupeCollinear,
  distMm,
  distSqMm2,
  ensureCcw,
  jaccard,
  offsetPolygon,
  offsetPolygonUniform,
  pointAlongSeg,
  pointInPolygon,
  pointInTriangle,
  polygonAreaMm2,
  polygonCentroid,
  polygonDoubledAreaMm2,
  polygonIsClosedRing,
  polygonIsSimple,
  polygonIntersectionAreaMm2,
  polygonKey,
  polygonOrientation,
  polygonPerimeterMm,
  polygonsCongruent,
  pt,
  ptRound,
  rectPolygon,
  removeSpurs,
  reversePolygon,
  segmentIntersection,
  segmentLengthMm,
  segmentsOverlapCollinear,
  segmentsProperlyCross,
  triangulate,
  unionAxisAlignedRects,
  unionAxisAlignedRectsHasHoles,
} from './geometry';

const square = rectPolygon(0, 0, 1000, 1000);

describe('Pt', () => {
  it('refuses non-integer millimetres', () => {
    expect(() => pt(1.5, 0)).toThrow(RangeError);
    expect(() => pt(0, Number.NaN)).toThrow(RangeError);
    expect(pt(-100, 200)).toEqual({ x: -100, y: 200 });
  });

  it('ptRound is the only float door, and rounds away from zero', () => {
    expect(ptRound(0.5, -0.5)).toEqual({ x: 1, y: -1 });
  });
});

describe('distances', () => {
  it('is exact for axis-aligned pairs', () => {
    expect(distMm(pt(0, 0), pt(3000, 0))).toBe(3000);
    expect(distMm(pt(0, 0), pt(0, -3000))).toBe(3000);
    expect(distSqMm2(pt(0, 0), pt(300, 400))).toBe(250_000);
    expect(distMm(pt(0, 0), pt(300, 400))).toBe(500);
  });

  it('segmentLengthMm agrees with distMm', () => {
    expect(segmentLengthMm({ a: pt(0, 0), b: pt(6000, 0) })).toBe(6000);
  });

  it('pointAlongSeg is exact on axis-aligned segments', () => {
    expect(pointAlongSeg({ a: pt(0, 0), b: pt(6000, 0) }, 2000)).toEqual({ x: 2000, y: 0 });
    expect(pointAlongSeg({ a: pt(0, 4000), b: pt(0, 0) }, 1000)).toEqual({ x: 0, y: 3000 });
  });
});

describe('polygon area, orientation, centroid', () => {
  it('shoelace is exact in mm²', () => {
    expect(polygonDoubledAreaMm2(square)).toBe(2_000_000);
    expect(polygonAreaMm2(square)).toBe(1_000_000);
    expect(polygonAreaMm2(reversePolygon(square))).toBe(1_000_000);
  });

  it('reports orientation', () => {
    expect(polygonOrientation(square)).toBe('ccw');
    expect(polygonOrientation(reversePolygon(square))).toBe('cw');
    expect(polygonOrientation([pt(0, 0), pt(10, 0), pt(20, 0)])).toBe('degenerate');
  });

  it('ensureCcw is idempotent', () => {
    expect(ensureCcw(square)).toEqual(square);
    expect(ensureCcw(reversePolygon(square))).toEqual(square);
  });

  it('centroid of a rectangle is its middle', () => {
    expect(polygonCentroid(square)).toEqual({ x: 500, y: 500 });
    expect(polygonCentroid(rectPolygon(0, 0, 3000, 1000))).toEqual({ x: 1500, y: 500 });
  });

  it('perimeter is exact for rectilinear rings', () => {
    expect(polygonPerimeterMm(square)).toBe(4000);
  });

  it('L-shaped area is the union of its two rectangles', () => {
    // 3000x3000 with a 1000x1000 bite out of the NE corner
    const lShape = [
      pt(0, 0),
      pt(3000, 0),
      pt(3000, 2000),
      pt(2000, 2000),
      pt(2000, 3000),
      pt(0, 3000),
    ];
    expect(polygonAreaMm2(lShape)).toBe(3000 * 3000 - 1000 * 1000);
  });
});

describe('point in polygon', () => {
  it('distinguishes inside, outside and boundary', () => {
    expect(pointInPolygon(pt(500, 500), square)).toBe('inside');
    expect(pointInPolygon(pt(1500, 500), square)).toBe('outside');
    expect(pointInPolygon(pt(0, 500), square)).toBe('boundary');
    expect(pointInPolygon(pt(0, 0), square)).toBe('boundary');
    expect(pointInPolygon(pt(1000, 1000), square)).toBe('boundary');
  });

  it('handles a concave ring', () => {
    const lShape = [
      pt(0, 0),
      pt(3000, 0),
      pt(3000, 1000),
      pt(1000, 1000),
      pt(1000, 3000),
      pt(0, 3000),
    ];
    expect(pointInPolygon(pt(500, 2500), lShape)).toBe('inside');
    expect(pointInPolygon(pt(2500, 2500), lShape)).toBe('outside');
    expect(pointInPolygon(pt(2500, 500), lShape)).toBe('inside');
  });

  it('pointInTriangle includes the boundary', () => {
    expect(pointInTriangle(pt(0, 0), pt(0, 0), pt(100, 0), pt(0, 100))).toBe(true);
    expect(pointInTriangle(pt(10, 10), pt(0, 0), pt(100, 0), pt(0, 100))).toBe(true);
    expect(pointInTriangle(pt(90, 90), pt(0, 0), pt(100, 0), pt(0, 100))).toBe(false);
  });
});

describe('simplicity', () => {
  it('accepts simple rings, including ones with collinear vertices', () => {
    expect(polygonIsSimple(square)).toBe(true);
    expect(polygonIsSimple([pt(0, 0), pt(500, 0), pt(1000, 0), pt(1000, 1000), pt(0, 1000)])).toBe(
      true,
    );
  });

  it('rejects bow-ties and duplicate vertices', () => {
    expect(polygonIsSimple([pt(0, 0), pt(1000, 1000), pt(1000, 0), pt(0, 1000)])).toBe(false);
    expect(polygonIsSimple([pt(0, 0), pt(0, 0), pt(1000, 1000)])).toBe(false);
  });

  it('polygonIsClosedRing is what the ROOM_NOT_CLOSED invariant uses', () => {
    expect(polygonIsClosedRing(square)).toBe(true);
    expect(polygonIsClosedRing([pt(0, 0), pt(1000, 0)])).toBe(false);
    expect(polygonIsClosedRing([pt(0, 0), pt(1000, 0), pt(2000, 0)])).toBe(false);
  });
});

describe('segment intersection', () => {
  it('finds a proper crossing exactly when it is integral', () => {
    const r = segmentIntersection(
      { a: pt(0, 0), b: pt(1000, 0) },
      { a: pt(500, -500), b: pt(500, 500) },
    );
    expect(r.kind).toBe('point');
    if (r.kind === 'point') {
      expect(r.point).toEqual({ x: 500, y: 0 });
      expect(r.exact).toBe(true);
      expect(r.onEndpoint).toBe(false);
    }
  });

  it('reports touching endpoints separately from crossings', () => {
    const r = segmentIntersection(
      { a: pt(0, 0), b: pt(1000, 0) },
      { a: pt(1000, 0), b: pt(1000, 1000) },
    );
    expect(r.kind).toBe('point');
    if (r.kind === 'point') expect(r.onEndpoint).toBe(true);
    expect(segmentsProperlyCross({ a: pt(0, 0), b: pt(1000, 0) }, { a: pt(1000, 0), b: pt(1000, 1000) })).toBe(
      false,
    );
  });

  it('detects collinear overlap (the WALL_DUPLICATE invariant)', () => {
    const a = { a: pt(0, 0), b: pt(1000, 0) };
    const b = { a: pt(500, 0), b: pt(1500, 0) };
    const r = segmentIntersection(a, b);
    expect(r.kind).toBe('collinear');
    expect(collinearOverlap(a, b)).toEqual({ a: { x: 500, y: 0 }, b: { x: 1000, y: 0 } });
    expect(segmentsOverlapCollinear(a, b)).toBe(true);
    // touching end to end is NOT an overlap
    expect(segmentsOverlapCollinear(a, { a: pt(1000, 0), b: pt(2000, 0) })).toBe(false);
  });

  it('finds nothing when segments miss', () => {
    expect(
      segmentIntersection({ a: pt(0, 0), b: pt(100, 0) }, { a: pt(0, 50), b: pt(100, 50) }).kind,
    ).toBe('none');
  });
});

describe('compareAngleAround (exact, integer-only)', () => {
  it('orders directions counter-clockwise from +X', () => {
    const o = pt(0, 0);
    const east = pt(100, 0);
    const north = pt(0, 100);
    const west = pt(-100, 0);
    const south = pt(0, -100);
    expect(compareAngleAround(o, east, north)).toBe(-1);
    expect(compareAngleAround(o, north, west)).toBe(-1);
    expect(compareAngleAround(o, west, south)).toBe(-1);
    expect(compareAngleAround(o, south, east)).toBe(1);
    expect(compareAngleAround(o, east, east)).toBe(0);
  });

  it('sorts a full fan into ascending angle', () => {
    const o = pt(0, 0);
    const dirs = [pt(0, -100), pt(-100, 0), pt(0, 100), pt(100, 0), pt(100, 100)];
    const sorted = dirs.slice().sort((a, b) => compareAngleAround(o, a, b));
    expect(sorted).toEqual([pt(100, 0), pt(100, 100), pt(0, 100), pt(-100, 0), pt(0, -100)]);
  });
});

describe('offsetPolygon', () => {
  it('insets a rectangle by a uniform distance', () => {
    const inner = offsetPolygonUniform(rectPolygon(0, 0, 6000, 4000), 115);
    expect(inner).toEqual(rectPolygon(115, 115, 5885, 3885));
  });

  it('offsets each edge by its own distance (setback envelopes)', () => {
    // edges of rectPolygon: 0 south, 1 east, 2 north, 3 west
    const envelope = offsetPolygon(rectPolygon(0, 0, 9144, 12192), [1500, 900, 1200, 900]);
    expect(envelope).toEqual(rectPolygon(900, 1500, 8244, 10992));
  });

  it('grows outward with negative distances', () => {
    expect(offsetPolygonUniform(rectPolygon(0, 0, 1000, 1000), -100)).toEqual(
      rectPolygon(-100, -100, 1100, 1100),
    );
  });

  it('returns null when the offset collapses the polygon', () => {
    expect(offsetPolygonUniform(rectPolygon(0, 0, 1000, 1000), 600)).toBeNull();
  });

  it('preserves the input orientation', () => {
    const cw = reversePolygon(rectPolygon(0, 0, 1000, 1000));
    const inset = offsetPolygonUniform(cw, 100);
    expect(inset).not.toBeNull();
    expect(polygonOrientation(inset as ReturnType<typeof rectPolygon>)).toBe('cw');
    expect(polygonAreaMm2(inset as ReturnType<typeof rectPolygon>)).toBe(800 * 800);
  });

  it('rejects a distance list that does not match the edge count', () => {
    expect(() => offsetPolygon(square, [1, 2])).toThrow(RangeError);
  });
});

describe('rect/L/T union', () => {
  it('unions two rectangles into an L', () => {
    const rings = unionAxisAlignedRects([
      { minX: 0, minY: 0, maxX: 3000, maxY: 1000 },
      { minX: 0, minY: 0, maxX: 1000, maxY: 3000 },
    ]);
    expect(rings).toHaveLength(1);
    expect(polygonAreaMm2(rings[0]!)).toBe(3000 * 1000 + 1000 * 2000);
    expect(polygonOrientation(rings[0]!)).toBe('ccw');
    expect(rings[0]).toHaveLength(6);
  });

  it('unions three rectangles into a T', () => {
    const rings = unionAxisAlignedRects([
      { minX: 0, minY: 2000, maxX: 3000, maxY: 3000 },
      { minX: 1000, minY: 0, maxX: 2000, maxY: 2000 },
    ]);
    expect(rings).toHaveLength(1);
    expect(polygonAreaMm2(rings[0]!)).toBe(3000 * 1000 + 1000 * 2000);
    expect(rings[0]).toHaveLength(8);
  });

  it('merges overlapping rectangles exactly', () => {
    const rings = unionAxisAlignedRects([
      { minX: 0, minY: 0, maxX: 2000, maxY: 2000 },
      { minX: 1000, minY: 0, maxX: 3000, maxY: 2000 },
    ]);
    expect(rings).toHaveLength(1);
    expect(polygonAreaMm2(rings[0]!)).toBe(3000 * 2000);
    expect(rings[0]).toHaveLength(4);
  });

  it('returns two rings for disjoint rectangles, largest first', () => {
    const rings = unionAxisAlignedRects([
      { minX: 0, minY: 0, maxX: 1000, maxY: 1000 },
      { minX: 5000, minY: 0, maxX: 8000, maxY: 1000 },
    ]);
    expect(rings).toHaveLength(2);
    expect(polygonAreaMm2(rings[0]!)).toBe(3_000_000);
  });

  it('flags a courtyard instead of silently filling it', () => {
    const donut = [
      { minX: 0, minY: 0, maxX: 3000, maxY: 1000 },
      { minX: 0, minY: 2000, maxX: 3000, maxY: 3000 },
      { minX: 0, minY: 0, maxX: 1000, maxY: 3000 },
      { minX: 2000, minY: 0, maxX: 3000, maxY: 3000 },
    ];
    expect(unionAxisAlignedRectsHasHoles(donut)).toBe(true);
  });
});

describe('triangulation and intersection area', () => {
  it('triangulates a rectangle into two triangles covering its area', () => {
    const tris = triangulate(square);
    expect(tris).toHaveLength(2);
    const total = tris.reduce((sum, t) => sum + polygonAreaMm2(t), 0);
    expect(total).toBe(1_000_000);
  });

  it('triangulates an L-shape without losing area', () => {
    const lShape = [
      pt(0, 0),
      pt(3000, 0),
      pt(3000, 1000),
      pt(1000, 1000),
      pt(1000, 3000),
      pt(0, 3000),
    ];
    const tris = triangulate(lShape);
    expect(tris).toHaveLength(4);
    const total = tris.reduce((sum, t) => sum + polygonAreaMm2(t), 0);
    expect(total).toBe(polygonAreaMm2(lShape));
  });

  it('computes intersection area of overlapping rectangles', () => {
    const a = rectPolygon(0, 0, 2000, 2000);
    const b = rectPolygon(1000, 1000, 3000, 3000);
    expect(polygonIntersectionAreaMm2(a, b)).toBe(1_000_000);
    expect(polygonIntersectionAreaMm2(a, rectPolygon(5000, 5000, 6000, 6000))).toBe(0);
    expect(polygonIntersectionAreaMm2(a, a)).toBe(polygonAreaMm2(a));
  });
});

describe('jaccard (load-bearing for room-id matching)', () => {
  it('is 1 for identical polygons and 0 for disjoint ones', () => {
    expect(jaccard(square, square)).toBe(1);
    expect(jaccard(square, rectPolygon(5000, 5000, 6000, 6000))).toBe(0);
  });

  it('measures partial overlap', () => {
    const a = rectPolygon(0, 0, 2000, 1000);
    const b = rectPolygon(1000, 0, 3000, 1000);
    // intersection 1000x1000, union 3000x1000
    expect(jaccard(a, b)).toBeCloseTo(1 / 3, 9);
  });

  it('containmentRatio sees a shrunken but same room', () => {
    const before = rectPolygon(0, 0, 3000, 3000);
    const after = rectPolygon(0, 0, 2000, 3000);
    expect(containmentRatio(after, before)).toBe(1);
    expect(containmentRatio(before, after)).toBeCloseTo(2 / 3, 9);
  });

  it('a wall move keeps the same room the best match', () => {
    const roomA = rectPolygon(115, 115, 2943, 3885);
    const roomB = rectPolygon(3057, 115, 5885, 3885);
    const roomAMoved = rectPolygon(115, 115, 3943, 3885);
    expect(jaccard(roomAMoved, roomA)).toBeGreaterThan(jaccard(roomAMoved, roomB));
  });
});

describe('ring cleanup helpers', () => {
  it('dedupeCollinear drops redundant vertices', () => {
    expect(dedupeCollinear([pt(0, 0), pt(500, 0), pt(1000, 0), pt(1000, 1000), pt(0, 1000)])).toEqual(
      square,
    );
  });

  it('removeSpurs drops out-and-back excursions', () => {
    const ring = [pt(0, 0), pt(1000, 0), pt(1000, 1000), pt(1000, 0), pt(1000, -500)];
    const cleaned = removeSpurs(ring);
    expect(cleaned).not.toContainEqual(pt(1000, 1000));
  });

  it('polygonKey is rotation and orientation invariant', () => {
    const rotated = [pt(1000, 0), pt(1000, 1000), pt(0, 1000), pt(0, 0)];
    expect(polygonKey(square)).toBe(polygonKey(rotated));
    expect(polygonKey(square)).toBe(polygonKey(reversePolygon(square)));
    expect(polygonKey(square)).not.toBe(polygonKey(rectPolygon(0, 0, 1000, 2000)));
  });

  it('polygonsCongruent compares rings up to rotation and direction', () => {
    expect(polygonsCongruent(square, [pt(1000, 0), pt(1000, 1000), pt(0, 1000), pt(0, 0)])).toBe(
      true,
    );
    expect(polygonsCongruent(square, reversePolygon(square))).toBe(true);
    expect(polygonsCongruent(square, rectPolygon(0, 0, 1000, 2000))).toBe(false);
  });
});

describe('bbox', () => {
  it('measures and intersects', () => {
    const b = bbox(square);
    expect(b).toEqual({ minX: 0, minY: 0, maxX: 1000, maxY: 1000 });
    expect(bboxAreaMm2(b)).toBe(1_000_000);
    expect(bboxIntersects(b, bbox(rectPolygon(1000, 1000, 2000, 2000)))).toBe(true);
    expect(bboxIntersects(b, bbox(rectPolygon(1001, 1001, 2000, 2000)))).toBe(false);
  });
});

describe('cross product sign convention', () => {
  it('is positive for a left turn', () => {
    expect(cross(pt(0, 0), pt(100, 0), pt(100, 100))).toBeGreaterThan(0);
    expect(cross(pt(0, 0), pt(100, 0), pt(100, -100))).toBeLessThan(0);
    expect(cross(pt(0, 0), pt(100, 0), pt(200, 0))).toBe(0);
  });
});
