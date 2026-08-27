/**
 * ONE palette for a person, everywhere they appear.
 *
 * A teammate now shows up twice: as an initials chip in the top bar, and as a
 * live cursor on the plan. If those are different colours the feature actively
 * misleads — you learn "Priya is the green one" from the chips and then watch a
 * blue arrow move. So `PresenceChips` owns the palette and the lookup, and the
 * cursor layer imports the same function.
 *
 * That is easy to write and easy to un-write: the natural next edit, when
 * somebody wants a cursor colour, is a second list of tokens keyed by the same
 * index. It would look right, pass typecheck, and drift the first time anybody
 * reordered the original. The last test in this file is the guard against
 * exactly that — it asserts the module exports ONE palette.
 */

import { describe, expect, it } from 'vitest';

import * as presence from '../../../components/PresenceChips';
import {
  PRESENCE_PALETTE,
  presencePaletteClasses,
  presencePaletteIndex,
} from '../../../components/PresenceChips';

const IDS = [
  '7b8c9d0e-1f2a-3b4c-5d6e-7f8a9b0c1d2e',
  '4f6d2f66-9a1c-4c4e-8f8a-1c2d3e4f5a6b',
  'u1',
  'u2',
  'u3',
  '',
  'Priya',
  'a-very-long-user-identifier-that-is-not-a-uuid-at-all',
];

describe('presencePaletteClasses', () => {
  it('is deterministic — a teammate keeps their colour across reloads', () => {
    for (const id of IDS) {
      expect(presencePaletteClasses(id)).toBe(presencePaletteClasses(id));
    }
  });

  it('always answers with a member of the one palette', () => {
    // This is what catches a forked list: a second palette's classes would not
    // be in this one.
    for (const id of IDS) {
      expect(PRESENCE_PALETTE).toContain(presencePaletteClasses(id));
    }
  });

  it('is exactly the chip lookup — one expression, not two', () => {
    // The chip renders `presencePaletteClasses(user.userId)` and so does the
    // cursor. Asserting the composition holds means a change to either half
    // (the hash, or the list) moves both surfaces together or fails here.
    for (const id of IDS) {
      expect(presencePaletteClasses(id)).toBe(PRESENCE_PALETTE[presencePaletteIndex(id)]);
    }
  });

  it('spreads across the palette rather than collapsing onto one colour', () => {
    // A hash that always returned 0 would pass every test above. Six users in a
    // project is the realistic ceiling, so ask a larger sample to hit at least
    // half the palette.
    const seen = new Set<string>();
    for (let i = 0; i < 200; i += 1) seen.add(presencePaletteClasses(`user-${String(i)}`));
    expect(seen.size).toBeGreaterThanOrEqual(PRESENCE_PALETTE.length / 2);
  });

  it('never returns undefined, whatever the id', () => {
    // `noUncheckedIndexedAccess` types the number-indexed read as possibly
    // undefined; the fallback must be a colour, not a crash on a presence chip.
    expect(presencePaletteClasses('')).toBeTypeOf('string');
    expect(presencePaletteClasses('')).not.toBe('');
  });

  it('exports ONE palette — the anti-fork guard', () => {
    const arrayExports = Object.entries(presence)
      .filter(([, value]) => Array.isArray(value))
      .map(([name]) => name);
    expect(arrayExports).toEqual(['PRESENCE_PALETTE']);
  });
});

describe('presencePaletteIndex', () => {
  it('stays inside the palette for any id', () => {
    for (const id of IDS) {
      const index = presencePaletteIndex(id);
      expect(Number.isInteger(index)).toBe(true);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(PRESENCE_PALETTE.length);
    }
  });
});
