/**
 * The Phase 5 Definition of Done.
 *
 *     "plan edit reflects in 3D <100ms; facade kit applies/edits/exports
 *      consistently; screenshot-based visual regression on demo project."
 *
 * The screenshot third lives in `visual-regression.spec.ts` (skipped with its
 * reasons named until a CI baseline exists). This file owns the behaviour:
 * one journey — draw in 2D, Tab into 3D, select across the views, dress the
 * building in a kit, edit one component, scrub the sun, and watch the §14
 * budget — in one session, for the same reason `plan-canvas.spec.ts` is one
 * journey (the undo stack and the 3D group cache live in the tab).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HOW EACH CLAIM IS ASSERTED, HONESTLY
 * ════════════════════════════════════════════════════════════════════════════
 *  · **Ops** — against the SERVER's log (`opsSince`) and fold (`projectModel`),
 *    exactly like the Phase-4 spec. `facade.apply_kit` / `facade.edit_component`
 *    must be IN the log, and the walls must be byte-identical around both (§8).
 *  · **"The wall mesh updated"** — through the 3D status chip's `data-garh-*`
 *    attributes, written by `ThreeDScene.onRebuildStats` → `stores/three.ts`:
 *    the rebuild COUNTER moves exactly when some group re-meshed, and
 *    `data-garh-rebuild-ms` is the §14 wall-clock, asserted < 100 ms on the
 *    incremental path (scene stays mounted, one storey dirty).
 *  · **Selection sync** — the inspector panel (DOM) plus the dev-build hook's
 *    read-only snapshot; the 3D→selection direction is proven with a REAL
 *    canvas click resolved by the one shared picker.
 *  · **Sun scrub** — three freezes at once: the server op count, the client
 *    head/pending, and the rebuild counter — while the slider provably moved.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS ARRANGED THROUGH THE API, AND WHY
 * ════════════════════════════════════════════════════════════════════════════
 * The plot + city pack (Phase 2's editor owns plot drawing), the two storeys
 * (no UI creates a storey yet — the §12 keymap switches between existing
 * ones), and one door + one window (aiming the opening tool needs the wall's
 * PIXEL position, which typed-length drawing deliberately does not reveal;
 * `plan-canvas.spec.ts` covers the opening tool's real pointer path). The
 * openings are appended before a reload so the hydrate picks them up — this
 * client has no live pull for ops it did not send. Walls, the kit, the
 * component edit, the scrub and every toggle are real interactions.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS SPEC CANNOT ASSERT, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 *  · **Pixels.** No claim the extrusion LOOKS right — walls could render
 *    magenta and this passes. That is `visual-regression.spec.ts`'s job.
 *  · **Opening holes.** Whether Manifold's WASM loads in the CI browser is an
 *    environment fact; the chip reports it (`data-garh-holes`) and the spec
 *    records it in an annotation instead of failing on either answer — the
 *    no-WASM fallback is a designed, honest state, not a defect.
 *  · **Shadow/sun direction.** Pinned by the sun module's 36-row NOAA table
 *    in vitest; a browser screenshot cannot re-derive it.
 *  · **Storey-visibility pixels.** Asserted through the store probe; whether
 *    the GPU stopped drawing the hidden storey is a pixel claim this spec
 *    refuses to fake. (Hidden ⇒ unpickable is pinned in the canvas core.)
 *  · **Walk mode.** No CI runner exercises pointer-lock-style navigation
 *    honestly; `orbitOps.test.ts` owns the maths.
 */

import { expect, test } from '@playwright/test';

import { appendOps, createProject, opsSince, projectModel, signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import {
  canvasBox,
  clickEmpty3d,
  drawWallChain,
  focusCanvasKeyboard,
  hooksSnapshot,
  inspector,
  selectViaHooks,
  adoptApiSession,
  statusChip3d,
  toggleViewWithTab,
} from '../support/ui';

/* ── the building, in millimetres ─────────────────────────────────────────── */

const OUTER_W_MM = 6900;
const OUTER_H_MM = 3450;

/** 12 m × 12 m plot — room for the plan with setbacks to spare. */
const PLOT_MM = [
  { x: 0, y: 0 },
  { x: 12_000, y: 0 },
  { x: 12_000, y: 12_000 },
  { x: 0, y: 12_000 },
];

/** Caller-minted ids (`{type}_{26-char ULID}`, first char 0–7, Crockford). */
const STOREY_G = 'storey_01J3D00000000000000000000G';
const STOREY_1 = 'storey_01J3D00000000000000000000F';
const DOOR_ID = 'opening_01J3D00000000000000000000D';
const WINDOW_ID = 'opening_01J3D00000000000000000000W';

/** Server round trip with headroom. */
const SYNC_TIMEOUT_MS = 20_000;

/** §14: 3D rebuild after an edit, dirty storeys only. */
const REBUILD_BUDGET_MS = 100;

function wallLengthMm(wall: { a: { x: number; y: number }; b: { x: number; y: number } }): number {
  return Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y);
}

