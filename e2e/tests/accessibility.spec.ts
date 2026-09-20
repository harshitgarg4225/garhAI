/**
 * `@a11y` — axe-core over every screen an architect reaches, in a real browser.
 *
 * The bar is **serious and critical**: those are the violations that stop someone
 * using the product (an unlabelled control, a contrast a low-vision architect
 * cannot read, a list a screen reader cannot parse). Moderate findings are
 * printed, not failed — `landmark-one-main` and `region` fire on the canvas
 * shell's panel grid, and chasing them would mean restructuring §12's layout for
 * a machine rather than for a person.
 *
 * Why a browser and not jsdom: two of the three real findings from the first run
 * of this file were a colour contrast (3.42:1 on an 11px chip label, invisible to
 * jsdom, which computes no styles) and a `<dl>` whose pairs were wrapped in
 * `<span>`. jsdom would have reported neither.
 *
 * Running it:
 *
 *     pnpm --filter @garh/e2e exec playwright test --grep @a11y
 *
 * `axe-core` (MPL-2.0, the same licence family as hypothesis) must be installed
 * for it to run. If it is not, this spec **fails** rather than skipping: a
 * green accessibility check that silently never ran is the repo's cardinal sin
 * (CLAUDE.md), and a skip here would read exactly like a pass in CI's summary.
 */

import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import { APP_URL, DEMO_EMAIL } from '../support/env';
import { createProjectThroughUi, signInThroughUi } from '../support/ui';

const require_ = createRequire(import.meta.url);

/** The axe source, read once. A missing dependency is a failure, never a skip. */
function axeSource(): string {
  try {
    return readFileSync(require_.resolve('axe-core/axe.min.js'), 'utf8');
  } catch (error) {
    throw new Error(
      'axe-core is not installed, so the accessibility gate cannot run. ' +
        'Install it (pnpm add -D axe-core --filter @garh/web) and run again. ' +
        `Resolution error: ${String(error)}`,
    );
  }
}

interface AxeNode {
  target: string[];
  failureSummary?: string;
}

interface AxeViolation {
  id: string;
  impact: string | null;
  help: string;
  nodes: AxeNode[];
}

const BLOCKING = new Set(['serious', 'critical']);

async function scan(page: Page, label: string): Promise<AxeViolation[]> {
  await page.addScriptTag({ content: axeSource() });
  const result = (await page.evaluate(async () => {
    const axe = (
      window as unknown as { axe: { run: (c: unknown, o: unknown) => Promise<unknown> } }
    ).axe;
    return await axe.run(document, {
      resultTypes: ['violations'],
      runOnly: {
        type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'],
      },
    });
  })) as { violations: AxeViolation[] };

  const blocking = result.violations.filter((v) => BLOCKING.has(v.impact ?? ''));
  const other = result.violations.filter((v) => !BLOCKING.has(v.impact ?? ''));
  if (other.length > 0) {
    console.log(
      `  ${label}: ${other.map((v) => `${v.id}×${v.nodes.length}`).join(', ')} (not blocking)`,
    );
  }
  return blocking;
}

/** Fails with the rule, the selector and axe's own remedy — enough to fix it. */
function assertClean(label: string, violations: AxeViolation[]): void {
  const report = violations
    .map(
      (v) =>
        `[${v.impact}] ${v.id}: ${v.help}\n` +
        v.nodes
          .slice(0, 5)
          .map(
            (n) =>
              `    ${n.target.join(' ')}\n      ${(n.failureSummary ?? '').replace(/\n/g, ' | ')}`,
          )
          .join('\n'),
    )
    .join('\n');
  expect(violations, `${label} has serious/critical accessibility violations:\n${report}`).toEqual(
    [],
  );
}

test.describe('@a11y accessibility', () => {
  test('the signed-out login screen', async ({ page }) => {
    await page.goto(`${APP_URL}/login`);
    await page.getByLabel(/email/i).first().waitFor();
    assertClean('login', await scan(page, 'login'));
  });

  test('the signed-in screens an architect lives in', async ({ page }) => {
    await signInThroughUi(page, DEMO_EMAIL);
    // The tour is a first-run overlay; it gets its own case below.
    await page.evaluate(() => localStorage.setItem('garh.tourDone', '1'));
    const projectId = await createProjectThroughUi(page, `A11y ${Date.now()}`);

    const screens: [string, string, string?][] = [
      ['dashboard', `${APP_URL}/`, '[data-testid="project-card"], main'],
      ['settings/practice', `${APP_URL}/settings/practice`, 'form, main'],
      ['settings/team', `${APP_URL}/settings/team`, 'main'],
      ['settings/account', `${APP_URL}/settings/account`, '[data-testid="profile-form"]'],
      ['settings/privacy', `${APP_URL}/settings/privacy`, '[data-testid="erasure-form"]'],
      ['billing', `${APP_URL}/billing`, 'main'],
      ['project/brief', `${APP_URL}/projects/${projectId}/brief`, 'section'],
      ['project/compliance', `${APP_URL}/projects/${projectId}/compliance`, 'main'],
      ['project/sheets', `${APP_URL}/projects/${projectId}/sheets`, 'main'],
      ['project/renders', `${APP_URL}/projects/${projectId}/renders`, 'main'],
    ];

    for (const [label, url, selector] of screens) {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      if (selector !== undefined) {
        await page
          .locator(selector)
          .first()
          .waitFor({ timeout: 20_000 })
          .catch(() => undefined);
      }
      await page.waitForTimeout(1_500);
      assertClean(label, await scan(page, label));
    }
  });

  test('the first-run tour, open over the project shell', async ({ page }) => {
    await signInThroughUi(page, DEMO_EMAIL);
    const projectId = await createProjectThroughUi(page, `A11y tour ${Date.now()}`);
    await page.goto(`${APP_URL}/projects/${projectId}/brief`);
    await page.evaluate(() => localStorage.removeItem('garh.tourDone'));
    await page.reload();
    await page.locator('[data-testid="tour-card"]').waitFor({ timeout: 20_000 });
    assertClean('tour', await scan(page, 'tour'));
  });
});
