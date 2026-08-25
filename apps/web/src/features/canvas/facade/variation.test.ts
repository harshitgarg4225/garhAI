/**
 * variation.test.ts — the seeded-variation contract: pure functions of
 * (seed, key), pinned values so a refactor that changes the hash is a red
 * test, not a silently reshuffled facade on every existing project.
 */

import { describe, expect, it } from 'vitest';

import { fnv1a32, nextSeed, pickVariant, variantIndex } from './variation';

describe('fnv1a32', () => {
  it('matches the FNV-1a reference values', () => {
    // Published test vectors for 32-bit FNV-1a.
    expect(fnv1a32('')).toBe(0x811c9dc5);
    expect(fnv1a32('a')).toBe(0xe40c292c);
    expect(fnv1a32('foobar')).toBe(0xbf9cf968);
  });

  it('is stable across calls', () => {
    expect(fnv1a32('chajja-projection#7')).toBe(fnv1a32('chajja-projection#7'));
  });
});

describe('variantIndex', () => {
  it('is deterministic and in range', () => {
    for (let seed = 0; seed < 100; seed += 1) {
      const i = variantIndex(seed, 'chajja-projection', 2);
      expect(i === 0 || i === 1).toBe(true);
      expect(variantIndex(seed, 'chajja-projection', 2)).toBe(i);
    }
  });

  it('returns 0 for degenerate counts instead of throwing mid-render', () => {
    expect(variantIndex(9, 'x', 0)).toBe(0);
    expect(variantIndex(9, 'x', 1)).toBe(0);
    expect(variantIndex(9, 'x', -3)).toBe(0);
  });

  it('different keys decouple: a wall insert cannot reshuffle other picks', () => {
    // Same seed, different semantic keys — picks are independent draws.
    const a = Array.from({ length: 64 }, (_, s) => variantIndex(s, 'key-a', 2));
    const b = Array.from({ length: 64 }, (_, s) => variantIndex(s, 'key-b', 2));
    expect(a).not.toEqual(b);
  });
});

describe('pickVariant', () => {
  it('picks from the list and falls back on empty', () => {
    expect([600, 750]).toContain(pickVariant(7, 'p', [600, 750], 600));
    expect(pickVariant(7, 'p', [] as number[], 123)).toBe(123);
  });

  it('reaches every variant across seeds (the control is honest)', () => {
    const seen = new Set<number>();
    for (let seed = 0; seed < 64; seed += 1) seen.add(pickVariant(seed, 'p', [600, 750], 600));
    expect(seen).toEqual(new Set([600, 750]));
  });
});

describe('nextSeed', () => {
  it('is deterministic, non-negative and moves', () => {
    const s1 = nextSeed(7);
    expect(nextSeed(7)).toBe(s1);
    expect(s1).not.toBe(7);
    expect(s1).toBeGreaterThanOrEqual(0);
    expect(Number.isSafeInteger(s1)).toBe(true);
  });
});
