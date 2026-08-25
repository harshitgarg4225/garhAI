/**
 * Pre-flight: fail with an instruction, never with a 30-second timeout.
 *
 * Golden rule 9 is about the product, but a test suite that answers "the stack is not
 * running" with `TimeoutError: locator.click` wastes exactly the same amount of somebody's
 * afternoon. This checks the three things every spec needs and names the command that fixes
 * each one.
 *
 * It deliberately does **not** start anything. `docker compose up` is the supported way to
 * run this app (playbook §1); a Playwright `webServer` block would be a second, CI-only
 * wiring that nobody uses in anger and that would drift.
 */

import { request } from '@playwright/test';
import { API_URL, APP_URL, DEMO_EMAIL, apiBase } from './support/env';

const HINT_STACK = 'Start it with `docker compose up -d --wait` from the repo root.';
const HINT_SEED = 'Seed it with `make seed` (or `docker compose exec api python -m garh_api.seed`).';

async function main(): Promise<void> {
  const context = await request.newContext({ timeout: 10_000 });
  const problems: string[] = [];

  // 1. The API is up.
  let apiUp = false;
  try {
    const health = await context.get(`${API_URL}/healthz`);
    apiUp = health.ok();
    if (!apiUp) problems.push(`GET ${API_URL}/healthz returned ${health.status()}. ${HINT_STACK}`);
  } catch (error) {
    problems.push(`Cannot reach the API at ${API_URL}: ${(error as Error).message}. ${HINT_STACK}`);
  }

  // 2. The web app is up.
  try {
    const page = await context.get(APP_URL);
    if (!page.ok()) problems.push(`GET ${APP_URL} returned ${page.status()}. ${HINT_STACK}`);
  } catch (error) {
    problems.push(`Cannot reach the web app at ${APP_URL}: ${(error as Error).message}. ${HINT_STACK}`);
  }

  if (apiUp) {
    // 3. Providers are mocked, so no spec needs an API key or a GPU (locked decision).
    try {
      const meta = (await (await context.get(`${apiBase()}/meta`)).json()) as {
        providers?: Record<string, string>;
        env?: string;
      };
      const providers = meta.providers ?? {};
      for (const name of ['llm', 'render']) {
        if (providers[name] !== 'mock') {
          problems.push(
            `PROVIDER_${name.toUpperCase()} is "${providers[name]}", not "mock". The e2e suite ` +
              'runs with zero API keys and zero GPUs by design.',
          );
        }
      }
      if (providers['modelEngine'] !== 'ready') {
        problems.push('The API reports modelEngine != ready, so no op can be validated.');
      }
    } catch (error) {
      problems.push(`GET ${apiBase()}/meta failed: ${(error as Error).message}`);
    }

    // 4. The dev OTP echo works and the API can write to Postgres.
    //
    // Checked with a **throwaway** signup, deliberately not with `demo@garh.ai`: the API
    // enforces a 60-second resend cooldown per address (§13), and the smoke spec spends the
    // demo user's one code on the login screen. A pre-flight that burned it first would
    // turn this check into the reason the suite fails.
    try {
      const email = `preflight-${Date.now().toString(36)}@studio.test`;
      const signup = await context.post(`${apiBase()}/auth/signup`, {
        data: { email, firmName: 'Pre-flight', name: 'Pre-flight' },
      });
      if (!signup.ok()) {
        problems.push(`POST /auth/signup returned ${signup.status()}. ${HINT_STACK}`);
      } else {
        const body = (await signup.json()) as { devCode?: string | null };
        if (typeof body.devCode !== 'string' || body.devCode.length === 0) {
          problems.push(
            'The API does not echo sign-in codes, so no spec can sign in. Run the API with ' +
              'APP_ENV=dev (compose does) and DEV_ECHO_OTP unset or 1.',
          );
        }
      }
    } catch (error) {
      problems.push(`Sign-in pre-flight failed: ${(error as Error).message}`);
    }
  }

  if (problems.length === 0) {
    // Not fatal: the seed is only needed by the specs that open the demo project, and they
    // say so themselves. Warning here turns "why is the demo project missing" into one line.
    console.log(
      `[e2e] stack is up (api ${API_URL}, web ${APP_URL}). Smoke signs in as ${DEMO_EMAIL}; ` +
        `if that account does not exist yet: ${HINT_SEED}`,
    );
  }

  await context.dispose();

  if (problems.length > 0) {
    throw new Error(
      ['The e2e suite cannot run:', ...problems.map((line) => `  - ${line}`)].join('\n'),
    );
  }
}

export default main;
