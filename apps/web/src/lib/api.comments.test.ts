/**
 * The comments bindings, pinned against the REAL routes in
 * `apps/api/garh_api/routers/share.py` — paths, methods, body and response
 * shapes — over a stubbed fetch (no live server, per the http.test.ts pattern).
 *
 * The regression that matters: `GET /projects/:id/comments` answers a BARE
 * ARRAY (`list[CommentOut]`), not a cursor page. The first binding parsed it
 * with `pageParser`, which would have thrown `malformed_response` on every
 * real response — the same never-executed class as `share.create`'s nested
 * body. These tests make that impossible to regress silently.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { createApiClient } from './api';
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

/** One comment exactly as `CommentOut` serialises it (camelCase). */
const WIRE_COMMENT = {
  id: 'c0ffee00-0000-4000-8000-000000000001',
  projectId: 'aaaaaaaa-0000-4000-8000-000000000001',
  body: 'The pooja room feels tight.',
  authorName: 'Client',
  anchor: {},
  resolved: false,
  fromShareLink: true,
  createdAt: '2026-08-27T10:00:00Z',
};

let calls: Call[];
let api: ReturnType<typeof createApiClient>;
let respond: (call: Call) => Response;

beforeEach(() => {
  sessionStorage.clear();
  calls = [];
  respond = () => jsonResponse(200, {});
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

describe('api.comments', () => {
  it('list: GET /projects/:id/comments, parsing the BARE ARRAY the server sends', async () => {
    respond = () => jsonResponse(200, [WIRE_COMMENT]);

    const result = await api.comments.list('p1');

    expect(calls[0]?.method).toBe('GET');
    // No query string: the route takes none (`resolved` filtering was fiction).
    expect(calls[0]?.url).toBe(`${BASE}/projects/p1/comments`);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: WIRE_COMMENT.id,
      body: WIRE_COMMENT.body,
      authorName: 'Client',
      resolved: false,
      fromShareLink: true,
    });
  });

  it('list: an empty thread is an empty array, not an error', async () => {
    respond = () => jsonResponse(200, []);
    await expect(api.comments.list('p1')).resolves.toEqual([]);
  });

  it('create: POST with the FLAT CommentIn body', async () => {
    respond = () => jsonResponse(201, { ...WIRE_COMMENT, fromShareLink: false });

    const created = await api.comments.create('p1', {
      body: 'Looks good to me.',
      authorName: 'Asha Rao',
    });

    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.url).toBe(`${BASE}/projects/p1/comments`);
    // Flat, exactly as CommentIn reads it — no envelope, no extra members
    // (the server forbids extras; see share.create's identical lesson).
    expect(calls[0]?.body).toEqual({ body: 'Looks good to me.', authorName: 'Asha Rao' });
    expect(created.fromShareLink).toBe(false);
  });

  it('resolve: POST /comments/:id/resolve?resolved=… (no project segment)', async () => {
    respond = () => jsonResponse(200, { ...WIRE_COMMENT, resolved: true });

    const resolved = await api.comments.setResolved('p1', 'c1', true);
    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.url).toBe(`${BASE}/comments/c1/resolve?resolved=true`);
    expect(resolved.resolved).toBe(true);

    respond = () => jsonResponse(200, WIRE_COMMENT);
    await api.comments.setResolved('p1', 'c1', false);
    expect(calls[1]?.url).toBe(`${BASE}/comments/c1/resolve?resolved=false`);
  });
});
