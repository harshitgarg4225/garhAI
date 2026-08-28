/**
 * defaultCommands.ts — the command set the app boots with.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE MIRROR IS GENERATED, NOT TYPED OUT
 * ════════════════════════════════════════════════════════════════════════════
 * Every binding the app already has lives in `KEY_BINDINGS` (`lib/keymap.ts`).
 * A palette and a cheatsheet that re-listed those by hand would be two more
 * copies of a list that already exists twice, and the first one to fall behind
 * would fall behind silently — a shortcut that works but is not listed is
 * invisible, and a shortcut that is listed but was renamed is a lie.
 *
 * So the mirror is FOLDED out of `KEY_BINDINGS` itself: one command per
 * `CommandId`, its title taken from that table's own §15-reviewed sentence, its
 * binding strings rebuilt from that table's own key + modifier fields. The only
 * hand-written part is {@link MIRRORED} — group, icon, search keywords, whether
 * the command can run right now, and what it does — and that table is typed
 * `Record<CommandId, …>`, so adding a command to `lib/keymap.ts` and forgetting
 * this file is a COMPILE error, not a gap in a menu.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY SO MANY `run: null`
 * ════════════════════════════════════════════════════════════════════════════
 * `run: null` means "this key is real and belongs in the cheatsheet, but the
 * action lives somewhere this registry cannot reach". Fit-to-screen and the two
 * zoom keys drive `ViewportController`, which lives outside React inside the
 * canvas; Delete and Select-all need the live selection; Enter and Esc are the
 * tool state machine's own; `/` needs the copilot panel's input node, and
 * `lib/shortcuts.ts` explains at length why that handler belongs to the project
 * shell and not to a shared layer.
 *
 * Faking any of them — reaching for a global, or dispatching a synthetic
 * keystroke — would give the palette a row that appears to work and does not.
 * `features/layers` faced the identical choice for the A-TITL row and answered
 * it the same way: show the thing, say where it lives, offer no dead switch.
 */

import type { IconName } from '@garh/ui';

import {
  KEY_BINDINGS_BY_COMMAND,
  type CommandId,
  type KeyBinding,
  type ToolId,
} from '../../lib/keymap';
import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import { formatBinding, parseBinding } from './binding';
import { useCommandUiStore } from './store';
import type { Command, CommandGroup, CommandRun } from './types';

// ---------------------------------------------------------------------------
// Store reads
// ---------------------------------------------------------------------------

/**
 * Arm a tool from the palette.
 *
 * The same store action the tool rail's buttons and `lib/shortcuts.ts`'s `arm()`
 * call — §15's rule that a keyboard path and a pointer path must end in one
 * function, not two implementations of the same intent.
 *
 * `arm()` also declines in the 3D view (W walks the camera there), which is a
 * property of the KEY, not of the command; from the palette the user asked for
 * the tool by name. It is expressed here as `enabled` instead, so the palette
 * greys the row out and explains itself rather than swallowing a click.
 */
function armTool(tool: ToolId): CommandRun {
  return () => useUiStore.getState().setTool(tool);
}

const inPlanView = (): boolean => useUiStore.getState().viewMode === '2d';

function storeyAt(index: number): string | null {
  return useModelStore.getState().doc.house.storeys[index]?.id ?? null;
}

function goToStorey(index: number): CommandRun {
  return () => {
    const id = storeyAt(index);
    // `enabled` already gated this, but the model can change between the render
    // that drew the row and the click that runs it.
    if (id !== null) useUiStore.getState().setActiveStorey(id);
  };
}

// ---------------------------------------------------------------------------
// The mirror table
// ---------------------------------------------------------------------------

interface MirroredSpec {
  readonly group: CommandGroup;
  readonly run: CommandRun | null;
  readonly icon?: IconName;
  readonly keywords?: readonly string[];
  readonly enabled?: () => boolean;
}

/**
 * One entry per `CommandId`. Exhaustive by type — that is the whole point.
 *
 * `run: null` is documented above. `enabled` is a live store read: an Undo row
 * that is not greyed out on an empty history is a palette that lies about the
 * state of the document.
 */
