/**
 * `jobs` — solver, render, sheet and export work happening on a worker (§12).
 *
 * The store is deliberately thin over the API: it holds one row per job, keyed
 * by project, and keeps those rows current from the SSE progress stream
 * (`lib/sse.ts`). It does not model the *results* — a finished solve is three
 * options in the model store, a finished render is a row in the render history —
 * only the fact that something is running and how far along it is.
 *
 * §15 "generation theater" is a constraint on this file specifically: the stage
 * message shown to the user is the worker's own `message`, passed through
 * untouched. There is no interpolation, no synthesised progress between events,
 * and no client-side timer nudging a bar forward. If a worker goes quiet the
 * card goes quiet, because a progress bar that creeps to 90% and waits is a lie
 * the product does not tell.
 *
 * Subscriptions live in a module-level map rather than in state: an
 * `AbortController` is not rendering state, and putting one in a Zustand store
 * makes every job update re-render anything that selected it.
 */

import { create } from 'zustand';

import { api, type ExportKind, type RenderInput } from '../lib/api';
import { AppError, toProblemDetail, type ProblemDetail } from '../lib/errors';
import { subscribeJobEvents } from '../lib/sse';
import type { ExportJob, Job, JobKind as ApiJobKind, ProgressEvent } from '../lib/schemas';
import type { JobKind, JobStatus } from '../components/types';
import type { JobDTO as ContractJobDTO, JobsSlice } from '../pages/_contracts';

// ---------------------------------------------------------------------------
// The row
// ---------------------------------------------------------------------------

/**
 * What the job cards render. Extends the page contract with the two fields an
 * export needs; `toJobVM()` ignores them, and the exports panel reads them from
 * the store directly.
 */
export interface JobDTO extends ContractJobDTO {
  /** Machine stage token from the worker (`packing_rooms`, `scoring_vastu`). */
  stage?: string | null | undefined;
  /** Short-lived signed URL, present only once an export has succeeded (§13). */
  downloadUrl?: string | null | undefined;
  downloadExpiresAt?: string | null | undefined;
}

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>(['succeeded', 'failed', 'cancelled']);

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL.has(status);
}

/**
 * API job kind → the kind the UI shows.
 *
 * The queue has three worker kinds (`solver`, `render`, `drawings`) and the UI
 * distinguishes four, because "your drawing set is generating" and "your DXF is
 * being packaged" are different sentences to a user waiting on them. Both run on
 * the drawings worker, so the split comes from the job's `type` discriminator.
 */
export function toUiKind(job: { kind: ApiJobKind; type?: string }): JobKind {
  if (job.kind === 'solver') return 'solver';
  if (job.kind === 'render') return 'render';
  return (job.type ?? '').startsWith('export') ? 'export' : 'sheets';
}

/** UI kind → the SSE endpoint family. Exports stream from the drawings worker. */
function toEventKind(kind: JobKind): ApiJobKind {
  if (kind === 'solver') return 'solver';
  if (kind === 'render') return 'render';
  return 'drawings';
}

const KIND_NOUN: Readonly<Record<JobKind, string>> = {
  solver: 'plan generation',
  render: 'render',
  sheets: 'sheet generation',
  export: 'export',
};

/**
 * A worker failure, given the next action golden rule 9 requires.
 *
 * The worker sends a message; what it cannot know is what the user should do
 * about it, so that part is supplied here per kind rather than left blank.
 */
function jobFailure(kind: JobKind, message: string | null): ProblemDetail {
  return {
    code: `${kind}_job_failed`,
    message:
      message === null || message === '' ? `The ${KIND_NOUN[kind]} did not finish.` : message,
    action:
      kind === 'solver'
        ? 'Try again — if it keeps failing, loosen a brief requirement and re-run.'
        : `Try the ${KIND_NOUN[kind]} again. Nothing in your design was changed.`,
  };
}

/** API job → row. */
export function toJobDTO(job: Job | ExportJob): JobDTO {
  const kind = toUiKind(job);
  const download = 'downloadUrl' in job ? job : null;
  return {
    id: job.id,
    kind,
    status: job.status,
    progress: job.progress,
    message: job.message,
    stage: job.stage,
    queuePosition: job.queuePosition,
    createdAt: job.createdAt,
    error: job.status === 'failed' ? jobFailure(kind, job.error) : null,
    downloadUrl: download?.downloadUrl ?? null,
    downloadExpiresAt: download?.expiresAt ?? null,
  };
}

