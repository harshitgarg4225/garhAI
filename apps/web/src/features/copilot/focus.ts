/**
 * focus.ts — the `/` shortcut's target, exported for the integrator.
 *
 * The keyboard map (`lib/keymap.ts`) is the integrator's file; this feature
 * only exports the HANDLER. Wiring is one line wherever the app-wide
 * shortcuts are registered:
 *
 *     useKeyboardMap({ ..., 'copilot.focus': copilotFocusHandler });
 *
 * plus a `{ command: 'copilot.focus', key: COPILOT_FOCUS_KEY, modifiers:
 * 'none', scope: 'global' }` row in KEY_BINDINGS. The map's own
 * `isTypingTarget` guard already keeps `/` working normally inside inputs,
 * so typing a slash in the brief never yanks focus.
 *
 * A module-level registry (not a React context) because the keystroke can
 * arrive while the panel is closed and unmounted: the handler opens the panel
 * through the ui store, then focuses the input once it exists.
 */

import { useUiStore } from '../../stores/ui';
import type { CommandHandler } from '../../lib/keymap';

/** The key the integrator should bind. One place, so docs and code agree. */
export const COPILOT_FOCUS_KEY = '/';

let inputNode: HTMLTextAreaElement | HTMLInputElement | null = null;

/** CopilotPanel registers its command input here (null on unmount). */
export function registerCopilotInput(node: HTMLTextAreaElement | HTMLInputElement | null): void {
  inputNode = node;
}

/**
 * Open the copilot rail (if closed) and put the caret in the command input.
 * Safe to call from anywhere — a menu item, the tour, the keyboard map.
 */
export function focusCopilotInput(): void {
  useUiStore.getState().setPanel('copilot', true);

  if (inputNode !== null && inputNode.isConnected) {
    inputNode.focus();
    return;
  }
  // The panel was closed: it mounts on the state change above, so focus on
  // the next frame, when the input has registered itself. Two frames covers
  // a lazy-mounted shell without resorting to polling.
  requestAnimationFrame(() => {
    if (inputNode !== null && inputNode.isConnected) {
      inputNode.focus();
      return;
    }
    requestAnimationFrame(() => inputNode?.focus());
  });
}

/**
 * Ready-made handler for the keyboard map. Returning `void` (not `false`)
 * lets the map call `preventDefault()`, so the `/` never also types itself
 * into the input it just focused.
 */
export const copilotFocusHandler: CommandHandler = () => {
  focusCopilotInput();
};
