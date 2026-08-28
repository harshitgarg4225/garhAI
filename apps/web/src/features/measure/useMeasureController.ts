/**
 * useMeasureController.ts — where the measure session meets the app.
 *
 * The same thin shape as `tools/useToolController.ts`: build a context from the
 * current state, hand an event to the machine, apply what it asked for. Every
 * decision is in `session.ts`; this is wiring, and it is the only file in the
 * feature that reads a store the feature does not own.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * §14 — WHAT RE-RENDERS WHEN THE POINTER MOVES
 * ────────────────────────────────────────────────────────────────────────────
 * The session lives in a ref, the stores are read through `getState()` inside
 * the handlers rather than subscribed, and the returned handler object is
 * stable — so this hook itself renders only when the measure KIND changes, a
 * few times a minute. A pointer move writes the draft to `useMeasureStore`,
 * which re-renders the readout panel (a dozen DOM nodes) and nothing else:
 * `MeasureLayer` reads the same store imperatively and mutates buffers. Moves
 * are already coalesced to one per animation frame by `useCanvasControls`.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE ONE DUPLICATION, NAMED
 * ────────────────────────────────────────────────────────────────────────────
 * `buildContext` mirrors `useToolController`'s private `buildContext`. That is
 * a genuine second source of truth for the snap module and the zoom, and it is
 * here only because the original is not exported. Of the ten fields, this
 * feature reads five (`doc`, `storeyId`, `snapModuleMm`, `mmPerPx`,
 * `unitsDisplay`); the rest exist to satisfy `ToolContext` so that the session
 * can call the drawing tools' own `resolveSnap` unchanged, which is worth far
 * more than the duplication costs. Exporting `buildToolContext` from the tool
 * controller and passing it in would delete this function — see the handoff.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';

import { newId } from '@garh/model';

import { isTypingTarget } from '../../lib/keymap';
import { useModelStore } from '../../stores/model';
import { snapStepMm, useUiStore } from '../../stores/ui';
import { useSelectionStore } from '../../stores/selection';
import type { CanvasCore } from '../canvas/core/context';
import type { CanvasControlsCallbacks, CanvasPointerEvent } from '../canvas/core/useCanvasControls';
import type { ToolContext, ToolPointerInput } from '../canvas/tools/types';
import { readToolSettings } from '../canvas/tools/useToolSettings';
import type { FurnitureItem } from '../../lib/schemas';
import { MeasureSession, type MeasureResponse } from './session';
import { useMeasureStore } from './store';
import type { MeasureKind } from './types';

const EMPTY_CATALOG: ReadonlyMap<string, FurnitureItem> = new Map();

export interface MeasureControllerOptions {
  /** The canvas core, once `<CanvasRoot onCoreReady>` has handed it over. */
  readonly core: CanvasCore | null;
  /** True while the measure tool owns the pointer. Nothing happens when false. */
  readonly enabled: boolean;
}

export interface MeasureController {
  /** Spread onto `<CanvasRoot>` (or merge with the tool controller's). Stable. */
  readonly canvasHandlers: CanvasControlsCallbacks;
  readonly kind: MeasureKind;
  readonly setKind: (kind: MeasureKind) => void;
  /** Discard the draft — the panel's Cancel, and every route change. */
  readonly cancel: () => void;
  /** Finish the draft — the panel's button, the mouse equivalent of Enter (§15). */
  readonly finish: () => void;
}

