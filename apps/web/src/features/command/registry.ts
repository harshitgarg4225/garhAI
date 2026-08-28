/**
 * registry.ts — the one list of commands the palette, the key layer and the
 * cheatsheet all read.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THIS FILE IS BUG CLASS 4 TERRITORY, SO IT IS LOUD
 * ════════════════════════════════════════════════════════════════════════════
 * The furniture layer tagged its meshes for hit-testing, documented itself as
 * integrated, and never called `PickRegistry.register` — so every placed item
 * was invisible to clicks and nothing anywhere went red. A command registry has
 * the same shape and the same failure: a command that believes it is
 * registered is a menu entry that does not exist and a shortcut that does
 * nothing, with no compile-time signal either way.
 *
 * Four things here exist only to make that impossible:
 *
 *  1. **Registration is validating, and it throws.** A duplicate id, an
 *     unparseable binding, a binding another command already holds, or a
 *     `keyOwner` that contradicts `lib/keymap.ts` — each is a `RegistryError`
 *     at `register()` time, not a shrug.
 *  2. **`run()` reports why nothing happened.** `'unknown'` (never registered)
 *     is a different answer from `'disabled'` (registered, not available now)
 *     and from `'documentation-only'`. `'unknown'` also warns, because it is
 *     always a wiring bug.
 *  3. **`lib/keymap.ts` is the oracle, not a copy of it.** Whether the app's
 *     fixed key table already claims a key is answered by calling that
 *     module's own `matchBinding` with a synthesised event. There is no second
 *     opinion here to drift.
 *  4. **Registration is observable.** `subscribe()` + `snapshot()` feed
 *     `useSyncExternalStore`, so a component cannot render a stale command list
 *     and a test can assert that mounting the layer really did populate this.
 *
 * The registry is a plain class, and the singleton at the bottom is the app's
 * instance. Plain class for the same reason `PickRegistry` is one: membership
 * changes on mount and unmount, never during interaction, and nothing should
 * re-render because a feature registered a command — the subscription exists so
 * the palette can pick the change up, not so every keystroke costs a render.
 */

import { matchBinding, type CommandId } from '../../lib/keymap';
import {
  bindingsCollide,
  isMacPlatform,
  matchesBinding,
  parseBinding,
  synthesiseEvent,
  type KeyEventLike,
  type ParsedBinding,
} from './binding';
import { groupOrder, type Command, type CommandOutcome, type CommandSource } from './types';

export class RegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RegistryError';
  }
}

/** A command plus its parsed bindings, which are computed once at register(). */
interface Entry {
  readonly command: Command;
  readonly bindings: readonly ParsedBinding[];
  /** Registration order, so an unsorted listing is at least deterministic. */
  readonly seq: number;
}

/**
 * Does `lib/keymap.ts`'s fixed table already fire on this keystroke?
 *
 * Asked on both platforms: a binding that is free on Linux and taken on macOS
 * is taken. `inCanvas: true` widens the question to canvas-scoped bindings too
 * — Delete and Tab are only live inside the drawing surface, but a command
 * registered here would be live everywhere, so overlapping them is still a
 * collision from our side.
 */
function keymapClaim(binding: ParsedBinding): CommandId | null {
  for (const mac of [true, false]) {
    const claimed = matchBinding(synthesiseEvent(binding, mac), { mac, inCanvas: true });
    if (claimed !== null) return claimed.command;
  }
  return null;
}

export class CommandRegistry {
  private readonly entries = new Map<string, Entry>();

  private readonly listeners = new Set<() => void>();

  private cache: readonly Command[] = [];

  private dirty = true;

  private seq = 0;

  get size(): number {
    return this.entries.size;
  }

  /**
   * Add a command. Returns its unregister function — bind it straight to a
   * `useEffect` cleanup so a command cannot outlive the feature that owns it.
   *
   * Throws {@link RegistryError} rather than replacing, skipping or repairing
   * anything. Every throw below is a wiring mistake that would otherwise
   * present as "the shortcut just doesn't work sometimes".
   */
  register(command: Command): () => void {
    if (this.entries.has(command.id)) {
      throw new RegistryError(
        `Command "${command.id}" is already registered. Ids are the palette's ` +
          `identity and the key layer's lookup; silently replacing one would ` +
          `retire a working command the moment two features picked the same name.`,
      );
    }

    const owner = command.keyOwner ?? 'command';
    const bindings = (command.bindings ?? []).map((spec) => parseBinding(spec));

    for (const binding of bindings) {
      const claimed = keymapClaim(binding);

      if (owner === 'command' && claimed !== null) {
        throw new RegistryError(
          `Command "${command.id}" binds ${binding.source}, which lib/keymap.ts ` +
            `already fires as "${claimed}". Both listeners sit on document and ` +
            `stopPropagation does not stop a sibling, so the key would run twice. ` +
            `Set keyOwner: 'keymap' to mirror that command, or pick another key.`,
        );
      }
      if (owner === 'keymap' && claimed === null) {
        throw new RegistryError(
          `Command "${command.id}" declares keyOwner: 'keymap' for ${binding.source}, ` +
            `but lib/keymap.ts does not match that keystroke — so nothing would ` +
            `ever fire it while the cheatsheet advertised that it would.`,
        );
      }
      // A mirror must mirror ITS OWN key. Pointing at another command's key
      // puts the wrong label beside that key in the cheatsheet, and the palette
      // row would claim a shortcut that runs something else entirely.
      if (owner === 'keymap' && claimed !== null && claimed !== command.id) {
        throw new RegistryError(
          `Command "${command.id}" mirrors ${binding.source}, but lib/keymap.ts fires ` +
            `that keystroke as "${claimed}". A mirror must carry the id of the ` +
            `command it mirrors, or the cheatsheet labels the key wrongly.`,
        );
      }

      for (const existing of this.entries.values()) {
        if ((existing.command.keyOwner ?? 'command') !== owner) continue;
        const clash = existing.bindings.find((other) => bindingsCollide(binding, other));
        if (clash !== undefined) {
          throw new RegistryError(
            `Command "${command.id}" binds ${binding.source}, which "${existing.command.id}" ` +
              `already holds as ${clash.source}. One key, one command — otherwise ` +
              `which one runs depends on registration order.`,
          );
        }
      }
    }

    this.entries.set(command.id, { command, bindings, seq: this.seq++ });
    this.invalidate();
    return () => this.unregister(command.id);
  }

