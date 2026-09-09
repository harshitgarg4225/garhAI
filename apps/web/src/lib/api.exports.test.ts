/**
 * `api.exports.create`, pinned against the REAL request model in
 * `apps/api/garh_api/schemas/jobs.py` (`ExportIn`).
 *
 * Every schema on the server forbids unknown keys, so the body the client sends
 * must be a subset of the fields the model declares — not "roughly the right
 * shape". The first time an architect clicked "PDF set" the client sent a
 * `params` key the model had never heard of, the server answered 422, and the
 * Sheets tab showed "Preparing your download" over a request that had already
 * died. Nothing here had ever compared the two sides. Now it does: the field
 * names are read out of the Python source, so renaming a field on the server
 * turns this red instead of turning the button into a 422.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createApiClient } from './api';
import { HttpClient } from './http';
import { TokenStore } from './tokens';

const BASE = 'http://api.test/api/v1';

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
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

/** Exactly what `ExportJobOut` serialises for a freshly queued job. */
const WIRE_JOB = {
  id: 'e1',
  projectId: '4f9b1a5e-1b2c-4d3e-8f90-123456789abc',
  kind: 'pdf-set',
  status: 'queued',
  progress: 0,
  designVersionId: null,
  downloadUrl: null,
  error: null,
  eventsUrl: '/api/v1/export-jobs/e1/events',
  createdAt: '2026-09-07T14:04:49Z',
  updatedAt: '2026-09-07T14:04:49Z',
};

/**
 * The field names `ExportIn` declares, camel-cased the way `CamelModel` aliases
 * them. Read from the source on every run — a hand-copied list would agree with
 * the client forever and with the server only until someone edits it.
 */
function exportInFields(): Set<string> {
  // `path.resolve` off this file, not `new URL(…, import.meta.url)`: Vite rewrites
  // that expression into a dev-server asset URL (see layerSpecs.test.ts).
  const source = readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), '../../../api/garh_api/schemas/jobs.py'),
    'utf8',
  );
  const start = source.indexOf('class ExportIn(');
  expect(start, 'ExportIn must still exist on the server').toBeGreaterThan(-1);
  const rest = source.slice(start + 1);
  const end = rest.search(/\nclass /);
  const body = end === -1 ? rest : rest.slice(0, end);
  const fields = new Set<string>();
  for (const match of body.matchAll(/^ {4}([a-z_][a-z0-9_]*): /gm)) {
    const snake = match[1] ?? '';
    fields.add(snake.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()));
  }
  return fields;
}

let calls: Call[];
let api: ReturnType<typeof createApiClient>;

beforeEach(() => {
  sessionStorage.clear();
  calls = [];
  const record: typeof fetch = (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method ?? 'GET',
      body:
        typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : null,
    });
    return Promise.resolve(jsonResponse(200, WIRE_JOB));
  };
  vi.stubGlobal('fetch', record);
  const tokens = new TokenStore();
  tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
  api = createApiClient(new HttpClient({ baseUrl: BASE, tokens, fetchImpl: record }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api.exports.create', () => {
  it('reads the server model: ExportIn declares kind, sheetIds, options and friends', () => {
    const fields = exportInFields();
    expect(fields.has('kind')).toBe(true);
    expect(fields.has('options')).toBe(true);
    expect(fields.has('sheetIds')).toBe(true);
    expect(fields.has('designVersionId')).toBe(true);
    expect(fields.has('includeDisclaimer')).toBe(true);
    // The negative control: the key that shipped the 422 is NOT a field.
    expect(fields.has('params')).toBe(false);
  });

  it('a bare click sends only keys ExportIn declares — never `params`', async () => {
    await api.exports.create('p1', { kind: 'pdf-set' });

    const call = calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.url).toBe(`${BASE}/projects/p1/export`);
    expect(call?.body).toEqual({ kind: 'pdf-set', options: {} });
    const allowed = exportInFields();
    for (const key of Object.keys(call?.body ?? {})) {
      expect(allowed.has(key), `ExportIn has no field "${key}"`).toBe(true);
    }
  });

  it('every optional field rides under the name the server aliases it to', async () => {
    await api.exports.create('p1', {
      kind: 'dxf',
      designVersionId: 'dv1',
      sheetIds: ['s1', 's2'],
      includeDisclaimer: false,
      options: { paper: 'A1' },
    });

    expect(calls[0]?.body).toEqual({
      kind: 'dxf',
      designVersionId: 'dv1',
      sheetIds: ['s1', 's2'],
      includeDisclaimer: false,
      options: { paper: 'A1' },
    });
    const allowed = exportInFields();
    for (const key of Object.keys(calls[0]?.body ?? {})) {
      expect(allowed.has(key), `ExportIn has no field "${key}"`).toBe(true);
    }
  });

  it('the response parses as an export job, not as a solver job', async () => {
    const job = await api.exports.create('p1', { kind: 'pdf-set' });
    expect(job.kind).toBe('drawings');
    expect(job.exportKind).toBe('pdf-set');
    expect(job.status).toBe('queued');
  });
});
