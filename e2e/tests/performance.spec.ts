/**
 * §14 performance budgets, as trace-based assertions. Skipped until the canvas exists.
 *
 * | Surface | Budget | Enforcement |
 * |---|---|---|
 * | Canvas frame | <16ms during pan/zoom/drag on the G+2 demo | Playwright trace assertion |
 * | 3D rebuild after edit | <100ms dirty-storey | vitest perf (not here) |
 * | Render (mock) | <1s | e2e |
 * | Initial web load | <3s on 4G mid-range, initial bundle <1.5MB gz | Lighthouse CI >=85 |
 *
 * ## Why the scaffolding lands before the canvas
 *
 * *"Latency budgets are features"* (golden rule 7). A budget with no measurement is a wish,
 * and the usual failure mode is that the measurement gets written after the canvas is
 * already slow — at which point the number is negotiated down instead of met. The
 * measurement helpers below are real and runnable today; only the assertions that need
 * something to pan are skipped.
 *
 * ## What is measured, and how
 *
 * `frameStatistics` collects `requestAnimationFrame` deltas in the page while an interaction
 * runs, and reports the median and the 95th percentile. The **95th** is the number that
 * matters: a median of 8ms with a 60ms tail is a visible stutter, and averaging hides it.
 * A budget of 16ms is 60fps.
 *
 * Playwright's own tracing (`context.tracing`) records what happened; it does not measure
 * frames, which is why this uses in-page instrumentation and keeps the trace for the
 * post-mortem.
 */

import { expect, test, type Page } from '@playwright/test';
import { APP_URL } from '../support/env';
import { canvas } from '../support/ui';

/** 60fps. §14's canvas budget. */
const FRAME_BUDGET_MS = 16;

/** §14: "Render (mock) < 1s". */
const MOCK_RENDER_BUDGET_MS = 1_000;

/** §15 micro-speed: "open project → interactive canvas < 2s (snapshot + tail)". */
const OPEN_PROJECT_BUDGET_MS = 2_000;

export interface FrameStats {
  count: number;
  medianMs: number;
  p95Ms: number;
  worstMs: number;
}

/**
 * Measure frame intervals while `interact()` runs.
 *
 * Deliberately started and stopped around the interaction rather than for the whole test:
 * an idle page renders no frames, and including idle time would dilute the tail into
 * nothing.
 */
export async function frameStatistics(
  page: Page,
  interact: () => Promise<void>,
): Promise<FrameStats> {
  await page.evaluate(() => {
    const store: number[] = [];
    (window as unknown as { __garhFrames?: number[] }).__garhFrames = store;
    let previous = performance.now();
    const tick = (): void => {
      const now = performance.now();
      store.push(now - previous);
      previous = now;
      if ((window as unknown as { __garhFrameCollecting?: boolean }).__garhFrameCollecting) {
        requestAnimationFrame(tick);
      }
    };
    (window as unknown as { __garhFrameCollecting?: boolean }).__garhFrameCollecting = true;
    requestAnimationFrame(tick);
  });

  await interact();

  return page.evaluate(() => {
    (window as unknown as { __garhFrameCollecting?: boolean }).__garhFrameCollecting = false;
    const raw = (window as unknown as { __garhFrames?: number[] }).__garhFrames ?? [];
    // Drop the first sample: it spans the gap between instrumenting and interacting.
    const samples = raw.slice(1).sort((a, b) => a - b);
    const at = (fraction: number): number =>
      samples.length === 0 ? 0 : (samples[Math.min(samples.length - 1, Math.floor(samples.length * fraction))] ?? 0);
    return {
      count: samples.length,
      medianMs: at(0.5),
      p95Ms: at(0.95),
      worstMs: samples.length === 0 ? 0 : (samples[samples.length - 1] ?? 0),
    };
  });
}

/** Attach the numbers to the report, so a regression has a paper trail. */
async function reportStats(label: string, stats: FrameStats): Promise<void> {
  await test.info().attach(`${label}.json`, {
    body: JSON.stringify(stats, null, 2),
    contentType: 'application/json',
  });
}

