/**
 * The Phase 4 Definition of Done.
 *
 *     "Playwright: draw a 2-room plan from scratch, all ops sync, undo/redo
 *      works, compliance chip appears when a bedroom < 9.5 m² and disappears
 *      on fix; 60fps pan/zoom on demo G+2 (measured)."
 *
 * The frame-rate half lives in `performance.spec.ts`, which owns tracing and
 * needs a solved G+2 demo (Phase 3). This file owns the editing half.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE TEST, NOT FOUR — AND WHY
 * ════════════════════════════════════════════════════════════════════════════
 * The undo stack lives in the browser tab. `useModelStore.hydrate` rebuilds it
 * empty on load, exactly as it should — undo history is not something a server
 * hands you. So a spec that reloads the page between "draw" and "undo" would be
 * pressing ⌘Z against an empty stack and asserting nothing.
 *
 * The whole DoD is therefore one journey with `test.step` boundaries, on one
 * page, in one session. The steps read as the DoD sentence.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HOW A WEBGL CANVAS IS DRIVEN HONESTLY
 * ════════════════════════════════════════════════════════════════════════════
 * A canvas has no accessible structure, so every assertion about the DRAWING is
 * made against the server: `GET /projects/:id/model` is the fold of the op log
 * the browser actually sent. That is a stronger claim than a screenshot — not
 * "a wall is on screen" but "the wall reached the op log, folded into a room,
 * and the compliance engine saw it".
 *
 * The one thing the spec must know is the camera's scale, and it refuses to
 * guess: `calibrate()` draws a single wall across a known number of PIXELS,
 * reads its length in MILLIMETRES back from the API, and derives `mmPerPx` plus
 * the origin. Every later click is planned in millimetres and executed in
 * pixels through those two numbers. A viewport change, a different device pixel
 * ratio or a new fit-on-open padding constant therefore cannot make this spec
 * lie — it re-derives the mapping on every run.
 *
 * The calibration wall is then undone, which is also the first undo assertion.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * SYNCHRONISATION
 * ════════════════════════════════════════════════════════════════════════════
 * Never "wait for the badge to say Saved" — it may already say that from the
 * previous edit. Every wait is `expect.poll` against the server until the state
 * the step is about is true. That makes "all ops sync" the waiting condition
 * rather than a hopeful assertion afterwards.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS ARRANGED THROUGH THE API, AND WHY
 * ════════════════════════════════════════════════════════════════════════════
 * The plot boundary, one road and the city pack — the compliance engine's
 * preconditions. Drawing a plot is Phase 2's spec; a canvas spec that spends
 * twenty interactions on a rectangle it is not testing is a canvas spec that
 * goes red for Phase 2's reasons. Walls are NEVER arranged this way: every wall
 * in this file was drawn with the mouse and the keyboard.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS SPEC CANNOT ASSERT, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 *  · **Pixels.** Nothing here claims the plan LOOKS right. This spec would pass
 *    against a renderer that drew nothing, as long as the ops and the fold were
 *    correct. Visual regression on the canvas arrives with Phase 5's screenshot
 *    suite; a pixel assertion here would be flaky across three GPUs.
 *  · **Frame rate.** `performance.spec.ts` owns it.
 *  · **The chip's exact wording.** It comes from the rule pack, so the
 *    assertion is on the rule id and on "9.5" appearing, not on a sentence a
 *    reviewing architect may legitimately rewrite.
 *  · **Ortho and snapping directly.** Asserted only in consequence: the
 *    typed-length helper produces correct geometry only if both work.
 *  · **The furniture tool, the balcony tool, stairs and openings.** Covered by
 *    their vitest state-machine specs; adding them here would trade a long
 *    runtime for coverage that is already better tested at the unit level.
 *
 * The compliance step declares its precondition and SKIPS WITH A REASON when
 * the stack cannot meet it. A skip that names the missing thing is honest; a
 * pass that asserted nothing would not be.
 */

import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import {
  appendOps,
  complianceReport,
  createProject,
  opsSince,
  projectModel,
  signUpFirm,
  type FoldedModel,
} from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import {
  canvasBox,
  complianceStrip,
  drawWallChain,
  focusCanvas,
  inspector,
  adoptApiSession,
  type CanvasBox,
} from '../support/ui';

/* ── the plan this spec draws, in millimetres ─────────────────────────────── */

