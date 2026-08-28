/**
 * Binding parsing and matching, at the level a keyboard actually behaves.
 *
 * The interesting cases are all platform or layout shaped: ⌘ vs Ctrl, `?` on a
 * layout where it needs Shift and one where it does not, Alt meaning "leave
 * this alone". None of them show up in a happy-path test, and every one of them
 * is a shortcut that works on the author's machine and nowhere else.
 */

import { describe, expect, it } from 'vitest';

import {
  BindingSyntaxError,
  bindingsCollide,
  formatBinding,
  matchesBinding,
  parseBinding,
  synthesiseEvent,
  type KeyEventLike,
} from './binding';

function press(key: string, held: Partial<KeyEventLike> = {}): KeyEventLike {
  return { key, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...held };
}

describe('parseBinding', () => {
  it('reads modifiers and normalises the key', () => {
    const parsed = parseBinding('mod+Shift+K');
    expect(parsed.mod).toBe(true);
    expect(parsed.shift).toBe(true);
    expect(parsed.key).toBe('k');
  });

  it('canonicalises named keys to the values the browser reports', () => {
    expect(parseBinding('esc').key).toBe('Escape');
    expect(parseBinding('Enter').key).toBe('Enter');
    expect(parseBinding('down').key).toBe('ArrowDown');
    expect(parseBinding('mod+Delete').key).toBe('Delete');
    expect(parseBinding('space').key).toBe(' ');
  });

  it('handles + as a key rather than only as a separator', () => {
    const parsed = parseBinding('mod++');
    expect(parsed.key).toBe('+');
    expect(parsed.mod).toBe(true);
    expect(parseBinding('+').key).toBe('+');
  });

  it('throws on a key it does not know, instead of registering a dead binding', () => {
    // The failure mode this replaces: a typo parses to something, the command
    // registers, the cheatsheet lists the key, and it never once fires.
    expect(() => parseBinding('mod+kk')).toThrow(BindingSyntaxError);
    expect(() => parseBinding('supr+k')).toThrow(/unknown modifier/);
    expect(() => parseBinding('')).toThrow(BindingSyntaxError);
    expect(() => parseBinding('mod+')).toThrow(/no key/);
  });

  it('refuses mod combined with a literal ctrl or cmd', () => {
    expect(() => parseBinding('mod+ctrl+k')).toThrow(/cannot be combined/);
  });
});

describe('matchesBinding — platform', () => {
  const modK = parseBinding('mod+k');

  it('is Cmd on a Mac and Ctrl everywhere else', () => {
    expect(matchesBinding(press('k', { metaKey: true }), modK, { mac: true })).toBe(true);
    expect(matchesBinding(press('k', { ctrlKey: true }), modK, { mac: false })).toBe(true);
  });

  it('does NOT accept the other platform’s modifier', () => {
    // Ctrl-K on macOS is the system’s delete-to-end-of-line. Stealing it is
    // the kind of thing that makes a web app feel like it is fighting you.
    expect(matchesBinding(press('k', { ctrlKey: true }), modK, { mac: true })).toBe(false);
    expect(matchesBinding(press('k', { metaKey: true }), modK, { mac: false })).toBe(false);
  });

  it('does not fire an unmodified binding when mod is held', () => {
    const plainK = parseBinding('k');
    expect(matchesBinding(press('k'), plainK, { mac: false })).toBe(true);
    expect(matchesBinding(press('k', { ctrlKey: true }), plainK, { mac: false })).toBe(false);
    expect(matchesBinding(press('k', { metaKey: true }), plainK, { mac: true })).toBe(false);
  });

  it('treats Alt as reserved rather than as "do not care"', () => {
    const plainK = parseBinding('k');
    expect(matchesBinding(press('k', { altKey: true }), plainK, { mac: false })).toBe(false);
  });
});

