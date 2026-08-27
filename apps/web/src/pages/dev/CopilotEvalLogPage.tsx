/**
 * CopilotEvalLogPage — the §10 eval-log surface, and an honest account of what
 * it can and cannot show.
 *
 * §10 says: *"Log {command, ops, applied|rejected|invalid} for the eval set."*
 * That logging is real and it happens on the server, in two halves:
 *
 *   · `POST /projects/:id/copilot`          → one `copilot.command` line
 *                                             {command (PII-masked), opTypes,
 *                                              opCount, outcome}
 *   · `POST /projects/:id/copilot/decision` → one `copilot.decision` line
 *                                             {command, outcome: applied|rejected}
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS PAGE IS NOT
 * ════════════════════════════════════════════════════════════════════════════
 * It is **not** a view of that server-side log, because there is no endpoint to
 * read it. Those lines go to the structured logger (stdout → whatever ships
 * logs in the deployment), and §11 defines no `GET /copilot/log` or aggregate.
 * Inventing one here would have meant inventing an API surface — a tenancy
 * question (whose commands? every firm's?), a retention question, and a PII
 * question (the log is masked precisely because commands are free text) — all
 * decided by a UI page. That is the wrong place to decide them.
 *
 * ┌─ FOR THE CLOSER ──────────────────────────────────────────────────────────┐
 * │ To make this page show the REAL corpus, the API needs a read side:        │
 * │   `GET /projects/:id/copilot/log?since=` → {items:[{at, command, outcome, │
 * │   opCount, decision}], nextCursor}, firm-scoped like every other list,    │
 * │   admin-only, reading the same masked text the logger writes.             │
 * │ Until it exists this page is honest about its scope rather than empty.    │
 * └───────────────────────────────────────────────────────────────────────────┘
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IT IS
 * ════════════════════════════════════════════════════════════════════════════
 * A tally of **this browser session's** turns, read straight from
 * `useCopilotStore` — the same state the panel renders, so it cannot disagree
 * with what the architect saw. That is genuinely useful for the thing the page
 * exists for: running the 40-command corpus by hand against a real stack and
 * seeing the outcome split without reading a docker log. It is DEV-only
 * (`import.meta.env.DEV`, statically false in production builds).
 *
 * It also prints the same table to the console on demand, because "console
 * table" is what the task description asked for and copy-pasting from
 * `console.table` into a bug report is easier than from the DOM.
 */

import { useMemo } from 'react';
import { Link } from 'react-router-dom';

import { Badge, Button, Card, CardBody, CardHeader } from '@garh/ui';

import { PageBody, PageHeader } from '../../components';
import { selectTurns, useCopilotStore } from '../../features/copilot';
import type { CopilotTurn, CopilotTurnStatus } from '../../features/copilot';

/**
 * Turn status → §10's outcome vocabulary.
 *
 * The mapping is deliberately lossy in one direction and not the other: every
 * server outcome class is represented, and the client-only statuses
 * (`cancelled`, transport `error`) get their own bucket rather than being
 * folded into `invalid`. A network blip is not a model failure, and a corpus
 * that conflated them would teach the wrong lesson.
 */
const OUTCOME_OF: Readonly<Record<CopilotTurnStatus, string>> = {
  thinking: 'in flight',
  ready: 'ops (undecided)',
  applied: 'ops → applied',
  rejected: 'ops → rejected',
  cannot: 'cannotDo',
  clarify: 'needsClarification',
  error: 'error (client/transport)',
  cancelled: 'cancelled by user',
};

const TONE_OF: Readonly<Record<string, 'brand' | 'pass' | 'warn' | 'fail' | 'neutral'>> = {
  'ops → applied': 'pass',
  'ops → rejected': 'warn',
  'ops (undecided)': 'brand',
  cannotDo: 'neutral',
  needsClarification: 'neutral',
  'error (client/transport)': 'fail',
  'cancelled by user': 'neutral',
  'in flight': 'brand',
};

interface Row {
  readonly command: string;
  readonly outcome: string;
  readonly opCount: number;
  readonly attempts: number | null;
  readonly selfCorrected: boolean;
  readonly dryRunMs: number | null;
  readonly rulesChecked: boolean | null;
  readonly at: number;
}

