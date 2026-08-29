/**
 * Running a constraint from the canvas (C-3), against the real stores.
 *
 * The maths has its own suite in `@garh/model`. What is tested here is the app's half,
 * and specifically the three things a quieter implementation swallows:
 *
 *   * the selection ORDER, which is the anchor rule — reverse it and the wrong wall
 *     moves, silently and plausibly;
 *   * a refusal, which must reach the architect as a sentence rather than as a button
 *     that appears to do nothing;
 *   * a rejection by the fold, which must not be reported as success.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NEGATIVE CONTROLS — each applied, the suite run, the failure observed, reverted.
 * ════════════════════════════════════════════════════════════════════════════
 *   A. `selectedWallIds` returns `ids` unfiltered (a selected door joins in)
 *   B. `selectedWallIds` sorts the ids (the anchor rule stops being click order)
 *   C. `runConstraint` returns true without checking `applied.ok`
 *   D. the empty-selection branch stops toasting
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { applyGroup, fixedId, makeEmptyDoc, type Op, type ProjectDoc } from '@garh/model';

import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useUiStore } from '../../stores/ui';
import { runConstraint, selectedWallIds } from './actions';

const STOREY = fixedId('storey', 'ground');
const W = (tag: string): string => fixedId('wall', tag);

/** Two walls: a true horizontal anchor, and one 40 mm out of true above it. */
function twoWalls(): ProjectDoc {
  const ops: Op[] = [
    { type: 'storey.add', payload: { id: STOREY, index: 0, name: 'Ground', heightMm: 3000 } },
    {
      type: 'wall.add',
      payload: {
        id: fixedId('wall', 'anchor'),
        storeyId: STOREY,
        a: { x: 0, y: 0 },
        b: { x: 4000, y: 0 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'wall.add',
      payload: {
        id: fixedId('wall', 'skew'),
        storeyId: STOREY,
        a: { x: 0, y: 2500 },
        b: { x: 4000, y: 2540 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
  ];
  return applyGroup(makeEmptyDoc(), ops, fixedId('group', 'setup')).model;
}

function wall(tag: string) {
  const found = useModelStore.getState().doc.house.walls.find((item) => item.id === W(tag));
  if (found === undefined) throw new Error(`no wall ${tag}`);
  return found;
}

function toastTitles(): string[] {
  return useUiStore.getState().toasts.map((toast) => toast.title);
}

beforeEach(() => {
  useModelStore.setState({ doc: twoWalls(), undoStack: [], redoStack: [], divergedAt: null });
  useSelectionStore.getState().clear();
  useUiStore.setState({ toasts: [] });
});

describe('the selection is the input', () => {
  it('ignores everything in the selection that is not a wall', () => {
    useSelectionStore.setState({
      ids: ['some-door', W('anchor'), 'some-room'],
      kinds: { 'some-door': 'opening', [W('anchor')]: 'wall', 'some-room': 'room' },
    });
    expect(selectedWallIds()).toEqual([W('anchor')]);
  });

  it('keeps click order, because the order IS the anchor rule', () => {
    useSelectionStore.setState({
      ids: [W('skew'), W('anchor')],
      kinds: { [W('skew')]: 'wall', [W('anchor')]: 'wall' },
    });
    expect(selectedWallIds()).toEqual([W('skew'), W('anchor')]);
  });
});

describe('applying', () => {
  it('straightens a wall and records one undoable step', () => {
    useSelectionStore.setState({ ids: [W('skew')], kinds: { [W('skew')]: 'wall' } });
    expect(runConstraint('horizontal')).toBe(true);
    expect(wall('skew').a.y).toBe(wall('skew').b.y);
    expect(useModelStore.getState().undoStack).toHaveLength(1);
  });

  it('the first wall selected is the one that stays put', () => {
    const before = { ...wall('anchor') };
    useSelectionStore.setState({
      ids: [W('anchor'), W('skew')],
      kinds: { [W('anchor')]: 'wall', [W('skew')]: 'wall' },
    });
    expect(runConstraint('parallel')).toBe(true);
    expect(wall('anchor').a).toEqual(before.a);
    expect(wall('anchor').b).toEqual(before.b);
    expect(wall('skew').a.y).toBe(wall('skew').b.y);
  });

  it('and reversing the selection moves the OTHER wall', () => {
    // The other half of the rule, so "the anchor did not move" cannot pass by accident
    // on a solver that never moves anything at all.
    const before = { ...wall('skew') };
    useSelectionStore.setState({
      ids: [W('skew'), W('anchor')],
      kinds: { [W('skew')]: 'wall', [W('anchor')]: 'wall' },
    });
    expect(runConstraint('parallel')).toBe(true);
    expect(wall('skew').a).toEqual(before.a);
    expect(wall('skew').b).toEqual(before.b);
  });

  it('undo puts the wall back exactly', () => {
    const before = { ...wall('skew') };
    useSelectionStore.setState({ ids: [W('skew')], kinds: { [W('skew')]: 'wall' } });
    runConstraint('horizontal');
    useModelStore.getState().undo();
    expect(wall('skew').a).toEqual(before.a);
    expect(wall('skew').b).toEqual(before.b);
  });
});

describe('saying what happened', () => {
  it('explains itself when nothing is selected', () => {
    expect(runConstraint('horizontal')).toBe(false);
    expect(toastTitles()).toContain('Select a wall first');
  });

  it('explains a refusal rather than doing nothing visible', () => {
    // Already horizontal: there is genuinely nothing to do, and a button that appears
    // dead is indistinguishable from a broken one.
    useSelectionStore.setState({ ids: [W('anchor')], kinds: { [W('anchor')]: 'wall' } });
    expect(runConstraint('horizontal')).toBe(false);
    expect(useUiStore.getState().toasts.at(-1)?.description).toContain('already');
  });

  it('asks for a second wall when an anchored constraint has only one', () => {
    useSelectionStore.setState({ ids: [W('skew')], kinds: { [W('skew')]: 'wall' } });
    expect(runConstraint('parallel')).toBe(false);
    expect(useUiStore.getState().toasts.at(-1)?.description).toContain('Select two walls');
  });

  it('reports failure when the fold rejects the move', () => {
    // Collinear here would slide the skew wall straight onto the anchor. The model
    // refuses that (WALL_DUPLICATE) — and this must come back false, not true, or the
    // architect is told an edit landed that did not.
    useSelectionStore.setState({
      ids: [W('anchor'), W('skew')],
      kinds: { [W('anchor')]: 'wall', [W('skew')]: 'wall' },
    });
    expect(runConstraint('collinear')).toBe(false);
    expect(useModelStore.getState().undoStack).toHaveLength(0);
  });
});
