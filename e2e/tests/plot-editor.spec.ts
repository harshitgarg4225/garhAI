/**
 * @plot — the plot editor's direct-manipulation surfaces in a REAL browser.
 *
 * The jsdom specs (`apps/web/src/features/plot/*.test.tsx`) prove the handlers
 * fold the right ops. What they cannot prove is the furniture-layer class of
 * bug (CLAUDE.md #4): an SVG `<circle role="button">` that a real pointer
 * never reaches — covered by a sibling, swallowed by a `pointer-events` rule,
 * or scaled away by `preserveAspectRatio`. So every gesture below is made with
 * `page.mouse` / the keyboard on the live page, and every assertion is made
 * against the SERVER's op log folded through the real model core
 * (`projectModel`) — an edit that was drawn but never dispatched, or dispatched
 * but never flushed, fails here.
 *
 * Runs against any stack with the mock providers (`docker compose up`, or a
 * per-agent api + vite via `APP_URL` / `API_URL`); no seed needed — it signs up
 * a throwaway firm.
 */

import { expect, test, type Page } from '@playwright/test';

import { createProject, projectModel, signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import {
  adoptApiSession,
  collectConsoleErrors,
  expectNoConsoleErrors,
  waitForSaved,
} from '../support/ui';

/** The 30 × 40 ft quick-start, in mm. */
const RECT = [
  { x: 0, y: 0 },
  { x: 9144, y: 0 },
  { x: 9144, y: 12192 },
  { x: 0, y: 12192 },
];

/** Drag with a real pointer: down on the element's centre, move in steps, up. */
async function dragBy(page: Page, target: ReturnType<Page['locator']>, dx: number, dy: number) {
  const box = await target.boundingBox();
  expect(box, 'the handle has no box — it is not laid out, so nothing can hit it').not.toBeNull();
  const cx = box!.x + box!.width / 2;
  const cy = box!.y + box!.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + dx / 2, cy + dy / 2, { steps: 4 });
  await page.mouse.move(cx + dx, cy + dy, { steps: 4 });
  await page.mouse.up();
}

