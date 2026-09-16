/**
 * terrace.test.ts — the exposed-roof derivation, case by case, on integer
 * rectangles an architect would actually draw: identical floors (nothing),
 * a rear set-back (one face, three parapet edges), a set-back on every
 * side (the hole case), an overhanging upper floor (nothing), and an upper
 * storey with no walls yet (everything).
 */

import { describe, expect, it } from 'vitest';

import { polygonAreaMm2, rectPolygon, type Polygon } from '@garh/model';

import { exposedTerraceFaces } from './terrace';

const GF: Polygon = rectPolygon(0, 0, 6000, 4000);

describe('exposedTerraceFaces', () => {
  it('identical outlines expose nothing', () => {
    expect(exposedTerraceFaces(GF, rectPolygon(0, 0, 6000, 4000))).toEqual([]);
  });

  it('a first floor set back at the rear exposes one face with three parapet edges', () => {
    // FF shares the south edge and stops at y=2500.
    const faces = exposedTerraceFaces(GF, rectPolygon(0, 0, 6000, 2500));
    expect(faces).toHaveLength(1);
    const face = faces[0]!;
    expect(face.hole).toBeNull();
    expect(polygonAreaMm2(face.ring as Polygon)).toBe(6000 * 1500);
    // Every vertex of the face lies in the exposed strip.
    for (const p of face.ring) expect(p.y).toBeGreaterThanOrEqual(2500);
    // Exactly one edge is the FF's north wall (y = 2500 at both ends): no
    // parapet there — the wall is the parapet. The other three get one.
    const covered = face.ring.map((p, i) => {
      const q = face.ring[(i + 1) % face.ring.length]!;
      return p.y === 2500 && q.y === 2500;
    });
    expect(covered.filter(Boolean)).toHaveLength(1);
    face.exposedEdges.forEach((exposed, i) => {
      expect(exposed).toBe(!covered[i]);
    });
    expect(face.exposedEdges.filter(Boolean)).toHaveLength(3);
  });

  it('a first floor inset on every side is the hole case: whole ring, upper ring as the hole', () => {
    const faces = exposedTerraceFaces(GF, rectPolygon(1000, 1000, 5000, 3000));
    expect(faces).toHaveLength(1);
    const face = faces[0]!;
    expect(polygonAreaMm2(face.ring as Polygon)).toBe(6000 * 4000);
    expect(face.hole).not.toBeNull();
    expect(polygonAreaMm2(face.hole as Polygon)).toBe(4000 * 2000);
    expect(face.exposedEdges).toEqual([true, true, true, true]);
  });

  it('an overhanging first floor exposes nothing on the ground floor', () => {
    expect(exposedTerraceFaces(GF, rectPolygon(-500, -500, 6500, 4500))).toEqual([]);
  });

  it('no upper outline yet exposes the whole lower outline, parapet all round', () => {
    const faces = exposedTerraceFaces(GF, []);
    expect(faces).toHaveLength(1);
    expect(polygonAreaMm2(faces[0]!.ring as Polygon)).toBe(6000 * 4000);
    expect(faces[0]!.exposedEdges.every(Boolean)).toBe(true);
    expect(faces[0]!.hole).toBeNull();
  });

  it('an L-shaped set-back (two corners cut) yields two faces whose areas sum exactly', () => {
    // FF keeps the south-west 4000×2500 block only ⇒ exposed: the east strip
    // (2000×4000) and the north-west strip (4000×1500), touching at a corner.
    const ff: Polygon = rectPolygon(0, 0, 4000, 2500);
    const faces = exposedTerraceFaces(GF, ff);
    const total = faces.reduce((sum, f) => sum + polygonAreaMm2(f.ring as Polygon), 0);
    expect(total).toBe(6000 * 4000 - 4000 * 2500);
    for (const face of faces) {
      expect(face.hole).toBeNull();
      expect(face.exposedEdges.some(Boolean)).toBe(true);
      expect(face.exposedEdges.every(Boolean)).toBe(false); // each touches the FF
    }
  });

  it('a degenerate lower outline exposes nothing', () => {
    expect(exposedTerraceFaces([], GF)).toEqual([]);
  });
});
