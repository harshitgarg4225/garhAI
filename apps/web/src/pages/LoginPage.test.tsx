/**
 * LoginPage against the server's error contract — every branch it takes, and the
 * two it must NOT take: a "tries left" count the server refuses to reveal, and a
 * mobile field that was collected and silently discarded.
 *
 * The session store and the API client are mocked at the module boundary; the
 * UI kit is real (the OtpInput is the actual control), so what is asserted is
 * what an architect would see.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppError, ERROR_CODES } from '../lib/errors';

const { store, inviteStatus } = vi.hoisted(() => ({
  store: {
    requestOtp: vi.fn(),
    verifyOtp: vi.fn(),
    signUp: vi.fn(),
    completeTwoFactor: vi.fn(),
  },
  inviteStatus: vi.fn(),
}));

vi.mock('../stores/session', () => ({
  useSessionStore: (selector: (s: typeof store) => unknown) => selector(store),
}));
vi.mock('../lib/api', () => ({ api: { auth: { inviteStatus } } }));

import { LoginPage, classifyVerifyError, describeInvite } from './LoginPage';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  store.requestOtp.mockReset();
  store.verifyOtp.mockReset();
  store.signUp.mockReset();
  store.completeTwoFactor.mockReset();
  inviteStatus.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

function problem(
  code: string,
  extra: Partial<ConstructorParameters<typeof AppError>[0]> = {},
): AppError {
  return new AppError({ code, message: `server said ${code}`, action: 'do the thing', ...extra });
}

function input(label: string): HTMLInputElement {
  const el = Array.from(container.querySelectorAll('label')).find((l) =>
    (l.textContent ?? '').startsWith(label),
  );
  if (el === undefined) throw new Error(`no label ${label}`);
  const target = document.getElementById(el.htmlFor) as HTMLInputElement | null;
  if (target === null) throw new Error(`no control for ${label}`);
  return target;
}

function type(el: HTMLInputElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function button(text: string): HTMLButtonElement {
  const el = Array.from(container.querySelectorAll('button')).find(
    (b) => (b.textContent ?? '').trim() === text,
  );
  if (el === undefined) throw new Error(`no button ${text}`);
  return el;
}

function click(el: HTMLElement): void {
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function requestCode(
  onSignedIn = vi.fn(),
  inviteToken?: string,
): Promise<ReturnType<typeof vi.fn>> {
  act(() => root.render(<LoginPage onSignedIn={onSignedIn} inviteToken={inviteToken} />));
  type(input('Work email'), 'asha@studio.in');
  click(button('Send me a code'));
  await flush();
  return onSignedIn;
}

describe('LoginPage — the code step', () => {
  it('collects only an email: the discarded mobile field is gone', () => {
    act(() => root.render(<LoginPage />));
    expect(container.textContent).not.toContain('Mobile');
    expect(container.querySelector('input[type="tel"]')).toBeNull();
  });

  it('shows the dev code box and counts the resend button down from the server value', async () => {
    store.requestOtp.mockResolvedValue({
      expiresInSeconds: 600,
      resendAfterSeconds: 60,
      devCode: '123456',
    });
    await requestCode();
    expect(store.requestOtp).toHaveBeenCalledWith('asha@studio.in');
    expect(container.textContent).toContain('123456');
    expect(container.textContent).toContain('never shown in staging or production');
    const resend = button('Send again in 60s');
    expect(resend.disabled).toBe(true);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(button('Send a fresh code').disabled).toBe(false);
  });

  it('renders no dev-code box when the code went by email', async () => {
    store.requestOtp.mockResolvedValue({ expiresInSeconds: 600, resendAfterSeconds: 60 });
    await requestCode();
    expect(container.textContent).not.toContain('Email sending is switched off');
  });

  it('says a wrong code did not work, without inventing a tries-left count', async () => {
    store.requestOtp.mockResolvedValue({ expiresInSeconds: 600, resendAfterSeconds: 60 });
    store.verifyOtp.mockRejectedValue(problem(ERROR_CODES.otpInvalid, { status: 400 }));
    const onSignedIn = await requestCode();
    type(input('Verification code'), '999999');
    await flush();
    expect(store.verifyOtp).toHaveBeenCalledWith('asha@studio.in', '999999');
    expect(container.textContent).toContain("That code didn't work");
    expect(container.textContent).not.toMatch(/tries left|try left/);
    expect(onSignedIn).not.toHaveBeenCalled();
    // The control is not locked: the server owns the attempt cap, not this page.
    expect(input('Verification code').disabled).toBe(false);
  });

  it('adopts the server Retry-After when a resend is rate-limited', async () => {
    store.requestOtp
      .mockResolvedValueOnce({ expiresInSeconds: 600, resendAfterSeconds: 60 })
      .mockRejectedValueOnce(
        problem(ERROR_CODES.otpRateLimited, { status: 429, retryAfterSeconds: 42 }),
      );
    await requestCode();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    click(button('Send a fresh code'));
    await flush();
    expect(container.textContent).toContain('Send again in 42s');
    expect(container.textContent).toContain('42 seconds');
  });

  it('moves to the second-factor step on two_factor_required and finishes there', async () => {
    store.requestOtp.mockResolvedValue({ expiresInSeconds: 600, resendAfterSeconds: 60 });
    store.verifyOtp.mockRejectedValue(
      problem(ERROR_CODES.twoFactorRequired, {
        status: 403,
        data: { challenge: 'chal.lenge.token', expiresInSeconds: 300 },
      }),
    );
    store.completeTwoFactor.mockResolvedValue(undefined);
    const onSignedIn = await requestCode();
    type(input('Verification code'), '123456');
    await flush();
    expect(container.textContent).toContain('One more step');
    type(input('Authenticator code'), '654321');
    click(button('Sign in'));
    await flush();
    expect(store.completeTwoFactor).toHaveBeenCalledWith('chal.lenge.token', '654321');
    expect(onSignedIn).toHaveBeenCalledTimes(1);
  });

  it('sends a proven-but-accountless address to create a practice', async () => {
    store.requestOtp.mockResolvedValue({ expiresInSeconds: 600, resendAfterSeconds: 60 });
    store.verifyOtp.mockRejectedValue(problem(ERROR_CODES.accountUnknown, { status: 404 }));
    await requestCode();
    type(input('Verification code'), '123456');
    await flush();
    expect(container.textContent).toContain('Create your practice');
    expect(container.textContent).toContain('no practice behind it');
  });

  it('calls onSignedIn once the code is accepted', async () => {
    store.requestOtp.mockResolvedValue({ expiresInSeconds: 600, resendAfterSeconds: 60 });
    store.verifyOtp.mockResolvedValue(undefined);
    const onSignedIn = await requestCode();
    type(input('Verification code'), '123456');
    await flush();
    expect(onSignedIn).toHaveBeenCalledTimes(1);
  });
});

describe('LoginPage — creating a practice', () => {
  it('bounces a taken address back to sign-in with a plain sentence', async () => {
    store.signUp.mockRejectedValue(problem(ERROR_CODES.emailAlreadyRegistered, { status: 409 }));
    act(() => root.render(<LoginPage />));
    click(button('Create an account'));
    type(input('Practice name'), 'Studio One');
    type(input('Your name'), 'Asha Rao');
    type(input('Work email'), 'asha@studio.in');
    click(button('Create account'));
    await flush();
    expect(container.textContent).toContain('Sign in');
    expect(container.textContent).toContain('already has an account');
    expect(container.textContent).not.toContain('Practice name');
  });
});

describe('LoginPage — opened from an invite link', () => {
  it('names the practice, pre-fills the address, and leaves sign-in as the way in', async () => {
    inviteStatus.mockResolvedValue({
      status: 'pending',
      firmName: 'Studio One',
      invitedByName: 'Asha Rao',
      role: 'member',
      email: 'rahul@studio.in',
      expiresAt: '2026-09-14T00:00:00Z',
    });
    act(() => root.render(<LoginPage inviteToken="tok_abc" />));
    await flush();
    expect(inviteStatus).toHaveBeenCalledWith('tok_abc');
    const banner = container.querySelector('[data-testid="invite-banner"]');
    expect(banner?.getAttribute('data-status')).toBe('pending');
    expect(banner?.textContent).toContain('Asha Rao invited you to join Studio One');
    expect(input('Work email').value).toBe('rahul@studio.in');
    expect(button('Send me a code')).toBeDefined();
  });

  it('says honestly when the link has expired', async () => {
    inviteStatus.mockResolvedValue({
      status: 'expired',
      firmName: 'Studio One',
      invitedByName: 'Asha Rao',
      role: 'member',
      email: 'rahul@studio.in',
      expiresAt: '2026-09-01T00:00:00Z',
    });
    act(() => root.render(<LoginPage inviteToken="tok_old" />));
    await flush();
    expect(container.querySelector('[data-testid="invite-banner"]')?.textContent).toContain(
      'This invite has expired',
    );
  });

  it('says a token that matches nothing is not valid', async () => {
    inviteStatus.mockRejectedValue(problem(ERROR_CODES.inviteInvalid, { status: 404 }));
    act(() => root.render(<LoginPage inviteToken="tok_nope" />));
    await flush();
    const banner = container.querySelector('[data-testid="invite-banner"]');
    expect(banner?.getAttribute('data-status')).toBe('invalid');
    expect(banner?.textContent).toContain("isn't valid");
  });

  it('never asks the API without a token', () => {
    act(() => root.render(<LoginPage />));
    expect(inviteStatus).not.toHaveBeenCalled();
  });
});

describe('the pure helpers', () => {
  it('classifies every verify code the server can answer with', () => {
    expect(classifyVerifyError(problem(ERROR_CODES.otpInvalid)).kind).toBe('otp');
    expect(classifyVerifyError(problem(ERROR_CODES.accountUnknown)).kind).toBe('account_unknown');
    expect(
      classifyVerifyError(problem(ERROR_CODES.twoFactorRequired, { data: { challenge: 'c' } })),
    ).toEqual({ kind: 'two_factor', challenge: 'c' });
    // A two_factor_required with no challenge is a malformed answer, not a step.
    expect(classifyVerifyError(problem(ERROR_CODES.twoFactorRequired)).kind).toBe('other');
    expect(classifyVerifyError(problem(ERROR_CODES.serviceUnavailable)).kind).toBe('other');
  });

  it('describes each invite status in words a colleague can act on', () => {
    const base = {
      firmName: 'Studio One',
      invitedByName: 'Asha Rao',
      role: 'admin',
      email: 'r@x.in',
      expiresAt: '2026-09-14T00:00:00Z',
    };
    expect(describeInvite({ ...base, status: 'pending' }).detail).toContain('an admin');
    expect(describeInvite({ ...base, status: 'revoked' }).title).toContain('withdrawn');
    expect(describeInvite({ ...base, status: 'accepted' }).title).toContain('already been used');
    expect(describeInvite({ ...base, invitedByName: null, status: 'expired' }).detail).toContain(
      'Ask Studio One',
    );
  });
});