  /**
   * Register several, atomically: if any one throws, none of them stay.
   *
   * A half-installed default set is worse than none — the palette would open,
   * look plausible, and be missing whatever came after the mistake.
   */
  registerAll(commands: readonly Command[]): () => void {
    const undo: (() => void)[] = [];
    try {
      for (const command of commands) undo.push(this.register(command));
    } catch (error) {
      for (const off of undo.reverse()) off();
      throw error;
    }
    return () => {
      for (const off of undo.reverse()) off();
    };
  }

  unregister(id: string): void {
    if (this.entries.delete(id)) this.invalidate();
  }

  clear(): void {
    if (this.entries.size === 0) return;
    this.entries.clear();
    this.invalidate();
  }

  has(id: string): boolean {
    return this.entries.has(id);
  }

  get(id: string): Command | undefined {
    return this.entries.get(id)?.command;
  }

  /** Parsed bindings for one command, in declaration order. */
  bindingsOf(id: string): readonly ParsedBinding[] {
    return this.entries.get(id)?.bindings ?? [];
  }

  /**
   * Everything registered, ordered by group then by registration.
   *
   * PERF + CORRECTNESS: the same array instance is returned until membership
   * changes, which is what makes it a legal `useSyncExternalStore` snapshot —
   * a fresh array every call would re-render on every store read and React 18
   * would throw "getSnapshot should be cached".
   */
  all(): readonly Command[] {
    if (this.dirty) {
      this.cache = [...this.entries.values()]
        .sort((a, b) => {
          const byGroup = groupOrder(a.command.group) - groupOrder(b.command.group);
          return byGroup === 0 ? a.seq - b.seq : byGroup;
        })
        .map((entry) => entry.command);
      this.dirty = false;
    }
    return this.cache;
  }

  /** Commands the palette may list: registered, runnable, not hidden. */
  paletteCommands(): readonly Command[] {
    return this.all().filter((command) => command.run !== null && command.hidden !== true);
  }

  /** Commands the cheatsheet may list: everything with at least one binding. */
  boundCommands(): readonly Command[] {
    return this.all().filter((command) => this.bindingsOf(command.id).length > 0);
  }

  isEnabled(id: string): boolean {
    const command = this.entries.get(id)?.command;
    if (command === undefined) return false;
    return command.enabled === undefined || command.enabled();
  }

  /**
   * Run a command by id and say what happened.
   *
   * `'unknown'` also warns: asking for a command that was never registered is
   * always a wiring bug, and the whole reason this feature has a registry is
   * that such bugs are otherwise completely silent.
   */
  run(id: string, source: CommandSource): CommandOutcome {
    const command = this.entries.get(id)?.command;
    if (command === undefined) {
      console.warn(
        `[command] "${id}" was invoked but is not registered. ` +
          `Something is holding an id the registry has never seen.`,
      );
      return 'unknown';
    }
    if (command.run === null) return 'documentation-only';
    if (!this.isEnabled(id)) return 'disabled';
    command.run(source);
    return 'ran';
  }

  /**
   * The command this keystroke means, among the ones WE own the key for.
   *
   * Commands mirrored from `lib/keymap.ts` are skipped: that module's listener
   * is already going to handle them, and matching here as well is the
   * double-fire this registry refuses at registration time.
   */
  match(event: KeyEventLike, mac = isMacPlatform()): Command | null {
    for (const entry of this.entries.values()) {
      if ((entry.command.keyOwner ?? 'command') !== 'command') continue;
      for (const binding of entry.bindings) {
        if (matchesBinding(event, binding, { mac })) return entry.command;
      }
    }
    return null;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private invalidate(): void {
    this.dirty = true;
    for (const listener of this.listeners) listener();
  }
}

/**
 * The app's registry.
 *
 * A module singleton for the reason `features/layers/store.ts` gives about its
 * own store: the consumers cannot see each other. The palette lives in the DOM
 * overlay, the key listener lives on `document`, and a canvas feature that
 * wants to contribute a command lives inside react-three-fiber's separate React
 * root, which React context does not cross.
 */
export const commandRegistry = new CommandRegistry();
