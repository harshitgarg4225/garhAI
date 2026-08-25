/**
 * The +91 mobile helpers (§15 "Indian defaults").
 *
 * These run on every keystroke of the phone field, and the failure they exist
 * to prevent is a real one: an architect pastes "+91 98765 43210" from WhatsApp
 * and a naive field stores twelve digits, or strips the leading 9 along with
 * the country code. Each case below is a paste format we expect to see.
 */

import { describe, expect, it } from 'vitest';

import { formatIndianMobile, isPlausibleIndianMobile, normaliseIndianMobile } from './Input';

describe('normaliseIndianMobile', () => {
  it('keeps a plain ten-digit number', () => {
    expect(normaliseIndianMobile('9876543210')).toBe('9876543210');
  });

  it('strips separators of every kind', () => {
    expect(normaliseIndianMobile('98765 43210')).toBe('9876543210');
    expect(normaliseIndianMobile('98765-43210')).toBe('9876543210');
    expect(normaliseIndianMobile('(98765) 43210')).toBe('9876543210');
  });

  it('drops a pasted +91 country code', () => {
    expect(normaliseIndianMobile('+91 98765 43210')).toBe('9876543210');
    expect(normaliseIndianMobile('+919876543210')).toBe('9876543210');
    expect(normaliseIndianMobile('0091 9876543210')).toBe('9876543210');
  });

  it('drops the STD zero', () => {
    expect(normaliseIndianMobile('09876543210')).toBe('9876543210');
  });

  it('does NOT strip a leading 91 from a genuine ten-digit number', () => {
    // 9188888888 is a valid mobile number that happens to start with 91. The
    // length guard is what keeps it intact.
    expect(normaliseIndianMobile('9188888888')).toBe('9188888888');
  });

  it('does NOT strip a leading 00 from a ten-digit string', () => {
    // Same length guard, other prefix. Only longer-than-ten input can be
    // carrying a dialling prefix worth removing.
    expect(normaliseIndianMobile('0012345678')).toBe('0012345678');
  });

  it('truncates anything longer than ten digits', () => {
    expect(normaliseIndianMobile('98765432101234')).toBe('9876543210');
  });

  it('returns an empty string for input with no digits', () => {
    expect(normaliseIndianMobile('')).toBe('');
    expect(normaliseIndianMobile('call me')).toBe('');
  });
});

describe('formatIndianMobile', () => {
  it('groups as 5 + 5, the way the number is read aloud', () => {
    expect(formatIndianMobile('9876543210')).toBe('98765 43210');
  });

  it('does not add a space until there is something after it', () => {
    expect(formatIndianMobile('98765')).toBe('98765');
    expect(formatIndianMobile('987654')).toBe('98765 4');
  });

  it('normalises before formatting, so pasted text renders correctly', () => {
    expect(formatIndianMobile('+91-98765-43210')).toBe('98765 43210');
  });
});

describe('isPlausibleIndianMobile', () => {
  it('accepts numbers starting 6, 7, 8 or 9', () => {
    for (const first of ['6', '7', '8', '9']) {
      expect(isPlausibleIndianMobile(`${first}876543210`)).toBe(true);
    }
  });

  it('rejects numbers starting 0–5', () => {
    for (const first of ['0', '1', '2', '3', '4', '5']) {
      expect(isPlausibleIndianMobile(`${first}876543210`)).toBe(false);
    }
  });

  it('rejects anything that is not exactly ten digits', () => {
    expect(isPlausibleIndianMobile('987654321')).toBe(false);
    expect(isPlausibleIndianMobile('')).toBe(false);
  });

  it('validates the normalised form, not the raw text', () => {
    expect(isPlausibleIndianMobile('+91 98765 43210')).toBe(true);
  });
});
