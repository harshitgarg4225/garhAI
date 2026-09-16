/**
 * platform — the platform owner's pages: `/platform/ops` (this folder) and
 * `/platform/fee` (`features/billing`, where the fee's own helpers live).
 *
 * `OpsPage` is deliberately NOT re-exported: `routes.tsx` lazy-loads it by path so
 * the dashboard chunk does not carry a page a handful of people open.
 */

export {
  alarmsFor,
  describeAge,
  describeDuration,
  describeUptime,
  failureRate,
  headline,
  queueWaiting,
  workerVerdict,
} from './ops';
export type { Alarm } from './ops';
export { useOpsStatus, OPS_REFRESH_MS } from './useOpsStatus';
export type { OpsStatusState } from './useOpsStatus';
