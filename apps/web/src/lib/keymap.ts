/**
 * The keyboard map (§12), in one place, once.
 *
 *   V select · W wall · D door · N window · S stair · B balcony · M measure
 *   F furniture · Cmd/Ctrl-Z undo · Cmd/Ctrl-Y (or Shift-Z) redo
 *   1/2/3 storey · Tab 2D↔3D · G snap toggle · Esc cancel · Enter commit
 *   Del/⌫ delete selection · Cmd/Ctrl-A select all (canvas only)
 *   0 fit · =/- zoom · ⇧G grid · ⇧D dimensions · ? shortcuts sheet
 *
 * This module is the registration point the canvas and tool layers plug into
 * rather than each attaching its own `keydown` listener. Three reasons that
 * matters, all learned the hard way in editors like this one:
 *
 *  - **Discoverability.** Every binding is one array below, so the shortcuts
 *    sheet, the tool rail's tooltips and the handler all read from the same
 *    source and cannot disagree.
 *  - **Typing safety.** A single listener can apply one consistent rule for
 *    "the user is in a text field, leave them alone". Scattered listeners
 *    inevitably grow inconsistent versions of that check, and the symptom is a
 *    wall tool activating while someone types a room name.
 *  - **Accessibility (§15).** Every command here also has a toolbar button.
 *    The two paths call the same function, so a mouse-only user is never
 *    locked out of something the keyboard can do.
 *
 * Tab is the one genuinely delicate binding: it is the browser's focus-traversal
 * key, and hijacking it globally would make the app unusable with a keyboard.
 * It is therefore intercepted ONLY when focus is inside the canvas surface
 * (an element marked `data-garh-canvas`) and no modifier is held. Everywhere
 * else it moves focus, exactly as a user expects.
 */

import { useEffect } from 'react';

/** The eight direct-manipulation tools (§F4). */
export const TOOL_IDS = [
  'select',
  'wall',
  'door',
  'window',
  'stair',
  'balcony',
  'measure',
  'furniture',
] as const;
export type ToolId = (typeof TOOL_IDS)[number];

export const COMMAND_IDS = [
  'tool.select',
  'tool.wall',
  'tool.door',
  'tool.window',
  'tool.stair',
  'tool.balcony',
  'tool.measure',
  'tool.furniture',
  'edit.undo',
  'edit.redo',
  'edit.delete',
  'edit.selectAll',
  'storey.1',
  'storey.2',
  'storey.3',
  'view.toggle',
  'view.fit',
  'view.zoomIn',
  'view.zoomOut',
  'view.grid',
  'view.dimensions',
  'snap.toggle',
  'tool.cancel',
  'tool.commit',
  'help.shortcuts',
  'copilot.focus',
] as const;
export type CommandId = (typeof COMMAND_IDS)[number];

/**
 * Which modifier the binding needs. `'mod'` = Cmd on macOS, Ctrl elsewhere.
 *
 * `'shift'` exists for the two Phase-4 bindings that genuinely need it: the
 * layer toggles (⇧G / ⇧D) sit next to the unshifted commands they relate to,
 * and `?` is a shifted glyph on most layouts. Without it those keys would have
 * to steal an unrelated letter.
 */
export type ModifierSpec = 'none' | 'shift' | 'mod' | 'mod+shift';

/** Where a binding is live. `'canvas'` bindings need focus inside the canvas. */
export type BindingScope = 'global' | 'canvas';

export interface KeyBinding {
  readonly command: CommandId;
  /** `KeyboardEvent.key`, lower-cased for letters. */
  readonly key: string;
  readonly modifiers: ModifierSpec;
  readonly scope: BindingScope;
  /** Short label for the tool rail and the shortcuts sheet. */
  readonly label: string;
  /** One plain sentence — §15 tone, no jargon. */
  readonly description: string;
  /** Tool this binding activates, when it activates one. */
  readonly tool?: ToolId;
}

/**
 * THE map. Order matters only for display; matching is exact.
 *
 * Adding a binding here is the whole change — `useKeyboardMap` picks it up, and
 * a command with no handler is simply inert rather than a crash.
 */