/** Outer rectangle, wall CENTRELINES. 6.9 m × 3.45 m — a real small dwelling. */
const OUTER_W_MM = 6900;
const OUTER_H_MM = 3450;
/** Divider offset from the left wall's centreline. */
const DIVIDER_X_MM = 2300;
/** How far right the divider is dragged to fix the undersized bedroom. */
const ENLARGE_BY_MM = 2000;

/**
 * With the default 230 mm wall the left room's CLEAR area is
 * (2300 − 230) × (3450 − 230) = 2.07 × 3.22 = 6.67 m², comfortably under the
 * NBC habitable minimum. After the drag it is (4300 − 230) × 3.22 = 13.1 m²,
 * comfortably over. Both margins are far wider than a 115 mm snap either way,
 * so the answer cannot flip on a rounding decision.
 */
const NBC_HABITABLE_MIN_MM2 = 9_500_000;

/** A 12 m × 12 m plot: room for the plan with setbacks to spare. */
const PLOT_MM = [
  { x: 0, y: 0 },
  { x: 12_000, y: 0 },
  { x: 12_000, y: 12_000 },
  { x: 0, y: 12_000 },
];

/** Server round trip plus the ≤500 ms compliance debounce, with headroom. */
const SYNC_TIMEOUT_MS = 20_000;

