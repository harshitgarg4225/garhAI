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
 * ones), one door + one window (aiming the opening tool needs the wall's
 * PIXEL position, which typed-length drawing deliberately does not reveal),
 * and — since 2026-09-20 — THE WALLS. The step that drove the wall tool with
 * real pointer events broke when the 2D view grew its docked panels: they
 * cover the right half of the canvas and two of four legs landed on a panel.
 * `plan-canvas.spec.ts` is the spec whose subject is the drawing tools and
 * it still drives them for real; this one is the 3D DoD, and none of its
 * claims depend on how the walls arrived. The openings are appended before a
 * reload so the hydrate picks them up — this client has no live pull for ops
 * it did not send. The kit, the component edit, the inspector edit that
 * drives the §14 rebuild, the scrub, the click-to-select, the GLB export and
 * every toggle are real interactions.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS SPEC CANNOT ASSERT, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 *  · **Which pixel is which colour.** The spec reads the frame's STATISTICS
 *    (2026-09-20) — distinct colours, the dominant colour's share, mean
 *    brightness, and how much two frames differ — not a baseline image. That
 *    catches a blank canvas, a flat canvas and an unlit one on any GPU and
 *    any font stack. "The wall is the right shade of cream" still needs the
 *    golden PNG in `visual-regression.spec.ts`, which only the CI runner
 *    class may mint.
 *  · **Shadow/sun DIRECTION.** Pinned by the sun module's 36-row NOAA table
 *    in vitest; a browser screenshot cannot re-derive an azimuth. What the
 *    spec does assert is that moving the sun between two daylight hours
 *    really repaints the scene — the claim the unlit facade layer used to
 *    break silently.
 *  · **Storey-visibility pixels.** Asserted through the store probe; whether
 *    the GPU stopped drawing the hidden storey is a pixel claim this spec
 *    refuses to fake. (Hidden ⇒ unpickable is pinned in the canvas core.)
 *  · **Walk mode.** No CI runner exercises pointer-lock-style navigation
 *    honestly; `orbitOps.test.ts` owns the maths — including, since
 *    2026-09-20, wall collision and the look-up clamp.
 *
 * WHAT IT NOW ASSERTS THAT IT ONCE ONLY ANNOTATED: the boolean engine
 * reaching `ready` with holes cut. That was called an environment fact for a
 * month while the real cause was a missing `locateFile` in our own loader —
 * every session ran the no-holes fallback and no test could say so.
 */

import { expect, test, type Page } from '@playwright/test';

import { appendOps, createProject, opsSince, projectModel, signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import { readFile } from 'node:fs/promises';

import {
  canvasBox,
  changedPixelShare,
  clickEmpty3d,
  describeCanvasPixels,
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

/** Caller-minted wall ids for the arranged envelope (see the step's note). */
const WALL_G_S = 'wall_01J3D0000000000000000000G1';
const WALL_G_E = 'wall_01J3D0000000000000000000G2';
const WALL_G_N = 'wall_01J3D0000000000000000000G3';
const WALL_G_W = 'wall_01J3D0000000000000000000G4';
const WALL_F_S = 'wall_01J3D0000000000000000000F1';
const WALL_F_E = 'wall_01J3D0000000000000000000F2';
const WALL_F_N = 'wall_01J3D0000000000000000000F3';
const WALL_F_W = 'wall_01J3D0000000000000000000F4';

/** The first floor stops here: the rest of the ground floor is terrace. */
const SETBACK_H_MM = 2300;

/** Server round trip with headroom. */
const SYNC_TIMEOUT_MS = 20_000;

/** §14: 3D rebuild after an edit, dirty storeys only. */
const REBUILD_BUDGET_MS = 100;

function wallLengthMm(wall: { a: { x: number; y: number }; b: { x: number; y: number } }): number {
  return Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y);
}

/**
 * Wait until the 3D scene stops re-meshing, then return the settled counter.
 *
 * The scene rebuilds asynchronously for reasons that have nothing to do with
 * the step under test: the boolean engine arriving salts every group's
 * signature, so the whole building re-meshes once more AFTER the chip first
 * reports `ready`. A baseline captured during that window makes the §8
 * isolation claim ("a facade op must not dirty the building meshes") fail for
 * a timing reason — executed, 2026-09-20: 3 → 5 with no group re-meshed.
 * A settle is honest here in a way that a bare `waitForTimeout` is not: it
 * asserts the scene reached a quiet state, and fails loudly if it never does.
 */
async function settledRebuildCount(page: Page): Promise<number> {
  let last = -1;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const now = (await hooksSnapshot(page)).rebuildCount;
    if (now === last) return now;
    last = now;
    await page.waitForTimeout(400);
  }
  throw new Error('the 3D scene never stopped re-meshing');
}