test.describe('@perf §14 budgets', () => {
  test('the login screen loads within the initial-load budget', async ({ page }) => {
    // This one is NOT skipped: it needs no canvas, and it is the budget most likely to rot
    // silently as dependencies accumulate.
    const started = Date.now();
    await page.goto(`${APP_URL}/login`, { waitUntil: 'load' });
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    const elapsed = Date.now() - started;

    await test.info().attach('login-load.json', {
      body: JSON.stringify({ elapsedMs: elapsed }, null, 2),
      contentType: 'application/json',
    });

    // Generous against a cold Vite dev server on CI hardware: §14's 3s target is for a
    // production bundle on 4G, and Lighthouse CI is the real gate for it. This assertion
    // exists to catch an order-of-magnitude regression, not to police the last 200ms.
    expect(elapsed, 'the login screen took unusually long to become interactive').toBeLessThan(
      15_000,
    );
  });

  test('the timing instrumentation itself works', async ({ page }) => {
    // Also not skipped: if `frameStatistics` silently returns zeros, every budget below
    // passes forever. Measuring an animation-free page still yields ~16ms rAF ticks.
    await page.goto(`${APP_URL}/login`);
    const stats = await frameStatistics(page, async () => {
      await page.waitForTimeout(500);
    });
    await reportStats('instrumentation-check', stats);

    expect(stats.count, 'no frames were sampled — the helper is broken, not the app').toBeGreaterThan(
      5,
    );
    expect(stats.medianMs).toBeGreaterThan(0);
  });

  test('pan and zoom on the demo G+2 hold 60fps', async ({ page }) => {
    test.skip(
      true,
      'Phase 3: the canvas exists now (Phase 4) and this body would run against it, but ' +
        'the budget is written for "the demo G+2" and the seeded demo project has no ' +
        'solved plan until the solver has run on it (§17). Measuring pan/zoom on an empty ' +
        'storey would report a number that means nothing. Turn this on in the same commit ' +
        'that seeds a solved demo.',
    );

    await expect(canvas(page)).toBeVisible();
    const box = await canvas(page).boundingBox();
    expect(box).toBeTruthy();

    const stats = await frameStatistics(page, async () => {
      const { x, y, width, height } = box!;
      const centre = { x: x + width / 2, y: y + height / 2 };
      await page.mouse.move(centre.x, centre.y);
      await page.mouse.down();
      for (let step = 0; step < 40; step += 1) {
        await page.mouse.move(centre.x + step * 8, centre.y + step * 4);
      }
      await page.mouse.up();
      for (let step = 0; step < 10; step += 1) {
        await page.mouse.wheel(0, -120);
      }
    });
    await reportStats('pan-zoom', stats);

    expect(stats.p95Ms, `§14: canvas frames must stay under ${FRAME_BUDGET_MS}ms`).toBeLessThan(
      FRAME_BUDGET_MS,
    );
  });

  test('dragging a wall stays under the frame budget', async ({ page }) => {
    test.skip(
      true,
      'Phase 3, same reason as the pan/zoom budget: the select tool and the shared picker ' +
        'exist, but a drag on an empty storey is not the load §14 budgets. ' +
        '`plan-canvas.spec.ts` proves the drag WORKS; this proves it is fast enough, and ' +
        'that needs the solved demo.',
    );

    const stats = await frameStatistics(page, async () => {
      await page.waitForTimeout(0);
    });
    await reportStats('wall-drag', stats);
    expect(stats.p95Ms).toBeLessThan(FRAME_BUDGET_MS);
  });

  test('opening a project reaches an interactive canvas within 2s', async ({ page }) => {
    test.skip(
      true,
      'Phase 3: the canvas has an interactive state to reach now, but §15 measures it on ' +
        'the demo project ("open project → interactive canvas <2s"), and an empty project ' +
        'reaches it trivially. Needs the solved demo the solver produces.',
    );

    const started = Date.now();
    await canvas(page).click();
    expect(Date.now() - started).toBeLessThan(OPEN_PROJECT_BUDGET_MS);
  });

  test('a mock render returns in under a second', async () => {
    test.skip(true, 'Phase 7: the render worker and the Renders tab UI are later.');

    const started = Date.now();
    // Assert: the render job reaches `succeeded` and the image is on screen.
    expect(Date.now() - started).toBeLessThan(MOCK_RENDER_BUDGET_MS);
  });

  test('switching storeys is instant (pre-built meshes)', async ({ page }) => {
    test.skip(true, 'Phase 5: storey meshes do not exist until the 3D scene does.');

    const stats = await frameStatistics(page, async () => {
      await page.keyboard.press('2');
      await page.keyboard.press('1');
    });
    await reportStats('storey-switch', stats);
    expect(stats.p95Ms).toBeLessThan(FRAME_BUDGET_MS);
  });
});
