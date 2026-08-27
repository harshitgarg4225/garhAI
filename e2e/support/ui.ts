/**
 * UI helpers — signing in through the real screen, and the locators the specs share.
 *
 * ## Why there are no `data-testid` attributes here
 *
 * Every locator below is a role, a label or visible text. That is not purity: it is the
 * only kind of selector that also asserts something a user cares about. `getByLabel('Work
 * email')` failing means the field lost its label — which is a real accessibility
 * regression (§15 "full keyboard operability, focus rings, WCAG AA"), not a test-only
 * annoyance. A `data-testid` would have kept passing.
 *
 * The one exception is reserved for the canvas (`[data-garh-canvas]`), which is a WebGL
 * surface with no accessible structure to select. That attribute is already part of the
 * keyboard-scoping contract in `apps/web/src/lib/keymap.ts`, so the specs borrow it rather
 * than inventing a hook.
 */

import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { APP_URL } from './env';

/** The dev-only panel that shows the OTP when no mail provider is configured. */
export function devCodePanel(page: Page): Locator {
  return page.getByText('Email sending is switched off');
}

/**
 * Sign in through the login screen with the dev OTP echo.
 *
 * Walks exactly what a first-time user walks: type the address, ask for a code, read the
 * code off the dev panel, submit it. The `OtpInput` auto-submits on the sixth digit, so
 * filling it is the whole interaction.
 */
export async function signInThroughUi(page: Page, email: string): Promise<void> {
  await page.goto(`${APP_URL}/login`);

  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Work email').fill(email);
  await page.getByRole('button', { name: 'Send me a code' }).click();

  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible();
  await expect(
    devCodePanel(page),
    [
      'The login screen did not show the dev OTP panel, so there is no way to read the code.',
      'Three things cause this, in order of likelihood:',
      `  1. ${email} does not exist — the stack is not seeded (\`make seed\`). An unknown`,
      '     address gets the same 202 with no code, on purpose (§13 non-enumerability).',
      '  2. A code was requested for this address less than 60 seconds ago and the resend',
      '     cooldown is still running. This is why the smoke project runs with retries: 0.',
      '  3. The API is not in dev mode, so the echo is off (correct outside dev).',
    ].join('\n'),
  ).toBeVisible();

  const code = (await page.locator('code').first().innerText()).trim();
  expect(code, `expected a six-digit code, got ${JSON.stringify(code)}`).toMatch(/^\d{6}$/);

  await page.getByLabel('Verification code').fill(code);

  // Landing on the dashboard is the assertion that the session took.
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible({ timeout: 15_000 });
}

/**
 * Enter the app with a session ARRANGED over the API — no OTP screen.
 *
 * For specs whose subject is not the login UI (the canvas, 3D, renders): the
 * account was just created with `signUpFirm(request, …)`, whose verify left the
 * refresh cookie in the REQUEST context's jar — and whose OTP issue also
 * started the 60s per-email resend cooldown, so `signInThroughUi` a second
 * later is answered 429 and the spec dies in its arrangement. Found on the
 * canvas specs' first execution. Copying the cookie jar into the browser
 * context lets the app's own bootstrap (`POST /auth/refresh`) mint the access
 * token — the product's real returning-user path, with zero login UI driven.
 * The login UI keeps its coverage in the @smoke suite.
 */
export async function adoptApiSession(page: Page, request: APIRequestContext): Promise<void> {
  const state = await request.storageState();
  await page.context().addCookies(state.cookies);
  await page.goto(`${APP_URL}/`);
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible({ timeout: 15_000 });
}

/** Create a project through the dashboard dialog and land on its Brief tab. */
export async function createProjectThroughUi(page: Page, name: string): Promise<string> {
  await page.getByRole('button', { name: 'New project' }).first().click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Project name').fill(name);
  await dialog.getByRole('button', { name: 'Create project' }).click();

  // The dashboard navigates to /projects/:id/brief on success.
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}\/brief$/, { timeout: 15_000 });
  const projectId = new URL(page.url()).pathname.split('/')[2];
  expect(projectId, 'could not read the project id out of the URL').toBeTruthy();
  return projectId!;
}

/** The six project tabs, as the shell renders them (§12 routing). */
export const PROJECT_TABS = ['Brief', 'Plan', '3D', 'Renders', 'Sheets', 'Compliance'] as const;

export function tabLink(page: Page, name: (typeof PROJECT_TABS)[number]): Locator {
  return page.getByRole('link', { name, exact: true });
}

/**
 * The 2D/3D canvas surface. Phase 4 owns the element; this locator exists now so the
 * skipped perf and happy-path specs are written against a real contract rather than a
 * guess made later under time pressure.
 */
