/**
 * The ops page, rendered for real over a stub transport.
 *
 * What is pinned: the alarm list in reading order with a next step on each, every
 * queue and worker row with the stale badge, the job percentiles, the providers
 * and Sentry state, the schema verdict, the manual refresh re-reading the
 * endpoint, and the 403 branch for a caller the allowlist refuses.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ToastProvider } from '@garh/ui';

import { createApiClient } from '../../lib/api';
import { WIRE_STATUS } from './opsFixture';
import { HttpClient } from '../../lib/http';
import { TokenStore } from '../../lib/tokens';
import { OpsPage } from './OpsPage';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const BASE = 'http://api.test/api/v1';

interface Call {
  url: string;
  method: string;
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

let calls: Call[];
let respond: (call: Call) => Response;
let container: HTMLDivElement;
let root: Root;

function client() {
  const tokens = new TokenStore();
  tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
  const http = new HttpClient({
    baseUrl: BASE,
    tokens,
    fetchImpl: (input, init) => {
      const call: Call = { url: String(input), method: init?.method ?? 'GET' };
      calls.push(call);
      return Promise.resolve(respond(call));
    },
  });
  return createApiClient(http);
}

async function flush(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(element: ReactElement): Promise<void> {
  act(() => {
    root.render(
      <ToastProvider>
        <MemoryRouter>{element}</MemoryRouter>
      </ToastProvider>,
    );
  });
  await flush();
}

function text(selector: string): string {
  return container.querySelector(selector)?.textContent ?? '';
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  sessionStorage.clear();
  calls = [];
  respond = () => jsonResponse(200, WIRE_STATUS);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('OpsPage', () => {
  it('reads GET /admin/ops once and leads with the alarms, each with a next step', async () => {
    await mount(<OpsPage client={client()} refreshMs={0} />);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([`GET ${BASE}/admin/ops`]);

    const alarms = Array.from(container.querySelectorAll('[data-testid^="alarm-"]'));
    expect(alarms.map((el) => el.getAttribute('data-testid'))).toEqual([
      'alarm-sentry',
      'alarm-workers-missing',
      'alarm-workers-stale',
      'alarm-dead-render',
      'alarm-migrations',
    ]);
    expect(text('[data-testid="alarm-sentry"]')).toContain('Set SENTRY_DSN on the api service');
    expect(text('[data-testid="alarm-workers-missing"]')).toContain('No heartbeat from drawings');
    expect(text('[aria-label="Findings"]')).toContain(
      '5 things to act on before trusting this deployment.',
    );
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(5);
  });

  it('shows every queue, worker (with the stale badge) and job kind with its percentiles', async () => {
    await mount(<OpsPage client={client()} refreshMs={0} />);

    expect(text('[data-testid="queue-solver"]')).toContain('3'); // waiting = 2 + 1
    expect(text('[data-testid="queue-render"]')).toContain('3'); // dead
    expect(text('[data-testid="queue-render"] .bg-fail-soft')).toBe('3');

    expect(text('[data-testid="worker-host-solver-1"]')).toContain('live');
    expect(text('[data-testid="worker-host-solver-1"]')).toContain('10 s ago');
    expect(text('[data-testid="worker-host-solver-1"]')).toContain('4 ok · 1 failed');
    expect(text('[data-testid="worker-host-solver-1"]')).toContain('800 ms / 2.4 s');
    expect(text('[data-testid="worker-host-solver-1"]')).toContain('2 h 03 min');
    expect(text('[data-testid="worker-host-render-1"]')).toContain('stale');
    expect(text('[data-testid="worker-host-render-1"]')).toContain('stability');
    expect(text('[data-testid="worker-host-render-1"]')).toContain('sentry on');
    expect(text('[aria-label="Workers"]')).toContain('2 reporting');

    expect(text('[data-testid="jobs-solver"]')).toContain('33%');
    expect(text('[data-testid="jobs-solver"]')).toContain('30.0 s / 1.4 min');
    expect(text('[data-testid="jobs-solver"]')).toContain('1.5 min');
    expect(text('[data-testid="jobs-render"]')).toContain('—');
    expect(text('[aria-label="Jobs"]')).toContain('last 24 h, every firm');
    expect(text('[aria-label="Queues"]')).toContain('2 export jobs live');
  });

  it('names the providers, the Sentry state and the schema verdict', async () => {
    await mount(<OpsPage client={client()} refreshMs={0} />);
    const providers = text('[aria-label="Providers"]');
    expect(providers).toContain('mock');
    expect(providers).toContain('dev-echo');
    expect(providers).toContain('codes are echoed');
    expect(text('[data-testid="ops-sentry"]')).toBe('sentry off');
    const schema = text('[aria-label="Schema"]');
    expect(schema).toContain('not at head');
    expect(text('[data-testid="ops-migration-current"]')).toBe('never stamped');
    expect(schema).toContain('0016_compliance_summary');
    expect(schema).toContain('never run on this database');
    expect(text('[data-testid="ops-as-of"]')).toContain('api 0.1.0');
  });

  it('an all-clear document renders no alarm and a green headline', async () => {
    respond = () =>
      jsonResponse(200, {
        ...WIRE_STATUS,
        sentry: 'on',
        queues: WIRE_STATUS.queues.map((q) => ({ ...q, dead: 0 })),
        workers: WIRE_STATUS.workers.map((w) => ({ ...w, sentry: 'on', stale: false })),
        migrations: {
          current: ['0016_compliance_summary'],
          heads: ['0016_compliance_summary'],
          upToDate: true,
          reason: null,
        },
        providers: { ...WIRE_STATUS.providers, mail: 'brevo-http' },
        observability: {
          ...WIRE_STATUS.observability,
          sentry: 'on',
          workersMissing: [],
          workersStale: [],
        },
      });
    await mount(<OpsPage client={client()} refreshMs={0} />);
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(0);
    expect(text('[aria-label="Findings"]')).toContain('All clear');
    expect(text('[data-testid="ops-sentry"]')).toBe('sentry on');
    expect(text('[aria-label="Schema"]')).toContain('at head');
  });

  it('Refresh re-reads the endpoint', async () => {
    await mount(<OpsPage client={client()} refreshMs={0} />);
    const button = Array.from(container.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes('Refresh'),
    );
    expect(button).toBeDefined();
    act(() => {
      button?.click();
    });
    await flush();
    expect(calls).toHaveLength(2);
  });

  it('a 403 renders the refusal as a problem panel, not a blank page', async () => {
    respond = () =>
      jsonResponse(403, {
        code: 'forbidden',
        message: 'Only a platform owner can change this.',
        action: 'Sign in as a platform owner to change this.',
      });
    await mount(<OpsPage client={client()} refreshMs={0} />);
    const body = container.textContent ?? '';
    expect(body).toContain('Only a platform owner can change this.');
    expect(container.querySelector('[aria-label="Queues"]')).toBeNull();
  });
});
