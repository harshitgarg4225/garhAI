/**
 * Spec for the storage boundary.
 *
 * Two things are being guarded, and only one of them is "does JSON round-trip".
 *
 *  1. **Nothing throws.** `localStorage` is a hostile API: the PROPERTY ACCESS
 *     itself throws in a framed page with third-party cookies blocked, which is
 *     exactly how a share link is embedded. Every case below replaces
 *     `globalThis.localStorage` with a thrower and asserts the panel would
 *     still work.
 *  2. **Nothing unusable gets in.** A camera that the controller would clamp on
 *     restore is a bookmark that lands somewhere else — silently. So a stored
 *     payload is coerced on the way in, and the assertion is the property
 *     itself (`isStorableCamera`), not a spot-check of one field.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { MAX_MM_PER_PX, MIN_MM_PER_PX } from '../canvas/core/constants';
import { isStorableCamera } from './camera';
import {
  cleanViewName,
  clearViews,
  MAX_NAME_LENGTH,
  MAX_VIEWS,
  parseCamera,
  readViews,
  storageKey,
  writeViews,
} from './persist';
import type { NamedView, Saved2dCamera, Saved3dCamera, SavedCamera, ViewsScope } from './types';

const SCOPE: ViewsScope = { userId: 'user_1', projectId: 'proj_1' };

const PLAN: Saved2dCamera = { mode: '2d', centreMm: { x: 1234.5, y: -678.25 }, mmPerPx: 3.7795 };
const ORBIT: Saved3dCamera = {
  mode: '3d',
  targetMm: { x: 100, y: 200, z: 300 },
  distanceMm: 20_000,
  azimuthDeg: 225,
  polarDeg: 60,
};

function view(id: string, name: string, camera: SavedCamera = PLAN): NamedView {
  return { id, name, camera, createdAt: 1_700_000_000_000 };
}

const realStorage = globalThis.localStorage;

/** Replace `localStorage` with something that throws on the property read. */
function breakStorage(): void {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    get() {
      throw new Error('SecurityError: storage is disabled');
    },
  });
}

function restoreStorage(): void {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    writable: true,
    value: realStorage,
  });
}

beforeEach(() => {
  restoreStorage();
  globalThis.localStorage.clear();
});

afterEach(() => {
  restoreStorage();
});

describe('the key', () => {
  it('carries the user AND the project, and the schema version', () => {
    const key = storageKey(SCOPE);
    expect(key).toContain('user_1');
    expect(key).toContain('proj_1');
    expect(key).toMatch(/^garh:views:v\d+:/);
    // A shared studio machine: two people, one project, two lists.
    expect(storageKey({ ...SCOPE, userId: 'user_2' })).not.toBe(key);
    expect(storageKey({ ...SCOPE, projectId: 'proj_2' })).not.toBe(key);
  });
});

describe('round trip', () => {
  it('writes and reads a list back unchanged', () => {
    const views = [view('a', 'Kitchen detail'), view('b', 'Street elevation', ORBIT)];
    expect(writeViews(SCOPE, views)).toBe(true);
    expect(readViews(SCOPE)).toEqual(views);
  });

  it('answers null when nothing has ever been stored', () => {
    expect(readViews(SCOPE)).toBe(null);
  });

  it('keeps two scopes apart', () => {
    writeViews(SCOPE, [view('a', 'Mine')]);
    expect(readViews({ ...SCOPE, userId: 'someone_else' })).toBe(null);
  });

  it('forgets on clear', () => {
    writeViews(SCOPE, [view('a', 'Mine')]);
    expect(clearViews(SCOPE)).toBe(true);
    expect(readViews(SCOPE)).toBe(null);
  });
});

describe('storage that fights back', () => {
  it('reads, writes and clears without throwing when the property access throws', () => {
    breakStorage();
    expect(() => readViews(SCOPE)).not.toThrow();
    expect(readViews(SCOPE)).toBe(null);
    expect(writeViews(SCOPE, [view('a', 'Kitchen')])).toBe(false);
    expect(clearViews(SCOPE)).toBe(false);
  });

  it('survives a setItem that throws (quota, private mode)', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: () => null,
        setItem: () => {
          throw new Error('QuotaExceededError');
        },
        removeItem: () => undefined,
      } as unknown as Storage,
    });
    expect(writeViews(SCOPE, [view('a', 'Kitchen')])).toBe(false);
  });

  it('shrugs off a payload that is not JSON, or is JSON of the wrong shape', () => {
    for (const raw of ['{', 'null', '"a string"', '[]', '{"views":"nope"}', '']) {
      globalThis.localStorage.setItem(storageKey(SCOPE), raw);
      expect(readViews(SCOPE)).toBe(null);
    }
  });
});

