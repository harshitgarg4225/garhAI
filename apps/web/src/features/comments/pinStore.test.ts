/**
 * The "pin a comment" state machine.
 *
 * Three actors drive this: a button in the comments panel, a click on the plan
 * canvas, and the composer's submit. They live in two different React roots and
 * cannot see each other, so the machine is the contract between them — and the
 * two transitions worth being paranoid about are the ones that do NOTHING:
 *
 *  · an ordinary drawing click while idle must not drop a pin, and
 *  · Escape must end the mode with no comment created.
 *
 * Both are tested by their absence of effect, which is the only way to test a
 * gate that is supposed to stay shut.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { readPlanAnchor } from './anchor';
import {
  IDLE_PLACEMENT,
  placementAnchor,
  reducePinPlacement,
  useCommentPinStore,
  type PinPlacement,
} from './pinStore';

const PT = { x: 4200, y: 1150 };
const STOREY = 'storey_ground';

describe('reducePinPlacement', () => {
  it('walks the happy path: arm → click → submit → idle', () => {
    let state: PinPlacement = IDLE_PLACEMENT;

    state = reducePinPlacement(state, { type: 'arm' });
    expect(state.phase).toBe('armed');

    state = reducePinPlacement(state, { type: 'canvasClick', ptMm: PT, storeyId: STOREY });
    expect(state).toEqual({ phase: 'placed', ptMm: PT, storeyId: STOREY });

    state = reducePinPlacement(state, { type: 'submitted' });
    expect(state).toBe(IDLE_PLACEMENT);
  });

  it('Escape from ARMED exits without capturing a point', () => {
    let state: PinPlacement = reducePinPlacement(IDLE_PLACEMENT, { type: 'arm' });
    state = reducePinPlacement(state, { type: 'cancel' });
    expect(state.phase).toBe('idle');
    expect(placementAnchor(state)).toBeNull();
  });

  it('Escape from PLACED discards the captured point', () => {
    let state: PinPlacement = reducePinPlacement(IDLE_PLACEMENT, { type: 'arm' });
    state = reducePinPlacement(state, { type: 'canvasClick', ptMm: PT, storeyId: STOREY });
    expect(placementAnchor(state)).not.toBeNull();

    state = reducePinPlacement(state, { type: 'cancel' });
    // Nothing left for the composer to attach: the next comment is a plain one.
    expect(placementAnchor(state)).toBeNull();
  });

  it('IGNORES a canvas click while idle', () => {
    // Every ordinary drawing click reaches this reducer whenever the layer is
    // mounted. Treating one as a placement would drop a pin every time somebody
    // drew a wall.
    const state = reducePinPlacement(IDLE_PLACEMENT, {
      type: 'canvasClick',
      ptMm: PT,
      storeyId: STOREY,
    });
    expect(state).toBe(IDLE_PLACEMENT);
    expect(placementAnchor(state)).toBeNull();
  });

  it('a second click while PLACED does not move the point', () => {
    let state: PinPlacement = reducePinPlacement(IDLE_PLACEMENT, { type: 'arm' });
    state = reducePinPlacement(state, { type: 'canvasClick', ptMm: PT, storeyId: STOREY });
    const moved = reducePinPlacement(state, {
      type: 'canvasClick',
      ptMm: { x: 0, y: 0 },
      storeyId: STOREY,
    });
    // You are typing the comment by now; the canvas is back to normal and a
    // click there is a click on the drawing, not a relocation.
    expect(moved).toBe(state);
  });

  it('re-arming from PLACED means "somewhere else", not "keep the old point"', () => {
    let state: PinPlacement = reducePinPlacement(IDLE_PLACEMENT, { type: 'arm' });
    state = reducePinPlacement(state, { type: 'canvasClick', ptMm: PT, storeyId: STOREY });
    state = reducePinPlacement(state, { type: 'arm' });
    expect(state.phase).toBe('armed');
    expect(placementAnchor(state)).toBeNull();
  });

  it('cancel while already idle returns the identical state', () => {
    // Identity matters: the store skips the `set` when the reducer returns the
    // same object, so a redundant Escape (the panel and the canvas both listen)
    // costs no re-render.
    expect(reducePinPlacement(IDLE_PLACEMENT, { type: 'cancel' })).toBe(IDLE_PLACEMENT);
    expect(reducePinPlacement(IDLE_PLACEMENT, { type: 'submitted' })).toBe(IDLE_PLACEMENT);
  });
});

describe('placementAnchor', () => {
  it('produces an anchor the pin reader can read back', () => {
    let state: PinPlacement = reducePinPlacement(IDLE_PLACEMENT, { type: 'arm' });
    state = reducePinPlacement(state, { type: 'canvasClick', ptMm: PT, storeyId: STOREY });

    const anchor = placementAnchor(state);
    expect(anchor).not.toBeNull();
    expect(readPlanAnchor(anchor ?? {})).toEqual({
      kind: 'plan',
      storeyId: STOREY,
      x: PT.x,
      y: PT.y,
    });
  });

  it('is null in every phase but PLACED — the composer needs no second flag', () => {
    expect(placementAnchor(IDLE_PLACEMENT)).toBeNull();
    expect(placementAnchor({ phase: 'armed' })).toBeNull();
  });
});

describe('useCommentPinStore', () => {
  beforeEach(() => {
    useCommentPinStore.getState().reset();
  });

  it('runs the machine through the store', () => {
    const s = useCommentPinStore.getState();
    s.dispatchPlacement({ type: 'arm' });
    expect(useCommentPinStore.getState().placement.phase).toBe('armed');

    s.dispatchPlacement({ type: 'canvasClick', ptMm: PT, storeyId: STOREY });
    expect(useCommentPinStore.getState().placement.phase).toBe('placed');

    s.dispatchPlacement({ type: 'cancel' });
    expect(useCommentPinStore.getState().placement.phase).toBe('idle');
  });

  it('focusing a thread forces the panel open', () => {
    // A pin click that highlighted a row in a closed panel would be a click
    // that visibly does nothing.
    useCommentPinStore.getState().focusComment('c1');
    expect(useCommentPinStore.getState().focusedCommentId).toBe('c1');
    expect(useCommentPinStore.getState().panelForcedOpen).toBe(true);
  });

  it('clearing focus leaves the panel where it is', () => {
    useCommentPinStore.getState().focusComment('c1');
    useCommentPinStore.getState().focusComment(null);
    expect(useCommentPinStore.getState().focusedCommentId).toBeNull();
    // The highlight fading must not close a panel the person is now reading.
    expect(useCommentPinStore.getState().panelForcedOpen).toBe(true);
  });

  it('closing the panel drops the force flag and the focus', () => {
    useCommentPinStore.getState().focusComment('c1');
    useCommentPinStore.getState().closePanel();
    expect(useCommentPinStore.getState().panelForcedOpen).toBe(false);
    expect(useCommentPinStore.getState().focusedCommentId).toBeNull();
  });

  it('reset clears the mirror, the mode and the UI intent', () => {
    const s = useCommentPinStore.getState();
    s.dispatchPlacement({ type: 'arm' });
    s.focusComment('c1');
    s.setShowResolvedPins(true);

    s.reset();
    const after = useCommentPinStore.getState();
    expect(after.placement.phase).toBe('idle');
    expect(after.focusedCommentId).toBeNull();
    expect(after.panelForcedOpen).toBe(false);
    expect(after.showResolvedPins).toBe(false);
    expect(after.comments).toEqual([]);
  });
});
