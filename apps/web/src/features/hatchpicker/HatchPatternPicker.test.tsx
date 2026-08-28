/**
 * HatchPatternPicker.test.tsx — the grid, in a real DOM, clicked.
 *
 * `createRoot` into jsdom and `element.click()`, following
 * `features/layers/LayerPanel.test.tsx`: this workspace has no testing
 * library, and React's own event system delivering the click is stronger than
 * a hand-rolled shallow render would be.
 *
 * The assertion that matters is not "fifteen buttons appeared" — it is that
 * each button contains SVG geometry with real path data in it. A picker whose
 * swatches render as empty boxes would pass a count, and would be the same
 * failure as the module that believed it was registered.
 */

import { act, useState, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HatchPatternPicker } from './HatchPatternPicker';
import { HATCH_PATTERN_KEYS, hatchPattern, isSolidPattern, type HatchPatternKey } from './patterns';

declare global {
  // React 18 reads this to decide whether `act` warnings apply.
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function mount(element: ReactElement): void {
  act(() => {
    root.render(element);
  });
}

function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

/** The tile for one pattern, addressed the way the DOM exposes it. */
function tile(pattern: HatchPatternKey): HTMLButtonElement {
  const found = container.querySelector<HTMLButtonElement>(`button[data-pattern="${pattern}"]`);
  expect(found, `no tile for ${pattern}`).not.toBeNull();
  if (found === null) throw new Error(`no tile for ${pattern}`);
  return found;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  act(() => {
    root = createRoot(container);
  });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

describe('the grid shows every pattern the renderer can draw', () => {
  it('offers all fifteen, each with a real swatch', () => {
    mount(<HatchPatternPicker value="diagonal" onChange={() => undefined} />);
    const tiles = container.querySelectorAll('button[data-pattern]');
    expect(tiles).toHaveLength(15);

    for (const key of HATCH_PATTERN_KEYS) {
      const swatch = tile(key).querySelector(`svg[data-pattern="${key}"]`);
      expect(swatch, `${key} has no swatch`).not.toBeNull();
      if (swatch === null) continue;

      if (isSolidPattern(key)) {
        // A fill, not lines: the second rect is the ink.
        expect(swatch.querySelectorAll('rect').length).toBe(2);
        continue;
      }
      const paths = [...swatch.querySelectorAll('path')];
      expect(paths.length, `${key} drew no line families`).toBeGreaterThan(0);
      for (const path of paths) {
        const d = path.getAttribute('d') ?? '';
        expect(d.length, `${key} drew an empty path`).toBeGreaterThan(0);
        expect(d.startsWith('M '), `${key}: ${d.slice(0, 20)}`).toBe(true);
      }
      // Line geometry, never a fill: a filled swatch would read as solid.
      expect(paths.every((p) => p.getAttribute('fill') === 'none')).toBe(true);
    }
  });

  it('names every tile for a screen reader, and marks the current one', () => {
    mount(<HatchPatternPicker value="brick" onChange={() => undefined} />);
    expect(tile('brick').getAttribute('aria-pressed')).toBe('true');
    expect(tile('stone').getAttribute('aria-pressed')).toBe('false');
    expect(
      [...container.querySelectorAll('button[aria-pressed="true"]')],
      'exactly one tile is current',
    ).toHaveLength(1);
    expect(tile('earth').getAttribute('aria-label')).toBe(hatchPattern('earth').label);
    // The ACAD name is in the tooltip — a drafter looking for ANSI31 finds it.
    expect(tile('diagonal').getAttribute('title')).toContain('ANSI31');
  });

  it('marks the pattern the material implies without hiding the others', () => {
    mount(<HatchPatternPicker value="stone" implied="brick" onChange={() => undefined} />);
    expect(tile('brick').getAttribute('aria-label')).toContain('from the material');
    expect(tile('stone').getAttribute('aria-label')).not.toContain('from the material');
    expect(container.querySelectorAll('button[data-pattern]')).toHaveLength(15);
  });

  it('reports the pattern that was clicked', () => {
    const onChange = vi.fn();
    mount(<HatchPatternPicker value="diagonal" onChange={onChange} />);
    click(tile('timber'));
    click(tile('earth'));
    expect(onChange.mock.calls).toEqual([['timber'], ['earth']]);
  });
});

describe('a chosen pattern survives a re-render', () => {
  it('keeps the override through an unrelated parent re-render', () => {
    // The picker is controlled, so "survives" means: the state that holds the
    // choice is not reset by rendering again. The harness re-renders on every
    // click of the counter, which is what a parent panel does all day.
    function Harness(): ReactElement {
      const [pattern, setPattern] = useState<HatchPatternKey>('diagonal');
      const [ticks, setTicks] = useState(0);
      return (
        <div>
          <button
            type="button"
            data-testid="tick"
            onClick={() => {
              setTicks((n) => n + 1);
            }}
          >
            {`ticks ${String(ticks)}`}
          </button>
          <span data-testid="current">{pattern}</span>
          <HatchPatternPicker value={pattern} onChange={setPattern} />
        </div>
      );
    }

    mount(<Harness />);
    const current = (): string =>
      container.querySelector('[data-testid="current"]')?.textContent ?? '';
    expect(current()).toBe('diagonal');

    click(tile('stone'));
    expect(current()).toBe('stone');
    expect(tile('stone').getAttribute('aria-pressed')).toBe('true');

    const tick = container.querySelector('[data-testid="tick"]');
    expect(tick).not.toBeNull();
    if (tick !== null) {
      click(tick);
      click(tick);
    }
    expect(container.querySelector('[data-testid="tick"]')?.textContent).toBe('ticks 2');
    // …and the choice is still the architect's, not the default.
    expect(current()).toBe('stone');
    expect(tile('stone').getAttribute('aria-pressed')).toBe('true');
    expect(tile('diagonal').getAttribute('aria-pressed')).toBe('false');
  });
});
