/**
 * "Fix it" is offered on a rule that is broken, and on no other.
 *
 * `fixAvailable` from the server means "the pack declares a fix for this rule" — a
 * property of the RULE, true whether or not this plan breaks it. The view-model used
 * it directly, so a passing rule with a declared fix showed the button.
 *
 * That is not cosmetic. `resize-opening-to-limit` sets the element to the LIMIT, so
 * pressing "Fix it" on a 1 000 mm door that already clears the pack's 900 mm minimum
 * resizes it DOWN to 900 — a silent downgrade of a compliant design, offered by a
 * button whose entire promise is to make things compliant. Found in a browser, on the
 * first run of the Fix-it journey in `plan-canvas.spec.ts`.
 *
 * The `warn` case is the interesting one and it is deliberate: a soft failure is
 * still a failure an architect may want cleared, so the button stays.
 */

import { describe, expect, it } from 'vitest';

import { toComplianceIssue, type ComplianceResultDTO } from './_contracts';

const OPENING_FIX = { opType: 'opening.resize', strategy: 'resize-opening-to-limit' };

function dto(over: Partial<ComplianceResultDTO>): ComplianceResultDTO {
  return {
    ruleId: 'nbc.door.main.width.min',
    status: 'fail',
    message: 'The main door is 750 mm - it needs at least 900 mm.',
    checkType: 'opening_width_min',
    fixAvailable: true,
    autofix: OPENING_FIX,
    elements: ['opening_1'],
    ...over,
  } as ComplianceResultDTO;
}

describe('fixAvailable on the view-model', () => {
  it('is true for a failing rule the client can fix', () => {
    expect(toComplianceIssue(dto({ status: 'fail' })).fixAvailable).toBe(true);
  });

  it('is true for a warning — a soft failure is still one to clear', () => {
    expect(toComplianceIssue(dto({ status: 'warn' })).fixAvailable).toBe(true);
  });

  it('is FALSE for a rule that passes, whatever the pack declares', () => {
    expect(
      toComplianceIssue(dto({ status: 'pass' })).fixAvailable,
      'a passing rule offers to "fix" itself. For resize-opening-to-limit that means ' +
        'shrinking a compliant door to the legal minimum on one click.',
    ).toBe(false);
  });

  it('is false for a rule that did not apply', () => {
    expect(toComplianceIssue(dto({ status: 'not_applicable' })).fixAvailable).toBe(false);
  });

  it('NEGATIVE CONTROL: the status is not the only gate', () => {
    // A failing rule the SERVER says has no fix, and a failing rule whose strategy
    // this client cannot compute, both stay false — so a change that replaced the
    // other two conditions with the status check alone goes red here.
    expect(toComplianceIssue(dto({ status: 'fail', fixAvailable: false })).fixAvailable).toBe(
      false,
    );
    expect(
      toComplianceIssue(
        dto({
          status: 'fail',
          autofix: { opType: 'wall.move', strategy: 'move-the-whole-building-back' },
        }),
      ).fixAvailable,
    ).toBe(false);
  });
});
