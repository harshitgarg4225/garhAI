/**
 * CommandLayer — the one thing the app shell mounts.
 *
 * It registers the default command set, arms the registry's key bindings, and
 * renders the palette and the cheatsheet. Mount it once, inside the providers,
 * beside `useAppShortcuts()`:
 *
 * ```tsx
 * useAppShortcuts();
 * // …
 * <CommandLayer />
 * ```
 *
 * ════════════════════════════════════════════════════════════════════════════
 * REGISTRATION IS AN EFFECT, AND THAT IS THE POINT
 * ════════════════════════════════════════════════════════════════════════════
 * The commands go in on mount and come out on unmount, through the unregister
 * function `registerAll` returns. Nothing here registers at module scope, so
 * importing this file has no effect on the registry and two tests cannot
 * poison each other.
 *
 * The failure this feature is built to prevent — a module that believes it is
 * registered — is therefore checked at the level that matters:
 * `CommandLayer.test.tsx` mounts this component and then drives ⌘K, arrows and
 * Enter through a real DOM to change real store state. Nothing short of that
 * would have caught the furniture-layer bug, and nothing short of it catches
 * this one.
 */

import { useMemo } from 'react';

import { useKeyboardMap, type CommandHandlers } from '../../lib/keymap';
import { defaultCommandHandlers } from '../../lib/shortcuts';
import { selectKeyboardEnabled, useUiStore } from '../../stores/ui';
import { Cheatsheet } from './Cheatsheet';
import { CommandPalette } from './CommandPalette';
import { defaultCommands } from './defaultCommands';
import { constraintCommands } from '../constraints/commands';
import { useCommandKeys, useRegisterCommands } from './hooks';
import { commandRegistry, type CommandRegistry } from './registry';
import { useCommandUiStore } from './store';

export interface CommandLayerProps {
  /** Defaults to the app registry. Tests pass their own. */
  readonly registry?: CommandRegistry | undefined;
  /** Override platform detection. Tests only. */
  readonly mac?: boolean | undefined;
  /**
   * Whether this layer answers the `?` key.
   *
   * **Default `false`, and the default is load-bearing.** `?` is claimed by
   * `lib/keymap.ts` as `help.shortcuts`, and `pages/project/PlanPage.tsx`
   * currently registers a handler for it that opens its own `ShortcutsDialog`.
   * Both handlers sit on `document`; `stopPropagation` does not stop a sibling
   * listener on the same node (see `stores/ui.ts` on `toolKeysActive`), so
   * turning this on before that line is removed puts two shortcut sheets on
   * screen at once on the Plan tab.
   *
   * The cheatsheet is reachable regardless — `⌘/` is registered as
   * `help.cheatsheet`, `lib/keymap.ts` does not claim it, and it works on every
   * tab and every layout. Flipping this to `true` is the second half of a
   * two-line change whose first half is deleting PlanPage's `'help.shortcuts'`
   * handler and its `<ShortcutsDialog>`.
   */
  readonly ownHelpKey?: boolean | undefined;
}

export function CommandLayer({
  registry,
  mac,
  ownHelpKey = false,
}: CommandLayerProps = {}): JSX.Element {
  const active = registry ?? commandRegistry;
  const keyboardEnabled = useUiStore(selectKeyboardEnabled);

  // Stable identity: `useRegisterCommands` has this in its dependency list, and
  // a fresh array per render would tear the whole registry down and rebuild it
  // on every state change in the app.
  // The constraints (C-3) join here rather than in `defaultCommands`, because that
  // table is an exhaustive mirror of `lib/keymap.ts`'s CommandId union and these carry
  // no key. Concatenated inside the same memo so the registry is still rebuilt once.
  const commands = useMemo(() => [...defaultCommands(), ...constraintCommands], []);
  useRegisterCommands(commands, active);

  useCommandKeys({
    registry: active,
    enabled: keyboardEnabled,
    ...(mac === undefined ? {} : { mac }),
  });

  /**
   * The `?` key, when this layer has been given it.
   *
   * Routed through `useKeyboardMap` rather than through our own listener
   * because `lib/keymap.ts` owns that keystroke's matching — including the two
   * registrations that make `?` work on layouts where it needs no Shift. The
   * registry refuses to bind `?` itself for exactly this reason, so this is the
   * only correct way in.
   */
  const helpHandlers = useMemo<CommandHandlers>(() => {
    if (!ownHelpKey) return {};
    // Belt and braces against the double-fire this prop exists to avoid: if
    // `lib/shortcuts.ts` ever grows its own `help.shortcuts` handler, stand
    // down rather than both of us opening a sheet.
    if ('help.shortcuts' in defaultCommandHandlers()) {
      console.warn(
        '[command] lib/shortcuts.ts now handles help.shortcuts; CommandLayer is ' +
          'standing down so the ? key does not fire twice. Set ownHelpKey={false}.',
      );
      return {};
    }
    return {
      'help.shortcuts': () => {
        useCommandUiStore.getState().toggleCheatsheet();
      },
    };
  }, [ownHelpKey]);
  useKeyboardMap(helpHandlers, { enabled: keyboardEnabled });

  return (
    <>
      <CommandPalette registry={active} mac={mac} />
      <Cheatsheet registry={active} mac={mac} />
    </>
  );
}
