/**
 * The fee form's logic, without a DOM: what the owner may type, what it becomes on the
 * wire, and what the page says about the fee in force.
 *
 * The validator mirrors the server's `percent_to_bps` contract (0–100, whole basis
 * points). Each refusal is pinned with its reason, because the reason is what the field
 * shows — and a validator that only said "invalid" would send the owner to the API to
 * find out why.
 */

import { describe, expect, it } from 'vitest';

import { platformMarkupSchema } from '../../lib/api';
import {
  bpsToPercent,
  describeChange,
  describeSetBy,
  formatWhen,
  isUnchanged,
  parsePercentInput,
} from './markup';

describe('parsePercentInput', () => {
  it.each([
    ['5', '5', 500],
    ['7.25', '7.25', 725],
    ['7.5', '7.5', 750],
    ['7.50', '7.5', 750],
    ['.5', '0.5', 50],
    ['0', '0', 0],
    ['100', '100', 10_000],
    [' 12.5 % ', '12.5', 1250],
    ['05', '5', 500],
  ])('accepts %j as %s (%d bps)', (raw, percent, bps) => {
    expect(parsePercentInput(raw)).toEqual({ ok: true, percent, bps });
  });

  it.each([
    ['', 'Enter a percentage'],
    ['   ', 'Enter a percentage'],
    ['abc', 'plain number'],
    ['5,5', 'plain number'],
    ['-1', 'plain number'],
    ['1e2', 'plain number'],
    ['.', 'plain number'],
    ['5.005', 'At most two decimals'],
    ['100.01', 'between 0 and 100'],
    ['101', 'between 0 and 100'],
  ])('refuses %j and says why', (raw, reason) => {
    const parsed = parsePercentInput(raw);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain(reason);
  });

  it('round-trips every whole-basis-point value through the display string', () => {
    for (const bps of [0, 1, 10, 99, 100, 500, 725, 750, 9_999, 10_000]) {
      const parsed = parsePercentInput(bpsToPercent(bps));
      expect(parsed).toMatchObject({ ok: true, bps });
    }
  });
});

describe('bpsToPercent', () => {
  it('has no float noise and no trailing zeros', () => {
    expect(bpsToPercent(500)).toBe('5');
    expect(bpsToPercent(725)).toBe('7.25');
    expect(bpsToPercent(750)).toBe('7.5');
    expect(bpsToPercent(1)).toBe('0.01');
    expect(bpsToPercent(10_000)).toBe('100');
  });
});

const SET = platformMarkupSchema.parse({
  percent: '7.25',
  bps: 725,
  source: 'setting',
  updatedAt: '2026-09-07T08:35:00+00:00',
  updatedBy: '11111111-1111-4111-8111-111111111111',
  updatedByEmail: 'owner@garh.example',
  canSet: true,
});

describe('describeSetBy', () => {
  it('names the owner and the moment for a fee an owner set', () => {
    const text = describeSetBy(SET);
    expect(text).toContain('Set by owner@garh.example on ');
    expect(text).toMatch(/07-09-2026, \d{2}:\d{2}\./u);
  });

  it('is honest about the boot default', () => {
    const fallback = platformMarkupSchema.parse({ percent: '5', bps: 500, source: 'default' });
    expect(describeSetBy(fallback)).toBe(
      'The boot default from BILLING_MARKUP_PERCENT — no owner has changed it yet.',
    );
  });

  it('falls back to the id, then to a generic owner, when the email is unknown', () => {
    expect(describeSetBy({ ...SET, updatedByEmail: null })).toContain(`Set by ${SET.updatedBy}`);
    expect(describeSetBy({ ...SET, updatedByEmail: null, updatedBy: null, updatedAt: null })).toBe(
      'Set by a platform owner.',
    );
  });

  it('tolerates an older api that sends none of the new fields', () => {
    const old = platformMarkupSchema.parse({ percent: '5', bps: 500, source: 'setting' });
    expect(old.canSet).toBe(false);
    expect(describeSetBy(old)).toBe('Set by a platform owner.');
  });
});

describe('the confirm copy and the no-op guard', () => {
  it('says the change starts with the next charge and leaves old rows alone', () => {
    const change = describeChange('7.5');
    expect(change.title).toBe('Change the platform fee to 7.5%?');
    expect(change.description).toContain('From the next metered charge');
    expect(change.description).toContain('provider cost plus 7.5%');
    expect(change.description).toContain('keep the fee they were charged at');
  });

  it('knows when the typed fee is already in force', () => {
    const same = parsePercentInput('7.25');
    const other = parsePercentInput('7.5');
    expect(same.ok && isUnchanged(SET, same)).toBe(true);
    expect(other.ok && isUnchanged(SET, other)).toBe(false);
  });

  it('formatWhen returns null for a timestamp it cannot read', () => {
    expect(formatWhen('not a date')).toBeNull();
  });
});
