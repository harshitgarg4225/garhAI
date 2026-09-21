/**
 * ProjectTour — the tour wired to the `ui` store and the router.
 *
 * The store already held `tourStep` / `tourDone` / `startTour` with nothing
 * rendering them; this is the renderer. Three responsibilities the presentational
 * `Tour` deliberately does not have:
 *
 *   - **first run**: when a project shell mounts for someone who has never
 *     finished or skipped the tour (`tourDone` is persisted per browser), it
 *     starts on its own — once, not on every tab change, and on the step that
 *     belongs to the tab already on screen, so it never moves anyone off the tab
 *     they asked for;
 *   - **navigation**: a step that lives on another tab navigates there first, so
 *     the highlight always points at something that is on screen;
 *   - **the keyboard map**: the tour owns the app-wide shortcuts only while
 *     focus is inside its card, so ←/→ move the tour rather than nudging a
 *     selection — and the moment the architect clicks the canvas, `W` and `0`
 *     are theirs again.
 *
 * Skip, Escape and Finish all persist completion (`setTourDone(true)`); "Take the
 * tour" in the top bar re-runs it from step one at any time.
 */

import { useCallback, useEffect, useRef, useState, type JSX } from 'react';
import { useNavigate } from 'react-router-dom';

import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import { TOUR_STEPS, autoStartStep, clampStep } from './steps';
import { Tour } from './Tour';
import { useTourAnchor } from './useTourAnchor';

export interface ProjectTourProps {
  readonly projectId: string;
  /** The tab currently on screen (the `:tab` route segment, a `TourTab` when known). */
  readonly currentTab: string | undefined;
  /** Start on first visit. Off in tests that only want the explicit path. */
  readonly autoStart?: boolean | undefined;
}

export function ProjectTour({
  projectId,
  currentTab,
  autoStart = true,
}: ProjectTourProps): JSX.Element | null {
  const navigate = useNavigate();
  const tourStep = useUiStore((s) => s.tourStep);
  const tourDone = useUiStore((s) => s.tourDone);
  const startTour = useUiStore((s) => s.startTour);
  const setTourStep = useUiStore((s) => s.setTourStep);
  const setTourDone = useUiStore((s) => s.setTourDone);
  const setKeyboardEnabled = useUiStore((s) => s.setKeyboardEnabled);
  // 'error' counts as settled: a document that would not load has no plan to
  // know about, and holding the tour back forever would be worse than showing it.
  const modelSettled = useModelStore((s) => s.status === 'ready' || s.status === 'error');

  const open = tourStep !== null;
  const index = tourStep === null ? 0 : clampStep(tourStep);
  const step = TOUR_STEPS[index];

  // First run: once per shell mount, never re-armed by a tab change.
  //
  // It opens on the step that belongs to the tab the architect is ALREADY on, and
  // when no step belongs to that tab it does not open at all (the lightbulb still
  // does). Starting at step one regardless would navigate them away from the tab
  // they asked for: browser UAT run 13 opened /projects/<id>/plan, the tour opened
  // its Brief step, the navigation effect below pulled the route to /brief, and the
  // plan canvas the architect came for never rendered.
  //
  // It also skips the steps that tell someone to BUILD something once there is a
  // plan (`autoStartStep`). A project started from a ready-made plan arrives with
  // its plot, brief and house already in the document, and the tour was opening on
  // "Start with the plot — draw the boundary…" on top of a finished house.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (!autoStart || autoStarted.current) return;
    // WAIT FOR THE DOCUMENT. The shell renders as soon as the project's metadata
    // lands and hydrates the op log "a beat later" (stores/project.ts says so in
    // as many words), so on mount the house is always empty — including for a
    // project that has one. Deciding here would make every project look new, and
    // the whole `buildStep` rule below would be dead code that never fired.
    if (!modelSettled) return;
    autoStarted.current = true;
    if (tourDone || tourStep !== null) return;
    // Read the document once, imperatively: the tour must not re-arm when the
    // house changes, and `autoStarted` has already closed the door by here.
    const hasPlan = useModelStore.getState().doc.house.walls.length > 0;
    const here = autoStartStep(currentTab, { hasPlan });
    if (here < 0) return;
    if (here === 0) startTour();
    else setTourStep(here);
  }, [autoStart, modelSettled, tourDone, tourStep, currentTab, startTour, setTourStep]);

  // The step's tab must be on screen for its anchor to exist — but ONLY when the
  // step is what moved. Firing this on any tab change makes the tour fight the
  // architect: open it on Brief, click Plan, and the effect drags the route back
  // to Brief because the step still says so. CI run 93's smoke journey caught
  // exactly that — clicking Plan never made its link aria-current — and a reader
  // would have called the app broken, not the tour. So the navigation is keyed on
  // the step index: pressing Next moves the route, clicking a tab does not.
  const navigatedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!open || step === undefined) {
      navigatedFor.current = null;
      return;
    }
    if (navigatedFor.current === index) return;
    navigatedFor.current = index;
    if (currentTab !== step.tab) {
      navigate(`/projects/${encodeURIComponent(projectId)}/${step.tab}`);
    }
  }, [open, step, index, currentTab, projectId, navigate]);

  /*
   * WHO OWNS THE KEYBOARD WHILE THE TOUR IS UP.
   *
   * It used to be the tour, unconditionally: `setKeyboardEnabled(false)` for as
   * long as the card was on screen. That reads fine and is wrong, because the
   * overlay stopped being modal when its dim layer went `pointer-events-none`
   * — half a modal is not a design, it is a trap. Step 4 of the tour is the
   * PLAN step: it opens over the 2D canvas, next to an empty-state card that
   * says "Press W and click twice to draw your first wall", and W did nothing.
   * The first architect to follow that sentence would have concluded the
   * drawing tools were broken. CI run 95 found it the same way, three specs at
   * once: the wall tool committed nothing, in a browser, with the tour open.
   *
   * So the rule is focus, which is the one thing that can tell the two apart:
   * while the card has focus its ←/→/Enter mean the tour; the moment focus is
   * anywhere else — the architect clicked the canvas, or tabbed out — the app's
   * own shortcuts are live again. Closing or unmounting always hands them back.
   */
  const [focusInCard, setFocusInCard] = useState(false);
  const onFocusWithinChange = useCallback((within: boolean) => setFocusInCard(within), []);

  useEffect(() => {
    if (!open) {
      setFocusInCard(false);
      return undefined;
    }
    setKeyboardEnabled(!focusInCard);
    return () => setKeyboardEnabled(true);
  }, [open, focusInCard, setKeyboardEnabled]);

  const anchor = useTourAnchor(
    open && step !== undefined && currentTab === step.tab ? step.anchor : null,
    open,
  );

  return (
    <Tour
      open={open}
      step={index}
      anchor={anchor}
      onStepChange={(next) => setTourStep(clampStep(next))}
      onSkip={() => setTourDone(true)}
      onFinish={() => setTourDone(true)}
      onFocusWithinChange={onFocusWithinChange}
    />
  );
}
