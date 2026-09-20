/**
 * The hatch layer, in a real browser (CLAUDE.md bug 4).
 *
 * `PlanScene` draws a wall whose surface is bound to a material with the
 * pattern the SHEET will poché it with, and it does that by building the
 * hatched walls into a SECOND merged mesh. A second mesh is exactly the shape
 * of the furniture-layer bug: it can render perfectly and never reach the
 * `PickRegistry`, and no compile step says so. `planBuffers.test.ts` casts a
 * ray at the same buffers through the same resolver, which proves the maths;
 * this spec proves the WIRING — that the mesh the page actually mounts is
 * registered, so a click on a hatched wall still selects that wall.
 *
 * It also proves the hatch is drawn at all: the plan's GL buffer is asked how
 * many line vertices it holds before and after the binding, through the same
 * dev test handle the 3D spec uses. A renderer that drew nothing would pass
 * an op-log assertion and fail this one.
 *
 * Arranged through the API (plot, storey, walls, the material assignment) for
 * the reason `plan-canvas.spec.ts` gives: the subject is the canvas, not the
 * plot editor. The CLICK is a real mouse click at a computed pixel.
 */

import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { appendOps, createProject, projectModel, signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import { adoptApiSession, canvasBox, focusCanvas, hooksSnapshot, pickProbe } from '../support/ui';

const STOREY_ID = 'storey_01J3D00000000000000000000A';
const WALL_SOUTH = 'wall_01J3D0000000000000000000S1';
const WALL_SPINE = 'wall_01J3D0000000000000000000P1';
const MATERIAL_ID = 'material_01J3D0000000000000000000M1';

/** A 12 m × 12 m plot, as in plan-canvas.spec. */
const PLOT_MM = [
  { x: 0, y: 0 },
  { x: 12_000, y: 0 },
  { x: 12_000, y: 12_000 },
  { x: 0, y: 12_000 },
];

const SYNC_TIMEOUT_MS = 20_000;

test.describe('@canvas hatches on the 2D canvas', () => {
  test.setTimeout(180_000);

  test('a bound wall draws its pattern and stays clickable', async ({ page, request }) => {
    const session = await signUpFirm(request, {
      email: uniqueEmail('hatch'),
      firmName: 'Hatch Test Associates',
    });
    const token = session.accessToken;
    const project = await createProject(request, token, 'Hatched wall plan');

    // Two walls: one will be bound to a material (and so hatched), one will
    // not. Both must stay clickable, and only one may grow hatch lines.
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
          payload: { id: STOREY_ID, index: 0, name: 'Ground Floor', heightMm: 3000 },
        },
        {
          type: 'wall.add',
          payload: {
            id: WALL_SOUTH,
            storeyId: STOREY_ID,
            a: { x: 1000, y: 1000 },
            b: { x: 7000, y: 1000 },
            thicknessMm: 230,
            kind: 'external',
          },
        },
        {
          type: 'wall.add',
          payload: {
            id: WALL_SPINE,
            storeyId: STOREY_ID,
            a: { x: 1000, y: 4000 },
            b: { x: 7000, y: 4000 },
            thicknessMm: 230,
            kind: 'external',
          },
        },
      ],
      -1,
    );

    await adoptApiSession(page, request);
    await page.goto(`${APP_URL}/projects/${project.id}/plan`);
    await expect(page.getByRole('toolbar', { name: 'Drawing tools' })).toBeVisible({
      timeout: 20_000,
    });
    await focusCanvas(page);

    // Frame the plan so the coordinate maths below is workable, then learn the
    // camera rather than assuming it — same discipline as plan-canvas.spec.
    await page.keyboard.press('0');
    await page.waitForTimeout(600);

    const ctx = await calibrate(page, request, token, project.id);

    const linesBefore = await hatchLineVertices(page);

    // Bind the south wall's surface to a material. `external_wall` narrowed to
    // this element is exactly what the hatch panel writes.
    const model = await projectModel(request, token, project.id);
    await appendOps(
      request,
      token,
      project.id,
      [
        {
          type: 'material.assign',
          payload: {
            id: MATERIAL_ID,
            target: { group: 'external_wall', storeyId: null, elementId: WALL_SOUTH },
            materialId: 'brick-exposed-wirecut',
          },
        },
      ],
      model.headIdx,
    );

    // The page holds its own op log; reload so it folds the assignment in.
    await page.reload();
    await expect(page.getByRole('toolbar', { name: 'Drawing tools' })).toBeVisible({
      timeout: 20_000,
    });
    await focusCanvas(page);
    await page.keyboard.press('0');
    await page.waitForTimeout(600);

    await expect
      .poll(() => hatchLineVertices(page), {
        timeout: SYNC_TIMEOUT_MS,
        message:
          'the bound wall grew no hatch lines. Either `buildWallHatch` returned nothing for ' +
          'the assignment, or the LineLayer is not mounted — a renderer that draws nothing.',
      })
      .toBeGreaterThan(linesBefore);

    // ── THE REGISTRATION TEST ────────────────────────────────────────────
    // Ask the RAYCAST, not the selection. `selectTool.pick()` falls back to a
    // geometric search when the pick is empty, so a click alone cannot tell a
    // registered mesh from CLAUDE.md's bug 4 — measured here: this spec stayed
    // green with the hatched layer's `usePickableResolver` call removed, until
    // this assertion existed. The probe runs the product's one picker at the
    // same pixel the click below uses.
    const hatchedPx = toPixel(ctx, { x: 4000, y: 1000 });
    const hatchedHit = await pickProbe(page, hatchedPx.x, hatchedPx.y);
    expect(
      hatchedHit,
      'the RAY at the hatched wall resolved nothing. The hatched-wall mesh is a second ' +
        'merged mesh and it is not registered with the one PickRegistry (CLAUDE.md bug 4). ' +
        'A click would still select it — through the select tool\u2019s geometric fallback, ' +
        'which is why this assertion and not only the click below.',
    ).toEqual({ kind: 'wall', id: WALL_SOUTH });

    // The flat mesh must still answer for the wall that is NOT hatched, so one
    // mesh cannot be standing in for both.
    const flatPx = toPixel(ctx, { x: 4000, y: 4000 });
    expect(await pickProbe(page, flatPx.x, flatPx.y)).toEqual({ kind: 'wall', id: WALL_SPINE });

    // ── AND THE CLICK ────────────────────────────────────────────────────
    // The whole path: real mouse, real tool, real selection store.
    await clickModelPoint(ctx, page, { x: 4000, y: 1000 });
    await expect
      .poll(async () => (await hooksSnapshot(page)).selectedIds.join(','), {
        timeout: 10_000,
        message:
          'clicking the HATCHED wall selected nothing. The hatched-wall mesh is not registered ' +
          'with the one PickRegistry (CLAUDE.md bug 4).',
      })
      .toBe(WALL_SOUTH);

    // …and the unhatched wall still resolves to itself, so the split did not
    // simply register one mesh for both.
    await clickModelPoint(ctx, page, { x: 4000, y: 4000 });
    await expect
      .poll(async () => (await hooksSnapshot(page)).selectedIds.join(','), {
        timeout: 10_000,
        message: 'clicking the UNHATCHED wall did not select it',
      })
      .toBe(WALL_SPINE);
  });
});

