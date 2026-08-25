/**
 * The app's default keyboard behaviour — where `lib/keymap.ts` meets the stores.
 *
 * `keymap.ts` is deliberately store-free: it declares the bindings, matches an
 * event to one, and calls a handler. That is what makes it testable without a
 * DOM and reusable by the shortcuts sheet. This module is the other half: it
 * says what each command *does* in this app.
 *
 * ## For the tools agent (Phase 4)
 *
 * Do not attach a second `keydown` listener. Two listeners means two
 * definitions of "the user is typing, leave them alone", and the symptom is the
 * wall tool arming itself while someone renames a room.
 *
 * Instead:
 *
 * ```tsx
 * // Inside the canvas component:
 * useKeyboardMap(
 *   {
 *     'tool.cancel': () => machine.cancel(),
 *     'tool.commit': () => machine.commit(),
 *   },
 *   { target: canvasRef.current },
 * );
 * ```
 *
 * A scoped map on the canvas element runs first (the event bubbles up to the
 * document listener afterwards) and a handled command calls
 * `stopPropagation()`, so the global default never double-fires. Returning
 * `false` from a handler declines the event and lets it fall through — which is
 * how a tool that is mid-drag can let Escape close a dialog instead.
 *
 * Adding a NEW binding is a change to `KEY_BINDINGS` in `keymap.ts` plus a case
 * here. Do not invent a shortcut inside a component.
 */

import { useMemo } from 'react';

import { useKeyboardMap, type CommandHandler, type CommandHandlers, type ToolId } from './keymap';
import { useModelStore } from '../stores/model';
import { useUiStore } from '../stores/ui';

/**
 * PHASE 5 — WHO OWNS AN OVERLAPPING COMMAND, EXACTLY ONCE
 *
 * `useToolController` (the canvas tool layer) registers its own keyboard map on
 * `document`, and so does this module (via `useAppShortcuts` in `App.tsx`).
 * `stopPropagation()` does not stop a SECOND listener on the SAME node, so any
 * command both maps handle would fire twice while the editor is open: two
 * undos per ⌘Z, a Tab that toggles 2D→3D→2D and looks dead, an Esc that both
 * cancels the drawing AND disarms the tool. The editor page therefore sets
 * `ui.toolKeysActive` while the tool controller's map is live (mounted and in
 * 2D), and every handler below that the controller also implements DECLINES
 * (returns false) while the flag is up. One command, one owner, always:
 *
 *   toolKeysActive (Plan/3D page, 2D mode)  → the tool controller handles it
 *   otherwise (any other tab, or 3D mode)   → this map handles it
 */
function deferToTools(handler: CommandHandler): CommandHandler {
  return (event, binding) => {
    if (useUiStore.getState().toolKeysActive) return false;
    return handler(event, binding);
  };
}

/**
 * Arm a tool. The tool rail's buttons call the same store action (§15 a11y).
 *
 * Declines in the 3D view: the eight drawing tools are 2D state machines, and
 * the 3D navigation layer uses W/A/S/D for walking — a `w` mid-walk must move
 * the camera, not arm the wall tool (the useNav3d header states this scoping
 * as its integration contract).
 */
function arm(tool: ToolId): CommandHandler {
  return () => {
    if (useUiStore.getState().viewMode === '3d') return false;
    useUiStore.getState().setTool(tool);
    return undefined;
  };
}

/** Switch to the storey at `index` (0 = ground), if the model has one there. */
function goToStorey(index: number): boolean {
  const storey = useModelStore.getState().doc.house.storeys[index];
  // Declining (returning false) rather than swallowing the key matters: on a
  // ground-floor-only project, "2" should do nothing visible, not silently
  // consume a keystroke a future feature might want.
  if (!storey) return false;
  useUiStore.getState().setActiveStorey(storey.id);
  return true;
}

/**
 * Undo, with the §15 acknowledgement.
 *
 * The model store already toasts when an undo cannot be applied; what it does
 * not do is confirm the ordinary case, and a silent undo on a canvas full of
 * walls leaves you unsure whether the key registered.
 */
