/**
 * The keyboard map is a §12 contract, not a convenience: the tools agent
 * (Phase 4) plugs into `KEY_BINDINGS` rather than adding listeners, so a
 * binding that silently changes shape breaks a feature nobody has written yet.
 */

import { describe, expect, it } from 'vitest';

import {
  COMMAND_IDS,
  KEY_BINDINGS,
  TOOL_IDS,
  TOOL_SHORTCUT,
  formatShortcut,
  isTypingTarget,
  matchBinding,
  type KeyBinding,
} from './keymap';

interface KeyEventLike {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  repeat: boolean;
}

function key(k: string, mods: Partial<KeyEventLike> = {}): KeyEventLike {
  return {
    key: k,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    repeat: false,
    ...mods,
  };
}

describe('KEY_BINDINGS', () => {
  it('binds exactly the §12 letters to the eight tools', () => {
    const letters: Record<string, string> = {};
    for (const b of KEY_BINDINGS) {
      if (b.tool) letters[b.key] = b.tool;
    }
    expect(letters).toEqual({
      v: 'select',
      w: 'wall',
      d: 'door',
      n: 'window',
      s: 'stair',
      b: 'balcony',
      m: 'measure',
      f: 'furniture',
    });
  });

  it('gives every tool a shortcut label and every binding a known command', () => {
    for (const tool of TOOL_IDS) expect(TOOL_SHORTCUT[tool]).toBeTruthy();
    for (const b of KEY_BINDINGS) expect(COMMAND_IDS).toContain(b.command);
  });

  it('has no duplicate (key, modifier, scope) triples', () => {
    const seen = new Set<string>();
    for (const b of KEY_BINDINGS) {
      const sig = `${b.key}|${b.modifiers}|${b.scope}`;
      expect(seen.has(sig), `duplicate binding ${sig}`).toBe(false);
      seen.add(sig);
    }
  });

  it('writes plain, sentence-cased descriptions (§15 tone)', () => {
    for (const b of KEY_BINDINGS) {
      expect(b.description.endsWith('.')).toBe(true);
      expect(b.description).not.toMatch(/module|entity|invoke/i);
    }
  });
});