test.describe('@canvas Phase 5 DoD — instant 3D + facade kits', () => {
  test.setTimeout(240_000);

  test('extrude the plan, select across views, dress it in a kit, scrub the sun', async ({
    page,
    request,
  }) => {
    const email = uniqueEmail('three-d');
    const session = await signUpFirm(request, { email, firmName: '3D Test Associates' });
    const token = session.accessToken;
    let projectId = '';

    await test.step('arrange plot + two storeys, then open the Plan tab', async () => {
      const project = await createProject(request, token, 'Instant 3D');
      projectId = project.id;
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
            payload: { id: STOREY_G, index: 0, name: 'Ground Floor', heightMm: 3000 },
          },
          {
            type: 'storey.add',
            payload: { id: STOREY_1, index: 1, name: 'First Floor', heightMm: 3000 },
          },
        ],
        -1,
      );

      await adoptApiSession(page, request);
      await page.goto(`${APP_URL}/projects/${projectId}/plan`);
      await expect(page.locator('[data-garh-canvas="plan"]')).toBeVisible({ timeout: 20_000 });
    });

    await test.step('draw the plan in 2D — every wall through the real tools', async () => {
      const box = await canvasBox(page);
      await focusCanvasKeyboard(page);

      // Minimal calibration, the plan-canvas trick: fit, draw ONE wall across a
      // known pixel run, read its mm length off the server, undo. Without the
      // derived scale a multi-leg chain's pointer falls metres behind its typed
      // anchor and the tool (correctly) refuses the whole chain — this spec's
      // first execution committed zero walls exactly that way.
      await page.keyboard.press('0');
      await page.waitForTimeout(400);
      const RUN_PX = 240;
      const calibFrom = { x: box.x + box.width * 0.15, y: box.y + box.height * 0.85 };
      await page.keyboard.press('w');
      await page.mouse.move(calibFrom.x, calibFrom.y);
      await page.mouse.click(calibFrom.x, calibFrom.y);
      await page.mouse.move(calibFrom.x + RUN_PX, calibFrom.y, { steps: 6 });
      await page.mouse.click(calibFrom.x + RUN_PX, calibFrom.y);
      await page.keyboard.press('Enter');
      await expect
        .poll(
          async () => (await projectModel(request, token, projectId)).model.house.walls.length,
          { timeout: SYNC_TIMEOUT_MS, message: 'the calibration wall never reached the server' },
        )
        .toBe(1);
      const calib = await projectModel(request, token, projectId);
      const calibWall = calib.model.house.walls[0]!;
      const mmPerPx =
        Math.hypot(calibWall.b.x - calibWall.a.x, calibWall.b.y - calibWall.a.y) / RUN_PX;
      await page.keyboard.press(`${process.platform === 'darwin' ? 'Meta' : 'Control'}+z`);
      await expect
        .poll(
          async () => (await projectModel(request, token, projectId)).model.house.walls.length,
          { timeout: SYNC_TIMEOUT_MS, message: 'undo should have removed the calibration wall' },
        )
        .toBe(0);

      const start = { x: box.x + box.width * 0.35, y: box.y + box.height * 0.65 };
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
      await page.keyboard.press('v'); // select tool — later corner clicks must not draw

      await expect
        .poll(
          async () => (await projectModel(request, token, projectId)).model.house.walls.length,
          {
            timeout: SYNC_TIMEOUT_MS,
            message: 'the four drawn walls never reached the server',
          },
        )
        .toBe(4);
    });

    await test.step('arrange one door + one window on the drawn walls (header: why)', async () => {
      const log = await opsSince(request, token, projectId, -1);
      const folded = await projectModel(request, token, projectId);
      const [w0, w1] = folded.model.house.walls;
      await appendOps(
        request,
        token,
        projectId,
        [
          {
            type: 'opening.add',
            payload: {
              id: DOOR_ID,
              wallId: w0!.id,
              kind: 'door',
              widthMm: 900,
              heightMm: 2100,
              sillMm: 0,
              offsetMm: Math.round(wallLengthMm(w0!) / 2),
              swing: 'in-left',
              tag: null,
            },
          },
          {
            type: 'opening.add',
            payload: {
              id: WINDOW_ID,
              wallId: w1!.id,
              kind: 'window',
              widthMm: 1200,
              heightMm: 1200,
              sillMm: 900,
              offsetMm: Math.round(wallLengthMm(w1!) / 2),
              swing: 'out-left',
              tag: null,
            },
          },
        ],
        log.headIdx,
      );

      // This client has no live pull for ops another client appended — reload
      // so the hydrate folds them in. (The undo stack resets with it, which
      // is why every undo asserted below is of an op made AFTER this point.)
      await page.goto(`${APP_URL}/projects/${projectId}/plan`);
      await expect(page.locator('[data-garh-canvas="plan"]')).toBeVisible({ timeout: 20_000 });
    });

    await test.step('Tab into 3D — the same canvas swaps its layer set in place', async () => {
      await toggleViewWithTab(page, '3d');

      await expect(statusChip3d(page), 'the 3D status chip never appeared').toBeVisible({
        timeout: 20_000,
      });
      await expect
        .poll(async () => (await hooksSnapshot(page)).rebuildCount, {
          timeout: SYNC_TIMEOUT_MS,
          message: 'the 3D scene never reported a rebuild — nothing was extruded',
        })
        .toBeGreaterThan(0);

      const snapshot = await hooksSnapshot(page);
      expect(snapshot.viewMode).toBe('3d');
      // Record the boolean-engine outcome instead of asserting it (see header).
      test.info().annotations.push({
        type: 'boolean-engine',
        description: `status=${snapshot.engineStatus} holes=${
          (await statusChip3d(page).getAttribute('data-garh-holes')) ?? '?'
        }`,
      });
    });

    await test.step('selection crosses the views, both directions', async () => {
      const folded = await projectModel(request, token, projectId);
      const wallId = folded.model.house.walls[0]!.id;

      // 2D → 3D: select in the plan, Tab, still selected — same store, same id.
      await toggleViewWithTab(page, 'plan');
      await selectViaHooks(page, [wallId]);
      await expect(
        inspector(page).getByText(/wall/i).first(),
        'selecting a wall should put it in the inspector',
      ).toBeVisible({ timeout: 10_000 });

      await toggleViewWithTab(page, '3d');
      expect(
        (await hooksSnapshot(page)).selectedIds,
        'the Tab swap must not touch the selection',
      ).toEqual([wallId]);
      await expect(
        inspector(page).getByText(/wall/i).first(),
        'the inspector should still show the wall in 3D',
      ).toBeVisible();

      // 3D → selection: a REAL click, resolved by the one shared picker.
      await clickEmpty3d(page); // sky/ground — the empty pick clears
      expect((await hooksSnapshot(page)).selectedIds, 'a click on sky/ground clears').toEqual([]);

      const box = await canvasBox(page);
      // The entry fit frames the FULL building extent — BOTH storeys — and the
      // first floor has no walls yet, so the built massing sits in the UPPER
      // half of the frame with empty headroom above centre-screen. (Executed:
      // probes clustered at 0.5–0.65 of the height all landed on the ground
      // mat and honestly cleared the selection four times over.) The fan
      // therefore sweeps the centre column from above centre downwards; one
      // hit is all the assertion needs.
      const probes = [
        { x: box.x + box.width / 2, y: box.y + box.height * 0.42 },
        { x: box.x + box.width / 2, y: box.y + box.height * 0.35 },
        { x: box.x + box.width / 2, y: box.y + box.height * 0.5 },
        { x: box.x + box.width / 2, y: box.y + box.height * 0.55 },
        { x: box.x + box.width / 2, y: box.y + box.height * 0.65 },
        { x: box.x + box.width * 0.42, y: box.y + box.height * 0.6 },
        { x: box.x + box.width * 0.58, y: box.y + box.height * 0.5 },
        { x: box.x + box.width * 0.58, y: box.y + box.height * 0.4 },
      ];
      let picked: readonly string[] = [];
      for (const probe of probes) {
        await page.mouse.click(probe.x, probe.y);
        picked = (await hooksSnapshot(page)).selectedIds;
        if (picked.length > 0) break;
      }
      expect(
        picked.length,
        'clicking the building in 3D should select a model element through the shared picker',
      ).toBeGreaterThan(0);

      const known = new Set([
        ...folded.model.house.walls.map((w) => w.id),
        ...folded.model.house.rooms.map((r) => r.id),
        DOOR_ID,
        WINDOW_ID,
      ]);
      expect(
        known.has(picked[0]!),
        `3D picked "${picked[0]!}", which the fold does not contain`,
      ).toBe(true);
    });

    let wallsBeforeKit = '';
    let rebuildsBeforeKit = 0;

    await test.step('apply a facade kit — op 27 in the log, walls untouched (§8)', async () => {
      const before = await projectModel(request, token, projectId);
      wallsBeforeKit = JSON.stringify(before.model.house.walls);
      rebuildsBeforeKit = (await hooksSnapshot(page)).rebuildCount;

      await page.getByRole('button', { name: 'Apply Contemporary' }).click();

      await expect
        .poll(
          async () =>
            (await opsSince(request, token, projectId, -1)).ops.some(
              (op) => op.type === 'facade.apply_kit',
            ),
          { timeout: SYNC_TIMEOUT_MS, message: 'facade.apply_kit never reached the op log' },
        )
        .toBe(true);

      const after = await projectModel(request, token, projectId);
      expect(after.model.house.facade.kitId).toBe('contemporary');
      expect(
        after.model.house.facade.components.length,
        'applying a kit to a real frontage should instantiate components',
      ).toBeGreaterThan(0);
      expect(
        JSON.stringify(after.model.house.walls),
        '§8: a facade change must not move, add or retag a single wall',
      ).toBe(wallsBeforeKit);

      // The store mirror agrees with the fold.
      const snapshot = await hooksSnapshot(page);
      expect(snapshot.facadeKitId).toBe('contemporary');
      expect(snapshot.facadeComponentCount).toBe(after.model.house.facade.components.length);
    });

    await test.step('edit one component through the inspector — op 28', async () => {
      const folded = await projectModel(request, token, projectId);
      const chajja = folded.model.house.facade.components.find((c) => c.kind === 'chajja');
      expect(
        chajja,
        'the Contemporary kit dresses a window with a chajja — the arranged window guarantees one',
      ).toBeTruthy();

      const currentMm = Number(chajja!.params.projectionMm ?? 600);
      const nextMm = currentMm === 600 ? 750 : 600; // the kit's allowedProjectionsMm

      await selectViaHooks(page, [chajja!.id]);
      await expect(
        inspector(page).getByLabel('Projection'),
        'selecting a facadecomp id should route the inspector to the facade element panel',
      ).toBeVisible({ timeout: 10_000 });
      await inspector(page).getByLabel('Projection').selectOption(String(nextMm));

      await expect
        .poll(
          async () =>
            (await opsSince(request, token, projectId, -1)).ops.some(
              (op) => op.type === 'facade.edit_component',
            ),
          { timeout: SYNC_TIMEOUT_MS, message: 'facade.edit_component never reached the op log' },
        )
        .toBe(true);

      const after = await projectModel(request, token, projectId);
      const edited = after.model.house.facade.components.find((c) => c.id === chajja!.id);
      expect(Number(edited?.params.projectionMm), 'op 28 should have patched the projection').toBe(
        nextMm,
      );
      expect(
        JSON.stringify(after.model.house.walls),
        '§8 again: editing a component must not touch the plan',
      ).toBe(wallsBeforeKit);

      // §8 seen from §14's side: the kit apply AND the component edit happened
      // with the 3D scene mounted, and neither re-meshed a single storey group.
      expect(
        (await hooksSnapshot(page)).rebuildCount,
        'facade ops must not dirty the building meshes',
      ).toBe(rebuildsBeforeKit);
    });

    await test.step('scrub the sun — document, ops and meshes all stand still', async () => {
      const opsBefore = (await opsSince(request, token, projectId, -1)).ops.length;
      const before = await hooksSnapshot(page);

      const slider = page.getByLabel('Time of day, IST');
      await expect(slider, 'the sun scrubber should be docked on the 3D view').toBeVisible();
      const valueBefore = await slider.inputValue();
      await slider.focus();
      for (let i = 0; i < 24; i += 1) await page.keyboard.press('ArrowRight');
      expect(await slider.inputValue(), 'the scrub must have really moved the slider').not.toBe(
        valueBefore,
      );

      const after = await hooksSnapshot(page);
      expect(after.rebuildCount, 'a sun scrub must re-mesh NOTHING (§14)').toBe(
        before.rebuildCount,
      );
      expect(after.headIdx, 'a sun scrub folds from no op').toBe(before.headIdx);
      expect(after.pendingCount, 'nothing may be queued to the server').toBe(0);
      expect(
        (await opsSince(request, token, projectId, -1)).ops.length,
        'the server log must not have grown',
      ).toBe(opsBefore);
    });

    await test.step('§14: a plan edit re-meshes, incrementally, under 100 ms', async () => {
      // The scene has been mounted since the Tab — this is the INCREMENTAL
      // path (per-storey signature cache), not a first build. The edit goes
      // through the inspector's real thickness field (millimetre-native).
      const folded = await projectModel(request, token, projectId);
      const wall = folded.model.house.walls[0]!;
      const before = await hooksSnapshot(page);

      await selectViaHooks(page, [wall.id]);
      const thickness = inspector(page).getByLabel('Thickness');
      await expect(thickness).toBeVisible({ timeout: 10_000 });
      await thickness.fill('345');
      await thickness.press('Enter');

      await expect
        .poll(
          async () =>
            (await projectModel(request, token, projectId)).model.house.walls.find(
              (w) => w.id === wall.id,
            )?.thicknessMm,
          { timeout: SYNC_TIMEOUT_MS, message: 'the thickness edit never reached the server' },
        )
        .toBe(345);

      await expect
        .poll(async () => (await hooksSnapshot(page)).rebuildCount, {
          timeout: SYNC_TIMEOUT_MS,
          message: 'the wall edit should have re-meshed the building while in 3D',
        })
        .toBeGreaterThan(before.rebuildCount);

      const ms = Number(await statusChip3d(page).getAttribute('data-garh-rebuild-ms'));
      expect(Number.isFinite(ms), 'the chip should carry the rebuild wall-clock').toBe(true);
      test.info().annotations.push({
        type: 'rebuild-budget',
        description: `${ms} ms (budget ${REBUILD_BUDGET_MS} ms, §14)`,
      });
      expect(ms, `§14: 3D rebuild after an edit must be < ${REBUILD_BUDGET_MS} ms`).toBeLessThan(
        REBUILD_BUDGET_MS,
      );

      // Undo is a plan edit too — it must re-mesh, and stay inside the budget.
      await focusCanvasKeyboard(page);
      await page.keyboard.press('ControlOrMeta+z');
      await expect
        .poll(
          async () =>
            (await projectModel(request, token, projectId)).model.house.walls.find(
              (w) => w.id === wall.id,
            )?.thicknessMm,
          { timeout: SYNC_TIMEOUT_MS, message: 'undo never reverted the thickness on the server' },
        )
        .not.toBe(345);
      const undoMs = Number(await statusChip3d(page).getAttribute('data-garh-rebuild-ms'));
      expect(undoMs, 'undo re-mesh must also sit inside the §14 budget').toBeLessThan(
        REBUILD_BUDGET_MS,
      );
    });

    await test.step('see one storey / all — visibility is view state, never an op', async () => {
      const opsBefore = (await opsSince(request, token, projectId, -1)).ops.length;

      const bar = page.getByRole('group', { name: 'Storeys shown in 3D' });
      await expect(bar, 'two storeys should produce the visibility switch').toBeVisible();
      await bar.getByRole('button', { name: 'First' }).click();
      expect((await hooksSnapshot(page)).visibleStoreyId).toBe(STOREY_1);
      await bar.getByRole('button', { name: 'All' }).click();
      expect((await hooksSnapshot(page)).visibleStoreyId).toBeNull();

      expect(
        (await opsSince(request, token, projectId, -1)).ops.length,
        'visibility toggles must never write the op log',
      ).toBe(opsBefore);
    });

    await test.step('and Tab lands you back on the plan you left', async () => {
      await toggleViewWithTab(page, 'plan');
      expect((await hooksSnapshot(page)).viewMode).toBe('2d');
      await expect
        .poll(async () => (await projectModel(request, token, projectId)).model.house.walls.length)
        .toBe(4);
    });
  });
});
