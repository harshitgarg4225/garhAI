/**
 * The tracing underlay in the SVG plot editor.
 *
 * Two things are worth testing and one is worth testing HARD: that the image
 * lands where the Plan tab's canvas layer puts it. The two surfaces share one
 * record, so a placement that disagrees would let an architect trace a boundary
 * off a scan that sits somewhere else on the other tab — silently, because
 * neither view shows the other. `agrees with UnderlayLayer's placement` derives
 * the expected centre the way `UnderlayLayer.tsx` does and requires this
 * layer's box to have the same centre and size.
 */

import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import type { Pt } from '@garh/model';

import { useUnderlayStore } from '../underlay';
import { boundaryOp } from './ops';
import { PlotEditor } from './PlotEditor';
import { PlotUnderlayControls, PlotUnderlayImage } from './PlotUnderlay';
import {
  byTestId,
  click,
  currentDoc,
  installPointerEvents,
  installSvgBox,
  mount,
  seedStore,
  setSvgBox,
  setValue,
  type Mounted,
} from './testHarness';
import { makeViewport, toSvg } from './viewport';

const RECT: Pt[] = [
  { x: 0, y: 0 },
  { x: 9144, y: 0 },
  { x: 9144, y: 12192 },
  { x: 0, y: 12192 },
];

/** A 1000 × 800 px scan at 12 mm/px, its top-left pinned at (−2000, 15000) mm. */
const RECORD = {
  objectKey: 'underlays/p1/scan.png',
  imageUrl: 'https://example.invalid/scan.png?sig=1',
  widthPx: 1000,
  heightPx: 800,
  mmPerPx: 12,
  originXMm: -2000,
  originYMm: 15000,
  opacity: 0.6,
  locked: false,
  visible: true,
};

function seedUnderlay(patch: Partial<typeof RECORD> | null = {}): void {
  useUnderlayStore.setState({
    // projectId stays null: the store's debounced writer returns early without
    // one, so a patch is local-only and no test touches the network.
    projectId: null,
    record: patch === null ? null : { ...RECORD, ...patch },
    imageError: null,
    imageNonce: 0,
    mode: 'off',
    marks: [],
  });
}

let m: Mounted | null = null;

beforeAll(() => {
  installPointerEvents();
  installSvgBox();
});

afterEach(() => {
  m?.unmount();
  m = null;
  seedUnderlay(null);
});

describe('the scan behind the boundary', () => {
  it('agrees with UnderlayLayer’s placement: pixel (0,0) is the top-left, the image hangs down', () => {
    seedUnderlay();
    const vp = makeViewport(RECT);
    setSvgBox(vp.vbW, vp.vbH);
    m = mount(
      <svg viewBox={`0 0 ${String(vp.vbW)} ${String(vp.vbH)}`}>
        <PlotUnderlayImage vp={vp} />
      </svg>,
    );
    const img = byTestId<SVGImageElement>(m.container, 'plot-underlay-image');

    const widthMm = RECORD.widthPx * RECORD.mmPerPx; // 12,000
    const heightMm = RECORD.heightPx * RECORD.mmPerPx; // 9,600
    expect(Number(img.getAttribute('width'))).toBe(widthMm);
    expect(Number(img.getAttribute('height'))).toBe(heightMm);

    // The canvas layer (UnderlayLayer.tsx) centres the quad at
    //   x = originXMm + widthMm/2,  y = originYMm − heightMm/2
    // in MODEL mm. Derive that here and map it with the editor's own toSvg, so
    // the two surfaces are compared through their real code, not by eye.
    const centreModel = {
      x: RECORD.originXMm + widthMm / 2,
      y: RECORD.originYMm - heightMm / 2,
    };
    const centreSvg = toSvg(vp, centreModel);
    const boxCentre = {
      x: Number(img.getAttribute('x')) + widthMm / 2,
      y: Number(img.getAttribute('y')) + heightMm / 2,
    };
    expect(boxCentre.x).toBeCloseTo(centreSvg.x, 6);
    expect(boxCentre.y).toBeCloseTo(centreSvg.y, 6);

    expect(img.getAttribute('href')).toBe(RECORD.imageUrl);
    expect(Number(img.getAttribute('opacity'))).toBe(0.6);
    // Never in the hit test: dragging a corner over the scan must not grab it.
    expect(img.getAttribute('class')).toContain('pointer-events-none');
  });

  it('renders nothing when the record is hidden or absent (negative control)', () => {
    const vp = makeViewport(RECT);
    setSvgBox(vp.vbW, vp.vbH);

    seedUnderlay({ visible: false });
    m = mount(
      <svg>
        <PlotUnderlayImage vp={vp} />
      </svg>,
    );
    expect(m.container.querySelector('[data-testid="plot-underlay-image"]')).toBeNull();
    m.unmount();

    seedUnderlay(null);
    m = mount(
      <svg>
        <PlotUnderlayImage vp={vp} />
      </svg>,
    );
    expect(m.container.querySelector('[data-testid="plot-underlay-image"]')).toBeNull();
  });

  it('sits under the grid, the boundary and every handle inside the editor', () => {
    seedUnderlay();
    seedStore([boundaryOp(RECT)]);
    const vp = makeViewport(RECT);
    setSvgBox(vp.vbW, vp.vbH);
    m = mount(<PlotEditor />);
    // The editor canvas, not the icon <svg>s in the header buttons above it.
    const svg = m.container.querySelector('svg[role="application"]')!;
    const kids = Array.from(svg.children);
    const imageAt = kids.findIndex(
      (el) => el.getAttribute('data-testid') === 'plot-underlay-image',
    );
    const polygonAt = kids.findIndex((el) => el.tagName.toLowerCase() === 'polygon');
    expect(imageAt).toBe(0);
    expect(polygonAt).toBeGreaterThan(imageAt);
    // And the boundary is still editable over it.
    expect(m.container.querySelector('[data-testid="vertex-0"]')).not.toBeNull();
  });
});

