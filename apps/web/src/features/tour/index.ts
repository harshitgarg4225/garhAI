/**
 * tour — the six-step first-run walkthrough over the project shell (§15).
 *
 * `ProjectTour` is mounted by `pages/ProjectShell.tsx`; `Tour` is the
 * presentational card; `TOUR_STEPS` is the data both read.
 */

export { ProjectTour } from './ProjectTour';
export type { ProjectTourProps } from './ProjectTour';
export { Tour, placeCard } from './Tour';
export type { TourProps } from './Tour';
export { TOUR_STEPS, TOUR_STEP_COUNT, clampStep } from './steps';
export type { TourStep, TourTab } from './steps';
export { useTourAnchor } from './useTourAnchor';
export type { AnchorRect } from './useTourAnchor';