/**
 * The JSON chunk of a GLB, by the spec's own layout: a 12-byte header, then
 * chunks of `[uint32 length][uint32 type][payload]`, the first of which is
 * JSON (type 0x4E4F534A). Parsed here rather than with a glTF library
 * because the point is to read what the FILE says, in as few layers as
 * possible — a loader that repaired or renamed anything would hide the very
 * defect this asserts against.
 */
function readGlbJsonChunk(bytes: Buffer): { nodes?: { name?: string }[] } {
  const chunkLength = bytes.readUInt32LE(12);
  const chunkType = bytes.readUInt32LE(16);
  expect(chunkType, 'the first GLB chunk is not JSON').toBe(0x4e4f534a);
  const text = bytes.subarray(20, 20 + chunkLength).toString('utf8');
  return JSON.parse(text) as { nodes?: { name?: string }[] };
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

    await test.step('arrange the envelope: a G+1 whose first floor is set back', async () => {
      // WHY THE WALLS ARE APPENDED, NOT DRAWN (changed 2026-09-20). This step
      // used to drive the wall tool with real pointer events, calibrating the
      // camera scale from one throwaway wall. It stopped working when the 2D
      // view grew its docked panels (layers, measure, views, underlay): they
      // cover the right half of the canvas, so two of the four legs landed on
      // a panel and the chain committed two walls instead of four. Executed
      // on this machine, 2026-09-20 — the screenshot shows the panels over
      // the drawing.
      //
      // Drawing tools are `plan-canvas.spec.ts`'s subject and it still drives
      // them for real. THIS spec is the 3D DoD, and every 3D claim below —
      // the extrusion, the picker, the kit, the sun, the §14 incremental
      // rebuild through the real inspector field — is unaffected by how the
      // walls got there. Arranging them keeps the 3D suite from going red for
      // a 2D reason, which is the difference between a signal and a nuisance.
      //
      // The first floor is deliberately SMALLER than the ground floor: that
      // is the commonest Indian massing, and it is what puts a terrace slab
      // and parapet on the ground floor's exposed rear (`three/terrace.ts`).
      const log = await opsSince(request, token, projectId, -1);
      const wall = (
        id: string,
        storeyId: string,
        a: { x: number; y: number },
        b: { x: number; y: number },
      ) => ({
        type: 'wall.add',
        payload: { id, storeyId, a, b, thicknessMm: 230, kind: 'external' },
      });
      await appendOps(
        request,
        token,
        projectId,
        [
          wall(WALL_G_S, STOREY_G, { x: 0, y: 0 }, { x: OUTER_W_MM, y: 0 }),
          wall(WALL_G_E, STOREY_G, { x: OUTER_W_MM, y: 0 }, { x: OUTER_W_MM, y: OUTER_H_MM }),
          wall(WALL_G_N, STOREY_G, { x: OUTER_W_MM, y: OUTER_H_MM }, { x: 0, y: OUTER_H_MM }),
          wall(WALL_G_W, STOREY_G, { x: 0, y: OUTER_H_MM }, { x: 0, y: 0 }),
          wall(WALL_F_S, STOREY_1, { x: 0, y: 0 }, { x: OUTER_W_MM, y: 0 }),
          wall(WALL_F_E, STOREY_1, { x: OUTER_W_MM, y: 0 }, { x: OUTER_W_MM, y: SETBACK_H_MM }),
          wall(WALL_F_N, STOREY_1, { x: OUTER_W_MM, y: SETBACK_H_MM }, { x: 0, y: SETBACK_H_MM }),
          wall(WALL_F_W, STOREY_1, { x: 0, y: SETBACK_H_MM }, { x: 0, y: 0 }),
        ],
        log.headIdx,
      );

      await expect
        .poll(
          async () => (await projectModel(request, token, projectId)).model.house.walls.length,
          { timeout: SYNC_TIMEOUT_MS, message: 'the arranged walls never reached the server' },
        )
        .toBe(8);
    });

    await test.step('arrange one door + one window on the ground-floor walls', async () => {
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

      // THE OPENING HOLES, ASSERTED (2026-09-20). This used to be an
      // annotation — "whether Manifold's WASM loads in the CI browser is an
      // environment fact" — and that politeness hid a real defect for a
      // month: no `locateFile`, so Emscripten fetched `manifold.wasm`
      // relative to Vite's pre-bundled script, got index.html, and failed
      // with `expected magic word 00 61 73 6d, found 3c 21 64 6f`. EVERY
      // session silently ran the no-holes fallback. The loader now hands it
      // the bundled asset URL (features/canvas/three/booleans.ts), so the
      // engine reaching `ready` is a PRODUCT claim and belongs in a claim
      // that can fail.
      await expect
        .poll(async () => (await hooksSnapshot(page)).engineStatus, {
          timeout: SYNC_TIMEOUT_MS,
          message:
            'the Manifold boolean engine never became ready — walls would render without their opening holes',
        })
        .toBe('ready');
      await expect
        .poll(async () => statusChip3d(page).getAttribute('data-garh-holes'), {
          timeout: SYNC_TIMEOUT_MS,
          message: 'the engine is ready but the scene still reports uncut walls',
        })
        .toBe('true');
    });

    await test.step('the view actually DRAWS a building — pixels, not just state', async () => {
      // §16's third claim, in the one form a CI runner can hold honestly:
      // not a golden PNG (that needs a baseline minted on the runner class),
      // but the two properties a broken 3D view always violates — a blank
      // frame, or a frame of one flat colour. Executed on the first run:
      // a project with no walls drew 53.8% one colour and 0 selectable
      // elements, which is exactly what this catches.
      const { distinctColours, dominantShare } = await describeCanvasPixels(page);
      test.info().annotations.push({
        type: 'pixels',
        description: `${distinctColours} distinct colours, dominant ${(dominantShare * 100).toFixed(1)}%`,
      });
      expect(distinctColours, 'the 3D canvas drew almost nothing').toBeGreaterThan(40);
      expect(dominantShare, 'the 3D canvas is one flat colour — nothing was extruded').toBeLessThan(
        0.92,
      );
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
      rebuildsBeforeKit = await settledRebuildCount(page);

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

    await test.step('…and the sun really MOVES the shadows, in pixels', async () => {
      // The claim behind the sun study, and the one the facade change of
      // 2026-09-20 was about: kit components used to be unlit boxes with
      // shading baked against a fixed direction, so chajjas and porches —
      // the elements whose job is to shade — did not move with the sun.
      // Two DAYLIGHT angles, not day vs night: a dark frame would "differ"
      // while proving nothing, so both frames must also still be lit.
      const slider = page.getByLabel('Time of day, IST');
      const at = async (minutes: number) => {
        await slider.fill(String(minutes));
        await slider.dispatchEvent('input');
        await slider.dispatchEvent('change');
        await page.waitForTimeout(900); // demand frameloop: one settle beat
        return describeCanvasPixels(page);
      };
      const morning = await at(9 * 60);
      const evening = await at(16 * 60);
      const changed = changedPixelShare(morning, evening);
      test.info().annotations.push({
        type: 'sun-shadows',
        description: `09:00→16:00 changed ${(changed * 100).toFixed(1)}% of pixels; brightness ${morning.meanBrightness.toFixed(0)} → ${evening.meanBrightness.toFixed(0)}`,
      });
      expect(morning.meanBrightness, '09:00 should be daylight').toBeGreaterThan(90);
      expect(evening.meanBrightness, '16:00 should be daylight').toBeGreaterThan(90);
      expect(
        changed,
        'moving the sun from morning to afternoon changed almost no pixels — the shadows are not following it',
      ).toBeGreaterThan(0.02);
    });

    await test.step('export the model as a GLB, from this very view', async () => {
      // Reads the produced BYTES (glTF magic, version, declared length) —
      // a test that mocked the exporter would prove the mock.
      const download = page.waitForEvent('download', { timeout: 60_000 });
      await page.getByTestId('export-glb-client').click();
      const file = await download;
      const path = await file.path();
      expect(path, 'the GLB download produced no file').toBeTruthy();
      const bytes = await readFile(path);
      expect(file.suggestedFilename()).toMatch(/\.glb$/);
      expect(bytes.length, 'the GLB is suspiciously small').toBeGreaterThan(2048);
      expect(bytes.readUInt32LE(0), 'not a glTF magic word').toBe(0x46546c67);
      expect(bytes.readUInt32LE(4), 'not glTF 2.0').toBe(2);
      expect(bytes.readUInt32LE(8), 'the GLB header length disagrees with the file').toBe(
        bytes.length,
      );

      // THE NAMES, read out of the real file. A GLB whose objects are all
      // `mesh_0 … mesh_N` is useless to the renderer artist the panel
      // advertises it for — they cannot select "the external walls". That is
      // exactly what shipped when the live meshes had no `name` prop while
      // the unit test exported a headless fixture that named its own
      // (CLAUDE.md bug 6). This reads the LIVE scene's bytes, so it goes red
      // if either producer forgets `meshNames.ts` again.
      const json = readGlbJsonChunk(bytes);
      const names: string[] = (json.nodes ?? []).map((node) => node.name ?? '');
      expect(names.length, 'the GLB carries no nodes at all').toBeGreaterThan(3);
      expect(
        names.filter((n) => /^mesh_\d+$/.test(n)),
        'unnamed meshes: the exporter fell back to glTF defaults',
      ).toEqual([]);
      expect(
        names.some((n) => n.startsWith('external_wall')),
        `no external-wall object in the export; names were ${names.join(', ')}`,
      ).toBe(true);
      expect(
        names.some((n) => n.startsWith('facade_')),
        'the applied facade kit is missing from the export',
      ).toBe(true);
      expect(names).toContain('garh-facade');
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
        .toBe(8);
    });
  });
});
