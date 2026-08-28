/**
 * Favourites and recents in `localStorage`, including the browsers where
 * `localStorage` is not a working object.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE NEGATIVE CONTROL FOR THE THROWING-STORAGE TESTS
 * ════════════════════════════════════════════════════════════════════════════
 * The "storage that throws" cases below are only worth anything if they go RED
 * when the guard is removed. Both guards were checked, separately:
 *
 *   1. Replace the body of `safeStorage()` with
 *      `return globalThis.localStorage ?? null;` (delete the try/catch):
 *        × survives a localStorage whose property access throws
 *        × survives a localStorage whose property access throws on write
 *        Tests  2 failed | 16 passed (18)
 *
 *   2. Restore that, and delete the try/catch around `setItem` in `writeKeys`:
 *        × survives a setItem that throws (Safari private browsing)
 *        Tests  1 failed | 17 passed (18)
 *
 *   With both guards in place: Tests 18 passed (18).
 *
 * They fail independently, which is the point — the property access and the
 * method call are two different failures in two different browsers, and one
 * try/catch would not have covered both.
 *
 * `withThrowingStorage` shadows `globalThis.localStorage` with an own accessor
 * that throws, because that is what Chrome actually does when third-party
 * cookies are blocked and the app is framed — the PROPERTY ACCESS throws, not
 * the method call. A test that only made `setItem` throw would leave the more
 * common failure untested.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  FAVOURITES_MAX,
  RECENTS_MAX,
  clearAssetPrefs,
  favouritesKey,
  pushRecent,
  readFavourites,
  readRecents,
  recentsKey,
  toggleFavourite,
  writeFavourites,
  writeRecents,
} from './persist';

/** Run `fn` with `globalThis.localStorage` throwing on the property access. */
function withThrowingStorage(fn: () => void): void {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    get(): Storage {
      throw new Error('SecurityError: storage is disabled');
    },
  });
  try {
    fn();
  } finally {
    if (original === undefined) {
      delete (globalThis as { localStorage?: Storage }).localStorage;
    } else {
      Object.defineProperty(globalThis, 'localStorage', original);
    }
  }
}

/** Run `fn` with a storage whose `setItem` throws, as Safari private mode did. */
function withFullStorage(fn: () => void): void {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const stub: Storage = {
    length: 0,
    clear: () => undefined,
    getItem: () => null,
    key: () => null,
    removeItem: () => {
      throw new Error('QuotaExceededError');
    },
    setItem: () => {
      throw new Error('QuotaExceededError');
    },
  };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: stub });
  try {
    fn();
  } finally {
    if (original === undefined) {
      delete (globalThis as { localStorage?: Storage }).localStorage;
    } else {
      Object.defineProperty(globalThis, 'localStorage', original);
    }
  }
}

beforeEach(() => {
  globalThis.localStorage.clear();
});

afterEach(() => {
  globalThis.localStorage.clear();
});

describe('round trip', () => {
  it('writes and reads a favourites list', () => {
    writeFavourites('user_a', ['furniture:bed-queen', 'material:kota-stone']);
    expect(readFavourites('user_a')).toEqual(['furniture:bed-queen', 'material:kota-stone']);
  });

  it('keeps one user out of another user list', () => {
    writeFavourites('user_a', ['furniture:bed-queen']);
    expect(readFavourites('user_b')).toEqual([]);
  });

  it('keeps favourites and recents apart', () => {
    writeFavourites('user_a', ['furniture:bed-queen']);
    writeRecents('user_a', ['furniture:bed-king']);
    expect(readFavourites('user_a')).toEqual(['furniture:bed-queen']);
    expect(readRecents('user_a')).toEqual(['furniture:bed-king']);
    expect(favouritesKey('user_a')).not.toBe(recentsKey('user_a'));
  });

  it('forgets both lists on request', () => {
    writeFavourites('user_a', ['furniture:bed-queen']);
    writeRecents('user_a', ['furniture:bed-king']);
    clearAssetPrefs('user_a');
    expect(readFavourites('user_a')).toEqual([]);
    expect(readRecents('user_a')).toEqual([]);
  });
});

