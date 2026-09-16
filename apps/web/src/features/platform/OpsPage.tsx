/**
 * OpsPage — `/platform/ops`, the platform owner's one operational page.
 *
 * Reading order is the operator's order: alarms first (Sentry off, a worker
 * missing, dead letters, a schema behind the code), then the queues, the workers
 * with their heartbeats, the 24-hour job counts and percentiles, the providers in
 * force and the migration head. Everything comes from one `GET /admin/ops`,
 * re-read every 30 s while the tab is open.
 *
 * The gate is the server's: the route answers 403 for anyone not on
 * `PLATFORM_OWNER_EMAILS`, and this page shows that refusal as a panel rather than
 * pretending the page does not exist. The dashboard's link appears only for owners
 * (`BillingLinks` reads `canSet` from the fee endpoint), which is a courtesy — the
 * 403 is the control.
 */

import type { JSX } from 'react';
import { Link } from 'react-router-dom';

import { Badge, Button, Card, CardBody, CardHeader, DataRow } from '@garh/ui';

import { AppShell, PageBody, PageHeader, ProblemPanel, toProblem } from '../../components';
import { api, type ApiClient, type OpsJobKind, type OpsQueue, type OpsWorker } from '../../lib/api';
import { useSessionStore } from '../../stores/session';
import {
  alarmsFor,
  describeAge,
  describeDuration,
  describeUptime,
  failureRate,
  headline,
  queueWaiting,
  workerVerdict,
} from './ops';
import { OPS_REFRESH_MS, useOpsStatus } from './useOpsStatus';

export interface OpsPageProps {
  /** Injected by tests; the app uses the singleton. */
  readonly client?: ApiClient | undefined;
  /** Auto-refresh cadence; tests pass 0 to switch it off. */
  readonly refreshMs?: number | undefined;
}

const STATUS_ORDER = ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;

function QueueRow({ queue }: { queue: OpsQueue }): JSX.Element {
  const waiting = queueWaiting(queue);
  return (
    <tr data-testid={`queue-${queue.worker}`}>
      <th scope="row" className="py-1.5 pr-3 text-left font-medium text-ink">
        {queue.worker}
        <span className="block font-mono text-[11px] text-ink-subtle">{queue.name}</span>
      </th>
      <td className="py-1.5 pr-3 tabular-nums">{waiting}</td>
      <td className="py-1.5 pr-3 tabular-nums">{queue.processing}</td>
      <td className="py-1.5 pr-3 tabular-nums">
        {queue.dead > 0 ? <Badge tone="fail">{queue.dead}</Badge> : '0'}
      </td>
    </tr>
  );
}

function WorkerRow({ worker }: { worker: OpsWorker }): JSX.Element {
  const verdict = workerVerdict(worker);
  return (
    <tr data-testid={`worker-${worker.instance}`}>
      <th scope="row" className="py-1.5 pr-3 text-left font-medium text-ink">
        {worker.worker}
        <span className="block font-mono text-[11px] text-ink-subtle">{worker.instance}</span>
      </th>
      <td className="py-1.5 pr-3">
        <Badge tone={verdict.tone} dot>
          {verdict.label}
        </Badge>
      </td>
      <td className="py-1.5 pr-3 tabular-nums">{describeAge(worker.ageSeconds)}</td>
      <td className="py-1.5 pr-3 tabular-nums">
        {worker.inFlight}/{worker.concurrency}
      </td>
      <td className="py-1.5 pr-3 tabular-nums">
        {worker.jobsSucceeded} ok · {worker.jobsFailed} failed
      </td>
      <td className="py-1.5 pr-3 tabular-nums">
        {describeDuration(worker.p50DurationMs)} / {describeDuration(worker.p95DurationMs)}
      </td>
      <td className="py-1.5 pr-3">{describeUptime(worker.uptimeSeconds)}</td>
      <td className="py-1.5 pr-3">
        {worker.providerRender ?? '—'} · {worker.providerLlm ?? '—'} ·{' '}
        <span className={worker.sentry === 'on' ? 'text-pass-ink' : 'text-fail'}>
          sentry {worker.sentry}
        </span>
      </td>
    </tr>
  );
}

