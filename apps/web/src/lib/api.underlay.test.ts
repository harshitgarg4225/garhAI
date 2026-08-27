/**
 * The underlay bindings, pinned against the REAL routes in
 * `apps/api/garh_api/routers/underlay.py` — paths, methods, bodies and the one
 * status code that is not an error — over a stubbed fetch (no live server, the
 * `api.comments.test.ts` / `http.test.ts` pattern).
 *
 * The case that matters most is the LAST one. `GET /projects/:id/underlay`
 * answers **404 with code `no_underlay`** for a project that simply has not had
 * a scan uploaded yet, which is the state every project starts in. A binding
 * that let that throw would put a failure banner on the plan tab of every new
 * project — and, worse, would look completely fine in review, because throwing
 * on a 404 is what every other GET in this file correctly does. So: this one
 * answers `null`, and a 404 that is NOT `no_underlay` (someone else's project)
 * still throws. Both directions are asserted; one without the other is a gate
 * that cannot fail.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createApiClient } from './api';
import { AppError } from './errors';
import { HttpClient } from './http';
import { TokenStore } from './tokens';

const BASE = 'http://api.test/api/v1';

interface Call {
  url: string;
  method: string;
  body: unknown;
  contentType: string | null;
  rawBody: unknown;
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

/** Exactly what `UnderlayOut` serialises (camelCase, presigned URL per call). */
const WIRE_UNDERLAY = {
  objectKey: 'underlays/f1/p1/2b6c.png',
  imageUrl: 'https://storage.test/underlays/f1/p1/2b6c.png?X-Amz-Signature=deadbeef',
  widthPx: 2480,
  heightPx: 3508,
  mmPerPx: 8.4677,
  originXMm: -12000,
  originYMm: 18000,
  opacity: 0.45,
  locked: false,
  visible: true,
};

/** problem+json exactly as `_no_underlay` builds it. */
const NO_UNDERLAY_PROBLEM = {
  code: 'no_underlay',
  message: 'This project has no underlay image.',
  action: 'Upload a plan image (PNG or JPEG) to trace over.',
  projectId: 'p1',
};

let calls: Call[];
let api: ReturnType<typeof createApiClient>;
let respond: (call: Call) => Response;

function headerOf(init: RequestInit | undefined, name: string): string | null {
  const headers = init?.headers as Record<string, string> | undefined;
  return headers?.[name] ?? null;
}

beforeEach(() => {
  sessionStorage.clear();
  calls = [];
  respond = () => jsonResponse(200, WIRE_UNDERLAY);

  const record: typeof fetch = (input, init) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null,
      contentType: headerOf(init, 'Content-Type'),
      rawBody: init?.body ?? null,
    };
    calls.push(call);
    return Promise.resolve(respond(call));
  };

  // BOTH doors, because the client has two. `HttpClient.request` goes through
  // the injected `fetchImpl`, but `postBinary` — the raw-body path the upload
  // uses, shared with the DXF import — reaches for the GLOBAL `fetch` instead.
  // Stubbing only the first leaves the upload cases hitting the network and
  // failing on DNS, which is exactly how this was found.
  vi.stubGlobal('fetch', record);

  const tokens = new TokenStore();
  tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
  const client = new HttpClient({ baseUrl: BASE, tokens, fetchImpl: record });
  api = createApiClient(client);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api.underlay', () => {
  it('get: GET /projects/:id/underlay, parsed into the record', async () => {
    const record = await api.underlay.get('p1');

    expect(calls[0]?.method).toBe('GET');
    expect(calls[0]?.url).toBe(`${BASE}/projects/p1/underlay`);
    expect(record).toEqual(WIRE_UNDERLAY);
  });

  it('get: 404 `no_underlay` is NOT an error — it answers null', async () => {
    respond = () => jsonResponse(404, NO_UNDERLAY_PROBLEM);

    await expect(api.underlay.get('p1')).resolves.toBeNull();
  });

  it('get: any OTHER 404 still throws — "no such project" is a real failure', async () => {
    respond = () =>
      jsonResponse(404, {
        code: 'not_found',
        message: 'No such project.',
        action: 'Check the link and try again.',
      });

    await expect(api.underlay.get('p1')).rejects.toBeInstanceOf(AppError);
  });

  it('get: a malformed record becomes a malformed-response error, not a TypeError', async () => {
    // `mmPerPx: 0` violates the server's own `gt=0`; the schema is the gate.
    respond = () => jsonResponse(200, { ...WIRE_UNDERLAY, mmPerPx: 0 });

    await expect(api.underlay.get('p1')).rejects.toMatchObject({
      code: 'malformed_response',
    });
  });

  it('upload: POSTs the RAW bytes with the file’s own content type', async () => {
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'survey.png', {
      type: 'image/png',
    });

    const record = await api.underlay.upload({ projectId: 'p1', file });

    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.url).toBe(`${BASE}/projects/p1/underlay/image`);
    expect(calls[0]?.contentType).toBe('image/png');
    // The blob itself, not JSON and not a FormData wrapper.
    expect(calls[0]?.rawBody).toBe(file);
    expect(record.objectKey).toBe(WIRE_UNDERLAY.objectKey);
  });

  it('upload: a blob with no declared type still names a type, never multipart', async () => {
    const blob = new Blob([new Uint8Array([0xff, 0xd8, 0xff])]);

    await api.underlay.upload({ projectId: 'p1', file: blob });

    expect(calls[0]?.contentType).toBe('application/octet-stream');
    expect(calls[0]?.contentType?.startsWith('multipart/')).toBe(false);
  });

  it('patch: PATCH with ONLY the fields given (extra="forbid" on the server)', async () => {
    respond = () => jsonResponse(200, { ...WIRE_UNDERLAY, opacity: 0.8 });

    const updated = await api.underlay.patch('p1', { opacity: 0.8 });

    expect(calls[0]?.method).toBe('PATCH');
    expect(calls[0]?.url).toBe(`${BASE}/projects/p1/underlay`);
    expect(calls[0]?.body).toEqual({ opacity: 0.8 });
    expect(updated.opacity).toBe(0.8);
  });

  it('patch: calibration goes up as mmPerPx plus an INTEGER-mm origin', async () => {
    respond = () => jsonResponse(200, WIRE_UNDERLAY);

    await api.underlay.patch('p1', { mmPerPx: 3.75, originXMm: -9000, originYMm: 12500 });

    expect(calls[0]?.body).toEqual({ mmPerPx: 3.75, originXMm: -9000, originYMm: 12500 });
  });

  it('remove: DELETE /projects/:id/underlay, answering the Ack', async () => {
    respond = () => jsonResponse(200, { ok: true });

    await expect(api.underlay.remove('p1')).resolves.toEqual({ ok: true });
    expect(calls[0]?.method).toBe('DELETE');
    expect(calls[0]?.url).toBe(`${BASE}/projects/p1/underlay`);
  });

  it('remove: 404 `no_underlay` DOES throw here — deleting nothing is a mistake', async () => {
    // The asymmetry with `get` is deliberate: "there is nothing to show" is a
    // normal state, "you asked me to delete something that is not there" is not.
    respond = () => jsonResponse(404, NO_UNDERLAY_PROBLEM);

    await expect(api.underlay.remove('p1')).rejects.toBeInstanceOf(AppError);
  });
});