test.describe('@canvas Phase 4 DoD — the 2D editor', () => {
  // Six steps, each with a server round trip and a debounce. The default 60 s
  // is not enough and a timeout here should mean "something is stuck", not
  // "the machine is slow".
  test.setTimeout(240_000);

  test('draw a two-room plan, undo/redo it, and watch a bye-law chip come and go', async ({
    page,
    request,
  }) => {
    const email = uniqueEmail('canvas');
    const session = await signUpFirm(request, { email, firmName: 'Canvas Test Associates' });
    const token = session.accessToken;

    const ctx: Ctx = {
      page,
      request,
      token,
      projectId: '',
      mmPerPx: 0,
      originMm: { x: 0, y: 0 },
      originPx: { x: 0, y: 0 },
      box: { x: 0, y: 0, width: 0, height: 0 },
    };

    await test.step('open an empty project on the Plan tab', async () => {
      const project = await createProject(request, token, 'Two-room canvas plan');
      ctx.projectId = project.id;
      // The compliance engine's two preconditions. `baseIdx: -1` is "the log is
      // empty", which is true of a project created a moment ago.
      await appendOps(
        request,
        token,
        ctx.projectId,
        [
          { type: 'plot.set_boundary', payload: { polygon: PLOT_MM, source: 'manual' } },
          { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000, name: '9m Road' } },
          { type: 'plot.set_reg_profile', payload: { cityPack: 'blr', overrides: {} } },
          // The ground storey. A fresh document has NO storeys, nothing in the
          // UI yet creates one (the §12 keymap only switches between existing
          // storeys), and the wall tool honestly declines to draw with no
          // active storey (`wallTool.ts`: `ctx.storeyId === null` → none). So
          // a storey is a precondition of drawing, arranged like the plot —
          // caught during the Phase-5 integration, when the 3D spec hit the
          // same wall (literally).
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

      await adoptApiSession(page, request);
      await page.goto(`${APP_URL}/projects/${ctx.projectId}/plan`);
      await expect(
        page.getByRole('toolbar', { name: 'Drawing tools' }),
        'the Plan tab should show the §12 tool rail',
      ).toBeVisible({ timeout: 20_000 });
      await focusCanvas(page);
      ctx.box = await canvasBox(page);
    });

    await test.step('learn the camera scale, then undo the wall that taught it', async () => {
      await calibrate(ctx);
    });

    await test.step('draw the outer rectangle with typed lengths', async () => {
      const start = {
        x: ctx.box.x + ctx.box.width / 2 - toPx(ctx, OUTER_W_MM) / 2,
        y: ctx.box.y + ctx.box.height / 2 + toPx(ctx, OUTER_H_MM) / 2,
      };
      await drawWallChain(
        ctx.page,
        start,
        [
          { dir: 'right', lengthMm: OUTER_W_MM },
          { dir: 'up', lengthMm: OUTER_H_MM },
          { dir: 'left', lengthMm: OUTER_W_MM },
          { dir: 'down', lengthMm: OUTER_H_MM },
        ],
        { mmPerPx: ctx.mmPerPx },
      );

      await expectWalls(ctx, 4, 'the four walls of the chain should have reached the server');
      await expectRooms(
        ctx,
        1,
        'a closed rectangle is one room — a count of 0 means the chain never closed, ' +
          'which usually means the ortho constraint or the typed length did not apply',
      );
    });

    await test.step('split it in two, and confirm every op is in the server log', async () => {
      const start = {
        x: ctx.box.x + ctx.box.width / 2 - toPx(ctx, OUTER_W_MM) / 2 + toPx(ctx, DIVIDER_X_MM),
        y: ctx.box.y + ctx.box.height / 2 + toPx(ctx, OUTER_H_MM) / 2,
      };
      await drawWallChain(ctx.page, start, [{ dir: 'up', lengthMm: OUTER_H_MM }], {
        mmPerPx: ctx.mmPerPx,
      });

      await expectWalls(ctx, 5, 'the divider should have reached the server');
      await expectRooms(ctx, 2, 'the divider should have split the rectangle into two rooms');

      const folded = await projectModel(request, token, ctx.projectId);
      const areas = folded.model.house.rooms.map((r) => r.areaMm2).sort((a, b) => a - b);
      expect(areas[0], 'the left room should be under the NBC habitable minimum').toBeLessThan(
        NBC_HABITABLE_MIN_MM2,
      );
      expect(areas[1], 'the right room should be over it').toBeGreaterThan(NBC_HABITABLE_MIN_MM2);

      // "All ops sync" — the server's log, not the browser's optimistic copy.
      // SIX, not five: the log is append-only, so the calibration wall's
      // `wall.add` is still in it — its undo appended an inverse rather than
      // erasing history. The five walls standing are the fold's answer
      // (asserted above); the log's answer is every wall ever drawn.
      const log = await opsSince(request, token, ctx.projectId, -1);
      const wallAdds = log.ops.filter((op) => op.type === 'wall.add');
      expect(wallAdds.length, 'every drawn wall should be a `wall.add` in the op log').toBe(6);

      // §12: a chain is ONE undo group, so one ⌘Z removes the whole room.
      // Skip index 0 — that is the calibration wall, its own group.
      const chainGroups = new Set(wallAdds.slice(1, 5).map((op) => op.groupId ?? ''));
      expect(chainGroups.size, 'the four walls of one chain should share a group id').toBe(1);
    });

    await test.step('undo removes the divider, redo puts it back', async () => {
      await page.keyboard.press(`${modifier()}+z`);
      await expectWalls(ctx, 4, 'undo should have removed the divider');
      await expectRooms(ctx, 1, 'with the divider gone the two rooms merge back into one');

      await page.keyboard.press(`${modifier()}+Shift+z`);
      await expectWalls(ctx, 5, 'redo should have put the divider back');
      await expectRooms(ctx, 2, 'and the two rooms with it');
    });

    await test.step('an undersized bedroom raises a chip', async () => {
      const report = await complianceReport(request, token, ctx.projectId);
      test.skip(
        !report.evaluated,
        'The compliance engine answered `evaluated: false` for this project — usually a ' +
          'rule pack that is not being served (run `make seed`, or check `GET /rulepacks`). ' +
          'Skipping is honest: a chip cannot appear if nothing was checked.',
      );

      const folded = await projectModel(request, token, ctx.projectId);
      const small = [...folded.model.house.rooms].sort((a, b) => a.areaMm2 - b.areaMm2)[0];
      expect(small, 'the two-room plan should still be here').toBeTruthy();

      await page.keyboard.press('v');
      await clickModelPoint(ctx, roomProbe(folded, small!.id));

      const typeField = inspector(page).getByLabel('Type');
      await expect(
        typeField,
        'clicking inside a room should put that room in the inspector',
      ).toBeVisible({ timeout: 10_000 });
      await typeField.selectOption({ label: 'Bedroom' });

      await expect
        .poll(
          async () => {
            const r = await complianceReport(request, token, ctx.projectId);
            return r.results.some(
              (x) => x.ruleId.includes('area') && x.ruleId.includes('room') && x.status === 'fail',
            );
          },
          {
            timeout: SYNC_TIMEOUT_MS,
            message: 'expected a failing room-area rule once the small room was typed as a bedroom',
          },
        )
        .toBe(true);

      await expect(
        complianceStrip(page).getByText(/9\.5/).first(),
        'the strip should show the 9.5 m² minimum in plain words (≤500 ms debounce, §14)',
      ).toBeVisible({ timeout: SYNC_TIMEOUT_MS });
    });

    await test.step('enlarging the bedroom clears the chip', async () => {
      const folded = await projectModel(request, token, ctx.projectId);
      const divider = dividerWall(folded);
      expect(divider, 'the divider wall should be findable in the folded model').toBeTruthy();

      const midMm = {
        x: (divider!.a.x + divider!.b.x) / 2,
        y: (divider!.a.y + divider!.b.y) / 2,
      };
      await dragModel(ctx, midMm, { x: midMm.x + ENLARGE_BY_MM, y: midMm.y });

      // Assert the FIX landed before asserting the chip cleared. Without this,
      // a drag that silently did nothing and a chip that never rendered would
      // look identical — and the test would pass for the wrong reason.
      await expect
        .poll(
          async () => {
            const now = await projectModel(request, token, ctx.projectId);
            const bedroom = now.model.house.rooms.find((r) => r.type === 'bedroom');
            return bedroom?.areaMm2 ?? 0;
          },
          {
            timeout: SYNC_TIMEOUT_MS,
            message:
              'the drag should have taken the bedroom over 9.5 m². If this fails the select ' +
              'tool moved the wrong wall, or not far enough, and the chip assertion below ' +
              'would have been meaningless.',
          },
        )
        .toBeGreaterThan(NBC_HABITABLE_MIN_MM2);

      await expect(
        complianceStrip(page).getByText(/9\.5/),
        'the chip should clear once the room is big enough',
      ).toHaveCount(0, { timeout: SYNC_TIMEOUT_MS });
    });
  });
});

// ---------------------------------------------------------------------------
// The spec's own little coordinate system
// ---------------------------------------------------------------------------

interface Ctx {
  page: Page;
  request: APIRequestContext;
  token: string;
  projectId: string;
  /** Millimetres per CSS pixel, measured — never assumed. */
  mmPerPx: number;
  /** A model point whose screen position is known. */
  originMm: { x: number; y: number };
  originPx: { x: number; y: number };
  box: CanvasBox;
}

function modifier(): string {
  return process.platform === 'darwin' ? 'Meta' : 'Control';
}

function toPx(ctx: Ctx, mm: number): number {
  return mm / ctx.mmPerPx;
}

/**
 * Model millimetres → screen pixels.
 *
 * Two numbers are the whole transform: the plan camera has no rotation, model
 * +Y is up and screen +Y is down, hence the sign on the Y term.
 */
function toPixel(ctx: Ctx, ptMm: { x: number; y: number }): { x: number; y: number } {
  return {
    x: ctx.originPx.x + (ptMm.x - ctx.originMm.x) / ctx.mmPerPx,
    y: ctx.originPx.y - (ptMm.y - ctx.originMm.y) / ctx.mmPerPx,
  };
}

/**
 * Derive `mmPerPx` and the origin from a wall drawn over a known pixel run,
 * then undo it so it is not part of the plan under test.
 */
async function calibrate(ctx: Ctx): Promise<void> {
  const RUN_PX = 240;
  // Low and left, well away from where the plan will be drawn, so a snap to
  // this wall's endpoints cannot perturb the rectangle.
  const from = { x: ctx.box.x + ctx.box.width * 0.18, y: ctx.box.y + ctx.box.height * 0.86 };

  // §12 `view.fit` FIRST, deliberately. The fit-on-open pass races model
  // hydration: executed runs saw the same arrangement open at 1:75 on one run
  // and 1:2000 on the next. Calibration would faithfully measure either — but
  // at 1:2000 the whole 6.9 m rectangle is 17 px, every click lands inside
  // MIN_WALL_LENGTH of the last, and the tool (correctly) reads them as
  // "finish". Fitting to the 12 m plot pins the scale to a workable band and
  // makes the measurement below deterministic.
  await ctx.page.keyboard.press('0');
  await ctx.page.waitForTimeout(400);

  const before = await projectModel(ctx.request, ctx.token, ctx.projectId);
  const wallsBefore = before.model.house.walls.length;

  await ctx.page.keyboard.press('w');
  await ctx.page.mouse.move(from.x, from.y);
  await ctx.page.mouse.click(from.x, from.y);
  await ctx.page.mouse.move(from.x + RUN_PX, from.y, { steps: 6 });
  await ctx.page.mouse.click(from.x + RUN_PX, from.y);
  await ctx.page.keyboard.press('Enter');

  await expect
    .poll(
      async () =>
        (await projectModel(ctx.request, ctx.token, ctx.projectId)).model.house.walls.length,
      {
        timeout: SYNC_TIMEOUT_MS,
        message:
          'the calibration wall never reached the server. The wall tool is not committing, ' +
          'and nothing after this point could be trusted.',
      },
    )
    .toBe(wallsBefore + 1);

  const after = await projectModel(ctx.request, ctx.token, ctx.projectId);
  const wall = after.model.house.walls[after.model.house.walls.length - 1]!;
  const lengthMm = Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y);
  expect(lengthMm, 'the calibration wall has no length').toBeGreaterThan(0);

  ctx.mmPerPx = lengthMm / RUN_PX;
  // The click at `from` produced `wall.a` (the run went right, so `a` is the
  // left end). Snapping may have moved it by up to half a module; that is
  // ±57 mm on a probe aimed at a room centroid metres away.
  ctx.originMm = wall.a.x <= wall.b.x ? wall.a : wall.b;
  ctx.originPx = from;

  await ctx.page.keyboard.press(`${modifier()}+z`);
  await expect
    .poll(
      async () =>
        (await projectModel(ctx.request, ctx.token, ctx.projectId)).model.house.walls.length,
      { timeout: SYNC_TIMEOUT_MS, message: 'undo should have removed the calibration wall' },
    )
    .toBe(wallsBefore);
}