describe('the scan controls', () => {
  it('hide and show flip the ONE shared record (the Plan tab sees it too)', () => {
    seedUnderlay();
    m = mount(<PlotUnderlayControls />);
    const toggle = byTestId(m.container, 'plot-underlay-toggle');
    expect(toggle.textContent).toBe('Hide scan');
    click(toggle);
    expect(useUnderlayStore.getState().record?.visible).toBe(false);
    expect(byTestId(m.container, 'plot-underlay-toggle').textContent).toBe('Show scan');
    click(byTestId(m.container, 'plot-underlay-toggle'));
    expect(useUnderlayStore.getState().record?.visible).toBe(true);
  });

  it('fading writes the opacity the image renders with', () => {
    seedUnderlay();
    m = mount(<PlotUnderlayControls />);
    click(byTestId(m.container, 'plot-underlay-fade'));
    const slider = m.container.querySelector<HTMLInputElement>('input[type="range"]')!;
    expect(slider.value).toBe('60');
    setValue(slider, '25');
    expect(useUnderlayStore.getState().record?.opacity).toBeCloseTo(0.25, 6);
    expect(m.container.textContent).toContain('25%');
  });

  it('offers nothing at all when the project has no scan', () => {
    seedUnderlay(null);
    m = mount(<PlotUnderlayControls />);
    expect(m.container.textContent).toBe('');
  });

  it('an unloadable scan says so instead of leaving a blank editor', () => {
    seedUnderlay();
    const vp = makeViewport(RECT);
    setSvgBox(vp.vbW, vp.vbH);
    m = mount(
      <div>
        <PlotUnderlayControls />
        <svg>
          <PlotUnderlayImage vp={vp} />
        </svg>
      </div>,
    );
    const img = byTestId<SVGImageElement>(m.container, 'plot-underlay-image');
    // Two failures: the first re-signs (no projectId here, so it answers false),
    // and either way the architect is told what to do next.
    img.dispatchEvent(new Event('error', { bubbles: false }));
    img.dispatchEvent(new Event('error', { bubbles: false }));
    expect(useUnderlayStore.getState().imageError).toMatch(/could not be loaded/);
  });

  it('the editor never invents a scan: no projectId, no load, no layer', () => {
    seedUnderlay(null);
    seedStore([boundaryOp(RECT)]);
    setSvgBox(1000, 1000);
    m = mount(<PlotEditor />);
    expect(useUnderlayStore.getState().projectId).toBeNull();
    expect(m.container.querySelector('[data-testid="plot-underlay-image"]')).toBeNull();
    expect(m.container.querySelector('[data-testid="plot-underlay-toggle"]')).toBeNull();
    expect(currentDoc().plot.boundary).toEqual(RECT);
  });
});
