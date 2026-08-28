/**
 * The default command set, held to two promises that are easy to break quietly.
 *
 * ONE — the mirror is complete and correct. Every binding in `lib/keymap.ts`
 * appears here, under its own command id, matching the same keystrokes. The
 * oracle is that module's OWN `matchBinding`, so this test cannot drift away
 * from the thing it is checking; it can only agree with it or fail.
 *
 * TWO — no listed command is inert. Every command the palette will offer is
 * actually run against the real stores, and the test fails unless observable
 * state moved. This repository has shipped a panel of controls wired to a
 * store nothing read, and `features/layers/mapping.test.ts` exists because of
 * it. A palette is the same trap with a search box in front of it.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { makeTwoRoomPlanWithOpenings } from '@garh/model';

import { KEY_BINDINGS, matchBinding, type CommandId } from '../../lib/keymap';
import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import { matchesBinding, parseBinding, synthesiseEvent } from './binding';
import { bindingSpecOf, defaultCommands, keymapMirrorCommands } from './defaultCommands';
import { CommandRegistry } from './registry';
import { useCommandUiStore } from './store';

/** A fresh registry with the real default set in it. */
function loaded(): CommandRegistry {
  const registry = new CommandRegistry();
  registry.registerAll(defaultCommands());
  return registry;
}

beforeEach(() => {
  useUiStore.setState({
    activeTool: 'select',
    viewMode: '2d',
    snapMode: 'module',
    activeStoreyId: null,
    canvasLayers: {
      grid: true,
      dimensions: true,
      roomTags: true,
      furniture: true,
      compliance: true,
    },
  });
  useModelStore.setState({ undoStack: [], redoStack: [] });
  useCommandUiStore.setState({ paletteOpen: false, cheatsheetOpen: false, query: '' });
});

describe('the mirror of lib/keymap.ts', () => {
  it('covers every command in KEY_BINDINGS, exactly once', () => {
    const expected = new Set<CommandId>(KEY_BINDINGS.map((b) => b.command));
    const mirrored = keymapMirrorCommands().map((c) => c.id);
    expect(new Set(mirrored)).toEqual(expected);
    expect(mirrored.length).toBe(expected.size);
  });

  it('matches exactly the keystrokes lib/keymap.ts matches', () => {
    const registry = loaded();
    for (const keyBinding of KEY_BINDINGS) {
      const parsed = parseBinding(bindingSpecOf(keyBinding));
      for (const mac of [true, false]) {
        const event = synthesiseEvent(parsed, mac);
        // The oracle: what does the app's own matcher say this keystroke is?
        const claimed = matchBinding(event, { mac, inCanvas: true });
        expect(claimed?.command, `${keyBinding.label} on ${mac ? 'mac' : 'pc'}`).toBe(
          keyBinding.command,
        );
        // And does the registry hold that same command under a binding that
        // agrees the event belongs to it?
        const bindings = registry.bindingsOf(keyBinding.command);
        expect(
          bindings.some((b) => matchesBinding(event, b, { mac })),
          `${keyBinding.command} has no binding matching ${keyBinding.label}`,
        ).toBe(true);
      }
    }
  });

  it('NEGATIVE CONTROL: the equivalence check rejects a wrong spec', () => {
    // If this passed, the test above would pass with any spec at all.
    const wrong = parseBinding('mod+shift+q');
    const wall = KEY_BINDINGS.find((b) => b.command === 'tool.wall');
    expect(wall).toBeDefined();
    const event = synthesiseEvent(parseBinding(bindingSpecOf(wall!)), false);
    expect(matchesBinding(event, wrong, { mac: false })).toBe(false);
  });

  it('dedupes the two registrations of ? into one printed key', () => {
    // lib/keymap.ts registers `?` shifted AND unshifted so it works on every
    // layout. Both are real; showing the glyph twice would read as a bug.
    expect(loaded().bindingsOf('help.shortcuts')).toHaveLength(1);
    expect(KEY_BINDINGS.filter((b) => b.command === 'help.shortcuts')).toHaveLength(2);
  });

  it('keeps genuinely different keys for one command', () => {
    // ⌘Y and ⇧⌘Z are both redo and both belong on the sheet.
    expect(loaded().bindingsOf('edit.redo')).toHaveLength(2);
  });
});

