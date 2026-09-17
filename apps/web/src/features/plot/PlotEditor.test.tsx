/**
 * PlotEditor, driven by real pointer and keyboard events on the real SVG.
 *
 * The furniture-layer class of bug (CLAUDE.md #4) is a surface that tags itself
 * interactive and never receives an event. Every handle here is exercised the
 * way a hand would — pointerdown/move/up on the vertex circle, a click on the
 * edge-length text, a keydown on a focused corner — and every assertion is
 * made against the FOLDED DOCUMENT in the model store, not against a class
 * name. A drag that changed a `<circle cx>` but never dispatched an op would
 * fail here.
 */

import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { newId, type Op, type Pt } from '@garh/model';

import { SNAP_COARSE_MM, SNAP_FINE_MM, snapMm } from '../../lib/units';
import { useModelStore } from '../../stores/model';
import { boundaryOp, roadOp } from './ops';
import { PlotEditor } from './PlotEditor';
import { makeViewport, toSvg } from './viewport';
import {
  blur,
  byAriaLabel,
  byTestId,
  click,
  commitValue,
  currentDoc,
  focus,
  installPointerEvents,
  installSvgBox,
  keyDown,
  mount,
  pointer,
  seedStore,
  setSvgBox,
  type Mounted,
} from './testHarness';
import { usePlotEditSession } from './usePlot';

const RECT: Pt[] = [
  { x: 0, y: 0 },
  { x: 9144, y: 0 },
  { x: 9144, y: 12192 },
  { x: 0, y: 12192 },
];

const TRAPEZOID: Pt[] = [
  { x: 0, y: 0 },
  { x: 12000, y: 0 },
  { x: 10000, y: 9000 },
  { x: 2000, y: 9000 },
];

const STOREY_ID = newId('storey');
const WALL_OPS: Op[] = [
  { type: 'storey.add', payload: { id: STOREY_ID, index: 0, name: 'Ground', heightMm: 3050 } },
  {
    type: 'wall.add',
    payload: {
      id: newId('wall'),
      storeyId: STOREY_ID,
      a: { x: 1500, y: 1500 },
      b: { x: 7500, y: 1500 },
      thicknessMm: 230,
      kind: 'external',
    },
  },
];

let m: Mounted | null = null;

function mountEditor(boundary: Pt[], extra: Op[] = []): Mounted {
  seedStore([boundaryOp(boundary), ...extra]);
  const vp = makeViewport(boundary);
  // A box exactly the viewBox size: client px == SVG user units.
  setSvgBox(vp.vbW, vp.vbH);
  m = mount(
    <MemoryRouter>
      <PlotEditor complianceHref="/projects/p1/compliance" />
    </MemoryRouter>,
  );
  return m;
}

/** Where a model point sits on screen, given the identity mapping above. */
function clientAt(boundary: Pt[], p: Pt): { clientX: number; clientY: number } {
  const s = toSvg(makeViewport(boundary), p);
  return { clientX: s.x, clientY: s.y };
}

beforeAll(() => {
  installPointerEvents();
  installSvgBox();
});

beforeEach(() => {
  m = null;
});

afterEach(() => {
  m?.unmount();
  m = null;
});

// ---------------------------------------------------------------------------
// Vertex drag
// ---------------------------------------------------------------------------

