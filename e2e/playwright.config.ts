/**
 * Playwright configuration (playbook §16).
 *
 * *"Smoke on every PR (login → open demo → edit wall → undo → compliance chip); full happy
 * path nightly (Phase 9 DoD scenario)."*
 *
 * ## Three projects, three purposes
 *
 * * **api** — the Phase 0 DoD walked with no browser. Runs first, so "the stack is broken"
 *   is a different, faster failure from "the UI moved".
 * * **smoke** — the §16 journey through Chromium. `@smoke` is what CI greps for.
 * * **slow** — the specs that need a canvas or a full happy path. They are `test.skip`ped
 *   with the phase named until those phases land, and this project exists so turning them
 *   on is a matter of deleting a skip, not of writing configuration under pressure.
 *
 * ## Output locations
 *
 * `outputDir` and the HTML report deliberately point **outside** `e2e/`, at the repo root,
 * because that is where `.github/workflows/ci.yml` uploads artefacts from
 * (`playwright-report/` and `test-results/` relative to the workspace). Keeping them here
 * would silently upload nothing, and `if-no-files-found: ignore` means nobody would notice.
 *
 * ## No `webServer`
 *
 * `docker compose up` is the supported way to run this app (§1). A `webServer` block would
 * be a second, CI-only wiring of the stack, which is a thing to maintain and a thing to
 * drift. `global-setup.ts` checks the stack is up and says what to run if it is not.
 */

import { defineConfig, devices } from '@playwright/test';
import { APP_URL } from './support/env';

const isCI = !!process.env.CI;

export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',

  /* Written to the repo root — see the header note. */
  outputDir: '../test-results',

  /* A spec that hangs should fail the job, not occupy a runner for an hour. */
  timeout: 60_000,
  expect: { timeout: 10_000 },
  globalTimeout: isCI ? 20 * 60_000 : 0,

  fullyParallel: false,
  workers: 1,

  /*
   * retries: 0, on purpose.
   *
   * The smoke spec signs in through the OTP screen, and the API enforces a 60-second
   * resend cooldown per address (§13). A retry inside that window cannot get a second code,
   * so it would fail for a reason that has nothing to do with the first failure — turning
   * one honest red into one confusing red. Flakiness here should be fixed, not absorbed.
   */
  retries: 0,

  forbidOnly: isCI,

  reporter: isCI
    ? [['list'], ['html', { outputFolder: '../playwright-report', open: 'never' }], ['github']]
    : [['list'], ['html', { outputFolder: '../playwright-report', open: 'never' }]],

  use: {
    baseURL: APP_URL,
    /* Traces on the first failure: §14 also wants trace-based frame assertions later, and
     * the same artefact answers "what did the page look like when it broke". */
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: isCI ? 'retain-on-failure' : 'off',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    /* Indian defaults, so a date or a number formatted for the wrong locale fails here
     * rather than in front of a client (§15: DD-MM-YYYY, ₹ grouping). */
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
    ignoreHTTPSErrors: true,
  },

  projects: [
    {
      name: 'api',
      testMatch: /api-.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'smoke',
      /* Anchored: an unanchored /smoke\.spec\.ts/ also matches api-smoke.spec.ts, which
       * would run that file twice under two project names. */
      testMatch: /(^|[\\/])smoke\.spec\.ts$/,
      dependencies: ['api'],
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      /*
       * Everything that needs a canvas: the Phase 4 DoD, the Phase 9 happy
       * path and the §14 budgets.
       *
       * The viewport is fixed at 1600×1000 and that matters more than it looks:
       * `plan-canvas.spec.ts` measures the camera scale rather than assuming
       * it, but it still needs enough room to draw a 6.9 m rectangle without
       * the pointer leaving the surface.
       */
      name: 'slow',
      /* three-d = the Phase-5 DoD; copilot + renders = the Phase-6/7 DoDs
       * (both need the canvas: the copilot rail docks beside it and a render
       * is a photograph OF it); visual-regression = the §16 screenshot suite
       * (skipped with named reasons until CI holds a baseline). */
      testMatch:
        /(plan-canvas|happy-path|performance|three-d|copilot|renders|visual-regression)\.spec\.ts/,
      dependencies: ['api'],
      timeout: 5 * 60_000,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } },
    },
  ],
});
