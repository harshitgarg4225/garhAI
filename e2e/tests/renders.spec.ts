/**
 * The Phase 7 Definition of Done, walked in a browser.
 *
 *     "submit a mock render → a result comes back → edit a wall → the stale
 *      banner appears."
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE ONE STRUCTURAL FACT THIS SPEC IS SHAPED BY
 * ════════════════════════════════════════════════════════════════════════════
 * A render is CAPTURED, not requested: the browser photographs its own live
 * WebGL scene (colour + depth + a Sobel edge map) and posts those three PNGs as
 * the §9 control set. There is exactly one live scene, on the 3D view, and
 * `features/renders/store.ts` refuses to cache capture bytes — a re-render from
 * a tab with no canvas would otherwise ship a stale photograph of an edited
 * model, which is the §9 lie the whole version-pinning design exists to
 * prevent. So the journey below goes *through* the 3D tab, exactly as a user
 * does, and the "New render" button on the Renders tab is asserted to route
 * there rather than to render something it cannot see.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HOW EACH DoD CLAIM IS ASSERTED, HONESTLY
 * ════════════════════════════════════════════════════════════════════════════
 *  · **"submit"** — the real launcher: pick a preset (a `radio` in the "Render
 *    style" group), pick Precise/Explore ("Render mode"), press "Start render".
 *    No store pokes: capture is the part most likely to break, so it must run.
 *  · **"a result comes back"** — the SERVER's own render row (`/render-history`)
 *    reaching `succeeded` with an `outputUrl`, and then the `<img>` for it in
 *    the gallery. Both, because a row without a picture and a picture without a
 *    row are different failures.
 *  · **"edit a wall"** — a real wall drawn with the real tool (`drawWallChain`,
 *    the Phase-4 helper), so the op goes through the sequencer that flips
 *    `stale`.
 *  · **"the stale banner appears"** — the SERVER sets `stale`; the client only
 *    renders it. Asserted in both places: the flag on the history row, and the
 *    §9 sentence on the card. The client never computes staleness (see
 *    `stores/model.ts::selectHeadIdx`), so a spec that only checked the DOM
 *    could pass on a client-side guess.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS SPEC CANNOT ASSERT, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 *  · **The §14 "<1 s e2e" budget.** That budget is on the mock PROVIDER, and it
 *    is asserted where a clock is meaningful: `apps/api/tests/test_render_jobs.py`
 *    times `MockRenderProvider` directly. From a browser the same number also
 *    contains a WebGL readback on SwiftShader, three base64 PNGs over the
 *    network, a queue hop and an SSE round trip — none of which the budget is
 *    about. This spec RECORDS the wall-clock as a test annotation (so a
 *    regression is visible in the report) and only fails on a ceiling generous
 *    enough that tripping it means "broken", not "slow runner".
 *  · **That the image looks like the building.** The mock provider composites a
 *    deterministic placeholder from the capture; whether the capture framed the
 *    house is a pixel claim for `visual-regression.spec.ts`. What IS checked is
 *    that a capture happened at all — a black or empty control map would fail
 *    the launcher's own capture step, which this spec lets run for real.
 *  · **Depth-map correctness.** `RGBADepthPacking` unpacking is verified by
 *    construction and by `renders.test.ts`'s Sobel expectations; no browser
 *    assertion can re-derive it without reimplementing the shader.
 *  · **The 8-shot client pack end to end.** Eight captures, eight jobs and a
 *    zip is minutes of runner time and needs object storage reachable from the
 *    browser (presigned PUT + CORS). The button's ROUTING is asserted here; the
 *    group semantics, derived seeds and archive are asserted server-side in
 *    `test_render_jobs.py`. Turning this on is a `test.skip` deletion once CI
 *    has minio with CORS.
 *  · **A real diffusion provider.** `DiffusersProvider` has never been run
 *    (see the Phase-7 notes). This spec skips unless the stack is on `mock`.
 */

import { expect, test } from '@playwright/test';

import { appendOps, createProject, meta, projectModel, signUpFirm } from '../support/api';
import { API_PREFIX, API_URL, APP_URL, uniqueEmail } from '../support/env';
import {
  canvasBox,
  collectConsoleErrors,
  drawWallChain,
  expectNoConsoleErrors,
  focusCanvas,
  signInThroughUi,
  staleBanner,
  tabLink,
  waitForSaved,
} from '../support/ui';

/** 12 m × 12 m plot — the same fixture geometry the canvas spec uses. */
const PLOT_MM = [
  { x: 0, y: 0 },
  { x: 12_000, y: 0 },
  { x: 12_000, y: 12_000 },
  { x: 0, y: 12_000 },
];

const GROUND_STOREY_ID = 'storey_01J3D00000000000000000000A';

