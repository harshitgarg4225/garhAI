/**
 * The §16 smoke spec — runs on every PR.
 *
 * §16's target is *"login → open demo → edit wall → undo → compliance chip"*. The last three
 * need the Phase 4 canvas, so the Phase 0 journey is:
 *
 *     login (dev OTP) → dashboard → open the demo project → create a project → the shell renders
 *
 * and the canvas half is written out, skipped, in `happy-path.spec.ts` rather than left to be
 * invented later.
 *
 * Phase 2 extends the same journey on the fresh project's Brief tab:
 *
 *     empty states teach → 30×40 ft boundary → Bengaluru pack → setback chips →
 *     compliance strip re-checks live → pasted brief moves the completeness meter →
 *     … → DXF import round-trip (the one step that needs the drawings worker)
 *
 * The DXF test sits near the END of the whole journey on purpose: `serial` mode skips
 * everything after a failure, and a stopped worker container should not mask a broken
 * form or tab.
 *
 * ## One journey, one session
 *
 * The whole file is a single serial journey with one page. That is not a shortcut: the API
 * enforces a 60-second OTP resend cooldown per address (§13), so signing in once per test
 * would rate-limit the suite against itself. `test.describe.serial` also means a failure
 * early on skips the rest instead of reporting five failures for one cause.
 *
 * ## Why the demo user
 *
 * §17 makes the demo project the universal fixture. Signing in as `demo@garh.ai` is what a
 * new user does on their first visit (delight rule: "seeded demo project opens on first
 * login"), so it is what the smoke test does too. `api-smoke.spec.ts` uses throwaway
 * accounts for everything else so this stays the only demo sign-in per run.
 */

import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { APP_URL, DEMO_EMAIL, DEMO_FIRM_NAME } from '../support/env';
import {
  collectConsoleErrors,
  createProjectThroughUi,
  expectNoConsoleErrors,
  PROJECT_TABS,
  signInThroughUi,
  tabLink,
} from '../support/ui';

test.describe.configure({ mode: 'serial' });

