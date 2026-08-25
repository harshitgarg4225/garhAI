/**
 * The plot feature's binding to the stores and the API. Components in this
 * folder import THESE hooks, never `useModelStore` directly — one file knows
 * how the feature reads and writes, and the op path is visibly the only write
 * path (golden rule 1).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { JsonObject, PlotDoc, Polygon, Road, UnitsDisplay } from '@garh/model';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { useModelStore, type DispatchResult } from '../../stores/model';
import {
  boundaryGroupOps,
  boundaryOp,
  northOp,
  regProfileOp,
  roadOp,
  type BoundarySource,
} from './ops';
import { rulepackDocSchema, type RulepackDoc } from './rules';

// ---------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------

/** The plot document. A stable reference — it only changes when an op folds. */
export function usePlotDoc(): PlotDoc {
  return useModelStore((s) => s.doc.plot);
}

/** Project display units, from the folded document (the drawings' authority). */
export function useUnitsDisplay(): UnitsDisplay {
  return useModelStore((s) => s.doc.house.meta.unitsDisplay);
}

/** True once the model finished hydrating — skeletons before, editor after. */
export function useModelReady(): boolean {
  return useModelStore((s) => s.status === 'ready');
}

// ---------------------------------------------------------------------------
// Writing — every mutation is an op dispatch with an undo-toast label
// ---------------------------------------------------------------------------

export interface PlotActions {
  /** Replace the boundary; carries the (possibly renumbered) roads across. */
  setBoundary: (
    nextPolygon: Polygon,
    options?: {
      label?: string;
      source?: BoundarySource;
      /** Roads as they should read AFTER the change. Defaults to current roads. */
      nextRoads?: readonly Road[];
    },
  ) => DispatchResult;
  setNorth: (deg: number) => DispatchResult;
  setRoad: (edgeIndex: number, widthMm: number | null, name?: string | null) => DispatchResult;
  setRegProfile: (cityPack: string | null, overrides: JsonObject, label?: string) => DispatchResult;
}

export function usePlotActions(): PlotActions {
  // `dispatch` has a stable identity in the zustand store; reading it once and
  // memoizing keeps these callbacks referentially stable for memoized children.
  const dispatch = useModelStore((s) => s.dispatch);

  return useMemo<PlotActions>(
    () => ({
      setBoundary: (nextPolygon, options = {}) => {
        const prevRoads = useModelStore.getState().doc.plot.roads;
        const nextRoads = options.nextRoads ?? prevRoads;
        const ops =
          nextPolygon.length === 0
            ? [boundaryOp(nextPolygon, options.source ?? 'manual')]
            : boundaryGroupOps(prevRoads, nextPolygon, nextRoads, options.source ?? 'manual');
        return dispatch(ops, { label: options.label ?? 'Plot boundary' });
      },
      setNorth: (deg) => dispatch([northOp(deg)], { label: 'North direction' }),
      setRoad: (edgeIndex, widthMm, name) =>
        dispatch([roadOp(edgeIndex, widthMm, name ?? null)], {
          label: widthMm === null ? 'Road removed' : 'Road on plot edge',
        }),
      setRegProfile: (cityPack, overrides, label) =>
        dispatch([regProfileOp(cityPack, overrides)], {
          label: label ?? 'Regulatory profile',
        }),
    }),
    [dispatch],
  );
}

// ---------------------------------------------------------------------------
// Rule packs (GET /rulepacks, GET /rulepacks/{id})
// ---------------------------------------------------------------------------

export interface RulepackSummaryVM {
  readonly id: string;
  readonly name: string;
  readonly version: string;
  readonly confidence: string;
}

export type Loadable<T> =
  | { readonly state: 'loading' }
  | { readonly state: 'error'; readonly error: AppError }
  | { readonly state: 'ready'; readonly data: T };

/**
 * Full-pack cache. Packs are versioned files served with cache headers; within
 * a session they are immutable, so one fetch per pack id is correct as well as
 * cheap. The map holds promises so concurrent mounts share one request.
 */
const packCache = new Map<string, Promise<RulepackDoc>>();

function fetchRulepack(packId: string, signal?: AbortSignal): Promise<RulepackDoc> {
  const cached = packCache.get(packId);
  if (cached !== undefined) return cached;
  const request = api.http
    .request<RulepackDoc>({
      path: `/rulepacks/${encodeURIComponent(packId)}`,
      parse: (data: unknown) => rulepackDocSchema.parse(data),
      ...(signal === undefined ? {} : { signal }),
    })
    .catch((err: unknown) => {
      // A failed fetch must not poison the cache — the retry button refetches.
      packCache.delete(packId);
      throw err;
    });
  packCache.set(packId, request);
  return request;
}

/** One rule pack, verbatim as authored, with loading/error/ready states. */
export function useRulepack(packId: string | null): Loadable<RulepackDoc> & { retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Loadable<RulepackDoc>>({ state: 'loading' });

  useEffect(() => {
    if (packId === null) {
      setState({ state: 'loading' });
      return undefined;
    }
    let cancelled = false;
    setState({ state: 'loading' });
    fetchRulepack(packId)
      .then((data) => {
        if (!cancelled) setState({ state: 'ready', data });
      })
      .catch((err: unknown) => {
        const error = AppError.from(err);
        if (!cancelled && !error.isAborted) setState({ state: 'error', error });
      });
    return () => {
      cancelled = true;
    };
  }, [packId, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);
  return { ...state, retry };
}

/** The pack catalogue for the preset selector. */
export function useRulepackList(): Loadable<RulepackSummaryVM[]> & { retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Loadable<RulepackSummaryVM[]>>({ state: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ state: 'loading' });
    api.catalog
      .rulepacks()
      .then((page) => {
        if (cancelled) return;
        setState({
          state: 'ready',
          data: page.items.map((p) => ({
            id: p.id,
            name: p.name,
            version: p.version,
            confidence: p.confidence,
          })),
        });
      })
      .catch((err: unknown) => {
        const error = AppError.from(err);
        if (!cancelled && !error.isAborted) setState({ state: 'error', error });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);
  return { ...state, retry };
}
