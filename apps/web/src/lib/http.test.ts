/**
 * The transport's two load-bearing behaviours, both of which are invisible
 * until they are wrong:
 *
 *  1. **Single-flight refresh.** Six concurrent 401s must produce ONE call to
 *     `/auth/refresh`. With refresh-token rotation (§13), six calls means five
 *     present an already-rotated token, the server reads reuse, and the user is
 *     signed out at random.
 *  2. **A stable `Idempotency-Key` across the retry.** Generating a fresh key
 *     for the replay turns a timeout into a duplicated mutation (§11).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ERROR_CODES } from './errors';
import { HttpClient } from './http';
import { TokenStore } from './tokens';

/**
 * A duck-typed `Response`. Deliberately not `new Response(...)`: the fetch
 * globals are not part of jsdom, and a test that depends on which of them the
 * runner happens to inject is a test that fails for the wrong reason.
 */
function jsonResponse(
  status: number,
  body: unknown,
  headers: Readonly<Record<string, string>> = {},
): Response {
  const lower: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) lower[k.toLowerCase()] = v;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

interface Call {
  url: string;
  method: string;
  authorization: string | null;
  idempotencyKey: string | null;
}

function record(input: RequestInfo | URL, init: RequestInit | undefined): Call {
  // `HttpClient` always passes a real `Headers`; reading it through its own
  // method rather than re-constructing one keeps this helper independent of
  // which fetch globals the test runner injected.
  const headers = init?.headers as Headers | undefined;
  return {
    url: String(input),
    method: init?.method ?? 'GET',
    authorization: headers?.get('Authorization') ?? null,
    idempotencyKey: headers?.get('Idempotency-Key') ?? null,
  };
}

const BASE = 'http://api.test/api/v1';
const identity = (data: unknown): unknown => data;

let tokens: TokenStore;

beforeEach(() => {
  sessionStorage.clear();
  tokens = new TokenStore();
});

