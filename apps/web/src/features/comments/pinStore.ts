/**
 * pinStore.ts — the one piece of state the comments PANEL and the comments
 * PIN LAYER both read.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY A STORE AND NOT PROPS
 * ────────────────────────────────────────────────────────────────────────────
 * The two halves of canvas-pinned comments sit on opposite sides of the app's
 * biggest boundary. `CommentsPanel` is docked in `ProjectShell`, outside the
 * `<Canvas>`; the pin layer is mounted from `PlanScene`, *inside* it — and R3F
 * reconciles its children with a separate React root, so React context does not
 * cross that line (see `core/context.ts`). There is no common ancestor to hold
 * this in, and threading it through `CanvasRoot` would make a generic canvas
 * component know about comments.
 *
 * A module-scoped store is the seam that already exists for exactly this shape
 * of problem everywhere else in `stores/`. It holds three things:
 *
 *  1. A MIRROR of the thread. `useComments` owns the fetch, the optimism and
 *     the error handling; it publishes its list here so the pin layer can read
 *     it without opening a second subscription to the same endpoint. One
 *     fetcher, two readers — the same argument the hook's own docblock makes
 *     for living in the shell rather than in the panel.
 *  2. The PLACEMENT state machine (below).
 *  3. Cross-surface UI intent: which thread the canvas asked the panel to show.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE PLACEMENT MACHINE, AND WHY IT IS A PURE REDUCER
 * ────────────────────────────────────────────────────────────────────────────
 * "Pin a comment" is a three-actor interaction: a button in the panel arms it,
 * a click on the canvas fixes the point, and the composer's submit consumes it.
 * Escape can end it at any moment, and — the case that actually bites — so can
 * a failed POST, which must NOT leave the mode armed and the anchor half-used.
 *
 * Written as `if` statements spread across three components that would be four
 * bugs. Written as a reducer with an explicit state union it is a table you can
 * read, and {@link reducePinPlacement} is exported so the table can be tested
 * without a canvas, a panel or a network: enter → click → submit → idle, and
 * Escape → idle with nothing created.
 */

import { create } from 'zustand';

import type { Pt } from '@garh/model';

import type { Comment } from '../../lib/schemas';
import { planAnchorPayload } from './anchor';

// ---------------------------------------------------------------------------
// The placement state machine
// ---------------------------------------------------------------------------

export type PinPlacement =
  /** Nothing in flight. The canvas behaves normally. */
  | { readonly phase: 'idle' }
  /** Armed: the hint banner is up and the next canvas click captures a point. */
  | { readonly phase: 'armed' }
  /**
   * A point is captured and the composer is holding it. The canvas is back to
   * normal — you can pan, zoom and read the plan while you type the comment.
   */
  | { readonly phase: 'placed'; readonly ptMm: Pt; readonly storeyId: string | null };

export type PinPlacementEvent =
  | { readonly type: 'arm' }
  | { readonly type: 'canvasClick'; readonly ptMm: Pt; readonly storeyId: string | null }
  /** The composer posted the comment (successfully). */
  | { readonly type: 'submitted' }
  /** Escape, the panel closing, the composer clearing, or a failed post. */
  | { readonly type: 'cancel' };

export const IDLE_PLACEMENT: PinPlacement = { phase: 'idle' };

/**
 * The whole transition table. Every unlisted pair is a deliberate no-op, and
 * the two that matter most are:
 *
 *  · `idle` + `canvasClick` → `idle`. Ordinary drawing clicks stream through
 *    this reducer whenever the layer is mounted; treating one as a placement
 *    would drop a pin every time somebody drew a wall.
 *  · `placed` + `arm` → `armed`. Pressing "Pin a comment" again with a point
 *    already captured means "no, somewhere else" — it re-arms rather than
 *    keeping the stale point, which is what a second press visibly implies.
 */
export function reducePinPlacement(state: PinPlacement, event: PinPlacementEvent): PinPlacement {
  switch (event.type) {
    case 'arm':
      return { phase: 'armed' };
    case 'canvasClick':
      if (state.phase !== 'armed') return state;
      return { phase: 'placed', ptMm: event.ptMm, storeyId: event.storeyId };
    case 'submitted':
    case 'cancel':
      return state.phase === 'idle' ? state : IDLE_PLACEMENT;
    default:
      return state;
  }
}

/**
 * The `anchor` body for the captured point, or `null` when nothing is captured.
 *
 * Null is the signal `useComments.add` uses to decide whether this is a pinned
 * comment or an ordinary one, so the composer needs no second flag and cannot
 * disagree with the machine about which it is sending.
 */
export function placementAnchor(state: PinPlacement): Record<string, unknown> | null {
  if (state.phase !== 'placed') return null;
  return planAnchorPayload(state.ptMm, state.storeyId);
}

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------

export interface CommentPinState {
  /** Mirror of `useComments.comments`, newest first. Never written by the canvas. */
  comments: readonly Comment[];
  placement: PinPlacement;
  /**
   * The thread the canvas asked the panel to reveal. Cleared by the panel once
   * it has scrolled to it, so the same pin can be clicked twice.
   */
  focusedCommentId: string | null;
  /**
   * True when a canvas pin needs the panel open and the shell has it closed.
   *
   * The shell owns `commentsOpen` and this feature may not reach into it, so
   * the panel treats `open || panelForcedOpen` as its real visibility and
   * clears the flag on close. That keeps "click a pin, read the thread" working
   * from a closed panel without a prop drilled through a component this feature
   * does not own.
   */
  panelForcedOpen: boolean;
  /** Draw pins for resolved comments. Off by default; the panel toggles it. */
  showResolvedPins: boolean;

  setComments: (comments: readonly Comment[]) => void;
  dispatchPlacement: (event: PinPlacementEvent) => void;
  focusComment: (commentId: string | null) => void;
  closePanel: () => void;
  setShowResolvedPins: (show: boolean) => void;
  reset: () => void;
}

const INITIAL = {
  comments: [] as readonly Comment[],
  placement: IDLE_PLACEMENT,
  focusedCommentId: null,
  panelForcedOpen: false,
  showResolvedPins: false,
};

export const useCommentPinStore = create<CommentPinState>()((set) => ({
  ...INITIAL,

  setComments: (comments) => set({ comments }),
  dispatchPlacement: (event) =>
    set((s) => {
      const placement = reducePinPlacement(s.placement, event);
      return placement === s.placement ? s : { placement };
    }),
  // Focusing a thread also opens the panel: a pin click that highlighted a row
  // in a panel nobody can see would be a feature that silently does nothing.
  focusComment: (commentId) =>
    set(
      commentId === null
        ? { focusedCommentId: null }
        : { focusedCommentId: commentId, panelForcedOpen: true },
    ),
  closePanel: () => set({ panelForcedOpen: false, focusedCommentId: null }),
  setShowResolvedPins: (show) => set({ showResolvedPins: show }),
  // Called on project change. The comments belong to one project and a stale
  // pin drawn over a different plan is worse than no pin.
  reset: () => set({ ...INITIAL }),
}));

export const selectPinComments = (s: CommentPinState): readonly Comment[] => s.comments;
export const selectPinPlacement = (s: CommentPinState): PinPlacement => s.placement;
