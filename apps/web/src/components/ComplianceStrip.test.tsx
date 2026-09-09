/**
 * The strip's two honesty contracts, rendered for real:
 *
 *  1. "Fix it" is present exactly when a press does something — the VM says a
 *     fix is computable AND the shell handed over an `onApplyFix`. A rule with
 *     no computable fix shows a hint, never a dead button (§15).
 *  2. A failed re-check is SAID, not hidden: the chips from the last good run
 *     stay (a blank strip reads as "all passed"), a badge names them as possibly
 *     stale, and Retry is offered.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ComplianceStrip } from './ComplianceStrip';
import type { ComplianceIssueVM } from './types';

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

const fixable: ComplianceIssueVM = {
  ruleId: 'nbc.door.main.width.min',
  status: 'fail',
  message: 'Main door is 900 mm — NBC needs 1,000 mm',
  cite: 'NBC 2016 Part 3, Cl. 4.4',
  confidence: 'seed',
  elementIds: ['opening_01J0000000000000000000D1'],
  fixAvailable: true,
  checkType: 'opening_width_min',
  autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
};

const hintOnly: ComplianceIssueVM = {
  ruleId: 'nbc.room.habitable.area.min',
  status: 'fail',
  message: 'Bedroom 2 is 8.9 m² — NBC needs 9.5 m²',
  cite: 'NBC 2016 Part 3, Cl. 4.2',
  confidence: 'seed',
  elementIds: ['room_01J0000000000000000000R2'],
  fixAvailable: false,
  fixHint: 'Move the shared wall 300 mm into the passage.',
};

function fixButtons(): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll<HTMLButtonElement>('button')).filter((b) =>
    (b.textContent ?? '').includes('Fix it'),
  );
}

describe('ComplianceStrip — "Fix it"', () => {
  it('shows the button only on the fixable chip when the shell provides onApplyFix', () => {
    const onApplyFix = vi.fn();
    act(() =>
      root.render(<ComplianceStrip issues={[fixable, hintOnly]} onApplyFix={onApplyFix} />),
    );
    const buttons = fixButtons();
    expect(buttons).toHaveLength(1);
    act(() => buttons[0]?.click());
    expect(onApplyFix).toHaveBeenCalledTimes(1);
    expect(onApplyFix).toHaveBeenCalledWith(fixable);
  });

  it('NEGATIVE: a rule with no computable fix never gets a button', () => {
    act(() => root.render(<ComplianceStrip issues={[hintOnly]} onApplyFix={vi.fn()} />));
    expect(fixButtons()).toHaveLength(0);
  });

  it('NEGATIVE: without a handler there is no button even on a fixable rule', () => {
    act(() => root.render(<ComplianceStrip issues={[fixable]} />));
    expect(fixButtons()).toHaveLength(0);
  });
});

describe('ComplianceStrip — a failed re-check is surfaced', () => {
  it('keeps the last chips, names them as possibly stale, and offers Retry', () => {
    const onRetry = vi.fn();
    act(() =>
      root.render(
        <ComplianceStrip
          issues={[fixable]}
          error={{ message: 'Compliance service returned 503' }}
          onRetry={onRetry}
        />,
      ),
    );
    // The chip from the last good run is still there…
    expect(container.textContent).toContain('Main door is 900 mm');
    // …and the strip says the last run failed.
    const badge = container.querySelector('[data-testid="compliance-error"]');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain('Last check failed');
    expect(badge?.textContent).toContain('stale');
    const retry = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
      (b.textContent ?? '').includes('Retry'),
    );
    expect(retry).toBeDefined();
    act(() => retry?.click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('says "Check failed" when there were never any chips — never "all passed"', () => {
    act(() =>
      root.render(
        <ComplianceStrip issues={[]} error={{ message: 'network down' }} onRetry={vi.fn()} />,
      ),
    );
    const badge = container.querySelector('[data-testid="compliance-error"]');
    expect(badge?.textContent).toContain('Check failed');
    expect(container.textContent).not.toContain('checks passed');
  });

  it('NEGATIVE: no error → no error badge, and the chips are presented as current', () => {
    act(() => root.render(<ComplianceStrip issues={[fixable]} error={null} onRetry={vi.fn()} />));
    expect(container.querySelector('[data-testid="compliance-error"]')).toBeNull();
  });

  it('hides the error badge while a retry is in flight so two states never compete', () => {
    act(() =>
      root.render(
        <ComplianceStrip issues={[fixable]} checking error={{ message: 'x' }} onRetry={vi.fn()} />,
      ),
    );
    expect(container.querySelector('[data-testid="compliance-error"]')).toBeNull();
    expect(container.textContent).toContain('Re-checking');
  });
});
