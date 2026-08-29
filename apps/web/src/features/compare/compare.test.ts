/**
 * Version compare, the app's half (C-8).
 *
 * The diff itself is Python and has its own suite. What is tested here is the state
 * machine around it, and specifically the two ways this feature can lie:
 *
 *   * showing a change list captioned with versions it no longer describes — the classic
 *     stale-view bug, which is believed because it looks like a result;
 *   * drawing a box on the wrong storey, which points an architect at a change that is
 *     not on the drawing in front of them.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NEGATIVE CONTROLS — each applied, the suite run, the failure observed, reverted.
 * ════════════════════════════════════════════════════════════════════════════
 *   A. `setA`/`setB` keep the previous `result` (stale caption)
 *   B. `compareBoxesForStorey` drops the storey filter (boxes from another floor)
 *   C. `compareBoxesForStorey` ignores `overlayVisible` (Hide on plan does nothing)
 *   D. `compareBoxesForStorey` stops filtering on `box.length === 4`
 *      (an unplaced furniture change becomes a degenerate box at the origin)
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../lib/api';
import { loadCompare } from './api';
import { compareBoxesForStorey, useCompareStore } from './store';

const PROJECT = '11111111-1111-4111-8111-111111111111';

function result(overrides: Record<string, unknown> = {}) {
  return {
    projectId: PROJECT,
    a: { id: 'v1' },
    b: { id: 'v2' },
    summary: '1 modified (wall)',
    counts: { modified: 1 },
    storeyIds: ['ground', 'first'],
    changes: [
      {
        elementId: 'wall-1',
        kind: 'wall',
        change: 'modified',
        storeyId: 'ground',
        box: [0, 0, 4000, 230],
        fields: ['a', 'b'],
        derived: false,
      },
      {
        elementId: 'wall-2',
        kind: 'wall',
        change: 'modified',
        storeyId: 'first',
        box: [0, 3000, 4000, 3230],
        fields: ['a'],
        derived: false,
      },
      // Unplaced changes come back in `changes` with an empty box in some shapes; a
      // zero-length box must never be drawn as a rectangle at the origin.
      {
        elementId: 'sofa-1',
        kind: 'furniture',
        change: 'modified',
        storeyId: 'ground',
        box: [],
        fields: ['pt'],
        derived: false,
      },
    ],
    unplaced: [{ elementId: 'sofa-1', kind: 'furniture', change: 'modified' }],
    comparedKinds: ['wall', 'furniture'],
    excludedKinds: { slab: 'derived and floor-sized' },
    areasA: null,
    areasB: null,
    ...overrides,
  } as never;
}

beforeEach(() => {
  useCompareStore.getState().reset();
  vi.restoreAllMocks();
});

describe('choosing the two sides', () => {
  it('drops a loaded result when either side changes', () => {
    // The stale-view bug: a change list still on screen, captioned with the versions it
    // no longer describes. It looks like a result, so it is believed.
    useCompareStore.setState({ a: 'v1', b: 'v2', result: result() });
    useCompareStore.getState().setB('v3');
    expect(useCompareStore.getState().result).toBeNull();

    useCompareStore.setState({ result: result() });
    useCompareStore.getState().setA('v4');
    expect(useCompareStore.getState().result).toBeNull();
  });

  it('does not fetch until both sides are chosen', async () => {
    const spy = vi.spyOn(api.versions, 'compare');
    useCompareStore.getState().setA('v1');
    await loadCompare(PROJECT);
    expect(spy).not.toHaveBeenCalled();
  });

  it('fetches with both ids, in the order they were chosen', async () => {
    const spy = vi.spyOn(api.versions, 'compare').mockResolvedValue(result());
    useCompareStore.getState().setA('v1');
    useCompareStore.getState().setB('v2');
    await loadCompare(PROJECT);
    expect(spy).toHaveBeenCalledWith(PROJECT, 'v1', 'v2', {});
    expect(useCompareStore.getState().result).not.toBeNull();
    expect(useCompareStore.getState().loading).toBe(false);
  });

  it('turns a failure into a sentence and stops loading', async () => {
    vi.spyOn(api.versions, 'compare').mockRejectedValue(new Error('boom'));
    useCompareStore.setState({ a: 'v1', b: 'v2' });
    await loadCompare(PROJECT);
    const state = useCompareStore.getState();
    expect(state.loading).toBe(false);
    expect(state.error).toContain('Could not compare');
  });
});

describe('what gets drawn on the plan', () => {
  beforeEach(() => {
    useCompareStore.setState({ a: 'v1', b: 'v2', result: result(), overlayVisible: true });
  });

  it('draws only the boxes for the storey on screen', () => {
    // A box drawn on the wrong floor points an architect at a change that is not on the
    // drawing in front of them.
    const boxes = compareBoxesForStorey(useCompareStore.getState(), 'ground');
    expect(boxes).toEqual([[0, 0, 4000, 230]]);
  });

  it('and the other storey gets its own', () => {
    // Negative control on the filter: "one box" must be a fact about the storey, not
    // about the filter returning a single element whatever it is asked.
    expect(compareBoxesForStorey(useCompareStore.getState(), 'first')).toEqual([
      [0, 3000, 4000, 3230],
    ]);
  });

  it('never draws a change that has no box', () => {
    // A moved sofa has no footprint in the model. An empty box drawn anyway is a
    // degenerate rectangle at the origin — a mark on the plan pointing at nothing.
    const boxes = compareBoxesForStorey(useCompareStore.getState(), 'ground');
    expect(boxes.every((box) => box.length === 4)).toBe(true);
    expect(boxes).toHaveLength(1);
  });

  it('draws nothing when the overlay is hidden', () => {
    useCompareStore.getState().toggleOverlay();
    expect(compareBoxesForStorey(useCompareStore.getState(), 'ground')).toEqual([]);
  });

  it('draws nothing before a compare has been run', () => {
    useCompareStore.getState().reset();
    expect(compareBoxesForStorey(useCompareStore.getState(), 'ground')).toEqual([]);
  });

  it('draws nothing when no storey is active', () => {
    expect(compareBoxesForStorey(useCompareStore.getState(), null)).toEqual([]);
  });
});
