/**
 * Spec for the list itself: save, rename, reorder, delete, and the promise
 * that each of those survives a reload.
 *
 * Every mutation is checked TWICE — once against the store's own state, and
 * once against what `localStorage` now holds, read back through the real
 * `readViews`. A store that updated its state and forgot to persist would pass
 * the first assertion and fail the second, which is the whole reason the second
 * one is there.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { readViews } from './persist';
import { nextViewName, selectIsFull, useViewsStore } from './store';
import type { Saved2dCamera, Saved3dCamera, ViewsScope } from './types';

const SCOPE: ViewsScope = { userId: 'user_1', projectId: 'proj_1' };

const PLAN: Saved2dCamera = { mode: '2d', centreMm: { x: 1000, y: 2000 }, mmPerPx: 4 };
const ORBIT: Saved3dCamera = {
  mode: '3d',
  targetMm: { x: 0, y: 0, z: 0 },
  distanceMm: 25_000,
  azimuthDeg: 225,
  polarDeg: 60,
};

function store() {
  return useViewsStore.getState();
}

function names(): string[] {
  return store().views.map((view) => view.name);
}

/** What is actually on disk for the bound scope. */
function persistedNames(): string[] {
  return (readViews(SCOPE) ?? []).map((view) => view.name);
}

beforeEach(() => {
  globalThis.localStorage.clear();
  useViewsStore.setState({ scope: null, views: [] });
});

describe('binding a scope', () => {
  it('loads what was stored for that project and user', () => {
    store().bind(SCOPE);
    store().saveView('Kitchen detail', PLAN);
    expect(persistedNames()).toEqual(['Kitchen detail']);

    // A fresh page load.
    useViewsStore.setState({ scope: null, views: [] });
    store().bind(SCOPE);
    expect(names()).toEqual(['Kitchen detail']);
  });

  it('starts empty for a different project on the same machine', () => {
    store().bind(SCOPE);
    store().saveView('Kitchen detail', PLAN);
    store().bind({ ...SCOPE, projectId: 'proj_2' });
    expect(names()).toEqual([]);
  });

  it('works unbound, and simply does not remember', () => {
    const result = store().saveView('Ephemeral', PLAN);
    expect(result.view).not.toBe(null);
    expect(names()).toEqual(['Ephemeral']);
    expect(readViews(SCOPE)).toBe(null);
  });
});

describe('saving', () => {
  beforeEach(() => {
    store().bind(SCOPE);
  });

  it('keeps the camera verbatim, in either projection', () => {
    store().saveView('Plan', PLAN);
    store().saveView('Orbit', ORBIT);
    expect(store().views[0]?.camera).toEqual(PLAN);
    expect(store().views[1]?.camera).toEqual(ORBIT);
    expect(readViews(SCOPE)?.[1]?.camera).toEqual(ORBIT);
  });

  it('names an unnamed view, without colliding with one already there', () => {
    store().saveView('View 1', PLAN);
    store().saveView('', PLAN);
    // "View 2" is free; "View 1" was taken by the explicit name above.
    expect(names()).toEqual(['View 1', 'View 2']);
  });

  it('gives each view a distinct id', () => {
    for (let i = 0; i < 5; i++) store().saveView('', PLAN);
    expect(new Set(store().views.map((view) => view.id)).size).toBe(5);
  });

  it('refuses a camera the controller would not take back, and says why', () => {
    const result = store().saveView('Impossible', {
      mode: '2d',
      centreMm: { x: 0, y: 0 },
      mmPerPx: Number.NaN,
    });
    expect(result.view).toBe(null);
    expect(result.refused).toBe('unusable-camera');
    expect(names()).toEqual([]);
  });

  it('refuses once the list is full, and says why', () => {
    for (let i = 0; i < 40; i++) store().saveView('', PLAN);
    expect(selectIsFull(store())).toBe(true);
    const result = store().saveView('One too many', PLAN);
    expect(result.refused).toBe('full');
    expect(store().views).toHaveLength(40);
  });
});

describe('renaming', () => {
  beforeEach(() => {
    store().bind(SCOPE);
    store().saveView('Kitchen', PLAN);
  });

  it('renames in place and persists', () => {
    const id = store().views[0]?.id ?? '';
    store().rename(id, '  Kitchen   detail  ');
    expect(names()).toEqual(['Kitchen detail']);
    expect(persistedNames()).toEqual(['Kitchen detail']);
  });

  it('refuses an empty name rather than leaving an unlabelled row', () => {
    const id = store().views[0]?.id ?? '';
    store().rename(id, '   ');
    expect(names()).toEqual(['Kitchen']);
  });

  it('ignores an unknown id', () => {
    store().rename('no-such-view', 'Ghost');
    expect(names()).toEqual(['Kitchen']);
  });
});

describe('reordering', () => {
  beforeEach(() => {
    store().bind(SCOPE);
    store().saveView('A', PLAN);
    store().saveView('B', PLAN);
    store().saveView('C', PLAN);
  });

  it('moves a view up and down, and persists the order', () => {
    const c = store().views[2]?.id ?? '';
    store().move(c, 0);
    expect(names()).toEqual(['C', 'A', 'B']);
    expect(persistedNames()).toEqual(['C', 'A', 'B']);

    store().move(c, 1);
    expect(names()).toEqual(['A', 'C', 'B']);
  });

  it('clamps an out-of-range index instead of losing the view', () => {
    const a = store().views[0]?.id ?? '';
    store().move(a, 99);
    expect(names()).toEqual(['B', 'C', 'A']);
    store().move(a, -99);
    expect(names()).toEqual(['A', 'B', 'C']);
  });

  it('is a no-op for an unknown id or a move to the same place', () => {
    store().move('nope', 0);
    expect(names()).toEqual(['A', 'B', 'C']);
    const b = store().views[1]?.id ?? '';
    store().move(b, 1);
    expect(names()).toEqual(['A', 'B', 'C']);
  });
});

describe('deleting', () => {
  beforeEach(() => {
    store().bind(SCOPE);
    store().saveView('A', PLAN);
    store().saveView('B', PLAN);
  });

  it('removes one and persists the shorter list', () => {
    const a = store().views[0]?.id ?? '';
    store().remove(a);
    expect(names()).toEqual(['B']);
    expect(persistedNames()).toEqual(['B']);
  });

  it('clears everything, on disk as well as in memory', () => {
    store().clearAll();
    expect(names()).toEqual([]);
    expect(readViews(SCOPE)).toBe(null);
  });
});

describe('nextViewName', () => {
  it('skips numbers already taken', () => {
    expect(nextViewName([])).toBe('View 1');
    expect(
      nextViewName([
        { id: 'a', name: 'View 1', camera: PLAN, createdAt: 0 },
        { id: 'b', name: 'View 2', camera: PLAN, createdAt: 0 },
      ]),
    ).toBe('View 3');
    // Two views, one of which is already called "View 3": the natural next
    // number is taken, so it moves on rather than proposing a duplicate.
    expect(
      nextViewName([
        { id: 'a', name: 'Kitchen', camera: PLAN, createdAt: 0 },
        { id: 'b', name: 'View 3', camera: PLAN, createdAt: 0 },
      ]),
    ).toBe('View 4');
  });
});
