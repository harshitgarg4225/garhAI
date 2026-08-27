/**
 * CoPresenceLayer — live cursors and canvas-pinned comments, over the plan.
 *
 * This is the R3F-side half: it holds every hook (context, stores, the canvas
 * core) and hands plain values and plain callbacks across `CanvasDomOverlay`'s
 * root boundary to `CoPresenceOverlayUi`, which does the drawing.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS IS DOM AND NOT A WEBGL LAYER — THE PICKING DECISION
 * ════════════════════════════════════════════════════════════════════════════
 * §12's rule is one canvas, one picker: a clickable canvas layer registers with
 * `PickRegistry` or it is not clickable, and CLAUDE.md's bug pattern 4 is a
 * layer that believed it had registered and never called the registry. So the
 * question "should comment pins be pick targets?" deserves an actual answer.
 *
 * They are NOT, and the pins are DOM instead. Three reasons, in order of weight:
 *
 *  1. **A comment is not an element of the model.** `PickKind` is a closed union
 *     of `wall | opening | room | stair | furniture | facade | balcony | column
 *     | dimension`, and `PickTarget.id` is documented as "the `{type}_{ulid}`
 *     element id. Never an object uuid." A comment id is a server UUID for a row
 *     in a different table. Registering one under a borrowed kind would feed a
 *     comment's uuid to the selection store and the tool layer as if it were a
 *     wall — a wrong answer that typechecks.
 *  2. **Registering is only half a pick.** A resolver returns a target; turning
 *     that target into an action happens in `PlanPage`'s click handling. Adding
 *     a `'comment'` kind means editing `core/constants.ts` (the kind list AND
 *     the priority table Phase 5 shares) and `PlanPage`. A layer that registers
 *     and has no consumer is exactly bug pattern 4 with the paperwork filed.
 *  3. **A pin is chrome, not drawing.** It is a numbered badge with a tooltip
 *     and a link into a side panel. In the DOM it gets real hit-testing, hover,
 *     focus, an accessible name and text that a screen reader can read —
 *     which is the same reasoning `CanvasRoot` gives for keeping its `overlay`
 *     outside the WebGL context.
 *
 * Remote cursors are DOM for an additional reason of their own: their per-user
 * colour is a design-token class pair owned by `PresenceChips`, and the whole
 * point of sharing that function is that a teammate's chip and their cursor
 * cannot be different colours. Re-deriving those tokens as three.js `Color`s
 * would be the second palette that file forbids. Cursors also must never be
 * pick targets at all, and a `pointer-events:none` div in a different DOM
 * subtree is a stronger guarantee than `raycast={() => null}`: these nodes are
 * not in the scene graph the raycaster walks.
 *
 * Positions come from the camera projection (`overlayProjection.ts`), which
 * composes `core/coords.ts`'s conversions and adds no arithmetic of its own.
 */

import { useCallback, useEffect, useMemo } from 'react';
import { useThree } from '@react-three/fiber';

import type { HouseModel, Pt } from '@garh/model';

import { planPins } from '../../comments/anchor';
import { useCommentPinStore } from '../../comments/pinStore';
import { useCollabStore, visibleCursors } from '../../../stores/collab';
import { useProjectStore } from '../../../stores/project';
import { ndcFromPixel, useCanvasCore } from '../core';
import { CanvasDomOverlay } from './CanvasDomOverlay';
import { CoPresenceOverlayUi } from './CoPresenceOverlayUi';
import { projectMmToOverlay, type OverlayPoint } from './overlayProjection';
import { useCursorBroadcast } from './useCursorBroadcast';

/**
 * Slack around the viewport before a mark is dropped, in CSS pixels. A pin is
 * ~24px and a cursor ~18px wide and both are anchored at a tip, so a strict
 * test would blink them out while still visually on the canvas.
 */
const OVERLAY_MARGIN_PX = 48;

/**
 * Storey ID → the INDEX the cursor wire protocol speaks (`CursorIn.storeyIndex`).
 *
 * The model identifies a storey by id; the collab frame identifies it by index.
 * This is the only place the two meet, and it is a lookup in the document
 * rather than an assumption about ordering. A storey that has since been
 * deleted (`findIndex` → −1) becomes `null` — "not storey-bound", which renders
 * everywhere — rather than being published as storey −1, which nothing would
 * ever match and which would make the cursor silently vanish.
 */
export function storeyIndexOf(
  storeys: readonly { readonly id: string }[],
  storeyId: string | null,
): number | null {
  if (storeyId === null) return null;
  const index = storeys.findIndex((storey) => storey.id === storeyId);
  return index < 0 ? null : index;
}

export interface CoPresenceLayerProps {
  readonly house: HouseModel;
  /** The storey being drawn. Pins and cursors are filtered to it. */
  readonly storeyId: string | null;
  /** FFL of that storey — the plane marks are projected from. */
  readonly elevationMm: number;
}