function undo(): boolean {
  const model = useModelStore.getState();
  const next = model.undoStack[model.undoStack.length - 1];
  if (!next) {
    useUiStore.getState().pushToast({
      tone: 'info',
      title: 'Nothing left to undo.',
      dedupeKey: 'undo-empty',
      durationMs: 2500,
    });
    return true;
  }
  return model.undo();
}

function redo(): boolean {
  const model = useModelStore.getState();
  if (model.redoStack.length === 0) {
    useUiStore.getState().pushToast({
      tone: 'info',
      title: 'Nothing to redo.',
      dedupeKey: 'redo-empty',
      durationMs: 2500,
    });
    return true;
  }
  return model.redo();
}

/**
 * Build the default handler map.
 *
 * Every handler reads its store through `getState()` rather than through a
 * subscription: these run in response to a keystroke, not a render, and
 * subscribing would re-create the handler map — and therefore the listener —
 * on every state change.
 */
export function defaultCommandHandlers(): CommandHandlers {
  return {
    // Every command in this object is also implemented by the canvas tool
    // controller, so every one of them defers while `toolKeysActive` is up —
    // see the Phase 5 note above. `arm()` carries its own 3D guard as well.
    'tool.select': deferToTools(arm('select')),
    'tool.wall': deferToTools(arm('wall')),
    'tool.door': deferToTools(arm('door')),
    'tool.window': deferToTools(arm('window')),
    'tool.stair': deferToTools(arm('stair')),
    'tool.balcony': deferToTools(arm('balcony')),
    'tool.measure': deferToTools(arm('measure')),
    'tool.furniture': deferToTools(arm('furniture')),

    'edit.undo': deferToTools(() => undo()),
    'edit.redo': deferToTools(() => redo()),

    'storey.1': deferToTools(() => goToStorey(0)),
    'storey.2': deferToTools(() => goToStorey(1)),
    'storey.3': deferToTools(() => goToStorey(2)),

    'view.toggle': deferToTools(() => {
      useUiStore.getState().toggleViewMode();
      return undefined;
    }),
    'snap.toggle': deferToTools(() => {
      useUiStore.getState().toggleSnap();
      return undefined;
    }),

    // Cancel is global so Escape always has an owner. The canvas overrides it
    // with a scoped map while a tool is mid-draw; here it just disarms.
    'tool.cancel': deferToTools(() => {
      const ui = useUiStore.getState();
      if (ui.modal !== null) {
        ui.closeModal();
        return true;
      }
      if (ui.activeTool !== 'select') {
        ui.setTool('select');
        return true;
      }
      // Nothing of ours to cancel — let it through to whatever is focused.
      return false;
    }),
  };
}

/**
 * WHY `copilot.focus` IS NOT IN THE MAP ABOVE
 *
 * The binding lives in `KEY_BINDINGS` (one map, always), but its handler is
 * registered by `pages/ProjectShell.tsx`, for two reasons:
 *
 *  1. **Layering.** This module is `lib/`, and its handlers reach only into
 *     `stores/`. The copilot handler lives in `features/copilot/focus.ts`
 *     (it has to — it talks to the panel's input node), and a `lib → features`
 *     import is the wrong direction; the shell is where features are allowed
 *     to be known about.
 *  2. **Scope.** The copilot is project-scoped. `/` on the dashboard should
 *     type a slash, not open a rail for no project.
 *
 * A command with no handler is inert rather than swallowed (`useKeyboardMap`
 * returns early), so the binding existing app-wide costs nothing outside a
 * project.
 */

/**
 * Register the app-wide keyboard map. Mounted once, from `App.tsx`.
 *
 * Honours `ui.keyboardEnabled`, which a focus-trapped dialog turns off — a
 * modal owns the keyboard while it is open rather than every shortcut having to
 * remember to check for one.
 */
export function useAppShortcuts(): void {
  const enabled = useUiStore((s) => s.keyboardEnabled);
  // Stable across renders: the handlers close over `getState()`, not over props,
  // so the document listener is attached once for the life of the app.
  const handlers = useMemo(() => defaultCommandHandlers(), []);
  useKeyboardMap(handlers, { enabled });
}
