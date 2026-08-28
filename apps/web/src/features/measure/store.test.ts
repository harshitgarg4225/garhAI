/**
 * Spec for the measurement list — "persisted until dismissed", which is the
 * half of the feature a live readout cannot give you.
 *
 * Also pins the two consistency rules that are easy to break and awkward to
 * notice: dismissing the selected measurement clears the selection (otherwise
 * the panel keeps highlighting a row that is gone and the layer keeps drawing a
 * highlight for geometry it no longer has), and a commit clears the draft
 * (otherwise the same measurement is drawn twice, once live and once saved).
 */

import { beforeEach, describe, expect, it } from 'vitest';

import type { Pt } from '@garh/model';

import { measurementsForStorey, resetMeasureStore, useMeasureStore } from './store';
import type { Measurement } from './types';

const P = (x: number, y: number): Pt => ({ x, y });

function make(id: string, storeyId: string | null = 'storey_GF'): Measurement {
  return {
    id,
    kind: 'distance',
    points: [P(0, 0), P(3000, 4000)],
    storeyId,
    createdAt: 0,
  };
}

beforeEach(() => {
  resetMeasureStore();
});

describe('the list', () => {
  it('starts empty, visible, and measuring distances', () => {
    const s = useMeasureStore.getState();
    expect(s.measurements).toEqual([]);
    expect(s.kind).toBe('distance');
    expect(s.visible).toBe(true);
  });

  it('keeps measurements in the order they were taken', () => {
    const store = useMeasureStore.getState();
    store.add(make('measure:1'));
    store.add(make('measure:2'));
    expect(useMeasureStore.getState().measurements.map((m) => m.id)).toEqual([
      'measure:1',
      'measure:2',
    ]);
  });

  it('selects a new measurement and clears the draft it came from', () => {
    const store = useMeasureStore.getState();
    store.setDraft({ kind: 'distance', points: [P(0, 0)], cursor: P(1, 1), willClose: false });
    store.add(make('measure:1'));
    expect(useMeasureStore.getState().selectedId).toBe('measure:1');
    expect(useMeasureStore.getState().draft).toBeNull();
  });

  it('dismisses one, and lets go of the selection with it', () => {
    const store = useMeasureStore.getState();
    store.add(make('measure:1'));
    store.add(make('measure:2'));
    expect(useMeasureStore.getState().selectedId).toBe('measure:2');

    useMeasureStore.getState().dismiss('measure:2');
    expect(useMeasureStore.getState().measurements.map((m) => m.id)).toEqual(['measure:1']);
    expect(useMeasureStore.getState().selectedId).toBeNull();
  });

  it('keeps a selection that was not the one dismissed', () => {
    const store = useMeasureStore.getState();
    store.add(make('measure:1'));
    store.add(make('measure:2'));
    useMeasureStore.getState().select('measure:1');
    useMeasureStore.getState().dismiss('measure:2');
    expect(useMeasureStore.getState().selectedId).toBe('measure:1');
  });

  it('dismisses everything at once', () => {
    const store = useMeasureStore.getState();
    store.add(make('measure:1'));
    store.add(make('measure:2'));
    useMeasureStore.getState().dismissAll();
    expect(useMeasureStore.getState().measurements).toEqual([]);
    expect(useMeasureStore.getState().selectedId).toBeNull();
  });

  it('clears a stale refusal when the mode changes', () => {
    useMeasureStore.getState().setNotice('Those corners are in a straight line — no area.');
    useMeasureStore.getState().setKind('distance');
    expect(useMeasureStore.getState().notice).toBeNull();
  });
});

describe('storey filtering', () => {
  it('shows this storey’s measurements and the storey-agnostic ones', () => {
    const list = [
      make('measure:g', 'storey_GF'),
      make('measure:f', 'storey_FF'),
      make('measure:x', null),
    ];
    expect(measurementsForStorey(list, 'storey_GF').map((m) => m.id)).toEqual([
      'measure:g',
      'measure:x',
    ]);
    expect(measurementsForStorey(list, 'storey_FF').map((m) => m.id)).toEqual([
      'measure:f',
      'measure:x',
    ]);
  });

  it('shows only the agnostic ones before a storey is known', () => {
    const list = [make('measure:g', 'storey_GF'), make('measure:x', null)];
    expect(measurementsForStorey(list, null).map((m) => m.id)).toEqual(['measure:x']);
  });
});
