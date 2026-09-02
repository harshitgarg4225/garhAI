/**
 * The card an architect reads to know what the trial has left — rendered for real
 * (createRoot into jsdom, the pattern `features/layers/LayerPanel.test.tsx` set).
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { usageSchema } from '../../lib/api';
import { TrialUsageCard, UsageInline } from './TrialUsageCard';

const USAGE = usageSchema.parse({
  planCode: 'free',
  effectivePlanCode: 'free',
  periodStart: '2026-09-01T00:00:00+00:00',
  periodEnd: '2026-10-01T00:00:00+00:00',
  lines: [{ kind: 'solver', used: 2, allowance: 10, remaining: 8 }],
  spend: {
    capUsd: '$5.00',
    spentUsd: '$0.04',
    remainingUsd: '$4.96',
    capMicros: 5_000_000,
    spentMicros: 40_000,
    remainingMicros: 4_960_000,
    enforced: true,
  },
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('TrialUsageCard', () => {
  it('shows generations used, money left and the reset date', () => {
    act(() => root.render(<TrialUsageCard usage={USAGE} />));
    const text = container.textContent ?? '';
    expect(text).toContain('Generations: 2 of 10 used this period');
    expect(text).toContain('Budget: $4.96 of $5.00 left');
    expect(text).toContain('resets on 01-10-2026');
    expect(text).toContain('refunded automatically');
  });

  it('turns red and says so when the allowance is used up', () => {
    const spent = { ...USAGE, lines: [{ kind: 'solver', used: 10, allowance: 10, remaining: 0 }] };
    act(() => root.render(<TrialUsageCard usage={spent} />));
    const section = container.querySelector('section');
    expect(section?.className).toContain('border-fail-line');
    expect(container.textContent).toContain('used up for this period');
  });

  it('says it is loading rather than showing zeros', () => {
    act(() => root.render(<TrialUsageCard usage={null} loading />));
    expect(container.textContent).toContain('Loading your usage');
    expect(container.textContent).not.toContain('0 of');
  });

  it('reports an error instead of pretending', () => {
    act(() => root.render(<TrialUsageCard usage={null} error="Sign in again" />));
    expect(container.textContent).toContain("Usage isn't available right now — Sign in again");
  });
});

describe('UsageInline', () => {
  it('is one line for a toolbar', () => {
    act(() => root.render(<UsageInline usage={USAGE} />));
    expect(container.textContent).toBe(
      'Generations: 2 of 10 used this period · Budget: $4.96 of $5.00 left',
    );
  });

  it('renders nothing while there is nothing to say', () => {
    act(() => root.render(<UsageInline usage={null} />));
    expect(container.textContent).toBe('');
  });
});
