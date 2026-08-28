/**
 * The registry's gates, each broken on purpose.
 *
 * Every `expect(...).toThrow()` below is a negative control in its own right:
 * the assertion is that a specific wiring mistake is REFUSED, and the way to
 * prove such a test can fail is to make the mistake and watch it be accepted.
 * So each one is paired with the closest legal registration, which must be
 * accepted — a gate that rejected everything would pass all the throw tests and
 * be worse than no gate at all.
 */

import { describe, expect, it, vi } from 'vitest';

import { KEY_BINDINGS } from '../../lib/keymap';
import { BindingSyntaxError } from './binding';
import { CommandRegistry, RegistryError } from './registry';
import type { Command } from './types';

function command(partial: Partial<Command> & Pick<Command, 'id'>): Command {
  return {
    title: partial.title ?? 'Do the thing',
    group: partial.group ?? 'Edit',
    run: partial.run === undefined ? () => undefined : partial.run,
    ...partial,
  };
}

describe('registration', () => {
  it('registers, exposes, and unregisters through the returned function', () => {
    const registry = new CommandRegistry();
    const off = registry.register(command({ id: 'a.one' }));
    expect(registry.has('a.one')).toBe(true);
    expect(registry.size).toBe(1);
    off();
    expect(registry.has('a.one')).toBe(false);
  });

  it('refuses a duplicate id rather than replacing the working command', () => {
    const registry = new CommandRegistry();
    registry.register(command({ id: 'a.one' }));
    expect(() => registry.register(command({ id: 'a.one' }))).toThrow(RegistryError);
    // …and the original is untouched, not half-replaced.
    expect(registry.get('a.one')?.title).toBe('Do the thing');
  });

  it('refuses an unparseable binding at registration, not at first press', () => {
    const registry = new CommandRegistry();
    expect(() => registry.register(command({ id: 'a.one', bindings: ['mod+nope'] }))).toThrow(
      BindingSyntaxError,
    );
    expect(registry.has('a.one')).toBe(false);
  });

  it('refuses a second command on a key another command already holds', () => {
    const registry = new CommandRegistry();
    registry.register(command({ id: 'a.one', bindings: ['mod+j'] }));
    expect(() => registry.register(command({ id: 'a.two', bindings: ['mod+j'] }))).toThrow(
      /already holds/,
    );
    // The near-miss must still be accepted, or the gate is just a wall.
    expect(() =>
      registry.register(command({ id: 'a.two', bindings: ['mod+shift+j'] })),
    ).not.toThrow();
  });

  it('registerAll is atomic — a bad member takes the whole batch with it', () => {
    const registry = new CommandRegistry();
    expect(() =>
      registry.registerAll([
        command({ id: 'a.one' }),
        command({ id: 'a.two' }),
        command({ id: 'a.one' }), // duplicate: the batch must roll back
      ]),
    ).toThrow(RegistryError);
    // A half-installed set is a palette that looks plausible and is missing
    // everything after the mistake.
    expect(registry.size).toBe(0);
  });
});

describe('ownership of a key, against lib/keymap.ts', () => {
  it('refuses to bind a key the app keymap already fires', () => {
    const registry = new CommandRegistry();
    // `w` is tool.wall in KEY_BINDINGS, handled by lib/shortcuts.ts on document.
    // Registering it here would put a second listener on the same key.
    expect(() => registry.register(command({ id: 'x.wall', bindings: ['w'] }))).toThrow(
      /already fires as "tool.wall"/,
    );
    expect(() => registry.register(command({ id: 'x.undo', bindings: ['mod+z'] }))).toThrow(
      /already fires as "edit.undo"/,
    );
  });

  it('accepts a key the app keymap leaves alone', () => {
    const registry = new CommandRegistry();
    expect(() =>
      registry.register(command({ id: 'x.palette', bindings: ['mod+k'] })),
    ).not.toThrow();
    expect(() => registry.register(command({ id: 'x.sheet', bindings: ['mod+/'] }))).not.toThrow();
  });

  it('refuses keyOwner: "keymap" for a key the keymap does NOT claim', () => {
    // This is the bug-4 direction: a command that says "somebody else fires my
    // key" when nobody does. It would sit in the cheatsheet advertising a
    // shortcut that has never once worked.
    const registry = new CommandRegistry();
    expect(() =>
      registry.register(command({ id: 'x.ghost', bindings: ['mod+k'], keyOwner: 'keymap' })),
    ).toThrow(/does not match that keystroke/);
  });

  it('refuses a mirror pointing at another command’s key', () => {
    const registry = new CommandRegistry();
    // `w` is claimed, but by tool.wall — mirroring it under the wrong id would
    // put the wrong label next to the key in the cheatsheet.
    expect(() =>
      registry.register(command({ id: 'tool.door', bindings: ['w'], keyOwner: 'keymap' })),
    ).toThrow(/lib\/keymap\.ts fires .* as "tool\.wall"/);
  });

  it('accepts a correct mirror of every binding in KEY_BINDINGS', () => {
    // The gate must not reject the legitimate case, or defaultCommands.ts
    // could never register at all.
    for (const binding of KEY_BINDINGS) {
      const registry = new CommandRegistry();
      const key = binding.key.length === 1 ? binding.key.toLowerCase() : binding.key;
      const spec =
        binding.modifiers === 'none'
          ? key
          : binding.modifiers === 'shift'
            ? `shift+${key}`
            : binding.modifiers === 'mod'
              ? `mod+${key}`
              : `mod+shift+${key}`;
      expect(() =>
        registry.register(command({ id: binding.command, bindings: [spec], keyOwner: 'keymap' })),
      ).not.toThrow();
    }
  });

  it('never matches a keymap-owned command itself — that would be the double fire', () => {
    const registry = new CommandRegistry();
    registry.register(command({ id: 'tool.wall', bindings: ['w'], keyOwner: 'keymap' }));
    const event = { key: 'w', metaKey: false, ctrlKey: false, shiftKey: false, altKey: false };
    expect(registry.match(event, false)).toBeNull();
  });
});

