/**
 * Phase 0's Definition of Done, walked through the API with no browser.
 *
 *     docker compose up -> login -> create empty project
 *     ... and a cross-tenant access attempt proves 404/403.
 *
 * This runs before the UI smoke (`dependencies: ['api']` in the config) so that a broken
 * stack and a moved button are two differently-named failures. It is also the one e2e file
 * that will keep working unchanged through Phases 1-9, which makes it the honest answer to
 * "is the deployment alive".
 *
 * The tenancy assertions duplicate `apps/api/tests/test_cross_tenant.py` on purpose. That
 * suite proves the guarantee against the repository layer with a truncated database; this
 * one proves it survives the real deployment — a reverse proxy that strips an Authorization
 * header, or a cache in front of `/projects/:id`, would pass the pytest suite and fail here.
 *
 * **Nothing in this file touches `demo@garh.ai`.** The API enforces a 60-second OTP resend
 * cooldown per address (§13) and `smoke.spec.ts` spends the demo user's one code on the
 * login screen. Every account here is signed up fresh.
 */

import { expect, test } from '@playwright/test';
import { createProject, getProjectAs, listProjects, meta, signUpFirm } from '../support/api';
import { apiBase, uniqueEmail } from '../support/env';

test.describe('@smoke Phase 0 DoD (API)', () => {
  test('the API reports itself healthy and fully mocked', async ({ request }) => {
    const body = await meta(request);
    const providers = body.providers as Record<string, string>;

    expect(providers.llm, 'the suite must run with no API keys').toBe('mock');
    expect(providers.render, 'the suite must run with no GPU').toBe('mock');
    expect(providers.modelEngine, 'without the model core no op can be validated').toBe('ready');

    const limits = body.limits as Record<string, number>;
    expect(limits.opsPerSecond).toBe(60);
    expect(limits.opSnapshotInterval).toBe(200);
    expect(limits.signedUrlTtlSeconds).toBeLessThanOrEqual(600);
  });

  test('signup, sign in, and create an empty project', async ({ request }) => {
    const email = uniqueEmail('dod');
    const session = await signUpFirm(request, { email, firmName: 'Phase Zero Studio' });

    expect(session.accessToken.split('.')).toHaveLength(3);
    expect(session.expiresIn, '§11: 15-minute access token').toBe(900);
    expect(session.user.email).toBe(email);
    expect(session.user.role).toBe('admin');
    expect(session.firm.name).toBe('Phase Zero Studio');

    const project = await createProject(request, session.accessToken, 'Empty project');
    expect(project.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(project.status).toBe('draft');
    expect(project.units, 'ft-in is the Indian default').toBe('ft-in');
    expect(project.demo).toBe(false);

    const mine = await listProjects(request, session.accessToken);
    expect(mine.map((item) => item.id)).toEqual([project.id]);
  });

  test('a cross-tenant read is 404, not 403 and never 200', async ({ request }) => {
    const alice = await signUpFirm(request, {
      email: uniqueEmail('alice'),
      firmName: 'Alice Associates',
    });
    const bob = await signUpFirm(request, {
      email: uniqueEmail('bob'),
      firmName: 'Bob Builders',
    });

    const secret = await createProject(request, alice.accessToken, 'Alice private project');

    const asBob = await getProjectAs(request, bob.accessToken, secret.id);
    expect(
      asBob.status,
      'another firm must not learn that this project exists (§13: 404, not 403)',
    ).toBe(404);
    expect(asBob.problem?.code).toBe('not_found');
    expect(asBob.problem?.action, 'golden rule 9: every error says what to do next').toBeTruthy();
    expect(
      JSON.stringify(asBob.problem),
      'the 404 body must not name the project it is hiding',
    ).not.toContain('Alice');

    // Bob's dashboard is empty, and Alice can still read her own project.
    expect(await listProjects(request, bob.accessToken)).toEqual([]);
    const asAlice = await getProjectAs(request, alice.accessToken, secret.id);
    expect(asAlice.status).toBe(200);
  });

  test('a cross-tenant write changes nothing', async ({ request }) => {
    const owner = await signUpFirm(request, { email: uniqueEmail('owner') });
    const intruder = await signUpFirm(request, { email: uniqueEmail('intruder') });
    const project = await createProject(request, owner.accessToken, 'Original name');

    const patched = await request.patch(`${apiBase()}/projects/${project.id}`, {
      headers: { Authorization: `Bearer ${intruder.accessToken}` },
      data: { name: 'Renamed by an intruder' },
    });
    expect(patched.status()).toBe(404);

    const [still] = await listProjects(request, owner.accessToken);
    expect(still?.name).toBe('Original name');
  });

  test('the op sequencer answers a stale baseIdx with 409 and a headIdx', async ({ request }) => {
    const session = await signUpFirm(request, { email: uniqueEmail('ops') });
    const project = await createProject(request, session.accessToken, 'Sequencer');
    const headers = { Authorization: `Bearer ${session.accessToken}` };
    const url = `${apiBase()}/projects/${project.id}/ops`;
    const body = {
      ops: [{ type: 'plot.set_north', payload: { deg: 90 } }],
      baseIdx: -1,
      source: 'manual',
    };

    const first = await request.post(url, { headers, data: body });
    expect(first.status()).toBe(200);
    expect(((await first.json()) as { headIdx: number }).headIdx).toBe(0);

    const stale = await request.post(url, { headers, data: body });
    expect(stale.status(), 'a second append at baseIdx -1 must conflict').toBe(409);
    const problem = (await stale.json()) as { code: string; headIdx: number };
    expect(problem.code).toBe('op_sequence_conflict');
    expect(problem.headIdx, 'the client rebases onto this number').toBe(0);
  });

  test('no credentials means 401 with a problem+json body', async ({ request }) => {
    const response = await request.get(`${apiBase()}/projects`);
    expect(response.status()).toBe(401);
    expect(response.headers()['content-type']).toContain('application/problem+json');
    const body = (await response.json()) as { code: string; action: string };
    expect(body.code).toBe('unauthenticated');
    expect(body.action).toBeTruthy();
  });
});
