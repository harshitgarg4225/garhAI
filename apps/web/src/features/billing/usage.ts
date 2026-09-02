/**
 * Pure readers over `GET /billing/usage` — the numbers the trial cards show.
 * Kept out of the component file so React Fast Refresh sees only components there,
 * and so a test can pin the copy without rendering.
 */

import type { Usage, UsageLine } from '../../lib/api';

const KIND_LABEL: Record<string, string> = {
  solver: 'Generations',
  render: 'Renders',
  export: 'Exports',
  llm: 'Copilot calls',
};

/** The line for one metered kind, or null when the plan does not meter it. */
export function lineFor(usage: Usage, kind: string): UsageLine | null {
  return usage.lines.find((line) => line.kind === kind) ?? null;
}

export function describeLine(line: UsageLine): string {
  const label = KIND_LABEL[line.kind] ?? line.kind;
  if (line.allowance === null) return `${label}: ${line.used} used this period`;
  return `${label}: ${line.used} of ${line.allowance} used this period`;
}

export function describeSpend(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend?.enforced) return null;
  return `Budget: ${spend.remainingUsd} of ${spend.capUsd} left`;
}
