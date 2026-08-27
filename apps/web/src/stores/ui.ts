/**
 * `ui` — chrome state (§12). Which tool is armed, which storey and view you are
 * looking at, which panels are open, what the snap grid is, and the toast queue.
 *
 * Nothing here touches the design. If a piece of state would survive a reload
 * and matter to the drawing, it belongs in the model store as an op, not here.
 *
 * The toast queue lives in this store rather than in a React context because
 * the model store must be able to say "that edit was rejected — here is why"
 * from inside an async flush, with no component in scope. Golden rule 9 is only
 * enforceable if reporting a failure is always available.
 */

import { create } from 'zustand';

import { applyTheme, readStoredTheme, storeTheme } from '@garh/ui';

import type { AppError } from '../lib/errors';
import { SNAP_COARSE_MM, SNAP_FINE_MM } from '../lib/units';
import type { ToolId } from '../lib/keymap';

export type ViewMode = '2d' | '3d';
export type SnapMode = 'module' | 'fine' | 'off';
export type ThemePreference = 'light' | 'dark' | 'system';
export type ToastTone = 'info' | 'success' | 'warning' | 'error';

/**
 * Drawing layers the architect can switch off. Not the renderer's layers
 * (`CANVAS_LAYERS` in the canvas core is a draw-order table); these are the
 * five things a drafter routinely wants out of the way while working.
 */
export const CANVAS_TOGGLES = [
  'grid',
  'dimensions',
  'roomTags',
  'furniture',
  'compliance',
] as const;
export type CanvasToggle = (typeof CANVAS_TOGGLES)[number];

/**
 * A request to put the camera on some elements — a compliance chip clicked in
 * the bottom strip, a search result, a copilot diff.
 *
 * It travels through the store rather than through props because the strip
 * lives in `ProjectShell` and the camera lives inside the Plan tab's canvas,
 * with a router `<Outlet>` between them. `at` makes the request re-fire when
 * the same chip is clicked twice, which is what a user expects.
 */
export interface CanvasFocusRequest {
  readonly elementIds: readonly string[];
  /** What asked for it, for the canvas's own highlight ("this chip"). */
  readonly key: string | null;
  readonly at: number;
}

/**
 * A toast. `action` is not decoration — §15 wants "Wall deleted — Undo", and an
 * error toast without a next step is a golden-rule-9 violation waiting to ship.
 */
export interface Toast {
  readonly id: string;
  readonly tone: ToastTone;
  readonly title: string;
  readonly description: string | null;
  readonly action: { readonly label: string; readonly run: () => void } | null;
  /** Milliseconds before auto-dismiss. `0` keeps it until dismissed. */
  readonly durationMs: number;
  readonly createdAt: number;
  /** Support correlation id, when the failure came from the API. */
  readonly requestId: string | null;
  /** Collapse key — see {@link ToastInput.dedupeKey}. `null` when not deduped. */
  readonly dedupeKey: string | null;
}

export interface ToastInput {
  readonly tone?: ToastTone;
  readonly title: string;
  readonly description?: string | null;
  readonly action?: { readonly label: string; readonly run: () => void } | null;
  readonly durationMs?: number;
  readonly requestId?: string | null;
  /**
   * Collapse repeats. A second toast with the same key replaces the first
   * instead of stacking — which is what keeps a flapping connection from
   * burying the screen in identical "couldn't reach Garh AI" cards.
   */
  readonly dedupeKey?: string;
}

/** A modal request. The UI layer owns the components; this is just the routing. */
export interface ModalRequest {
  readonly kind: string;
  readonly props: Readonly<Record<string, unknown>>;
}

export interface UiState {
  activeTool: ToolId;
  viewMode: ViewMode;
  /** Which storey the canvas is showing. Null until the model has loaded. */
  activeStoreyId: string | null;
  snapMode: SnapMode;