describe('a payload this process did not write', () => {
  it('reads nothing from an absent key', () => {
    expect(readFavourites('nobody')).toEqual([]);
  });

  it('reads nothing from broken JSON', () => {
    globalThis.localStorage.setItem(favouritesKey('user_a'), '{not json');
    expect(readFavourites('user_a')).toEqual([]);
  });

  it('reads nothing from a payload of the wrong shape', () => {
    globalThis.localStorage.setItem(favouritesKey('user_a'), '{"favourites":[]}');
    expect(readFavourites('user_a')).toEqual([]);
  });

  it('drops non-string and empty entries rather than putting them in a Set', () => {
    globalThis.localStorage.setItem(
      favouritesKey('user_a'),
      JSON.stringify(['furniture:bed-queen', 42, null, '', { id: 'x' }, 'material:kota-stone']),
    );
    expect(readFavourites('user_a')).toEqual(['furniture:bed-queen', 'material:kota-stone']);
  });

  it('drops duplicates', () => {
    globalThis.localStorage.setItem(favouritesKey('user_a'), JSON.stringify(['a', 'a', 'b', 'a']));
    expect(readFavourites('user_a')).toEqual(['a', 'b']);
  });

  it('caps a payload that is far too long', () => {
    const huge = Array.from({ length: FAVOURITES_MAX + 50 }, (_, i) => `k${String(i)}`);
    globalThis.localStorage.setItem(favouritesKey('user_a'), JSON.stringify(huge));
    expect(readFavourites('user_a')).toHaveLength(FAVOURITES_MAX);
  });
});

describe('storage that is not a working object', () => {
  it('survives a localStorage whose property access throws', () => {
    withThrowingStorage(() => {
      expect(readFavourites('user_a')).toEqual([]);
      expect(readRecents('user_a')).toEqual([]);
    });
  });

  it('survives a localStorage whose property access throws on write', () => {
    withThrowingStorage(() => {
      expect(() => {
        writeFavourites('user_a', ['furniture:bed-queen']);
        writeRecents('user_a', ['furniture:bed-king']);
        clearAssetPrefs('user_a');
      }).not.toThrow();
    });
  });

  it('survives a setItem that throws (Safari private browsing)', () => {
    withFullStorage(() => {
      expect(() => {
        writeFavourites('user_a', ['furniture:bed-queen']);
        clearAssetPrefs('user_a');
      }).not.toThrow();
      expect(readFavourites('user_a')).toEqual([]);
    });
  });

  it('leaves the real storage untouched afterwards', () => {
    withThrowingStorage(() => undefined);
    writeFavourites('user_a', ['furniture:bed-queen']);
    expect(readFavourites('user_a')).toEqual(['furniture:bed-queen']);
  });
});

describe('the pure list operations', () => {
  it('toggles a favourite on and off, newest first', () => {
    expect(toggleFavourite([], 'a')).toEqual(['a']);
    expect(toggleFavourite(['a'], 'b')).toEqual(['b', 'a']);
    expect(toggleFavourite(['b', 'a'], 'a')).toEqual(['b']);
  });

  it('caps favourites', () => {
    const full = Array.from({ length: FAVOURITES_MAX }, (_, i) => `k${String(i)}`);
    expect(toggleFavourite(full, 'new')).toHaveLength(FAVOURITES_MAX);
    expect(toggleFavourite(full, 'new')[0]).toBe('new');
  });

  it('moves a re-used item to the front instead of duplicating it', () => {
    expect(pushRecent(['a', 'b'], 'b')).toEqual(['b', 'a']);
    expect(pushRecent(['a', 'b'], 'c')).toEqual(['c', 'a', 'b']);
  });

  it('caps recents at a length a human can scan', () => {
    let list: readonly string[] = [];
    for (let i = 0; i < RECENTS_MAX + 5; i += 1) list = pushRecent(list, `k${String(i)}`);
    expect(list).toHaveLength(RECENTS_MAX);
    expect(list[0]).toBe(`k${String(RECENTS_MAX + 4)}`);
  });
});
