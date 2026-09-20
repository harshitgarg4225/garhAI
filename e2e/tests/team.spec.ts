/**
 * J01's other half, in a browser: an architect invites a colleague and the
 * colleague turns up inside the same practice.
 *
 * WHY THIS FILE EXISTS. Invites, roles, seats and the pre-auth invite screen are
 * thoroughly tested on the API (375 pytest cases) and in vitest against mocked
 * fetches. What had never been executed is the join: an admin typing an address
 * into Settings → Team, the invite email's link opening the login screen in a
 * DIFFERENT browser context, that person signing in with an address that has no
 * account of its own, and landing inside the inviter's firm as an editor rather
 * than as the founder of a new one.
 *
 * That join is where this kind of feature goes wrong, and it is the one thing no
 * unit test can assert, because every fixture already agrees about who is in
 * which firm. The negative control is in the same test: before accepting, the
 * invitee's address must NOT be able to see the practice's projects.
 *
 * Two browser contexts, not two pages: an invite accepted in the same context as
 * the admin's session would prove nothing about the session the invitee gets.
 */

import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test';

import { signUpFirm } from '../support/api';
import { APP_URL, uniqueEmail } from '../support/env';
import { adoptApiSession, devCodePanel, skipFirstRunTour } from '../support/ui';

/** `/login?invite=<token>` is the link in the email; the page asks the API about it. */
function inviteUrl(token: string): string {
  return `${APP_URL}/login?invite=${encodeURIComponent(token)}`;
}

/**
 * Sign in on the login screen using the dev OTP echo.
 *
 * Deliberately not `signInThroughUi`: that helper asserts it lands on "Projects",
 * which is right for a returning user and wrong here — the whole question is
 * whether an invitee lands anywhere at all.
 */
async function signInWithCode(page: Page, email: string): Promise<void> {
  await page.getByLabel('Work email').fill(email);
  await page.getByRole('button', { name: /send me a code/i }).click();
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible({
    timeout: 20_000,
  });
  const code = (await page.locator('code').first().innerText()).trim();
  expect(code, `expected a six-digit dev code, got ${JSON.stringify(code)}`).toMatch(/^\d{6}$/);
  await page.getByLabel('Verification code').fill(code);
}

async function freshContextPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await skipFirstRunTour(page);
  return page;
}

