/**
 * Spec for the stair plan symbol — the second flight the model does not store.
 *
 * The contract under test: every turning kind lays its flights out from the
 * ONE riser count the model carries, split exactly as `stairFootprintPolygon`
 * splits it for the slab well, so the drawing and the hole in the floor above
 * cannot disagree about where the stair is. Straight stairs are the negative
 * control — one flight, no landing edge, an arrow with two points.
 */

import { describe, expect, it } from 'vitest';

import { bbox, stairFootprintPolygon, type Pt, type Stair } from '@garh/model';

import { risersBeforeLanding, stairSymbol } from './stairSymbol';

function stair(patch: Partial<Stair>): Stair {
  return {
    id: 'stair_01J3D00000000000000000000A',
    storeyId: 'storey_01J3D00000000000000000000A',
    kind: 'straight',
    origin: { x: 1000, y: 2000 },
    direction: 'N',
    riserMm: 167,
    treadMm: 250,
    widthMm: 1000,
    risersCount: 18,
    landing: null,
    ...patch,
  };
}

function isInteger(p: Pt): boolean {
  return Number.isInteger(p.x) && Number.isInteger(p.y);
}

function allPoints(symbol: ReturnType<typeof stairSymbol>): Pt[] {
  return [
    ...symbol.ringMm,
    ...symbol.arrow,
    ...symbol.treads.flatMap(([a, b]) => [a, b]),
    ...symbol.edges.flatMap(([a, b]) => [a, b]),
    ...symbol.arrowHead.flatMap(([a, b]) => [a, b]),
  ];
}

describe('straight (the negative control)', () => {
  const s = stair({ kind: 'straight' });
  const symbol = stairSymbol(s);

  it('is one flight, no landing edge, no well line', () => {
    expect(symbol.flights).toHaveLength(1);
    expect(symbol.edges).toHaveLength(0);
    expect(symbol.arrow).toHaveLength(2);
  });

  it('draws risers − 1 tread lines across the flight, going north', () => {
    // 18 risers, 17 treads, 17 interior riser lines at 250 mm pitch.
    expect(symbol.treads).toHaveLength(17);
    expect(symbol.treads[0]).toEqual([
      { x: 1000, y: 2250 },
      { x: 2000, y: 2250 },
    ]);
  });

  it('rings exactly the slab well', () => {
    expect(bbox(symbol.ringMm)).toEqual(bbox(stairFootprintPolygon(s)));
  });
});

describe('dogleg: two antiparallel flights either side of a well', () => {
  const s = stair({ kind: 'dogleg', landing: { widthMm: 2115, depthMm: 1000 } });
  const symbol = stairSymbol(s);

  it('splits the count the way the fold splits the slab well', () => {
    expect(risersBeforeLanding(s)).toBe(9);
    expect(symbol.flights.map((f) => f.risers)).toEqual([9, 9]);
  });

  it('sends flight 2 back the other way, on the far side of the well', () => {
    const [first, second] = symbol.flights;
    expect(first?.direction).toBe('N');
    expect(second?.direction).toBe('S');
    // Flight 1 is 8 treads = 2000 mm of going, so the landing starts at y=4000
    // and flight 2 starts there, across 2115 − 1000 = 1115 mm to the right.
    expect(second?.origin).toEqual({ x: 1000 + 2115, y: 4000 });
    expect(second?.goingMm).toBe(2000);
  });

  it('draws riser lines on BOTH flights, each across its own flight only', () => {
    // 8 interior lines on flight 1 and 8 on flight 2.
    expect(symbol.treads).toHaveLength(16);
    const flight2 = symbol.treads.slice(8);
    for (const [a, b] of flight2) {
      expect(Math.min(a.x, b.x)).toBe(1000 + 1115);
      expect(Math.max(a.x, b.x)).toBe(1000 + 2115);
      expect(a.y).toBeLessThan(4000);
    }
  });

  it('draws the landing edge across the full width and the two well lines', () => {
    expect(symbol.edges).toContainEqual([
      { x: 1000, y: 4000 },
      { x: 3115, y: 4000 },
    ]);
    expect(symbol.edges).toHaveLength(3);
  });

  it('runs the UP arrow up flight 1, across the landing and down flight 2', () => {
    expect(symbol.arrow).toHaveLength(4);
    const tail = symbol.arrow[0];
    const head = symbol.arrow[3];
    expect(tail?.x).toBe(1500);
    expect(head?.x).toBe(1000 + 2115 - 500);
    // The head sits half a tread short of flight 2's top riser at y = 2000.
    expect(head?.y).toBe(2000 + 125);
    expect(symbol.arrowHead).toHaveLength(2);
  });

  it('rings exactly the slab well — flight 2 fits inside the footprint', () => {
    expect(bbox(symbol.ringMm)).toEqual(bbox(stairFootprintPolygon(s)));
  });

  it('stays integer everywhere', () => {
    for (const p of allPoints(symbol)) expect(isInteger(p)).toBe(true);
  });
});

