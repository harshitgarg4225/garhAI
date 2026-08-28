/**
 * Spec for where the numbers sit.
 *
 * The assertion with teeth is the last one: label ids must be unique. They are
 * React keys, and two labels sharing a key means React renders ONE of them —
 * a number silently missing from the drawing, with no error anywhere.
 */

import { describe, expect, it } from 'vitest';

import type { Pt } from '@garh/model';

import { draftLabels, measurementLabels } from './labels';
import type { Measurement } from './types';

const P = (x: number, y: number): Pt => ({ x, y });

function measurement(kind: Measurement['kind'], points: readonly Pt[]): Measurement {
  return { id: 'measure:1', kind, points, storeyId: null, createdAt: 0 };
}

describe('distance labels', () => {
  it('gives a single leg ONE label, at its midpoint', () => {
    // Printing the length twice on top of itself is how a plan turns into a
    // grey wash of digits.
    const labels = measurementLabels(measurement('distance', [P(0, 0), P(3000, 4000)]), 'm');
    expect(labels).toHaveLength(1);
    expect(labels[0]?.emphasis).toBe(true);
    expect(labels[0]?.text).toBe('5.00 m');
    expect(labels[0]?.atMm).toEqual(P(1500, 2000));
  });

  it('gives a chain a label per leg plus the total at the far end', () => {
    const chain = measurement('distance', [P(0, 0), P(3000, 4000), P(3000, 9000)]);
    const labels = measurementLabels(chain, 'm');
    expect(labels.map((l) => l.text)).toEqual(['5.00 m', '5.00 m', '10.00 m']);
    expect(labels.filter((l) => l.emphasis)).toHaveLength(1);
    // The total sits at the last point, clear of the leg labels in the middle.
    expect(labels.at(-1)?.atMm).toEqual(P(3000, 9000));
  });
});

describe('angle labels', () => {
  it('puts the angle at the corner and a length on each arm', () => {
    const labels = measurementLabels(measurement('angle', [P(3000, 0), P(0, 0), P(0, 4000)]), 'm');
    const headline = labels.find((l) => l.emphasis);
    expect(headline?.text).toBe('90.0°');
    expect(headline?.atMm).toEqual(P(0, 0));
    expect(labels.filter((l) => !l.emphasis).map((l) => l.text)).toEqual(['3.00 m', '4.00 m']);
  });
});

describe('area labels', () => {
  const rect = measurement('area', [P(0, 0), P(6000, 0), P(6000, 4000), P(0, 4000)]);

  it('labels every edge INCLUDING the implied closing one', () => {
    const legs = measurementLabels(rect, 'm').filter((l) => !l.emphasis);
    expect(legs).toHaveLength(4);
    expect(legs.map((l) => l.text)).toEqual(['6.00 m', '4.00 m', '6.00 m', '4.00 m']);
  });

  it('puts both units at the centroid', () => {
    const headline = measurementLabels(rect, 'm').find((l) => l.emphasis);
    expect(headline?.text).toBe('24.00 m² · 258.3 sq ft');
    expect(headline?.atMm).toEqual(P(3000, 2000));
  });
});

describe('draft labels', () => {
  it('include the rubber-band leg, so the number moves with the pointer', () => {
    const labels = draftLabels(
      { kind: 'distance', points: [P(0, 0)], cursor: P(3000, 4000), willClose: false },
      'm',
    );
    expect(labels.map((l) => l.text)).toEqual(['5.00 m']);
  });

  it('say nothing at all before there is a second point', () => {
    expect(
      draftLabels({ kind: 'distance', points: [P(0, 0)], cursor: null, willClose: false }, 'm'),
    ).toEqual([]);
  });
});

describe('label ids', () => {
  it('are unique across every measurement on screen and the draft', () => {
    const ids = [
      ...measurementLabels(measurement('distance', [P(0, 0), P(1000, 0), P(1000, 1000)]), 'm'),
      ...measurementLabels(
        { ...measurement('area', [P(0, 0), P(6000, 0), P(6000, 4000)]), id: 'measure:2' },
        'm',
      ),
      ...draftLabels(
        { kind: 'distance', points: [P(0, 0)], cursor: P(500, 0), willClose: false },
        'm',
      ),
    ].map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