const MIRRORED: Readonly<Record<CommandId, MirroredSpec>> = {
  'tool.select': {
    group: 'Tools',
    icon: 'cursor',
    keywords: ['select', 'arrow', 'pick', 'move'],
    run: armTool('select'),
    enabled: inPlanView,
  },
  'tool.wall': {
    group: 'Tools',
    icon: 'wall',
    keywords: ['wall', 'draw', 'partition'],
    run: armTool('wall'),
    enabled: inPlanView,
  },
  'tool.door': {
    group: 'Tools',
    icon: 'door',
    keywords: ['door', 'opening'],
    run: armTool('door'),
    enabled: inPlanView,
  },
  'tool.window': {
    group: 'Tools',
    icon: 'window',
    keywords: ['window', 'opening', 'ventilation'],
    run: armTool('window'),
    enabled: inPlanView,
  },
  'tool.stair': {
    group: 'Tools',
    icon: 'stair',
    keywords: ['stair', 'staircase', 'steps'],
    run: armTool('stair'),
    enabled: inPlanView,
  },
  'tool.balcony': {
    group: 'Tools',
    icon: 'balcony',
    keywords: ['balcony', 'projection', 'chajja'],
    run: armTool('balcony'),
    enabled: inPlanView,
  },
  'tool.measure': {
    group: 'Tools',
    icon: 'ruler',
    keywords: ['measure', 'dimension', 'distance', 'tape'],
    run: armTool('measure'),
    enabled: inPlanView,
  },
  'tool.furniture': {
    group: 'Tools',
    icon: 'sofa',
    keywords: ['furniture', 'block', 'fixture'],
    run: armTool('furniture'),
    enabled: inPlanView,
  },

  'edit.undo': {
    group: 'Edit',
    icon: 'undo',
    keywords: ['undo', 'revert', 'back'],
    run: () => void useModelStore.getState().undo(),
    enabled: () => useModelStore.getState().undoStack.length > 0,
  },
  'edit.redo': {
    group: 'Edit',
    icon: 'redo',
    keywords: ['redo', 'forward', 'again'],
    run: () => void useModelStore.getState().redo(),
    enabled: () => useModelStore.getState().redoStack.length > 0,
  },
  // Needs the live selection and the canvas's own delete path.
  'edit.delete': { group: 'Edit', icon: 'trash', keywords: ['delete', 'remove'], run: null },
  // Needs the canvas selection store and the active storey's geometry.
  'edit.selectAll': { group: 'Edit', icon: 'check', keywords: ['select all'], run: null },

  'storey.1': {
    group: 'Storeys',
    icon: 'layers',
    keywords: ['ground', 'floor', 'storey'],
    run: goToStorey(0),
    enabled: () => storeyAt(0) !== null,
  },
  'storey.2': {
    group: 'Storeys',
    icon: 'layers',
    keywords: ['first', 'floor', 'storey'],
    run: goToStorey(1),
    enabled: () => storeyAt(1) !== null,
  },
  'storey.3': {
    group: 'Storeys',
    icon: 'layers',
    keywords: ['second', 'floor', 'storey'],
    run: goToStorey(2),
    enabled: () => storeyAt(2) !== null,
  },

  'view.toggle': {
    group: 'View',
    icon: 'cube',
    keywords: ['3d', '2d', 'plan', 'model', 'switch'],
    run: () => useUiStore.getState().toggleViewMode(),
  },
  // The camera lives in ViewportController, outside React and inside the canvas.
  'view.fit': { group: 'View', icon: 'compass', keywords: ['fit', 'zoom to fit'], run: null },
  'view.zoomIn': { group: 'View', icon: 'plus', keywords: ['zoom in'], run: null },
  'view.zoomOut': { group: 'View', icon: 'minus', keywords: ['zoom out'], run: null },
  'view.grid': {
    group: 'View',
    icon: 'grid',
    keywords: ['grid', 'guides'],
    run: () => useUiStore.getState().toggleCanvasLayer('grid'),
  },
  'view.dimensions': {
    group: 'View',
    icon: 'ruler',
    keywords: ['dimensions', 'sizes', 'annotation'],
    run: () => useUiStore.getState().toggleCanvasLayer('dimensions'),
  },
  'snap.toggle': {
    group: 'View',
    icon: 'grid',
    keywords: ['snap', 'grid', 'brick', 'module', 'fine'],
    run: () => useUiStore.getState().toggleSnap(),
  },

  // Both belong to the tool state machine, which owns what "cancel" and
  // "commit" mean for whatever is half-drawn right now.
  'tool.cancel': { group: 'Edit', icon: 'x', keywords: ['cancel', 'escape'], run: null },
  'tool.commit': { group: 'Edit', icon: 'check', keywords: ['commit', 'finish'], run: null },

  /**
   * The `?` key. `run: null`, and that is the honest answer today: `?` is
   * claimed by `lib/keymap.ts` and answered by `PlanPage.tsx`, which opens its
   * own `ShortcutsDialog` on the Plan tab. This registry documents the key —
   * the cheatsheet must list every binding, including the ones it does not own
   * — and offers its own sheet under `help.cheatsheet` below.
   *
   * `CommandLayer`'s `ownHelpKey` prop is the seam for taking `?` over; see the
   * note there. It does not need a `run` for that, because a keymap-owned key
   * is fired through `useKeyboardMap`, not through the registry.
   */
  'help.shortcuts': { group: 'Help', icon: 'info', keywords: ['shortcuts', 'keys'], run: null },

  // The copilot's input node lives in features/copilot, and lib/shortcuts.ts
  // sets out why that handler belongs to the project shell.
  'copilot.focus': {
    group: 'Project',
    icon: 'sparkles',
    keywords: ['copilot', 'ask', 'chat', 'ai'],
    run: null,
  },
};

