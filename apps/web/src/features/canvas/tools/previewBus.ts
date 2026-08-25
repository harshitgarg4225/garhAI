/**
 * previewBus.ts — how the tool preview reaches the screen without a re-render
 * per pointer move.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE §14 PROBLEM
 * ────────────────────────────────────────────────────────────────────────────
 * A tool produces a new preview on every pointer move. If that preview were
 * React state, every move would re-render the component that owns it — and in
 * a canvas app the component that owns it is an ancestor of the scene, so a
 * 500 Hz mouse would reconcile the whole drawing 60 times a second on top of
 * the work the renderer is already doing. The frame budget is 16 ms in total.
 *
 * So the preview lives OUTSIDE React, in a mutable holder, and consumers pick
 * their poison deliberately:
 *
 *   - **Scene overlays** (the three.js preview meshes) read {@link get} inside
 *     their frame callback and mutate geometry in place. Zero renders.
 *   - **DOM chrome** (the numeric-entry HUD, the readouts) subscribe through
 *     {@link useToolPreview}, which is `useSyncExternalStore`. That re-renders
 *     a handful of nodes at most once per animation frame — pointer moves are
 *     already coalesced to one per frame by `useCanvasControls` — and the
 *     subtree unmounts entirely while no tool is drawing.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY A MODULE SINGLETON
 * ────────────────────────────────────────────────────────────────────────────
 * There is one `<CanvasRoot>` in the app (§12: one scene graph, 2D and 3D share
 * it), so there is one active tool and one preview. A context would add a
 * provider and a `useContext` on the hot path to model a plurality that does
 * not exist. {@link ToolPreviewBus} is exported as a class anyway, so a spec —
 * or a future split-view — can make its own.
 */

import { useCallback, useSyncExternalStore } from 'react';

import type { ToolPreview } from './types';

export type PreviewListener = () => void;

export class ToolPreviewBus {
  private current: ToolPreview | null = null;

  private readonly listeners = new Set<PreviewListener>();

  /** The live preview, or null when no tool has published one. */
  get(): ToolPreview | null {
    return this.current;
  }

  /**
   * Publish. Skips the notification when nothing consumers care about changed —
   * same tool, same version — which is what keeps a stationary pointer from
   * waking the HUD 60 times a second.
   */
  set(preview: ToolPreview | null): void {
    const previous = this.current;
    if (preview === null && previous === null) return;
    if (
      preview !== null &&
      previous !== null &&
      preview.toolId === previous.toolId &&
      preview.version === previous.version &&
      preview.phase === previous.phase
    ) {
      // Same version: the tool did not call `touch()`, so nothing visible moved.
      this.current = preview;
      return;
    }
    this.current = preview;
    for (const listener of this.listeners) listener();
  }

  subscribe(listener: PreviewListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Drop the preview and tell everyone. Called when a tool is torn down. */
  clear(): void {
    if (this.current === null) return;
    this.current = null;
    for (const listener of this.listeners) listener();
  }
}

/** The app's single bus. */
export const toolPreviewBus = new ToolPreviewBus();

/**
 * Subscribe a DOM component to the preview.
 *
 * `useSyncExternalStore` rather than `useEffect` + `useState`: it is the only
 * subscription form React 18 guarantees will not tear during a concurrent
 * render, and a HUD that shows a length from one frame next to an echo from
 * another is a HUD nobody can trust.
 */
export function useToolPreview(bus: ToolPreviewBus = toolPreviewBus): ToolPreview | null {
  const subscribe = useCallback((listener: PreviewListener) => bus.subscribe(listener), [bus]);
  const snapshot = useCallback(() => bus.get(), [bus]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
