/**
 * The Phase-4 additions to the two stores the canvas writes.
 *
 * `selection` gained pick RESULTS (kind, storey, model point) and `ui` gained
 * the canvas slice (layer visibility, the mirrored zoom, the focus channel).
 * Both are small, and both are the kind of small that goes wrong quietly:
 *
 *   - a kind map that is not pruned with the ids it describes leaks one entry
 *     per element ever clicked, and then reports a stale kind when an id is
 *     reused after an undo;
 *   - a `setCanvasZoom` that writes on every call re-renders every subscriber
 *     once per animation frame during a pan, which is the §14 budget gone.
 *
 * Neither failure shows up in a screenshot, so they are asserted here.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { useSelectionStore } from './selection';
import { snapStepMm, useUiStore } from './ui';

const WALL = 'wall_01HZY0000000000000000000';
const ROOM = 'room_01HZY0000000000000000001';
const OPENING = 'opening_01HZY0000000000000000002';

beforeEach(() => {
  useSelectionStore.setState({
    ids: [],
    kinds: {},
    hoverId: null,
    hover: null,
    lastPointMm: null,
    marquee: null,
  });
  useUiStore.setState({
    canvasLayers: { grid: true, dimensions: true, roomTags: true, furniture: true, compliance: true },
    mmPerPx: 10,
    scaleLabel: '',
    canvasFocus: null,
  });
});

describe('selection: canvas hit results', () => {
  it('records the kind a pick resolved to, which the id alone cannot give', () => {
    const s = useSelectionStore.getState();
    s.selectHit({ kind: 'wall', id: WALL, storeyId: 'storey_1', pointMm: { x: 1150, y: 2300 } });

    const state = useSelectionStore.getState();
    expect(state.ids).toEqual([WALL]);
    expect(state.kinds[WALL]).toBe('wall');
    // The click point is kept for context menus and "insert here" actions.
    expect(state.lastPointMm).toEqual({ x: 1150, y: 2300 });
  });

  it('treats a click on empty paper as a deselect, but only in replace mode', () => {
    const s = useSelectionStore.getState();
    s.selectHit({ kind: 'wall', id: WALL, storeyId: null, pointMm: null });
    s.selectHit({ kind: 'room', id: null, storeyId: null, pointMm: { x: 0, y: 0 } }, 'add');
    expect(useSelectionStore.getState().ids, 'shift-clicking nothing must not clear').toEqual([
      WALL,
    ]);

    s.selectHit({ kind: 'room', id: null, storeyId: null, pointMm: { x: 0, y: 0 } });
    expect(useSelectionStore.getState().ids).toEqual([]);
  });

  it('adds and toggles without losing the recorded kinds', () => {
    const s = useSelectionStore.getState();
    s.selectHit({ kind: 'wall', id: WALL, storeyId: null, pointMm: null });
    s.selectHit({ kind: 'opening', id: OPENING, storeyId: null, pointMm: null }, 'add');
    expect(useSelectionStore.getState().kinds).toEqual({ [WALL]: 'wall', [OPENING]: 'opening' });

    s.selectHit({ kind: 'opening', id: OPENING, storeyId: null, pointMm: null }, 'toggle');
    const after = useSelectionStore.getState();
    expect(after.ids).toEqual([WALL]);
    // Toggled OUT, so its kind goes with it — otherwise the map grows forever.
    expect(after.kinds).toEqual({ [WALL]: 'wall' });
  });

  it('prunes kinds along with the ids they describe', () => {
    const s = useSelectionStore.getState();
    s.selectHit({ kind: 'wall', id: WALL, storeyId: null, pointMm: null });
    s.selectHit({ kind: 'room', id: ROOM, storeyId: null, pointMm: null }, 'add');

    // The wall was deleted by an undo; the model store prunes after every doc
    // change.
    s.prune(new Set([ROOM]));

    const after = useSelectionStore.getState();
    expect(after.ids).toEqual([ROOM]);
    expect(after.kinds).toEqual({ [ROOM]: 'room' });
  });

  it('keeps the hovered element out of the kind map once the hover moves on', () => {
    const s = useSelectionStore.getState();
    s.setHoverHit({ kind: 'wall', id: WALL, storeyId: null, pointMm: null });
    expect(useSelectionStore.getState().kinds[WALL]).toBe('wall');
    expect(useSelectionStore.getState().hoverId).toBe(WALL);

    s.setHoverHit(null);
    const after = useSelectionStore.getState();
    expect(after.hoverId).toBeNull();
    expect(after.hover).toBeNull();
    expect(after.kinds, 'nothing selected and nothing hovered means nothing to remember').toEqual(
      {},
    );
  });

  it('does not write when the hover has not actually changed', () => {
    const s = useSelectionStore.getState();
    s.setHoverHit({ kind: 'wall', id: WALL, storeyId: null, pointMm: null });

    let notifications = 0;
    const unsubscribe = useSelectionStore.subscribe(() => {
      notifications += 1;
    });
    s.setHoverHit({ kind: 'wall', id: WALL, storeyId: null, pointMm: { x: 5, y: 5 } });
    unsubscribe();

    // Hover fires once per change from `CanvasRoot`, but a re-render or a
    // storey switch can re-offer the same target. Writing anyway would notify
    // every subscriber for nothing.
    expect(notifications).toBe(0);
  });
});

describe('ui: the canvas slice', () => {
  it('starts with every drawing layer on', () => {
    expect(useUiStore.getState().canvasLayers).toEqual({
      grid: true,
      dimensions: true,
      roomTags: true,
      furniture: true,
      compliance: true,
    });
  });

  it('toggles one layer without disturbing the others', () => {
    useUiStore.getState().toggleCanvasLayer('dimensions');
    const layers = useUiStore.getState().canvasLayers;
    expect(layers.dimensions).toBe(false);
    expect(layers.grid).toBe(true);
  });

  it('does not notify when a layer is set to the value it already has', () => {
    let notifications = 0;
    const unsubscribe = useUiStore.subscribe(() => {
      notifications += 1;
    });
    useUiStore.getState().setCanvasLayer('grid', true);
    unsubscribe();
    expect(notifications).toBe(0);
  });

  it('mirrors the zoom only when it actually moved — the §14 guard', () => {
    const { setCanvasZoom } = useUiStore.getState();
    setCanvasZoom(12, '1:100');
    expect(useUiStore.getState().scaleLabel).toBe('1:100');

    let notifications = 0;
    const unsubscribe = useUiStore.subscribe(() => {
      notifications += 1;
    });
    // What a stationary camera does on every animation frame.
    setCanvasZoom(12, '1:100');
    setCanvasZoom(12, '1:100');
    unsubscribe();
    expect(
      notifications,
      'a pan that does not change the scale must not re-render every subscriber',
    ).toBe(0);
  });

  it('carries a focus request with a timestamp, so the same chip can be clicked twice', () => {
    const { requestCanvasFocus } = useUiStore.getState();
    requestCanvasFocus([ROOM], 'nbc.room.habitable.area.min');
    const first = useUiStore.getState().canvasFocus;
    expect(first?.elementIds).toEqual([ROOM]);
    expect(first?.key).toBe('nbc.room.habitable.area.min');

    useUiStore.getState().clearCanvasFocus();
    expect(useUiStore.getState().canvasFocus).toBeNull();

    requestCanvasFocus([ROOM], 'nbc.room.habitable.area.min');
    expect(
      useUiStore.getState().canvasFocus,
      'a cleared request must be re-raisable, or the second click does nothing',
    ).not.toBeNull();
  });

  it('keeps the snap step honest for all three modes', () => {
    expect(snapStepMm('module')).toBe(115);
    expect(snapStepMm('fine')).toBe(25);
    expect(snapStepMm('off'), '0 means "no rounding", not "round to zero"').toBe(0);
  });
});
