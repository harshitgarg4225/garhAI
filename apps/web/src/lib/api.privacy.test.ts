/**
 * The audit and privacy bindings, pinned against the REAL routes in
 * `apps/api/garh_api/routers/privacy.py` — paths, methods, query, body and
 * response shapes — over a stubbed fetch (the http.test.ts pattern).
 *
 * Two drift guards that would have caught the class of bug this repo keeps
 * finding: the erasure body's field name is read out of the router's own
 * `ErasureRequest`, and the response fields the UI renders are read out of
 * `ErasureResponse`. A server rename turns this red instead of turning the
 * Privacy page into a 422 nobody sees until an architect asks to be forgotten.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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

const WIRE_ENTRY = {
  id: 'a0000000-0000-4000-8000-000000000001',
  at: '2026-09-20T09:15:00+00:00',
  action: 'export.created',
  entity: 'project',
  entityId: 'b0000000-0000-4000-8000-000000000002',
  actorId: 'c0000000-0000-4000-8000-000000000003',
  actorName: 'Asha Rao',
  meta: { kind: 'pdf-set', code: '[redacted]' },
};

const WIRE_ERASURE = {
  erased: true,
  opsAnonymised: 142,
  commentsAnonymised: 3,
  shareLinksAnonymised: 1,
  sessionsEnded: 2,
  auditEntriesRetained: 87,
};

const WIRE_EXPORT = {
  generatedAt: '2026-09-20T09:20:00+00:00',
  subject: {
    id: 'c0000000-0000-4000-8000-000000000003',
    email: 'asha@studio.test',
    name: 'Asha Rao',
    role: 'admin',
    coaNumber: 'CA/2019/12345',
  },
  firm: { id: 'd0000000-0000-4000-8000-000000000004', role: 'admin' },
  twoFactor: { enabled: false, pending: false, confirmedAt: null, recoveryCodesRemaining: 0 },
  signedInDevices: [{ id: 'fam1', device: 'Chrome on macOS' }],
  authTrail: [{ at: '2026-09-19T10:00:00+00:00', action: 'auth.login', entity: 'user', meta: {} }],
  authTrailTruncated: false,
  comments: [],
  commentsWithheld: 0,
  commentsNote: '',
  designActivity: {
    opCount: 142,
    projectIds: ['b0000000-0000-4000-8000-000000000002'],
    firstOpAt: '2026-08-01T10:00:00+00:00',
    lastOpAt: '2026-09-19T10:00:00+00:00',
    shareLinkIds: [],
    note: 'Op contents are the firm’s design data and are not included.',
  },
  retention: { auditLog: 'Audit rows are retained…' },
};

function routerSource(): string {
  // `path.resolve` off this file, not `new URL(…, import.meta.url)`: Vite rewrites
  // `import.meta.url` under jsdom to a non-file scheme (see api.exports.test.ts).
  return readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), '../../../api/garh_api/routers/privacy.py'),
    'utf8',
  );
}

/** `snake_case: type` field declarations of one Pydantic class, camelised. */
function fieldsOf(source: string, className: string): string[] {
  const start = source.indexOf(`class ${className}(`);
  if (start === -1) throw new Error(`no class ${className} in routers/privacy.py`);
  const rest = source.slice(start);
  const end = rest.indexOf('\nclass ', 1);
  const body = end === -1 ? rest : rest.slice(0, end);
  return Array.from(body.matchAll(/^ {4}([a-z][a-z0-9_]*): /gm), (m) =>
    (m[1] ?? '').replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase()),
  );
}

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

