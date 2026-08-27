/**
 * Spec for the generation-theater SSE reduction. The invariant under test is
 * §15's: the state NEVER contains anything an event did not deliver — no
 * synthesised percent, no invented copy, no stage the worker didn't announce.
 */

import { describe, expect, it } from 'vitest';

import type { ProgressEvent } from '../../lib/schemas';
import {
  INITIAL_THEATER,
  eventPercent,
  reduceTheater,
  stageDetail,
  theaterFromJob,
  type TheaterState,
} from './theater';

let seqCounter = 0;

/** A wire-shaped event with defaults; `seq` auto-increments per fixture. */
function evt(partial: Partial<ProgressEvent>): ProgressEvent {
  seqCounter += 1;
  return {
    eventVersion: 1,
    jobId: 'job_1',
    jobKind: 'solver',
    seq: seqCounter,
    at: '2026-08-06T10:00:00Z',
    status: 'running',
    progress: 0,
    stage: null,
    message: null,
    data: {},
    terminal: false,
    ...partial,
  };
}

function run(events: readonly ProgressEvent[]): TheaterState {
  return events.reduce(reduceTheater, INITIAL_THEATER);
}

describe('reduceTheater — queueing', () => {
  it('shows the queue position the event delivered, and only that', () => {
    const state = run([evt({ status: 'queued', stage: 'queued', data: { queuePosition: 3 } })]);
    expect(state.status).toBe('queued');
    expect(state.queuePosition).toBe(3);
    expect(state.percent).toBeNull(); // no percent was delivered
    expect(state.stages).toHaveLength(0);
  });

  it('clears the queue position once the job runs', () => {
    const state = run([
      evt({ status: 'queued', stage: 'queued', data: { queuePosition: 2 } }),
      evt({ stage: 'envelope', message: 'Working out the buildable area…', data: { percent: 5 } }),
    ]);
    expect(state.status).toBe('running');
    expect(state.queuePosition).toBeNull();
  });
});

describe('reduceTheater — stage timeline', () => {
  it("renders the worker's words verbatim, in arrival order", () => {
    const state = run([
      evt({ stage: 'envelope', message: 'Working out the buildable area…', data: { percent: 5 } }),
      evt({ stage: 'stairs', message: 'Placing the staircase…', data: { percent: 15 } }),
    ]);
    expect(state.stages.map((s) => s.id)).toEqual(['envelope', 'stairs']);
    expect(state.stages[0]?.message).toBe('Working out the buildable area…');
    expect(state.stages[0]?.state).toBe('done'); // superseded by stairs
    expect(state.stages[1]?.state).toBe('active');
    expect(state.percent).toBe(15);
  });

  it('updates a re-emitted stage in place instead of duplicating it', () => {
    const state = run([
      evt({ stage: 'envelope', message: 'Working out the buildable area…', data: { percent: 5 } }),
      evt({
        stage: 'envelope',
        message: 'Buildable area: 89 m2 across 2 floor(s).',
        data: { percent: 10 },
      }),
    ]);
    expect(state.stages).toHaveLength(1);
    expect(state.stages[0]?.message).toBe('Buildable area: 89 m2 across 2 floor(s).');
    expect(state.stages[0]?.percent).toBe(10);
  });

  it('derives sub-progress detail only from delivered facts', () => {
    const state = run([
      evt({
        stage: 'topology',
        message: 'Packing rooms onto the floor…',
        data: { percent: 35, stairCandidate: 2, stairCandidates: 5 },
      }),
    ]);
    expect(state.stages[0]?.detail).toBe('Stair position 2 of 5');
  });

  it('keeps an unknown stage id renderable — the message is still real', () => {
    const state = run([evt({ stage: 'shafts', message: 'Aligning plumbing shafts…' })]);
    expect(state.stages[0]?.id).toBe('shafts');
    expect(state.stages[0]?.message).toBe('Aligning plumbing shafts…');
  });
});

describe('reduceTheater — honesty about progress', () => {
  it('never invents a percent: absent stays null', () => {
    const state = run([evt({ stage: 'refine', message: 'Squaring up walls…' })]);
    expect(state.percent).toBeNull();
  });

  it('treats the schema-default progress 0 as "not stated"', () => {
    expect(eventPercent(evt({ progress: 0 }))).toBeNull();
    expect(eventPercent(evt({ progress: 40 }))).toBe(40);
    expect(eventPercent(evt({ progress: 0, data: { percent: 30 } }))).toBe(30);
  });

  it('keeps the last delivered percent through silent events', () => {
    const state = run([
      evt({ stage: 'topology', message: 'Packing rooms…', data: { percent: 30 } }),
      evt({ stage: 'topology', message: 'Packing rooms…' }),
    ]);
    expect(state.percent).toBe(30);
  });
});