describe('run', () => {
  it('runs an enabled command and says so', () => {
    const registry = new CommandRegistry();
    const run = vi.fn();
    registry.register(command({ id: 'a.one', run }));
    expect(registry.run('a.one', 'palette')).toBe('ran');
    expect(run).toHaveBeenCalledWith('palette');
  });

  it('refuses a disabled command without calling it', () => {
    const registry = new CommandRegistry();
    const run = vi.fn();
    let available = false;
    registry.register(command({ id: 'a.one', run, enabled: () => available }));

    expect(registry.run('a.one', 'palette')).toBe('disabled');
    expect(run).not.toHaveBeenCalled();

    // NEGATIVE CONTROL for the assertion above: with the predicate satisfied
    // the very same call must go through, so "not called" is attributable to
    // the gate and not to a command that could never run.
    available = true;
    expect(registry.run('a.one', 'palette')).toBe('ran');
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('distinguishes a documentation-only command from a disabled one', () => {
    const registry = new CommandRegistry();
    registry.register(command({ id: 'a.doc', run: null }));
    expect(registry.run('a.doc', 'palette')).toBe('documentation-only');
  });

  it('warns loudly when asked for a command nobody registered', () => {
    // The furniture-layer failure, in registry form: something holds an id the
    // registry has never seen. Silence here is how that goes unnoticed.
    const registry = new CommandRegistry();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(registry.run('a.missing', 'api')).toBe('unknown');
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});

describe('listings', () => {
  it('separates what the palette may list from what the cheatsheet may list', () => {
    const registry = new CommandRegistry();
    registry.register(command({ id: 'a.normal', bindings: ['mod+j'] }));
    registry.register(command({ id: 'a.hidden', hidden: true, bindings: ['mod+shift+j'] }));
    registry.register(command({ id: 'a.doc', run: null, bindings: ['mod+e'] }));
    registry.register(command({ id: 'a.keyless' }));

    expect(registry.paletteCommands().map((c) => c.id)).toEqual(['a.normal', 'a.keyless']);
    expect(registry.boundCommands().map((c) => c.id)).toEqual(['a.normal', 'a.hidden', 'a.doc']);
  });

  it('orders by group and returns a cached array so it is a legal snapshot', () => {
    const registry = new CommandRegistry();
    registry.register(command({ id: 'h.one', group: 'Help' }));
    registry.register(command({ id: 't.one', group: 'Tools' }));
    expect(registry.all().map((c) => c.id)).toEqual(['t.one', 'h.one']);

    // React 18 throws "getSnapshot should be cached" if this identity changes
    // between reads with no mutation in between.
    expect(registry.all()).toBe(registry.all());
    registry.register(command({ id: 't.two', group: 'Tools' }));
    expect(registry.all().map((c) => c.id)).toEqual(['t.one', 't.two', 'h.one']);
  });

  it('notifies subscribers on every membership change', () => {
    const registry = new CommandRegistry();
    const listener = vi.fn();
    const off = registry.subscribe(listener);
    const unregister = registry.register(command({ id: 'a.one' }));
    expect(listener).toHaveBeenCalledTimes(1);
    unregister();
    expect(listener).toHaveBeenCalledTimes(2);
    off();
    registry.register(command({ id: 'a.two' }));
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
