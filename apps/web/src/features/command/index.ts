/**
 * `features/command` — the command palette (C-1) and the keyboard layer (C-2).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * READ THESE FOUR FILES IN THIS ORDER
 * ════════════════════════════════════════════════════════════════════════════
 *   binding          binding strings parsed, matched and printed
 *   registry         the one list; every gate that keeps it honest
 *   defaultCommands  the fixed map folded in from lib/keymap.ts
 *   hooks            the key listener, and the guard it borrows
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WIRING — ONE LINE
 * ════════════════════════════════════════════════════════════════════════════
 * In `App.tsx`, inside the providers, next to `useAppShortcuts()`:
 *
 * ```tsx
 * <CommandLayer />
 * ```
 *
 * That is the whole integration. It registers the default set, arms ⌘K and ⌘/,
 * and renders both overlays into a portal on `<body>`.
 *
 * Anything else in the app contributes commands with:
 *
 * ```tsx
 * const COMMANDS = [{ id: 'sheet.export', title: 'Export the sheet set', … }];
 * useRegisterCommands(COMMANDS);   // module-constant array, not an inline one
 * ```
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HOW THIS COEXISTS WITH `lib/keymap.ts`, WHICH ALREADY EXISTS
 * ════════════════════════════════════════════════════════════════════════════
 * `lib/keymap.ts` owns a fixed table of 26 commands and one `document`
 * listener. It is not replaced and it is not duplicated:
 *
 *  · Its `isTypingTarget` is THE typing guard. This feature imports it. There
 *    is no second definition of "the user is busy" anywhere in this directory,
 *    which is the whole reason that module asked for a single listener.
 *  · Its table is FOLDED into the registry by `defaultCommands.ts`, through a
 *    `Record<CommandId, …>` that fails to compile if a command is added there
 *    and forgotten here. The palette and the cheatsheet therefore cannot fall
 *    behind the map they describe.
 *  · Its `matchBinding` is the ORACLE for key ownership. Registering a command
 *    on a key that module already claims throws; declaring `keyOwner: 'keymap'`
 *    for a key it does NOT claim throws too. The two listeners are disjoint by
 *    construction rather than by convention, which is what makes a second
 *    `document` listener safe here at all.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE ONE THING THAT NEEDS A FILE OUTSIDE THIS DIRECTORY
 * ════════════════════════════════════════════════════════════════════════════
 * `?` currently opens `components/ShortcutsDialog` and only on the Plan tab,
 * because `pages/project/PlanPage.tsx` registers `'help.shortcuts'` itself. The
 * cheatsheet here is app-wide and shows registry commands that dialog cannot
 * see (⌘K among them). Handing `?` over is: delete PlanPage's
 * `'help.shortcuts'` handler and its `<ShortcutsDialog>`, then mount
 * `<CommandLayer ownHelpKey />`. Until then `⌘/` opens the cheatsheet
 * everywhere and nothing double-fires — see `CommandLayerProps.ownHelpKey`.
 */

export { CommandLayer } from './CommandLayer';
export type { CommandLayerProps } from './CommandLayer';

export { CommandPalette } from './CommandPalette';
export type { CommandPaletteProps } from './CommandPalette';

export { Cheatsheet } from './Cheatsheet';
export type { CheatsheetProps } from './Cheatsheet';

export { CommandRegistry, RegistryError, commandRegistry } from './registry';

export { useCommandKeys, useCommands, useRegisterCommands, resolveKeyEvent } from './hooks';
export type { CommandKeysOptions } from './hooks';

export {
  BindingSyntaxError,
  bindingsCollide,
  formatBinding,
  isMacPlatform,
  matchesBinding,
  normaliseEventKey,
  parseBinding,
  synthesiseEvent,
} from './binding';
export type { KeyEventLike, MatchContext, ParsedBinding } from './binding';

export {
  flattenGroups,
  fuzzyMatch,
  groupMatches,
  normaliseQuery,
  scoreCommand,
  searchCommands,
} from './search';
export type { CommandGroupResult, CommandMatch, MatchRange } from './search';

export { selectCheatsheetOpen, selectPaletteOpen, selectQuery, useCommandUiStore } from './store';
export type { CommandUiState } from './store';

export {
  bindingSpecOf,
  defaultCommands,
  keymapMirrorCommands,
  paletteCommands,
} from './defaultCommands';

export { COMMAND_GROUPS, groupOrder } from './types';
export type {
  Command,
  CommandGroup,
  CommandKeyOwner,
  CommandOutcome,
  CommandRun,
  CommandSource,
} from './types';