function JobRow({ job }: { job: OpsJobKind }): JSX.Element {
  const rate = failureRate(job);
  return (
    <tr data-testid={`jobs-${job.kind}`}>
      <th scope="row" className="py-1.5 pr-3 text-left font-medium text-ink">
        {job.kind}
      </th>
      {STATUS_ORDER.map((status) => (
        <td key={status} className="py-1.5 pr-3 tabular-nums">
          {job.counts[status] ?? 0}
        </td>
      ))}
      <td className="py-1.5 pr-3 tabular-nums">
        {rate === null ? '—' : <span className={rate > 0 ? 'text-warn-ink' : ''}>{rate}%</span>}
      </td>
      <td className="py-1.5 pr-3 tabular-nums">
        {describeDuration(job.p50Ms)} / {describeDuration(job.p95Ms)}
      </td>
      <td className="py-1.5 pr-3 tabular-nums">{describeDuration(job.maxMs)}</td>
    </tr>
  );
}

const TABLE = 'w-full border-collapse text-sm';
const HEAD =
  'border-b border-line text-left text-xs font-medium uppercase tracking-wide text-ink-muted';

export function OpsPage({ client = api, refreshMs = OPS_REFRESH_MS }: OpsPageProps): JSX.Element {
  const firm = useSessionStore((s) => s.firm);
  const user = useSessionStore((s) => s.user);
  const signOut = useSessionStore((s) => s.signOut);
  const { status, loading, error, refresh, fetchedAt } = useOpsStatus(client, refreshMs);

  const alarms = status === null ? [] : alarmsFor(status);

  return (
    <AppShell
      firmName={firm?.name}
      userName={user?.name}
      onSignOut={() => void signOut()}
      renderHomeLink={({ className, children }) => (
        <Link to="/" className={className}>
          {children}
        </Link>
      )}
    >
      <PageBody className="max-w-5xl">
        <PageHeader
          title="Operations"
          description="What this deployment is running on right now: queues, workers, jobs, providers, error tracking and the schema head."
          actions={
            <span className="flex items-center gap-2">
              <Link
                to="/platform/fee"
                className="text-sm text-brand-ink underline underline-offset-2"
              >
                Platform fee
              </Link>
              <Button size="sm" iconLeft="refresh" loading={loading} onClick={refresh}>
                Refresh
              </Button>
            </span>
          }
        />

        {error !== null && status === null ? (
          <ProblemPanel problem={toProblem(error)} onRetry={refresh} />
        ) : status === null ? (
          <Card aria-busy="true">
            <CardBody className="pt-4 text-sm text-ink-muted">Reading the deployment…</CardBody>
          </Card>
        ) : (
          <>
            <p className="mb-4 text-sm text-ink-muted" data-testid="ops-as-of">
              As of {fetchedAt === null ? '—' : fetchedAt.toLocaleTimeString('en-IN')} · api{' '}
              {status.version}
              {status.release === null ? '' : ` (${status.release})`} · {status.env} · re-read every{' '}
              {Math.round(refreshMs / 1000) || '—'} s
              {error !== null ? ` · last refresh failed: ${error.message}` : ''}
            </p>

            <Card
              className={`mb-4 ${alarms.length === 0 ? 'border-pass' : 'border-fail'}`}
              aria-label="Findings"
            >
              <CardHeader
                title={headline(status, alarms)}
                actions={
                  <Badge tone={alarms.length === 0 ? 'pass' : 'fail'}>
                    {alarms.length === 0 ? 'all clear' : `${alarms.length} to act on`}
                  </Badge>
                }
              />
              {alarms.length === 0 ? null : (
                <CardBody>
                  <ul className="flex flex-col gap-3" data-testid="ops-alarms">
                    {alarms.map((alarm) => (
                      <li key={alarm.key} data-testid={`alarm-${alarm.key}`} role="alert">
                        <p className="text-sm font-medium text-fail">{alarm.title}</p>
                        <p className="text-sm text-ink-muted">{alarm.detail}</p>
                        <p className="text-sm text-ink">{alarm.action}</p>
                      </li>
                    ))}
                  </ul>
                </CardBody>
              )}
            </Card>

            <Card className="mb-4" aria-label="Queues">
              <CardHeader
                title="Queues"
                description="Waiting = pending + delayed retries. Dead letters failed every retry."
              />
              <CardBody>
                <div className="overflow-x-auto">
                  <table className={TABLE}>
                    <thead>
                      <tr className={HEAD}>
                        <th scope="col" className="py-1.5 pr-3">
                          Queue
                        </th>
                        <th scope="col" className="py-1.5 pr-3">
                          Waiting
                        </th>
                        <th scope="col" className="py-1.5 pr-3">
                          Processing
                        </th>
                        <th scope="col" className="py-1.5 pr-3">
                          Dead
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {status.queues.map((queue) => (
                        <QueueRow key={queue.name} queue={queue} />
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-xs text-ink-muted">
                  {status.exportJobsLive} export job{status.exportJobsLive === 1 ? '' : 's'} live in
                  the last 24 h (Redis-only, 24 h TTL).
                </p>
              </CardBody>
            </Card>

            <Card className="mb-4" aria-label="Workers">
              <CardHeader
                title={`Workers — ${status.observability.workersReporting} reporting`}
                description="One row per process, from the heartbeat it writes every sweep. Stale = more than two sweeps missed."
              />
              <CardBody>
                {status.workers.length === 0 ? (
                  <p className="text-sm text-fail" data-testid="workers-empty">
                    No worker has written a heartbeat. Expected:{' '}
                    {status.observability.workersMissing.join(', ')}.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className={TABLE}>
                      <thead>
                        <tr className={HEAD}>
                          <th scope="col" className="py-1.5 pr-3">
                            Worker
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            State
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            Heartbeat
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            In flight
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            Jobs (process)
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            p50 / p95
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            Up
                          </th>
                          <th scope="col" className="py-1.5 pr-3">
                            Render · LLM · Sentry
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {status.workers.map((worker) => (
                          <WorkerRow key={worker.instance} worker={worker} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardBody>
            </Card>

            <Card className="mb-4" aria-label="Jobs">
              <CardHeader
                title={`Jobs — last ${status.jobWindowHours} h, every firm`}
                description="Counts by status; p50 / p95 / max are enqueue-to-finish over succeeded and failed jobs."
              />
              <CardBody>
                <div className="overflow-x-auto">
                  <table className={TABLE}>
                    <thead>
                      <tr className={HEAD}>
                        <th scope="col" className="py-1.5 pr-3">
                          Kind
                        </th>
                        {STATUS_ORDER.map((s) => (
                          <th key={s} scope="col" className="py-1.5 pr-3">
                            {s}
                          </th>
                        ))}
                        <th scope="col" className="py-1.5 pr-3">
                          Fail rate
                        </th>
                        <th scope="col" className="py-1.5 pr-3">
                          p50 / p95
                        </th>
                        <th scope="col" className="py-1.5 pr-3">
                          Max
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {status.jobs.map((job) => (
                        <JobRow key={job.kind} job={job} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardBody>
            </Card>

            <div className="grid gap-4 md:grid-cols-2">
              <Card aria-label="Providers">
                <CardHeader title="Providers in force" />
                <CardBody>
                  <DataRow label="Copilot / brief (LLM)" value={status.providers.llm} />
                  <DataRow label="Renders" value={status.providers.render} />
                  <DataRow label="Billing" value={status.providers.billing} />
                  <DataRow
                    label="Sign-in mail"
                    value={status.providers.mail}
                    hint={
                      status.providers.mail === 'dev-echo'
                        ? 'codes are echoed in the response — dev/test only'
                        : undefined
                    }
                  />
                  <DataRow
                    label="Error tracking (api)"
                    value={
                      <span
                        data-testid="ops-sentry"
                        className={status.sentry === 'on' ? 'text-pass-ink' : 'text-fail'}
                      >
                        sentry {status.sentry}
                      </span>
                    }
                  />
                  <DataRow label="Log format" value={status.observability.logFormat} />
                </CardBody>
              </Card>
              <Card aria-label="Schema">
                <CardHeader
                  title="Schema"
                  actions={
                    <Badge tone={status.migrations.upToDate ? 'pass' : 'fail'}>
                      {status.migrations.upToDate ? 'at head' : 'not at head'}
                    </Badge>
                  }
                />
                <CardBody>
                  <DataRow
                    label="Database"
                    value={
                      <span className="font-mono text-xs" data-testid="ops-migration-current">
                        {status.migrations.current.length === 0
                          ? 'never stamped'
                          : status.migrations.current.join(', ')}
                      </span>
                    }
                  />
                  <DataRow
                    label="Code"
                    value={
                      <span className="font-mono text-xs">
                        {status.migrations.heads.join(', ')}
                      </span>
                    }
                  />
                  {status.migrations.reason === null ? null : (
                    <p className="mt-2 text-sm text-ink-muted">{status.migrations.reason}</p>
                  )}
                </CardBody>
              </Card>
            </div>
          </>
        )}
      </PageBody>
    </AppShell>
  );
}

export default OpsPage;