// ---------------------------------------------------------------------------
// Subscription bookkeeping (not state)
// ---------------------------------------------------------------------------

interface JobRuntime {
  readonly projectId: string;
  readonly kind: JobKind;
  /** Closes the SSE stream. Idempotent. */
  readonly unsubscribe: () => void;
  /**
   * Everything needed to re-submit this job. Present only for jobs this tab
   * started — a job adopted from history after a reload cannot be retried
   * blind, and `retry()` says so rather than sending an empty request.
   */
  readonly resubmit: (() => Promise<Job>) | null;
}

const runtimes = new Map<string, JobRuntime>();

function stopRuntime(jobId: string): void {
  const runtime = runtimes.get(jobId);
  if (!runtime) return;
  runtime.unsubscribe();
  runtimes.delete(jobId);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface JobsState extends JobsSlice {
  byProject: Readonly<Record<string, readonly JobDTO[]>>;
  /** Last transport failure while talking to the queue. Not a job failure. */
  error: ProblemDetail | null;

  /**
   * Adopt a job returned by any `POST` that enqueues one, and start streaming
   * its progress. Safe to call twice with the same job.
   */
  track: (projectId: string, job: Job, resubmit?: () => Promise<Job>) => void;

  /** Load what is already running for a project and subscribe to it. */
  watchProject: (projectId: string) => Promise<void>;
  /** Close every stream for a project. Call when leaving the project shell. */
  unwatchProject: (projectId: string) => void;

  /** `POST /projects/:id/solve` (§5). */
  startSolve: (projectId: string, params?: Record<string, unknown>) => Promise<JobDTO>;
  /** `POST /projects/:id/renders` (§9). */
  startRender: (input: RenderInput) => Promise<JobDTO>;
  /** `POST /projects/:id/sheets/generate` (§7). */
  startSheets: (
    projectId: string,
    input?: { designVersionId?: string | null; kinds?: string[] },
  ) => Promise<JobDTO>;
  /** `POST /projects/:id/export` (§F9). */
  startExport: (
    projectId: string,
    kind: ExportKind,
    params?: Record<string, unknown>,
  ) => Promise<JobDTO>;

  clearError: () => void;
}

/** Newest first, with anything still running pinned above anything finished. */
function sortJobs(jobs: readonly JobDTO[]): JobDTO[] {
  return jobs.slice().sort((a, b) => {
    const aDone = isTerminal(a.status) ? 1 : 0;
    const bDone = isTerminal(b.status) ? 1 : 0;
    if (aDone !== bDone) return aDone - bDone;
    return Date.parse(b.createdAt ?? '') - Date.parse(a.createdAt ?? '');
  });
}

/** Insert or merge one row, leaving the other projects untouched. */
function upsert(
  byProject: Readonly<Record<string, readonly JobDTO[]>>,
  projectId: string,
  patch: JobDTO | (Partial<JobDTO> & { id: string }),
): Record<string, readonly JobDTO[]> {
  const list = byProject[projectId] ?? [];
  const index = list.findIndex((j) => j.id === patch.id);
  let next: JobDTO[];
  if (index === -1) {
    // A partial for a job we have never seen would produce a row with no kind
    // or status; drop it rather than render a half-defined card.
    if (patch.kind === undefined || patch.status === undefined) return { ...byProject };
    next = [...list, patch as JobDTO];
  } else {
    next = list.slice();
    next[index] = { ...(list[index] as JobDTO), ...patch };
  }
  return { ...byProject, [projectId]: sortJobs(next) };
}

export const useJobsStore = create<JobsState>()((set, get) => ({
  byProject: {},
  error: null,

  // ── Adoption + streaming ───────────────────────────────────────────────

  track: (projectId, job, resubmit) => {
    const dto = toJobDTO(job);
    set((s) => ({ byProject: upsert(s.byProject, projectId, dto) }));

    if (isTerminal(dto.status)) {
      stopRuntime(job.id);
      return;
    }
    if (runtimes.has(job.id)) return;

    const kind = dto.kind;
    const unsubscribe = subscribeJobEvents({
      jobId: job.id,
      kind: toEventKind(kind),
      onEvent: (event: ProgressEvent) => {
        set((s) => ({
          byProject: upsert(s.byProject, projectId, {
            id: job.id,
            status: event.status,
            progress: event.progress,
            // The worker's own words. Never synthesised — §15.
            message: event.message,
            stage: event.stage,
            queuePosition: readQueuePosition(event) ?? null,
            error: event.status === 'failed' ? jobFailure(kind, event.message) : null,
          }),
        }));

        if (event.terminal || isTerminal(event.status)) {
          stopRuntime(job.id);
          // One last GET: the terminal SSE frame carries status, the job row
          // carries the result (render URL, sheet ids, signed download).
          void refetch(projectId, job.id, kind, set);
        }
      },
      onError: (error) => {
        // A dropped stream is not a failed job. The row keeps its last known
        // state and `lib/sse.ts` reconnects; only a fatal error is worth
        // surfacing, and even then the job itself is still running.
        if (!error.retryable) set({ error: toProblemDetail(error) });
      },
    });

    runtimes.set(job.id, {
      projectId,
      kind,
      unsubscribe,
      resubmit: resubmit ?? null,
    });
  },

  watchProject: async (projectId) => {
    try {
      // §11 gives a list endpoint for renders only. Solver, sheet and export
      // jobs are adopted when this tab starts them; after a reload their cards
      // are gone, which is honest — we genuinely do not know about them — and
      // the render history is what the user actually comes back for.
      const page = await api.renders.list(projectId, { limit: 20 });
      const jobs = page.items.map(toJobDTO);
      set((s) => ({
        byProject: { ...s.byProject, [projectId]: sortJobs(jobs) },
      }));
      for (const job of page.items) {
        if (!isTerminal(job.status)) get().track(projectId, job);
      }
    } catch (err) {
      const error = AppError.from(err);
      if (error.isAborted) return;
      // Not fatal: the project shell renders fine with no job cards.
      set({ error: toProblemDetail(error) });
    }
  },

  unwatchProject: (projectId) => {
    for (const [jobId, runtime] of runtimes) {
      if (runtime.projectId === projectId) stopRuntime(jobId);
    }
    set((s) => {
      const next = { ...s.byProject };
      delete next[projectId];
      return { byProject: next };
    });
  },

  // ── Starting work ──────────────────────────────────────────────────────

  startSolve: async (projectId, params = {}) => {
    const job = await api.solver.start({ projectId, params });
    get().track(projectId, job, () => api.solver.start({ projectId, params }));
    return toJobDTO(job);
  },

  startRender: async (input) => {
    const job = await api.renders.start(input);
    // The viewport capture in `input.view` is what makes a render reproducible,
    // so the retry closure keeps it. That is the only reason renders can be
    // retried and sheets cannot.
    get().track(input.projectId, job, () => api.renders.start(input));
    return toJobDTO(job);
  },

  startSheets: async (projectId, input = {}) => {
    // `generate` answers the whole SheetSetOut — the current sheets PLUS the
    // new job — so the tab can keep showing yesterday's set while today's is
    // drawn. The store tracks only the job half. No retry closure: see the
    // note on startRender for why sheets cannot be retried.
    const result = await api.sheets.generate(projectId, input);
    const job = result.job;
    if (job === null || job === undefined) {
      throw new Error('Sheet generation did not return a job to track.');
    }
    get().track(projectId, job);
    return toJobDTO(job);
  },

  startExport: async (projectId, kind, params = {}) => {
    const job = await api.exports.create(projectId, { kind, params });
    get().track(projectId, job, () => api.exports.create(projectId, { kind, params }));
    return toJobDTO(job);
  },

  // ── Card actions (JobsSlice) ───────────────────────────────────────────

  cancel: async (jobId) => {
    const runtime = runtimes.get(jobId);
    const kind = runtime?.kind ?? findKind(get().byProject, jobId);
    if (kind === null) throw unknownJob(jobId);

    if (kind === 'solver') {
      const job = await api.solver.cancel(jobId);
      applyJob(set, runtime?.projectId ?? findProject(get().byProject, jobId), job);
    } else if (kind === 'render') {
      const job = await api.renders.cancel(jobId);
      applyJob(set, runtime?.projectId ?? findProject(get().byProject, jobId), job);
    } else {
      // §11 defines no cancel for the drawings worker. Saying so beats a button
      // that appears to work and quietly does nothing.
      throw new AppError({
        code: 'cancel_unsupported',
        message: `A ${KIND_NOUN[kind]} can't be cancelled once it has started.`,
        action: 'It finishes in a few minutes — you can keep working while it runs.',
      });
    }
    stopRuntime(jobId);
  },

  retry: async (jobId) => {
    const runtime = runtimes.get(jobId);
    if (!runtime?.resubmit) {
      throw new AppError({
        code: 'retry_unavailable',
        message: "This job can't be retried from here.",
        action:
          'Start it again from the tab that launched it — a render needs the 3D view it was taken from.',
      });
    }
    const job = await runtime.resubmit();
    // A retry is a new job id; the failed row stays until dismissed so the
    // reason it failed is still readable next to the new attempt.
    get().track(runtime.projectId, job, runtime.resubmit);
  },

  dismiss: (jobId) => {
    stopRuntime(jobId);
    set((s) => {
      const next: Record<string, readonly JobDTO[]> = {};
      for (const [projectId, list] of Object.entries(s.byProject)) {
        next[projectId] = list.filter((j) => j.id !== jobId);
      }
      return { byProject: next };
    });
  },

  clearError: () => set({ error: null }),
}));

// ---------------------------------------------------------------------------
// Helpers that need `set`
// ---------------------------------------------------------------------------

type SetState = (partial: Partial<JobsState> | ((state: JobsState) => Partial<JobsState>)) => void;

function applyJob(set: SetState, projectId: string | null, job: Job): void {
  if (projectId === null) return;
  set((s) => ({ byProject: upsert(s.byProject, projectId, toJobDTO(job)) }));
}

/** Re-read a finished job so the row carries its result, not just its status. */
async function refetch(
  projectId: string,
  jobId: string,
  kind: JobKind,
  set: SetState,
): Promise<void> {
  try {
    const job =
      kind === 'solver'
        ? await api.solver.get(jobId)
        : kind === 'render'
          ? await api.renders.get(jobId)
          : await api.exports.get(jobId);
    set((s) => ({ byProject: upsert(s.byProject, projectId, toJobDTO(job)) }));
  } catch {
    // The terminal event already told us how it ended; the extra detail is a
    // nicety and losing it is not worth an error toast.
  }
}

/** Queue position, when the worker put one in the event payload. */
function readQueuePosition(event: ProgressEvent): number | null {
  const raw = event.data.queuePosition;
  return typeof raw === 'number' && Number.isInteger(raw) ? raw : null;
}

function findKind(
  byProject: Readonly<Record<string, readonly JobDTO[]>>,
  jobId: string,
): JobKind | null {
  for (const list of Object.values(byProject)) {
    const hit = list.find((j) => j.id === jobId);
    if (hit) return hit.kind;
  }
  return null;
}

function findProject(
  byProject: Readonly<Record<string, readonly JobDTO[]>>,
  jobId: string,
): string | null {
  for (const [projectId, list] of Object.entries(byProject)) {
    if (list.some((j) => j.id === jobId)) return projectId;
  }
  return null;
}

function unknownJob(jobId: string): AppError {
  return new AppError({
    code: 'not_found',
    message: "That job isn't on this screen any more.",
    action: 'Reload the project to see its current jobs.',
    data: { jobId },
  });
}

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

/**
 * Stable empty list. `?? []` here minted a FRESH array identity on every store
 * read, and `useSyncExternalStore` treats an unstable snapshot as a change —
 * an infinite re-render loop ("Maximum update depth exceeded") on any project
 * whose jobs entry has not hydrated yet. Latent since authorship: nothing
 * MOUNTED `useSolverJob` until the options overlay landed, so the selector had
 * never executed inside React (bug pattern #4, the selector edition).
 */
const NO_JOBS: readonly JobDTO[] = [];

export const selectJobsFor =
  (projectId: string) =>
  (s: JobsState): readonly JobDTO[] =>
    s.byProject[projectId] ?? NO_JOBS;

export const selectActiveJobsFor =
  (projectId: string) =>
  (s: JobsState): readonly JobDTO[] =>
    (s.byProject[projectId] ?? NO_JOBS).filter((j) => !isTerminal(j.status));

export const selectHasActiveJob =
  (projectId: string) =>
  (s: JobsState): boolean =>
    (s.byProject[projectId] ?? NO_JOBS).some((j) => !isTerminal(j.status));

export const selectJobsError = (s: JobsState): ProblemDetail | null => s.error;
