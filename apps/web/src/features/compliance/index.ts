/**
 * Compliance — the client half of "check compliance continuously".
 *
 * The rules engine lives on the API (`garh_rules`, pure stdlib). This feature
 * owns what the browser adds: the report view model, the "Fix it" op-group
 * computation, the formatting of what a rule measured, the override control,
 * and the Compliance tab's rows, header, area statement and filters.
 */

export { AreaStatementCard } from './AreaStatementCard';
export { ComplianceRow } from './ComplianceRow';
export { OverrideDialog } from './OverrideDialog';
export { ReportHeader } from './ReportHeader';
export { computeAutofix, hasClientAutofix } from './autofix';
export type { AutofixPlan } from './autofix';
export {
  DEFAULT_FILTERS,
  filterIssues,
  groupByStorey,
  packIdsOf,
  packReviewState,
  readRuleOverrides,
  sortBySeverity,
  storeyOfElements,
  storeyOfIssue,
  storeyRefs,
} from './filters';
export type {
  ComplianceFilters,
  ReviewState,
  RuleOverrideRecord,
  SeverityFilter,
  StoreyGroup,
  StoreyRef,
} from './filters';
export { formatActualVsLimit, formatComplianceValue, toComplianceReport } from './report';
export type { ComplianceCountsVM, ComplianceReportVM } from './report';
export { useRuleOverrides } from './useRuleOverrides';
export type { RuleOverrides } from './useRuleOverrides';