export const KEY_BINDINGS: readonly KeyBinding[] = [
  // ── Tools (§12) ──────────────────────────────────────────────────────────
  {
    command: 'tool.select',
    key: 'v',
    modifiers: 'none',
    scope: 'global',
    label: 'V',
    description: 'Select and move things.',
    tool: 'select',
  },
  {
    command: 'tool.wall',
    key: 'w',
    modifiers: 'none',
    scope: 'global',
    label: 'W',
    description: 'Draw walls.',
    tool: 'wall',
  },
  {
    command: 'tool.door',
    key: 'd',
    modifiers: 'none',
    scope: 'global',
    label: 'D',
    description: 'Place a door.',
    tool: 'door',
  },
  {
    command: 'tool.window',
    key: 'n',
    modifiers: 'none',
    scope: 'global',
    label: 'N',
    description: 'Place a window.',
    tool: 'window',
  },
  {
    command: 'tool.stair',
    key: 's',
    modifiers: 'none',
    scope: 'global',
    label: 'S',
    description: 'Place a staircase.',
    tool: 'stair',
  },
  {
    command: 'tool.balcony',
    key: 'b',
    modifiers: 'none',
    scope: 'global',
    label: 'B',
    description: 'Draw a balcony or projection.',
    tool: 'balcony',
  },
  {
    command: 'tool.measure',
    key: 'm',
    modifiers: 'none',
    scope: 'global',
    label: 'M',
    description: 'Measure a distance.',
    tool: 'measure',
  },
  {
    command: 'tool.furniture',
    key: 'f',
    modifiers: 'none',
    scope: 'global',
    label: 'F',
    description: 'Place furniture.',
    tool: 'furniture',
  },

  // ── Undo / redo (§15 "everything undoable, visibly") ────────────────────
  {
    command: 'edit.undo',
    key: 'z',
    modifiers: 'mod',
    scope: 'global',
    label: 'Z',
    description: 'Undo the last change.',
  },
  {
    command: 'edit.redo',
    key: 'y',
    modifiers: 'mod',
    scope: 'global',
    label: 'Y',
    description: 'Redo the change you undid.',
  },
  // The Mac idiom. Both are registered so muscle memory from either platform works.
  {
    command: 'edit.redo',
    key: 'z',
    modifiers: 'mod+shift',
    scope: 'global',
    label: '⇧Z',
    description: 'Redo the change you undid.',
  },

  // ── Editing the selection ───────────────────────────────────────────────
  // Canvas-scoped, both of them. Delete outside the canvas belongs to whatever
  // list or field has focus, and Cmd-A must still select the text in an input
  // or the paragraph in a panel — hijacking either globally is the kind of
  // shortcut that makes a keyboard user distrust the whole app.
  {
    command: 'edit.delete',
    key: 'Delete',
    modifiers: 'none',
    scope: 'canvas',
    label: 'Del',
    description: 'Delete what is selected.',
  },
  {
    command: 'edit.delete',
    key: 'Backspace',
    modifiers: 'none',
    scope: 'canvas',
    label: '⌫',
    description: 'Delete what is selected.',
  },
  {
    command: 'edit.selectAll',
    key: 'a',
    modifiers: 'mod',
    scope: 'canvas',
    label: 'A',
    description: 'Select everything on this floor.',
  },

  // ── Storeys and views ───────────────────────────────────────────────────
  {
    command: 'storey.1',
    key: '1',
    modifiers: 'none',
    scope: 'global',
    label: '1',
    description: 'Go to the ground floor.',
  },
  {
    command: 'storey.2',
    key: '2',
    modifiers: 'none',
    scope: 'global',
    label: '2',
    description: 'Go to the first floor.',
  },
  {
    command: 'storey.3',
    key: '3',
    modifiers: 'none',
    scope: 'global',
    label: '3',
    description: 'Go to the second floor.',
  },
  {
    command: 'view.toggle',
    key: 'Tab',
    modifiers: 'none',
    scope: 'canvas',
    label: 'Tab',
    description: 'Switch between the plan and the 3D view.',
  },
  // "brick grid", not "brick module": `keymap.test.ts` bans jargon words, and
  // it is right to — the sentence a tooltip shows should read the way an
  // architect talks, and "grid" is what everyone says out loud anyway.
  {
    command: 'snap.toggle',
    key: 'g',
    modifiers: 'none',
    scope: 'global',
    label: 'G',
    description: 'Switch between the 115 mm brick grid and the 25 mm fine grid.',
  },

  // ── Zoom and layers ─────────────────────────────────────────────────────
  // Unmodified on purpose. Cmd-0 / Cmd-plus / Cmd-minus are the browser's own
  // page zoom, and a page that fights them is a page that zooms twice.
  {
    command: 'view.fit',
    key: '0',
    modifiers: 'none',
    scope: 'global',
    label: '0',
    description: 'Fit the whole floor on screen.',
  },
  {
    command: 'view.zoomIn',
    key: '=',
    modifiers: 'none',
    scope: 'global',
    label: '=',
    description: 'Zoom in.',
  },
  {
    command: 'view.zoomOut',
    key: '-',
    modifiers: 'none',
    scope: 'global',
    label: '-',
    description: 'Zoom out.',
  },
  {
    command: 'view.grid',
    key: 'g',
    modifiers: 'shift',
    scope: 'global',
    label: '⇧G',
    description: 'Show or hide the grid.',
  },
  {
    command: 'view.dimensions',
    key: 'd',
    modifiers: 'shift',
    scope: 'global',
    label: '⇧D',
    description: 'Show or hide the dimensions.',
  },

  // ── Tool state machine (§12: "Esc cancels, Enter commits") ──────────────
  {
    command: 'tool.cancel',
    key: 'Escape',
    modifiers: 'none',
    scope: 'global',
    label: 'Esc',
    description: 'Cancel what you are drawing.',
  },
  {
    command: 'tool.commit',
    key: 'Enter',
    modifiers: 'none',
    scope: 'canvas',
    label: 'Enter',
    description: 'Finish what you are drawing.',
  },

  // ── Copilot (§10) ───────────────────────────────────────────────────────
  // `/` is the chat-input idiom every messaging app trained people on, and it
  // is safe here for one specific reason: `isTypingTarget` already excuses text
  // fields, so typing a slash into a room name or the brief never yanks focus
  // into the copilot. The handler is registered by the project shell, not by
  // `defaultCommandHandlers` — see `lib/shortcuts.ts`.
  {
    command: 'copilot.focus',
    key: '/',
    modifiers: 'none',
    scope: 'global',
    label: '/',
    description: 'Ask the copilot to change something.',
  },

  // ── Help ────────────────────────────────────────────────────────────────
  // Two bindings for one glyph: `?` needs Shift on a US layout and arrives
  // unshifted on several others. Matching only the shifted form would make the
  // shortcut sheet unreachable on the layouts that need it most.
  {
    command: 'help.shortcuts',
    key: '?',
    modifiers: 'shift',
    scope: 'global',
    label: '?',
    description: 'Show the keyboard shortcuts.',
  },
  {
    command: 'help.shortcuts',
    key: '?',
    modifiers: 'none',
    scope: 'global',
    label: '?',
    description: 'Show the keyboard shortcuts.',
  },
];

