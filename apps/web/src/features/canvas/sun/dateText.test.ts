/**
 * Spec for the DD-MM-YYYY boundary (§15: Indian dates are day-first).
 * Strict parse — a scrubber that guesses at ambiguous input invents a date.
 */

import { describe, expect, it } from 'vitest';

import { daysInMonth, formatDdMmYyyy, formatMinutes, isLeapYear, parseDdMmYyyy } from './dateText';

describe('formatDdMmYyyy', () => {
  it('pads day and month', () => {
    expect(formatDdMmYyyy({ year: 2026, month: 6, day: 1 })).toBe('01-06-2026');
    expect(formatDdMmYyyy({ year: 2026, month: 12, day: 21 })).toBe('21-12-2026');
  });
});

describe('parseDdMmYyyy', () => {
  it('round-trips its own output', () => {
    const date = { year: 2026, month: 3, day: 20 };
    expect(parseDdMmYyyy(formatDdMmYyyy(date))).toEqual(date);
  });

  it('accepts /, . and - separators and 1-digit day/month', () => {
    expect(parseDdMmYyyy('21/6/2026')).toEqual({ year: 2026, month: 6, day: 21 });
    expect(parseDdMmYyyy('1.1.2026')).toEqual({ year: 2026, month: 1, day: 1 });
    expect(parseDdMmYyyy('  21-06-2026 ')).toEqual({ year: 2026, month: 6, day: 21 });
  });

  it('rejects impossible dates rather than normalising them', () => {
    expect(parseDdMmYyyy('31-02-2026')).toBeNull();
    expect(parseDdMmYyyy('29-02-2025')).toBeNull(); // not a leap year
    expect(parseDdMmYyyy('29-02-2024')).toEqual({ year: 2024, month: 2, day: 29 });
    expect(parseDdMmYyyy('00-06-2026')).toBeNull();
    expect(parseDdMmYyyy('15-13-2026')).toBeNull();
  });

  it('rejects ISO year-first input — the field says DD-MM-YYYY', () => {
    expect(parseDdMmYyyy('2026-06-21')).toBeNull();
  });

  it('rejects junk', () => {
    expect(parseDdMmYyyy('')).toBeNull();
    expect(parseDdMmYyyy('21-06')).toBeNull();
    expect(parseDdMmYyyy('tomorrow')).toBeNull();
    expect(parseDdMmYyyy('21-06-26')).toBeNull(); // two-digit year is a guess
  });
});

describe('calendar helpers', () => {
  it('leap years: divisible by 4, except centuries, except ÷400', () => {
    expect(isLeapYear(2024)).toBe(true);
    expect(isLeapYear(2026)).toBe(false);
    expect(isLeapYear(2100)).toBe(false);
    expect(isLeapYear(2000)).toBe(true);
  });

  it('daysInMonth follows February', () => {
    expect(daysInMonth(2024, 2)).toBe(29);
    expect(daysInMonth(2026, 2)).toBe(28);
    expect(daysInMonth(2026, 4)).toBe(30);
    expect(daysInMonth(2026, 12)).toBe(31);
  });
});

describe('formatMinutes', () => {
  it('renders HH:MM and clamps', () => {
    expect(formatMinutes(0)).toBe('00:00');
    expect(formatMinutes(750)).toBe('12:30');
    expect(formatMinutes(1439)).toBe('23:59');
    expect(formatMinutes(5000)).toBe('23:59');
    expect(formatMinutes(-10)).toBe('00:00');
  });
});
