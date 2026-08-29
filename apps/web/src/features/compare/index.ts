/**
 * Version compare (C-8) — the op log has always branched; this is what surfaces it.
 *
 * `ComparePanel` picks the two versions and lists what differs; `CompareOverlay` draws
 * the same changes on the plan, which is the half that makes it an answer rather than a
 * list.
 */

export { ComparePanel } from './ComparePanel';
export { CompareOverlay } from './CompareOverlay';
export { loadCompare } from './api';
export { compareBoxesForStorey, useCompareStore } from './store';
export type { CompareState } from './store';