// ---------------------------------------------------------------------------
// Camera calibration — measure, never assume
// ---------------------------------------------------------------------------

interface Ctx {
  mmPerPx: number;
  originMm: { x: number; y: number };
  originPx: { x: number; y: number };
}

/**
 * Derive `mmPerPx` and an origin by DRAWING a wall over a known pixel run and
 * reading its length back in millimetres from the server, then undoing it.
 *
 * The same method `plan-canvas.spec.ts` uses, and for its reason: a spec that
 * assumed a scale would go red the day somebody changed a fit-padding
 * constant. It is also why no new camera probe was added to the dev handle —
 * this measures the camera the page is really using, through the ops it really
 * produced.
 */
async function calibrate(
  page: Page,
  request: APIRequestContext,
  token: string,
  projectId: string,
): Promise<Ctx> {
  const RUN_PX = 240;
  const box = await canvasBox(page);
  // Low and left, clear of the two walls under test, so a snap to this wall's
  // ends cannot perturb them.
  const from = { x: box.x + box.width * 0.18, y: box.y + box.height * 0.86 };

  const before = await projectModel(request, token, projectId);
  const wallsBefore = before.model.house.walls.length;

  await page.keyboard.press('w');
  await page.mouse.move(from.x, from.y);
  await page.mouse.click(from.x, from.y);
  await page.mouse.move(from.x + RUN_PX, from.y, { steps: 6 });
  await page.mouse.click(from.x + RUN_PX, from.y);
  await page.keyboard.press('Enter');

  await expect
    .poll(async () => (await projectModel(request, token, projectId)).model.house.walls.length, {
      timeout: SYNC_TIMEOUT_MS,
      message: 'the calibration wall never reached the server — the wall tool is not committing',
    })
    .toBe(wallsBefore + 1);

  const after = await projectModel(request, token, projectId);
  const wall = after.model.house.walls[after.model.house.walls.length - 1];
  expect(wall, 'the calibration wall is missing from the folded model').toBeTruthy();
  const lengthMm = Math.hypot(wall!.b.x - wall!.a.x, wall!.b.y - wall!.a.y);
  expect(lengthMm, 'the calibration wall has no length').toBeGreaterThan(0);

  const ctx: Ctx = {
    mmPerPx: lengthMm / RUN_PX,
    originMm: wall!.a.x <= wall!.b.x ? wall!.a : wall!.b,
    originPx: from,
  };

  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+z' : 'Control+z');
  await expect
    .poll(async () => (await projectModel(request, token, projectId)).model.house.walls.length, {
      timeout: SYNC_TIMEOUT_MS,
      message: 'undo should have removed the calibration wall',
    })
    .toBe(wallsBefore);

  // Back to the select tool, so the clicks below select rather than draw.
  await page.keyboard.press('v');
  return ctx;
}

/** Model millimetres → screen pixels. Model +Y is up, screen +Y is down. */
function toPixel(ctx: Ctx, ptMm: { x: number; y: number }): { x: number; y: number } {
  return {
    x: ctx.originPx.x + (ptMm.x - ctx.originMm.x) / ctx.mmPerPx,
    y: ctx.originPx.y - (ptMm.y - ctx.originMm.y) / ctx.mmPerPx,
  };
}

async function clickModelPoint(
  ctx: Ctx,
  page: Page,
  ptMm: { x: number; y: number },
): Promise<void> {
  const target = toPixel(ctx, ptMm);
  await page.mouse.move(target.x, target.y, { steps: 2 });
  await page.mouse.click(target.x, target.y);
}

/**
 * Line vertices in the plan's hatch layer, off the dev handle. A count, not a
 * screenshot: it goes up only if geometry was genuinely built and mounted.
 */
async function hatchLineVertices(page: Page): Promise<number> {
  return (await hooksSnapshot(page)).hatchLineVertices;
}
