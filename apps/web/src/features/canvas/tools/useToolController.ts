/**
 * useToolController.ts — where the tools meet the app.
 *
 * This is the ONLY React file in the tool layer that touches the stores, and it
 * is deliberately thin: build a {@link ToolContext} from the current state,
 * hand an event to the active tool, apply whatever the tool asked for. The
 * decisions all live in the machines; this is wiring.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * §14: NOT ONE REACT RENDER PER POINTER MOVE
 * ────────────────────────────────────────────────────────────────────────────
 * Three things make that true, and all three are easy to break by accident:
 *
 *  1. **The tool lives in a ref**, not in state. Its internal transitions never
 *     touch React.
 *  2. **Stores are read through `getState()`** inside the handlers rather than
 *     subscribed with hooks. The controller re-renders only when the ACTIVE
 *     TOOL changes — a few times a minute, not sixty times a second.
 *  3. **The preview is published to `toolPreviewBus`**, which the scene reads
 *     imperatively and the HUD subscribes to through `useSyncExternalStore`.
 *
 * The handler object handed to `<CanvasRoot>` is stable across renders, so
 * `useCanvasControls` never re-attaches its listeners mid-gesture.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE KEYBOARD, IN TWO LAYERS
 * ────────────────────────────────────────────────────────────────────────────
 * §12's map (V/W/D/N/S/B/M/F, ⌘Z/⌘Y, 1/2/3, Tab, G) and §12's "typing a number
 * overrides the mouse" want the same keys. The resolution is an order, not a
 * compromise:
 *
 *   1. A **capture-phase** listener on `document` offers every keystroke to the
 *      active tool first, and only when `tool.wantsKey()` says yes. That is
 *      true for digits and length glyphs while mid-draw, for Esc/Enter while
 *      drawing, for Delete with a selection, and for the tool's own modifiers
 *      (`X`, `[`, `]`). Consumed keys stop there — `stopPropagation` in the
 *      capture phase means the bubble-phase map never sees them.
 *   2. Everything else falls through to `useKeyboardMap`, which owns the map.
 *
 * So `3` while drawing a wall is three thousand-and-something millimetres, and
 * `3` while idle is the second floor. Both are §12; neither is guessed at the
 * call site.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';

import { newId } from '@garh/model';

import { isTypingTarget, useKeyboardMap, type CommandHandlers, type ToolId } from '../../../lib/keymap';
import type { FurnitureItem } from '../../../lib/schemas';
import { useModelStore } from '../../../stores/model';
import { useSelectionStore } from '../../../stores/selection';
import { snapStepMm, useUiStore } from '../../../stores/ui';
import type { CanvasCore } from '../core/context';
import type { CanvasControlsCallbacks, CanvasPointerEvent } from '../core/useCanvasControls';
import { toolPreviewBus, type ToolPreviewBus } from './previewBus';
import { createTool } from './registry';
import type {
  SelectionIntent,
  SetbackContext,
  Tool,
  ToolContext,
  ToolKeyInput,
  ToolPointerInput,
  ToolResponse,
} from './types';
import { readToolSettings, useToolSettings } from './useToolSettings';

const EMPTY_CATALOG: ReadonlyMap<string, FurnitureItem> = new Map();

export interface ToolControllerOptions {
  /** The canvas core, once `<CanvasRoot onCoreReady>` has handed it over. */
  readonly core: CanvasCore | null;
  /** Turn the whole tool layer off (a modal owns the pointer and keyboard). */
  readonly enabled?: boolean | undefined;
  /** Buildable envelope + projection limit for the active storey, if known. */
  readonly setback?: SetbackContext | null | undefined;
  /** `/catalog/furniture`, keyed by id. */
  readonly furnitureCatalog?: ReadonlyMap<string, FurnitureItem> | undefined;
  /** Override the preview bus (specs, or a future split view). */
  readonly bus?: ToolPreviewBus | undefined;
}

export interface ToolController {
  /** Spread onto `<CanvasRoot {...canvasHandlers} />`. Stable identity. */
  readonly canvasHandlers: CanvasControlsCallbacks;
  readonly activeTool: ToolId;
  /** For the tool rail's "cancel" affordance and for route changes. */
  readonly cancelActiveTool: () => void;
  /** For a toolbar "finish" button — the mouse equivalent of Enter (§15). */
  readonly commitActiveTool: () => void;
}