describe('reduceTheater — silhouettes', () => {
  const silhouette = (optionId: string, rank: number, extra: Record<string, unknown> = {}) =>
    evt({
      data: {
        artifactName: 'plan-option',
        optionId,
        rank,
        composite: 70 + rank,
        ...extra,
      },
    });

  it('collects silhouettes as options clear the gates, sorted by rank', () => {
    const state = run([silhouette('opt_b', 1), silhouette('opt_a', 0)]);
    expect(state.silhouettes.map((s) => s.optionId)).toEqual(['opt_a', 'opt_b']);
    expect(state.silhouettes[0]?.composite).toBe(70);
  });

  it('dedupes by option id', () => {
    const state = run([silhouette('opt_a', 0), silhouette('opt_a', 0)]);
    expect(state.silhouettes).toHaveLength(1);
  });

  it('parses the coordinated miniPlan payload when present, tolerates absence', () => {
    const withPlan = run([
      silhouette('opt_a', 0, {
        miniPlan: {
          walls: [{ a: { x: 0, y: 0 }, b: { x: 6000, y: 0 }, thicknessMm: 230 }],
          rooms: [],
          storeyIndex: 0,
        },
      }),
    ]);
    expect(withPlan.silhouettes[0]?.miniPlan?.walls).toHaveLength(1);

    const without = run([silhouette('opt_b', 0)]);
    expect(without.silhouettes[0]?.miniPlan).toBeNull();
  });

  it('ignores a silhouette event with no option id', () => {
    const state = run([evt({ data: { artifactName: 'plan-option' } })]);
    expect(state.silhouettes).toHaveLength(0);
  });
});

describe('reduceTheater — terminal events', () => {
  it('marks success: stages done, percent 100, banner captured', () => {
    const state = run([
      evt({ stage: 'envelope', message: 'Working out the buildable area…' }),
      evt({
        status: 'succeeded',
        terminal: true,
        data: { banner: '2 strong options found for this plot' },
      }),
    ]);
    expect(state.status).toBe('succeeded');
    expect(state.done).toBe(true);
    expect(state.percent).toBe(100);
    expect(state.stages.every((s) => s.state === 'done')).toBe(true);
    expect(state.banner).toBe('2 strong options found for this plot');
  });

  it('captures an honest failure with the discard-reason summary', () => {
    const state = run([
      evt({
        status: 'failed',
        terminal: true,
        message: 'No layout satisfied every hard rule.',
        data: {
          action: 'Loosen a room size in the brief and try again.',
          rejectedByGates: 14,
          considered: 14,
        },
      }),
    ]);
    expect(state.status).toBe('failed');
    expect(state.failure?.message).toBe('No layout satisfied every hard rule.');
    expect(state.failure?.action).toBe('Loosen a room size in the brief and try again.');
    expect(state.failure?.discardSummary).toContain('14 of 14');
  });

  it('joins explicit discard reasons when the worker listed them', () => {
    const state = run([
      evt({
        status: 'failed',
        terminal: true,
        message: 'Nothing cleared the gates.',
        data: { discardReasons: ['circulation over 18%', 'composite under 55'] },
      }),
    ]);
    expect(state.failure?.discardSummary).toBe('circulation over 18% · composite under 55');
  });

  it('renders cancellation as its own quiet state', () => {
    const state = run([evt({ status: 'cancelled', terminal: true })]);
    expect(state.status).toBe('cancelled');
    expect(state.failure).toBeNull();
  });
});

describe('reduceTheater — replay and ordering guards', () => {
  it('ignores an event whose seq was already applied', () => {
    const first = evt({ stage: 'envelope', message: 'first' });
    const state = reduceTheater(reduceTheater(INITIAL_THEATER, first), {
      ...first,
      message: 'replayed with same seq',
    });
    expect(state.stages[0]?.message).toBe('first');
  });

  it('accepts seq 0 events (some producers omit seq) without deduping them away', () => {
    const state = run([
      evt({ seq: 0, stage: 'envelope', message: 'a' }),
      evt({ seq: 0, stage: 'stairs', message: 'b' }),
    ]);
    expect(state.stages).toHaveLength(2);
  });
});

describe('theaterFromJob — the row-state fallback after a reload', () => {
  it('builds an honest failed state from the persisted row', () => {
    const state = theaterFromJob({
      status: 'failed',
      progress: 60,
      message: 'Worker crashed mid-refinement.',
      stage: 'refine',
      queuePosition: null,
      error: { message: 'Plan generation failed.', action: 'Try again.' },
    });
    expect(state.status).toBe('failed');
    expect(state.done).toBe(true);
    expect(state.failure?.message).toBe('Plan generation failed.');
    expect(state.failure?.action).toBe('Try again.');
  });

  it('shows a queued row with its position and nothing invented', () => {
    const state = theaterFromJob({
      status: 'queued',
      progress: 0,
      message: null,
      stage: null,
      queuePosition: 4,
      error: null,
    });
    expect(state.status).toBe('queued');
    expect(state.queuePosition).toBe(4);
    expect(state.percent).toBeNull();
    expect(state.stages).toHaveLength(0);
  });

  it('accepts a sparse JobDTO — every optional fact absent renders as nothing', () => {
    const state = theaterFromJob({ status: 'running' });
    expect(state.status).toBe('running');
    expect(state.percent).toBeNull();
    expect(state.queuePosition).toBeNull();
    expect(state.stages).toHaveLength(0);
    expect(state.failure).toBeNull();
    expect(state.done).toBe(false);
  });
});

describe('stageDetail', () => {
  it('reads only what the payload carries', () => {
    expect(stageDetail(evt({ data: { candidates: 1 } }))).toBe('1 candidate layout');
    expect(stageDetail(evt({ data: { refined: 3 } }))).toBe('3 layouts refined');
    expect(stageDetail(evt({ data: {} }))).toBeNull();
  });
});