export function useMeasureController(options: MeasureControllerOptions): MeasureController {
  const { core, enabled } = options;

  const kind = useMeasureStore((s) => s.kind);

  const sessionRef = useRef<MeasureSession | null>(null);
  // Lazily, NOT `useRef(new MeasureSession())`: the argument to `useRef` is
  // evaluated on every render and thrown away after the first, so the eager
  // form mints a session (and an id counter) per render.
  sessionRef.current ??= new MeasureSession({ kind });

  const coreRef = useRef<CanvasCore | null>(core);
  coreRef.current = core;
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  // ── context ──────────────────────────────────────────────────────────────

  const buildContext = useCallback((): ToolContext => {
    const model = useModelStore.getState();
    const ui = useUiStore.getState();
    return {
      doc: model.doc,
      storeyId: ui.activeStoreyId,
      snapModuleMm: snapStepMm(ui.snapMode),
      // Same fallback as the tool controller: 10 mm/px is roughly 1:250, and
      // every use of it here is a TOLERANCE, never a coordinate.
      mmPerPx: coreRef.current?.viewport.mmPerPx ?? 10,
      unitsDisplay: model.doc.house.meta.unitsDisplay,
      settings: readToolSettings(),
      setback: null,
      furnitureCatalog: EMPTY_CATALOG,
      selectedIds: useSelectionStore.getState().ids,
      newId,
    };
  }, []);

  // ── applying a response ──────────────────────────────────────────────────

  const apply = useCallback((response: MeasureResponse): void => {
    const session = sessionRef.current;
    if (session === null) return;
    // A move with nothing started changes nothing this feature draws (the snap
    // marker belongs to the tool preview layer, not to us). Writing the store
    // anyway would be one zustand notification per animation frame for as long
    // as the pointer is over the canvas with the tool merely armed.
    if (!response.handled && response.committed === null && response.blocked === null) return;
    const store = useMeasureStore.getState();

    if (response.committed !== null) store.add(response.committed);
    if (response.blocked !== null) store.setNotice(response.blocked);
    // The draft is published on EVERY applied response, including the ones that
    // cleared it: a commit that left a stale draft behind would draw the
    // measurement twice, once live and once persisted.
    store.setDraft(session.draft());
    if (response.redraw) coreRef.current?.invalidate();
  }, []);

  // ── pointer ──────────────────────────────────────────────────────────────

  const toPointerInput = useCallback((event: CanvasPointerEvent): ToolPointerInput => {
    return {
      pointMm: event.pointMm,
      rawPointMm: event.rawPointMm,
      hit: event.hit,
      button: event.button,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
    };
  }, []);

  const canvasHandlers = useMemo<CanvasControlsCallbacks>(
    () => ({
      onPointerDown: (event) => {
        if (!enabledRef.current) return;
        const session = sessionRef.current;
        if (session === null) return;
        apply(session.pointerDown(buildContext(), toPointerInput(event)));
      },
      onPointerMove: (event) => {
        if (!enabledRef.current) return;
        const session = sessionRef.current;
        if (session === null) return;
        apply(session.pointerMove(buildContext(), toPointerInput(event)));
      },
      onDoubleClick: () => {
        if (!enabledRef.current) return;
        const session = sessionRef.current;
        if (session === null) return;
        apply(session.doubleClick(buildContext()));
      },
    }),
    [apply, buildContext, toPointerInput],
  );

  // ── keyboard ─────────────────────────────────────────────────────────────
  //
  // Capture phase on `document`, the same place the tool layer takes its keys,
  // so Enter/Esc/Backspace reach the measurement before the page's keyboard map
  // turns Esc into "clear the selection". Only attached while the tool is
  // armed, and only `stopPropagation` on keys the session actually consumed —
  // an unconsumed Esc must still reach the map.
  useEffect(() => {
    if (!enabled) return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (isTypingTarget(event.target)) return;
      const session = sessionRef.current;
      if (session === null) return;
      const response = session.key(buildContext(), {
        key: event.key,
        shiftKey: event.shiftKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        altKey: event.altKey,
      });
      if (!response.handled) return;
      event.preventDefault();
      event.stopPropagation();
      apply(response);
    };
    document.addEventListener('keydown', onKeyDown, { capture: true });
    return () => document.removeEventListener('keydown', onKeyDown, { capture: true });
  }, [enabled, apply, buildContext]);

  // ── mode + lifecycle ─────────────────────────────────────────────────────

  useEffect(() => {
    const session = sessionRef.current;
    if (session === null) return;
    apply(session.setKind(kind));
  }, [kind, apply]);

  // Disarming abandons the draft. A half-finished chain that survived a switch
  // to the wall tool would come back to life on the next measure click, three
  // edits later, anchored to a point the architect has forgotten. The cleanup
  // is registered WHILE armed, so it runs on the transition to disarmed and on
  // unmount — registering it while disarmed would never run at the right time.
  useEffect(() => {
    if (!enabled) return undefined;
    return () => {
      sessionRef.current?.cancel();
      useMeasureStore.getState().setDraft(null);
    };
  }, [enabled]);

  const cancel = useCallback(() => {
    sessionRef.current?.cancel();
    useMeasureStore.getState().setDraft(null);
    coreRef.current?.invalidate();
  }, []);

  const finish = useCallback(() => {
    const session = sessionRef.current;
    if (session === null) return;
    apply(session.commit(buildContext()));
  }, [apply, buildContext]);

  const setKind = useCallback((next: MeasureKind) => {
    useMeasureStore.getState().setKind(next);
  }, []);

  return { canvasHandlers, kind, setKind, cancel, finish };
}