describe('api.audit', () => {
  it('list: GET /audit, cursor-paginated, parsing nextCursor into hasMore', async () => {
    respond = () => jsonResponse(200, { items: [WIRE_ENTRY], nextCursor: 'cur2' });
    const page = await api.audit.list({ limit: 50 });
    expect(calls[0]?.method).toBe('GET');
    expect(calls[0]?.url).toBe(`${BASE}/audit?limit=50`);
    expect(page.items[0]?.action).toBe('export.created');
    expect(page.items[0]?.actorName).toBe('Asha Rao');
    // The server redacts inside meta; the client passes it through untouched.
    expect(page.items[0]?.meta).toEqual({ kind: 'pdf-set', code: '[redacted]' });
    expect(page.nextCursor).toBe('cur2');
    expect(page.hasMore).toBe(true);

    respond = () => jsonResponse(200, { items: [], nextCursor: null });
    const last = await api.audit.list({
      cursor: 'cur2',
      action: 'auth.login',
      since: '2026-09-01',
    });
    expect(calls[1]?.url).toBe(`${BASE}/audit?cursor=cur2&action=auth.login&since=2026-09-01`);
    expect(last.hasMore).toBe(false);
  });

  it('list: an entry with no actor (a system row) parses', async () => {
    respond = () =>
      jsonResponse(200, {
        items: [{ ...WIRE_ENTRY, actorId: null, actorName: null, entityId: null }],
        nextCursor: null,
      });
    const page = await api.audit.list({});
    expect(page.items[0]?.actorId).toBeNull();
    expect(page.items[0]?.actorName).toBeNull();
  });

  it('actions: GET /audit/actions, unwrapped to the array', async () => {
    respond = () => jsonResponse(200, { actions: ['auth.login', 'export.created'] });
    expect(await api.audit.actions()).toEqual(['auth.login', 'export.created']);
    expect(calls[0]?.url).toBe(`${BASE}/audit/actions`);
  });

  it('a member’s 403 surfaces as the problem, not an empty page', async () => {
    respond = () =>
      jsonResponse(403, {
        code: 'permission_denied',
        message: 'Only an admin can read the trail.',
        action: 'Ask an admin of your practice.',
      });
    await expect(api.audit.list({})).rejects.toMatchObject({
      code: 'permission_denied',
      status: 403,
    });
  });
});

describe('api.privacy', () => {
  it('export: GET /privacy/export, keeping the sections the page renders', async () => {
    respond = () => jsonResponse(200, WIRE_EXPORT);
    const doc = await api.privacy.export();
    expect(calls[0]?.url).toBe(`${BASE}/privacy/export`);
    expect(doc.subject.email).toBe('asha@studio.test');
    expect(doc.designActivity.opCount).toBe(142);
    // Passthrough: a section the client does not name must still reach the file a
    // person is legally owed — a strict schema would silently drop it.
    const passthrough = doc as Record<string, unknown>;
    expect(passthrough.retention).toEqual({ auditLog: 'Audit rows are retained…' });
    expect(passthrough.twoFactor).toBeDefined();
  });

  it('erase: POST /privacy/erasure with the typed confirmation', async () => {
    respond = () => jsonResponse(200, WIRE_ERASURE);
    const outcome = await api.privacy.erase({ confirmEmail: 'asha@studio.test' });
    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.url).toBe(`${BASE}/privacy/erasure`);
    expect(calls[0]?.body).toEqual({ confirmEmail: 'asha@studio.test' });
    expect(outcome.opsAnonymised).toBe(142);
    expect(outcome.auditEntriesRetained).toBe(87);
  });

  it('erase: a mismatched address is the server’s 409, surfaced whole', async () => {
    respond = () =>
      jsonResponse(409, {
        code: 'conflict',
        message: "That email doesn't match the account you're signed in as.",
        action: 'Type the address you signed in with, exactly.',
      });
    await expect(api.privacy.erase({ confirmEmail: 'wrong@studio.test' })).rejects.toMatchObject({
      status: 409,
    });
  });
});

describe('the server contract', () => {
  it('the erasure body and response fields are the router’s own', () => {
    const source = routerSource();
    // The one key the client sends must be the one field the request model names;
    // `extra="forbid"` on every request schema makes a stray key a 422.
    expect(fieldsOf(source, 'ErasureRequest')).toEqual(['confirmEmail']);

    const responseFields = new Set(fieldsOf(source, 'ErasureResponse'));
    for (const key of [
      'opsAnonymised',
      'commentsAnonymised',
      'shareLinksAnonymised',
      'sessionsEnded',
      'auditEntriesRetained',
    ]) {
      expect(responseFields.has(key)).toBe(true);
    }

    const entryFields = new Set(fieldsOf(source, 'AuditEntryResponse'));
    for (const key of [
      'id',
      'at',
      'action',
      'entity',
      'entityId',
      'actorId',
      'actorName',
      'meta',
    ]) {
      expect(entryFields.has(key)).toBe(true);
    }
    expect(fieldsOf(source, 'AuditPageResponse')).toEqual(['items', 'nextCursor']);
    expect(fieldsOf(source, 'AuditActionsResponse')).toEqual(['actions']);

    // Negative control: the reader really parses fields, so a name that is not
    // there fails rather than silently passing.
    expect(entryFields.has('actorEmail')).toBe(false);
  });
});