describe('matchesBinding — shift', () => {
  it('is strict for letters, so a and Shift+A stay distinguishable', () => {
    const shiftA = parseBinding('shift+a');
    // `event.key` is lower-cased before comparison, so Shift is the ONLY thing
    // separating these two. A lenient rule here would make `a` fire for both.
    expect(matchesBinding(press('A', { shiftKey: true }), shiftA, { mac: false })).toBe(true);
    expect(matchesBinding(press('a'), shiftA, { mac: false })).toBe(false);
  });

  it('is lenient for punctuation, because the glyph already carries the shift', () => {
    const question = parseBinding('shift+?');
    // US layout: Shift+/ arrives as key '?' with shiftKey true.
    expect(matchesBinding(press('?', { shiftKey: true }), question, { mac: false })).toBe(true);
    // Layouts where ? is its own key report no shift at all. lib/keymap.ts
    // works around this by registering the binding twice; here it is one rule.
    expect(matchesBinding(press('?'), question, { mac: false })).toBe(true);
  });

  it('lenient shift still cannot leak across different glyphs', () => {
    const slash = parseBinding('/');
    // Shift+/ produces '?', not '/', so the copilot's `/` is untouched by it.
    expect(matchesBinding(press('?', { shiftKey: true }), slash, { mac: false })).toBe(false);
  });
});

describe('bindingsCollide', () => {
  it('catches a clash that only exists on one platform', () => {
    // `mod+k` is ⌘K on a Mac and Ctrl-K elsewhere, so it collides with `meta+k`
    // on a Mac only — and a collision on one platform is still a collision.
    expect(bindingsCollide(parseBinding('mod+k'), parseBinding('cmd+k'))).toBe(true);
    expect(bindingsCollide(parseBinding('mod+k'), parseBinding('ctrl+k'))).toBe(true);
  });

  it('does not report a clash between different keys or modifiers', () => {
    expect(bindingsCollide(parseBinding('mod+k'), parseBinding('mod+j'))).toBe(false);
    expect(bindingsCollide(parseBinding('mod+k'), parseBinding('mod+shift+k'))).toBe(false);
  });

  it('reports the shifted and unshifted forms of a glyph as one binding', () => {
    // Exactly the pair lib/keymap.ts registers for `?`.
    expect(bindingsCollide(parseBinding('?'), parseBinding('shift+?'))).toBe(true);
  });
});

describe('synthesiseEvent', () => {
  it('puts the modifier on the right key for the platform', () => {
    const modK = parseBinding('mod+k');
    expect(synthesiseEvent(modK, true)).toMatchObject({ metaKey: true, ctrlKey: false });
    expect(synthesiseEvent(modK, false)).toMatchObject({ metaKey: false, ctrlKey: true });
  });

  it('round-trips: a synthesised event matches the binding it came from', () => {
    for (const spec of ['mod+k', 'shift+g', 'Escape', 'mod+shift+z', '?', 'alt+f']) {
      const parsed = parseBinding(spec);
      for (const mac of [true, false]) {
        expect(matchesBinding(synthesiseEvent(parsed, mac), parsed, { mac }), spec).toBe(true);
      }
    }
  });
});

describe('formatBinding', () => {
  it('uses the platform idiom', () => {
    expect(formatBinding(parseBinding('mod+k'), true)).toBe('⌘K');
    expect(formatBinding(parseBinding('mod+k'), false)).toBe('Ctrl+K');
    expect(formatBinding(parseBinding('mod+shift+z'), true)).toBe('⇧⌘Z');
    expect(formatBinding(parseBinding('mod+shift+z'), false)).toBe('Ctrl+Shift+Z');
  });

  it('prints a punctuation binding as the glyph alone', () => {
    // "⇧?" would invite someone to press three keys for a two-key shortcut.
    expect(formatBinding(parseBinding('shift+?'), true)).toBe('?');
    expect(formatBinding(parseBinding('?'), false)).toBe('?');
  });

  it('names the keys that have no glyph', () => {
    expect(formatBinding(parseBinding('Escape'), false)).toBe('Esc');
    expect(formatBinding(parseBinding('space'), false)).toBe('Space');
    expect(formatBinding(parseBinding('down'), false)).toBe('↓');
  });
});
