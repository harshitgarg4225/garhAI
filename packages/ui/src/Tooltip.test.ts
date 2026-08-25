/**
 * Tooltip description wiring.
 *
 * The tooltip is how §15's "cite on hover" reaches a compliance chip, and the
 * bug this guards against is silent: hang `aria-describedby` on the wrapper
 * span instead of the trigger and the citation is perfectly visible to a mouse
 * user while being completely absent for a screen-reader user. Nothing on
 * screen looks wrong, so only a test catches it.
 *
 * Rendering is not needed to check the contract — the id list is computed by a
 * pure function, which is why it was extracted.
 */

import { describe, expect, it } from 'vitest';

import { mergeDescribedBy } from './Tooltip';

describe('mergeDescribedBy', () => {
  it('describes the trigger with the tooltip while it is open', () => {
    expect(mergeDescribedBy(undefined, 'tip-1', true)).toBe('tip-1');
  });

  it('drops the id when closed, since the bubble is no longer in the DOM', () => {
    expect(mergeDescribedBy(undefined, 'tip-1', false)).toBeUndefined();
  });

  it("appends to the trigger's own description rather than replacing it", () => {
    // A LengthInput inside a tooltip must keep announcing its hint and error.
    expect(mergeDescribedBy('field-hint field-error', 'tip-1', true)).toBe(
      'field-hint field-error tip-1',
    );
  });

  it("preserves the trigger's own description when closed", () => {
    expect(mergeDescribedBy('field-hint', 'tip-1', false)).toBe('field-hint');
  });

  it('treats blank and whitespace-only values as absent', () => {
    expect(mergeDescribedBy('', 'tip-1', true)).toBe('tip-1');
    expect(mergeDescribedBy('   ', 'tip-1', true)).toBe('tip-1');
    expect(mergeDescribedBy('  ', 'tip-1', false)).toBeUndefined();
  });

  it('trims so the id list never contains empty entries', () => {
    expect(mergeDescribedBy('  field-hint  ', 'tip-1', true)).toBe('field-hint tip-1');
  });
});
