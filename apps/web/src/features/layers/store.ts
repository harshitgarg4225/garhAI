/**
 * store.ts — the layer manager's state.
 *
 * Zustand, and a module store rather than context, for the reason
 * `features/underlay/store.ts` gives: this state has two consumers that cannot
 * see each other. `LayerPanel` lives in the DOM overlay; the pick gate and the
 * filtered model live inside react-three-fiber's separate React root, and
 * context does not cross that boundary.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ISOLATE
 * ════════════════════════════════════════════════════════════════════════════
 * Isolate is "show only this one, and put everything back when I leave". The
 * snapshot is taken at the moment isolate turns on and is restored verbatim on
 * exit — so isolating A-WALL while dimensions were already off, then exiting,
 * leaves dimensions off. Restoring to "everything on" would silently discard a
 * deliberate choice, and a drafter would not notice until the plan looked
 * wrong.
 *
 * Re-isolating a different layer while already isolated keeps the ORIGINAL
 * snapshot. Otherwise a walk through four layers would end up restoring the
 * third one's single-layer view, which is not what anyone means by "exit".
 *
 * Toggling visibility by hand while isolated drops isolate: the moment you
 * hand-edit the view, "restore what was here before" stops being a promise
 * anyone can keep. The A-TITL row is the one exception — see below.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * A-TITL, AND THE CONTROL THAT WOULD OTHERWISE DO NOTHING
 * ════════════════════════════════════════════════════════════════════════════
 * Eight of the nine layers have geometry on the plan. The title block does not
 * — it is drawn by the sheet frame in the drawings service. Its visibility is
 * still stored (it is a real DXF layer, and the export path is where it will
 * be consumed) but the panel renders that row as unavailable on this surface
 * and says why, rather than offering a switch that flips a boolean nothing
 * reads. `mapping.test.ts` holds the other eight to the opposite promise.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * PERSISTENCE
 * ════════════════════════════════════════════════════════════════════════════
 * `bind(scope)` is called by the page when the project and user are known. It
 * loads whatever was stored for that pair, or the default state when storage
 * is empty, unreadable, or throws outright. Every subsequent change is written
 * back on the same key. Before `bind`, and after `unbind`, the store still
 * works — it just does not remember, which is exactly what a browser with
 * storage switched off gets.
 */

import { create } from 'zustand';

import type { HouseModel } from '@garh/model';

import { DRAWING_LAYER_NAMES, layerSpec, type DrawingLayerName } from './layerSpecs';
import {
  blockedPicks,
  resolvePlanLayerView,
  type LayerFlags,
  type LayerPickBlock,
  type PlanLayerView,
} from './mapping';
import {
  allLayers,
  clearLayerState,
  defaultLayerState,
  readLayerState,
  writeLayerState,
  type LayerScope,
} from './persist';

export interface LayerState {
  /** Set once the page knows the project and the signed-in user. */
  scope: LayerScope | null;
  visible: LayerFlags;
  locked: LayerFlags;
  /** The layer currently isolated, or null. */
  isolated: DrawingLayerName | null;
  /** Visibility as it was when isolate turned on. Never persisted. */
  preIsolate: LayerFlags | null;

  /** Adopt a project+user scope and load its stored state. */
  bind: (scope: LayerScope) => void;
  /** Forget the scope (project closed). State stays as-is; writes stop. */
  unbind: () => void;

  setVisible: (layer: DrawingLayerName, on: boolean) => void;
  toggleVisible: (layer: DrawingLayerName) => void;
  setLocked: (layer: DrawingLayerName, on: boolean) => void;
  toggleLocked: (layer: DrawingLayerName) => void;

  /** Show only `layer`, remembering what was visible. Re-isolating swaps the target. */
  isolate: (layer: DrawingLayerName) => void;
  /** Leave isolate, restoring the remembered visibility exactly. */
  exitIsolate: () => void;
  /** Isolate `layer`, or leave isolate if it is already the isolated one. */
  toggleIsolate: (layer: DrawingLayerName) => void;

  /** Everything visible. Leaves locks alone — they are a different intent. */
  showAll: () => void;
  /** Back to the opening state, and drop the stored payload. */
  resetLayers: () => void;
}

/**
 * Persist the parts of `next` that belong on disk. Called from every mutator
 * rather than from a subscription so that a change and its write cannot get
 * out of order, and so nothing writes while the store is unbound.
 */
function persist(state: Pick<LayerState, 'scope' | 'visible' | 'locked'>): void {
  if (state.scope === null) return;
  writeLayerState(state.scope, { visible: state.visible, locked: state.locked });
}

