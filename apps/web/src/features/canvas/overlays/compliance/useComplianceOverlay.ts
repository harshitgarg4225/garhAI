/**
 * useComplianceOverlay — the debounced compliance run, mapped for the canvas.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHERE THE DEBOUNCE LIVES, AND WHY THERE ARE TWO
 * ────────────────────────────────────────────────────────────────────────────
 * §14 caps the compliance re-check at "≤500 ms debounce". `useLiveCompliance`
 * (pages/) already implements that against the API at 450 ms, keyed on
 * `baseIdx` — the server's confirmed op index — because the engine evaluates
 * the SERVER's op log, not the browser's optimistic document.
 *
 * This hook does not re-implement that. It takes the debounced result as its
 * input and applies a SECOND, shorter debounce (150 ms) to the mapping and the
 * re-render. That is not belt-and-braces:
 *
 *   · the input can change while its results are unchanged (a re-check that
 *     returns the same report still produces a new array identity), and
 *     re-mapping 40 chips against the document on every one of those is work
 *     nobody sees;
 *   · the mapping ALSO depends on the document, which changes optimistically on
 *     every keystroke of a dimension edit. Without a debounce here, dragging a
 *     wall would re-resolve every chip's bbox per pointer frame.
 *
 * Total worst case: 450 + 150 = 600 ms from an edit to a chip moving. That is
 * over the §14 number, so the mapping debounce is BYPASSED when the issue set
 * itself changed (the case the budget is about) and applied only when the
 * document moved under a stable report. {@link COMPLIANCE_MAP_DEBOUNCE_MS}.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * NON-BLOCKING, ALWAYS (golden rule 5)
 * ────────────────────────────────────────────────────────────────────────────
 * Nothing here can prevent an edit, and the hook never throws. `issues === null`
 * ("nothing has been checked") stays distinct from `issues === []` ("checked,
 * all clear") all the way to the strip, because conflating them tells an
 * architect their plan passed when nobody looked at it.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import type { HouseModel } from '@garh/model';

import type { ComplianceIssueVM, ComplianceResultStatus } from '../../../../components/types';
import {
  complianceCounts,
  mapComplianceChips,
  markersFor,
  type ComplianceChipVM,
  type ComplianceMarker,
} from './mapping';

/**
 * How long the MAPPING waits after a document change that did not change the
 * report. Deliberately short: it exists to coalesce a drag, not to delay news.
 */
export const COMPLIANCE_MAP_DEBOUNCE_MS = 150;

/** §14's cap, restated here so a drift in `useLiveCompliance` is visible. */
export const COMPLIANCE_DEBOUNCE_BUDGET_MS = 500;

export interface ComplianceOverlayInput {
  /** From `useLiveCompliance`. `null` means nothing has been evaluated yet. */
  readonly issues: readonly ComplianceIssueVM[] | null;
  readonly checking: boolean;
  readonly house: HouseModel;
  readonly activeStoreyId: string | null;
  /** Which statuses the strip shows. Defaults to fail + warn. */
  readonly statuses?: readonly ComplianceResultStatus[] | undefined;
}

export interface ComplianceOverlay {
  /** Sorted, resolved chips. `null` when nothing has been checked. */
  readonly chips: readonly ComplianceChipVM[] | null;
  /** On-canvas markers for the active storey. */
  readonly markers: readonly ComplianceMarker[];
  readonly counts: { fail: number; warn: number; pass: number };
  readonly checking: boolean;
  /** True once a check has run — the strip's empty state depends on it. */
  readonly evaluated: boolean;
}

export function useComplianceOverlay(input: ComplianceOverlayInput): ComplianceOverlay {
  const { issues, checking, house, activeStoreyId, statuses } = input;

  // The document the mapping is allowed to use. Lags `house` by the debounce
  // when only the document changed; jumps to it immediately when the report did.
  const [mapHouse, setMapHouse] = useState<HouseModel>(house);
  const lastIssues = useRef<readonly ComplianceIssueVM[] | null>(issues);

  useEffect(() => {
    if (lastIssues.current !== issues) {
      // New answer from the engine — show it now. This is the path §14 budgets.
      lastIssues.current = issues;
      setMapHouse(house);
      return undefined;
    }
    if (mapHouse === house) return undefined;
    const timer = setTimeout(() => setMapHouse(house), COMPLIANCE_MAP_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [issues, house, mapHouse]);

  const chips = useMemo(() => {
    if (issues === null) return null;
    return mapComplianceChips(issues, mapHouse, statuses === undefined ? {} : { statuses });
  }, [issues, mapHouse, statuses]);

  const markers = useMemo(
    () => (chips === null ? [] : markersFor(chips, activeStoreyId)),
    [chips, activeStoreyId],
  );

  const counts = useMemo(
    () => (issues === null ? { fail: 0, warn: 0, pass: 0 } : complianceCounts(issues)),
    [issues],
  );

  return { chips, markers, counts, checking, evaluated: issues !== null };
}
