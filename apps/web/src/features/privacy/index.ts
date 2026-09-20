/**
 * privacy — `/settings/privacy`: the firm's audit trail (F-5) and the DPDP
 * export/erasure rights (F-6). The API had all of it with nothing calling it.
 */

export { PrivacySection } from './PrivacySection';
export type { PrivacySectionProps } from './PrivacySection';
export {
  ACTION_PHRASES,
  describeAction,
  describeActor,
  describeEntity,
  formatWhen,
  groupByDay,
  metaPairs,
} from './audit';
export { useAuditTrail, AUDIT_PAGE_SIZE } from './useAuditTrail';
export type { AuditFilters, AuditTrailState } from './useAuditTrail';
