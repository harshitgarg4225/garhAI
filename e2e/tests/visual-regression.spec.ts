/**
 * Visual regression (§16): "Playwright screenshots of options screen, 3D w/
 * facade, one sheet — 0.1% pixel tolerance." This file is the "3D w/ facade"
 * third; the options screen and the sheet belong to their own phases' suites.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * SKIPPED UNTIL CI CAN HOLD A BASELINE — the reasons, named
 * ════════════════════════════════════════════════════════════════════════════
 * 1. **No committed baseline exists, and this machine must not mint one.**
 *    `toHaveScreenshot` self-blesses on first run — whatever THIS GPU, driver
 *    and font stack render becomes "correct". A baseline is only meaningful
 *    when generated once on the CI runner class that will judge against it
 *    (`--update-snapshots` on the runner, committed in the same PR), and no
 *    CI runner has executed this suite yet (the repo has never been
 *    `pnpm install`ed — see DECISIONS.md's lockfile row).
 * 2. **The canvas label font is a known gap.** `/fonts/inter-medium.woff` is
 *    not in the repository (`make asset-audit` names it a RELEASE BLOCKER).
 *    Until it lands, canvas text falls back per-machine, and a screenshot
 *    diff would measure fonts, not the Phase-5 geometry.
 * 3. **The demo project is the §17 fixture, and its facade state ships with
 *    the seed.** The seeded demo carries a solved plan with a facade applied;
 *    asserting on anything else would pin pixels of an arbitrary test plan
 *    rather than the one fixture tours, budgets and goldens share.
 *
 * Turning this on = drop the font in (asset-audit goes clean), run once on CI
 * with `--update-snapshots`, commit `three-d-facade.png`, delete the
 * `test.skip`. The body below is written and reviewed so that is the WHOLE
 * change — no configuration invented later under pressure.
 *
 * DETERMINISM NOTES (already handled in the body):
 *  · the sun is pinned to a fixed date/time by driving the real scrubber
 *    controls — "screenshot at whatever time the CI clock says" would diff
 *    every run (the sun store deliberately initialises to now);
 *  · `animations: 'disabled'` and a settle wait on the status chip keep the
 *    demand-frameloop canvas from being caught mid-tween;
 *  · the storey filter is left at "All" and the camera at the deterministic
 *    first-entry fit.
 */

import { expect, test } from '@playwright/test';

import { findDemoProject, signIn } from '../support/api';
import { APP_URL, DEMO_EMAIL } from '../support/env';
import { signInThroughUi, statusChip3d } from '../support/ui';

test.describe('@visual Phase 5 — 3D with facade, 0.1% tolerance', () => {
  test.skip(
    true,
    'Skipped until CI holds the baseline: (1) no committed three-d-facade.png — a first ' +
      'run would self-bless this machine\'s GPU/fonts as truth; (2) /fonts/inter-medium.woff ' +
      'is a known asset gap (make asset-audit), so canvas text still differs per machine; ' +
      '(3) needs the seeded §17 demo project (make seed) with its solved plan + facade. ' +
      'To enable: font in, run once on the CI runner with --update-snapshots, commit the ' +
      'PNG, delete this skip. The body is complete — that is the whole change.',
  );

  test('the demo project in 3D, facade applied, matches the baseline', async ({
    page,
    request,
  }) => {
    const session = await signIn(request, DEMO_EMAIL);
    const demo = await findDemoProject(request, session.accessToken);
    expect(demo, 'the §17 seeded demo project must exist (run `make seed`)').not.toBeNull();

    await signInThroughUi(page, DEMO_EMAIL);
    await page.goto(`${APP_URL}/projects/${demo!.id}/3d`);

    // The scene has extruded once the chip reports its first rebuild.
    await expect(statusChip3d(page)).toBeVisible({ timeout: 30_000 });

    // Pin the sun: solstice noon, so the shadow state is a constant of the
    // spec rather than of the CI clock. Driven through the real controls.
    const dateField = page.getByLabel(/date/i).first();
    await dateField.fill('21-06-2026');
    await dateField.press('Enter');
    const slider = page.getByLabel('Time of day, IST');
    await slider.fill('720').catch(async () => {
      // range inputs reject fill on some drivers — walk it to noon instead
      await slider.focus();
      await page.keyboard.press('Home');
      for (let i = 0; i < 48; i += 1) await page.keyboard.press('ArrowRight');
    });

    // Demand frameloop: give the renderer one settle beat after the last
    // control change, then freeze the comparison.
    await page.waitForTimeout(500);

    await expect(page).toHaveScreenshot('three-d-facade.png', {
      // §16 verbatim: 0.1% pixel tolerance.
      maxDiffPixelRatio: 0.001,
      animations: 'disabled',
      // The chip carries live milliseconds; masking it keeps the comparison
      // about geometry, not about how fast this particular runner meshed.
      mask: [statusChip3d(page)],
    });
  });
});
