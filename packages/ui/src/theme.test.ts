/**
 * theme.ts, in a world with no DOM.
 *
 * Every function here claims to be safe when `window`, `document` and
 * `localStorage` are absent — that claim is what lets `main.tsx` call
 * `initTheme()` before React mounts and what would let the app be pre-rendered
 * later. The claim is only worth anything if something checks it, and the node
 * test environment provides exactly the missing-globals condition for free.
 */

import { describe, expect, it } from 'vitest';

import { THEME_STORAGE_KEY, applyTheme, initTheme, readStoredTheme, resolveTheme } from './theme';

describe('resolveTheme', () => {
  it('passes explicit choices straight through', () => {
    expect(resolveTheme('light')).toBe('light');
    expect(resolveTheme('dark')).toBe('dark');
  });

  it('falls back to light for "system" when the OS cannot be asked', () => {
    expect(resolveTheme('system')).toBe('light');
  });
});

describe('DOM-free safety', () => {
  it('applyTheme returns the resolved theme without touching a document', () => {
    expect(applyTheme('dark')).toBe('dark');
    expect(applyTheme('system')).toBe('light');
  });

  it('readStoredTheme defaults to "system"', () => {
    expect(readStoredTheme()).toBe('system');
  });

  it('initTheme returns a callable unsubscribe', () => {
    const stop = initTheme();
    expect(typeof stop).toBe('function');
    expect(() => stop()).not.toThrow();
  });
});

describe('storage key', () => {
  it('is namespaced, so it cannot collide with another app on the same origin', () => {
    expect(THEME_STORAGE_KEY).toBe('garh.theme');
  });
});