test.describe('@team an admin invites a colleague into the practice', () => {
  test.setTimeout(180_000);

  test('invite → the colleague signs in → they are in the firm, as an editor', async ({
    browser,
    request,
  }) => {
    const adminEmail = uniqueEmail('admin');
    const colleagueEmail = uniqueEmail('colleague');

    const session = await signUpFirm(request, {
      email: adminEmail,
      firmName: 'Invite Test Associates',
    });

    const admin = await freshContextPage(browser);
    await adoptApiSession(admin, request);

    let token = '';

    await test.step('the admin invites them from Settings → Team', async () => {
      await admin.goto(`${APP_URL}/settings/team`);
      const form = admin.locator('[data-testid="invite-form"]');
      await expect(
        form,
        'the founder of a firm is its admin and must see the invite form',
      ).toBeVisible({ timeout: 20_000 });

      await form.getByLabel('Name').fill('Rahul Verma');
      await form.getByLabel('Work email').fill(colleagueEmail);

      // The token is minted server-side and shown once; read it off the request
      // the form makes rather than out of an email nobody is reading here.
      const [response] = await Promise.all([
        admin.waitForResponse(
          (r) => r.url().includes('/firm/invites') && r.request().method() === 'POST',
        ),
        form
          .getByRole('button', { name: /invite|send/i })
          .first()
          .click(),
      ]);
      expect(response.status(), await response.text()).toBe(201);
      // `url` is present exactly once, on create and resend (schemas/team.py), and
      // carries the same link the email does. Reading it here is the only way to
      // follow the link without a mailbox.
      const body = (await response.json()) as { url?: string | null };
      const url = body.url ?? '';
      expect(url, `the 201 carried no invite url: ${JSON.stringify(body)}`).not.toBe('');
      token = new URL(url, APP_URL).searchParams.get('invite') ?? '';
      expect(token, `the invite url has no ?invite= token: ${url}`).not.toBe('');

      await expect(
        admin.locator('[data-testid="invite-row"]').filter({ hasText: colleagueEmail }),
      ).toBeVisible({ timeout: 20_000 });
    });

    await test.step('NEGATIVE CONTROL: before accepting, they are nobody here', async () => {
      // An invite that has not been accepted must not already be a membership.
      const listed = await listMemberEmails(request, session.accessToken);
      expect(
        listed,
        'the invitee is a MEMBER before accepting anything — an invite is being treated ' +
          'as a join, which would let anyone be added to a firm without consent',
      ).not.toContain(colleagueEmail);
    });

    const colleague = await freshContextPage(browser);

    await test.step('they open the link and sign in with that address', async () => {
      await colleague.goto(inviteUrl(token));
      // The pre-auth screen says whose practice this is before asking for anything.
      await expect(
        colleague.getByText(/Invite Test Associates/i).first(),
        'the invite screen should name the practice doing the inviting',
      ).toBeVisible({ timeout: 20_000 });

      await signInWithCode(colleague, colleagueEmail);
      await expect(
        colleague.getByRole('heading', { name: 'Projects', exact: true }),
        'accepting an invite should land the colleague in the app, not on a dead end',
      ).toBeVisible({ timeout: 20_000 });
    });

    await test.step('they are inside the SAME practice, as an editor', async () => {
      await colleague.goto(`${APP_URL}/settings/team`);
      const row = colleague.locator('[data-testid="member-row"]').filter({ hasText: adminEmail });
      await expect(
        row,
        'the colleague cannot see the admin in the member list, so they joined a ' +
          'different firm — the invite minted a new practice instead of a membership',
      ).toBeVisible({ timeout: 20_000 });

      const mine = colleague.locator('[data-testid="member-row"]').filter({
        hasText: colleagueEmail,
      });
      await expect(mine).toBeVisible();
      await expect(mine, 'an invited colleague is an editor, not an admin').toContainText(
        /editor/i,
      );

      // ...and the invite form is not theirs to use.
      await expect(
        colleague.locator('[data-testid="invite-form"]'),
        'a non-admin was shown the invite form',
      ).toHaveCount(0);
    });

    await test.step('the admin now sees them as a member, and the open invite is gone', async () => {
      await admin.goto(`${APP_URL}/settings/team`);
      await expect(
        admin.locator('[data-testid="member-row"]').filter({ hasText: colleagueEmail }),
      ).toBeVisible({ timeout: 20_000 });
      await expect(
        admin.locator('[data-testid="invite-row"]').filter({ hasText: colleagueEmail }),
      ).toHaveCount(0);
    });

    await admin.context().close();
    await colleague.context().close();
  });

  /*
   * THE CONTROL THAT MAKES THE TEST ABOVE MEAN SOMETHING.
   *
   * Everything that test asserts — lands on Projects, sees a member list, sees itself
   * — would ALSO be true of someone who had simply signed up and founded a firm of
   * one. So it has to be shown that the invite is what let them in.
   *
   * The proof turns out to be stronger than "they end up somewhere else": an address
   * nobody has invited and that has no practice of its own cannot sign in AT ALL. The
   * screen still says "Check your email", because answering differently would turn
   * the login form into an oracle for which addresses have accounts (§13), but no
   * code is sent — so the dev echo panel, which shows every code this stack issues,
   * stays empty. The login copy says so in as many words.
   *
   * Which means the colleague in the test above could not have got in without the
   * link. That is the claim, and this is what backs it.
   */
  test('NEGATIVE CONTROL: an address nobody invited cannot sign in at all', async ({
    browser,
    request,
  }) => {
    const adminEmail = uniqueEmail('admin-nc');
    const strangerEmail = uniqueEmail('stranger');
    await signUpFirm(request, { email: adminEmail, firmName: 'Control Associates' });

    const stranger = await freshContextPage(browser);
    await stranger.goto(`${APP_URL}/login`);
    await stranger.getByLabel('Work email').fill(strangerEmail);
    await stranger.getByRole('button', { name: /send me a code/i }).click();

    // The same 202 screen an invited address gets. Deliberately indistinguishable.
    await expect(stranger.getByRole('heading', { name: 'Check your email' })).toBeVisible({
      timeout: 20_000,
    });

    // ...but nothing was actually sent.
    await expect(
      devCodePanel(stranger),
      'a code was issued to an address with no account and no invite. Sign-in is ' +
        'minting practices for strangers, and the invite link in the test above is ' +
        'not what put the colleague in the firm.',
    ).toHaveCount(0);
    await expect(
      stranger.getByText(/only works for practices that already have an account/i),
      'the login screen no longer explains why no code arrived, which is the one ' +
        'thing standing between this behaviour and a bug report',
    ).toBeVisible();

    await stranger.context().close();
  });
});

/** Member addresses as the API reports them, through the same route the UI calls. */
async function listMemberEmails(request: APIRequestContext, token: string): Promise<string[]> {
  const base = process.env.API_URL ?? 'http://localhost:8000';
  const response = await request.get(`${base}/api/v1/firm/members`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok(), `GET /firm/members: ${response.status()}`).toBe(true);
  const body = (await response.json()) as { items?: { email?: string }[] };
  return (body.items ?? []).map((m) => String(m.email ?? ''));
}