export function useToolController(options: ToolControllerOptions): ToolController {
  const { core, enabled = true, setback = null, furnitureCatalog, bus = toolPreviewBus } = options;

  // The one piece of state worth re-rendering for.
  const activeTool = useUiStore((s) => s.activeTool);
  const keyboardEnabled = useUiStore((s) => s.keyboardEnabled);

  const toolRef = useRef<Tool | null>(null);
  const coreRef = useRef<CanvasCore | null>(core);
  const setbackRef = useRef<SetbackContext | null>(setback);
  const catalogRef = useRef<ReadonlyMap<string, FurnitureItem>>(furnitureCatalog ?? EMPTY_CATALOG);

  coreRef.current = core;
  setbackRef.current = setback;
  catalogRef.current = furnitureCatalog ?? EMPTY_CATALOG;

  // ── context ──────────────────────────────────────────────────────────────

  const buildContext = useCallback((): ToolContext => {
    const model = useModelStore.getState();
    const ui = useUiStore.getState();
    const selection = useSelectionStore.getState();
    return {
      doc: model.doc,
      storeyId: ui.activeStoreyId,
      snapModuleMm: snapStepMm(ui.snapMode),
      // A sane fallback before the camera exists: 10 mm/px is roughly 1:250,
      // and every use of it is a tolerance, never a coordinate.
      mmPerPx: coreRef.current?.viewport.mmPerPx ?? 10,
      unitsDisplay: model.doc.house.meta.unitsDisplay,
      settings: readToolSettings(),
      setback: setbackRef.current,
      furnitureCatalog: catalogRef.current,
      selectedIds: selection.ids,
      newId,
    };
  }, []);

  // ── publishing ───────────────────────────────────────────────────────────

  const publish = useCallback(() => {
    const tool = toolRef.current;
    if (tool === null) {
      bus.clear();
      return;
    }
    bus.set(tool.preview(buildContext()));
    coreRef.current?.invalidate();
  }, [bus, buildContext]);

  // ── applying a tool response ─────────────────────────────────────────────

  const applySelection = useCallback((intent: SelectionIntent) => {
    const selection = useSelectionStore.getState();
    switch (intent.mode) {
      case 'replace':
        selection.selectMany(intent.ids);
        break;
      case 'add':
        selection.add(intent.ids);
        break;
      case 'toggle':
        for (const id of intent.ids) selection.toggle(id);
        break;
      case 'clear':
        selection.clear();
        break;
      default:
        break;
    }
  }, []);

  const apply = useCallback(
    (response: ToolResponse) => {
      if (response.settingsPatch !== undefined) {
        useToolSettings.getState().patch(response.settingsPatch);
      }
      if (response.selection != null) applySelection(response.selection);

      const commit = response.commit;
      if (commit != null && commit.ops.length > 0) {
        const result = useModelStore.getState().dispatch(commit.ops, { label: commit.label });
        if (result.ok) {
          if (commit.selectIds !== undefined && commit.selectIds.length > 0) {
            useSelectionStore.getState().selectMany(commit.selectIds);
          }
        } else {
          // The local fold refused it. The tool already checked, so this is a
          // race (the document moved under the preview) — say what happened and
          // what to do, never a raw exception (golden rule 9).
          const issue = result.issues[0];
          useUiStore.getState().pushToast({
            tone: 'error',
            title: issue?.message ?? 'That change is not valid here.',
            description: issue?.fix ?? 'Try again, or undo the last change.',
            dedupeKey: 'tool-commit-rejected',
          });
        }
      }

      if (response.exitTool === true) useUiStore.getState().setTool('select');
      publish();
    },
    [applySelection, publish],
  );

  // ── tool lifecycle ───────────────────────────────────────────────────────

  useEffect(() => {
    toolRef.current?.cancel();
    toolRef.current = createTool(activeTool);
    useSelectionStore.getState().setMarquee(null);
    publish();
    return () => {
      toolRef.current?.cancel();
      toolRef.current = null;
      bus.clear();
    };
  }, [activeTool, bus, publish]);

  // ── pointer plumbing ─────────────────────────────────────────────────────

  const toInput = useCallback(
    (event: CanvasPointerEvent): ToolPointerInput => ({
      pointMm: event.pointMm,
      rawPointMm: event.rawPointMm,
      hit: event.hit,
      button: event.button,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
    }),
    [],
  );

  const canvasHandlers = useMemo<CanvasControlsCallbacks>(() => {
    const guard = (): Tool | null => (enabled ? toolRef.current : null);

    return {
      onPointerDown: (event) => {
        const tool = guard();
        if (tool === null) return;
        apply(tool.onPointerDown(buildContext(), toInput(event)));
      },
      onPointerMove: (event) => {
        const tool = guard();
        if (tool === null) return;
        const response = tool.onPointerMove(buildContext(), toInput(event));
        // Publish even for an unhandled move: the hover crosshair follows the
        // pointer whether or not the tool did anything with it.
        if (response.redraw === true || response.handled) apply(response);
      },
      onPointerUp: (event) => {
        const tool = guard();
        if (tool === null) return;
        apply(tool.onPointerUp(buildContext(), toInput(event)));
      },
      onDoubleClick: (event) => {
        const tool = guard();
        if (tool === null) return;
        // A double-click means "finish" everywhere in CAD; Enter already means
        // exactly that to every tool, so it routes there rather than growing a
        // seventh verb on the interface.
        apply(tool.onKey(buildContext(), keyInput('Enter', event)));
      },
      onContextMenu: (event) => {
        const tool = guard();
        if (tool === null) return;
        if (tool.phase === 'idle') return;
        apply(tool.onKey(buildContext(), keyInput('Escape', event)));
      },
      onPointerLeave: () => {
        publish();
      },
      onNavigatingChange: (navigating) => {
        // Panning with a half-drawn wall on screen is fine; the preview simply
        // stops following a pointer that is driving the camera.
        if (navigating) publish();
      },
    };
  }, [apply, buildContext, enabled, publish, toInput]);

  // ── layer 1: the capture-phase tool listener ─────────────────────────────

  useEffect(() => {
    if (!enabled || !keyboardEnabled) return;
    if (typeof document === 'undefined') return;

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.repeat && event.key !== 'Backspace') return;
      if (isTypingTarget(event.target)) return;
      const tool = toolRef.current;
      if (tool === null) return;

      const input: ToolKeyInput = {
        key: event.key,
        shiftKey: event.shiftKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        altKey: event.altKey,
      };
      if (!tool.wantsKey(input)) return;

      const response = tool.onKey(buildContext(), input);
      if (!response.handled) return;
      event.preventDefault();
      // Capture phase: this stops the keystroke before `useKeyboardMap`'s
      // bubble-phase listener on `document` ever sees it.
      event.stopPropagation();
      apply(response);
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [apply, buildContext, enabled, keyboardEnabled]);

  // ── layer 2: the §12 keyboard map ────────────────────────────────────────

  const commandHandlers = useMemo<CommandHandlers>(() => {
    const setTool = (id: ToolId) => (): void => {
      useUiStore.getState().setTool(id);
    };
    const gotoStorey = (index: number) => (): void | boolean => {
      const storeys = useModelStore.getState().doc.house.storeys;
      const storey = storeys[index];
      if (storey === undefined) return false;
      useUiStore.getState().setActiveStorey(storey.id);
      toolRef.current?.cancel();
      publish();
      return undefined;
    };

    return {
      'tool.select': setTool('select'),
      'tool.wall': setTool('wall'),
      'tool.door': setTool('door'),
      'tool.window': setTool('window'),
      'tool.stair': setTool('stair'),
      'tool.balcony': setTool('balcony'),
      'tool.measure': setTool('measure'),
      'tool.furniture': setTool('furniture'),

      'edit.undo': () => {
        toolRef.current?.cancel();
        useModelStore.getState().undo();
        publish();
      },
      'edit.redo': () => {
        toolRef.current?.cancel();
        useModelStore.getState().redo();
        publish();
      },

      'storey.1': gotoStorey(0),
      'storey.2': gotoStorey(1),
      'storey.3': gotoStorey(2),

      'view.toggle': () => {
        useUiStore.getState().toggleViewMode();
      },
      'snap.toggle': () => {
        useUiStore.getState().toggleSnap();
        publish();
      },

      // Reached only when the tool declined the key (it was idle), so this is
      // the "nothing is being drawn" meaning of Esc: drop the selection.
      'tool.cancel': () => {
        const tool = toolRef.current;
        if (tool !== null && tool.phase !== 'idle') {
          tool.cancel();
          publish();
          return;
        }
        useSelectionStore.getState().clear();
      },
      'tool.commit': () => {
        const tool = toolRef.current;
        if (tool === null) return false;
        apply(tool.onKey(buildContext(), { key: 'Enter', shiftKey: false, ctrlKey: false, metaKey: false, altKey: false }));
        return undefined;
      },
    };
  }, [apply, buildContext, publish]);

  useKeyboardMap(commandHandlers, { enabled: enabled && keyboardEnabled });

  // ── imperative escape hatches for toolbar buttons (§15 accessibility) ────

  const cancelActiveTool = useCallback(() => {
    toolRef.current?.cancel();
    publish();
  }, [publish]);

  const commitActiveTool = useCallback(() => {
    const tool = toolRef.current;
    if (tool === null) return;
    const commit = tool.commit(buildContext());
    if (commit === null) {
      publish();
      return;
    }
    tool.cancel();
    apply({ handled: true, commit });
  }, [apply, buildContext, publish]);

  return { canvasHandlers, activeTool, cancelActiveTool, commitActiveTool };
}

/** A synthetic key input for gestures that mean a key (double-click = Enter). */
function keyInput(key: string, event: CanvasPointerEvent): ToolKeyInput {
  return {
    key,
    shiftKey: event.shiftKey,
    ctrlKey: event.ctrlKey,
    metaKey: event.metaKey,
    altKey: event.altKey,
  };
}
