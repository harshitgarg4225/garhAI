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
 *   - **the keyboard map**: while the tour is open the app-wide shortcuts are
 *     off (`setKeyboardEnabled(false)`, the same switch a modal flips), so `G`
 *     does not arm the wall tool under the dialog.
 *
 * Skip, Escape and Finish all persist completion (`setTourDone(true)`); "Take the
 * tour" in the top bar re-runs it from step one at any time.
 */

import { useEffect, useRef, type JSX } from 'react';
import { useNavigate } from 'react-router-dom';

import { useUiStore } from '../../stores/ui';
import { TOUR_STEPS, clampStep } from './steps';
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
  const autoStarted = useRef(false);
  useEffect(() => {
    if (!autoStart || autoStarted.current) return;
    autoStarted.current = true;
    if (tourDone || tourStep !== null) return;
    const here = TOUR_STEPS.findIndex((candidate) => candidate.tab === currentTab);
    if (here < 0) return;
    if (here === 0) startTour();
    else setTourStep(here);
  }, [autoStart, tourDone, tourStep, currentTab, startTour, setTourStep]);

  // The step's tab must be on screen for its anchor to exist.
  useEffect(() => {
    if (!open || step === undefined) return;
    if (currentTab !== step.tab) {
      navigate(`/projects/${encodeURIComponent(projectId)}/${step.tab}`);
    }
  }, [open, step, currentTab, projectId, navigate]);

  // Own the keyboard while open, like a modal; hand it back on close/unmount.
  useEffect(() => {
    if (!open) return undefined;
    setKeyboardEnabled(false);
    return () => setKeyboardEnabled(true);
  }, [open, setKeyboardEnabled]);

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
    />
  );
}
