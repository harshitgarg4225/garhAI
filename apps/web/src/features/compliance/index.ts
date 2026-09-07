/**
 * Compliance — the client half of "check compliance continuously".
 *
 * The rules engine lives on the API (`garh_rules`, pure stdlib). This feature
 * owns what the browser adds: the report view model, the "Fix it" op-group
 * computation, and the formatting of what a rule measured.
 */

export { computeAutofix, hasClientAutofix } from './autofix';
export type { AutofixPlan } from './autofix';
export { formatActualVsLimit, formatComplianceValue, toComplianceReport } from './report';
export type { ComplianceCountsVM, ComplianceReportVM } from './report';
