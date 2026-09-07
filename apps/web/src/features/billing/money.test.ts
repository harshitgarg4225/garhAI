/**
 * Rupees derived from micro-dollars, to the paisa, with no float in the path.
 *
 * `SHARED_CASES` is the SAME table `apps/api/tests/test_inr_display.py` pins against
 * the server's `micros_to_paise` — one list, two implementations, so the card and
 * any server-side figure cannot drift apart by a paisa without one of the two files
 * going red.
 */

import { describe, expect, it } from 'vitest';

import { describeRate, formatInr, formatUsd, inrFor, microsToPaise, parseRate } from './money';

/** [micro-USD, rate, paise] — mirrored verbatim from test_inr_display.py. */
const SHARED_CASES: readonly (readonly [number, string, number])[] = [
  [0, '84.00', 0],
  [1, '84.00', 0],
  [59, '84.00', 0],
  [60, '84.00', 1],
  [100, '50.00', 1],
  [300, '50.00', 2],
  [40_000, '84.00', 336],
  [38_095, '84.00', 320],
  [1_000_000, '84.00', 8_400],
  [5_000_000, '84.00', 42_000],
  [1_050_000, '83.1234', 8_728],
  [123_456_789, '84.5', 1_043_210],
];

describe('microsToPaise', () => {
  it.each(SHARED_CASES)('%d µUSD at %s → %d paise (shared table)', (micros, rate, paise) => {
    const scaled = parseRate(rate);
    expect(scaled).not.toBeNull();
    expect(microsToPaise(micros, scaled!)).toBe(paise);
  });

  it('rounds half-up exactly at the half, never to even', () => {
    // 100 µUSD at ₹50 is exactly 0.5 paise; banker's rounding would say 0.
    expect(microsToPaise(100, parseRate('50.00')!)).toBe(1);
    expect(microsToPaise(300, parseRate('50.00')!)).toBe(2);
  });

  it('treats a negative or non-finite amount as nothing, not a credit', () => {
    expect(microsToPaise(-5, parseRate('84.00')!)).toBe(0);
    expect(microsToPaise(Number.NaN, parseRate('84.00')!)).toBe(0);
  });
});

describe('parseRate', () => {
  it.each(['84.00', '84', ' 84 ', '83.1234', '0.5', '84.'])('accepts %j', (raw) => {
    expect(parseRate(raw)).not.toBeNull();
  });

  it('reads "84" and "84.00" as the same rate', () => {
    expect(parseRate('84')).toBe(parseRate('84.00'));
    expect(parseRate('84.5')).toBe(parseRate('84.5000'));
  });

  it.each(['', 'abc', '0', '0.0000', '-84', '84.12345', '8.4e1', '84,00', '₹84'])(
    'refuses %j — the UI shows dollars rather than inventing a rate',
    (raw) => {
      expect(parseRate(raw)).toBeNull();
    },
  );
});

describe('formatInr', () => {
  it('groups the Indian way and always shows paise', () => {
    expect(formatInr(0)).toBe('₹0.00');
    expect(formatInr(5)).toBe('₹0.05');
    expect(formatInr(42_000)).toBe('₹420.00');
    expect(formatInr(100_000)).toBe('₹1,000.00');
    expect(formatInr(12_345_678)).toBe('₹1,23,456.78');
    expect(formatInr(1_234_567_890)).toBe('₹1,23,45,678.90');
  });
});

describe('formatUsd', () => {
  it("floors cents like the server's format_usd", () => {
    expect(formatUsd(4_250_000)).toBe('$4.25');
    expect(formatUsd(38_095)).toBe('$0.03');
    expect(formatUsd(0)).toBe('$0.00');
    expect(formatUsd(-1)).toBe('$0.00');
  });
});

describe('inrFor and describeRate', () => {
  it('converts the trial budget and names the rate with its date', () => {
    expect(inrFor(5_000_000, '84.00')).toBe('₹420.00');
    expect(inrFor(5_000_000, '')).toBeNull();
    expect(describeRate('84.00', '2026-09-01')).toBe('₹84.00 per $ (rate of 2026-09-01)');
    expect(describeRate('84', '2026-09-01')).toBe('₹84.00 per $ (rate of 2026-09-01)');
    expect(describeRate('83.1234', '')).toBe('₹83.1234 per $');
    expect(describeRate('nope', '2026-09-01')).toBeNull();
  });
});