  // ── canvas slice (§12 chrome for the 2D/3D editor) ─────────────────────
  /**
   * Layer visibility. Everything on by default: a drafter turns things off
   * when a drawing gets busy, and a new user should see what the tools produce.
   */
  canvasLayers: Readonly<Record<CanvasToggle, boolean>>;
  /**
   * Zoom, as millimetres per CSS pixel, mirrored out of the viewport
   * controller for the scale readout and for anything that needs a
   * zoom-dependent threshold in React.
   *
   * §14 WARNING: the camera's real zoom lives in `ViewportController`, outside
   * React, and a pan/zoom gesture must not write here on every frame. The Plan
   * page mirrors it only when the printed scale label CHANGES — a few times per
   * gesture, not sixty. Read `scaleLabel` if that is all you need; subscribing
   * to `mmPerPx` costs a render per zoom band.
   */
  mmPerPx: number;
  /** "1:100" — the printed-scale readout. Changes far less often than the zoom. */
  scaleLabel: string;
  /** Latest zoom-to-elements request, or null. See {@link CanvasFocusRequest}. */
  canvasFocus: CanvasFocusRequest | null;

  leftRailOpen: boolean;
  inspectorOpen: boolean;
  complianceStripOpen: boolean;
  copilotOpen: boolean;
  /** The §5.5 options overlay on the Plan tab — opened by Generate. */
  optionsOpen: boolean;
  setOptionsOpen: (open: boolean) => void;

  /** Global keyboard-map switch. A focus-trapped dialog turns it off. */
  keyboardEnabled: boolean;
  /**
   * True while the canvas tool controller's own keyboard map is live (the
   * editor page sets it: mounted AND in 2D). Both that map and the app-wide
   * one (`useAppShortcuts`) listen on `document`, and `stopPropagation` does
   * not stop a second listener on the SAME node — so without this flag every
   * overlapping command (⌘Z, Tab, G, Esc, 1/2/3) would fire twice: two undos
   * per ⌘Z, a Tab that toggles 2D→3D→2D and appears dead. The app-wide
   * handlers DECLINE (return false) while this is true; the tool controller
   * is then the single owner. See `lib/shortcuts.ts`.
   */
  toolKeysActive: boolean;
  modal: ModalRequest | null;
  toasts: Toast[];
  theme: ThemePreference;

  /** First-run coach marks (§15). `null` = not running. */
  tourStep: number | null;
  /**
   * Whether the 5-step tour has been completed or skipped. Persisted, because
   * "first run" means once per person, not once per tab.
   */
  tourDone: boolean;

  // ── actions ────────────────────────────────────────────────────────────
  setTool: (tool: ToolId) => void;
  setViewMode: (mode: ViewMode) => void;
  toggleViewMode: () => void;
  setActiveStorey: (storeyId: string | null) => void;
  setSnapMode: (mode: SnapMode) => void;
  /** Cycle module → fine → module. `off` is only reachable explicitly. */
  toggleSnap: () => void;

  setCanvasLayer: (layer: CanvasToggle, on: boolean) => void;
  toggleCanvasLayer: (layer: CanvasToggle) => void;
  /** Mirror the viewport's zoom. No-ops when neither value actually moved. */
  setCanvasZoom: (mmPerPx: number, scaleLabel: string) => void;
  /** Ask the canvas to select and frame these elements. */
  requestCanvasFocus: (elementIds: readonly string[], key?: string | null) => void;
  clearCanvasFocus: () => void;

  setPanel: (
    panel: 'leftRail' | 'inspector' | 'complianceStrip' | 'copilot',
    open: boolean,
  ) => void;
  togglePanel: (panel: 'leftRail' | 'inspector' | 'complianceStrip' | 'copilot') => void;

  setKeyboardEnabled: (enabled: boolean) => void;
  /** The editor page's mount/unmount contract for the flag above. */
  setToolKeysActive: (active: boolean) => void;
  openModal: (kind: string, props?: Record<string, unknown>) => void;
  closeModal: () => void;

  pushToast: (input: ToastInput) => string;
  /** Convenience for the most common toast: an `AppError` with its own action. */
  toastError: (error: AppError, override?: Partial<ToastInput>) => string;
  dismissToast: (id: string) => void;
  clearToasts: () => void;

  setTheme: (theme: ThemePreference) => void;
  startTour: () => void;
  setTourStep: (step: number | null) => void;
  /** Marks the tour finished (or skipped) and persists that. */
  setTourDone: (done: boolean) => void;
}