test.describe('@smoke Phase 0: login, dashboard, project shell', () => {
  let page: Page;
  let consoleErrors: string[];

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    consoleErrors = collectConsoleErrors(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('the login screen loads and explains itself', async () => {
    await page.goto(`${APP_URL}/login`);

    await expect(page.getByRole('heading', { name: 'Garh AI' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    // No password field anywhere: email OTP is the whole auth story (§13).
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    // The compliance disclaimer is a legal requirement, not decoration.
    await expect(page.getByText(/compliance checks are advisory/i)).toBeVisible();
  });

  test('an unauthenticated deep link bounces to /login', async () => {
    await page.goto(`${APP_URL}/projects/00000000-0000-0000-0000-000000000000/plan`);
    await page.waitForURL(/\/login$/, { timeout: 15_000 });
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  });

  test('login with the dev OTP lands on the dashboard', async () => {
    await signInThroughUi(page, DEMO_EMAIL);

    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible();
    // The shell knows who is signed in — the firm name comes from the API, not the token.
    await expect(page.getByText(DEMO_FIRM_NAME).first()).toBeVisible();
    expect(new URL(page.url()).pathname).toBe('/');
  });

  test('the seeded demo project is on the dashboard (§17)', async () => {
    // Skipped, not failed, when the stack was never seeded: `make seed` is a separate
    // command and "you forgot to seed" should read as that, not as a product bug. CI runs
    // the seed step, so this always executes there.
    const demoCard = page.getByRole('link', { name: /demo/i }).first();
    const seeded = await demoCard.isVisible().catch(() => false);
    test.skip(!seeded, 'No demo project on the dashboard — run `make seed`.');

    await demoCard.click();
    await page.waitForURL(/\/projects\/[0-9a-f-]{36}\/brief$/, { timeout: 15_000 });

    // The demo project opens with its plot and brief already filled in, which is what makes
    // it a usable first-run fixture rather than an empty shell with a label.
    await expect(page.getByRole('link', { name: 'Brief', exact: true })).toBeVisible();
    await expect(page.getByText(/30/).first()).toBeVisible();

    await page.goto(`${APP_URL}/`);
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible();
  });

  test('create an empty project — the Phase 0 DoD sentence', async () => {
    const name = `Smoke project ${Date.now().toString(36)}`;
    const projectId = await createProjectThroughUi(page, name);

    expect(projectId).toMatch(/^[0-9a-f-]{36}$/);
    await expect(page.getByText(name).first()).toBeVisible();
  });

  test('the project shell renders all six tabs', async () => {
    for (const tab of PROJECT_TABS) {
      await expect(tabLink(page, tab), `the ${tab} tab is missing`).toBeVisible();
    }
    // Brief is the landing tab.
    await expect(tabLink(page, 'Brief')).toHaveAttribute('aria-current', 'page');
  });

  // ───────────────────────────────────────────────────────────────────────
  // Phase 2: plot, rules, brief (the playbook Phase 2 DoD, end to end)
  // ───────────────────────────────────────────────────────────────────────

  test('the empty project teaches: draw or import a plot, describe or fill a brief', async () => {
    // Plot side (§15 "empty states teach"): the editor's own empty state plus
    // the always-visible import path.
    await expect(page.getByText('No plot boundary yet')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create boundary' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Import DXF' })).toBeVisible();
    // Brief side: the completeness meter at 0 says where to start, and the
    // free-text path is offered right next to it.
    await expect(page.getByText('Nothing captured yet')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Read this brief' })).toBeVisible();
  });

  test('one click draws the classic 30 × 40 ft plot, with gaj alongside (§15)', async () => {
    // RectQuickStart defaults to 30 × 40 ft (9144 × 12192 mm — exact integers).
    await page.getByRole('button', { name: 'Create boundary' }).click();
    // 1,200 sq ft exactly; gaj ride along per the Indian-formatting rule.
    await expect(page.getByText(/1,200(\.\d)? sq ft/).first()).toBeVisible();
    await expect(page.getByText(/133 gaj/).first()).toBeVisible();
  });

  test('picking the Bengaluru pack resolves setback values from seeded rules', async () => {
    await page.getByLabel('Rule preset').selectOption('blr');

    // The resolver fetches the pack and the assumption chips fill in. "not set"
    // disappearing is the assertion that a real value resolved for THIS plot.
    const front = page.locator('li', { hasText: 'Front setback' }).first();
    await expect(front).toBeVisible({ timeout: 15_000 });
    await expect(front).not.toContainText('not set');

    // Seed honesty is a locked decision: pack values are provisional and say so.
    await expect(page.getByText(/seed values/i).first()).toBeVisible();
  });

  test('the compliance strip re-checks live once the plot ops are confirmed', async () => {
    // The shell re-fetches GET /compliance (debounced ≤500ms) whenever the
    // server confirms an op group. With a boundary drawn, the engine can
    // evaluate, so "nothing to check yet" must give way to real results —
    // either chips or an honest "all N checks passed". Never a blank.
    const strip = page.getByRole('region', { name: 'Compliance' });
    await expect(strip).toBeVisible();
    await expect(strip).not.toContainText('Nothing to check yet', { timeout: 15_000 });
  });

  test('a pasted brief fills the form and moves the completeness meter', async () => {
    const ring = page.getByRole('progressbar', { name: 'Brief completeness' });
    await expect(ring).toHaveAttribute('aria-valuenow', '0');

    await page
      .getByLabel('Client brief text')
      .fill('3BHK G+1 for a family of four, pooja room, one covered parking, budget 60 lakh');
    await page.getByRole('button', { name: 'Read this brief' }).click();

    // The parse is a suggestion, never a silent apply: the review panel shows
    // what was understood and what was assumed, and the meter has NOT moved yet.
    await expect(page.getByText('What we understood')).toBeVisible({ timeout: 30_000 });
    await expect(ring).toHaveAttribute('aria-valuenow', '0');

    await page.getByRole('button', { name: 'Use this brief' }).click();

    // One op group applied; the meter re-computes from the store.
    await expect(ring).not.toHaveAttribute('aria-valuenow', '0');
    const value = Number(await ring.getAttribute('aria-valuenow'));
    expect(value, 'completeness should rise after applying a parsed brief').toBeGreaterThan(0);
  });

  test('every tab renders something honest rather than a blank panel', async () => {
    // Delight rule: "empty states teach", and the unfinished tabs must say which phase
    // brings them rather than showing a fake canvas.
    for (const tab of ['Plan', '3D', 'Renders', 'Sheets', 'Compliance'] as const) {
      await tabLink(page, tab).click();
      await expect(tabLink(page, tab)).toHaveAttribute('aria-current', 'page');
      // Something must be on screen: a heading, an empty state, or a skeleton — never nothing.
      await expect(page.locator('main, [role="main"]').first()).not.toBeEmpty();
    }
    await tabLink(page, 'Brief').click();
  });

  test('a reload keeps the session (the refresh cookie restores it)', async () => {
    const before = page.url();
    await page.reload();
    await expect(page.getByRole('link', { name: 'Brief', exact: true })).toBeVisible({
      timeout: 20_000,
    });
    expect(page.url()).toBe(before);
  });

  test('a DXF round-trips: upload → worker parse → layer picker → boundary op', async () => {
    // NEEDS THE DRAWINGS WORKER (compose runs it; `docker compose up` is the
    // supported topology). If this fails with the "taking much longer" panel,
    // check the worker container before suspecting the UI. Deliberately near
    // the end of the journey: serial mode skips everything after a failure,
    // and a stopped worker should mask as little of the UI suite as possible.
    const fixture = fileURLToPath(new URL('../../fixtures/dxf/plot_rect_mm.dxf', import.meta.url));

    await page.getByRole('button', { name: 'Import DXF' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    await dialog.getByLabel('DXF file').setInputFiles(fixture);

    // Upload → queued → parsed. The fixture carries one closed ring on layer
    // PLOT (30 × 40 ft again) plus an open polyline on ROADS that must be
    // skipped, not imported.
    await expect(dialog.getByText(/closed boundaries found/i)).toBeVisible({ timeout: 60_000 });
    await expect(dialog.getByText(/1,200(\.\d)? sq ft/).first()).toBeVisible();

    await dialog.getByRole('button', { name: 'Use this boundary' }).click();

    // The op applied: dialog gone, boundary (still 1,200 sq ft) on screen.
    await expect(dialog).not.toBeVisible();
    await expect(page.getByText(/1,200(\.\d)? sq ft/).first()).toBeVisible();
  });

  test('signing out returns to the login screen', async () => {
    await page.goto(`${APP_URL}/`);
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible();

    // `AppShell` renders it as an icon button with an accessible name — which is exactly
    // why the locator can be a role query (§15 accessibility: no unlabelled icon buttons).
    await page.getByRole('button', { name: 'Sign out' }).first().click();

    await page.waitForURL(/\/login$/, { timeout: 15_000 });
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  });

  test('the whole journey logged no console error', async () => {
    // A page that renders correctly while throwing on every keystroke is not passing.
    // Vite's dev server emits HMR chatter, so only genuine errors are collected.
    expectNoConsoleErrors(
      consoleErrors.filter((message) => !/\[vite\]|favicon|apple-touch-icon/i.test(message)),
    );
  });
});
