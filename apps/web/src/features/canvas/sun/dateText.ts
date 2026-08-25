/**
 * dateText.ts — DD-MM-YYYY, because §15 says Indian dates are DD-MM-YYYY.
 *
 * Strict on parse (a scrubber that guesses at "3/4" invents a date the user
 * did not type), forgiving on separators (`-`, `/`, `.` all appear on Indian
 * keyboards and forms).
 */

import type { CalendarDate } from './solar';

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] as const;

export function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

export function daysInMonth(year: number, month: number): number {
  if (month === 2 && isLeapYear(year)) return 29;
  return DAYS_IN_MONTH[month - 1] ?? 0;
}

export function isValidCalendarDate(date: CalendarDate): boolean {
  return (
    Number.isInteger(date.year) &&
    Number.isInteger(date.month) &&
    Number.isInteger(date.day) &&
    date.year >= 1900 &&
    date.year <= 2200 &&
    date.month >= 1 &&
    date.month <= 12 &&
    date.day >= 1 &&
    date.day <= daysInMonth(date.year, date.month)
  );
}

/** `{2026, 6, 21}` → `"21-06-2026"`. */
export function formatDdMmYyyy(date: CalendarDate): string {
  const dd = String(date.day).padStart(2, '0');
  const mm = String(date.month).padStart(2, '0');
  return `${dd}-${mm}-${String(date.year).padStart(4, '0')}`;
}

/**
 * `"21-06-2026"` (also `21/06/2026`, `21.6.2026`) → a calendar date, or null.
 * Rejects impossible dates (31-02) and anything that is not day-first —
 * an ISO `2026-06-21` fails the day range and comes back null, which is the
 * correct answer for a field labelled DD-MM-YYYY.
 */
export function parseDdMmYyyy(text: string): CalendarDate | null {
  const m = /^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$/.exec(text);
  if (m === null) return null;
  const date: CalendarDate = {
    day: Number(m[1]),
    month: Number(m[2]),
    year: Number(m[3]),
  };
  return isValidCalendarDate(date) ? date : null;
}

/** `"14:30"` for the time readout next to the slider. */
export function formatMinutes(minutesOfDay: number): string {
  const clamped = Math.min(1439, Math.max(0, Math.round(minutesOfDay)));
  const h = Math.floor(clamped / 60);
  const m = clamped % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}