function toRow(turn: CopilotTurn): Row {
  return {
    command: turn.command,
    outcome: OUTCOME_OF[turn.status],
    // On reject the store clears `ops`; the proposal still knows what was
    // offered, which is the number the eval set cares about.
    opCount: turn.proposal?.ops.length ?? turn.ops.length,
    attempts: turn.proposal?.attempts ?? null,
    selfCorrected: turn.proposal?.selfCorrected ?? false,
    dryRunMs: turn.proposal?.dryRunMs ?? null,
    rulesChecked: turn.proposal?.rulesChecked ?? null,
    at: turn.at,
  };
}

export function CopilotEvalLogPage(): JSX.Element {
  const turns = useCopilotStore(selectTurns);

  const rows = useMemo(() => turns.map(toRow), [turns]);

  const counts = useMemo(() => {
    const tally = new Map<string, number>();
    for (const row of rows) tally.set(row.outcome, (tally.get(row.outcome) ?? 0) + 1);
    return [...tally.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  /** Worst observed server-side dry-run fold. §14 budgets it under 10 ms. */
  const worstDryRun = useMemo(() => {
    const values = rows.map((r) => r.dryRunMs).filter((v): v is number => v !== null);
    return values.length === 0 ? null : Math.max(...values);
  }, [rows]);

  if (!import.meta.env.DEV) {
    return (
      <PageBody>
        <PageHeader
          title="Not available"
          description="The copilot eval log is a development-build tool."
        />
        <Link to="/" className="text-sm text-brand">
          Back to your projects
        </Link>
      </PageBody>
    );
  }

  return (
    <PageBody>
      <PageHeader
        title="Copilot eval log"
        description="Outcomes for the commands sent from THIS browser session. The durable corpus lives in the server's structured log — see this file's header."
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                // eslint-disable-next-line no-console -- the point of the page
                console.table(rows);
                // eslint-disable-next-line no-console
                console.table(counts.map(([outcome, n]) => ({ outcome, n })));
              }}
            >
              Print to console
            </Button>
            <Button variant="ghost" size="sm" onClick={() => useCopilotStore.getState().clear()}>
              Clear session
            </Button>
          </>
        }
      />

      <Card className="mb-4">
        <CardHeader title="Outcome counts" />
        <CardBody>
          {counts.length === 0 ? (
            <p className="text-sm text-ink-muted">
              No commands yet in this session. Open a project, press{' '}
              <kbd className="rounded border border-line px-1">/</kbd> and send one — the corpus in{' '}
              <code>fixtures/llm/copilot-commands/commands.json</code> is 40 of them.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {counts.map(([outcome, n]) => (
                <li key={outcome}>
                  <Badge tone={TONE_OF[outcome] ?? 'neutral'}>
                    {outcome}: {n}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
          {worstDryRun === null ? null : (
            <p className="mt-3 text-xs text-ink-subtle garh-nums">
              Worst server dry-run fold this session: {worstDryRun.toFixed(2)} ms (§14 budget: 10
              ms).
            </p>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Commands" />
        <CardBody>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-ink-subtle">
                <tr>
                  <th className="py-1 pr-3">Command</th>
                  <th className="py-1 pr-3">Outcome</th>
                  <th className="py-1 pr-3 text-right">Ops</th>
                  <th className="py-1 pr-3 text-right">Attempts</th>
                  <th className="py-1 pr-3 text-right">Dry-run</th>
                  <th className="py-1">Rules</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.at}-${row.command}`} className="border-t border-line">
                    <td className="max-w-md truncate py-1.5 pr-3">{row.command}</td>
                    <td className="py-1.5 pr-3">{row.outcome}</td>
                    <td className="py-1.5 pr-3 text-right garh-nums">{row.opCount}</td>
                    <td className="py-1.5 pr-3 text-right garh-nums">
                      {row.attempts ?? '—'}
                      {row.selfCorrected ? ' ✎' : ''}
                    </td>
                    <td className="py-1.5 pr-3 text-right garh-nums">
                      {row.dryRunMs === null ? '—' : `${row.dryRunMs.toFixed(2)} ms`}
                    </td>
                    <td className="py-1.5">
                      {row.rulesChecked === null ? '—' : row.rulesChecked ? 'checked' : 'not run'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-ink-subtle">
            &ldquo;not run&rdquo; under Rules means the engine could not evaluate (no plot boundary
            yet) — it is never a pass.
          </p>
        </CardBody>
      </Card>
    </PageBody>
  );
}

export default CopilotEvalLogPage;
