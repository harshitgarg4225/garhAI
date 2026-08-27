/**
 * The §12 contract, asserted across EVERY tool at once.
 *
 * Each tool has its own spec next door. This file is the cross-cutting one: it
 * takes the registry, activates every machine in turn, and checks the promises
 * §12 makes about all of them — Esc cancels, Enter commits, typing a number
 * overrides the mouse, and the keyboard map owns the letters when nothing is
 * being drawn.
 *
 * A new tool that forgets one of these fails here rather than in a bug report,
 * which is the entire reason the guarantees live in `BaseTool` and not in seven
 * separate good intentions.
 */

import { describe, expect, it } from 'vitest';

import { validateOpShape } from '@garh/model';

import {
  formatShortcut,
  KEY_BINDINGS,
  matchBinding,
  TOOL_IDS,
  TOOL_SHORTCUT,
  type CommandId,
  type ToolId,
} from '../../../lib/keymap';
import type { FurnitureItem } from '../../../lib/schemas';
import { createTool, TOOL_META } from './registry';
import { FIXTURE_IDS, key, makeCtx, ptr } from './toolTestKit';
import type { Tool, ToolContext } from './types';

// ---------------------------------------------------------------------------
// Activating every tool
// ---------------------------------------------------------------------------

const SOFA: FurnitureItem = {
  id: 'sofa-3seat-1800x850',
  name: '3-seater sofa',
  category: 'living',
  widthMm: 1800,
  depthMm: 850,
  heightMm: 800,
  clearanceMm: 0,
  assetUrl: null,
  roomTypes: [],
};

/** A context each tool can actually do something in. */
function ctxFor(id: ToolId): ToolContext {
  return makeCtx({
    selectedIds: id === 'select' ? [FIXTURE_IDS.wallSpine] : [],
    settings: { furnitureCatalogId: SOFA.id },
    furnitureCatalog: new Map([[SOFA.id, SOFA]]),
  });
}

/** Drive each tool out of `idle` the way a pointer would. */
const ACTIVATE: Readonly<Record<ToolId, (tool: Tool, ctx: ToolContext) => void>> = {
  select: (tool, ctx) => {
    tool.onPointerDown(ctx, ptr(3000, 2000));
    tool.onPointerMove(ctx, ptr(3115, 2000));
  },
  wall: (tool, ctx) => {
    tool.onPointerDown(ctx, ptr(1150, 1150));
  },
  door: (tool, ctx) => {
    tool.onPointerMove(ctx, ptr(1500, 0));
  },
  window: (tool, ctx) => {
    tool.onPointerMove(ctx, ptr(1500, 0));
  },
  stair: (tool, ctx) => {
    tool.onPointerMove(ctx, ptr(1150, 1150));
  },
  balcony: (tool, ctx) => {
    tool.onPointerDown(ctx, ptr(1150, 4600));
  },
  measure: (tool, ctx) => {
    tool.onPointerDown(ctx, ptr(1150, 1150));
  },
  furniture: (tool, ctx) => {
    tool.onPointerMove(ctx, ptr(2000, 2000));
  },
};

function activated(id: ToolId): { tool: Tool; ctx: ToolContext } {
  const ctx = ctxFor(id);
  const tool = createTool(id);
  ACTIVATE[id](tool, ctx);
  return { tool, ctx };
}

// ---------------------------------------------------------------------------
// The registry
// ---------------------------------------------------------------------------

describe('the registry', () => {
  it('builds all eight tools, each answering to its own id', () => {
    expect(TOOL_IDS).toHaveLength(8);
    for (const id of TOOL_IDS) {
      expect(createTool(id).id).toBe(id);
    }
  });

  it('hands back a FRESH machine every time — no resumed chain from ten minutes ago', () => {
    const first = createTool('wall');
    const ctx = ctxFor('wall');
    first.onPointerDown(ctx, ptr(1150, 1150));
    expect(first.phase).toBe('drawing');
    expect(createTool('wall').phase).toBe('idle');
  });

  it('describes every tool for the tool rail, agreeing with the keyboard map', () => {
    for (const id of TOOL_IDS) {
      const meta = TOOL_META[id];
      expect(meta.id).toBe(id);
      expect(meta.label.length).toBeGreaterThan(0);
      expect(meta.description.length).toBeGreaterThan(0);
      expect(meta.shortcut).toBe(TOOL_SHORTCUT[id]);
    }
  });

  it('marks measure as the only tool that changes nothing', () => {
    const readOnly = TOOL_IDS.filter((id) => !TOOL_META[id].mutates);
    expect(readOnly).toEqual(['measure']);
  });
});

