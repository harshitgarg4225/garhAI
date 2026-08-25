/**
 * The Phase 9 Definition of Done, written out — mostly still skipped.
 *
 * FIRST STEP LIVE: `share link opens read-only for a client` runs for real —
 * the §13 viewer exists (`/share/:token`), so that test creates a scoped link
 * over the API, opens it in an anonymous context, and proves read-only.
 *
 *     signup → plot → brief → generate → edit → copilot → 3D → facade → render(mock)
 *            → sheets → PDF+DXF download → share link opens read-only
 *
 * ## Why this file exists now
 *
 * A target nobody has written down is a target nobody hits. Each step below is a real
 * `test.step` with the assertion it will make and the phase that unblocks it, so the last
 * phase is an exercise in deleting `test.skip` lines rather than in designing an e2e suite
 * on the day the deadline lands. It also makes the dependency order explicit: the copilot
 * step cannot be written before the solver, because it edits what the solver produced.
 *
 * ## Why it is skipped rather than absent
 *
 * `test.skip` with a reason shows up in every Playwright report as an explicit "not yet",
 * which is the honest state. A commented-out file shows up as nothing.
 *
 * ## How to turn a step on
 *
 * 1. delete the `test.skip(...)` line at the top of that test;
 * 2. replace the `expect(...).toBeTruthy()` placeholders with the real locators;
 * 3. keep the step names — they are what the nightly report reads like.
 *
 * §16: *"full happy path nightly (Phase 9 DoD scenario)"*. Wire this into a scheduled
 * workflow with `pnpm --filter @garh/e2e test:happy-path` when the first steps go green.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';
import {
  appendOps,
  createProject,
  createShareLink,
  revokeShareLink,
  signUpFirm,
  type ShareLink,
} from '../support/api';
import { APP_URL, apiBase, uniqueEmail } from '../support/env';
import { canvas, signInThroughUi, tabLink } from '../support/ui';

/**
 * The same arranged plan the copilot spec folds (plot, road, one storey, five
 * walls, two openings) — the share step below asserts that a GUEST sees this
 * geometry through the real viewer.
 */
const BASE_PLAN = JSON.parse(
  readFileSync(fileURLToPath(new URL('../fixtures/base-plan.ops.json', import.meta.url)), 'utf8'),
) as { ops: { type: string; payload: Record<string, unknown> }[] };

