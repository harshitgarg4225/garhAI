/**
 * DeedEntry through the DOM: the two forms, their live previews, their
 * refusals, and the op group each commits. The maths is pinned in
 * `deed.test.ts`; this spec pins that the FORM reaches it and the store.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { polygonAreaMm2 } from '@garh/model';

import { useModelStore } from '../../stores/model';
import { DeedEntry } from './DeedEntry';
import { boundaryOp, roadOp } from './ops';
import {
  byAriaLabel,
  byTestId,
  click,
  commitValue,
  currentDoc,
  inputByLabel,
  mount,
  seedStore,
  setValue,
  type Mounted,
} from './testHarness';

let m: Mounted | null = null;

afterEach(() => {
  m?.unmount();
  m = null;
});

describe('sides + diagonal', () => {
  it('previews the 30 × 40 default with both diagonals and creates it as one op group', () => {
    seedStore([]);
    m = mount(<DeedEntry />);
    const { container } = m;
    const preview = byTestId(container, 'deed-preview').textContent ?? '';
    expect(preview).toMatch(/1,200\.0 sq ft · 133 gaj/);
    expect(preview).toMatch(/140'-0" around/);
    expect(preview).toMatch(/A–C 50'-0"/);
    expect(preview).toMatch(/B–D 50'-0"/);
    click(byTestId(container, 'deed-create'));
    const doc = currentDoc();
    expect(doc.plot.boundary).toEqual([
      { x: 0, y: 0 },
      { x: 9144, y: 0 },
      { x: 9144, y: 12192 },
      { x: 0, y: 12192 },
    ]);
    expect(useModelStore.getState().undoStack).toHaveLength(1);
  });

  it('a skewed deed quadrilateral lands within a millimetre per side, and the check diagonal is compared', () => {
    seedStore([]);
    m = mount(<DeedEntry />);
    const { container } = m;
    commitValue(inputByLabel(container, 'Side A–B'), '12m');
    commitValue(inputByLabel(container, 'Side B–C'), '9.5m');
    commitValue(inputByLabel(container, 'Side C–D'), '11.2m');
    commitValue(inputByLabel(container, 'Side D–A'), '9m');
    commitValue(inputByLabel(container, 'Diagonal A–C'), '15.1m');
    expect(container.querySelector('[data-testid="deed-preview"]')).not.toBeNull();

    // The deed's other diagonal, typed as a check: these figures give B–D ≈ 13.0 m.
    commitValue(inputByLabel(container, 'Check: diagonal B–D'), '14m');
    const check = container.querySelector('[title="deed-check-diagonal"]')?.textContent ?? '';
    // A ft-in project shows 14 m as 45'-11"; the computed B–D is ≈ 13.0 m (42'-8").
    // These figures give B–D ≈ 14.55 m (47'-9"), 554 mm MORE than the deed's 14 m.
    expect(check).toMatch(/Deed says B–D 45'-11"; these figures give 47'-9" \(\+1'-10"\)/);
    expect(check).toMatch(/One of the deed figures is off/);

    click(byTestId(container, 'deed-create'));
    const ring = currentDoc().plot.boundary;
    expect(ring).toHaveLength(4);
    for (const p of ring) {
      expect(Number.isSafeInteger(p.x) && Number.isSafeInteger(p.y)).toBe(true);
    }
    expect(
      Math.abs(Math.hypot(ring[1]!.x - ring[0]!.x, ring[1]!.y - ring[0]!.y) - 12000),
    ).toBeLessThanOrEqual(1);
  });

  it('NEGATIVE: inconsistent figures show the triangle that cannot close and disable Create', () => {
    seedStore([
      boundaryOp([
        { x: 0, y: 0 },
        { x: 9144, y: 0 },
        { x: 9144, y: 12192 },
        { x: 0, y: 12192 },
      ]),
    ]);
    m = mount(<DeedEntry />);
    const { container } = m;
    commitValue(inputByLabel(container, 'Side A–B'), '3m');
    commitValue(inputByLabel(container, 'Side B–C'), '4m');
    commitValue(inputByLabel(container, 'Diagonal A–C'), '10m');
    const error = byTestId(container, 'deed-preview-error').textContent ?? '';
    expect(error).toMatch(/triangle A–B–C/);
    expect(byTestId<HTMLButtonElement>(container, 'deed-create').disabled).toBe(true);
    // Clicking a disabled button changes nothing.
    click(byTestId(container, 'deed-create'));
    expect(currentDoc().plot.boundary[1]).toEqual({ x: 9144, y: 0 });
  });

  it('changing the corner count re-shapes the form (a triangle needs no diagonal)', () => {
    seedStore([]);
    m = mount(<DeedEntry />);
    const { container } = m;
    const select = container.querySelector('select')!;
    setValue(select, '3');
    expect(container.querySelector('label')).not.toBeNull();
    expect(
      Array.from(container.querySelectorAll('label')).some((l) =>
        (l.textContent ?? '').includes('Diagonal'),
      ),
    ).toBe(false);
    expect(
      Array.from(container.querySelectorAll('label')).some((l) =>
        (l.textContent ?? '').includes('Side C–A'),
      ),
    ).toBe(true);
  });
});

describe('bearings', () => {
  function switchToBearings(container: Element): void {
    const tab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent ?? '').includes('Bearings'),
    )!;
    click(tab);
  }

  it('walks the default square, reports an exact closure, and sets north with the ring in ONE undo step', () => {
    seedStore([roadOp(0, 9000)].slice(0, 0));
    // Start from a rotated plot so the north reset is observable.
    seedStore([
      boundaryOp([
        { x: 0, y: 0 },
        { x: 5000, y: 0 },
        { x: 5000, y: 5000 },
        { x: 0, y: 5000 },
      ]),
      { type: 'plot.set_north', payload: { deg: 37 } },
    ]);
    m = mount(<DeedEntry />);
    const { container } = m;
    switchToBearings(container);
    expect(byTestId(container, 'deed-closure').textContent).toMatch(/Closes exactly/);
    click(byTestId(container, 'deed-create'));
    const doc = currentDoc();
    expect(doc.plot.boundary).toEqual([
      { x: 0, y: 0 },
      { x: 9144, y: 0 },
      { x: 9144, y: 12192 },
      { x: 0, y: 12192 },
    ]);
    expect(doc.plot.northDeg).toBe(0);
    expect(useModelStore.getState().undoStack).toHaveLength(1);
    // One undo brings back both the old ring and the old north.
    useModelStore.getState().undo();
    expect(currentDoc().plot.northDeg).toBe(37);
    expect(polygonAreaMm2(currentDoc().plot.boundary)).toBe(25_000_000);
  });

  it('reports a misclosure as 1 in N and distributes it; a DMS bearing parses', () => {
    seedStore([]);
    m = mount(<DeedEntry />);
    const { container } = m;
    switchToBearings(container);
    // Last leg 100 mm short → 1 in 425 on the 30 × 40 perimeter. (A bare number
    // is feet in a ft-in project, so say mm.)
    commitValue(inputByLabel(container, 'Length of leg 4'), '12092mm');
    const closure = byTestId(container, 'deed-closure').textContent ?? '';
    expect(closure).toMatch(/Misclosure 100 mm — 1 in 42\d/);
    expect(closure).toMatch(/compass rule/);
    // A field-book bearing.
    const bearing = byAriaLabel<HTMLInputElement>(container, 'Bearing of leg 1');
    setValue(bearing, "90°00'");
    expect(container.textContent).toMatch(/90°00'/);
    click(byTestId(container, 'deed-create'));
    expect(currentDoc().plot.boundary).toHaveLength(4);
  });

  it('NEGATIVE: a gross misclosure is refused with the leg count and precision, and Create stays disabled', () => {
    seedStore([]);
    m = mount(<DeedEntry />);
    const { container } = m;
    switchToBearings(container);
    commitValue(inputByLabel(container, 'Length of leg 4'), '6000mm');
    const error = byTestId(container, 'deed-preview-error').textContent ?? '';
    expect(error).toMatch(/misses its starting corner by 6192 mm/);
    expect(error).toMatch(/1 in \d+/);
    expect(byTestId<HTMLButtonElement>(container, 'deed-create').disabled).toBe(true);
    // Not a bearing: flagged inline, and the preview waits.
    setValue(byAriaLabel<HTMLInputElement>(container, 'Bearing of leg 2'), 'northish');
    expect(container.textContent).toMatch(/not a bearing/);
    expect(container.querySelector('[data-testid="deed-preview-pending"]')).not.toBeNull();
  });

  it('legs can be added and removed, never below three', () => {
    seedStore([]);
    m = mount(<DeedEntry initialMode="bearings" />);
    const { container } = m;
    expect(container.querySelectorAll('[aria-label^="Remove leg"]')).toHaveLength(4);
    click(byAriaLabel(container, 'Remove leg 4'));
    expect(container.querySelectorAll('[aria-label^="Remove leg"]')).toHaveLength(3);
    expect(byAriaLabel<HTMLButtonElement>(container, 'Remove leg 3').disabled).toBe(true);
    click(
      Array.from(container.querySelectorAll('button')).find((b) =>
        (b.textContent ?? '').includes('Add a leg'),
      )!,
    );
    expect(container.querySelectorAll('[aria-label^="Remove leg"]')).toHaveLength(4);
  });
});