describe('vertex drag', () => {
  it('moves the corner to the snapped pointer position and dispatches ONE boundary op', () => {
    const { container } = mountEditor(RECT, [roadOp(0, 9000, 'Main Road')]);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-2');
    const target = { x: 10000, y: 13000 };

    pointer(handle, 'pointerdown', clientAt(RECT, RECT[2]!));
    pointer(handle, 'pointermove', clientAt(RECT, target));
    pointer(handle, 'pointerup', clientAt(RECT, target));

    const doc = currentDoc();
    expect(doc.plot.boundary[2]).toEqual({
      x: snapMm(target.x, SNAP_COARSE_MM),
      y: snapMm(target.y, SNAP_COARSE_MM),
    });
    expect(doc.plot.boundary).toHaveLength(4);
    // Roads survive a corner move (the edge count is unchanged).
    expect(doc.plot.roads).toEqual([{ edgeIndex: 0, widthMm: 9000, name: 'Main Road' }]);
    // One undo step for the whole drag, not one per pointermove.
    expect(useModelStoreUndoDepth()).toBe(1);
  });

  it('snaps to the fine module with Shift held', () => {
    const { container } = mountEditor(RECT);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-2');
    const target = { x: 9200, y: 12300 };
    pointer(handle, 'pointerdown', clientAt(RECT, RECT[2]!));
    pointer(handle, 'pointermove', { ...clientAt(RECT, target), shiftKey: true });
    pointer(handle, 'pointerup', { ...clientAt(RECT, target), shiftKey: true });
    expect(currentDoc().plot.boundary[2]).toEqual({
      x: snapMm(target.x, SNAP_FINE_MM),
      y: snapMm(target.y, SNAP_FINE_MM),
    });
  });

  it('NEGATIVE: refuses a drag that makes the ring cross itself, and says so', () => {
    const { container } = mountEditor(RECT);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-1');
    // Corner B dragged far above the top edge: A–B would cross C–D.
    const target = { x: 9144, y: 20000 };
    pointer(handle, 'pointerdown', clientAt(RECT, RECT[1]!));
    pointer(handle, 'pointermove', clientAt(RECT, target));
    pointer(handle, 'pointerup', clientAt(RECT, target));
    expect(currentDoc().plot.boundary).toEqual(RECT);
    const status = container.querySelector('[role="status"]');
    expect(status?.textContent).toMatch(/cross itself/);
    expect(useModelStoreUndoDepth()).toBe(0);
  });

  it('a drag that ends where it started dispatches nothing', () => {
    const { container } = mountEditor(RECT);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-3');
    pointer(handle, 'pointerdown', clientAt(RECT, RECT[3]!));
    pointer(handle, 'pointerup', clientAt(RECT, RECT[3]!));
    expect(useModelStoreUndoDepth()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Keyboard: nudge and delete
// ---------------------------------------------------------------------------

describe('keyboard on a corner', () => {
  it('arrow keys nudge by the coarse module, Shift by the fine one', () => {
    const { container } = mountEditor(RECT);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-1');
    focus(handle);
    keyDown(handle, 'ArrowRight');
    expect(currentDoc().plot.boundary[1]).toEqual({ x: 9144 + SNAP_COARSE_MM, y: 0 });
    keyDown(handle, 'ArrowUp', { shiftKey: true });
    expect(currentDoc().plot.boundary[1]).toEqual({
      x: 9144 + SNAP_COARSE_MM,
      y: SNAP_FINE_MM,
    });
    keyDown(handle, 'ArrowDown', { shiftKey: true });
    keyDown(handle, 'ArrowLeft');
    expect(currentDoc().plot.boundary[1]).toEqual({ x: 9144, y: 0 });
    expect(useModelStoreUndoDepth()).toBe(4);
  });

  it('Delete removes the corner and carries the roads across the renumbering', () => {
    const five: Pt[] = [
      { x: 0, y: 0 },
      { x: 4572, y: 0 },
      { x: 9144, y: 0 },
      { x: 9144, y: 12192 },
      { x: 0, y: 12192 },
    ];
    const { container } = mountEditor(five, [roadOp(1, 6000, 'Narrow'), roadOp(3, 9000, 'Wide')]);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-1');
    focus(handle);
    keyDown(handle, 'Delete');
    const doc = currentDoc();
    expect(doc.plot.boundary).toEqual(RECT);
    // Edges 0 and 1 merged (the road on edge 1 now sits on edge 0); edge 3 → 2.
    expect(doc.plot.roads).toEqual([
      { edgeIndex: 0, widthMm: 6000, name: 'Narrow' },
      { edgeIndex: 2, widthMm: 9000, name: 'Wide' },
    ]);
  });

  it('NEGATIVE: Delete on a triangle is refused with the reason', () => {
    const tri: Pt[] = [
      { x: 0, y: 0 },
      { x: 9000, y: 0 },
      { x: 0, y: 8000 },
    ];
    const { container } = mountEditor(tri);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-0');
    focus(handle);
    keyDown(handle, 'Delete');
    expect(currentDoc().plot.boundary).toEqual(tri);
    expect(container.querySelector('[role="status"]')?.textContent).toMatch(/3 corners/);
  });
});

// ---------------------------------------------------------------------------
// Click-to-type edge length
// ---------------------------------------------------------------------------

describe('edge length, click to type', () => {
  it('on a rectangle: the far side moves too and the notice says so', () => {
    const { container } = mountEditor(RECT);
    click(byTestId(container, 'edge-label-0'));
    const input = byAriaLabel<HTMLInputElement>(container, 'Edge 1 length');
    commitValue(input, "40'", 'enter');
    const doc = currentDoc();
    expect(doc.plot.boundary).toEqual([
      { x: 0, y: 0 },
      { x: 12192, y: 0 },
      { x: 12192, y: 12192 },
      { x: 0, y: 12192 },
    ]);
    const status = container.querySelector('[role="status"]')?.textContent ?? '';
    expect(status).toMatch(/A–B is now 40'-0"/);
    expect(status).toMatch(/far side moved/);
    expect(status).toMatch(/C–D/);
  });

  it('on a skewed ring: only the far corner slides, the changed neighbour is named', () => {
    const { container } = mountEditor(TRAPEZOID);
    // The hint tells the architect up front which semantic this shape gets.
    expect(container.textContent).toMatch(/moves only that edge’s far corner/);
    click(byTestId(container, 'edge-label-0'));
    const input = byAriaLabel<HTMLInputElement>(container, 'Edge 1 length');
    // A bare number is FEET in a ft-in project (the editor's own rule), so say metres.
    commitValue(input, '13m', 'enter');
    const doc = currentDoc();
    expect(doc.plot.boundary).toEqual([
      { x: 0, y: 0 },
      { x: 13000, y: 0 },
      { x: 10000, y: 9000 },
      { x: 2000, y: 9000 },
    ]);
    const status = container.querySelector('[role="status"]')?.textContent ?? '';
    expect(status).toMatch(/Only corner B moved/);
    expect(status).toMatch(/edge B–C changed/);
    expect(status).not.toMatch(/C–D changed/);
  });

  it('NEGATIVE: an unreadable length changes nothing and explains the formats', () => {
    const { container } = mountEditor(RECT);
    click(byTestId(container, 'edge-label-1'));
    const input = byAriaLabel<HTMLInputElement>(container, 'Edge 2 length');
    commitValue(input, 'forty', 'enter');
    expect(currentDoc().plot.boundary).toEqual(RECT);
    expect(container.querySelector('[role="status"]')?.textContent).toMatch(/40', 12.2m or 12200/);
  });

  it('Escape abandons the edit; blur with the same value is a no-op', () => {
    const { container } = mountEditor(RECT);
    click(byTestId(container, 'edge-label-2'));
    const input = byAriaLabel<HTMLInputElement>(container, 'Edge 3 length');
    keyDown(input, 'Escape');
    expect(container.querySelector('[aria-label="Edge 3 length"]')).toBeNull();
    click(byTestId(container, 'edge-label-2'));
    blur(byAriaLabel(container, 'Edge 3 length'));
    expect(currentDoc().plot.boundary).toEqual(RECT);
    expect(useModelStoreUndoDepth()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Add corner
// ---------------------------------------------------------------------------

describe('+ add-corner handle', () => {
  it('inserts the midpoint and keeps the road on both halves', () => {
    const { container } = mountEditor(RECT, [roadOp(0, 9000, 'Main Road'), roadOp(2, 6000)]);
    click(byTestId(container, 'add-corner-0'));
    const doc = currentDoc();
    expect(doc.plot.boundary).toEqual([
      { x: 0, y: 0 },
      { x: 4572, y: 0 },
      { x: 9144, y: 0 },
      { x: 9144, y: 12192 },
      { x: 0, y: 12192 },
    ]);
    expect(doc.plot.roads).toEqual([
      { edgeIndex: 0, widthMm: 9000, name: 'Main Road' },
      { edgeIndex: 1, widthMm: 9000, name: 'Main Road' },
      { edgeIndex: 3, widthMm: 6000, name: null },
    ]);
    // Five handles now, the new one addressable.
    expect(container.querySelectorAll('[data-testid^="vertex-"]')).toHaveLength(5);
  });

  it('is keyboard-operable (Enter)', () => {
    const { container } = mountEditor(RECT);
    keyDown(byTestId(container, 'add-corner-1'), 'Enter');
    expect(currentDoc().plot.boundary).toHaveLength(5);
  });
});

// ---------------------------------------------------------------------------
// The plan banner and the deed dialog
// ---------------------------------------------------------------------------

describe('after a plan exists', () => {
  it('a plot edit raises the non-blocking banner, counts walls left outside, links to compliance, dismisses', () => {
    const { container } = mountEditor(RECT, WALL_OPS);
    expect(container.querySelector('[data-testid="plan-envelope-banner"]')).toBeNull();

    // Shrink the plot so the wall's far end (7500, 1500) falls outside.
    const handle = byTestId<SVGCircleElement>(container, 'vertex-1');
    const target = { x: 5000, y: 0 };
    pointer(handle, 'pointerdown', clientAt(RECT, RECT[1]!));
    pointer(handle, 'pointermove', clientAt(RECT, target));
    pointer(handle, 'pointerup', clientAt(RECT, target));
    // (corner C stays at x = 9144, so the ring is a trapezoid; still simple.)
    expect(currentDoc().plot.boundary[1]?.x).toBe(snapMm(5000, SNAP_COARSE_MM));

    const banner = byTestId(container, 'plan-envelope-banner');
    expect(banner.textContent).toMatch(/changed after a plan was applied/);
    expect(banner.textContent).toMatch(/1 wall ends outside the boundary/);
    const link = banner.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/projects/p1/compliance');

    click(byAriaLabel(banner, 'Dismiss plan warning'));
    expect(container.querySelector('[data-testid="plan-envelope-banner"]')).toBeNull();
    expect(usePlotEditSession.getState().planTouchedAt).toBeNull();
  });

  it('NEGATIVE: no banner when there is no plan to be outside anything', () => {
    const { container } = mountEditor(RECT);
    const handle = byTestId<SVGCircleElement>(container, 'vertex-1');
    focus(handle);
    keyDown(handle, 'ArrowRight');
    expect(container.querySelector('[data-testid="plan-envelope-banner"]')).toBeNull();
  });
});

describe('from the deed', () => {
  it('opens the deed entry in a dialog and replacing the boundary is one undo step', () => {
    const { container } = mountEditor(RECT, [roadOp(0, 9000, 'Main Road')]);
    click(byTestId(container, 'plot-from-deed'));
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    // The sides form defaults to the 30 × 40 with its 50 ft diagonal; retype
    // side A–B to 12 m and commit.
    const side = inputByLabelIn(dialog!, 'Side A–B');
    commitValue(side, '12m', 'enter');
    click(byTestId(dialog!, 'deed-create'));
    const doc = currentDoc();
    expect(doc.plot.boundary[1]).toEqual({ x: 12000, y: 0 });
    expect(doc.plot.boundary).toHaveLength(4);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(useModelStoreUndoDepth()).toBe(1);
  });

  it('the empty state offers the deed tab beside width × depth', () => {
    seedStore([]);
    setSvgBox(100, 100);
    m = mount(
      <MemoryRouter>
        <PlotEditor />
      </MemoryRouter>,
    );
    const { container } = m;
    expect(container.textContent).toMatch(/No plot boundary yet/);
    const deedTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent ?? '').includes('sale deed'),
    );
    expect(deedTab).toBeDefined();
    click(deedTab!);
    expect(container.querySelector('[data-testid="deed-entry"]')).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function useModelStoreUndoDepth(): number {
  return useModelStore.getState().undoStack.length;
}

function inputByLabelIn(scope: Element, text: string): HTMLInputElement {
  const label = Array.from(scope.querySelectorAll('label')).find((l) =>
    (l.textContent ?? '').trim().startsWith(text),
  );
  if (label === undefined) throw new Error(`no label starting with "${text}"`);
  const control = document.getElementById(label.htmlFor) as HTMLInputElement | null;
  if (control === null) throw new Error(`no control for label "${text}"`);
  return control;
}
