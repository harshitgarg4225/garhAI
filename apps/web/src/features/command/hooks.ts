/**
 * hooks.ts — the React bindings for the registry: read it, and listen for its
 * keys.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE GUARD THIS FILE IS REALLY ABOUT
 * ════════════════════════════════════════════════════════════════════════════
 * A shortcut layer's signature bug is firing while someone is typing. You are
 * three characters into renaming a room, you type "w", and the wall tool arms
 * itself. `lib/keymap.ts` calls that out in its own header and solves it with
 * `isTypingTarget`, which knows about inputs, textareas, selects,
 * `contenteditable`, and the `data-garh-keys="off"` opt-out, and knows that a
 * checkbox is not typed into so a letter shortcut over one is safe.
 *
 * This module IMPORTS that function. It does not reimplement it, and it must
 * never grow its own version. Two definitions of "the user is typing" is how
 * one of them drifts, and the drifted one is always the one your feature is
 * using. There is exactly one guard in this app and this listener uses it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY A SECOND `document` LISTENER IS SAFE HERE
 * ════════════════════════════════════════════════════════════════════════════
 * `lib/keymap.ts` warns against adding a second `keydown` listener, and it is
 * right — `stopPropagation()` does not stop a sibling listener on the same
 * node, so two listeners that both act on one key produce the double-undo bug
 * `stores/ui.ts` documents at length.
 *
 * This listener cannot reach that state, because `registry.match()` only
 * considers commands whose `keyOwner` is `'command'`, and `registry.register()`
 * REFUSES to register such a command on a key `lib/keymap.ts` already claims —
 * asking that module's own `matchBinding` rather than keeping an opinion about
 * it. The two listeners are therefore disjoint by construction, and the
 * construction is enforced at registration time, not by everyone remembering.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EVENT ORDER
 * ════════════════════════════════════════════════════════════════════════════
 * Bubble phase on `document`, deliberately, matching `useKeyboardMap`. Capture
 * would run this layer BEFORE the canvas's own scoped maps, which are attached
 * to the canvas element and must get first refusal on a key while a tool is
 * mid-draw. `useOverlayPointerGuard` had to reach for a native listener between
 * two layers for the pointer case precisely because React's delegation runs at
 * the app root and therefore too late; nothing analogous applies here, because
 * every participant in the keyboard path is already a native listener.
 */

import { useEffect, useSyncExternalStore } from 'react';

import { isTypingTarget } from '../../lib/keymap';
import { isMacPlatform, type KeyEventLike } from './binding';
import { commandRegistry, type CommandRegistry } from './registry';
import type { Command } from './types';

/**
 * Subscribe to the registry's contents.
 *
 * `useSyncExternalStore` rather than a `useState` + effect pair: the registry
 * is mutated from effects in other components (a feature registering its own
 * commands on mount), and the tearing-free read is what keeps the palette from
 * rendering a list that no longer exists by the time you press Enter.
 */
export function useCommands(registry: CommandRegistry = commandRegistry): readonly Command[] {
  return useSyncExternalStore(
    (listener) => registry.subscribe(listener),
    () => registry.all(),
    () => registry.all(),
  );
}

/**
 * Should this keystroke be considered at all?
 *
 * Exported so it can be tested directly, and so the ONE reason a keystroke is
 * ignored lives in one readable place instead of being spread through a
 * listener body.
 */
export function shouldConsiderKeyEvent(event: KeyboardEvent): boolean {
  // Auto-repeat: holding a key would otherwise open and close the palette
  // dozens of times a second.
  if (event.repeat) return false;
  // THE guard. See the header — one definition, imported, never copied.
  if (isTypingTarget(event.target)) return false;
  return true;
}

/**
 * The command a keystroke means, or null.
 *
 * Pure apart from the registry read, so a test can ask "what would this event
 * have done?" without a DOM, a React tree or a listener.
 */
export function resolveKeyEvent(
  registry: CommandRegistry,
  event: KeyboardEvent,
  mac: boolean,
): Command | null {
  if (!shouldConsiderKeyEvent(event)) return null;
  return registry.match(event as KeyEventLike, mac);
}

export interface CommandKeysOptions {
  readonly registry?: CommandRegistry;
  /** Off while a focus-trapped dialog owns the keyboard. */
  readonly enabled?: boolean;
  /** Listen somewhere other than `document`. Tests use it; the app does not. */
  readonly target?: Document | HTMLElement | null;
  /** Override platform detection. Tests only. */
  readonly mac?: boolean;
}

/**
 * Arm the registry's own key bindings for as long as the caller is mounted.
 *
 * A disabled command does NOT consume its key: it is not run and nothing is
 * prevented, so the keystroke falls through to whatever else wants it. That is
 * `useKeyboardMap`'s convention ("commands with no handler do nothing and do
 * not consume the keystroke") and the right one — swallowing a key to do
 * nothing is indistinguishable from a broken keyboard.
 */
export function useCommandKeys(options: CommandKeysOptions = {}): void {
  const { registry = commandRegistry, enabled = true, target, mac } = options;

  useEffect(() => {
    if (!enabled) return undefined;
    const node: Document | HTMLElement | null =
      target ?? (typeof document === 'undefined' ? null : document);
    if (node === null) return undefined;

    const isMac = mac ?? isMacPlatform();

    const onKeyDown = (event: Event): void => {
      if (!(event instanceof KeyboardEvent)) return;
      const command = resolveKeyEvent(registry, event, isMac);
      if (command === null) return;
      if (registry.run(command.id, 'key') !== 'ran') return;
      event.preventDefault();
      event.stopPropagation();
    };

    node.addEventListener('keydown', onKeyDown);
    return () => node.removeEventListener('keydown', onKeyDown);
  }, [registry, enabled, target, mac]);
}

/**
 * Register commands for the lifetime of the calling component.
 *
 * The array must be stable (a `useMemo`, or a module constant) — it is in the
 * dependency list, and a fresh array each render would unregister and
 * re-register every command on every render, which is both wasteful and a
 * window in which the palette is empty.
 */
export function useRegisterCommands(
  commands: readonly Command[],
  registry: CommandRegistry = commandRegistry,
): void {
  useEffect(() => registry.registerAll(commands), [commands, registry]);
}
