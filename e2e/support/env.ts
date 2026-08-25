/**
 * Where the stack is, and the one account the specs sign in as.
 *
 * `docker compose up` is the supported way to run this app (playbook §1), so the defaults
 * are compose's published ports and CI overrides nothing in the normal case — it exports
 * the same two values it already sets for the `e2e-smoke` job.
 */

/** The web app's origin. Compose publishes the Vite dev server on 5173. */
export const APP_URL = (process.env.APP_URL ?? 'http://localhost:5173').replace(/\/+$/, '');

/** The API's origin — **without** the `/api/v1` prefix (see {@link apiBase}). */
export const API_URL = (process.env.API_URL ?? 'http://localhost:8000').replace(/\/+$/, '');

/** The versioned API prefix. Kept here so no spec spells it out. */
export const API_PREFIX = process.env.API_PREFIX ?? '/api/v1';

export function apiBase(): string {
  return `${API_URL}${API_PREFIX}`;
}

/**
 * The seeded demo account (playbook §17). The smoke spec signs in as this user because
 * `make seed` guarantees it exists and owns the demo project, which is the fixture every
 * later phase's screenshots and perf budgets also use.
 */
export const DEMO_EMAIL = process.env.GARH_DEMO_EMAIL ?? 'demo@garh.ai';

/** Studio Demo — asserted so a renamed seed firm fails loudly instead of silently. */
export const DEMO_FIRM_NAME = 'Studio Demo';

/**
 * A per-run unique email, for specs that must not share the demo account's OTP budget.
 *
 * The API applies a 60-second resend cooldown per address (§13), so two specs asking for a
 * code for the same address inside a minute is a 429 — a real limit doing its job, and a
 * confusing test failure. Anything that signs in more than once uses one of these.
 */
export function uniqueEmail(prefix = 'e2e'): string {
  const stamp = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return `${prefix}-${stamp}@studio.test`;
}