export function CoPresenceLayer({
  house,
  storeyId,
  elevationMm,
}: CoPresenceLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const canvasEl = useThree((state) => state.gl.domElement);
  const projectId = useProjectStore((s) => s.current?.id ?? '');

  const cursorMap = useCollabStore((s) => s.cursors);
  const comments = useCommentPinStore((s) => s.comments);
  const placement = useCommentPinStore((s) => s.placement);
  const showResolvedPins = useCommentPinStore((s) => s.showResolvedPins);
  const dispatchPlacement = useCommentPinStore((s) => s.dispatchPlacement);
  const focusComment = useCommentPinStore((s) => s.focusComment);

  const storeyIndex = useMemo(
    () => storeyIndexOf(house.storeys, storeyId),
    [house.storeys, storeyId],
  );

  // ── send: my pointer ─────────────────────────────────────────────────────
  const toMm = useCallback(
    (clientX: number, clientY: number): Pt | null => {
      const rect = canvasEl.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return null;
      const ndc = ndcFromPixel(
        { x: clientX - rect.left, y: clientY - rect.top },
        { width: rect.width, height: rect.height },
      );
      // UNSNAPPED. `core.pointMm` would round a cursor onto the 115mm grid,
      // which at a working zoom is a visible several-pixel stutter as somebody
      // else's pointer hops between modules. A cursor is a position, never an
      // op payload, so it has no reason to obey the snap module.
      return core.rawPointMm(ndc);
    },
    [core, canvasEl],
  );

  useCursorBroadcast({
    projectId,
    element: canvasEl,
    storeyIndex,
    toMm,
    // Nobody to broadcast to before the project is open. The stream's own
    // presence roster is not consulted: a colleague can join a moment after you
    // move, and a cursor that only starts working on the second wave of moves
    // would look broken.
    enabled: projectId !== '',
  });

  // ── receive: their pointers ──────────────────────────────────────────────
  //
  // `now` advances whenever the store changes, which is the only time a cursor
  // can appear. Expiry is driven by the sweeper in `startProjectCollab`, which
  // replaces the map when something ages out; the `visibleCursors` TTL check
  // here is the belt to that braces — a backgrounded tab throttles the sweeper
  // and the honest read is "true at the moment of drawing".
  const cursors = useMemo(
    () => visibleCursors(cursorMap, storeyIndex, Date.now()),
    [cursorMap, storeyIndex],
  );

  const pins = useMemo(
    () => planPins(comments, { storeyId, includeResolved: showResolvedPins }),
    [comments, storeyId, showResolvedPins],
  );

  // ── projection, shared by both mark kinds ────────────────────────────────
  const projectMm = useCallback(
    (xMm: number, yMm: number): OverlayPoint => {
      const camera = core.camera;
      if (camera === null) return { x: 0, y: 0, onScreen: false };
      return projectMmToOverlay(
        { x: xMm, y: yMm },
        elevationMm,
        camera,
        core.viewport.sizePx,
        OVERLAY_MARGIN_PX,
      );
    },
    [core, elevationMm],
  );

  // rAF-coalesced, not the raw commit stream: a drag commits several times
  // between two frames and the overlay only needs to be right once per frame.
  const subscribeViewport = useCallback(
    (listener: () => void) => core.viewport.subscribeAnimationFrame(listener),
    [core],
  );

  // ── placement mode ───────────────────────────────────────────────────────
  const onPlacementClick = useCallback(
    (px: number, py: number) => {
      const ndc = ndcFromPixel({ x: px, y: py }, core.viewport.sizePx);
      const ptMm = core.rawPointMm(ndc);
      // A click that missed the reference plane pins nothing and, crucially,
      // leaves the mode ARMED — so the next click can still land. Silently
      // dropping out of placement here would read as the click having worked.
      if (ptMm === null) return;
      dispatchPlacement({ type: 'canvasClick', ptMm, storeyId });
    },
    [core, dispatchPlacement, storeyId],
  );

  const cancelPlacement = useCallback(() => {
    dispatchPlacement({ type: 'cancel' });
  }, [dispatchPlacement]);

  // Escape, from the canvas side. The panel arms this mode and carries its own
  // Escape handler for the case where no canvas is mounted; both dispatch the
  // same idempotent `cancel`, so having two is a redundancy, not a conflict.
  useEffect(() => {
    if (placement.phase === 'idle') return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') cancelPlacement();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [placement.phase, cancelPlacement]);

  const onPinClick = useCallback(
    (commentId: string) => {
      // Focusing also forces the panel open (see `pinStore.focusComment`) —
      // highlighting a row in a panel nobody can see is a click that does
      // nothing.
      focusComment(commentId);
    },
    [focusComment],
  );

  // Nothing to draw and no mode to announce: mount no overlay at all rather
  // than an empty div and a second React root per plan view.
  const idle = cursors.length === 0 && pins.length === 0 && placement.phase !== 'armed';
  if (idle) return null;

  return (
    <CanvasDomOverlay>
      <CoPresenceOverlayUi
        cursors={cursors}
        pins={pins}
        placement={placement}
        subscribeViewport={subscribeViewport}
        projectMm={projectMm}
        onPlacementClick={onPlacementClick}
        onCancelPlacement={cancelPlacement}
        onPinClick={onPinClick}
      />
    </CanvasDomOverlay>
  );
}