const DEFAULT_TOAST_MS = 6_000;
const ERROR_TOAST_MS = 10_000;
/** More than this on screen at once is noise, not information. */
const MAX_TOASTS = 4;

let toastSeq = 0;
function nextToastId(): string {
  toastSeq += 1;
  return `toast_${toastSeq}`;
}

/** Millimetres per snap step for a mode. `off` means "no rounding". */
export function snapStepMm(mode: SnapMode): number {
  if (mode === 'module') return SNAP_COARSE_MM;
  if (mode === 'fine') return SNAP_FINE_MM;
  return 0;
}

/** Where "has this person seen the tour?" is remembered across sessions. */
const TOUR_STORAGE_KEY = 'garh.tourDone';

function readTourDone(): boolean {
  if (typeof localStorage === 'undefined') return false;
  return localStorage.getItem(TOUR_STORAGE_KEY) === '1';
}

export const useUiStore = create<UiState>()((set, get) => ({
  activeTool: 'select',
  viewMode: '2d',
  activeStoreyId: null,
  snapMode: 'module',

  canvasLayers: {
    grid: true,
    dimensions: true,
    roomTags: true,
    furniture: true,
    compliance: true,
  },
  mmPerPx: 10,
  scaleLabel: '',
  canvasFocus: null,

  leftRailOpen: true,
  inspectorOpen: true,
  complianceStripOpen: true,
  copilotOpen: false,
  optionsOpen: false,
  setOptionsOpen: (open) => set({ optionsOpen: open }),

  keyboardEnabled: true,
  toolKeysActive: false,
  modal: null,
  toasts: [],
  // Seeded from the same storage key `@garh/ui`'s `initTheme()` reads at boot,
  // so the toggle in the UI starts on whatever the page already rendered as.
  theme: readStoredTheme(),
  tourStep: null,
  tourDone: readTourDone(),

  setTool: (tool) => set({ activeTool: tool }),

  setViewMode: (mode) => set({ viewMode: mode }),

  toggleViewMode: () => set((s) => ({ viewMode: s.viewMode === '2d' ? '3d' : '2d' })),

  setActiveStorey: (storeyId) => set({ activeStoreyId: storeyId }),

  setSnapMode: (mode) => set({ snapMode: mode }),

  toggleSnap: () => set((s) => ({ snapMode: s.snapMode === 'module' ? 'fine' : 'module' })),

  setCanvasLayer: (layer, on) => {
    if (get().canvasLayers[layer] === on) return;
    set((s) => ({ canvasLayers: { ...s.canvasLayers, [layer]: on } }));
  },

  toggleCanvasLayer: (layer) =>
    set((s) => ({ canvasLayers: { ...s.canvasLayers, [layer]: !s.canvasLayers[layer] } })),

  setCanvasZoom: (mmPerPx, scaleLabel) => {
    const s = get();
    // The guard is the point of this action, not an optimisation. Without it a
    // pan/zoom gesture would re-render every `mmPerPx` subscriber once per
    // animation frame and the §14 budget would go with it.
    if (s.mmPerPx === mmPerPx && s.scaleLabel === scaleLabel) return;
    set({ mmPerPx, scaleLabel });
  },

  requestCanvasFocus: (elementIds, key = null) =>
    set({ canvasFocus: { elementIds: [...elementIds], key, at: Date.now() } }),

  clearCanvasFocus: () => {
    if (get().canvasFocus === null) return;
    set({ canvasFocus: null });
  },

  setPanel: (panel, open) => {
    if (panel === 'leftRail') set({ leftRailOpen: open });
    else if (panel === 'inspector') set({ inspectorOpen: open });
    else if (panel === 'complianceStrip') set({ complianceStripOpen: open });
    else set({ copilotOpen: open });
  },

  togglePanel: (panel) => {
    const s = get();
    const current =
      panel === 'leftRail'
        ? s.leftRailOpen
        : panel === 'inspector'
          ? s.inspectorOpen
          : panel === 'complianceStrip'
            ? s.complianceStripOpen
            : s.copilotOpen;
    s.setPanel(panel, !current);
  },

  setKeyboardEnabled: (enabled) => set({ keyboardEnabled: enabled }),

  setToolKeysActive: (active) => {
    if (get().toolKeysActive === active) return;
    set({ toolKeysActive: active });
  },

  // A modal owns the keyboard while it is open, so the map goes quiet rather
  // than the dialog having to swallow every shortcut individually.
  openModal: (kind, props = {}) => set({ modal: { kind, props }, keyboardEnabled: false }),

  closeModal: () => set({ modal: null, keyboardEnabled: true }),

  pushToast: (input) => {
    const id = nextToastId();
    const toast: Toast = {
      id,
      tone: input.tone ?? 'info',
      title: input.title,
      description: input.description ?? null,
      action: input.action ?? null,
      durationMs: input.durationMs ?? (input.tone === 'error' ? ERROR_TOAST_MS : DEFAULT_TOAST_MS),
      createdAt: Date.now(),
      requestId: input.requestId ?? null,
      dedupeKey: input.dedupeKey ?? null,
    };

    set((s) => {
      // Collapse on the KEY, not on the text: "We couldn't save \"move wall\""
      // and "We couldn't save \"add door\"" are the same recurring failure, and
      // a flapping connection must not bury the screen in variations of it.
      const kept = input.dedupeKey
        ? s.toasts.filter((t) => t.dedupeKey !== input.dedupeKey)
        : s.toasts;
      const next = [...kept, toast];
      return { toasts: next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next };
    });

    if (toast.durationMs > 0) {
      setTimeout(() => {
        // Read through the store, not a captured closure: the toast may already
        // have been dismissed by hand.
        useUiStore.getState().dismissToast(id);
      }, toast.durationMs);
    }
    return id;
  },

  toastError: (error, override = {}) =>
    get().pushToast({
      tone: 'error',
      title: error.message,
      // The API's `action` is the next step; showing it is the whole contract.
      description: error.action,
      requestId: error.requestId,
      ...override,
    }),

  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  clearToasts: () => set({ toasts: [] }),

  /**
   * The store owns the *preference*; `@garh/ui` owns the *mechanism* (which
   * class and which `data-theme` attribute go on `<html>`, and how `'system'`
   * resolves). Delegating rather than reimplementing matters because
   * `tokens.css` keys off both hooks — a second implementation that sets only
   * the class would theme the Tailwind utilities and leave the token block
   * behind.
   */
  setTheme: (theme) => {
    set({ theme });
    storeTheme(theme);
    applyTheme(theme);
  },

  startTour: () => set({ tourStep: 0, tourDone: false }),

  setTourStep: (step) => set({ tourStep: step }),

  setTourDone: (done) => {
    set({ tourDone: done, ...(done ? { tourStep: null } : {}) });
    if (typeof localStorage !== 'undefined') {
      if (done) localStorage.setItem(TOUR_STORAGE_KEY, '1');
      else localStorage.removeItem(TOUR_STORAGE_KEY);
    }
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectActiveTool = (s: UiState): ToolId => s.activeTool;
export const selectViewMode = (s: UiState): ViewMode => s.viewMode;
export const selectActiveStoreyId = (s: UiState): string | null => s.activeStoreyId;
export const selectSnapMode = (s: UiState): SnapMode => s.snapMode;
/** Millimetres per snap step, ready to hand to `snapMm()`. */
export const selectSnapStepMm = (s: UiState): number => snapStepMm(s.snapMode);
export const selectCanvasLayers = (s: UiState): Readonly<Record<CanvasToggle, boolean>> =>
  s.canvasLayers;
/** Curried: `useUiStore(selectCanvasLayer('dimensions'))`. */
export const selectCanvasLayer =
  (layer: CanvasToggle) =>
  (s: UiState): boolean =>
    s.canvasLayers[layer];
export const selectScaleLabel = (s: UiState): string => s.scaleLabel;
export const selectCanvasFocus = (s: UiState): CanvasFocusRequest | null => s.canvasFocus;
export const selectToasts = (s: UiState): Toast[] => s.toasts;
export const selectModal = (s: UiState): ModalRequest | null => s.modal;
export const selectTheme = (s: UiState): ThemePreference => s.theme;
export const selectTourStep = (s: UiState): number | null => s.tourStep;
export const selectTourDone = (s: UiState): boolean => s.tourDone;
export const selectKeyboardEnabled = (s: UiState): boolean => s.keyboardEnabled;