// ---------------------------------------------------------------------------
// The three guarantees
// ---------------------------------------------------------------------------

describe('every tool starts idle and does nothing', () => {
  it.each([...TOOL_IDS])('%s', (id) => {
    const tool = createTool(id);
    const ctx = ctxFor(id);
    expect(tool.phase).toBe('idle');
    expect(tool.commit(ctx)).toBeNull();
  });
});

describe('guarantee 1 — Esc cancels, from any phase, emitting nothing', () => {
  it.each([...TOOL_IDS])('%s', (id) => {
    const { tool, ctx } = activated(id);
    expect(tool.phase, `${id} should have left idle`).not.toBe('idle');

    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
  });

  it.each([...TOOL_IDS])('%s declines Esc while idle, so a dialog can close', (id) => {
    const tool = createTool(id);
    expect(tool.onKey(ctxFor(id), key('Escape')).handled).toBe(false);
  });

  it.each([...TOOL_IDS])('%s can be cancelled twice without complaint', (id) => {
    const { tool } = activated(id);
    tool.cancel();
    tool.cancel();
    expect(tool.phase).toBe('idle');
  });
});

describe('guarantee 2 — Enter commits, and never emits a malformed op', () => {
  it.each([...TOOL_IDS])('%s', (id) => {
    const { tool, ctx } = activated(id);
    const response = tool.onKey(ctx, key('Enter'));
    const commit = response.commit;
    if (!commit) return; // nothing complete enough to commit yet — legitimate

    expect(commit.label.length).toBeGreaterThan(0);
    expect(commit.ops.length).toBeGreaterThan(0);
    for (const op of commit.ops) {
      expect(validateOpShape(op), `${id} emitted a malformed ${op.type}`).toEqual([]);
    }
  });

  it('measure is the tool that commits nothing, by design', () => {
    const { tool, ctx } = activated('measure');
    expect(tool.commit(ctx)).toBeNull();
  });
});