describe('coercing an untrusted payload', () => {
  it('clamps a stored zoom into the range the controller accepts', () => {
    globalThis.localStorage.setItem(
      storageKey(SCOPE),
      JSON.stringify({
        views: [
          {
            id: 'a',
            name: 'From an older build',
            createdAt: 1,
            camera: { mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: MAX_MM_PER_PX * 1000 },
          },
        ],
      }),
    );
    const views = readViews(SCOPE);
    expect(views).toHaveLength(1);
    const stored = views?.[0];
    expect(stored).toBeDefined();
    if (stored === undefined) return;
    // THE PROPERTY: whatever was on disk, what comes out is a camera the
    // controller takes back unchanged. Without this the view would land at the
    // clamp instead of where its record says, and nothing would report it.
    expect(isStorableCamera(stored.camera)).toBe(true);
    expect((stored.camera as Saved2dCamera).mmPerPx).toBe(MAX_MM_PER_PX);
  });

  it('drops a view whose camera is missing a field rather than restoring a NaN', () => {
    globalThis.localStorage.setItem(
      storageKey(SCOPE),
      JSON.stringify({
        views: [
          { id: 'a', name: 'Half an orbit', createdAt: 1, camera: { mode: '3d', distanceMm: 10 } },
          { id: 'b', name: 'Fine', createdAt: 1, camera: PLAN },
        ],
      }),
    );
    const views = readViews(SCOPE);
    expect(views?.map((v) => v.id)).toEqual(['b']);
  });

  it('drops duplicates, blank ids and non-objects', () => {
    globalThis.localStorage.setItem(
      storageKey(SCOPE),
      JSON.stringify({
        views: [
          { id: 'a', name: 'First', createdAt: 1, camera: PLAN },
          { id: 'a', name: 'Duplicate id', createdAt: 2, camera: PLAN },
          { id: '   ', name: 'Blank id', createdAt: 3, camera: PLAN },
          'not an object',
          { id: 'b', name: '', createdAt: 4, camera: PLAN },
        ],
      }),
    );
    const views = readViews(SCOPE) ?? [];
    expect(views.map((v) => v.id)).toEqual(['a', 'b']);
    // An unnamed view still gets a label — an unclickable blank row is not an
    // improvement on a wrong one.
    expect(views[1]?.name).toBe('Untitled view');
  });

  it('never returns more than the cap, however many were stored', () => {
    const many = Array.from({ length: MAX_VIEWS + 25 }, (_, i) => ({
      id: `v${String(i)}`,
      name: `View ${String(i)}`,
      createdAt: i,
      camera: PLAN,
    }));
    globalThis.localStorage.setItem(storageKey(SCOPE), JSON.stringify({ views: many }));
    expect(readViews(SCOPE)).toHaveLength(MAX_VIEWS);
  });

  it('refuses to write a camera the controller would not take back', () => {
    const bad: NamedView = {
      ...view('bad', 'Impossible'),
      camera: { mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: MIN_MM_PER_PX / 10 },
    };
    writeViews(SCOPE, [bad, view('good', 'Fine')]);
    expect(readViews(SCOPE)?.map((v) => v.id)).toEqual(['good']);
  });
});

describe('parseCamera', () => {
  it('accepts both projections and rejects everything else', () => {
    expect(parseCamera(PLAN)).toEqual(PLAN);
    expect(parseCamera(ORBIT)).toEqual(ORBIT);
    expect(parseCamera(null)).toBe(null);
    expect(parseCamera({ mode: 'isometric' })).toBe(null);
    expect(parseCamera({ mode: '2d', centreMm: { x: 'no', y: 0 }, mmPerPx: 1 })).toBe(null);
    expect(parseCamera({ mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: Number.NaN })).toBe(null);
    expect(parseCamera({ ...ORBIT, polarDeg: null })).toBe(null);
  });
});

describe('cleanViewName', () => {
  it('trims, collapses whitespace and caps the length', () => {
    expect(cleanViewName('  Kitchen   detail  ')).toBe('Kitchen detail');
    expect(cleanViewName('\n\t')).toBe('');
    expect(cleanViewName('x'.repeat(500))).toHaveLength(MAX_NAME_LENGTH);
  });
});
