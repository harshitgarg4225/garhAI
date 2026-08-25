/**
 * The one place features/options touches stores and the network (mirrors the
 * useBrief pattern). Components in this feature call these hooks and dispatch
 * nothing themselves.
 *
 * Golden rule 1 everywhere: applying an option is `solver.apply_option`
 * dispatched through the model store — the op, not a bespoke endpoint — so it
 * is one undo step, one autosave unit, one op-log line with `source: 'solver'`.
 *
 * Why the job row is fetched here with its own parser: `lib/schemas.jobSchema`
 * deliberately strips kind-specific fields, and the solver row's `options`
 * list IS the payload this screen exists for. `solverJobDetailSchema` owns
 * that wire shape (see types.ts).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { Op } from '@garh/model';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { subscribeJobEvents } from '../../lib/sse';
import { isTerminal, selectJobsFor, useJobsStore, type JobDTO } from '../../stores/jobs';
import { useModelStore, type DispatchResult } from '../../stores/model';
import {
  INITIAL_THEATER,
  reduceTheater,
  type TheaterState,
} from './theater';
import {
  perFloorParams,
  regenerateOthersParams,
  type SolveRequestParams,
} from './stats';
import {
  readSolveOutcome,
  solverJobDetailSchema,
  type PlanOption,
  type SolveOutcome,
} from './types';

// ---------------------------------------------------------------------------
// The active solver job for a project
// ---------------------------------------------------------------------------

export interface UseSolverJob {
  /** Newest solver job this tab knows about, running or finished. */
  readonly job: JobDTO | null;
  readonly isRunning: boolean;
  /** Start a solve. Returns the job so callers can scroll to the theater. */
  readonly generate: (params?: SolveRequestParams) => Promise<JobDTO>;
}

export function useSolverJob(projectId: string): UseSolverJob {
  const jobs = useJobsStore(selectJobsFor(projectId));
  const startSolve = useJobsStore((s) => s.startSolve);

  const job = useMemo(() => {
    const solverJobs = jobs.filter((j) => j.kind === 'solver');
    if (solverJobs.length === 0) return null;
    // The store sorts running-first then newest-first; index 0 is the one.
    return solverJobs[0] ?? null;
  }, [jobs]);

  const generate = useCallback(
    (params: SolveRequestParams = {}) => startSolve(projectId, params),
    [projectId, startSolve],
  );

  return { job, isRunning: job !== null && !isTerminal(job.status), generate };
}

// ---------------------------------------------------------------------------
// Generation theater state (own SSE subscription — needs the full stream)
// ---------------------------------------------------------------------------

/**
 * The jobs store keeps only the LATEST event per job; the theater needs the
 * whole staged history plus silhouettes, so it holds its own subscription.
 * `lib/sse.ts` replays from seq 0 on connect, so mounting mid-run still
 * renders every stage that already happened.
 */
export function useTheater(jobId: string | null): TheaterState {
  const [state, setState] = useState<TheaterState>(INITIAL_THEATER);

  useEffect(() => {
    setState(INITIAL_THEATER);
    if (jobId === null) return undefined;
    const unsubscribe = subscribeJobEvents({
      jobId,
      kind: 'solver',
      onEvent: (event) => setState((s) => reduceTheater(s, event)),
      onError: (error) => {
        // A retryable drop is not a failure — the stream reconnects and
        // replays. Only a fatal error (404, auth) surfaces, honestly.
        if (!error.retryable) {
          setState((s) =>
            s.done
              ? s
              : {
                  ...s,
                  failure: {
                    message: error.message,
                    action: error.action,
                    discardSummary: null,
                  },
                },
          );
        }
      },
    });
    return unsubscribe;
  }, [jobId]);

  return state;
}

// ---------------------------------------------------------------------------
// The finished outcome (options list) for a terminal job
// ---------------------------------------------------------------------------

export interface UseSolveOutcome {
  readonly outcome: SolveOutcome | null;
  readonly loading: boolean;
  readonly error: AppError | null;
  readonly reload: () => void;
}

