/**
 * Spec for the storage layer.
 *
 * The interesting cases are all the ones where `localStorage` misbehaves, and
 * they are not hypothetical: Safari in private browsing has thrown on
 * `setItem`, and Chrome throws `SecurityError` on the PROPERTY ACCESS when
 * third-party cookies are blocked and the page is framed — which is precisely
 * how a share link gets embedded in someone's intranet.
 *
 * The contract being pinned: reading always answers, writing never throws, and
 * a browser with no usable storage still gets a working panel that simply
 * forgets. So each hostile case below asserts that the caller can carry on,
 * not merely that no exception escaped.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { DRAWING_LAYER_NAMES } from './layerSpecs';
import {
  allLayers,
  clearLayerState,
  defaultLayerState,
  readLayerState,
  storageKey,
  writeLayerState,
  type LayerScope,
} from './persist';

const SCOPE: LayerScope = { userId: 'user_abc', projectId: 'project_xyz' };
const OTHER_USER: LayerScope = { userId: 'user_def', projectId: 'project_xyz' };
const OTHER_PROJECT: LayerScope = { userId: 'user_abc', projectId: 'project_other' };

/**
 * Replace `globalThis.localStorage` for one test. Returns the restore
 * function; `describe`-level `afterEach` calls it, so a failing assertion can
 * never leave the rest of the file running against a broken global.
 */
function withStorage(descriptor: PropertyDescriptor): () => void {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, ...descriptor });
  return () => {
    if (original === undefined) delete (globalThis as Record<string, unknown>).localStorage;
    else Object.defineProperty(globalThis, 'localStorage', original);
  };
}

let restore: (() => void) | null = null;

afterEach(() => {
  restore?.();
  restore = null;
  try {
    globalThis.localStorage.clear();
  } catch {
    // A test that broke the global on purpose; nothing to clear.
  }
});

describe('keys', () => {
  it('separates users and projects', () => {
    expect(storageKey(SCOPE)).not.toBe(storageKey(OTHER_USER));
    expect(storageKey(SCOPE)).not.toBe(storageKey(OTHER_PROJECT));
  });

  it('is versioned, so a future shape change does not read an old payload', () => {
    expect(storageKey(SCOPE)).toMatch(/^garh:layers:v\d+:/);
  });
});

describe('round trip', () => {
  it('writes and reads back exactly', () => {
    const state = {
      visible: { ...allLayers(true), 'A-DIM': false },
      locked: { ...allLayers(false), 'A-WALL': true },
    };
    expect(writeLayerState(SCOPE, state)).toBe(true);
    expect(readLayerState(SCOPE)).toEqual(state);
  });

  it('does not leak between users or projects', () => {
    writeLayerState(SCOPE, { visible: allLayers(false), locked: allLayers(true) });
    expect(readLayerState(OTHER_USER)).toBeNull();
    expect(readLayerState(OTHER_PROJECT)).toBeNull();
  });

  it('clears', () => {
    writeLayerState(SCOPE, defaultLayerState());
    expect(clearLayerState(SCOPE)).toBe(true);
    expect(readLayerState(SCOPE)).toBeNull();
  });
});

describe('nothing stored', () => {
  it('answers null rather than an empty state', () => {
    expect(readLayerState(SCOPE)).toBeNull();
  });
});

describe('garbage stored', () => {
  it('answers null for text that is not JSON', () => {
    globalThis.localStorage.setItem(storageKey(SCOPE), 'not json {');
    expect(readLayerState(SCOPE)).toBeNull();
  });

  it('answers null for JSON that is not an object', () => {
    globalThis.localStorage.setItem(storageKey(SCOPE), '42');
    expect(readLayerState(SCOPE)).toBeNull();
  });

  it('drops layer names it does not recognise', () => {
    globalThis.localStorage.setItem(
      storageKey(SCOPE),
      JSON.stringify({ visible: { 'A-WALL': false, 'A-GHOST': false }, locked: {} }),
    );
    const state = readLayerState(SCOPE);
    expect(state).not.toBeNull();
    expect(Object.keys(state?.visible ?? {}).sort()).toEqual([...DRAWING_LAYER_NAMES].sort());
    expect(state?.visible['A-WALL']).toBe(false);
  });

  it('fills in layers the payload is missing, so no Record has holes', () => {
    // An older build wrote five layers; this build has nine. The four it never
    // heard of must default, not be undefined — a `Record` with holes is how
    // `visible[layer]` starts returning undefined and every comparison against
    // it quietly answers false.
    globalThis.localStorage.setItem(
      storageKey(SCOPE),
      JSON.stringify({ visible: { 'A-WALL': false }, locked: { 'A-DIM': true } }),
    );
    const state = readLayerState(SCOPE);
    for (const name of DRAWING_LAYER_NAMES) {
      expect(typeof state?.visible[name], `visible.${name}`).toBe('boolean');
      expect(typeof state?.locked[name], `locked.${name}`).toBe('boolean');
    }
    expect(state?.visible['A-DIM']).toBe(true);
    expect(state?.locked['A-WALL']).toBe(false);
  });

  it('ignores non-boolean values', () => {
    globalThis.localStorage.setItem(
      storageKey(SCOPE),
      JSON.stringify({ visible: { 'A-WALL': 'yes' }, locked: null }),
    );
    expect(readLayerState(SCOPE)?.visible['A-WALL']).toBe(true);
  });
});

describe('storage that throws', () => {
  it('survives a getter that throws on property access (framed page, cookies blocked)', () => {
    restore = withStorage({
      get() {
        throw new DOMException('The operation is insecure.', 'SecurityError');
      },
    });
    expect(readLayerState(SCOPE)).toBeNull();
    expect(writeLayerState(SCOPE, defaultLayerState())).toBe(false);
    expect(clearLayerState(SCOPE)).toBe(false);
  });

  it('survives setItem throwing (private browsing quota)', () => {
    restore = withStorage({
      value: {
        getItem: () => null,
        setItem: () => {
          throw new DOMException('QuotaExceededError', 'QuotaExceededError');
        },
        removeItem: () => undefined,
      } as unknown as Storage,
    });
    expect(writeLayerState(SCOPE, defaultLayerState())).toBe(false);
    // …and the caller still gets a usable state to render from.
    expect(readLayerState(SCOPE)).toBeNull();
  });

  it('survives getItem throwing', () => {
    restore = withStorage({
      value: {
        getItem: () => {
          throw new Error('nope');
        },
        setItem: () => undefined,
        removeItem: () => undefined,
      } as unknown as Storage,
    });
    expect(readLayerState(SCOPE)).toBeNull();
  });

  it('survives localStorage being absent entirely', () => {
    restore = withStorage({ value: undefined });
    expect(readLayerState(SCOPE)).toBeNull();
    expect(writeLayerState(SCOPE, defaultLayerState())).toBe(false);
  });
});

describe('defaults', () => {
  it('opens with everything visible and nothing locked', () => {
    const state = defaultLayerState();
    for (const name of DRAWING_LAYER_NAMES) {
      expect(state.visible[name], name).toBe(true);
      expect(state.locked[name], name).toBe(false);
    }
  });

  it('covers all nine layers and no more', () => {
    expect(Object.keys(allLayers(true)).sort()).toEqual([...DRAWING_LAYER_NAMES].sort());
  });
});