async function clickModelPoint(ctx: Ctx, ptMm: { x: number; y: number }): Promise<void> {
  const target = toPixel(ctx, ptMm);
  await ctx.page.mouse.move(target.x, target.y, { steps: 2 });
  await ctx.page.mouse.click(target.x, target.y);
}

async function dragModel(
  ctx: Ctx,
  fromMm: { x: number; y: number },
  toMm: { x: number; y: number },
): Promise<void> {
  const a = toPixel(ctx, fromMm);
  const b = toPixel(ctx, toMm);
  await ctx.page.mouse.move(a.x, a.y, { steps: 2 });
  await ctx.page.mouse.down();
  // Several steps: the select tool must cross its drag threshold before it
  // switches from "click to select" to "drag to move".
  await ctx.page.mouse.move(b.x, b.y, { steps: 12 });
  await ctx.page.mouse.up();
}

// ---------------------------------------------------------------------------
// Waiting on the server, never on a badge
// ---------------------------------------------------------------------------

async function expectWalls(ctx: Ctx, count: number, message: string): Promise<void> {
  await expect
    .poll(
      async () =>
        (await projectModel(ctx.request, ctx.token, ctx.projectId)).model.house.walls.length,
      { timeout: SYNC_TIMEOUT_MS, message },
    )
    .toBe(count);
}

