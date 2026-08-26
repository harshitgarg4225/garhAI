/** SCRATCH DEBUG SPEC — 3D empty-click pick probe. Delete before committing. */

import { expect, test } from '@playwright/test';

import { appendOps, createProject, signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import { adoptApiSession, canvasBox, focusCanvasKeyboard, hooksSnapshot } from '../support/ui';

const G = 'storey_01J3D00000000000000000000A';
const F = 'storey_01J3D00000000000000000000B';
const PLOT_MM = [
  { x: 0, y: 0 },
  { x: 12_000, y: 0 },
  { x: 12_000, y: 12_000 },
  { x: 0, y: 12_000 },
];

test('debug: 3d pick probe', async ({ page, request }) => {
  test.setTimeout(180_000);
  const session = await signUpFirm(request, { email: uniqueEmail('dbg5'), firmName: 'Debug Firm' });
  const token = session.accessToken;
  const project = await createProject(request, token, 'Debug 3d picks');
  const wall = (id: string, a: { x: number; y: number }, b: { x: number; y: number }): {
    type: string;
    payload: Record<string, unknown>;
  } => ({
    type: 'wall.add',
    payload: { id, storeyId: G, a, b, thicknessMm: 230, kind: 'external' },
  });
  await appendOps(
    request,
    token,
    project.id,
    [
      { type: 'plot.set_boundary', payload: { polygon: PLOT_MM, source: 'manual' } },
      { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000, name: '9m Road' } },
      { type: 'plot.set_reg_profile', payload: { cityPack: 'blr', overrides: {} } },
      { type: 'storey.add', payload: { id: G, index: 0, name: 'Ground Floor', heightMm: 3000 } },
      { type: 'storey.add', payload: { id: F, index: 1, name: 'First Floor', heightMm: 3000 } },
      // 6.9 m x 3.45 m rectangle at the plot centre-ish, like the spec draws.
      wall('wall_01J3D00000000000000000000B', { x: 2500, y: 4300 }, { x: 9400, y: 4300 }),
      wall('wall_01J3D00000000000000000000C', { x: 9400, y: 4300 }, { x: 9400, y: 7750 }),
      wall('wall_01J3D00000000000000000000D', { x: 9400, y: 7750 }, { x: 2500, y: 7750 }),
      wall('wall_01J3D00000000000000000000E', { x: 2500, y: 7750 }, { x: 2500, y: 4300 }),
    ],
    -1,
  );

  await adoptApiSession(page, request);
  await page.goto(`${APP_URL}/projects/${project.id}/3d`);
  await expect(page.locator('[data-garh-canvas="3d"]')).toBeVisible({ timeout: 20_000 });
  await focusCanvasKeyboard(page);
  await expect
    .poll(async () => (await hooksSnapshot(page)).rebuildCount, { timeout: 20_000 })
    .toBeGreaterThan(0);
  await page.waitForTimeout(1000);

  const box = await canvasBox(page);
  const coreState = await page.evaluate(() => {
    interface DebugCore {
      camera: { type: string } | null;
      viewport: { mode: string; mmPerPx: number; planeElevationMm: number };
      registry: { size: number };
      activeStoreyId: string | null;
      pick: (ndc: { x: number; y: number }) => {
        kind: string;
        id: string | null;
        pointMm: unknown;
      };
    }
    const core = (window as unknown as { __garhCore?: DebugCore }).__garhCore;
    if (core === undefined) return null;
    const picks: Record<string, unknown> = {};
    for (const [fx, fy] of [
      [0.5, 0.5],
      [0.93, 0.42],
      [0.5, 0.35],
    ] as const) {
      const ndc = { x: fx * 2 - 1, y: -(fy * 2 - 1) };
      const hit = core.pick(ndc);
      picks[`${fx},${fy}`] = { kind: hit.kind, id: hit.id, pointMm: hit.pointMm };
    }
    return {
      cameraType: core.camera?.type ?? null,
      mode: core.viewport.mode,
      mmPerPx: core.viewport.mmPerPx,
      registrySize: core.registry.size,
      activeStoreyId: core.activeStoreyId,
      picks,
    };
  });
  console.log('CORE =', JSON.stringify(coreState, null, 1));

  const points: Array<[number, number]> = [
    [0.5, 0.5],
    [0.93, 0.42],
    [0.5, 0.35],
  ];
  for (const [fx, fy] of points) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    await page.waitForTimeout(250);
    const snap = await hooksSnapshot(page);
    console.log(`click(${fx},${fy}) -> selected=${JSON.stringify(snap.selectedIds)}`);
  }
  await page.screenshot({
    path: '/tmp/claude-0/-home-user-garhAI/9f8f0507-a974-5e79-99e0-fd1696e76132/scratchpad/debug-3d.png',
  });
});
