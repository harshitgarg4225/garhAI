/**
 * Thin API client for the specs.
 *
 * Two uses, both deliberate:
 *
 * 1. **Arrange, not act.** Creating a second firm to prove tenant isolation should not be
 *    twelve UI interactions; it should be two API calls. The UI is what the spec is *about*,
 *    so everything the spec is not about goes through here.
 * 2. **A faster failure signal.** `api-smoke.spec.ts` walks the whole Phase 0 DoD through
 *    this client with no browser at all, so "the stack is broken" and "the UI moved" are
 *    different, separately-named failures.
 *
 * The dev OTP echo (`devCode`) is what makes signing in possible without a mail provider.
 * It is double-gated server-side (`garh_api.auth.dev_echo_otp_enabled`: dev/test only), so
 * a spec that relies on it cannot accidentally pass against staging — it fails, loudly,
 * which is the correct outcome.
 */

import type { APIRequestContext } from '@playwright/test';
import { apiBase } from './env';

export interface Problem {
  code: string;
  message: string;
  action: string;
  requestId?: string;
}

export interface Session {
  accessToken: string;
  expiresIn: number;
  user: { id: string; email: string; name: string; role: string };
  firm: { id: string; name: string };
}

export interface Project {
  id: string;
  name: string;
  status: string;
  units: string;
  cityPack: string | null;
  demo: boolean;
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function expectOk(
  label: string,
  response: { ok(): boolean; status(): number; text(): Promise<string> },
): Promise<void> {
  if (response.ok()) return;
  throw new Error(`${label} failed with ${response.status()}: ${(await response.text()).slice(0, 500)}`);
}

/** `POST /auth/otp`, returning the echoed code. Throws a useful error when the echo is off. */
export async function requestOtp(request: APIRequestContext, email: string): Promise<string> {
  const response = await request.post(`${apiBase()}/auth/otp`, { data: { email } });
  await expectOk(`POST /auth/otp for ${email}`, response);
  const body = (await response.json()) as { devCode?: string | null };
  if (typeof body.devCode !== 'string' || body.devCode.length === 0) {
    throw new Error(
      `The API did not echo an OTP for ${email}. The e2e suite signs in with the dev echo; ` +
        'start the API with APP_ENV=dev (compose does) and DEV_ECHO_OTP unset or 1.',
    );
  }
  return body.devCode;
}

/** OTP issue + verify, for an account that already exists. */
export async function signIn(request: APIRequestContext, email: string): Promise<Session> {
  const code = await requestOtp(request, email);
  const response = await request.post(`${apiBase()}/auth/verify`, { data: { email, code } });
  await expectOk(`POST /auth/verify for ${email}`, response);
  return (await response.json()) as Session;
}

/** Create a brand-new firm and sign its admin in. Used to get a *second* tenant. */
export async function signUpFirm(
  request: APIRequestContext,
  options: { email: string; firmName?: string; name?: string },
): Promise<Session> {
  const response = await request.post(`${apiBase()}/auth/signup`, {
    data: {
      email: options.email,
      firmName: options.firmName ?? 'E2E Studio',
      name: options.name ?? 'E2E Architect',
    },
  });
  await expectOk(`POST /auth/signup for ${options.email}`, response);
  const body = (await response.json()) as { devCode?: string | null };
  if (typeof body.devCode !== 'string') {
    throw new Error('Signup did not echo an OTP; see requestOtp for why that matters.');
  }
  const verified = await request.post(`${apiBase()}/auth/verify`, {
    data: { email: options.email, code: body.devCode },
  });
  await expectOk('POST /auth/verify after signup', verified);
  return (await verified.json()) as Session;
}

export async function createProject(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<Project> {
  const response = await request.post(`${apiBase()}/projects`, {
    headers: authHeaders(token),
    data: { name },
  });
  await expectOk(`POST /projects (${name})`, response);
  return (await response.json()) as Project;
}

export async function listProjects(
  request: APIRequestContext,
  token: string,
): Promise<Project[]> {
  const response = await request.get(`${apiBase()}/projects`, { headers: authHeaders(token) });
  await expectOk('GET /projects', response);
  const body = (await response.json()) as { items: Project[] };
  return body.items;
}

/** The demo project the seeder creates, or `undefined` if the stack was never seeded. */
export async function findDemoProject(
  request: APIRequestContext,
  token: string,
): Promise<Project | undefined> {
  return (await listProjects(request, token)).find((project) => project.demo);
}

/**
 * Fetch a project as somebody else. Returns the status and the problem body so a spec can
 * assert the §13 answer (404, never 403, never 200).
 */
export async function getProjectAs(
  request: APIRequestContext,
  token: string,
  projectId: string,
): Promise<{ status: number; problem: Problem | null }> {
  const response = await request.get(`${apiBase()}/projects/${projectId}`, {
    headers: authHeaders(token),
  });
  let problem: Problem | null = null;
  if (!response.ok()) {
    try {
      problem = (await response.json()) as Problem;
    } catch {
      problem = null;
    }
  }
  return { status: response.status(), problem };
}

/** `GET /meta` — what the app reads before it renders anything. */
export async function meta(request: APIRequestContext): Promise<Record<string, unknown>> {
  const response = await request.get(`${apiBase()}/meta`);
  await expectOk('GET /meta', response);
  return (await response.json()) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// The op log and the folded model (Phase 1 + Phase 4)
// ---------------------------------------------------------------------------

export interface OpEnvelope {
  type: string;
  payload: Record<string, unknown>;
  clientOpId: string;
  groupId?: string;
}

export interface OpsPage {
  ops: Array<{ idx: number; type: string; payload: Record<string, unknown>; groupId?: string }>;
  headIdx: number;
}

/**
 * `POST /projects/:id/ops` — arrange geometry without driving the canvas.
 *
 * The Phase-4 spec uses this for the PLOT (a boundary and a city pack are the
 * compliance engine's preconditions, and drawing one is Phase 2's spec, not
 * this one). It never uses it for walls: a canvas spec that appends wall ops
 * over HTTP has stopped testing the canvas.
 */
export async function appendOps(
  request: APIRequestContext,
  token: string,
  projectId: string,
  ops: Array<{ type: string; payload: Record<string, unknown> }>,
  baseIdx: number,
): Promise<{ headIdx: number }> {
  const response = await request.post(`${apiBase()}/projects/${projectId}/ops`, {
    headers: authHeaders(token),
    data: {
      ops: ops.map((op, i) => ({ ...op, clientOpId: `e2e-${Date.now()}-${i}` })),
      baseIdx,
      source: 'manual',
    },
  });
  await expectOk(`POST /projects/${projectId}/ops`, response);
  return (await response.json()) as { headIdx: number };
}

/** `GET /projects/:id/ops?since=` — what the server actually stored. */
export async function opsSince(
  request: APIRequestContext,
  token: string,
  projectId: string,
  since = -1,
): Promise<OpsPage> {
  const response = await request.get(
    `${apiBase()}/projects/${projectId}/ops?since=${since}&limit=500`,
    { headers: authHeaders(token) },
  );
  await expectOk(`GET /projects/${projectId}/ops`, response);
  return (await response.json()) as OpsPage;
}

export interface FoldedModel {
  headIdx: number;
  model: {
    house: {
      storeys: Array<{ id: string; name: string }>;
      walls: Array<{
        id: string;
        storeyId: string;
        a: { x: number; y: number };
        b: { x: number; y: number };
        thicknessMm: number;
      }>;
      rooms: Array<{
        id: string;
        storeyId: string;
        type: string;
        name: string;
        areaMm2: number;
        /** Clear inside-face ring, integer mm — how a spec aims a click. */
        polygon: Array<{ x: number; y: number }>;
      }>;
      /** Phase 5: the isolated facade sub-model (§8). */
      facade: {
        kitId: string | null;
        seed: number;
        colorwayId: string | null;
        components: Array<{
          id: string;
          kind: string;
          params: Record<string, unknown>;
        }>;
      };
    };
  };
}

/**
 * `GET /projects/:id/model` — the SERVER's fold, not the browser's.
 *
 * This is what makes "all ops sync" a real assertion rather than a screenshot:
 * the rooms the server detected from the walls the canvas drew are the same
 * rooms the compliance engine will be asked about.
 */
export async function projectModel(
  request: APIRequestContext,
  token: string,
  projectId: string,
): Promise<FoldedModel> {
  const response = await request.get(`${apiBase()}/projects/${projectId}/model`, {
    headers: authHeaders(token),
  });
  await expectOk(`GET /projects/${projectId}/model`, response);
  return (await response.json()) as FoldedModel;
}

export interface ComplianceReport {
  evaluated: boolean;
  results: Array<{
    ruleId: string;
    status: string;
    message?: string | null;
    elements?: string[];
  }>;
}

/** `GET /projects/:id/compliance` — the same report the chip strip renders. */
export async function complianceReport(
  request: APIRequestContext,
  token: string,
  projectId: string,
): Promise<ComplianceReport> {
  const response = await request.get(`${apiBase()}/projects/${projectId}/compliance`, {
    headers: authHeaders(token),
  });
  await expectOk(`GET /projects/${projectId}/compliance`, response);
  return (await response.json()) as ComplianceReport;
}
