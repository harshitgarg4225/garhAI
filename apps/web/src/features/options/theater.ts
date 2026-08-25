/**
 * Generation theater — the pure reduction from the solver job's SSE events to
 * what the screen shows. §15's rule is the whole design: the timeline renders
 * ONLY what events delivered. No synthesised progress, no timer nudging a bar,
 * no invented copy. If the worker goes quiet, the theater goes quiet, and that
 * silence is rendered honestly as "still working" with no percent.
 *
 * Event vocabulary (producer: services/solver/pipeline.py `announce()` via
 * common/progress.py, bridged by garh_api/queue.py — see the return notes for
 * the wire contract and the schema translation lib/schemas.ts must perform):
 *
 *   stage ids   envelope · program · stairs · topology · refine · critic ·
 *               vastu · relax (re-emitted with new messages as work proceeds;
 *               'queued' only ever comes from the job row, not the worker)
 *   artifact    data.artifactName === 'plan-option' (+ optionId, rank, composite,
 *               and optionally miniPlan — the coordinated silhouette payload)
 *   terminal    status succeeded | failed | cancelled, or event.terminal
 *
 * The reducer accepts events in `seq` order (lib/sse.ts already dedupes and
 * orders); an unknown stage id still renders — its message is the worker's own
 * words, which is exactly what §15 wants shown.
 */

import type { ProgressEvent } from '../../lib/schemas';
import { miniPlanSchema, type MiniPlan } from './types';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/**
 * Stage ids in pipeline order (services/solver/pipeline.py announce calls).
 * Documentation of the vocabulary, not a filter: the reducer renders unknown
 * ids too, in arrival order — the worker's words are always shown (§15).
 * 'queued' is synthesised from the job ROW only; 'relax' appears only when
 * §5.6 relaxed soft weights because fewer than 3 options cleared the gates.
 */
export const STAGE_ORDER: readonly string[] = [
  'queued',
  'envelope',
  'program',
  'stairs',
  'topology',
  'refine',
  'critic',
  'vastu',
  'relax',
];

export interface TheaterStage {
  readonly id: string;
  /** The worker's own message, verbatim. Never synthesised. */
  readonly message: string;
  /** Percent the worker reported when this stage last spoke, or null. */
  readonly percent: number | null;
  /** Sub-progress facts the event carried (stair candidate 2 of 5, …). */
  readonly detail: string | null;
  readonly state: 'done' | 'active' | 'pending';
}

export interface TheaterSilhouette {
  readonly optionId: string;
  readonly rank: number;
  readonly composite: number;
  /** Present when the event carried the coordinated miniPlan payload. */
  readonly miniPlan: MiniPlan | null;
}

export interface TheaterFailure {
  readonly message: string;
  readonly action: string | null;
  /** Why candidates were discarded, when the worker summarised it. */
  readonly discardSummary: string | null;
}

export interface TheaterState {
  readonly status: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  readonly queuePosition: number | null;
  /** Last percent an event actually delivered. Null = indeterminate. */
  readonly percent: number | null;
  /** Stages in the order they were first heard from. */
  readonly stages: readonly TheaterStage[];
  readonly silhouettes: readonly TheaterSilhouette[];
  readonly warnings: readonly string[];
  readonly failure: TheaterFailure | null;
  /** §5.6 honest banner, when the terminal event carried one. */
  readonly banner: string | null;
  readonly lastSeq: number;
  /** True once any terminal event arrived. */
  readonly done: boolean;
}

export const INITIAL_THEATER: TheaterState = {
  status: 'idle',
  queuePosition: null,
  percent: null,
  stages: [],
  silhouettes: [],
  warnings: [],
  failure: null,
  banner: null,
  lastSeq: 0,
  done: false,
};

// ---------------------------------------------------------------------------
// Reduction
// ---------------------------------------------------------------------------

function readInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