async function expectRooms(ctx: Ctx, count: number, message: string): Promise<void> {
  await expect
    .poll(
      async () =>
        (await projectModel(ctx.request, ctx.token, ctx.projectId)).model.house.rooms.length,
      { timeout: SYNC_TIMEOUT_MS, message },
    )
    .toBe(count);
}

// ---------------------------------------------------------------------------
// Reading the folded model
// ---------------------------------------------------------------------------

/** A point safely inside a room: the centroid of the server's own polygon. */
function roomProbe(folded: FoldedModel, roomId: string): { x: number; y: number } {
  const room = folded.model.house.rooms.find((r) => r.id === roomId);
  if (room === undefined) throw new Error(`room ${roomId} is not in the folded model`);
  const polygon = room.polygon ?? [];
  if (polygon.length === 0) {
    throw new Error(
      `room ${roomId} came back without a polygon — GET /model no longer returns room geometry, ` +
        'so this spec can no longer aim a click into a room',
    );
  }
  let x = 0;
  let y = 0;
  for (const p of polygon) {
    x += p.x;
    y += p.y;
  }
  return { x: Math.round(x / polygon.length), y: Math.round(y / polygon.length) };
}

/** The one vertical wall that is on neither outer edge: the divider. */
function dividerWall(
  folded: FoldedModel,
): { a: { x: number; y: number }; b: { x: number; y: number } } | undefined {
  const walls = folded.model.house.walls;
  const xs = walls.flatMap((w) => [w.a.x, w.b.x]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  return walls.find(
    (w) => w.a.x === w.b.x && Math.abs(w.a.x - minX) > 1 && Math.abs(w.a.x - maxX) > 1,
  );
}