export function canvas(page: Page): Locator {
  return page.locator('[data-garh-canvas]');
}

/** Assert no console error was logged. Attach with `collectConsoleErrors` first. */
export function expectNoConsoleErrors(errors: string[]): void {
  expect(errors, `console errors during the run:\n  ${errors.join('\n  ')}`).toEqual([]);
}

/**
 * Start collecting console errors and failed requests.
 *
 * A page that renders correctly while throwing on every keystroke is not a passing smoke
 * test — golden rule 9 says errors surface honestly, and a silent console error is the
 * opposite of that.
 */
export function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    // ONE allowlisted line: the boot session probe. A signed-out visitor's
    // POST /auth/refresh answers 401 BY DESIGN (the refresh credential is an
    // httpOnly cookie, so "am I signed in?" can only be answered by trying),
    // and the browser logs every failed fetch as a console error no matter
    // how gracefully the app handles it. Everything else still fails the run.
    const url = message.location().url ?? '';
    if (url.endsWith('/auth/refresh') && /\b401\b/.test(message.text())) return;
    errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

// ---------------------------------------------------------------------------
// Driving the Phase-4 canvas
// ---------------------------------------------------------------------------

/**
 * Why the canvas is driven by TYPED LENGTHS and not by pixel arithmetic.
 *
 * A spec that clicks at (x, y) and expects a 3600 mm wall has to know the
 * camera's `mmPerPx`, which depends on the viewport size, the device pixel
 * ratio and whatever the "fit on open" pass decided. That is a spec that goes
 * red when somebody changes a padding constant.
 *
 * §12 gives a better door: *"typing a number overrides the mouse (type exact
 * lengths while drawing)"*. So the helpers below move the pointer only far
 * enough to choose a DIRECTION — which the ortho constraint snaps to one of
 * four — and then type the length and press Enter. The resulting geometry is
 * exact in millimetres at any zoom, and the assertions can be about numbers
 * rather than about pixels.
 *
 * It also means these helpers exercise the §12 requirement itself: if numeric
 * entry breaks, every one of them fails.
 */

/** Compass directions as they appear ON SCREEN (+Y model is up, +Y screen is down). */
export type Leg = 'right' | 'up' | 'left' | 'down';

const LEG_VECTOR: Readonly<Record<Leg, { dx: number; dy: number }>> = {
  right: { dx: 1, dy: 0 },
  left: { dx: -1, dy: 0 },
  // Screen Y grows downward; model +Y is up. "up" on screen is -Y in pixels.
  up: { dx: 0, dy: -1 },
  down: { dx: 0, dy: 1 },
};

/** How far the pointer moves to declare a direction. Well past the drag slop. */
const DIRECTION_NUDGE_PX = 80;

export interface CanvasBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The canvas's bounding box, or a loud failure if it never mounted. */
export async function canvasBox(page: Page): Promise<CanvasBox> {
  const surface = canvas(page);
  await expect(surface, 'the 2D canvas surface never mounted').toBeVisible();
  const box = await surface.boundingBox();
  expect(box, 'the canvas has no layout box — it is probably 0×0').not.toBeNull();
  return box as CanvasBox;
}