export const useLayerStore = create<LayerState>()((set, get) => ({
  scope: null,
  ...defaultLayerState(),
  isolated: null,
  preIsolate: null,

  bind: (scope) => {
    const current = get().scope;
    if (
      current !== null &&
      current.userId === scope.userId &&
      current.projectId === scope.projectId
    ) {
      return;
    }
    // A stored payload or the default — `readLayerState` answers null for
    // "nothing", "garbage" and "storage threw" alike, and the response to all
    // three is the same opening state.
    const loaded = readLayerState(scope) ?? defaultLayerState();
    set({
      scope,
      visible: loaded.visible,
      locked: loaded.locked,
      // Switching projects must not carry an isolate across with it.
      isolated: null,
      preIsolate: null,
    });
  },

  unbind: () => set({ scope: null }),

  setVisible: (layer, on) => {
    const state = get();
    if (state.visible[layer] === on) return;
    const visible = { ...state.visible, [layer]: on };
    // A hand edit is the end of isolate: "put back what was here" is no longer
    // a promise this store can keep once the view has been edited underneath it.
    const next = { visible, isolated: null, preIsolate: null };
    set(next);
    persist({ scope: state.scope, visible, locked: state.locked });
  },

  toggleVisible: (layer) => {
    get().setVisible(layer, !get().visible[layer]);
  },

  setLocked: (layer, on) => {
    const state = get();
    if (state.locked[layer] === on) return;
    const locked = { ...state.locked, [layer]: on };
    set({ locked });
    persist({ scope: state.scope, visible: state.visible, locked });
  },

  toggleLocked: (layer) => {
    get().setLocked(layer, !get().locked[layer]);
  },

  isolate: (layer) => {
    const state = get();
    // Isolating a layer the plan does not draw would blank the canvas and show
    // nothing in its place. The panel does not offer the control on those rows;
    // this refuses it on the API too, so no caller can reach that state.
    if (!layerSpec(layer).onCanvas) return;
    // Keep the ORIGINAL snapshot when walking from one isolated layer to the
    // next, so "exit" always means the view you started from.
    const preIsolate = state.preIsolate ?? state.visible;
    const visible = allLayers(false);
    visible[layer] = true;
    set({ visible, isolated: layer, preIsolate });
    persist({ scope: state.scope, visible, locked: state.locked });
  },

  exitIsolate: () => {
    const state = get();
    if (state.isolated === null) return;
    const visible = state.preIsolate ?? allLayers(true);
    set({ visible, isolated: null, preIsolate: null });
    persist({ scope: state.scope, visible, locked: state.locked });
  },

  toggleIsolate: (layer) => {
    const state = get();
    if (state.isolated === layer) state.exitIsolate();
    else state.isolate(layer);
  },

  showAll: () => {
    const state = get();
    const visible = allLayers(true);
    set({ visible, isolated: null, preIsolate: null });
    persist({ scope: state.scope, visible, locked: state.locked });
  },

  resetLayers: () => {
    const state = get();
    const fresh = defaultLayerState();
    set({ visible: fresh.visible, locked: fresh.locked, isolated: null, preIsolate: null });
    if (state.scope !== null) clearLayerState(state.scope);
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectLayerVisibility = (s: LayerState): LayerFlags => s.visible;
export const selectLayerLocks = (s: LayerState): LayerFlags => s.locked;
export const selectIsolatedLayer = (s: LayerState): DrawingLayerName | null => s.isolated;

/** How many of the nine are hidden — the panel's "3 hidden" badge. */
export const selectHiddenCount = (s: LayerState): number =>
  DRAWING_LAYER_NAMES.reduce((n, name) => (s.visible[name] ? n : n + 1), 0);

/** How many are locked. */
export const selectLockedCount = (s: LayerState): number =>
  DRAWING_LAYER_NAMES.reduce((n, name) => (s.locked[name] ? n + 1 : n), 0);

// ---------------------------------------------------------------------------
// Derivations the canvas consumes
// ---------------------------------------------------------------------------

/**
 * What the plan should draw, for a given model and the CURRENT layer state.
 *
 * Read imperatively (`useLayerStore.getState()`) rather than through a hook so
 * that non-React callers — the pick gate's `read()` among them — use the same
 * function the React side does. There is one derivation, not two.
 */
export function planLayerViewFor(house: HouseModel, state: LayerState): PlanLayerView {
  return resolvePlanLayerView(house, state.visible);
}

/** The ids and kinds the picker must refuse, for the CURRENT layer state. */
export function blockedPicksFor(house: HouseModel, state: LayerState): LayerPickBlock {
  return blockedPicks(house, state.visible, state.locked);
}

/**
 * A layer's row model for the panel — one place that decides what a row is
 * allowed to do, so the component holds no policy.
 */
export interface LayerRow {
  readonly name: DrawingLayerName;
  readonly label: string;
  readonly description: string;
  /** ACI colour code, for the swatch. */
  readonly aci: number;
  readonly visible: boolean;
  readonly locked: boolean;
  readonly isolated: boolean;
  /**
   * False for a layer the plan editor does not draw. The row is shown (it is a
   * real DXF layer and the export will carry it) but its controls are inert
   * and labelled, instead of pretending to do something.
   */
  readonly actsOnCanvas: boolean;
  /** Why the controls are inert, when they are. Null when they are live. */
  readonly unavailableReason: string | null;
}

/**
 * Takes only the three fields it reads, not the whole store. That is what lets
 * `useLayerRows` memoise on exactly those three — handed the full state it
 * would have to either re-derive on every action-identity change or reach for
 * `getState()` inside a memo, and the second is how a row list goes stale.
 */
export function layerRows(
  state: Pick<LayerState, 'visible' | 'locked' | 'isolated'>,
): readonly LayerRow[] {
  return DRAWING_LAYER_NAMES.map((name) => {
    const spec = layerSpec(name);
    return {
      name,
      label: spec.label,
      description: spec.description,
      aci: spec.aci,
      visible: state.visible[name],
      locked: state.locked[name],
      isolated: state.isolated === name,
      actsOnCanvas: spec.onCanvas,
      unavailableReason: spec.onCanvas
        ? null
        : 'Drawn on the exported sheet, not on the plan — the sheet frame owns it.',
    };
  });
}
