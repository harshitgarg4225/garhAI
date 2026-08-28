/**
 * Spec for the layer store.
 *
 * The behaviours worth pinning are the ones a reviewer cannot see by reading:
 * isolate remembers the view you came from (not "everything on"), a hand edit
 * ends isolate honestly, a second project does not inherit the first one's
 * layers, and the panel keeps working when storage is unusable.
 *
 * Where a change is supposed to be visible on the canvas, the assertion goes
 * through `planLayerViewFor` / `blockedPicksFor` — the same derivations the
 * canvas consumes — rather than through the raw booleans. `mapping.test.ts`
 * carries the "and that really changes the drawing" half; this file's job is
 * that the state machine feeding it is right.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { makeTwoRoomPlanWithOpenings } from '@garh/model';

import { DRAWING_LAYER_NAMES } from './layerSpecs';
import { defaultLayerState, readLayerState, storageKey, type LayerScope } from './persist';
import {
  blockedPicksFor,
  layerRows,
  planLayerViewFor,
  selectHiddenCount,
  selectLockedCount,
  useLayerStore,
} from './store';

const HOUSE = makeTwoRoomPlanWithOpenings().house;
const SCOPE: LayerScope = { userId: 'user_abc', projectId: 'project_xyz' };
const OTHER: LayerScope = { userId: 'user_abc', projectId: 'project_two' };

beforeEach(() => {
  globalThis.localStorage.clear();
  useLayerStore.setState({
    scope: null,
    ...defaultLayerState(),
    isolated: null,
    preIsolate: null,
  });
});

const state = (): ReturnType<typeof useLayerStore.getState> => useLayerStore.getState();

describe('opening state', () => {
  it('shows every layer and locks none', () => {
    expect(selectHiddenCount(state())).toBe(0);
    expect(selectLockedCount(state())).toBe(0);
  });

  it('draws the model untouched — the same object, so nothing re-memoises', () => {
    expect(planLayerViewFor(HOUSE, state()).house).toBe(HOUSE);
    expect(blockedPicksFor(HOUSE, state()).ids.size).toBe(0);
  });
});

describe('visibility', () => {
  it('hiding a layer takes its elements out of the drawn model', () => {
    state().setVisible('A-DOOR', false);
    const view = planLayerViewFor(HOUSE, state());
    expect(view.house.openings.some((o) => o.kind === 'door')).toBe(false);
    expect(view.house.openings.some((o) => o.kind === 'window')).toBe(true);
  });

  it('hiding a layer also makes its elements unpickable', () => {
    const doorId = HOUSE.openings.find((o) => o.kind === 'door')?.id;
    expect(doorId).toBeDefined();
    state().setVisible('A-DOOR', false);
    expect(blockedPicksFor(HOUSE, state()).ids.has(doorId as string)).toBe(true);
  });

  it('toggles', () => {
    state().toggleVisible('A-STAIR');
    expect(state().visible['A-STAIR']).toBe(false);
    state().toggleVisible('A-STAIR');
    expect(state().visible['A-STAIR']).toBe(true);
  });

  it('setting the value it already has changes nothing', () => {
    const before = state().visible;
    state().setVisible('A-WALL', true);
    expect(state().visible).toBe(before);
  });
});

describe('lock', () => {
  it('keeps the layer drawn but refuses its picks', () => {
    const wallId = HOUSE.walls[0]?.id;
    expect(wallId).toBeDefined();
    state().setLocked('A-WALL', true);

    // Still drawn — that is the whole difference between lock and hide.
    expect(planLayerViewFor(HOUSE, state()).house).toBe(HOUSE);
    expect(blockedPicksFor(HOUSE, state()).ids.has(wallId as string)).toBe(true);
  });

  it('is independent of visibility', () => {
    state().setLocked('A-DIM', true);
    expect(state().visible['A-DIM']).toBe(true);
    expect(selectHiddenCount(state())).toBe(0);
    expect(selectLockedCount(state())).toBe(1);
  });
});

describe('isolate', () => {
  it('shows one layer and hides the rest', () => {
    state().isolate('A-WALL');
    expect(state().isolated).toBe('A-WALL');
    for (const name of DRAWING_LAYER_NAMES) {
      expect(state().visible[name], name).toBe(name === 'A-WALL');
    }
  });

  it('restores exactly what was visible before, not "everything on"', () => {
    // The point of the snapshot: a deliberate choice made before isolating
    // must survive the round trip.
    state().setVisible('A-DIM', false);
    state().isolate('A-WALL');
    state().exitIsolate();

    expect(state().visible['A-DIM']).toBe(false);
    expect(state().visible['A-WALL']).toBe(true);
    expect(state().isolated).toBeNull();
  });

  it('keeps the ORIGINAL snapshot when walking from one layer to the next', () => {
    state().setVisible('A-DIM', false);
    state().isolate('A-WALL');
    state().isolate('A-DOOR');
    state().isolate('A-STAIR');
    state().exitIsolate();

    // Not "only A-DOOR was visible" — the view we started from.
    expect(state().visible['A-DIM']).toBe(false);
    expect(selectHiddenCount(state())).toBe(1);
  });

  it('toggling the isolated layer leaves isolate', () => {
    state().toggleIsolate('A-WALL');
    expect(state().isolated).toBe('A-WALL');
    state().toggleIsolate('A-WALL');
    expect(state().isolated).toBeNull();
    expect(selectHiddenCount(state())).toBe(0);
  });

  it('a hand edit ends isolate rather than pretending it can still restore', () => {
    state().isolate('A-WALL');
    state().setVisible('A-DOOR', true);
    expect(state().isolated).toBeNull();
    expect(state().preIsolate).toBeNull();
  });

  it('refuses to isolate a layer the plan does not draw', () => {
    // Isolating the title block would blank the canvas and put nothing in its
    // place. The panel does not offer it; the store refuses it as well.
    state().isolate('A-TITL');
    expect(state().isolated).toBeNull();
    expect(selectHiddenCount(state())).toBe(0);
  });

  it('exiting when not isolated does nothing', () => {
    const before = state().visible;
    state().exitIsolate();
    expect(state().visible).toBe(before);
  });

  it('really does draw only the isolated layer', () => {
    state().isolate('A-STAIR');
    const view = planLayerViewFor(HOUSE, state());
    expect(view.house.walls).toEqual([]);
    expect(view.house.openings).toEqual([]);
    expect(view.showRooms).toBe(false);
    expect(view.showDimensions).toBe(false);
    expect(view.showRoomTags).toBe(false);
  });
});

describe('showAll / reset', () => {
  it('showAll turns everything on and leaves locks alone', () => {
    state().setVisible('A-DIM', false);
    state().setLocked('A-WALL', true);
    state().showAll();
    expect(selectHiddenCount(state())).toBe(0);
    expect(state().locked['A-WALL']).toBe(true);
  });

  it('resetLayers clears locks too, and forgets the stored payload', () => {
    state().bind(SCOPE);
    state().setLocked('A-WALL', true);
    expect(readLayerState(SCOPE)).not.toBeNull();

    state().resetLayers();
    expect(selectLockedCount(state())).toBe(0);
    expect(readLayerState(SCOPE)).toBeNull();
  });
});

describe('persistence', () => {
  it('writes on every change once bound', () => {
    state().bind(SCOPE);
    state().setVisible('A-DIM', false);
    expect(readLayerState(SCOPE)?.visible['A-DIM']).toBe(false);

    state().setLocked('A-WALL', true);
    expect(readLayerState(SCOPE)?.locked['A-WALL']).toBe(true);
  });

  it('reloads what was stored when the project is opened again', () => {
    state().bind(SCOPE);
    state().setVisible('A-TEXT', false);
    state().unbind();

    useLayerStore.setState({ ...defaultLayerState(), isolated: null, preIsolate: null });
    state().bind(SCOPE);
    expect(state().visible['A-TEXT']).toBe(false);
  });

  it('does not carry one project’s layers into another', () => {
    state().bind(SCOPE);
    state().setVisible('A-TEXT', false);

    state().unbind();
    state().bind(OTHER);
    expect(state().visible['A-TEXT']).toBe(true);
    expect(selectHiddenCount(state())).toBe(0);
  });

  it('does not carry an isolate across a project switch', () => {
    state().bind(SCOPE);
    state().isolate('A-WALL');
    state().unbind();
    state().bind(OTHER);
    expect(state().isolated).toBeNull();
    expect(selectHiddenCount(state())).toBe(0);
  });

  it('never persists the isolate snapshot', () => {
    state().bind(SCOPE);
    state().isolate('A-WALL');
    const raw = globalThis.localStorage.getItem(storageKey(SCOPE)) ?? '';
    expect(raw).not.toContain('preIsolate');
    expect(raw).not.toContain('isolated');
  });

  it('writes nothing while unbound, and still works', () => {
    state().setVisible('A-DIM', false);
    expect(state().visible['A-DIM']).toBe(false);
    expect(globalThis.localStorage.length).toBe(0);
  });

  it('re-binding the same scope does not clobber in-memory state', () => {
    state().bind(SCOPE);
    state().setVisible('A-DIM', false);
    state().bind(SCOPE);
    expect(state().visible['A-DIM']).toBe(false);
  });
});

describe('rows for the panel', () => {
  it('gives one row per layer, in the layers.py order', () => {
    expect(layerRows(state()).map((r) => r.name)).toEqual([...DRAWING_LAYER_NAMES]);
  });

  it('marks A-TITL as having no canvas effect, with a reason', () => {
    const titl = layerRows(state()).find((r) => r.name === 'A-TITL');
    expect(titl?.actsOnCanvas).toBe(false);
    expect(titl?.unavailableReason).toBeTruthy();
  });

  it('marks every other layer as live, with no reason to show', () => {
    for (const row of layerRows(state())) {
      if (row.name === 'A-TITL') continue;
      expect(row.actsOnCanvas, row.name).toBe(true);
      expect(row.unavailableReason, row.name).toBeNull();
    }
  });

  it('carries the current visibility, lock and isolate state', () => {
    state().setLocked('A-WALL', true);
    state().isolate('A-DOOR');
    const rows = layerRows(state());
    expect(rows.find((r) => r.name === 'A-WALL')?.locked).toBe(true);
    expect(rows.find((r) => r.name === 'A-DOOR')?.isolated).toBe(true);
    expect(rows.find((r) => r.name === 'A-DOOR')?.visible).toBe(true);
    expect(rows.find((r) => r.name === 'A-WIND')?.visible).toBe(false);
  });
});
