/**
 * The tour wired to the store and the router — the half the scorecard found
 * missing ("a store with no UI"). Pinned: it starts itself on first run and only
 * then, a step on another tab navigates there, Skip/Finish persist completion in
 * localStorage, the keyboard map is switched off while it is open, and the
 * lightbulb's `startTour` re-runs it after completion.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useUiStore } from '../../stores/ui';
import { ProjectTour } from './ProjectTour';
import { TOUR_STEPS } from './steps';

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

/** The shell's own shape: one mount, the tab read from the route, a link that moves it. */
function LiveHarness(): ReactElement {
  const params = useParams();
  const navigate = useNavigate();
  return (
    <>
      <LocationSpy />
      <button data-testid="go-plan" onClick={() => navigate('/projects/p1/plan')}>
        Plan
      </button>
      <ProjectTour projectId="p1" currentTab={params.tab} />
    </>
  );
}

function mountLive(tab: string): void {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[`/projects/p1/${tab}`]}>
        <Routes>
          <Route path="/projects/:projectId/:tab" element={<LiveHarness />} />
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

  it('auto-start never moves the architect off the tab they opened', () => {
    // Browser UAT run 13: opening /projects/<id>/plan auto-started the tour on its
    // Brief step, the navigation effect pulled the route to /brief, and the plan
    // canvas never rendered. Auto-start must open the step that belongs to the tab
    // already on screen and leave the route alone.
    mount('plan');
    expect(lastPath).toBe('/projects/p1/plan');
    expect(card()?.getAttribute('data-step')).toBe('plan');
    expect(useUiStore.getState().tourStep).toBe(
      TOUR_STEPS.findIndex((step) => step.tab === 'plan'),
    );
  });

  it('auto-start stays shut on a tab no step belongs to, rather than navigating away', () => {
    mount('renders');
    expect(lastPath).toBe('/projects/p1/renders');
    expect(card()).toBeNull();
    expect(useUiStore.getState().tourStep).toBeNull();
    // ...and the lightbulb still works from there, which is what makes the silence safe.
    act(() => {
      useUiStore.getState().startTour();
    });
    expect(card()?.getAttribute('data-step')).toBe('plot');
  });

  it('does not start on its own with autoStart off, and startTour opens step one', () => {
    mount('brief', false);
    expect(card()).toBeNull();
    act(() => {
      useUiStore.getState().startTour();
    });
    expect(card()?.getAttribute('data-step')).toBe('plot');
  });

  it('does not drag the architect back when THEY change tabs', () => {
    // CI run 93: the smoke journey clicked Plan and the tab never became current,
    // because the navigation effect re-fired on the tab change and sent the route
    // back to the open step's own tab. Pressing Next moves the route; clicking a
    // tab must not. This mounts ONCE and navigates inside that mount, which is what
    // the shell does — remounting would reset the effect and prove nothing.
    mountLive('brief');
    expect(useUiStore.getState().tourStep).toBe(0);
    expect(lastPath).toBe('/projects/p1/brief');

    click('go-plan');
    expect(lastPath).toBe('/projects/p1/plan');
    expect(useUiStore.getState().tourStep).toBe(0);
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

  /*
   * THE CI RUN 95 CASE, in a unit test.
   *
   * The tour opens on the Plan step, over a canvas whose empty state says
   * "Press W and click twice to draw your first wall" — and W did nothing,
   * because the tour held `keyboardEnabled` false for as long as it was on
   * screen. Three browser specs failed on it at once (the wall tool committed
   * nothing) and an architect would have read it as broken drawing tools.
   *
   * Focus decides. The card has it on open, so the tour's arrow keys are
   * unambiguous; a click on the canvas takes it away, and the app's shortcuts
   * come straight back WITHOUT the tour closing.
   */
  it('hands the keyboard back the moment focus leaves the card, tour still open', () => {
    const elsewhere = document.createElement('button');
    document.body.appendChild(elsewhere);

    mount('plan');
    expect(card()?.getAttribute('data-step')).toBe('plan');
    expect(useUiStore.getState().keyboardEnabled).toBe(false);

    act(() => elsewhere.focus());
    expect(useUiStore.getState().tourStep).not.toBeNull();
    expect(card()).not.toBeNull();
    expect(
      useUiStore.getState().keyboardEnabled,
      'the tour is still on screen but the architect is on the canvas — W must be theirs',
    ).toBe(true);

    // And back again, so the arrow keys never mean two things at once.
    const cardEl = card();
    act(() => (cardEl as HTMLElement).focus());
    expect(useUiStore.getState().keyboardEnabled).toBe(false);
    elsewhere.remove();
  });

  it('negative control: focus inside the card is what turns the shortcuts off', () => {
    // Without the focus rule this reads identically to the test above, so it is
    // worth stating: while nothing has moved focus out, the shortcuts ARE off.
    // A version that simply stopped touching `keyboardEnabled` would pass the
    // previous test and fail this one.
    mount('plan');
    expect(useUiStore.getState().keyboardEnabled).toBe(false);
    click('tour-next');
    expect(useUiStore.getState().keyboardEnabled).toBe(false);
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