/** Bindings grouped for a shortcuts sheet. */
export const KEY_BINDINGS_BY_COMMAND: ReadonlyMap<CommandId, KeyBinding[]> = (() => {
  const map = new Map<CommandId, KeyBinding[]>();
  for (const binding of KEY_BINDINGS) {
    const list = map.get(binding.command);
    if (list) list.push(binding);
    else map.set(binding.command, [binding]);
  }
  return map;
})();

/** The command each tool is activated by — used by the tool rail's tooltips. */
export const TOOL_SHORTCUT: Readonly<Record<ToolId, string>> = (() => {
  const out = {} as Record<ToolId, string>;
  for (const binding of KEY_BINDINGS) {
    if (binding.tool) out[binding.tool] = binding.label;
  }
  return out;
})();

// ---------------------------------------------------------------------------
// Matching
// ---------------------------------------------------------------------------

function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);
}

/** True when the primary modifier for this platform is held. */
function hasMod(event: Pick<KeyboardEvent, 'metaKey' | 'ctrlKey'>, mac: boolean): boolean {
  return mac ? event.metaKey : event.ctrlKey;
}

/**
 * Is the user typing? A text field, a textarea, a select, a `contenteditable`
 * region, or anything that has opted out with `data-garh-keys="off"`.
 *
 * Number inputs count: someone typing `2400` into a dimension field must not
 * also switch to the balcony tool.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target.closest('[data-garh-keys="off"]')) return true;
  const tag = target.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (target as HTMLInputElement).type;
    // Checkboxes, radios and buttons are activated by Space/Enter, not typed
    // into, so a letter shortcut is safe over them.
    return type !== 'checkbox' && type !== 'radio' && type !== 'button' && type !== 'submit';
  }
  return false;
}

/** Is focus inside the drawing surface? Marked with `data-garh-canvas`. */
export function isCanvasTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.closest('[data-garh-canvas]') !== null;
}

export interface MatchOptions {
  /** Override platform detection (tests). */
  readonly mac?: boolean;
  /** Whether canvas-scoped bindings are eligible. */
  readonly inCanvas?: boolean;
}

/**
 * Resolve a keyboard event to a binding, or null.
 *
 * Pure and side-effect free, so the shortcuts sheet and the tests can use it
 * without a DOM. Exported for exactly that reason.
 */