// ---------------------------------------------------------------------------
// Folding KEY_BINDINGS into commands
// ---------------------------------------------------------------------------

/**
 * A `KeyBinding` from `lib/keymap.ts`, written as a spec string this module can
 * parse. The two vocabularies are different on purpose (see `binding.ts`), and
 * this is the single place they meet.
 */
export function bindingSpecOf(binding: KeyBinding): string {
  const key = binding.key.length === 1 ? binding.key.toLowerCase() : binding.key;
  switch (binding.modifiers) {
    case 'none':
      return key;
    case 'shift':
      return `shift+${key}`;
    case 'mod':
      return `mod+${key}`;
    default:
      return `mod+shift+${key}`;
  }
}

/**
 * "Draw walls." → "Draw walls".
 *
 * The sentence in `KEY_BINDINGS` is the reviewed one (`keymap.test.ts` polices
 * its tone and bans jargon), so it is the title — minus the full stop, which
 * belongs to a tooltip and not to a menu row.
 */
function titleOf(description: string): string {
  return description.endsWith('.') ? description.slice(0, -1) : description;
}

/**
 * Drop bindings that print identically.
 *
 * `?` is registered twice in `lib/keymap.ts`, shifted and not, so it works on
 * every keyboard layout. Both are real and both must match; showing the glyph
 * twice in the cheatsheet would just look like a bug. Formatting for the
 * non-Mac form is an arbitrary but deterministic choice of canonical string.
 */
function dedupeSpecs(specs: readonly string[]): readonly string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const spec of specs) {
    const printed = formatBinding(parseBinding(spec), false);
    if (seen.has(printed)) continue;
    seen.add(printed);
    out.push(spec);
  }
  return out;
}

/** Every command in `lib/keymap.ts`, as registry commands. */
export function keymapMirrorCommands(): readonly Command[] {
  const commands: Command[] = [];
  for (const [id, bindings] of KEY_BINDINGS_BY_COMMAND) {
    const first = bindings[0];
    if (first === undefined) continue;
    const spec = MIRRORED[id];
    commands.push({
      id,
      title: titleOf(first.description),
      group: spec.group,
      bindings: dedupeSpecs(bindings.map(bindingSpecOf)),
      keyOwner: 'keymap',
      run: spec.run,
      ...(spec.icon === undefined ? {} : { icon: spec.icon }),
      ...(spec.keywords === undefined ? {} : { keywords: spec.keywords }),
      ...(spec.enabled === undefined ? {} : { enabled: spec.enabled }),
    });
  }
  return commands;
}

// ---------------------------------------------------------------------------
// Commands this feature introduces
// ---------------------------------------------------------------------------

/**
 * The two keys C-1 and C-2 add. Neither is in `lib/keymap.ts` and neither can
 * be: that module's `CommandId` is a closed union, which is exactly the
 * limitation an open registry exists to lift.
 */
export function paletteCommands(): readonly Command[] {
  return [
    {
      id: 'palette.open',
      title: 'Open the command palette',
      group: 'Help',
      icon: 'search',
      keywords: ['palette', 'command', 'search', 'menu'],
      bindings: ['mod+k'],
      // Hidden from the palette itself: a row that opens the thing you are
      // already looking at is one wasted line in a list people scan under
      // pressure. It stays in the cheatsheet, which is where you look for it.
      hidden: true,
      run: () => useCommandUiStore.getState().togglePalette(),
    },
    {
      /**
       * `mod+/` rather than `?`.
       *
       * `?` is taken — `lib/keymap.ts` claims it and `PlanPage.tsx` answers it,
       * and the registry refuses to double-bind a key for good reason: two
       * listeners on `document` both act, and `stopPropagation` does not stop a
       * sibling. `mod+/` is what Slack, GitHub, Linear and Notion all use for
       * this sheet, it is free here, and unlike `?` it needs no per-layout
       * shift guessing at all.
       */
      id: 'help.cheatsheet',
      title: 'Show every keyboard shortcut',
      group: 'Help',
      icon: 'info',
      keywords: ['shortcuts', 'keys', 'cheatsheet', 'keyboard', 'help'],
      bindings: ['mod+/'],
      run: () => useCommandUiStore.getState().toggleCheatsheet(),
    },
  ];
}

/** Everything the app registers at boot. */
export function defaultCommands(): readonly Command[] {
  return [...keymapMirrorCommands(), ...paletteCommands()];
}
