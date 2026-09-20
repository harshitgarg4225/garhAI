/**
 * The tour wired to the store and the router — the half the scorecard found
 * missing ("a store with no UI"). Pinned: it starts itself on first run and only
 * then, a step on another tab navigates there, Skip/Finish persist completion in
 * localStorage, the keyboard map is switched off while it is open, and the
 * lightbulb's `startTour` re-runs it after completion.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useUiStore } from '../../stores/ui';
import { ProjectTour } from './ProjectTour';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const TOUR_KEY = 'garh.tourDone';

let container: HTMLDivElement;
let root: Root;
let lastPath = '';

function LocationSpy(): null {
  lastPath = useLocation().pathname;
  return null;
}

/** Mounts the tour inside a router at `/projects/p1/<tab>`, like the shell does. */
function mount(tab: string, autoStart = true): void {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[`/projects/p1/${tab}`]}>
        <Routes>
          <Route
            path="/projects/:projectId/:tab"
            element={
              <>
                <LocationSpy />
                <ProjectTour projectId="p1" currentTab={tab} autoStart={autoStart} />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
  });
}

function card(): HTMLElement | null {
  const el = document.querySelector('[data-testid="tour-card"]');
  return el instanceof HTMLElement ? el : null;
}

function click(testId: string): void {
  const el = document.querySelector(`[data-testid="${testId}"]`);
  if (!(el instanceof HTMLElement)) throw new Error(`no ${testId}`);
  act(() => {
    el.click();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  useUiStore.setState({ tourStep: null, tourDone: false, keyboardEnabled: true });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('ProjectTour', () => {
  it('starts itself on first run, and not once the tour has been done', () => {
    mount('brief');
    expect(useUiStore.getState().tourStep).toBe(0);
    expect(card()?.getAttribute('data-step')).toBe('plot');
    expect(useUiStore.getState().keyboardEnabled).toBe(false);

    act(() => root.unmount());
    root = createRoot(container);
    localStorage.setItem(TOUR_KEY, '1');
    useUiStore.setState({ tourStep: null, tourDone: true, keyboardEnabled: true });
    mount('brief');
    expect(useUiStore.getState().tourStep).toBeNull();
    expect(card()).toBeNull();
    expect(useUiStore.getState().keyboardEnabled).toBe(true);
  });

  it('does not start on its own with autoStart off, and startTour opens step one', () => {
    mount('brief', false);
    expect(card()).toBeNull();
    act(() => {
      useUiStore.getState().startTour();
    });
    expect(card()?.getAttribute('data-step')).toBe('plot');
  });

  it('a step on another tab navigates there', () => {
    mount('brief');
    expect(lastPath).toBe('/projects/p1/brief');
    act(() => {
      useUiStore.getState().setTourStep(3); // plan
    });
    expect(lastPath).toBe('/projects/p1/plan');
    act(() => {
      useUiStore.getState().setTourStep(5); // sheets
    });
    expect(lastPath).toBe('/projects/p1/sheets');
  });

  it('Next advances the store; Skip marks the tour done, persists it and hands the keyboard back', () => {
    mount('brief');
    click('tour-next');
    expect(useUiStore.getState().tourStep).toBe(1);
    expect(card()?.getAttribute('data-step')).toBe('brief');

    click('tour-skip');
    expect(useUiStore.getState().tourStep).toBeNull();
    expect(useUiStore.getState().tourDone).toBe(true);
    expect(localStorage.getItem(TOUR_KEY)).toBe('1');
    expect(card()).toBeNull();
    expect(useUiStore.getState().keyboardEnabled).toBe(true);
  });

  it('Finish on the last step completes the tour, and it can be re-run afterwards', () => {
    mount('sheets');
    act(() => {
      useUiStore.getState().setTourStep(5);
    });
    expect(card()?.getAttribute('data-step')).toBe('sheets');
    click('tour-finish');
    expect(useUiStore.getState().tourDone).toBe(true);
    expect(localStorage.getItem(TOUR_KEY)).toBe('1');
    expect(card()).toBeNull();

    // The lightbulb: startTour re-opens from step one even after completion.
    act(() => {
      useUiStore.getState().startTour();
    });
    expect(card()?.getAttribute('data-step')).toBe('plot');
    expect(useUiStore.getState().tourDone).toBe(false);
  });

  it('Escape skips and persists, like the button', () => {
    mount('brief');
    act(() => {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(useUiStore.getState().tourDone).toBe(true);
    expect(localStorage.getItem(TOUR_KEY)).toBe('1');
  });
});
