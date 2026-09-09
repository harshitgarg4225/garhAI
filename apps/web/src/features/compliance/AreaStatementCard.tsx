/**
 * AreaStatementCard — the numbers the municipal area statement prints, on the tab.
 *
 * Every figure here comes from `report.areas`, which the rules engine built from
 * the SAME rule results the chips show (garh_rules/areas.py). The tab does no
 * arithmetic of its own: FAR consumed vs allowed, coverage, per-storey built-up
 * and setbacks are quoted, not recomputed, so the sheet and the tab cannot
 * disagree (CLAUDE.md: "one source for compliance numbers").
 *
 * Rules an architect accepted with a reason are listed under the statement, as
 * the annexure prints them: a reviewer sees the acknowledgement, not a silent
 * pass.
 */

import { Badge, Card, DataRow } from '@garh/ui';
import type { UnitsDisplay } from '@garh/model';

import type { ComplianceAreas } from '../../lib/schemas';
import { formatComplianceValue } from './report';
import type { ComplianceValueVM } from '../../components';

export interface AreaStatementCardProps {
  areas: ComplianceAreas;
  units: UnitsDisplay;
  overriddenRuleIds?: readonly string[] | undefined;
}

const SETBACK_STATUS: Readonly<
  Record<string, { label: string; tone: 'pass' | 'fail' | 'neutral' }>
> = {
  ok: { label: 'OK', tone: 'pass' },
  short: { label: 'Short', tone: 'fail' },
  not_regulated: { label: 'Not regulated', tone: 'neutral' },
};

export function AreaStatementCard({
  areas,
  units,
  overriddenRuleIds = [],
}: AreaStatementCardProps): JSX.Element {
  const fmt = (value: ComplianceValueVM | undefined, unit: string | null | undefined): string =>
    formatComplianceValue(value, unit ?? undefined, units);

  return (
    <Card data-testid="area-statement">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Area statement</h2>
        <span className="text-2xs text-ink-subtle">
          From the rules engine — the same figures the sheet prints
        </span>
      </div>

      {/* The printable rows, as the engine ordered them. */}
      <div className="px-4">
        {areas.rows.map((row) => (
          <DataRow
            key={row.key}
            label={
              <span>
                {row.label}
                {row.note === null ? null : (
                  <span className="ml-1.5 text-2xs font-normal text-ink-subtle">{row.note}</span>
                )}
              </span>
            }
            value={
              <span className="font-mono" data-testid={`area-row-${row.key}`}>
                {fmt(row.value, row.unit)}
                {row.allowed === null || row.allowed === undefined ? null : (
                  <span className="ml-2 font-sans text-ink-subtle">
                    {row.limitLabel ?? (row.kind === 'allowance' ? 'Permissible' : 'Required')}{' '}
                    <span className="font-mono text-ink">{fmt(row.allowed, row.unit)}</span>
                  </span>
                )}
              </span>
            }
          />
        ))}
      </div>

      {/* Setbacks per edge: provided vs required. */}
      {areas.setbacks.length > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-subtle">
            Setbacks
          </h3>
          <table className="w-full text-xs">
            <thead className="text-left text-ink-subtle">
              <tr>
                <th className="py-1 font-medium">Edge</th>
                <th className="py-1 font-medium">Provided</th>
                <th className="py-1 font-medium">Required</th>
                <th className="py-1 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {areas.setbacks.map((s) => {
                const status = SETBACK_STATUS[s.status] ?? SETBACK_STATUS.not_regulated;
                return (
                  <tr key={s.edgeIndex} className="border-t border-line">
                    <td className="py-1 capitalize text-ink">{s.role}</td>
                    <td className="py-1 font-mono text-ink">{fmt(s.providedMm, 'mm')}</td>
                    <td className="py-1 font-mono text-ink">
                      {s.requiredMm === null ? '—' : fmt(s.requiredMm, 'mm')}
                    </td>
                    <td className="py-1">
                      {status === undefined ? null : (
                        <Badge tone={status.tone}>
                          {status.label}
                          {s.shortfallMm > 0 ? ` by ${fmt(s.shortfallMm, 'mm')}` : ''}
                        </Badge>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {areas.warnings.length > 0 ? (
        <ul className="list-disc space-y-1 border-t border-line px-4 py-3 pl-9 text-xs text-warn-ink">
          {areas.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}

      {overriddenRuleIds.length > 0 ? (
        <div className="border-t border-line px-4 py-3 text-xs" data-testid="area-overridden">
          <span className="font-semibold text-ink">Rules accepted with a reason: </span>
          <span className="text-ink-muted">{overriddenRuleIds.join(', ')}</span>
        </div>
      ) : null}
    </Card>
  );
}

export default AreaStatementCard;
