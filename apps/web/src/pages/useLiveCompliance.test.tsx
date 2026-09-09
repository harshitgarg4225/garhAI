/**
 * The hook's error branch, executed. A re-check that fails after a green one
 * must (a) keep the last good results on screen, (b) expose the failure, and
 * (c) clear it on a successful `recheck()`. Before this test existed the shell
 * discarded `error`, so a 5xx left a green strip describing a state several
 * edits old with nothing to say it was stale.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { complianceSchema } from '../lib/schemas';
import { useModelStore } from '../stores/model';
import { useLiveCompliance } from './useLiveCompliance';
import type { LiveCompliance } from './useLiveCompliance';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../lib/api', () => ({ api: { compliance: { get: mocks.get } } }));

const PROJECT_ID = 'proj_01J0000000000000000000P1';

function report(ruleId: string) {
  return complianceSchema.parse({
    evaluated: true,
    projectId: PROJECT_ID,
    live: true,
    results: [
      {
        ruleId,
        status: 'fail',
        message: `${ruleId} failed`,
        checkType: 'opening_width_min',
        fixAvailable: true,
        autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
      },
    ],
    counts: { fail: 1 },
  });
}

let container: HTMLDivElement;
let root: Root;
let latest: LiveCompliance | null = null;

function Probe(): null {
  latest = useLiveCompliance(PROJECT_ID);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  latest = null;
  useModelStore.getState().reset();
  useModelStore.setState({ status: 'ready', projectId: PROJECT_ID, baseIdx: 0 });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

/** Let the 450 ms debounce fire and the promise chain settle. */
async function settle(): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(500);
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('useLiveCompliance — a failed re-check', () => {
  it('keeps the last good results, exposes the error, and recheck() clears it', async () => {
    mocks.get.mockResolvedValueOnce(report('nbc.door.main.width.min'));
    act(() => root.render(<Probe />));
    await settle();
    expect(latest?.issues).toHaveLength(1);
    expect(latest?.issues?.[0]?.ruleId).toBe('nbc.door.main.width.min');
    // The mapping ran through the shared wire→VM path: this rule is client-fixable.
    expect(latest?.issues?.[0]?.fixAvailable).toBe(true);
    expect(latest?.error).toBeNull();
    expect(latest?.report?.live).toBe(true);

    // The server confirms an edit; the re-check fails.
    mocks.get.mockRejectedValueOnce(new Error('503 from the rules service'));
    act(() => useModelStore.setState({ baseIdx: 1 }));
    await settle();
    expect(latest?.error).not.toBeNull();
    expect(latest?.error?.message).toContain('503');
    // Stale beats blank: the previous chips are still there…
    expect(latest?.issues).toHaveLength(1);
    // …and nothing pretends the check is still running.
    expect(latest?.checking).toBe(false);

    // Retry without any server confirmation.
    mocks.get.mockResolvedValueOnce(report('nbc.door.internal.width.min'));
    act(() => latest?.recheck());
    await settle();
    expect(latest?.error).toBeNull();
    expect(latest?.issues?.[0]?.ruleId).toBe('nbc.door.internal.width.min');
    expect(mocks.get).toHaveBeenCalledTimes(3);
  });

  it('NEGATIVE: a successful re-check never leaves a stale error behind', async () => {
    mocks.get.mockRejectedValueOnce(new Error('first run failed'));
    act(() => root.render(<Probe />));
    await settle();
    expect(latest?.error).not.toBeNull();
    expect(latest?.issues).toBeNull();

    mocks.get.mockResolvedValueOnce(report('nbc.door.main.width.min'));
    act(() => useModelStore.setState({ baseIdx: 1 }));
    await settle();
    expect(latest?.error).toBeNull();
    expect(latest?.issues).toHaveLength(1);
  });

  it('treats evaluated:false as "nothing checked", not as a pass and not as an error', async () => {
    mocks.get.mockResolvedValueOnce(
      complianceSchema.parse({ evaluated: false, projectId: PROJECT_ID, reason: 'no plot yet' }),
    );
    act(() => root.render(<Probe />));
    await settle();
    expect(latest?.issues).toBeNull();
    expect(latest?.report).toBeNull();
    expect(latest?.error).toBeNull();
  });
});
