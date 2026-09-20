/**
 * The side-rail surfaces of the plot tab, driven by real events:
 *
 *   NorthCompass  — a drag on the dial and a typed bearing both land as ONE
 *                   `plot.set_north`; arrows preview and Enter commits.
 *   RoadEdges     — the checkbox, the width field and the name field each
 *                   dispatch `plot.set_road`; the role chips say which edge the
 *                   setback rows will call rear / side A / side B.
 *   PlotReadouts  — perimeter, sides with bearings, diagonals; the deed area is
 *                   typed in sq ft / gaj / sq m and reconciled against the drawn
 *                   area with a percentage, tolerant to 2%.
 *
 * Assertions are against the folded document, never against a class name.
 */

import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import type { Pt } from '@garh/model';

import { NorthCompass } from './NorthCompass';
import { boundaryOp, roadOp } from './ops';
import { PlotReadouts } from './PlotReadouts';
import { RoadEdges } from './RoadEdges';
import { readDeedAreaMm2 } from './rules';
import {
  blur,
  byAriaLabel,
  byTestId,
  click,
  commitValue,
  currentDoc,
  inputByLabel,
  installPointerEvents,
  installSvgBox,
  keyDown,
  mount,
  pointer,
  seedStore,
  setSvgBox,
  setValue,
  type Mounted,
} from './testHarness';

const RECT: Pt[] = [
  { x: 0, y: 0 },
  { x: 9144, y: 0 },
  { x: 9144, y: 12192 },
  { x: 0, y: 12192 },
];

let m: Mounted | null = null;

beforeAll(() => {
  installPointerEvents();
  installSvgBox();
});

afterEach(() => {
  m?.unmount();
  m = null;
});

// ---------------------------------------------------------------------------
// NorthCompass
// ---------------------------------------------------------------------------

describe('NorthCompass', () => {
  it('a drag on the dial commits the bearing under the pointer, once, on release', () => {
    seedStore([boundaryOp(RECT)]);
    setSvgBox(88, 88); // centre (44, 44)
    m = mount(<NorthCompass size={88} />);
    const dial = byAriaLabel<SVGSVGElement>(m.container, 'True north direction');
    // Up-right of centre: 45° clockwise from up.
    pointer(dial, 'pointerdown', { clientX: 54, clientY: 34 });
    expect(currentDoc().plot.northDeg).toBe(0); // preview only
    pointer(dial, 'pointermove', { clientX: 44, clientY: 54 }); // straight down: 180°
    expect(currentDoc().plot.northDeg).toBe(0);
    pointer(dial, 'pointerup', { clientX: 44, clientY: 54 });
    expect(currentDoc().plot.northDeg).toBe(180);
    expect(dial.getAttribute('aria-valuenow')).toBe('180');
  });

  it('typed bearings commit; garbage reverts; arrows preview and Enter commits', () => {
    seedStore([boundaryOp(RECT)]);
    setSvgBox(88, 88);
    m = mount(<NorthCompass size={88} />);
    const readout = Array.from(m.container.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').endsWith('°'),
    )!;
    click(readout);
    const input = byAriaLabel<HTMLInputElement>(m.container, 'North direction in degrees');
    commitValue(input, '-45', 'enter');
    expect(currentDoc().plot.northDeg).toBe(315);

    click(
      Array.from(m.container.querySelectorAll('button')).find((b) =>
        (b.textContent ?? '').endsWith('°'),
      )!,
    );
    commitValue(byAriaLabel(m.container, 'North direction in degrees'), 'north-ish', 'enter');
    expect(currentDoc().plot.northDeg).toBe(315);

    const dial = byAriaLabel<SVGSVGElement>(m.container, 'True north direction');
    keyDown(dial, 'ArrowRight'); // +5 preview
    expect(currentDoc().plot.northDeg).toBe(315);
    keyDown(dial, 'Enter');
    expect(currentDoc().plot.northDeg).toBe(320);
    keyDown(dial, 'ArrowLeft', { shiftKey: true }); // −1 preview
    keyDown(dial, 'Escape'); // reverted
    expect(currentDoc().plot.northDeg).toBe(320);
    expect(dial.getAttribute('aria-valuenow')).toBe('320');
  });
});

// ---------------------------------------------------------------------------
// RoadEdges
// ---------------------------------------------------------------------------