describe('matchBinding', () => {
  it('matches unmodified letters to tools', () => {
    expect(matchBinding(key('w'))?.command).toBe('tool.wall');
    expect(matchBinding(key('W'))?.command).toBe('tool.wall');
    expect(matchBinding(key('f'))?.command).toBe('tool.furniture');
  });

  it('does not match a letter that carries the platform modifier', () => {
    expect(matchBinding(key('w', { metaKey: true }), { mac: true })).toBeNull();
    expect(matchBinding(key('w', { ctrlKey: true }), { mac: false })).toBeNull();
  });

  it('resolves undo/redo per platform', () => {
    expect(matchBinding(key('z', { metaKey: true }), { mac: true })?.command).toBe('edit.undo');
    expect(matchBinding(key('z', { ctrlKey: true }), { mac: false })?.command).toBe('edit.undo');
    expect(matchBinding(key('y', { ctrlKey: true }), { mac: false })?.command).toBe('edit.redo');
    // ⇧⌘Z is the Mac redo idiom and must not be read as an undo.
    expect(matchBinding(key('z', { metaKey: true, shiftKey: true }), { mac: true })?.command).toBe(
      'edit.redo',
    );
    // Ctrl-Z on a Mac is not the primary modifier: no match.
    expect(matchBinding(key('z', { ctrlKey: true }), { mac: true })).toBeNull();
  });

  it('keeps Tab for focus traversal outside the canvas', () => {
    expect(matchBinding(key('Tab'), { inCanvas: false })).toBeNull();
    expect(matchBinding(key('Tab'), { inCanvas: true })?.command).toBe('view.toggle');
  });

  it('ignores anything with Alt held', () => {
    expect(matchBinding(key('w', { altKey: true }))).toBeNull();
  });

  it('maps the storey digits and the snap toggle', () => {
    expect(matchBinding(key('1'))?.command).toBe('storey.1');
    expect(matchBinding(key('3'))?.command).toBe('storey.3');
    expect(matchBinding(key('g'))?.command).toBe('snap.toggle');
  });

  // ── Phase 4 additions ──────────────────────────────────────────────────

  it('separates the shifted layer toggles from the unshifted commands', () => {
    // G and ⇧G are different jobs: what is being SNAPPED to, and what is being
    // DRAWN. Sharing a letter is deliberate (they are the same idea) and only
    // safe because the modifier is matched exactly.
    expect(matchBinding(key('g'))?.command).toBe('snap.toggle');
    expect(matchBinding(key('G', { shiftKey: true }))?.command).toBe('view.grid');
    expect(matchBinding(key('d'))?.command).toBe('tool.door');
    expect(matchBinding(key('D', { shiftKey: true }))?.command).toBe('view.dimensions');
  });

  it('keeps delete and select-all inside the canvas', () => {
    // Outside the canvas these belong to whatever list or field has focus.
    // Hijacking ⌘A globally is the shortcut that makes a keyboard user stop
    // trusting an app.
    expect(matchBinding(key('Delete'), { inCanvas: false })).toBeNull();
    expect(matchBinding(key('Backspace'), { inCanvas: false })).toBeNull();
    expect(matchBinding(key('a', { metaKey: true }), { mac: true, inCanvas: false })).toBeNull();

    expect(matchBinding(key('Delete'), { inCanvas: true })?.command).toBe('edit.delete');
    expect(matchBinding(key('Backspace'), { inCanvas: true })?.command).toBe('edit.delete');
    expect(matchBinding(key('a', { metaKey: true }), { mac: true, inCanvas: true })?.command).toBe(
      'edit.selectAll',
    );
  });

  it('takes zoom and fit UNMODIFIED, leaving the browser its own zoom', () => {
    expect(matchBinding(key('0'))?.command).toBe('view.fit');
    expect(matchBinding(key('='))?.command).toBe('view.zoomIn');
    expect(matchBinding(key('-'))?.command).toBe('view.zoomOut');
    // ⌘0 / ⌘+ / ⌘− stay the browser's page zoom. A page that fights them zooms
    // twice, and the user cannot undo either half.
    expect(matchBinding(key('0', { metaKey: true }), { mac: true })).toBeNull();
    expect(matchBinding(key('=', { metaKey: true }), { mac: true })).toBeNull();
    expect(matchBinding(key('-', { metaKey: true }), { mac: true })).toBeNull();
  });

  it('reaches the shortcuts sheet on layouts that shift `?` and on those that do not', () => {
    expect(matchBinding(key('?', { shiftKey: true }))?.command).toBe('help.shortcuts');
    expect(matchBinding(key('?'))?.command).toBe('help.shortcuts');
  });

  it('claims no combination the browser will not give up', () => {
    // ⌘W closes the tab, ⌘T opens one, ⌘N a window, ⌘Q quits: `preventDefault`
    // does not reach any of them, so a binding on one is a binding that looks
    // broken. Asserted rather than remembered.
    const reserved = ['w', 't', 'n', 'q', 'l'];
    for (const k of reserved) {
      const binding = KEY_BINDINGS.find((b) => b.key === k && b.modifiers.startsWith('mod'));
      expect(binding, `⌘${k.toUpperCase()} is the browser's, not ours`).toBeUndefined();
    }
  });
});

describe('isTypingTarget', () => {
  it('protects text entry, and only text entry', () => {
    const text = document.createElement('input');
    text.type = 'text';
    expect(isTypingTarget(text)).toBe(true);

    const numeric = document.createElement('input');
    numeric.type = 'number';
    // Typing 2400 into a dimension field must not also arm the balcony tool.
    expect(isTypingTarget(numeric)).toBe(true);

    const check = document.createElement('input');
    check.type = 'checkbox';
    expect(isTypingTarget(check)).toBe(false);

    expect(isTypingTarget(document.createElement('textarea'))).toBe(true);
    expect(isTypingTarget(document.createElement('button'))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });

  it('honours an explicit opt-out on an ancestor', () => {
    const panel = document.createElement('div');
    panel.setAttribute('data-garh-keys', 'off');
    const button = document.createElement('button');
    panel.appendChild(button);
    expect(isTypingTarget(button)).toBe(true);
  });
});

describe('formatShortcut', () => {
  const undo: KeyBinding | undefined = KEY_BINDINGS.find((b) => b.command === 'edit.undo');

  it('speaks each platform', () => {
    expect(undo).toBeDefined();
    if (!undo) return;
    expect(formatShortcut(undo, true)).toBe('⌘Z');
    expect(formatShortcut(undo, false)).toBe('Ctrl+Z');
  });
});
