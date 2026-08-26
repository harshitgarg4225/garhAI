/**
 * SCRATCH DEBUG SPEC — not part of the suite. Delete before committing.
 *
 * Replays the plan-canvas spec's drawing flow while recording every flip of
 * the PlanPage container's computed `display` — to pin down exactly when the
 * route-level Suspense hides the page (React sets `display:none !important`
 * on the boundary's content while a child suspends).
 */

import { expect, test } from '@playwright/test';

import { appendOps, createProject, projectModel, signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import { adoptApiSession, canvasBox, drawWallChain, focusCanvas } from '../support/ui';

const PLOT_MM = [
  { x: 0, y: 0 },
  { x: 12_000, y: 0 },
  { x: 12_000, y: 12_000 },
  { x: 0, y: 12_000 },
];

test('debug: display flips while drawing the two-room plan', async ({ page, request }) => {
  test.setTimeout(240_000);
  const session = await signUpFirm(request, { email: uniqueEmail('dbg2'), firmName: 'Debug Firm' });
  const token = session.accessToken;
  const project = await createProject(request, token, 'Debug display flips');
  await appendOps(
    request,
    token,
    project.id,
    [
      { type: 'plot.set_boundary', payload: { polygon: PLOT_MM, source: 'manual' } },
      { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000, name: '9m Road' } },
      { type: 'plot.set_reg_profile', payload: { cityPack: 'blr', overrides: {} } },
      {
        type: 'storey.add',
        payload: {
          id: 'storey_01J3D00000000000000000000A',
          index: 0,
          name: 'Ground Floor',
          heightMm: 3000,
        },
      },
    ],
    -1,
  );

  // Recorder: sample the container's computed display every 50 ms.
  await page.addInitScript(() => {
    const flips: { t: number; display: string }[] = [];
    (window as unknown as { __flips: unknown[] }).__flips = flips;
    setInterval(() => {
      const el = document.querySelector('[data-garh-canvas]');
      const display = el === null ? '<absent>' : getComputedStyle(el).display;
      const last = flips[flips.length - 1];
      if (last === undefined || last.display !== display) {
        flips.push({ t: Math.round(performance.now()), display });
      }
    }, 50);
  });

  await adoptApiSession(page, request);
  await page.goto(`${APP_URL}/projects/${project.id}/plan`);
  await expect(page.getByRole('toolbar', { name: 'Drawing tools' })).toBeVisible({
    timeout: 20_000,
  });
  await focusCanvas(page);
  const box = await canvasBox(page);

  const mark = async (label: string): Promise<void> => {
    const flips = await page.evaluate(
      () => (window as unknown as { __flips: unknown[] }).__flips,
    );
    const walls = (await projectModel(request, token, project.id)).model.house.walls.length;
    console.log(`@${label}: walls=${walls} flips=${JSON.stringify(flips)}`);
  };

  await mark('open');

  // ── calibrate (same as the spec) ─────────────────────────────────────────
  await page.keyboard.press('0');
  await page.waitForTimeout(400);
  const RUN_PX = 240;
  const from = { x: box.x + box.width * 0.18, y: box.y + box.height * 0.86 };
  await page.keyboard.press('w');
  await page.mouse.move(from.x, from.y);
  await page.mouse.click(from.x, from.y);
  await page.mouse.move(from.x + RUN_PX, from.y, { steps: 6 });
  await page.mouse.click(from.x + RUN_PX, from.y);
  await page.keyboard.press('Enter');

  await expect
    .poll(async () => (await projectModel(request, token, project.id)).model.house.walls.length, {
      timeout: 20_000,
      message: 'calibration wall never reached the server',
    })
    .toBe(1);
  await mark('calibration-wall-committed');
  await page.waitForTimeout(1500);
  await mark('calibration+1.5s');

  const after = await projectModel(request, token, project.id);
  const wall = after.model.house.walls[0]!;
  const lengthMm = Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y);
  const mmPerPx = lengthMm / RUN_PX;

  await page.keyboard.press('Control+z');
  await expect
    .poll(async () => (await projectModel(request, token, project.id)).model.house.walls.length, {
      timeout: 20_000,
      message: 'undo of the calibration wall never reached the server',
    })
    .toBe(0);
  await mark('calibration-undone');

  // ── outer rectangle ──────────────────────────────────────────────────────
  const OUTER_W_MM = 6900;
  const OUTER_H_MM = 3450;
  const start = {
    x: box.x + box.width / 2 - OUTER_W_MM / mmPerPx / 2,
    y: box.y + box.height / 2 + OUTER_H_MM / mmPerPx / 2,
  };
  await drawWallChain(
    page,
    start,
    [
      { dir: 'right', lengthMm: OUTER_W_MM },
      { dir: 'up', lengthMm: OUTER_H_MM },
      { dir: 'left', lengthMm: OUTER_W_MM },
      { dir: 'down', lengthMm: OUTER_H_MM },
    ],
    { mmPerPx },
  );
  await expect
    .poll(async () => (await projectModel(request, token, project.id)).model.house.walls.length, {
      timeout: 20_000,
      message: 'rectangle never reached the server',
    })
    .toBe(4);
  await mark('rectangle-committed');
  await page.waitForTimeout(2000);
  await mark('rectangle+2s');

  // ── divider ──────────────────────────────────────────────────────────────
  const DIVIDER_X_MM = 2300;
  const dividerStart = {
    x: box.x + box.width / 2 - OUTER_W_MM / mmPerPx / 2 + DIVIDER_X_MM / mmPerPx,
    y: box.y + box.height / 2 + OUTER_H_MM / mmPerPx / 2,
  };
  await drawWallChain(page, dividerStart, [{ dir: 'up', lengthMm: OUTER_H_MM }], { mmPerPx });
  try {
    await expect
      .poll(
        async () => (await projectModel(request, token, project.id)).model.house.walls.length,
        { timeout: 20_000, message: 'divider never reached the server' },
      )
      .toBe(5);
  } catch (e) {
    console.log('DIVIDER FAILED:', String(e).split('\n')[0]);
  }
  await mark('divider');
  await page.screenshot({
    path: '/tmp/claude-0/-home-user-garhAI/9f8f0507-a974-5e79-99e0-fd1696e76132/scratchpad/debug-plan-2.png',
  });
});