export function useSolveOutcome(jobId: string | null, jobStatus: string | null): UseSolveOutcome {
  const [outcome, setOutcome] = useState<SolveOutcome | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    setOutcome(null);
    setError(null);
    if (jobId === null || jobStatus !== 'succeeded') return undefined;

    const controller = new AbortController();
    setLoading(true);
    api.http
      .request({
        path: `/solver-jobs/${encodeURIComponent(jobId)}`,
        parse: (data: unknown) => solverJobDetailSchema.parse(data),
        signal: controller.signal,
      })
      .then((row) => setOutcome(readSolveOutcome(row)))
      .catch((err: unknown) => {
        const appError = AppError.from(err);
        if (!appError.isAborted) setError(appError);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, [jobId, jobStatus, reloadTick]);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  return { outcome, loading, error, reload };
}

// ---------------------------------------------------------------------------
// Actions: apply, lock + regenerate, per-floor, variations
// ---------------------------------------------------------------------------

export interface LockableRoom {
  readonly roomId: string;
  readonly label: string;
  readonly storeyIndex: number;
  readonly locked: boolean;
}

export interface UseOptionActions {
  /** True when applying would replace existing design geometry (confirm first). */
  readonly modelHasGeometry: boolean;
  /** Dispatch `solver.apply_option` as one atomic, undoable group. */
  readonly apply: (option: PlanOption, optionIndex: number, solverJobId: string) => DispatchResult;
  /** Rooms of the CURRENT model, with lock state, for the lock-then-regen UI. */
  readonly lockableRooms: readonly LockableRoom[];
  /** Toggle a room's §5.7 lock — a `room.assign` op, undoable like the rest. */
  readonly setRoomLocked: (roomId: string, locked: boolean) => DispatchResult | null;
  /** Re-solve everything except the locked rooms (§5.7 partial re-solve). */
  readonly regenerateOthers: () => Promise<JobDTO>;
  /** Re-solve one floor; rooms on all other floors are locked automatically. */
  readonly regenerateFloor: (storeyIndex: number) => Promise<JobDTO>;
}

export function useOptionActions(projectId: string): UseOptionActions {
  const startSolve = useJobsStore((s) => s.startSolve);

  // Subscribing to the slices keeps lock toggles and the confirm gate live.
  const walls = useModelStore((s) => s.doc.house.walls);
  const rooms = useModelStore((s) => s.doc.house.rooms);
  const storeys = useModelStore((s) => s.doc.house.storeys);

  const modelHasGeometry = walls.length > 0 || rooms.length > 0;

  const storeyIndexOf = useCallback(
    (storeyId: string): number => {
      const index = storeys.findIndex((s) => s.id === storeyId);
      return index === -1 ? 0 : index;
    },
    [storeys],
  );

  const lockableRooms = useMemo<readonly LockableRoom[]>(
    () =>
      rooms.map((room) => ({
        roomId: room.id,
        label: room.name !== '' ? room.name : room.type,
        storeyIndex: storeyIndexOf(room.storeyId),
        locked: room.locked,
      })),
    [rooms, storeyIndexOf],
  );

  const apply = useCallback(
    (option: PlanOption, optionIndex: number, solverJobId: string): DispatchResult => {
      const store = useModelStore.getState();
      const lockedIds = store.doc.house.rooms.filter((r) => r.locked).map((r) => r.id);
      const op = {
        type: 'solver.apply_option',
        payload: {
          solverJobId,
          optionIndex,
          ops: option.ops,
          ...(lockedIds.length > 0 ? { lockedRoomIds: lockedIds } : {}),
        },
      } as unknown as Op;
      return store.dispatch([op], {
        label: `Plan option ${optionIndex + 1} applied`,
        source: 'solver',
      });
    },
    [],
  );

  const setRoomLocked = useCallback(
    (roomId: string, locked: boolean): DispatchResult | null => {
      const store = useModelStore.getState();
      const room = store.doc.house.rooms.find((r) => r.id === roomId);
      if (room === undefined) return null;
      const op = {
        type: 'room.assign',
        payload: {
          roomId: room.id,
          type: room.type,
          ...(room.name !== '' ? { name: room.name } : {}),
          locked,
        },
      } as unknown as Op;
      return store.dispatch([op], {
        label: locked ? 'Room locked for re-solve' : 'Room unlocked',
        source: 'manual',
      });
    },
    [],
  );

  const regenerateOthers = useCallback((): Promise<JobDTO> => {
    const locked = useModelStore
      .getState()
      .doc.house.rooms.filter((r) => r.locked)
      .map((r) => r.id);
    return startSolve(projectId, regenerateOthersParams(locked));
  }, [projectId, startSolve]);

  const regenerateFloor = useCallback(
    (storeyIndex: number): Promise<JobDTO> => {
      const state = useModelStore.getState();
      const targetStoreyId = state.doc.house.storeys[storeyIndex]?.id ?? null;
      // Everything NOT on the target floor is locked; the floor itself re-solves.
      const locked = state.doc.house.rooms
        .filter((r) => r.storeyId !== targetStoreyId)
        .map((r) => r.id);
      return startSolve(projectId, perFloorParams(storeyIndex, locked));
    },
    [projectId, startSolve],
  );

  return {
    modelHasGeometry,
    apply,
    lockableRooms,
    setRoomLocked,
    regenerateOthers,
    regenerateFloor,
  };
}