test.describe('@plot the plot editor answers a real pointer', () => {
  test('quick-start → drag a corner → type an edge → add a corner → nudge → road → compass → deed → deed area', async ({
    page,
    request,
  }) => {
    test.setTimeout(3 * 60_000);
    const consoleErrors = collectConsoleErrors(page);

    const email = uniqueEmail('plot');
    const session = await signUpFirm(request, { email, firmName: 'Plot Editor Associates' });
    const token = session.accessToken;
    const project = await createProject(request, token, 'Plot editor hit-test');
    const projectId = project.id;
    const model = () => projectModel(request, token, projectId);

    await test.step('open the Brief tab: the empty state offers width × depth and the deed', async () => {
      // The sign-up above already spent this address's code and started the 60s
      // resend cooldown (§13), and the login UI is not this spec's subject —
      // adopt the API session, exactly as the canvas/3D/render specs do.
      await adoptApiSession(page, request);
      await page.goto(`${APP_URL}/projects/${projectId}/brief`);
      await expect(page.getByText('No plot boundary yet')).toBeVisible({ timeout: 20_000 });
      await expect(page.getByRole('tab', { name: 'Width × depth' })).toBeVisible();
      await expect(page.getByRole('tab', { name: 'From the sale deed' })).toBeVisible();
    });

    await test.step('one click draws the 30 × 40 ft plot', async () => {
      await page.getByRole('button', { name: 'Create boundary' }).click();
      await expect(page.getByRole('application', { name: 'Plot boundary editor' })).toBeVisible();
      await expect(page.getByText('1,200.0 sq ft · 133 gaj').first()).toBeVisible();
      await waitForSaved(page);
      expect((await model()).model.plot.boundary).toEqual(RECT);
    });

    await test.step('a real pointer drags corner C and the server holds the snapped move', async () => {
      const cornerC = page.getByRole('button', { name: /^Corner C/ });
      await expect(cornerC).toBeVisible();
      // Up-right on screen is +x, +y in the model: the corner moves outward.
      await dragBy(page, cornerC, 40, -40);
      await waitForSaved(page);
      const ring = (await model()).model.plot.boundary;
      expect(ring).toHaveLength(4);
      expect(ring[2]).not.toEqual(RECT[2]);
      expect(ring[2]!.x).toBeGreaterThan(9144);
      expect(ring[2]!.y).toBeGreaterThan(12192);
      // Snapped to the coarse module: no corner lands off the 115 mm grid.
      expect(ring[2]!.x % 115).toBe(0);
      expect(ring[2]!.y % 115).toBe(0);
      expect(ring[0]).toEqual(RECT[0]);
      expect(ring[1]).toEqual(RECT[1]);
      expect(ring[3]).toEqual(RECT[3]);
    });

    await test.step('the area chip re-measures the dragged plot', async () => {
      await expect(page.getByText('1,200.0 sq ft · 133 gaj')).toHaveCount(0);
    });

    await test.step('undo returns the rectangle (one step for the whole drag)', async () => {
      await page.keyboard.press('Control+z');
      await waitForSaved(page);
      expect((await model()).model.plot.boundary).toEqual(RECT);
    });

    await test.step("clicking an edge length and typing 40' stretches the rectangle, and says the far side moved", async () => {
      await page.getByRole('button', { name: /^Edge 1,/ }).click();
      const input = page.getByLabel('Edge 1 length');
      await expect(input).toBeVisible();
      await input.fill("40'");
      await input.press('Enter');
      await expect(page.getByRole('status').filter({ hasText: /A–B is now 40'-0"/ })).toBeVisible();
      await expect(page.getByRole('status').filter({ hasText: /far side moved/ })).toBeVisible();
      await waitForSaved(page);
      expect((await model()).model.plot.boundary).toEqual([
        { x: 0, y: 0 },
        { x: 12192, y: 0 },
        { x: 12192, y: 12192 },
        { x: 0, y: 12192 },
      ]);
    });

    await test.step('the + handle adds a corner on edge B–C', async () => {
      await page.getByRole('button', { name: 'Add a corner on edge 2' }).click();
      await waitForSaved(page);
      const ring = (await model()).model.plot.boundary;
      expect(ring).toHaveLength(5);
      expect(ring[2]).toEqual({ x: 12192, y: 6096 });
      await expect(page.getByRole('button', { name: /^Corner E/ })).toBeVisible();
    });

    await test.step('a focused corner answers the arrow keys', async () => {
      const cornerC = page.getByRole('button', { name: /^Corner C/ });
      await cornerC.focus();
      await page.keyboard.press('ArrowRight');
      await waitForSaved(page);
      expect((await model()).model.plot.boundary[2]).toEqual({ x: 12192 + 115, y: 6096 });
      await page.keyboard.press('Shift+ArrowLeft');
      await waitForSaved(page);
      expect((await model()).model.plot.boundary[2]).toEqual({ x: 12192 + 115 - 25, y: 6096 });
    });

    await test.step('Delete removes the added corner', async () => {
      await page.getByRole('button', { name: /^Corner C/ }).focus();
      await page.keyboard.press('Delete');
      await waitForSaved(page);
      expect((await model()).model.plot.boundary).toHaveLength(4);
    });

    await test.step('ticking a road makes that edge the front and names the others', async () => {
      await page.getByRole('checkbox').first().check();
      await waitForSaved(page);
      expect((await model()).model.plot.roads).toEqual([
        { edgeIndex: 0, widthMm: 9000, name: null },
      ]);
      await expect(page.getByText('Front · entry')).toBeVisible();
      await expect(page.getByText('Side A', { exact: true })).toBeVisible();
      await expect(page.getByText('Side B', { exact: true })).toBeVisible();
      await expect(page.getByText('Rear', { exact: true })).toBeVisible();
      await page.getByLabel('Road name for edge 1').fill('12th Cross');
      await page.getByLabel('Road name for edge 1').press('Enter');
      await waitForSaved(page);
      expect((await model()).model.plot.roads[0]?.name).toBe('12th Cross');
    });

    await test.step('a real pointer turns the compass', async () => {
      const dial = page.getByRole('slider', { name: 'True north direction' });
      await expect(dial).toBeVisible();
      const box = await dial.boundingBox();
      expect(box).not.toBeNull();
      const cx = box!.x + box!.width / 2;
      const cy = box!.y + box!.height / 2;
      // Down at "east" of the dial, release there: north = 90° clockwise.
      await page.mouse.move(cx + box!.width * 0.4, cy);
      await page.mouse.down();
      await page.mouse.move(cx + box!.width * 0.4, cy + 1, { steps: 2 });
      await page.mouse.up();
      await waitForSaved(page);
      const deg = (await model()).model.plot.northDeg;
      expect(Math.abs(deg - 90)).toBeLessThanOrEqual(2);
      await expect(dial).toHaveAttribute('aria-valuenow', String(deg));
    });

    await test.step('From deed… replaces the boundary from typed sides in one step', async () => {
      await page.getByRole('button', { name: 'From deed…' }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await dialog.getByLabel('Side A–B').fill('12m');
      await dialog.getByLabel('Side A–B').press('Tab');
      await dialog.getByRole('button', { name: 'Replace boundary' }).click();
      await expect(dialog).toBeHidden();
      await waitForSaved(page);
      const ring = (await model()).model.plot.boundary;
      expect(ring).toHaveLength(4);
      expect(ring[1]).toEqual({ x: 12000, y: 0 });
      // The road survived the replacement (same edge count).
      expect((await model()).model.plot.roads[0]?.edgeIndex).toBe(0);
    });

    await test.step('the deed area typed in sq ft is reconciled against the drawn area', async () => {
      await page.getByRole('button', { name: /Not entered — click to type it/ }).click();
      const area = page.getByLabel('Area as per the sale deed');
      await area.fill('1200 sq ft');
      await area.press('Enter');
      await expect(page.getByText(/exceeds the deed by/)).toBeVisible();
      await waitForSaved(page);
      const overrides = (await model()).model.plot.regProfile.overrides as {
        values?: Record<string, number>;
      };
      expect(overrides.values?.deedAreaMm2).toBe(111_483_648);
    });

    expectNoConsoleErrors(consoleErrors);
  });
});
