/**
 * The `access` unit reads like a sentence, not like a measurement.
 *
 * `nbc.circulation.room.reachable` reports a WALK, so its `actual` is a word
 * ("unreachable") and its `limit` is the list of routes the rule accepts. The
 * generic formatter turns those into "unreachable of reachable, only-via-bath",
 * which is not a sentence anybody says and is the first thing an architect would
 * read on the chip for the worst defect a plan can have.
 *
 * The negative control is the last case: the same formatter on a `mm` row must
 * still say "of", so a fix that suppressed the limit everywhere goes red here.
 */

import { describe, expect, it } from 'vitest';

import type { ComplianceIssueVM } from '../../components/types';
import { formatActualVsLimit, formatComplianceValue } from './report';

function issue(over: Partial<ComplianceIssueVM>): ComplianceIssueVM {
  return {
    ruleId: 'nbc.circulation.room.reachable',
    packId: 'nbc-core',
    title: 'Every room must be reachable through a door',
    message: 'Bedroom 2 cannot be reached.',
    status: 'fail',
    severity: 'fail',
    unit: 'access',
    actual: 'unreachable',
    limit: ['reachable', 'only-via-bath'],
    elements: ['room_2'],
    ...over,
  } as ComplianceIssueVM;
}

describe('the access unit', () => {
  it('says what happened, in words an architect uses', () => {
    expect(formatComplianceValue('unreachable', 'access', 'm')).toBe('No door route reaches it');
    expect(formatComplianceValue('only-via-bath', 'access', 'm')).toBe('Only through a bath');
    expect(formatComplianceValue('reachable', 'access', 'm')).toBe('Reachable');
  });

  it('never renders the awkward "X of a, b"', () => {
    const rendered = formatActualVsLimit(issue({}), 'm');
    expect(rendered).toBe('No door route reaches it');
    expect(rendered).not.toContain(' of ');
  });

  it('passes an unknown label through rather than inventing one', () => {
    // A label the engine grew and the UI has not learned yet must still be
    // readable — silently blanking it would hide a failing rule.
    expect(formatComplianceValue('through-a-hatch', 'access', 'm')).toBe('through-a-hatch');
  });

  it('NEGATIVE CONTROL: a measured row still reads "actual of limit"', () => {
    const measured = formatActualVsLimit(
      issue({ ruleId: 'nbc.room.habitable.width.min', unit: 'mm', actual: 2400, limit: 2500 }),
      'm',
    );
    expect(measured).toContain(' of ');
  });
});