describe('the whole default set is legal', () => {
  it('registers without a single gate firing', () => {
    // Every gate in registry.ts runs here: parse, collision, and the two
    // directions of keymap ownership. A default set that cannot register is a
    // palette that throws at boot.
    expect(() => loaded()).not.toThrow();
  });

  it('introduces two keys of its own, on keys lib/keymap.ts does not claim', () => {
    const registry = loaded();
    expect(registry.bindingsOf('palette.open').map((b) => b.source)).toEqual(['mod+k']);
    expect(registry.bindingsOf('help.cheatsheet').map((b) => b.source)).toEqual(['mod+/']);
    // Both are ours to fire — the mirrored ones are not.
    const modK = { key: 'k', metaKey: false, ctrlKey: true, shiftKey: false, altKey: false };
    expect(registry.match(modK, false)?.id).toBe('palette.open');
  });

  it('documents ? without claiming it', () => {
    const registry = loaded();
    // The cheatsheet must list the key; the registry must not fire it, because
    // lib/keymap.ts + PlanPage.tsx already do.
    expect(registry.boundCommands().map((c) => c.id)).toContain('help.shortcuts');
    const question = { key: '?', metaKey: false, ctrlKey: false, shiftKey: true, altKey: false };
    expect(registry.match(question, false)).toBeNull();
  });
});

describe('no command in the palette is inert', () => {
  /** Everything that visibly changes when a command does its job. */
  function snapshot(): string {
    const ui = useUiStore.getState();
    const command = useCommandUiStore.getState();
    return JSON.stringify({
      activeTool: ui.activeTool,
      viewMode: ui.viewMode,
      snapMode: ui.snapMode,
      activeStoreyId: ui.activeStoreyId,
      canvasLayers: ui.canvasLayers,
      paletteOpen: command.paletteOpen,
      cheatsheetOpen: command.cheatsheetOpen,
    });
  }

  it('every enabled palette command moves observable state', () => {
    const registry = loaded();
    const checked: string[] = [];
    for (const command of registry.paletteCommands()) {
      // Reset before each one so the order of the loop cannot decide the
      // result, and so an idempotent setter is not asked to change a value it
      // is already sitting on — `tool.select` starting on `select` looks inert
      // and is not. Any other tool will do as the starting point.
      useUiStore.setState({
        viewMode: '2d',
        activeTool: command.id === 'tool.wall' ? 'select' : 'wall',
      });
      if (!registry.isEnabled(command.id)) continue;
      const before = snapshot();
      expect(registry.run(command.id, 'palette')).toBe('ran');
      expect(snapshot(), `${command.id} ran and changed nothing`).not.toBe(before);
      checked.push(command.id);
    }
    // Guard against the guard: a loop that checks nothing passes silently.
    expect(checked.length).toBeGreaterThanOrEqual(12);
    expect(checked).toContain('tool.select');
    expect(checked).toContain('tool.wall');
    expect(checked).toContain('view.grid');
    expect(checked).toContain('help.cheatsheet');
  });

  it('disabled commands refuse, and become enabled when their reason goes away', () => {
    const registry = loaded();

    // Undo, on an empty history.
    expect(registry.isEnabled('edit.undo')).toBe(false);
    const before = snapshot();
    expect(registry.run('edit.undo', 'palette')).toBe('disabled');
    expect(snapshot()).toBe(before);

    // Storeys, on a document that has none.
    expect(registry.isEnabled('storey.1')).toBe(false);
    expect(registry.run('storey.1', 'palette')).toBe('disabled');
    expect(useUiStore.getState().activeStoreyId).toBeNull();

    // NEGATIVE CONTROL for both: give the predicate what it wants and the very
    // same call goes through. Without this, an `enabled` that returned false
    // unconditionally would pass every assertion above.
    useModelStore.setState((s) => ({
      doc: { ...s.doc, house: makeTwoRoomPlanWithOpenings().house },
    }));
    expect(registry.isEnabled('storey.1')).toBe(true);
    expect(registry.run('storey.1', 'palette')).toBe('ran');
    expect(useUiStore.getState().activeStoreyId).not.toBeNull();
  });

  it('a tool is offered but refused in the 3D view', () => {
    const registry = loaded();
    expect(registry.isEnabled('tool.wall')).toBe(true);
    useUiStore.setState({ viewMode: '3d' });
    // W walks the camera in 3D (see useNav3d); arming the wall tool there would
    // be a keystroke doing two things at once.
    expect(registry.isEnabled('tool.wall')).toBe(false);
    expect(registry.run('tool.wall', 'palette')).toBe('disabled');
    expect(useUiStore.getState().activeTool).toBe('select');
  });

  it('the documentation-only commands are the ones whose surface we cannot reach', () => {
    const registry = loaded();
    const docOnly = registry
      .all()
      .filter((c) => c.run === null)
      .map((c) => c.id)
      .sort();
    expect(docOnly).toEqual(
      [
        'copilot.focus',
        'edit.delete',
        'edit.selectAll',
        'help.shortcuts',
        'tool.cancel',
        'tool.commit',
        'view.fit',
        'view.zoomIn',
        'view.zoomOut',
      ].sort(),
    );
    // They are still bound, and the cheatsheet still lists them.
    for (const id of docOnly) expect(registry.bindingsOf(id).length).toBeGreaterThan(0);
  });
});