export function matchBinding(
  event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'shiftKey' | 'altKey' | 'repeat'>,
  options: MatchOptions = {},
): KeyBinding | null {
  const mac = options.mac ?? isMacPlatform();
  const inCanvas = options.inCanvas ?? true;

  // Alt is reserved for the OS and for future modal tool variants; never a
  // silent alias for an unmodified binding.
  if (event.altKey) return null;

  const mod = hasMod(event, mac);
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;

  for (const binding of KEY_BINDINGS) {
    if (binding.key !== key) continue;
    if (binding.scope === 'canvas' && !inCanvas) continue;

    if (binding.modifiers === 'none') {
      if (mod || event.shiftKey) continue;
      return binding;
    }
    if (binding.modifiers === 'shift') {
      if (mod || !event.shiftKey) continue;
      return binding;
    }
    if (binding.modifiers === 'mod') {
      if (!mod || event.shiftKey) continue;
      return binding;
    }
    // 'mod+shift'
    if (mod && event.shiftKey) return binding;
  }
  return null;
}

/** `⌘Z` on macOS, `Ctrl+Z` elsewhere — for menus and tooltips. */
export function formatShortcut(binding: KeyBinding, mac = isMacPlatform()): string {
  const key = binding.key.length === 1 ? binding.key.toUpperCase() : binding.key;
  if (binding.modifiers === 'none') return key;
  if (binding.modifiers === 'shift') return mac ? `⇧${key}` : `Shift+${key}`;
  if (binding.modifiers === 'mod') return mac ? `⌘${key}` : `Ctrl+${key}`;
  return mac ? `⇧⌘${key}` : `Ctrl+Shift+${key}`;
}

// ---------------------------------------------------------------------------
// The hook
// ---------------------------------------------------------------------------

/**
 * A command handler. Return `false` to decline the event — the default
 * behaviour then runs and nothing is prevented, which is how a tool that is
 * mid-drag can let Escape fall through to close a dialog instead.
 */
export type CommandHandler = (event: KeyboardEvent, binding: KeyBinding) => void | boolean;

export type CommandHandlers = Partial<Record<CommandId, CommandHandler>>;

export interface KeyboardMapOptions {
  /** Turn the whole map off — a modal dialog sets this while it is open. */
  readonly enabled?: boolean;
  /** Listen on something other than `document` (a focus-trapped panel, say). */
  readonly target?: HTMLElement | Document | null;
  /**
   * Fired for every recognised binding, before the handler. The tools agent
   * uses this for telemetry and for the "shortcut hint" coach mark.
   */
  readonly onCommand?: (command: CommandId, event: KeyboardEvent) => void;
}

/**
 * Register the keyboard map for as long as the calling component is mounted.
 *
 * Handlers are looked up per event, from the object passed on the latest
 * render, so a component may pass inline closures without re-binding the
 * listener on every keystroke.
 *
 * ```tsx
 * useKeyboardMap({
 *   'tool.wall': () => setTool('wall'),
 *   'edit.undo': () => undo(),
 * });
 * ```
 *
 * Commands with no handler do nothing and do not consume the keystroke, so a
 * screen can adopt a subset of the map without swallowing the rest.
 */
export function useKeyboardMap(handlers: CommandHandlers, options: KeyboardMapOptions = {}): void {
  const { enabled = true, target, onCommand } = options;

  // The handlers object is read through a ref-like closure captured on each
  // render; `handlers` is intentionally in the dependency list so the listener
  // always sees the current one without needing a ref dance.
  useEffect(() => {
    if (!enabled) return;
    const node: HTMLElement | Document | null =
      target ?? (typeof document === 'undefined' ? null : document);
    if (!node) return;

    const onKeyDown = (event: Event): void => {
      if (!(event instanceof KeyboardEvent)) return;
      // Auto-repeat would fire a tool switch dozens of times from one held key.
      if (event.repeat) return;
      if (isTypingTarget(event.target)) return;

      const inCanvas = isCanvasTarget(event.target);
      const binding = matchBinding(event, { inCanvas });
      if (!binding) return;

      const handler = handlers[binding.command];
      if (!handler) return;

      onCommand?.(binding.command, event);
      const result = handler(event, binding);
      if (result === false) return;
      event.preventDefault();
      // Stop here: a global listener further up must not also act on a
      // keystroke a focused panel has already consumed.
      event.stopPropagation();
    };

    node.addEventListener('keydown', onKeyDown);
    return () => node.removeEventListener('keydown', onKeyDown);
  }, [enabled, target, handlers, onCommand]);
}