describe('RoadEdges', () => {
  it('checkbox, width and name each dispatch plot.set_road; roles follow the front', () => {
    seedStore([boundaryOp(RECT)]);
    m = mount(<RoadEdges />);
    const { container } = m;
    // No road yet: no role chips at all (every edge is `other`).
    expect(container.querySelectorAll('[data-testid^="edge-role-"]')).toHaveLength(0);

    const boxes = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    expect(boxes).toHaveLength(4);
    click(boxes[0]!);
    expect(currentDoc().plot.roads).toEqual([{ edgeIndex: 0, widthMm: 9000, name: null }]);

    // Roles, as the compliance rows will name them: left as seen from the road.
    expect(container.textContent).toMatch(/Front · entry/);
    expect(byTestId(container, 'edge-role-1').textContent).toBe('Side B');
    expect(byTestId(container, 'edge-role-2').textContent).toBe('Rear');
    expect(byTestId(container, 'edge-role-3').textContent).toBe('Side A');

    const width = inputByLabel(container, 'Road width on edge 1');
    commitValue(width, '12m', 'enter');
    expect(currentDoc().plot.roads[0]?.widthMm).toBe(12000);

    const name = byAriaLabel<HTMLInputElement>(container, 'Road name for edge 1');
    setValue(name, ' 12th Cross ');
    blur(name);
    expect(currentDoc().plot.roads[0]).toEqual({
      edgeIndex: 0,
      widthMm: 12000,
      name: '12th Cross',
    });

    // A wider road on edge 1 takes the front; the chips move with it.
    click(container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')[1]!);
    commitValue(inputByLabel(container, 'Road width on edge 2'), '18m', 'enter');
    expect(byTestId(container, 'edge-role-0').textContent).toBe('Side A');
    expect(byTestId(container, 'edge-role-3').textContent).toBe('Rear');

    // Unticking clears the road (and its name) in one op.
    click(container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')[0]!);
    expect(currentDoc().plot.roads).toEqual([{ edgeIndex: 1, widthMm: 18000, name: null }]);
  });
});

// ---------------------------------------------------------------------------
// PlotReadouts
// ---------------------------------------------------------------------------

describe('PlotReadouts', () => {
  it('shows perimeter, every side with its bearing, and both diagonals', () => {
    seedStore([boundaryOp(RECT)]);
    m = mount(<PlotReadouts />);
    const { container } = m;
    expect(byTestId(container, 'readout-perimeter').textContent).toMatch(/140'-0" around/);
    const sides = byTestId(container, 'readout-sides').textContent ?? '';
    expect(sides).toMatch(/A–B30'-0" · 90°00'/);
    expect(sides).toMatch(/B–C40'-0" · 0°00'/);
    expect(sides).toMatch(/D–A40'-0" · 180°00'/);
    const diagonals = byTestId(container, 'readout-diagonals').textContent ?? '';
    expect(diagonals).toMatch(/A–C50'-0"/);
    expect(diagonals).toMatch(/B–D50'-0"/);
  });

  it('reconciles a typed deed area (sq ft / gaj / sq m) against the drawn area', () => {
    seedStore([boundaryOp(RECT), roadOp(0, 9000)]);
    m = mount(<PlotReadouts />);
    const { container } = m;
    expect(container.querySelector('[data-testid="deed-reconciliation"]')).toBeNull();

    click(byTestId(container, 'deed-area-value'));
    commitValue(byAriaLabel(container, 'Area as per the sale deed'), '1200 sq ft', 'enter');
    // Stored as an integer mm² with the profile; the city pack is untouched.
    expect(readDeedAreaMm2(currentDoc().plot.regProfile.overrides)).toBe(111_483_648);
    expect(currentDoc().plot.regProfile.cityPack).toBeNull();
    let recon = byTestId(container, 'deed-reconciliation').textContent ?? '';
    expect(recon).toMatch(/\+0%/);
    expect(recon).toMatch(/Within 2%/);

    // 133 gaj is 1,197 sq ft — inside the tolerance.
    click(byTestId(container, 'deed-area-value'));
    commitValue(byAriaLabel(container, 'Area as per the sale deed'), '133 gaj', 'enter');
    recon = byTestId(container, 'deed-reconciliation').textContent ?? '';
    expect(recon).toMatch(/exceeds the deed/);
    expect(recon).toMatch(/Within 2%/);

    // 130 m² is 1,399 sq ft — the drawing is 14.2% short: a scrutiny note.
    click(byTestId(container, 'deed-area-value'));
    commitValue(byAriaLabel(container, 'Area as per the sale deed'), '130 sqm', 'enter');
    recon = byTestId(container, 'deed-reconciliation').textContent ?? '';
    expect(recon).toMatch(/is short of the deed/);
    expect(recon).toMatch(/−14\.2%/);
    expect(recon).toMatch(/More than 2% apart/);

    // NEGATIVE: unreadable text changes nothing and names the formats.
    click(byTestId(container, 'deed-area-value'));
    commitValue(byAriaLabel(container, 'Area as per the sale deed'), 'twelve hundred', 'enter');
    expect(readDeedAreaMm2(currentDoc().plot.regProfile.overrides)).toBe(130_000_000);
    expect(container.textContent).toMatch(/1200 sq ft, 133 gaj or 111.5 sqm/);

    // Clear removes the key entirely (an absent deed area is not a zero).
    click(Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Clear')!);
    expect(readDeedAreaMm2(currentDoc().plot.regProfile.overrides)).toBeNull();
    expect(currentDoc().plot.regProfile.overrides).toEqual({});
  });
});