test.describe('@happy-path Phase 9 DoD', () => {
  test('signup → plot → brief', async ({ page, request }) => {
    test.skip(
      true,
      'Phase 2: the plot boundary editor (vertex editing, north compass, roads per edge) ' +
        'and the brief form + completeness meter do not exist yet.',
    );

    const email = uniqueEmail('happy');
    await signUpFirm(request, { email, firmName: 'Happy Path Associates' });
    await signInThroughUi(page, email);

    await test.step('create a project', () => {
      expect(page.url()).toBeTruthy();
    });

    await test.step('draw a 30x40 ft plot with a 9 m road on the south edge', () => {
      // Phase 2: rect entry + vertex editor; every length typed in ft-in, stored in mm.
      // Assert: the plot area chip reads "1,200 sq ft · 133 gaj" (§15 Indian defaults).
      expect(true).toBeTruthy();
    });

    await test.step('fill the brief: G+1, 3BHK, Vastu advisory', () => {
      // Assert: the completeness meter reaches 100%, and every AI-filled default appears
      // as an editable assumption chip with a citation (golden rule 4).
      expect(true).toBeTruthy();
    });
  });

  test('generate → options screen → apply one', async () => {
    test.skip(true, 'Phase 3: the CP-SAT solver and the options screen do not exist yet.');

    await test.step('press Generate and watch honest progress', () => {
      // §15 generation theater: staged messages driven by real worker events
      // ("Placing staircase…", "checking BBMP setbacks…"), never a fake bar.
      // Assert: at least three distinct stage messages appear, then three option cards.
      expect(true).toBeTruthy();
    });

    await test.step('three options, each passing the hard rules', () => {
      // §5.6 gates: no option is shown unless it passes. Assert: 3 cards, each with a
      // composite score ring, a compliance badge with no red chip, and a "why this plan"
      // expander listing its assumptions.
      expect(true).toBeTruthy();
    });

    await test.step('apply an option', () => {
      // Assert: the ops land as one undoable group, and the autosave badge reads "Saved".
      expect(true).toBeTruthy();
    });
  });

  test('edit on the canvas → undo → compliance chip clears', async ({ page }) => {
    test.skip(
      true,
      'Phase 3: this step edits what the SOLVER produced, so it needs a generated plan to ' +
        'edit. The Phase 4 DoD itself — draw a two-room plan, ops sync, undo/redo, a ' +
        'bye-law chip appearing and clearing — is live in `plan-canvas.spec.ts` and runs ' +
        'today under `pnpm --filter @garh/e2e test:canvas`. What is still missing here is ' +
        'the *continuity*: the same project carried from the options screen into the ' +
        'editor and out to sheets.',
    );

    await test.step('drag a bedroom wall until the room is under 9.5 m²', async () => {
      await expect(canvas(page)).toBeVisible();
      // Assert: a red chip appears reading "Bedroom 2 is 8.9m² — NBC needs 9.5m²" with the
      // clause on hover (§15 compliance chips).
      expect(true).toBeTruthy();
    });

    await test.step('Ctrl-Z restores the wall and the chip disappears', async () => {
      await page.keyboard.press('Control+z');
      // Assert: the chip is gone and the state hash matches the pre-edit hash — the §16
      // smoke assertion, and the Phase 4 DoD sentence.
      expect(true).toBeTruthy();
    });

    await test.step('dimension-first editing: click a dimension, type a value', () => {
      // §4/§15: any number on the canvas is click-to-edit and dispatches an op.
      expect(true).toBeTruthy();
    });
  });

  test('copilot: a natural-language edit becomes a reviewed diff', async () => {
    test.skip(true, 'Phase 6: the copilot and its diff preview do not exist yet.');

    await test.step('ask for "make the kitchen 300mm wider"', () => {
      // Locked decision: the LLM emits typed ops, never geometry. Assert: a diff preview
      // with before/after mini-canvases and a plain-language op list.
      expect(true).toBeTruthy();
    });

    await test.step('reject it — nothing changed', () => {
      // Golden rule 3: reject = nothing happened. Assert: the head index is unchanged.
      expect(true).toBeTruthy();
    });

    await test.step('apply it — ops appended, undoable as one group', () => {
      expect(true).toBeTruthy();
    });

    await test.step('an out-of-scope ask is refused honestly', () => {
      // §10: "can't do that yet" is a first-class answer, and it is logged.
      expect(true).toBeTruthy();
    });
  });

  test('3D → facade kit → mock render', async ({ page }) => {
    test.skip(true, 'Phases 5 and 7: the 3D scene, facade kits and render worker are later.');

    await test.step('Tab switches to 3D and the storeys are extruded', async () => {
      await tabLink(page, '3D').click();
      // §5 DoD: a plan edit reflects in 3D in under 100ms.
      expect(true).toBeTruthy();
    });

    await test.step('apply the Contemporary facade kit', () => {
      // §8: parametric geometry with per-element edit. Assert: the elevation changes and
      // the change is expressed as `facade.apply_kit`.
      expect(true).toBeTruthy();
    });

    await test.step('render with the mock provider in under a second', () => {
      // §14: mock render < 1s. Assert: the render appears, pinned to a design version.
      expect(true).toBeTruthy();
    });

    await test.step('editing the model marks the render stale', () => {
      // §9: renders carry a version id and a stale flag; the UI must say so.
      expect(true).toBeTruthy();
    });
  });

  test('sheets → PDF + DXF download', async ({ page }) => {
    test.skip(true, 'Phase 8: the auto-dimensioning and sheet engine do not exist yet.');

    await test.step('generate the six municipal sheets', async () => {
      await tabLink(page, 'Sheets').click();
      // MVP cut line: site plan, floor plans, 4 elevations, 1 section, door/window
      // schedule, area statement. §14: the whole set in under 5 minutes for a G+1 3BHK.
      expect(true).toBeTruthy();
    });

    await test.step('download the PDF set', () => {
      // Assert: a `download` event, a non-empty file, and a short-lived signed URL (§13).
      expect(true).toBeTruthy();
    });

    await test.step('download the DXF', () => {
      // Assert: the file opens clean in `ezdxf.audit()` — the golden check runs in pytest,
      // this one just proves the byte stream reaches the browser.
      expect(true).toBeTruthy();
    });
  });

  test('share link opens read-only for a client', async ({ browser, request }) => {
    // Three browser contexts and several server round trips.
    test.setTimeout(120_000);

    // Arrange, not act (see support/api): the studio UI is not what this spec
    // is about, so the firm, the project and the plan are API calls. The plan
    // is the same fixture the copilot spec folds, so "the plan renders" below
    // means real walls through the real canvas.
    const session = await signUpFirm(request, {
      email: uniqueEmail('share'),
      firmName: 'Share Path Studio',
    });
    const project = await createProject(request, session.accessToken, 'Share Path Bungalow');
    await appendOps(request, session.accessToken, project.id, BASE_PLAN.ops, -1);

    let link!: ShareLink;

    await test.step('create a scoped share link', async () => {
      // §13: 256-bit token, stored hashed, scoped {projectId, sections[], canComment},
      // with an expiry.
      link = await createShareLink(request, session.accessToken, project.id, {
        sections: ['plan'],
        canComment: true,
        expiresInDays: 7,
      });
      // The token appears exactly ONCE — on the create response…
      expect(link.token, 'the create response carries the token').toBeTruthy();
      expect(link.url).toContain(`/share/${link.token}`);
      // …and a WhatsApp deep link is offered (§15).
      expect(link.whatsappUrl).toContain('wa.me');
      // The list endpoint proves "once": the same link, with the token withheld.
      const listed = await request.get(`${apiBase()}/projects/${project.id}/share`, {
        headers: { Authorization: `Bearer ${session.accessToken}` },
      });
      const rows = (await listed.json()) as ShareLink[];
      expect(rows.some((row) => row.id === link.id && row.token === null)).toBe(true);
    });

    await test.step('an anonymous browser can view but not edit', async () => {
      const guest = await browser.newContext();
      const guestPage = await guest.newPage();
      await guestPage.goto(`${APP_URL}/share/${link.token}`);

      // The viewer header names the project and says what this surface is.
      await expect(guestPage.getByText('Share Path Bungalow')).toBeVisible({ timeout: 20_000 });
      await expect(guestPage.getByText('View only')).toBeVisible();

      // The plan renders: the same fold, the same canvas, labelled as a viewer.
      await expect(
        guestPage.getByRole('application', { name: 'Plan of Share Path Bungalow (view only)' }),
      ).toBeVisible({ timeout: 30_000 });

      // Every write surface is absent, structurally: no Generate button, no tool
      // rail, and no `data-garh-canvas` scope — that attribute is what arms the
      // studio's editing keymap, and the share page deliberately never sets it.
      await expect(guestPage.getByRole('button', { name: /generate/i })).toHaveCount(0);
      await expect(guestPage.getByRole('toolbar')).toHaveCount(0);
      await expect(guestPage.locator('[data-garh-canvas]')).toHaveCount(0);

      // What a client CAN do: send a comment through the anonymous endpoint.
      await guestPage.getByLabel('Your name').fill('Client Kumar');
      await guestPage
        .getByLabel('Your comment')
        .fill('Love the plan — can the kitchen face east?');
      await guestPage.getByRole('button', { name: 'Send' }).click();
      await expect(guestPage.getByText(/Sent — your architect/)).toBeVisible();

      await guest.close();
    });

    await test.step('revoking the link kills it immediately', async () => {
      await revokeShareLink(request, session.accessToken, link.id);
      const guest = await browser.newContext();
      const guestPage = await guest.newPage();
      await guestPage.goto(`${APP_URL}/share/${link.token}`);
      // §13: revocation is immediate — the same URL now explains itself instead
      // of rendering a stale plan.
      await expect(guestPage.getByText('This link is no longer available')).toBeVisible({
        timeout: 20_000,
      });
      await guest.close();
    });
  });
});
