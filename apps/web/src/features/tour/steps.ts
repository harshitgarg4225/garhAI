/**
 * The six-step first-run tour (§15 "delight walkthrough"), as data.
 *
 * Each step names the project tab it lives on and a CSS selector for the thing
 * it points at (`data-tour` attributes set by the shell, the top bar and the
 * Brief page). The copy says what the architect DOES there, in the order the
 * product is used: plot → brief → generate → plan → compliance → sheets. The
 * tour never claims a feature the tab does not have.
 */

/** The `:tab` segments of `/projects/:id/:tab` a step can live on (routes.tsx). */
export type TourTab = 'brief' | 'plan' | '3d' | 'renders' | 'sheets' | 'compliance';

export interface TourStep {
  readonly id: string;
  /** The tab the step lives on; the tour navigates there before showing it. */
  readonly tab: TourTab;
  /** What to highlight. Missing on screen ⇒ the step is shown centred. */
  readonly anchor: string;
  readonly title: string;
  readonly body: string;
  /**
   * This step tells the architect to BUILD something, so it is only worth
   * auto-starting on while there is nothing built.
   *
   * Half the New-project dialog is ready-made plans, and one of those arrives with
   * its plot, its brief and its house already in the document. Opening on "Start
   * with the plot — draw the boundary, type each edge, or import the surveyor's
   * DXF" over a finished three-bedroom house is the product telling a new
   * architect to do work it has just done for them, in its very first sentence.
   * Measured in a browser: a project made from a ready-made plan opened on exactly
   * that step.
   *
   * Explicitly asking for the tour (the lightbulb) still walks every step — that
   * is someone asking what the product does, not someone being told what to do.
   */
  readonly buildStep?: boolean;
}

export const TOUR_STEPS: readonly TourStep[] = [
  {
    id: 'plot',
    buildStep: true,
    tab: 'brief',
    anchor: '[data-tour="plot"]',
    title: 'Start with the plot',
    body: 'Draw the boundary, type each edge, or import the surveyor’s DXF. Road sides and the city rule pack set the setbacks every later check uses.',
  },
  {
    id: 'brief',
    buildStep: true,
    tab: 'brief',
    anchor: '[data-tour="brief"]',
    title: 'Then the client brief',
    body: 'Fill the form or paste the client’s own words. Every assumption we read out of them is a chip you can edit — nothing applies silently.',
  },
  {
    id: 'generate',
    buildStep: true,
    tab: 'brief',
    anchor: '[data-tour="generate"]',
    title: 'Generate plan options',
    body: 'One click asks the solver for three compliant layouts. Each shows its scores and why it made its choices; apply one and keep editing.',
  },
  {
    id: 'plan',
    tab: 'plan',
    anchor: '[data-tour="tab-plan"]',
    title: 'Edit in 2D and 3D',
    body: 'Walls, doors, windows and stairs with typed lengths and a snap grid; the 3D tab is the same model from above. Everything is undoable, one step at a time.',
  },
  {
    id: 'compliance',
    tab: 'compliance',
    anchor: '[data-tour="tab-compliance"]',
    title: 'Compliance checks as you draw',
    body: 'Setbacks, FAR, coverage, ventilation and more re-check on every edit, each with its rule, its citation and how confident the seed value is.',
  },
  {
    id: 'sheets',
    tab: 'sheets',
    anchor: '[data-tour="tab-sheets"]',
    title: 'Produce the drawing set',
    body: 'Site plan, floor plans, elevations, sections, schedules and the area statement — generated, reviewed, then exported as PDF or DXF for submission.',
  },
];

export const TOUR_STEP_COUNT = TOUR_STEPS.length;

/** What the document already contains, as far as the tour needs to know. */
export interface TourFacts {
  /** The house has walls — this project did not arrive empty. */
  readonly hasPlan: boolean;
}

/**
 * The step a first run should open on, or `-1` for "say nothing".
 *
 * Two rules, both learned in a browser:
 *
 * 1. Only steps on the tab already on screen are candidates. Starting at step one
 *    regardless navigates the architect off the tab they asked for (UAT run 13).
 * 2. A build step is skipped once there is a plan, because it would be instructing
 *    someone to do work the product has already done (see `buildStep`).
 *
 * When nothing is left, nothing opens. A tour is an offer, and the lightbulb in the
 * top bar is where it stays on offer.
 */
export function autoStartStep(currentTab: string | undefined, facts: TourFacts): number {
  return TOUR_STEPS.findIndex(
    (step) => step.tab === currentTab && !(step.buildStep === true && facts.hasPlan),
  );
}

/** Clamp any number to a valid step index. */
export function clampStep(step: number): number {
  if (!Number.isFinite(step)) return 0;
  return Math.min(TOUR_STEP_COUNT - 1, Math.max(0, Math.trunc(step)));
}
