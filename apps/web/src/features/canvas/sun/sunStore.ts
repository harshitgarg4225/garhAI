/**
 * sunStore.ts — the sun scrubber's state. Date + minutes-of-day, IST.
 *
 * DELIBERATELY NOT PART OF THE MODEL. Where the sun is is a *viewing*
 * condition, like the camera — it is not design state, it folds from no op,
 * and it must never dirty a storey. Scrubbing this store re-aims one
 * directional light (`SunLight` subscribes transiently and calls
 * `core.invalidate()`); the model document, its `stateHash`, and every mesh
 * cache stay byte-identical. `scrubInvariance.test.ts` pins exactly that.
 *
 * Integers only: `minutesOfDay` is an integer minute and the date is integer
 * fields, so two clients scrubbed to "21-06, 14:30" compute the identical sun.
 */

import { create } from 'zustand';

import { utcMsToIst, type CalendarDate } from './solar';

export interface SunState {
  /** Calendar date, IST. */
  readonly day: CalendarDate;
  /** Integer minutes past midnight, IST wall clock. 0..1439. */
  readonly minutesOfDay: number;

  setDay: (day: CalendarDate) => void;
  setMinutesOfDay: (minutes: number) => void;
  /** Jump to the machine's current IST date and time. */
  setToNow: (nowMs?: number) => void;
}

function clampMinutes(minutes: number): number {
  if (!Number.isFinite(minutes)) return 720;
  return Math.min(1439, Math.max(0, Math.round(minutes)));
}

/** Initial state — exported so specs can pin a deterministic "now". */
export function initialSunFields(nowMs: number = Date.now()): Pick<SunState, 'day' | 'minutesOfDay'> {
  const ist = utcMsToIst(nowMs);
  return { day: ist.date, minutesOfDay: clampMinutes(ist.minutesOfDay) };
}

export const useSunStore = create<SunState>()((set) => ({
  ...initialSunFields(),

  setDay: (day) => set({ day }),
  setMinutesOfDay: (minutes) => set({ minutesOfDay: clampMinutes(minutes) }),
  setToNow: (nowMs) => set(initialSunFields(nowMs)),
}));

/**
 * Honest quick-picks for the scrubber (§15: controls, not decoration): the
 * solar extremes an architect actually checks a design against. Year is the
 * current one at module load; the solstice/equinox day drifts by at most a
 * day across years, which the scrubber's own day precision absorbs.
 */
export function seasonPresets(year: number): readonly { label: string; day: CalendarDate }[] {
  return [
    { label: 'Equinox', day: { year, month: 3, day: 20 } },
    { label: 'Jun solstice', day: { year, month: 6, day: 21 } },
    { label: 'Dec solstice', day: { year, month: 12, day: 21 } },
  ];
}
