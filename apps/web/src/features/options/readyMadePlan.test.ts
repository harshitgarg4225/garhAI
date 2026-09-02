import { describe, expect, it } from 'vitest';

import { READY_MADE_PLAN_HREF, READY_MADE_PLAN_TEMPLATE_ID } from './readyMadePlan';

describe('ready-made plan link', () => {
  it('deep-links the dashboard dialog to the solved-plan template', () => {
    expect(READY_MADE_PLAN_HREF).toBe(`/?new=1&template=${READY_MADE_PLAN_TEMPLATE_ID}`);
    expect(READY_MADE_PLAN_TEMPLATE_ID).toBe('blr-30x40-g1-3bhk');
  });
});
