/**
 * types.ts — what a command is.
 *
 * One shape, read by three consumers that must never disagree: the palette
 * (C-1), the key layer, and the cheatsheet (C-2). Anything a consumer needs to
 * know about a command is a field here, so "the palette shows it but the
 * cheatsheet forgot it" cannot be expressed.
 */

import type { IconName } from '@garh/ui';

/**
 * Display groups, in the order the palette and the cheatsheet lay them out.
 *
 * A closed union rather than a free string: a typo'd group name would silently
 * create a one-item section at the bottom of the palette that nobody notices
 * for a month.
 */
export const COMMAND_GROUPS = ['Tools', 'Edit', 'View', 'Storeys', 'Project', 'Help'] as const;
export type CommandGroup = (typeof COMMAND_GROUPS)[number];

/** Where a command was invoked from. Handlers occasionally care. */
export type CommandSource = 'palette' | 'key' | 'api';

export type CommandRun = (source: CommandSource) => void;

/**
 * Which keydown listener is responsible for a command's key.
 *
 * `'command'`  our own listener (`useCommandKeys`) fires it. This is the
 *              default and applies to every binding this feature introduces.
 *
 * `'keymap'`   `lib/keymap.ts`'s single global listener already matches this
 *              key and `lib/shortcuts.ts` already runs it. We display it in the
 *              palette and the cheatsheet, and the palette may run it on click,
 *              but we must NOT attach a handler for the KEY — two listeners on
 *              `document` both acting on one keystroke is the double-undo bug
 *              the `toolKeysActive` flag in `stores/ui.ts` was invented to fix,
 *              and it would be back the moment we forgot.
 *
 * `registry.ts` enforces the mapping in both directions: a `'command'` binding
 * that `lib/keymap.ts` already claims is refused, and a `'keymap'` binding that
 * it does NOT claim is refused too — the latter being a command whose key can
 * never fire, listed in the cheatsheet as though it could.
 */
export type CommandKeyOwner = 'command' | 'keymap';

export interface Command {
  /** Stable id, dotted. Matches `lib/keymap.ts`'s `CommandId` where mirrored. */
  readonly id: string;
  /** Imperative phrase, sentence case, no trailing stop. "Draw walls". */
  readonly title: string;
  readonly group: CommandGroup;
  /** Extra search terms: synonyms, the CAD word, the key letter. */
  readonly keywords?: readonly string[];
  /** One plain sentence for the cheatsheet's second column. §15 tone. */
  readonly description?: string;
  readonly icon?: IconName;
  /**
   * Key bindings, most-canonical first. Every entry is validated at
   * registration; an unparseable one throws rather than registering a command
   * with a dead key.
   */
  readonly bindings?: readonly string[];
  readonly keyOwner?: CommandKeyOwner;
  /**
   * Whether the command can run right now. Absent means always.
   *
   * Called on every render of the palette and on every matching keystroke, so
   * keep it a store read, not a computation.
   */
  readonly enabled?: () => boolean;
  /**
   * What the command does — or `null` for a command that is DOCUMENTATION ONLY:
   * its key is real and belongs in the cheatsheet, but the action lives on a
   * surface this registry cannot reach (the canvas camera, the tool state
   * machine, the copilot's input node).
   *
   * `null` is required rather than optional on purpose. `features/layers` had
   * to make the same call for the A-TITL row and reached the same answer:
   * showing the thing and saying where it lives beats offering a control that
   * does nothing. An optional field would let a missing `run` happen by
   * accident; a required `null` is a decision someone typed.
   */
  readonly run: CommandRun | null;
  /**
   * Keep it out of the palette. For commands that only make sense as a key
   * (opening the palette from inside the palette) — they still appear in the
   * cheatsheet, which is the point of the cheatsheet.
   */
  readonly hidden?: boolean;
}

/**
 * The result of asking the registry to run something.
 *
 * A discriminated outcome rather than `boolean`, because "there is no such
 * command" and "that command is disabled right now" are a bug and a normal
 * state respectively, and a caller that cannot tell them apart will report the
 * bug as normal — which is how a module comes to believe it is registered.
 */
export type CommandOutcome = 'ran' | 'disabled' | 'documentation-only' | 'unknown';

/** Sort key for a group, for anything that needs to order them itself. */
export function groupOrder(group: CommandGroup): number {
  return COMMAND_GROUPS.indexOf(group);
}