/** Click the canvas so it owns the keyboard (Tab and Enter are canvas-scoped). */
export async function focusCanvas(page: Page): Promise<void> {
  const box = await canvasBox(page);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

/**
 * Draw one closed wall chain with exact lengths.
 *
 * `legs` are walked in order from `startPx`; each is a direction plus a length
 * in millimetres. The chain is ended with Enter, so the whole rectangle is ONE
 * op group and therefore ONE undo — which is what the DoD's undo step asserts.
 *
 * THE POINTER MUST TRACK THE ANCHOR. When Enter places a typed 6900 mm
 * segment, the chain's real anchor jumps 6900 mm — while a pointer that only
 * nudged 80 px is now metres BEHIND it. The wall tool derives the next leg's
 * ortho direction from `pointer − anchor` (`wallTool.resolve`), so a stale
 * pointer makes every subsequent leg point back along the previous one; the
 * legs overlap collinearly and `validateCommit` rightly refuses the whole
 * chain — zero ops reach the server and nothing on screen says why to a
 * headless run. (Found executed: the first real run of `plan-canvas.spec.ts`
 * committed nothing.) So when the caller knows `mmPerPx` — and after
 * `calibrate()` it always does — each leg advances the pointer by its true
 * pixel length; the typed value still makes the geometry exact. The 80 px
 * nudge remains only for callers without a scale, and is only safe for
 * SINGLE-leg chains.
 */
export async function drawWallChain(
  page: Page,
  startPx: { x: number; y: number },
  legs: readonly { dir: Leg; lengthMm: number }[],
  opts: { mmPerPx?: number } = {},
): Promise<void> {
  await page.keyboard.press('w');
  await page.mouse.move(startPx.x, startPx.y);
  await page.mouse.click(startPx.x, startPx.y);

  let cursor = { ...startPx };
  for (const leg of legs) {
    const vector = LEG_VECTOR[leg.dir];
    const advancePx = opts.mmPerPx === undefined ? DIRECTION_NUDGE_PX : leg.lengthMm / opts.mmPerPx;
    cursor = {
      x: cursor.x + vector.dx * advancePx,
      y: cursor.y + vector.dy * advancePx,
    };
    // Two moves: the first wakes the tool out of `idle`, the second is the one
    // the ortho constraint reads. One move can be coalesced away by the rAF
    // batching in `useCanvasControls`.
    await page.mouse.move(cursor.x, cursor.y, { steps: 2 });
    await page.keyboard.type(String(leg.lengthMm), { delay: 15 });
    await page.keyboard.press('Enter');
  }

  // Enter on an empty buffer ends the chain and dispatches the group.
  await page.keyboard.press('Enter');
}

/** The autosave badge's text — "Saved · v14", "Saving…", "Offline". */
export function saveBadge(page: Page): Locator {
  return page
    .getByRole('status')
    .filter({ hasText: /Saved|Saving|Offline|couldn/i })
    .first();
}

/** Wait until the op queue has drained and the badge says so. */
export async function waitForSaved(page: Page, timeout = 20_000): Promise<void> {
  await expect(page.getByText(/^Saved/).first(), 'the op queue never drained').toBeVisible({
    timeout,
  });
}

/** The bottom compliance strip. */
export function complianceStrip(page: Page): Locator {
  return page.getByRole('region', { name: 'Compliance' });
}

/** The right-hand properties panel. */
export function inspector(page: Page): Locator {
  return page.getByRole('complementary', { name: 'Properties' });
}

/** A storey tab in the top bar ("Ground", "First", …). */
export function storeyTab(page: Page, label: string): Locator {
  return page.getByRole('radio', { name: label, exact: true });
}

/* ────────────────────────────────────────────────────────────────────────────
 * Phase 5 — the 3D view
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * The §15 status chip in the 3D view. Its `data-garh-*` attributes are the
 * spec's honest probes for the §14 rebuild budget and the sun-scrub
 * "no geometry rebuild" invariant — a written contract with
 * `pages/project/three/ThreeDControls.tsx`, not a guessed selector.
 */
export function statusChip3d(page: Page): Locator {
  return page.locator('[data-garh-3d-status]');
}

/**
 * Focus the canvas surface WITHOUT a click. A focusing CLICK would have side
 * effects that depend on what happens to be docked under the pointer (the
 * facade panel sits top-left in 3D, the tool options bar in 2D) or under the
 * drawing (a 3D click is a selection). Needed because Tab (view.toggle) is
 * canvas-scoped and pressing a panel button moves focus out.
 *
 * The focus target is the `role="application"` surface INSIDE the marker, not
 * `[data-garh-canvas]` itself: the marker is an ancestor div the page mounts
 * for `closest()`-based scoping and carries no `tabIndex`, so `.focus()` on it
 * silently does nothing — executed proof: three-d.spec's Tab fell through to
 * the browser's focus traversal and landed on the skip link. `CanvasRoot`'s
 * application div is the element that owns the keyboard (`tabIndex={0}`), and
 * focus inside it is what makes canvas-scoped bindings live.
 */
export async function focusCanvasKeyboard(page: Page): Promise<void> {
  await canvas(page).getByRole('application').focus();
}

/**
 * A REAL click on non-content space in the 3D view — high on the right, below
 * the nav HUD, above the status chip, outside every docked panel. Sky or
 * ground either way (the ground registers a null pick-resolver on purpose),
 * so the one thing it can do is clear the selection.
 */
export async function clickEmpty3d(page: Page): Promise<void> {
  const box = await canvasBox(page);
  await page.mouse.click(box.x + box.width * 0.93, box.y + box.height * 0.42);
}

/** Press Tab (the §12 view toggle) and wait for the layer set to swap. */
export async function toggleViewWithTab(page: Page, expected: 'plan' | '3d'): Promise<void> {
  await focusCanvasKeyboard(page);
  await page.keyboard.press('Tab');
  await expect(
    page.locator(`[data-garh-canvas="${expected}"]`),
    `Tab should have swapped the canvas to the ${expected} layer set, in place`,
  ).toBeVisible({ timeout: 10_000 });
}

/**
 * The dev-build test handle (`apps/web/src/lib/testHooks.ts`). Read that
 * header before adding a call site: it is an ARRANGEMENT tool (programmatic
 * selection — the one thing a perspective view gives pixels no honest path
 * to) plus read-only probes. Ops must still be produced through the real UI.
 */
export interface HooksSnapshot {
  readonly selectedIds: readonly string[];
  readonly viewMode: '2d' | '3d';
  readonly headIdx: number;
  readonly pendingCount: number;
  readonly visibleStoreyId: string | null;
  readonly facadeKitId: string | null;
  readonly facadeSeed: number;
  readonly facadeComponentCount: number;
  readonly rebuildCount: number;
  readonly lastRebuildMs: number | null;
  readonly engineStatus: string;
  /* ── Phase 6 (copilot). Read-only; the spec still types, clicks and reads
     the real panel — these exist because "one undo group, this group id" is
     not a claim the DOM can make. ─────────────────────────────────────────── */
  readonly copilotTurns: number;
  readonly copilotLastStatus: string | null;
  readonly copilotLastGroupId: string | null;
  readonly copilotLastOpCount: number;
  readonly undoDepth: number;
}

interface HooksWindow {
  __garhTestHooks?: {
    select: (ids: readonly string[]) => void;
    snapshot: () => HooksSnapshot;
  };
}

export async function hooksSnapshot(page: Page): Promise<HooksSnapshot> {
  const snapshot = await page.evaluate(() => {
    const hooks = (window as unknown as HooksWindow).__garhTestHooks;
    return hooks === undefined ? null : hooks.snapshot();
  });
  expect(
    snapshot,
    'window.__garhTestHooks is missing — the app is not a dev build, or the editor page never mounted',
  ).not.toBeNull();
  return snapshot!;
}

/* ────────────────────────────────────────────────────────────────────────────
 * Phase 6 — the copilot rail
 * ──────────────────────────────────────────────────────────────────────────── */

/** The docked chat rail. `aria-label="Copilot"` on the `<aside>`. */
export function copilotPanel(page: Page): Locator {
  return page.getByRole('complementary', { name: 'Copilot' });
}

/**
 * Open the rail with the `/` shortcut and confirm the caret landed in the
 * command box.
 *
 * The shortcut is the thing under test, not a convenience: §12 lists it, and
 * `lib/keymap.ts`'s `isTypingTarget` guard means a `/` typed into a field must
 * NOT do this — which is why the spec presses it from the canvas, not from an
 * input. Focus is asserted rather than assumed because the panel is a lazy
 * chunk and "opened but did not focus" is the exact regression this catches.
 */
export async function openCopilotWithSlash(page: Page): Promise<Locator> {
  await focusCanvasKeyboard(page);
  await page.keyboard.press('/');

  const panel = copilotPanel(page);
  await expect(panel, 'pressing "/" did not open the copilot rail').toBeVisible({
    timeout: 15_000,
  });

  const box = copilotInput(page);
  await expect(box, 'the copilot rail opened but its command box never appeared').toBeVisible();
  await expect(box, 'the "/" shortcut opened the rail without focusing the input').toBeFocused({
    timeout: 5_000,
  });
  return panel;
}

/** The command box inside the rail. */
export function copilotInput(page: Page): Locator {
  return copilotPanel(page).getByRole('textbox').first();
}

/** Type a command and send it with Enter (Shift+Enter is a newline). */
export async function sendCopilotCommand(page: Page, command: string): Promise<void> {
  const box = copilotInput(page);
  await box.click();
  await box.fill(command);
  await page.keyboard.press('Enter');
}

/* ────────────────────────────────────────────────────────────────────────────
 * Phase 7 — renders
 * ──────────────────────────────────────────────────────────────────────────── */

/** The §9 stale banner on a render card ("Design changed since this render"). */
export function staleBanner(page: Page): Locator {
  return page.getByText(/design changed since this render/i);
}

/** Programmatic selection — the same store write a real pick performs. */
export async function selectViaHooks(page: Page, ids: readonly string[]): Promise<void> {
  const ok = await page.evaluate((wanted) => {
    const hooks = (window as unknown as HooksWindow).__garhTestHooks;
    if (hooks === undefined) return false;
    hooks.select(wanted);
    return true;
  }, ids as string[]);
  expect(ok, 'window.__garhTestHooks is missing — see hooksSnapshot').toBe(true);
}
