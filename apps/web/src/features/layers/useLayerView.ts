/**
 * useLayerView.ts — the three hooks that connect the layer store to the canvas.
 *
 * Each one is a single call at the Plan page's top level, and each one is the
 * ONLY wiring its half of the feature needs:
 *
 *   useLayerScope       remember this project+user's layers
 *   usePlanLayerView    what the plan draws (the model, and three visible props)
 *   useLayerPickGate    what the picker refuses (locked and hidden elements)
 *
 * ════════════════════════════════════════════════════════════════════════════
 * MEMOISATION IS A §14 REQUIREMENT HERE, NOT AN OPTIMISATION
 * ════════════════════════════════════════════════════════════════════════════
 * `PlanScene` memoises every merged buffer on `house` OBJECT IDENTITY, and the
 * model store replaces the document exactly once per op group. A hook that
 * returned a freshly-filtered model on every render would rebuild every vertex
 * buffer on every render — a pan would rebuild the plan sixty times a second.
 *
 * So: `usePlanLayerView` memoises on `(house, visible)`, and
 * `filterHouseByLayers` returns the input reference unchanged when nothing is
 * hidden. With the default layer state the canvas receives byte-for-byte the
 * same object it does today, and nothing downstream re-memoises at all.
 */

import { useEffect, useMemo, useRef } from 'react';

import type { HouseModel } from '@garh/model';

import type { CanvasCore } from '../canvas/core';
import { installLayerPickGate } from './pickGate';
import {
  blockedPicks,
  resolvePlanLayerView,
  type LayerPickBlock,
  type PlanLayerView,
} from './mapping';
import type { LayerScope } from './persist';
import { layerRows, useLayerStore, type LayerRow } from './store';

/**
 * Bind the store to a project and user, so changes are remembered for that
 * pair. Safe to call with nulls while the session or the project is still
 * loading — the panel works unbound, it simply does not persist yet.
 */
export function useLayerScope(userId: string | null, projectId: string | null): void {
  useEffect(() => {
    if (userId === null || projectId === null) return undefined;
    const scope: LayerScope = { userId, projectId };
    useLayerStore.getState().bind(scope);
    // Unbind on unmount rather than reset: leaving the plan tab must not
    // forget which layers you had off, and `bind` re-reads storage anyway.
    return () => {
      useLayerStore.getState().unbind();
    };
  }, [userId, projectId]);
}

/**
 * What the plan should draw. Hand `view.house` to `<PlanScene>`, and the three
 * booleans to the room fill, the dimension layer and the room-tag layer.
 */
export function usePlanLayerView(house: HouseModel): PlanLayerView {
  const visible = useLayerStore((s) => s.visible);
  return useMemo(() => resolvePlanLayerView(house, visible), [house, visible]);
}

/**
 * Install the lock gate on the canvas's one pick registry.
 *
 * `core` is null until `CanvasRoot` hands it over, so this is a no-op on the
 * first render and installs on the second. The gate reads its state through a
 * ref, so a lock toggled mid-session takes effect on the next click without
 * re-installing anything — re-installing on every state change would churn a
 * registry that is meant to change only on mount and unmount of geometry.
 */
export function useLayerPickGate(core: CanvasCore | null, house: HouseModel): void {
  const visible = useLayerStore((s) => s.visible);
  const locked = useLayerStore((s) => s.locked);

  const block = useMemo<LayerPickBlock>(
    () => blockedPicks(house, visible, locked),
    [house, visible, locked],
  );

  const latest = useRef(block);
  latest.current = block;

  useEffect(() => {
    if (core === null) return undefined;
    return installLayerPickGate(core.registry, () => latest.current);
  }, [core]);
}

/** The nine rows the panel renders. Re-derived only when the state changes. */
export function useLayerRows(): readonly LayerRow[] {
  const visible = useLayerStore((s) => s.visible);
  const locked = useLayerStore((s) => s.locked);
  const isolated = useLayerStore((s) => s.isolated);
  return useMemo(() => layerRows({ visible, locked, isolated }), [visible, locked, isolated]);
}