describe('HttpClient', () => {
  it('sends the bearer, and an Idempotency-Key on mutations only', async () => {
    const calls: Call[] = [];
    tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });

    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: (input, init) => {
        calls.push(record(input, init));
        return Promise.resolve(jsonResponse(200, { ok: true }));
      },
    });

    await client.request({ path: '/projects', parse: identity });
    await client.request({ method: 'POST', path: '/projects', body: { name: 'x' }, parse: identity });

    expect(calls[0]?.url).toBe(`${BASE}/projects`);
    expect(calls[0]?.authorization).toBe('Bearer a1');
    expect(calls[0]?.idempotencyKey).toBeNull();
    expect(calls[1]?.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('omits the bearer where auth is none (the share-viewer surface)', async () => {
    const calls: Call[] = [];
    tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: (input, init) => {
        calls.push(record(input, init));
        return Promise.resolve(jsonResponse(200, {}));
      },
    });

    await client.request({ path: '/share/tok', auth: 'none', parse: identity });
    expect(calls[0]?.authorization).toBeNull();
  });

  it('refreshes exactly once for six concurrent 401s, and replays each request', async () => {
    tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });

    let refreshCalls = 0;
    const calls: Call[] = [];

    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: (input, init) => {
        const call = record(input, init);
        if (call.url.endsWith('/auth/refresh')) {
          refreshCalls += 1;
          return Promise.resolve(
            jsonResponse(200, { accessToken: 'a2', expiresIn: 900, refreshToken: 'r2' }),
          );
        }
        calls.push(call);
        if (call.authorization === 'Bearer a1') {
          return Promise.resolve(
            jsonResponse(401, {
              code: 'token_expired',
              message: 'Your session expired.',
              action: 'Signing you back in…',
            }),
          );
        }
        return Promise.resolve(jsonResponse(200, { id: call.url }));
      },
    });

    const results = await Promise.all(
      Array.from({ length: 6 }, (_, i) =>
        client.request({ path: `/projects/${i}`, parse: identity }),
      ),
    );

    expect(results).toHaveLength(6);
    expect(refreshCalls).toBe(1);
    // Six 401s + six replays.
    expect(calls).toHaveLength(12);
    expect(calls.filter((c) => c.authorization === 'Bearer a2')).toHaveLength(6);
  });

  it('reuses the same Idempotency-Key when a mutation is replayed after a refresh', async () => {
    tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
    const keys: (string | null)[] = [];

    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: (input, init) => {
        const call = record(input, init);
        if (call.url.endsWith('/auth/refresh')) {
          return Promise.resolve(jsonResponse(200, { accessToken: 'a2', expiresIn: 900 }));
        }
        keys.push(call.idempotencyKey);
        if (call.authorization === 'Bearer a1') {
          return Promise.resolve(jsonResponse(401, { code: 'token_expired' }));
        }
        return Promise.resolve(jsonResponse(200, {}));
      },
    });

    await client.request({ method: 'POST', path: '/projects/p1/solve', parse: identity });
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it('gives up and reports auth loss when the refresh itself fails', async () => {
    tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
    const onAuthLost = vi.fn();

    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      onAuthLost,
      fetchImpl: (input) =>
        Promise.resolve(
          String(input).endsWith('/auth/refresh')
            ? jsonResponse(401, { code: 'refresh_token_reused' })
            : jsonResponse(401, { code: 'token_expired' }),
        ),
    });

    await expect(client.request({ path: '/projects', parse: identity })).rejects.toMatchObject({
      isAuthFailure: true,
    });
    expect(onAuthLost).toHaveBeenCalledTimes(1);
    expect(tokens.accessToken).toBeNull();
  });

  it('keeps the session when the refresh fails for lack of a network', async () => {
    tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
    const onAuthLost = vi.fn();

    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      onAuthLost,
      fetchImpl: (input) => {
        if (String(input).endsWith('/auth/refresh')) return Promise.reject(new TypeError('offline'));
        return Promise.resolve(jsonResponse(401, { code: 'token_expired' }));
      },
    });

    await expect(client.request({ path: '/projects', parse: identity })).rejects.toBeTruthy();
    // Being offline is not the same as being signed out.
    expect(onAuthLost).not.toHaveBeenCalled();
    expect(tokens.refreshToken).toBe('r1');
  });

  it('retries a GET once after a transport failure', async () => {
    let attempts = 0;
    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: () => {
        attempts += 1;
        if (attempts === 1) return Promise.reject(new TypeError('Failed to fetch'));
        return Promise.resolve(jsonResponse(200, { ok: true }));
      },
    });

    await expect(client.request({ path: '/meta', auth: 'none', parse: identity })).resolves.toEqual(
      { ok: true },
    );
    expect(attempts).toBe(2);
  });

  it('does not retry a POST after a transport failure', async () => {
    let attempts = 0;
    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: () => {
        attempts += 1;
        return Promise.reject(new TypeError('Failed to fetch'));
      },
    });

    await expect(
      client.request({ method: 'POST', path: '/projects', auth: 'none', parse: identity }),
    ).rejects.toMatchObject({ code: ERROR_CODES.network });
    expect(attempts).toBe(1);
  });

  it('turns an unrecognised response shape into a reload-me error, not a TypeError', async () => {
    const client = new HttpClient({
      baseUrl: BASE,
      tokens,
      fetchImpl: () => Promise.resolve(jsonResponse(200, { unexpected: true })),
    });

    await expect(
      client.request({
        path: '/meta',
        auth: 'none',
        parse: () => {
          throw new Error('Expected object, received string');
        },
      }),
    ).rejects.toMatchObject({ code: ERROR_CODES.malformedResponse });
  });

  it('builds absolute URLs for downloads and streams', () => {
    const client = new HttpClient({ baseUrl: BASE, tokens, fetchImpl: () => Promise.reject() });
    expect(client.url('/projects/p1/sheets/s1.pdf')).toBe(`${BASE}/projects/p1/sheets/s1.pdf`);
    expect(client.url('/projects', { limit: 10, cursor: undefined })).toBe(`${BASE}/projects?limit=10`);
  });
});
