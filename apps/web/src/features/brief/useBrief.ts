/**
 * useBrief — the one place the brief feature touches the model store.
 *
 * Golden rule 1: components never mutate state; they dispatch ops. Every
 * control in this feature funnels through {@link UseBrief.update}, which builds
 * a single `brief.update` op (patch + recomputed completeness, optionally the
 * Vastu mode) and dispatches it as one group — one undo step, one autosave
 * unit, one line in the op log.
 *
 * The op is built against the store's CURRENT document at dispatch time
 * (`useModelStore.getState()`), not against the render-time snapshot a stale
 * closure would hold — two quick edits in a row must each patch on top of the
 * other, not on top of what the screen showed when the handler mounted.
 */

import { useCallback, useMemo } from 'react';

import type { JsonObject, VastuMode } from '@garh/model';

import { selectBrief, useModelStore, type DispatchResult } from '../../stores/model';
import { computeCompleteness, type CompletenessResult } from './completeness';
import { briefUpdateOp } from './mergePatch';
import { readBriefData, type BriefData } from './types';

export interface BriefUpdateArgs {
  /** RFC 7386 merge patch on `brief.data`. May be empty when only the mode changes. */
  readonly patch: JsonObject;
  /** Undo-toast copy: "Bedrooms updated". Sentence case, no trailing period. */
  readonly label: string;
  readonly vastuMode?: VastuMode | undefined;
}

export interface UseBrief {
  /** The typed view over the CURRENT brief data (optimistic document). */
  readonly data: BriefData;
  /** Raw `brief.data` — the free-text screen diffs against this. */
  readonly rawData: JsonObject;
  readonly vastuMode: VastuMode;
  /** Live completeness of the current data (not the stored stamp). */
  readonly completeness: CompletenessResult;
  /** True once the project document has loaded and edits will stick. */
  readonly ready: boolean;
  /** Dispatch one `brief.update` group. Returns the store's verdict. */
  readonly update: (args: BriefUpdateArgs) => DispatchResult;
}

export function useBrief(): UseBrief {
  const brief = useModelStore(selectBrief);
  const ready = useModelStore((s) => s.status === 'ready');

  const data = useMemo(() => readBriefData(brief.data), [brief.data]);
  const completeness = useMemo(() => computeCompleteness(brief.data), [brief.data]);

  const update = useCallback((args: BriefUpdateArgs): DispatchResult => {
    const store = useModelStore.getState();
    const op = briefUpdateOp(store.doc.brief, args.patch, {
      ...(args.vastuMode === undefined ? {} : { vastuMode: args.vastuMode }),
    });
    return store.dispatch([op], { label: args.label, source: 'manual' });
  }, []);

  return { data, rawData: brief.data, vastuMode: brief.vastuMode, completeness, ready, update };
}