/**
 * The ceiling, not the budget. §14's <1 s is the provider's, asserted in
 * Python; this is "something is wedged" territory for a whole browser journey
 * including a SwiftShader readback.
 */
const RENDER_CEILING_MS = 60_000;

test.describe('@renders Phase 7 DoD — AI renders', () => {
  test.setTimeout(300_000);

  test('capture → mock render → result → edit a wall → stale banner', async ({ page, request }) => {
    const providers = ((await meta(request)).providers ?? {}) as Record<string, string>;
    test.skip(
      providers.render !== 'mock',
      [
        `The stack's render provider is "${providers.render ?? 'unknown'}", not "mock".`,
        'A diffusion render takes minutes and needs a GPU; this spec asserts the §9 PIPELINE',
        '(capture → job → version pinning → stale), which the mock provider exercises fully.',
        'Run the stack with PROVIDER_RENDER=mock.',
      ].join('\n'),
    );

    const consoleErrors = collectConsoleErrors(page);

    const email = uniqueEmail('renders');
    const session = await signUpFirm(request, { email, firmName: 'Renders Test Associates' });
    const token = session.accessToken;
    const project = await createProject(request, token, 'Render DoD');
    const projectId = project.id;

    await test.step('arrange a plot and a storey, then draw a building', async () => {
      // Same two preconditions as the canvas spec: a boundary for the rules
      // engine and a storey for the wall tool to draw on.
      await appendOps(
        request,
        token,
        projectId,
        [
          { type: 'plot.set_boundary', payload: { polygon: PLOT_MM, source: 'manual' } },
          { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000, name: '9m Road' } },
          { type: 'plot.set_reg_profile', payload: { cityPack: 'blr', overrides: {} } },
          {
            type: 'storey.add',
            payload: { id: GROUND_STOREY_ID, index: 0, name: 'Ground Floor', heightMm: 3000 },
          },
        ],
        -1,
      );

      await signInThroughUi(page, email);
      await page.goto(`${APP_URL}/projects/${projectId}/plan`);
      await expect(page.getByRole('toolbar', { name: 'Drawing tools' })).toBeVisible({
        timeout: 20_000,
      });

      // A render of an empty plot is a picture of grass. Draw a closed room so
      // the 3D view has something to photograph.
      await focusCanvas(page);
      const box = await canvasBox(page);
      const start = { x: box.x + box.width * 0.32, y: box.y + box.height * 0.62 };
      await drawWallChain(page, start, [
        { dir: 'right', lengthMm: 6000 },
        { dir: 'up', lengthMm: 4000 },
        { dir: 'left', lengthMm: 6000 },
        { dir: 'down', lengthMm: 4000 },
      ]);
      await waitForSaved(page);

      const model = await projectModel(request, token, projectId);
      expect(model.model.house.walls.length, 'the room never reached the server').toBe(4);
    });

    await test.step('the Renders tab teaches before anything is spent', async () => {
      await tabLink(page, 'Renders').click();
      // §9's Precise/Explore split, in plain words — the tab explains the
      // choice before the architect can make it expensively.
      await expect(page.getByText(/precise/i).first()).toBeVisible();
      await expect(page.getByText(/explore/i).first()).toBeVisible();
      // Nothing rendered yet, so nothing to be stale.
      await expect(staleBanner(page)).toHaveCount(0);
    });

    let elapsedMs = 0;

    await test.step('capture and submit a Precise render from the 3D view', async () => {
      // Deliberately NOT through the Renders tab's "New render" button here:
      // that button hands a PENDING request to the launcher, which then
      // captures and submits by itself (asserted as its own contract at the
      // end). This step is about the pickers, so it opens the launcher by hand.
      await tabLink(page, '3D').click();

      // The launcher only mounts once the live renderer has registered itself
      // through `captureBridge`, so waiting for it also asserts the 3D scene
      // came up — on a runner with no working WebGL this is where it fails,
      // loudly and with the right reason.
      const launcher = page.getByRole('dialog', { name: 'Start a render' });
      const openButton = page.getByRole('button', { name: 'Render', exact: true });
      await expect(
        openButton,
        'the render launcher never appeared on the 3D view — the live canvas never registered ' +
          'a renderer (no WebGL on this runner?), so there is nothing to photograph',
      ).toBeVisible({ timeout: 60_000 });
      await openButton.click();
      await expect(launcher).toBeVisible();

      // Real preset + mode selection: both are `radiogroup`s, and mode is a §9
      // first-class field (it changes ControlNet scale and denoise, not a label).
      await launcher
        .getByRole('radiogroup', { name: 'Render style' })
        .getByRole('radio')
        .first()
        .click();
      await launcher
        .getByRole('radiogroup', { name: 'Render mode' })
        .getByRole('radio')
        .first()
        .click();

      const started = Date.now();
      await launcher.getByRole('button', { name: 'Start render' }).click();

      // The SERVER's row is the source of truth for "a result came back".
      await expect
        .poll(
          async () => {
            const response = await request.get(
              `${API_URL}${API_PREFIX}/projects/${projectId}/render-history?limit=5`,
              { headers: { Authorization: `Bearer ${token}` } },
            );
            if (!response.ok()) return 'http-' + String(response.status());
            const body = (await response.json()) as {
              items: { status: string; outputUrl: string | null; designVersionId: string | null }[];
            };
            const row = body.items[0];
            if (row === undefined) return 'no-row';
            if (row.status !== 'succeeded') return row.status;
            return row.outputUrl === null ? 'no-image' : 'ready';
          },
          {
            message:
              'the render never produced an image. Check the render worker is running and that ' +
              'the job envelope carried an output slot (mint_render_outputs).',
            timeout: RENDER_CEILING_MS,
            intervals: [250, 500, 1000],
          },
        )
        .toBe('ready');
      elapsedMs = Date.now() - started;

      // Recorded, not asserted — see the header's note on the §14 budget.
      test.info().annotations.push({
        type: 'render-e2e-ms',
        description:
          `${elapsedMs} ms from "Start render" to a succeeded row with an image. ` +
          'Includes WebGL capture + upload + queue; the §14 <1s provider budget is timed in ' +
          'apps/api/tests/test_render_jobs.py.',
      });
    });

    await test.step('the gallery shows it, pinned to the version it was made from', async () => {
      await tabLink(page, 'Renders').click();
      const image = page.locator('figure img').first();
      await expect(image, 'the finished render never appeared in the gallery').toBeVisible({
        timeout: 30_000,
      });
      // Pinned: the caption says so, and the row carries a designVersionId.
      await expect(page.getByText(/pinned to version/i).first()).toBeVisible();
      // Nothing is stale yet — a fresh render of the current design must not
      // warn, or the banner means nothing when it does appear.
      await expect(staleBanner(page)).toHaveCount(0);
    });

    await test.step('edit a wall — the render goes stale (§9)', async () => {
      await tabLink(page, 'Plan').click();
      await expect(page.getByRole('toolbar', { name: 'Drawing tools' })).toBeVisible({
        timeout: 20_000,
      });
      await focusCanvas(page);
      const box = await canvasBox(page);
      // One more wall is a real visual edit: the sequencer marks every render
      // pinned to the previous state stale as it appends.
      await drawWallChain(page, { x: box.x + box.width * 0.5, y: box.y + box.height * 0.45 }, [
        { dir: 'right', lengthMm: 2000 },
      ]);
      await waitForSaved(page);

      // The SERVER's flag first — the client only renders what it is told.
      await expect
        .poll(
          async () => {
            const response = await request.get(
              `${API_URL}${API_PREFIX}/projects/${projectId}/render-history?limit=5`,
              { headers: { Authorization: `Bearer ${token}` } },
            );
            if (!response.ok()) return false;
            const body = (await response.json()) as { items: { stale: boolean }[] };
            return body.items.every((row) => row.stale);
          },
          {
            message:
              'the op sequencer did not mark the existing render stale after a visual edit (§9)',
            timeout: 30_000,
          },
        )
        .toBe(true);

      // …then the banner the architect actually sees.
      await tabLink(page, 'Renders').click();
      await expect(
        staleBanner(page).first(),
        'the render went stale on the server but the gallery never said so',
      ).toBeVisible({ timeout: 30_000 });
    });

    await test.step('"New render" sends you where the scene is (the capture handoff)', async () => {
      // The Renders tab owns no canvas. Rather than render from a cached
      // photograph of an older design — the §9 lie — it writes a pending
      // request and routes to the 3D view, where the launcher picks it up.
      // Asserted as routing only: the auto-capture that follows is the same
      // code path the step above already ran for real.
      await page.getByRole('button', { name: 'New render' }).click();
      await page.waitForURL(/\/3d$/, { timeout: 15_000 });
    });

    expectNoConsoleErrors(consoleErrors);
  });

  test('the 8-shot client pack', () => {
    test.skip(
      true,
      [
        'Needs object storage reachable FROM THE BROWSER: a pack uploads 24 capture PNGs',
        'through presigned PUTs, which requires minio with CORS configured for the app',
        'origin. The inline-base64 fallback exists but 24 inline PNGs exceed the API body',
        'cap by design, so falling back is not a passing path — it is the honest failure.',
        'What is already asserted without a browser, in apps/api/tests/test_render_jobs.py:',
        '  · one job group per pack (shared packId, packIndex, packSlug)',
        '  · derived seeds (shot i renders with base+i)',
        '  · the §9 concurrency gate checked once per pack, not per member',
        '  · interior + Precise → 422, version pinning → 409, archive → signed download',
        'Turning this on is deleting this skip once CI has minio CORS.',
      ].join('\n'),
    );
  });
});
