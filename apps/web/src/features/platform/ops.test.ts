/**
 * The ops page's words, pinned without React: ages, durations, verdicts and — the
 * part that matters — which states are ALARMS. Each alarm has the negative control
 * that proves it fires on its state and on nothing else.
 */

import { describe, expect, it } from 'vitest';

import { platformStatusSchema, type PlatformStatus } from '../../lib/api';
import { WIRE_STATUS } from './opsFixture';
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

function status(overrides: (s: PlatformStatus) => void = () => undefined): PlatformStatus {
  const parsed = platformStatusSchema.parse(WIRE_STATUS);
  overrides(parsed);
  return parsed;
}

/** A deployment with nothing to act on — the baseline every alarm test starts from. */
function allClear(): PlatformStatus {
  return status((s) => {
    s.sentry = 'on';
    s.observability = {
      ...s.observability,
      sentry: 'on',
      workersMissing: [],
      workersStale: [],
      redis: 'ok',
      database: 'ok',
    };
    s.workers = s.workers.map((w) => ({ ...w, sentry: 'on', stale: false }));
    s.queues = s.queues.map((q) => ({ ...q, dead: 0 }));
    s.migrations = {
      current: s.migrations.heads,
      heads: s.migrations.heads,
      upToDate: true,
      reason: null,
    };
    s.providers = { ...s.providers, mail: 'brevo-http' };
  });
}

describe('describe*', () => {
  it('ages, durations and uptimes read the way an operator says them', () => {
    expect(describeAge(12)).toBe('12 s ago');
    expect(describeAge(180)).toBe('3 min ago');
    expect(describeAge(7_200)).toBe('2.0 h ago');
    expect(describeDuration(0)).toBe('—');
    expect(describeDuration(850)).toBe('850 ms');
    expect(describeDuration(12_400)).toBe('12.4 s');
    expect(describeDuration(192_000)).toBe('3.2 min');
    expect(describeUptime(40)).toBe('40 s');
    expect(describeUptime(7_500)).toBe('2 h 05 min');
    expect(describeUptime(273_600)).toBe('3 d 4 h');
  });

  it('a worker is live, draining or stale — stale wins', () => {
    const [live, stale] = status().workers;
    expect(live && workerVerdict(live)).toEqual({ label: 'live', tone: 'pass' });
    expect(stale && workerVerdict(stale)).toEqual({ label: 'stale', tone: 'fail' });
    expect(live && workerVerdict({ ...live, draining: true })).toEqual({
      label: 'draining',
      tone: 'warn',
    });
    expect(stale && workerVerdict({ ...stale, draining: true }).label).toBe('stale');
  });

  it('waiting is pending plus delayed; the fail rate is over terminal jobs only', () => {
    const [solver] = status().queues;
    expect(solver && queueWaiting(solver)).toBe(3);
    const jobs = status().jobs;
    expect(jobs[1] && failureRate(jobs[1])).toBe(33);
    expect(jobs[0] && failureRate(jobs[0])).toBeNull();
  });
});

describe('alarmsFor', () => {
  it('an all-clear deployment has no alarms and says so', () => {
    const s = allClear();
    expect(alarmsFor(s)).toEqual([]);
    expect(headline(s, [])).toBe(
      'All clear — 2 workers reporting, error tracking on, schema at head.',
    );
  });

  it('the wire fixture carries exactly the findings its state implies, in reading order', () => {
    const s = status();
    const keys = alarmsFor(s).map((a) => a.key);
    expect(keys).toEqual([
      'sentry',
      'workers-missing',
      'workers-stale',
      'dead-render',
      'migrations',
    ]);
    expect(headline(s, alarmsFor(s))).toBe('5 things to act on before trusting this deployment.');
    for (const alarm of alarmsFor(s)) {
      expect(alarm.action.length).toBeGreaterThan(10);
    }
  });

  it('sentry off on the api names the variable; on the api but off on a worker names the worker', () => {
    const off = allClear();
    off.sentry = 'off';
    off.observability = { ...off.observability, sentry: 'off' };
    const sentryAlarm = alarmsFor(off).find((a) => a.key === 'sentry');
    expect(sentryAlarm?.action).toContain('SENTRY_DSN');

    const workerOff = allClear();
    workerOff.workers = workerOff.workers.map((w, i) => (i === 0 ? { ...w, sentry: 'off' } : w));
    const workerAlarm = alarmsFor(workerOff).find((a) => a.key === 'sentry-workers');
    expect(workerAlarm?.title).toBe('Error tracking is off on 1 worker');
    expect(workerAlarm?.detail).toBe('host-solver-1');
    expect(alarmsFor(workerOff).map((a) => a.key)).toEqual(['sentry-workers']);
  });

  it('a Redis outage is one alarm, not three — the missing-worker alarm is suppressed', () => {
    const s = allClear();
    s.observability = {
      ...s.observability,
      redis: 'down',
      workersMissing: ['solver', 'render', 'drawings'],
    };
    s.queues = [];
    s.workers = [];
    const keys = alarmsFor(s).map((a) => a.key);
    expect(keys).toEqual(['redis']);
    expect(alarmsFor(s)[0]?.detail).toContain('not idle');
  });

  it('dead letters alarm per queue and name the count', () => {
    const s = allClear();
    s.queues = s.queues.map((q) => (q.worker === 'drawings' ? { ...q, dead: 4 } : q));
    const alarm = alarmsFor(s).find((a) => a.key === 'dead-drawings');
    expect(alarm?.title).toBe('4 dead-lettered on drawings');
    expect(alarmsFor(s)).toHaveLength(1);
  });

  it('a schema behind the code carries the server’s reason; two heads is its own alarm', () => {
    const behind = allClear();
    behind.migrations = {
      current: ['0015_team_invites'],
      heads: ['0016_compliance_summary'],
      upToDate: false,
      reason:
        'database is at 0015_team_invites, code head is 0016_compliance_summary: run `alembic upgrade head`.',
    };
    expect(alarmsFor(behind).map((a) => a.key)).toEqual(['migrations']);
    expect(alarmsFor(behind)[0]?.detail).toContain('0015_team_invites');

    const heads = allClear();
    heads.migrations = {
      current: ['0016_a', '0017_b'],
      heads: ['0016_a', '0017_b'],
      upToDate: true,
      reason: 'the migration scripts have 2 heads; merge them before the next release.',
    };
    expect(alarmsFor(heads).map((a) => a.key)).toEqual(['migrations-heads']);
  });

  it('no mail channel alarms; the dev echo alarms only outside dev/test', () => {
    const none = allClear();
    none.providers = { ...none.providers, mail: 'none' };
    expect(alarmsFor(none).map((a) => a.key)).toEqual(['mail']);

    const echoDev = allClear();
    echoDev.providers = { ...echoDev.providers, mail: 'dev-echo' };
    echoDev.env = 'dev';
    expect(alarmsFor(echoDev)).toEqual([]);

    const echoProd = allClear();
    echoProd.providers = { ...echoProd.providers, mail: 'dev-echo' };
    echoProd.env = 'prod';
    expect(alarmsFor(echoProd).map((a) => a.key)).toEqual(['mail-echo']);
  });
});
