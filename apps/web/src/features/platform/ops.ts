/**
 * ops — the pure half of the platform owner's ops page.
 *
 * Everything the page says in words is decided here, so a sentence can be pinned
 * in a unit test without mounting React: how old a heartbeat is, what a job
 * percentile reads as, and — the part that matters — which findings are ALARMS.
 * An alarm is a state an operator must act on before trusting the deployment:
 * Sentry off, a worker missing, a queue with dead letters, a migration behind.
 * The page renders alarms first and in red; nothing else on it is allowed to
 * look like one.
 */

import type { OpsJobKind, OpsQueue, OpsWorker, PlatformStatus } from '../../lib/api';

export interface Alarm {
  readonly key: string;
  readonly title: string;
  readonly detail: string;
  /** What to do. Never blank — an alarm without a next step is a §15 rule-9 miss. */
  readonly action: string;
}

/** "12 s ago", "3 min ago", "2 h ago". */
export function describeAge(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${(seconds / 3600).toFixed(1)} h ago`;
}

/** Milliseconds as an operator reads them: "850 ms", "12.4 s", "3.2 min". */
export function describeDuration(ms: number): string {
  if (ms <= 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

/** "3 d 4 h", "2 h 05 min", "40 s". */
export function describeUptime(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')} min`;
  return `${minutes} min`;
}

/** The one-word state of a worker row, and its badge tone. */
export function workerVerdict(worker: OpsWorker): {
  label: 'live' | 'draining' | 'stale';
  tone: 'pass' | 'warn' | 'fail';
} {
  if (worker.stale) return { label: 'stale', tone: 'fail' };
  if (worker.draining) return { label: 'draining', tone: 'warn' };
  return { label: 'live', tone: 'pass' };
}

/** What is waiting on a queue, as the user experiences it. */
export function queueWaiting(queue: OpsQueue): number {
  return queue.pending + queue.delayed;
}

/** Terminal jobs in the window that did not succeed, as a percentage. */
export function failureRate(job: OpsJobKind): number | null {
  const failed = job.counts.failed ?? 0;
  const succeeded = job.counts.succeeded ?? 0;
  const total = failed + succeeded;
  if (total === 0) return null;
  return Math.round((100 * failed) / total);
}

/**
 * The findings an operator must act on, in the order they should be read.
 * Deterministic and complete: every state the API can report that means "do not
 * trust this deployment yet" is a case here, and `OpsPage.test.tsx` renders each.
 */
export function alarmsFor(status: PlatformStatus): Alarm[] {
  const alarms: Alarm[] = [];
  const obs = status.observability;

  if (obs.redis === 'down') {
    alarms.push({
      key: 'redis',
      title: 'Redis did not answer',
      detail:
        'Queue depths and worker heartbeats are unknown — the empty lists below are not idle.',
      action:
        'Check the Redis service and the api’s Redis connection setting; every job path depends on it.',
    });
  }
  if (obs.database === 'down') {
    alarms.push({
      key: 'database',
      title: 'Postgres did not answer',
      detail: 'Job counts and the migration head could not be read.',
      action: 'Check the Postgres service and the api’s database connection setting.',
    });
  }
  if (obs.sentry === 'off') {
    alarms.push({
      key: 'sentry',
      title: 'Error tracking is off on the api',
      detail:
        'No Sentry DSN is set, so an exception in production is a log line nobody is paged for.',
      action:
        'Set the Sentry DSN variable on the api service (and each worker); /healthz then reads sentry: on.',
    });
  }
  const workersWithSentryOff = status.workers.filter((w) => w.sentry === 'off');
  if (obs.sentry === 'on' && workersWithSentryOff.length > 0) {
    alarms.push({
      key: 'sentry-workers',
      title: `Error tracking is off on ${workersWithSentryOff.length} worker${
        workersWithSentryOff.length === 1 ? '' : 's'
      }`,
      detail: workersWithSentryOff.map((w) => w.instance).join(', '),
      action: 'Set the Sentry DSN variable on every worker service, not only the api.',
    });
  }
  if (obs.redis === 'ok' && obs.workersMissing.length > 0) {
    alarms.push({
      key: 'workers-missing',
      title: `No heartbeat from ${obs.workersMissing.join(', ')}`,
      detail:
        'A worker that is not running answers no probe at all; its queue will grow until someone notices.',
      action:
        'Check the worker service is deployed and can reach Redis; a live worker reports every sweep.',
    });
  }
  if (obs.workersStale.length > 0) {
    alarms.push({
      key: 'workers-stale',
      title: `Missed heartbeats: ${obs.workersStale.join(', ')}`,
      detail: 'The key is still in Redis but the process has skipped more than two sweeps.',
      action:
        'Look at that worker’s logs; a stuck sweep usually means a stuck job or a Redis timeout.',
    });
  }
  for (const queue of status.queues) {
    if (queue.dead > 0) {
      alarms.push({
        key: `dead-${queue.worker}`,
        title: `${queue.dead} dead-lettered on ${queue.worker}`,
        detail: `${queue.name}:dead holds jobs that failed every retry; each one is an architect who saw an error card.`,
        action:
          'Read the dead letters on the worker (GET /healthz there lists them) and fix the cause before replaying.',
      });
    }
  }
  if (!status.migrations.upToDate) {
    alarms.push({
      key: 'migrations',
      title: 'Database schema is not at the code’s head',
      detail: status.migrations.reason ?? 'The migration state could not be determined.',
      action: 'Run the migration step (python -m garh_api.migrate) before serving this build.',
    });
  } else if (status.migrations.reason !== null) {
    alarms.push({
      key: 'migrations-heads',
      title: 'The migration scripts have more than one head',
      detail: status.migrations.reason,
      action: 'Merge the heads before the next release; alembic upgrade head refuses two of them.',
    });
  }
  if (status.providers.mail === 'none') {
    alarms.push({
      key: 'mail',
      title: 'No sign-in mail channel',
      detail: 'Every POST /auth/otp will answer 503 until a transport is configured.',
      action: 'Set BREVO_API_KEY + SMTP_FROM (Railway Hobby) or SMTP_HOST + SMTP_FROM on the api.',
    });
  } else if (
    status.providers.mail === 'dev-echo' &&
    status.env !== 'dev' &&
    status.env !== 'test'
  ) {
    alarms.push({
      key: 'mail-echo',
      title: 'Sign-in codes are being echoed',
      detail:
        'The dev echo is on outside dev/test — this should be impossible; treat it as a config fault.',
      action: 'Check APP_ENV and DEV_ECHO_OTP on the api.',
    });
  }
  return alarms;
}

/** A one-line headline the page shows above everything else. */
export function headline(status: PlatformStatus, alarms: readonly Alarm[]): string {
  if (alarms.length === 0) {
    return `All clear — ${status.observability.workersReporting} worker${
      status.observability.workersReporting === 1 ? '' : 's'
    } reporting, error tracking on, schema at head.`;
  }
  return `${alarms.length} thing${alarms.length === 1 ? '' : 's'} to act on before trusting this deployment.`;
}
