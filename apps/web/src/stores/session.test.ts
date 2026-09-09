/**
 * The session store's contract with the login page and the app shell.
 *
 * `lib/api` is mocked at the module boundary; `lib/tokens` and `lib/http` are
 * real, so "adopting a session" is asserted on the actual token store and
 * "signing out" on its actual clearing — the two things the login smoke cannot
 * see from the outside.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppError, ERROR_CODES } from '../lib/errors';
import { tokenStore } from '../lib/tokens';

const auth = vi.hoisted(() => ({
  requestOtp: vi.fn(),
  verifyOtp: vi.fn(),
  signup: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
  verifySecondFactor: vi.fn(),
  updateProfile: vi.fn(),
}));
vi.mock('../lib/api', () => ({ api: { auth } }));

import { useSessionStore } from './session';

const SESSION = {
  accessToken: 'access.jwt',
  expiresIn: 900,
  refreshToken: null,
  user: { id: 'u1', email: 'asha@studio.in', name: 'Asha Rao', role: 'admin', coaNumber: null },
  firm: { id: 'f1', name: 'Studio One', logoUrl: null, settings: {} },
};

function fail(code: string): AppError {
  return new AppError({ code, message: 'no', action: 'retry', status: 400 });
}

beforeEach(() => {
  for (const fn of Object.values(auth)) fn.mockReset();
  tokenStore.clear();
  useSessionStore.setState({
    status: 'unknown',
    user: null,
    firm: null,
    otp: null,
    share: null,
    error: null,
    busy: false,
  });
});

describe('requestOtp', () => {
  it('normalises the address, records the challenge, and hides an absent dev code', async () => {
    auth.requestOtp.mockResolvedValue({
      expiresInSeconds: 600,
      resendAfterSeconds: 60,
      devCode: null,
    });
    const result = await useSessionStore.getState().requestOtp('  Asha@Studio.IN ');
    expect(auth.requestOtp).toHaveBeenCalledWith({ email: 'asha@studio.in' });
    expect(result).toEqual({ expiresInSeconds: 600, resendAfterSeconds: 60 });
    expect('devCode' in result).toBe(false);
    expect(useSessionStore.getState().otp?.email).toBe('asha@studio.in');
  });

  it('rejects with the AppError and keeps it on the store', async () => {
    auth.requestOtp.mockRejectedValue(fail(ERROR_CODES.otpRateLimited));
    await expect(useSessionStore.getState().requestOtp('a@b.in')).rejects.toMatchObject({
      code: ERROR_CODES.otpRateLimited,
    });
    expect(useSessionStore.getState().error?.code).toBe(ERROR_CODES.otpRateLimited);
    expect(useSessionStore.getState().busy).toBe(false);
  });
});

describe('verifyOtp and completeTwoFactor', () => {
  it('adopts the session: tokens stored, identity set, challenge cleared', async () => {
    auth.verifyOtp.mockResolvedValue(SESSION);
    useSessionStore.setState({
      otp: { email: 'a', sentAt: 0, resendAfterSeconds: 60, expiresInSeconds: 600, devCode: null },
    });
    await useSessionStore.getState().verifyOtp('asha@studio.in', '123456');
    expect(auth.verifyOtp).toHaveBeenCalledWith({ email: 'asha@studio.in', code: '123456' });
    const state = useSessionStore.getState();
    expect(state.status).toBe('authenticated');
    expect(state.user?.name).toBe('Asha Rao');
    expect(state.firm?.name).toBe('Studio One');
    expect(state.otp).toBeNull();
    expect(tokenStore.current?.accessToken).toBe('access.jwt');
  });

  it('surfaces two_factor_required without adopting anything', async () => {
    auth.verifyOtp.mockRejectedValue(fail(ERROR_CODES.twoFactorRequired));
    await expect(useSessionStore.getState().verifyOtp('a@b.in', '1')).rejects.toMatchObject({
      code: ERROR_CODES.twoFactorRequired,
    });
    expect(useSessionStore.getState().status).toBe('unknown');
    expect(tokenStore.current).toBeNull();
  });

  it('completeTwoFactor posts the challenge and the code, then adopts the session', async () => {
    auth.verifySecondFactor.mockResolvedValue(SESSION);
    await useSessionStore.getState().completeTwoFactor('chal', '654321');
    expect(auth.verifySecondFactor).toHaveBeenCalledWith({ challenge: 'chal', code: '654321' });
    expect(useSessionStore.getState().status).toBe('authenticated');
    expect(tokenStore.current?.accessToken).toBe('access.jwt');
  });
});

describe('signOut', () => {
  it('clears locally FIRST, then hits /auth/logout — even when the network fails', async () => {
    auth.verifyOtp.mockResolvedValue(SESSION);
    await useSessionStore.getState().verifyOtp('a@b.in', '1');
    auth.logout.mockRejectedValue(new Error('offline'));
    await useSessionStore.getState().signOut();
    expect(auth.logout).toHaveBeenCalledWith({ everywhere: false });
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().user).toBeNull();
    expect(tokenStore.current).toBeNull();
  });

  it('signs out everywhere through the logout-all route, not a flag on logout', async () => {
    auth.verifyOtp.mockResolvedValue(SESSION);
    await useSessionStore.getState().verifyOtp('a@b.in', '1');
    auth.logout.mockResolvedValue({});
    await useSessionStore.getState().signOut({ everywhere: true });
    expect(auth.logout).toHaveBeenCalledWith({ everywhere: true });
    expect(tokenStore.current).toBeNull();
  });
});

describe('bootstrap', () => {
  it('lands on anonymous, quietly, when there is no refresh cookie', async () => {
    auth.refresh.mockRejectedValue(
      new AppError({
        code: ERROR_CODES.refreshMissing,
        message: 'no',
        action: 'sign in',
        status: 401,
      }),
    );
    await useSessionStore.getState().bootstrap();
    const state = useSessionStore.getState();
    expect(state.status).toBe('anonymous');
    expect(state.error).toBeNull();
  });

  it('restores an identity from a live refresh credential', async () => {
    auth.refresh.mockResolvedValue(SESSION);
    await useSessionStore.getState().bootstrap();
    expect(useSessionStore.getState().status).toBe('authenticated');
    expect(useSessionStore.getState().firm?.id).toBe('f1');
  });
});

describe('profile and firm name', () => {
  it('updateProfile follows the server response', async () => {
    auth.verifyOtp.mockResolvedValue(SESSION);
    await useSessionStore.getState().verifyOtp('a@b.in', '1');
    auth.updateProfile.mockResolvedValue({
      ...SESSION,
      user: { ...SESSION.user, name: 'Ar. Asha Rao', coaNumber: 'CA/2011/1' },
    });
    await useSessionStore
      .getState()
      .updateProfile({ name: 'Ar. Asha Rao', coaNumber: 'CA/2011/1' });
    expect(auth.updateProfile).toHaveBeenCalledWith({
      name: 'Ar. Asha Rao',
      coaNumber: 'CA/2011/1',
    });
    expect(useSessionStore.getState().user?.coaNumber).toBe('CA/2011/1');
  });

  it('setFirmName keeps the shell honest after a rename, and is a no-op signed out', () => {
    useSessionStore.getState().setFirmName('Nobody');
    expect(useSessionStore.getState().firm).toBeNull();
    useSessionStore.setState({ firm: SESSION.firm });
    useSessionStore.getState().setFirmName('Iyer & Rao');
    expect(useSessionStore.getState().firm?.name).toBe('Iyer & Rao');
  });
});