describe('guarantee 3 — typing a number overrides the mouse', () => {
  it.each([...TOOL_IDS])('%s claims digits once it is drawing', (id) => {
    const { tool } = activated(id);
    expect(tool.wantsKey(key('3'))).toBe(true);
  });

  it.each([...TOOL_IDS])('%s leaves the keyboard map alone while idle', (id) => {
    const tool = createTool(id);
    // Every §12 map key: the tool must not steal any of them when nothing is
    // being drawn. `3` is the second floor, `m` is measure, Tab is the 3D view.
    for (const k of ['1', '2', '3', 'v', 'w', 'd', 'n', 's', 'b', 'm', 'f', 'g', 'Tab']) {
      expect(tool.wantsKey(key(k)), `${id} must not claim "${k}" while idle`).toBe(false);
    }
  });

  it.each([...TOOL_IDS])('%s never claims a modified key', (id) => {
    const { tool } = activated(id);
    expect(tool.wantsKey(key('z', { metaKey: true }))).toBe(false);
    expect(tool.wantsKey(key('z', { ctrlKey: true }))).toBe(false);
    expect(tool.wantsKey(key('y', { metaKey: true }))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The preview envelope
// ---------------------------------------------------------------------------

describe('the published preview', () => {
  it.each([...TOOL_IDS])('%s always publishes a complete envelope', (id) => {
    const { tool, ctx } = activated(id);
    const preview = tool.preview(ctx);
    expect(preview.toolId).toBe(id);
    expect(preview.phase).toBe(tool.phase);
    expect(typeof preview.hint).toBe('string');
    expect(preview.hint.length).toBeGreaterThan(0);
    expect(Array.isArray(preview.readouts)).toBe(true);
    expect(Array.isArray(preview.chips)).toBe(true);
    expect(Number.isInteger(preview.version)).toBe(true);
  });

  it.each([...TOOL_IDS])('%s bumps its version when something visible changed', (id) => {
    const ctx = ctxFor(id);
    const tool = createTool(id);
    const before = tool.preview(ctx).version;
    ACTIVATE[id](tool, ctx);
    expect(tool.preview(ctx).version).toBeGreaterThan(before);
  });

  it.each([...TOOL_IDS])('%s reports every chip with human text and a fix path', (id) => {
    const { tool, ctx } = activated(id);
    for (const chip of tool.preview(ctx).chips) {
      expect(chip.text.length).toBeGreaterThan(0);
      expect(['info', 'warning', 'error']).toContain(chip.severity);
      // §15: a chip is never a dead end — it cites something or suggests a fix.
      expect(chip.cite !== null || chip.fix !== null).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// The keyboard map (§12)
// ---------------------------------------------------------------------------

describe('the §12 keyboard map', () => {
  function ev(
    k: string,
    mods: { meta?: boolean; ctrl?: boolean; shift?: boolean; alt?: boolean } = {},
  ) {
    return {
      key: k,
      metaKey: mods.meta ?? false,
      ctrlKey: mods.ctrl ?? false,
      shiftKey: mods.shift ?? false,
      altKey: mods.alt ?? false,
      repeat: false,
    };
  }

  function commandFor(
    k: string,
    mods: Parameters<typeof ev>[1] = {},
    options: { mac?: boolean; inCanvas?: boolean } = {},
  ): CommandId | null {
    return (
      matchBinding(ev(k, mods), { mac: options.mac ?? true, inCanvas: options.inCanvas ?? true })
        ?.command ?? null
    );
  }

  it('binds V W D N S B M F to the eight tools', () => {
    const expected: readonly (readonly [string, CommandId])[] = [
      ['v', 'tool.select'],
      ['w', 'tool.wall'],
      ['d', 'tool.door'],
      ['n', 'tool.window'],
      ['s', 'tool.stair'],
      ['b', 'tool.balcony'],
      ['m', 'tool.measure'],
      ['f', 'tool.furniture'],
    ];
    for (const [k, command] of expected) {
      expect(commandFor(k)).toBe(command);
      expect(commandFor(k.toUpperCase())).toBe(command);
    }
  });

  it('binds undo and redo on both platform idioms', () => {
    expect(commandFor('z', { meta: true }, { mac: true })).toBe('edit.undo');
    expect(commandFor('z', { ctrl: true }, { mac: false })).toBe('edit.undo');
    expect(commandFor('y', { meta: true }, { mac: true })).toBe('edit.redo');
    expect(commandFor('z', { meta: true, shift: true }, { mac: true })).toBe('edit.redo');
  });

  it('binds 1 / 2 / 3 to the storeys and G to the snap toggle', () => {
    expect(commandFor('1')).toBe('storey.1');
    expect(commandFor('2')).toBe('storey.2');
    expect(commandFor('3')).toBe('storey.3');
    expect(commandFor('g')).toBe('snap.toggle');
  });

  it('binds Tab to the 2D↔3D switch, but only inside the canvas', () => {
    expect(commandFor('Tab', {}, { inCanvas: true })).toBe('view.toggle');
    // Everywhere else Tab must still move focus — §15 accessibility.
    expect(commandFor('Tab', {}, { inCanvas: false })).toBeNull();
  });

  it('binds Esc and Enter to the tool verbs', () => {
    expect(commandFor('Escape')).toBe('tool.cancel');
    expect(commandFor('Enter', {}, { inCanvas: true })).toBe('tool.commit');
  });

  it('reserves Alt entirely', () => {
    expect(commandFor('v', { alt: true })).toBeNull();
    expect(commandFor('z', { meta: true, alt: true })).toBeNull();
  });

  it('gives every binding a label and a plain-language description', () => {
    for (const binding of KEY_BINDINGS) {
      expect(binding.label.length).toBeGreaterThan(0);
      expect(binding.description.length).toBeGreaterThan(0);
      expect(binding.description.endsWith('.')).toBe(true);
    }
  });

  it('renders shortcuts the way each platform writes them', () => {
    const undo = KEY_BINDINGS.find((b) => b.command === 'edit.undo' && b.modifiers === 'mod');
    expect(undo).toBeDefined();
    if (!undo) return;
    expect(formatShortcut(undo, true)).toBe('⌘Z');
    expect(formatShortcut(undo, false)).toBe('Ctrl+Z');
  });
});
