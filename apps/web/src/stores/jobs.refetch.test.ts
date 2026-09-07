/**
 * The terminal SSE frame must never be overwritten by a row the API has not
 * written yet.
 *
 * The worker's terminal event reaches the browser over pub/sub a few tens of
 * milliseconds before the API's lifecycle consumer marks the row. The store's
 * refetch on that frame read `running`, upserted it over `succeeded`, and — the
 * stream having closed on the terminal frame — the Plan tab said "still
 * generating" for a plan the solver had delivered in seven seconds.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../lib/api', () => ({
  api: { solver: { get: vi.fn() }, renders: { get: vi.fn() }, exports: { get: vi.fn() } },
}));
vi.mock('../lib/sse', () => ({ subscribeJobEvents: vi.fn() }));

import { api } from '../lib/api';
import { progressEventSchema, type Job } from '../lib/schemas';
import { subscribeJobEvents } from '../lib/sse';
import { TERMINAL_REFETCH_ATTEMPTS, TERMINAL_REFETCH_DELAY_MS, useJobsStore } from './jobs';

interface Handlers {
  onEvent: (event: unknown) => void;
  onError: (error: unknown) => void;
}

function row(id: string, status: Job['status']): Job {
  return {
    id,
    kind: 'solver',
    type: '',
    projectId: 'p1',
    status,
    progress: status === 'succeeded' ? 100 : 10,
    stage: null,
    message: null,
    error: null,
    result: null,
    params: {},
    designVersionId: null,
    queuePosition: null,
    createdAt: '2026-09-07T08:00:00.000Z',
    updatedAt: null,
  };
}

function terminalFrame(jobId: string) {
  return progressEventSchema.parse({ jobId, type: 'succeeded', seq: 9, tsMs: 0 });
}

function statusOf(jobId: string): string | undefined {
  return useJobsStore.getState().byProject.p1?.find((j) => j.id === jobId)?.status;
}

describe('the terminal refetch', () => {
  let handlers: Handlers | null = null;

  beforeEach(() => {
    handlers = null;
    vi.mocked(subscribeJobEvents).mockImplementation((options) => {
      handlers = options as unknown as Handlers;
      return () => undefined;
    });
    useJobsStore.setState({ byProject: {}, error: null });
  });

  afterEach(() => {
    vi.mocked(api.solver.get).mockReset();
  });

  it('keeps the frame status while the row still says running, then takes the settled row', async () => {
    vi.mocked(api.solver.get)
      .mockResolvedValueOnce(row('job-a', 'running'))
      .mockResolvedValueOnce(row('job-a', 'running'))
      .mockResolvedValueOnce(row('job-a', 'succeeded'));

    useJobsStore.getState().track('p1', row('job-a', 'queued'));
    expect(handlers).not.toBeNull();
    handlers?.onEvent(terminalFrame('job-a'));
    expect(statusOf('job-a')).toBe('succeeded');

    await vi.waitFor(() => expect(api.solver.get).toHaveBeenCalledTimes(1));
    expect(statusOf('job-a')).toBe('succeeded');

    await vi.waitFor(() => expect(api.solver.get).toHaveBeenCalledTimes(3), {
      timeout: TERMINAL_REFETCH_DELAY_MS * 4 + 1000,
    });
    await vi.waitFor(() => expect(useJobsStore.getState().byProject.p1?.[0]?.progress).toBe(100));
    expect(statusOf('job-a')).toBe('succeeded');
  });

  it('gives up re-reading after the attempt cap and still never downgrades', async () => {
    vi.mocked(api.solver.get).mockImplementation(() => Promise.resolve(row('job-b', 'running')));

    useJobsStore.getState().track('p1', row('job-b', 'queued'));
    handlers?.onEvent(terminalFrame('job-b'));

    await vi.waitFor(
      () => expect(api.solver.get).toHaveBeenCalledTimes(TERMINAL_REFETCH_ATTEMPTS + 1),
      { timeout: TERMINAL_REFETCH_DELAY_MS * (TERMINAL_REFETCH_ATTEMPTS + 2) + 1000 },
    );
    expect(statusOf('job-b')).toBe('succeeded');
  }, 15_000);
});
