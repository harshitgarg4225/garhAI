/**
 * cn() is hand-rolled (see the header of cn.ts for why), which means its
 * conflict table is ours to keep correct. These tests pin the behaviour every
 * primitive depends on: a caller's `className` must be able to override the
 * component's own utilities, and nothing else may be dropped.
 *
 * The "documented limits" block at the bottom is deliberate — those cases are
 * known and accepted, and asserting them means a future change to the group
 * table cannot silently alter them without a failing test.
 */

import { describe, expect, it } from 'vitest';

import { cn } from './cn';

describe('cn — input handling', () => {
  it('joins strings and ignores every falsy form', () => {
    expect(cn('a', false, null, undefined, '', 'b')).toBe('a b');
  });

  it('flattens arrays and object maps', () => {
    expect(cn('a', ['b', ['c']], { d: true, e: false, f: null })).toBe('a b c d');
  });

  it('splits multi-class strings so conflicts inside one literal resolve', () => {
    expect(cn('p-2 p-4')).toBe('p-4');
  });

  it('returns an empty string for no meaningful input', () => {
    expect(cn(undefined, false, null)).toBe('');
  });
});

describe('cn — last wins within a utility group', () => {
  it('lets a caller override padding', () => {
    expect(cn('px-3', 'px-6')).toBe('px-6');
  });

  it('keeps the earlier position when a later class evicts', () => {
    // Position is preserved for readability: px-6 takes px-3's slot rather
    // than moving to the end.
    expect(cn('px-3 py-2', 'px-6')).toBe('px-6 py-2');
  });

  it('treats a negative and a positive of the same utility as one group', () => {
    expect(cn('-mt-1', 'mt-2')).toBe('mt-2');
  });

  it('resolves bare display and position utilities', () => {
    expect(cn('block', 'flex')).toBe('flex');
    expect(cn('absolute', 'relative')).toBe('relative');
  });

  it('keeps flex sizing, direction and wrap as separate groups', () => {
    // Regression: one `flex-` group made `flex-col` evict `flex-1`, which
    // collapsed any flex-1 column passed through a component className — the
    // share viewer's canvas rendered at height 0 because of it.
    expect(cn('flex min-h-0 flex-1 flex-col')).toBe('flex min-h-0 flex-1 flex-col');
    expect(cn('flex-row', 'flex-col')).toBe('flex-col');
    expect(cn('flex-1', 'flex-none')).toBe('flex-none');
    expect(cn('flex-wrap', 'flex-nowrap')).toBe('flex-nowrap');
    // The legacy longhands share their group with the modern names.
    expect(cn('flex-grow', 'grow-0')).toBe('grow-0');
    expect(cn('flex-shrink', 'shrink-0')).toBe('shrink-0');
  });

  it('groups sizing longhands separately from each other', () => {
    expect(cn('max-w-md', 'max-w-lg')).toBe('max-w-lg');
    expect(cn('w-full', 'h-9')).toBe('w-full h-9');
  });

  it('resolves rounded shorthand against itself but not against corner longhands', () => {
    expect(cn('rounded', 'rounded-full')).toBe('rounded-full');
    expect(cn('rounded-md', 'rounded-t-lg')).toBe('rounded-md rounded-t-lg');
  });

  it('resolves the text-overflow family', () => {
    expect(cn('truncate', 'text-ellipsis')).toBe('text-ellipsis');
  });
});

describe('cn — text size vs text colour', () => {
  it('keeps a size and a colour together', () => {
    // The single hardest case: `text-` is both a size prefix and a colour
    // prefix, and a component that set `text-sm` must not lose it when the
    // caller passes `text-ink-muted`.
    expect(cn('text-sm', 'text-ink-muted')).toBe('text-sm text-ink-muted');
  });

  it('still resolves size against size and colour against colour', () => {
    expect(cn('text-2xs', 'text-sm')).toBe('text-sm');
    expect(cn('text-ink-subtle', 'text-fail-ink')).toBe('text-fail-ink');
  });

  it('treats an alpha-modified colour as a colour', () => {
    expect(cn('text-ink', 'text-ink/70')).toBe('text-ink/70');
  });
});

describe('cn — variants are part of the conflict key', () => {
  it('does not let a hover class evict its base', () => {
    expect(cn('bg-surface', 'hover:bg-surface-muted')).toBe('bg-surface hover:bg-surface-muted');
  });

  it('resolves within the same variant chain', () => {
    expect(cn('hover:bg-a', 'hover:bg-b')).toBe('hover:bg-b');
    expect(cn('dark:hover:text-ink', 'dark:hover:text-ink-muted')).toBe(
      'dark:hover:text-ink-muted',
    );
  });

  it('ignores colons inside arbitrary variants when splitting', () => {
    expect(cn('[&:hover]:bg-a', 'bg-b')).toBe('[&:hover]:bg-a bg-b');
  });

  it('groups arbitrary values by their prefix', () => {
    expect(cn('w-[37px]', 'w-full')).toBe('w-full');
  });
});

describe('cn — unknown classes are never dropped', () => {
  it('keeps our own utility classes', () => {
    expect(cn('garh-focus-ring', 'garh-nums')).toBe('garh-focus-ring garh-nums');
  });

  it('keeps repeated unknown classes rather than de-duplicating blindly', () => {
    // An unknown class evicts nothing, which is the safe direction: dropping a
    // class we do not understand is how a component silently loses styling.
    expect(cn('sr-only', 'focus:not-sr-only')).toBe('sr-only focus:not-sr-only');
  });
});

describe('cn — documented limits (accepted behaviour, pinned on purpose)', () => {
  it('does NOT resolve shorthand against longhand', () => {
    // Tailwind's own cascade cannot express this either; cn.ts says "write the
    // longhand you mean".
    expect(cn('px-2', 'p-4')).toBe('px-2 p-4');
  });

  it('groups border width and border colour together', () => {
    // `border-2` and `border-line` share the `border-` prefix, so the later one
    // wins. Components pass the bare `border` plus a colour, which is unaffected.
    expect(cn('border border-line', 'border-fail')).toBe('border border-fail');
  });
});
