# `e2e/` — Playwright end-to-end tests

Playbook §16: **smoke on every PR, full happy path nightly.**

## Run it

```bash
docker compose up -d --wait        # the supported way to run this app (§1)
make seed                          # the demo firm + demo project (§17)
pnpm install
pnpm --filter @garh/e2e exec playwright install --with-deps chromium

pnpm e2e:smoke                     # what CI runs on every PR
pnpm e2e                           # everything (most of it is skipped — see below)
pnpm --filter @garh/e2e test:ui    # interactive runner
pnpm --filter @garh/e2e report     # open the last HTML report
```

Nothing is started for you. `global-setup.ts` checks that the API and the web app answer,
that both providers are `mock`, and that the dev OTP echo works — and if any of that is
false it fails with the command that fixes it, instead of with a locator timeout.

Environment (all defaulted for compose):

| Variable          | Default                 | What it is                                    |
| ----------------- | ----------------------- | --------------------------------------------- |
| `APP_URL`         | `http://localhost:5173` | the web app's origin                          |
| `API_URL`         | `http://localhost:8000` | the API's origin, **without** `/api/v1`       |
| `API_PREFIX`      | `/api/v1`               | the versioned prefix                          |
| `GARH_DEMO_EMAIL` | `demo@garh.ai`          | the seeded account the smoke spec signs in as |

## What is here

| File                              | Tag           | State                                                                                                                                                      |
| --------------------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/api-smoke.spec.ts`         | `@smoke`      | **live** — Phase 0 DoD through the API: signup, sign in, create a project, cross-tenant 404, stale-`baseIdx` 409                                           |
| `tests/smoke.spec.ts`             | `@smoke`      | **live** — the UI journey: login (dev OTP) → dashboard → demo project → create project → the shell renders                                                 |
| `tests/happy-path.spec.ts`        | `@happy-path` | **skipped**, step by step, with the phase each step waits on (Phase 9 DoD)                                                                                 |
| `tests/performance.spec.ts`       | `@perf`       | **two live**, the rest skipped — §14 budgets; the frame-measuring helper is real and self-tested                                                           |
| `tests/plan-canvas.spec.ts`       | `@canvas`     | **live** — the Phase 4 DoD: draw a two-room plan, undo/redo, the bye-law chip                                                                              |
| `tests/three-d.spec.ts`           | `@canvas`     | **live** — the Phase 5 DoD: 2D→3D in place, cross-view selection, facade ops 27/28 with §8 wall-freeze, sun-scrub invariants, the §14 <100 ms rebuild      |
| `tests/visual-regression.spec.ts` | `@visual`     | **skipped with named reasons** — §16's "3D w/ facade, 0.1% tolerance" screenshot; the body is complete, enabling it = font + CI baseline + delete the skip |

`support/` holds the API client (`api.ts`), the UI helpers and locators (`ui.ts`), and the
environment (`env.ts`).

## Conventions worth knowing before you add a spec

**One demo sign-in per run.** The API enforces a 60-second OTP resend cooldown per address
(§13). `smoke.spec.ts` spends `demo@garh.ai`'s one code on the login screen; _everything
else_ signs up a throwaway firm with `uniqueEmail()`. This is also why `retries: 0` — a
retry inside that window cannot get a second code and would fail for the wrong reason.

**No `data-testid`.** Every locator is a role, a label or visible text, because those
selectors also assert something a user depends on. `getByLabel('Work email')` breaking means
the field lost its label — a real §15 accessibility regression that a test id would have
hidden. The one reserved exception is `[data-garh-canvas]` on the WebGL surface, which has no
accessible structure to select and which already exists as a keyboard-scoping contract in
`apps/web/src/lib/keymap.ts`.

**Skipped, not absent.** A future phase's spec is written out and `test.skip`ped with the
phase named. It then appears in every report as an explicit "not yet", which is the honest
state; a commented-out file appears as nothing and gets written under deadline pressure.

**Reports land at the repo root.** `outputDir: '../test-results'` and the HTML report's
`outputFolder: '../playwright-report'` match the paths `.github/workflows/ci.yml` uploads
from. Keeping them inside `e2e/` would upload nothing, and `if-no-files-found: ignore` means
nobody would notice for months.

**Locale is `en-IN` / `Asia/Kolkata`.** A date rendered as MM-DD-YYYY or a number grouped
`1,234,567` instead of `12,34,567` is a §15 bug, and it should fail here rather than in front
of a client.

## Turning on the skipped specs

1. delete the `test.skip(...)` at the top of the test;
2. replace the `expect(true).toBeTruthy()` placeholders with real locators — the comment above
   each one says what to assert;
3. keep the step names: they are what the nightly report reads like.

The dependency order is deliberate. The copilot steps cannot be written before the solver,
because they edit what the solver produced; the perf budgets need the canvas; the share-link
step needs `/share/:token` to be registered in the router (it is deliberately absent today —
see the comment in `apps/web/src/routes.tsx`).

## Not covered here, on purpose

- **Visual regression** (§16: options screen, 3D with facade, one sheet, 0.1% tolerance) —
  needs the screens to exist, and a baseline committed on the same CI image, or every run is
  a diff.
- **Lighthouse ≥85 on the dashboard** (Phase 9 DoD) — a separate tool against a production
  build, not a Playwright assertion against the dev server.
- **Load test** (50 concurrent solver jobs queue gracefully) — belongs with the worker, not
  in a browser.