function readStr(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/**
 * Percent, if the event genuinely carried one. The worker omits percent when
 * it does not know (§15), and the API schema defaults `progress` to 0 — so a
 * bare 0 with no stage is treated as "not stated", not as "0%".
 */
export function eventPercent(event: ProgressEvent): number | null {
  const fromData = readInt(event.data['percent']);
  if (fromData !== null) return clampPercent(fromData);
  if (event.progress > 0) return clampPercent(event.progress);
  return null;
}

function clampPercent(v: number): number {
  return Math.max(0, Math.min(100, v));
}

/** Sub-progress copy derived ONLY from facts the event carried. */
export function stageDetail(event: ProgressEvent): string | null {
  const candidate = readInt(event.data['stairCandidate']);
  const total = readInt(event.data['stairCandidates']);
  if (candidate !== null && total !== null && total > 0) {
    return `Stair position ${candidate} of ${total}`;
  }
  const candidates = readInt(event.data['candidates']);
  if (candidates !== null) return `${candidates} candidate ${plural(candidates, 'layout', 'layouts')}`;
  const refined = readInt(event.data['refined']);
  if (refined !== null) return `${refined} ${plural(refined, 'layout', 'layouts')} refined`;
  const scored = readInt(event.data['scored']);
  if (scored !== null) return `${scored} ${plural(scored, 'layout', 'layouts')} scored`;
  return null;
}

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

function isSilhouetteEvent(event: ProgressEvent): boolean {
  return event.data['artifactName'] === 'plan-option' || event.stage === 'option';
}

function readSilhouette(event: ProgressEvent): TheaterSilhouette | null {
  const optionId = readStr(event.data['optionId']);
  if (optionId === null) return null;
  const rawPlan = event.data['miniPlan'];
  const parsedPlan = rawPlan === undefined ? null : miniPlanSchema.safeParse(rawPlan);
  return {
    optionId,
    rank: readInt(event.data['rank']) ?? 0,
    composite: readInt(event.data['composite']) ?? 0,
    miniPlan: parsedPlan !== null && parsedPlan.success ? parsedPlan.data : null,
  };
}

function upsertStage(
  stages: readonly TheaterStage[],
  id: string,
  message: string | null,
  percent: number | null,
  detail: string | null,
): TheaterStage[] {
  const index = stages.findIndex((s) => s.id === id);
  if (index === -1) {
    // A newly-heard stage becomes active; everything before it is done.
    const done = stages.map((s): TheaterStage => ({ ...s, state: 'done' }));
    return [
      ...done,
      { id, message: message ?? '', percent, detail, state: 'active' },
    ];
  }
  return stages.map((s, i): TheaterStage => {
    if (i !== index) return s;
    return {
      ...s,
      message: message ?? s.message,
      percent: percent ?? s.percent,
      detail: detail ?? s.detail,
    };
  });
}

function readFailure(event: ProgressEvent): TheaterFailure {
  const message =
    readStr(event.message) ??
    readStr(event.data['message']) ??
    'Plan generation did not finish.';
  const action = readStr(event.data['action']);
  const rejected = readInt(event.data['rejectedByGates']);
  const considered = readInt(event.data['considered']);
  const reasons = event.data['discardReasons'];
  let discard: string | null = null;
  if (Array.isArray(reasons) && reasons.length > 0) {
    discard = reasons.filter((r): r is string => typeof r === 'string').join(' · ') || null;
  } else if (rejected !== null && rejected > 0) {
    discard =
      considered !== null
        ? `${rejected} of ${considered} candidate layouts were discarded by the quality gates.`
        : `${rejected} candidate ${plural(rejected, 'layout was', 'layouts were')} discarded by the quality gates.`;
  }
  return { message, action, discardSummary: discard };
}

/**
 * A TheaterState from the job ROW alone — the fallback when no live events
 * exist (page reloaded after the run; the SSE backlog expired). Everything in
 * it is a persisted fact from the row, so it is §15-honest by construction.
 *
 * The parameter shape is deliberately loose (every fact optional/nullable):
 * it accepts the jobs store's `JobDTO` directly, whose fields are all
 * `?: T | null | undefined` because the row may predate a field's existence.
 * A missing fact renders as nothing — never as a made-up default.
 */
export function theaterFromJob(job: {
  readonly status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  readonly progress?: number | null | undefined;
  readonly message?: string | null | undefined;
  readonly stage?: string | null | undefined;
  readonly queuePosition?: number | null | undefined;
  readonly error?:
    | { readonly message: string; readonly action?: string | null | undefined }
    | null
    | undefined;
}): TheaterState {
  const stage = job.stage ?? null;
  const message = job.message ?? null;
  const progress = job.progress ?? 0;
  return {
    ...INITIAL_THEATER,
    status: job.status,
    queuePosition: job.status === 'queued' ? (job.queuePosition ?? null) : null,
    percent: progress > 0 ? clampPercent(progress) : null,
    stages:
      stage !== null && message !== null && message !== ''
        ? [
            {
              id: stage,
              message,
              percent: progress > 0 ? clampPercent(progress) : null,
              detail: null,
              state: job.status === 'succeeded' ? 'done' : 'active',
            },
          ]
        : [],
    failure:
      job.status === 'failed'
        ? {
            message: job.error?.message ?? message ?? 'Plan generation did not finish.',
            action: job.error?.action ?? null,
            discardSummary: null,
          }
        : null,
    done: job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled',
  };
}

/** The reduction. Pure; unit-tested with recorded event sequences. */
export function reduceTheater(state: TheaterState, event: ProgressEvent): TheaterState {
  if (event.seq > 0 && event.seq <= state.lastSeq) return state; // replay guard
  const lastSeq = event.seq > 0 ? event.seq : state.lastSeq;

  // Terminal events settle everything.
  if (event.terminal || event.status === 'succeeded' || event.status === 'failed' || event.status === 'cancelled') {
    const stagesDone =
      event.status === 'succeeded'
        ? state.stages.map((s): TheaterStage => ({ ...s, state: 'done' }))
        : state.stages;
    return {
      ...state,
      lastSeq,
      done: true,
      status: event.status === 'succeeded' || event.status === 'failed' || event.status === 'cancelled'
        ? event.status
        : 'succeeded',
      stages: stagesDone,
      percent: event.status === 'succeeded' ? 100 : state.percent,
      queuePosition: null,
      banner: readStr(event.data['banner']) ?? state.banner,
      failure: event.status === 'failed' ? readFailure(event) : state.failure,
    };
  }

  // Silhouettes: one per option that cleared the gates, deduped by id.
  if (isSilhouetteEvent(event)) {
    const silhouette = readSilhouette(event);
    if (silhouette === null) return { ...state, lastSeq };
    const others = state.silhouettes.filter((s) => s.optionId !== silhouette.optionId);
    return {
      ...state,
      lastSeq,
      status: 'running',
      silhouettes: [...others, silhouette].sort((a, b) => a.rank - b.rank),
    };
  }

  const percent = eventPercent(event);
  const queuePosition = readInt(event.data['queuePosition']);

  // Queued: show position, nothing else moves.
  if (event.status === 'queued' || event.stage === 'queued') {
    return {
      ...state,
      lastSeq,
      status: 'queued',
      queuePosition: queuePosition ?? state.queuePosition,
      percent: percent ?? state.percent,
    };
  }

  const warnings =
    event.status === 'running' && event.data['warning'] === true && event.message
      ? [...state.warnings, event.message]
      : state.warnings;

  if (event.stage !== null && event.stage !== '') {
    return {
      ...state,
      lastSeq,
      status: 'running',
      queuePosition: null,
      percent: percent ?? state.percent,
      stages: upsertStage(state.stages, event.stage, event.message, percent, stageDetail(event)),
      warnings,
    };
  }

  return {
    ...state,
    lastSeq,
    status: 'running',
    queuePosition: null,
    percent: percent ?? state.percent,
    warnings,
  };
}
