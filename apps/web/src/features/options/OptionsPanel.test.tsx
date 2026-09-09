/**
 * The options panel's two terminal states, rendered for real (createRoot into
 * jsdom — the pattern `features/layers/LayerPanel.test.tsx` set) with the API
 * transport stubbed at `lib/api` and the solver job row seeded into the jobs store.
 *
 * Why these two states and nothing else first: the reader's blocking gap for J04
 * was that `pipeline.finalise` writes the stage-A shortfall sentence into
 * `result.banner`, the API row carries it, and the panel rendered `banner` only
 * beside option cards — so a solve that delivered NOTHING, the one state in which
 * the sentence is all there is to read, showed a generic verdict instead. The
 * first test pins the diagnosis on the empty screen; the second pins that the
 * banner still rides with the cards when there are some.
 *
 * NEGATIVE CONTROLS (each applied, the suite run, the failure seen, reverted):
 *   A. render the generic copy regardless of `banner` in NoPlanCleared
 *        → "shows the worker's diagnosis…" fails on the diagnosis text
 *   B. drop <ReadyMadePlanLink /> from NoPlanCleared
 *        → the same test fails on the ready-made href
 *   C. gate the banner paragraph on options.length > 1
 *        → "keeps the banner beside the cards" fails
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@garh/ui';

import { api } from '../../lib/api';
import { useJobsStore, type JobDTO } from '../../stores/jobs';
import { READY_MADE_PLAN_HREF } from './readyMadePlan';

vi.mock('../../lib/api', () => ({
  api: {
    http: { request: vi.fn() },
    solver: { start: vi.fn(), get: vi.fn(), cancel: vi.fn() },
  },
}));
vi.mock('../../lib/sse', () => ({ subscribeJobEvents: () => () => undefined }));
vi.mock('react-router-dom', () => ({
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

import { OptionsPanel } from './OptionsPanel';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const JOB_ID = 'job-solver-1';

const SHORTFALL =
  "The rooms on this floor need about 28.0 m² once circulation is allowed for, and the buildable area after setbacks is 21.1 m² — about 6.9 m² short. Add a floor, move a room upstairs, or reduce a room's minimum size.";

function job(status: JobDTO['status']): JobDTO {
  return {
    id: JOB_ID,
    kind: 'solver',
    status,
    progress: 100,
    createdAt: '2026-09-09T10:00:00Z',
  };
}

function option(id: string, rank: number): Record<string, unknown> {
  return {
    id,
    rank,
    scores: { composite: 70 + rank, circulationPercent: 14 },
    ops: [],
    signature: ['kitchen@SE', 'stair:se-1'],
    stairAnchorId: 'se-1',
    builtUpMm2: 120_000_000,
    footprintMm2: 60_000_000,
    rationaleFacts: ['composite:70'],
    assumptions: [],
    compliance: [],
  };
}

/** Make `api.http.request` answer the solver-job row exactly as the API would. */
function serveRow(row: Record<string, unknown>): void {
  vi.spyOn(api.http, 'request').mockImplementation((input: unknown) => {
    const { parse } = input as { parse: (data: unknown) => unknown };
    return Promise.resolve(parse(row)) as Promise<never>;
  });
}

let container: HTMLDivElement;
let root: Root;

async function mount(element: ReactElement): Promise<void> {
  act(() => {
    root.render(element);
  });
  // The outcome fetch resolves on a microtask; flush it inside act.
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  useJobsStore.setState({ byProject: {}, error: null });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

function panel(): ReactElement {
  return (
    <ToastProvider>
      <OptionsPanel projectId={PROJECT_ID} briefReady />
    </ToastProvider>
  );
}

describe('OptionsPanel after a solve that delivered nothing', () => {
  it("shows the worker's diagnosis, the gate count and the ready-made fallback", async () => {
    useJobsStore.setState({ byProject: { [PROJECT_ID]: [job('succeeded')] } });
    serveRow({
      id: JOB_ID,
      status: 'succeeded',
      options: [],
      banner: SHORTFALL,
      params: { seed: 7 },
      result: { options: [], considered: 4, rejectedByGates: 4 },
    });

    await mount(panel());

    const text = container.textContent ?? '';
    expect(text).toContain('No plan cleared the quality checks');
    // The stage-A sentence, verbatim — not the generic verdict.
    const diagnosis = container.querySelector('[data-testid="no-plan-diagnosis"]');
    expect(diagnosis?.textContent).toBe(SHORTFALL);
    expect(text).not.toContain('The plot, setbacks and brief left no workable layout');
    expect(text).toContain('4 layouts were tried and 4 were discarded by the checks');
    // The way out: the ready-made plan, deep-linked to the dashboard dialog.
    const link = [...container.querySelectorAll('a')].find((a) =>
      /ready-made plan/i.test(a.textContent ?? ''),
    );
    expect(link?.getAttribute('href')).toBe(READY_MADE_PLAN_HREF);
    // And a retry that changes something: a fresh seed, not the same search again.
    const retry = [...container.querySelectorAll('button')].find((b) =>
      /another seed/i.test(b.textContent ?? ''),
    );
    expect(retry).toBeDefined();
    // No option cards on this screen.
    expect(container.querySelectorAll('article').length).toBe(0);
  });

  it('falls back to the generic reason only when the row carries no banner', async () => {
    useJobsStore.setState({ byProject: { [PROJECT_ID]: [job('succeeded')] } });
    serveRow({
      id: JOB_ID,
      status: 'succeeded',
      options: [],
      banner: null,
      params: {},
      result: { options: [], considered: 0, rejectedByGates: 0 },
    });

    await mount(panel());

    const diagnosis = container.querySelector('[data-testid="no-plan-diagnosis"]');
    expect(diagnosis?.textContent).toContain(
      'The plot, setbacks and brief left no workable layout',
    );
    expect(container.textContent).not.toContain('discarded by the checks');
  });
});

describe('OptionsPanel with options', () => {
  it('keeps the banner beside the cards and never shows the empty verdict', async () => {
    useJobsStore.setState({ byProject: { [PROJECT_ID]: [job('succeeded')] } });
    serveRow({
      id: JOB_ID,
      status: 'succeeded',
      options: [option('plan_a', 0), option('plan_b', 1)],
      banner: '2 strong options found for this plot.',
      params: { seed: 7 },
    });

    await mount(panel());

    const text = container.textContent ?? '';
    expect(container.querySelectorAll('article').length).toBe(2);
    const status = [...container.querySelectorAll('[role="status"]')].map(
      (el) => el.textContent ?? '',
    );
    expect(status.some((s) => s.includes('2 strong options found for this plot.'))).toBe(true);
    expect(text).not.toContain('No plan cleared the quality checks');
    expect(container.querySelector('[data-testid="no-plan-diagnosis"]')).toBeNull();
  });
});
