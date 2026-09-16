/**
 * The ops binding, pinned against the REAL route in
 * `apps/api/garh_api/routers/platform_ops.py` — path, method, response shape —
 * over a stubbed fetch (the http.test.ts pattern).
 *
 * The drift guard: every camelCase key the client's schema requires must be a
 * field of `PlatformStatusOut` (or one of its nested models) in the router source.
 * Renaming a server field turns this red instead of turning the page into a
 * `malformed_response` panel for the one person who opens it during an incident.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it } from 'vitest';

import { createApiClient, platformStatusSchema } from './api';
import { WIRE_STATUS } from '../features/platform/opsFixture';
import { HttpClient } from './http';
import { TokenStore } from './tokens';

const BASE = 'http://api.test/api/v1';

interface Call {
  url: string;
  method: string;
  body: unknown;
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
let api: ReturnType<typeof createApiClient>;
let respond: (call: Call) => Response;

beforeEach(() => {
  sessionStorage.clear();
  calls = [];
  respond = () => jsonResponse(200, WIRE_STATUS);
  const tokens = new TokenStore();
  tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
  const client = new HttpClient({
    baseUrl: BASE,
    tokens,
    fetchImpl: (input, init) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? 'GET',
        body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null,
      };
      calls.push(call);
      return Promise.resolve(respond(call));
    },
  });
  api = createApiClient(client);
});

describe('api.admin.ops', () => {
  it('get: GET /admin/ops with the bearer, parsing the whole document', async () => {
    const status = await api.admin.ops.get();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.method).toBe('GET');
    expect(calls[0]?.url).toBe(`${BASE}/admin/ops`);
    expect(calls[0]?.body).toBeNull();
    expect(status.sentry).toBe('off');
    expect(status.queues.map((q) => q.worker)).toEqual(['solver', 'render', 'drawings']);
    expect(status.workers[1]?.stale).toBe(true);
    expect(status.jobs[1]?.p95Ms).toBe(84_000);
    expect(status.observability.workersMissing).toEqual(['drawings']);
    expect(status.migrations.upToDate).toBe(false);
  });

  it('get: a 403 for a non-owner surfaces as the problem, not a parse error', async () => {
    respond = () =>
      jsonResponse(403, {
        code: 'forbidden',
        message: 'Only a platform owner can change this.',
        action: 'Sign in as a platform owner to change this.',
      });
    await expect(api.admin.ops.get()).rejects.toMatchObject({ code: 'forbidden', status: 403 });
  });

  it('every field the client requires is one the router serialises', () => {
    // `path.resolve` off this file, not `new URL(…, import.meta.url)`: Vite rewrites
    // `import.meta.url` under jsdom to a non-file scheme (see api.exports.test.ts).
    const source = readFileSync(
      resolve(
        dirname(fileURLToPath(import.meta.url)),
        '../../../api/garh_api/routers/platform_ops.py',
      ),
      'utf8',
    );
    // `snake_case: type` field declarations inside the Pydantic classes, camelised the
    // way `ResponseModel`'s alias generator does it.
    const serverFields = new Set(
      Array.from(source.matchAll(/^ {4}([a-z][a-z0-9_]*): /gm), (m) =>
        (m[1] ?? '').replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase()),
      ),
    );
    expect(serverFields.has('generatedAt')).toBe(true);

    const required: string[] = [];
    const walk = (shape: Record<string, unknown>): void => {
      for (const [key, value] of Object.entries(shape)) {
        required.push(key);
        const inner = (value as { _def?: { innerType?: unknown; shape?: unknown } })._def;
        const def =
          inner?.innerType !== undefined ? (inner.innerType as { _def?: unknown })._def : inner;
        const nested = (def as { shape?: () => Record<string, unknown> } | undefined)?.shape;
        if (typeof nested === 'function') walk(nested());
        const element = (
          def as { type?: { _def?: { shape?: () => Record<string, unknown> } } } | undefined
        )?.type;
        const elementShape = element?._def?.shape;
        if (typeof elementShape === 'function') walk(elementShape());
      }
    };
    walk(platformStatusSchema.shape as Record<string, unknown>);

    // `counts` is a dict on the server (`dict[str, int]`), `providers` is a nested model
    // whose keys are its own fields (llm/render/billing/mail) — all four appear above.
    const missing = required.filter((key) => !serverFields.has(key));
    expect(missing).toEqual([]);
    // Negative control: the walk really sees nested keys, so a rename inside a nested
    // model would be caught — `ageSeconds` lives two levels down.
    expect(required).toContain('ageSeconds');
    expect(required).toContain('workersMissing');
  });
});