describe('U draws like a dogleg (the model carries the same landing block)', () => {
  const dog = stairSymbol(stair({ kind: 'dogleg', landing: { widthMm: 2115, depthMm: 1000 } }));
  const u = stairSymbol(stair({ kind: 'U', landing: { widthMm: 2115, depthMm: 1000 } }));
  it('has identical geometry', () => {
    expect(u.treads).toEqual(dog.treads);
    expect(u.arrow).toEqual(dog.arrow);
    expect(u.ringMm).toEqual(dog.ringMm);
  });
});

describe('L: the return flight turns right off a square landing', () => {
  const s = stair({ kind: 'L', landing: { widthMm: 1000, depthMm: 1000 }, risersCount: 15 });
  const symbol = stairSymbol(s);

  it('turns right — east, for a flight going north', () => {
    expect(symbol.flights.map((f) => f.direction)).toEqual(['N', 'E']);
    expect(symbol.flights.map((f) => f.risers)).toEqual([8, 7]);
  });

  it('starts the return flight at the landing’s far corner', () => {
    // Flight 1: 7 treads = 1750 mm; the landing is 1000 deep beyond that.
    expect(symbol.flights[1]?.origin).toEqual({ x: 2000, y: 3750 });
    expect(symbol.flights[1]?.goingMm).toBe(1500);
  });

  it('draws the return flight’s risers running north–south, east of the landing', () => {
    const returnTreads = symbol.treads.slice(7);
    expect(returnTreads).toHaveLength(6);
    for (const [a, b] of returnTreads) {
      expect(a.x).toBe(b.x);
      expect(a.x).toBeGreaterThan(2000);
      expect(Math.min(a.y, b.y)).toBe(3750);
      expect(Math.max(a.y, b.y)).toBe(4750);
    }
  });

  it('rings everything drawn, which reaches past the fold’s slab well', () => {
    const ring = bbox(symbol.ringMm);
    const well = bbox(stairFootprintPolygon(s));
    expect(ring.maxX).toBe(2000 + 1500);
    expect(well.maxX).toBe(3000);
    expect(ring.maxX).toBeGreaterThan(well.maxX);
    expect(symbol.footprintMm).toEqual(stairFootprintPolygon(s));
  });
});

describe('every direction of travel', () => {
  it('keeps flight 2 inside the footprint for a dogleg facing each way', () => {
    for (const direction of ['N', 'E', 'S', 'W'] as const) {
      const s = stair({ kind: 'dogleg', direction, landing: { widthMm: 2115, depthMm: 1000 } });
      const symbol = stairSymbol(s);
      expect(bbox(symbol.ringMm)).toEqual(bbox(stairFootprintPolygon(s)));
      for (const p of allPoints(symbol)) expect(isInteger(p)).toBe(true);
    }
  });
});
