/**
 * `session` — who is signed in, and what they may do (§12, §13).
 *
 * Sign-in is email + OTP. The store models the whole flow honestly, including
 * the two things a naive implementation gets wrong:
 *
 *  - **`POST /auth/otp` answers identically for a known and an unknown
 *    address.** The server is careful about that (it burns the same rate-limit
 *    budget either way, so even the 429 behaviour cannot be used to enumerate
 *    accounts). The UI must not undo it by saying "no account found".
 *  - **The refresh cookie is invisible to JavaScript.** So `bootstrap()` cannot
 *    ask "am I signed in?" — it has to *try* a refresh and see. `status` is
 *    `'restoring'` while that is in flight, which is what stops the router from
 *    bouncing a signed-in user to the login screen on every hard reload.
 *
 * The store never touches tokens directly; `lib/tokens.ts` owns them and
 * `lib/http.ts` refreshes them. What this store owns is the *identity* — user,
 * firm, role — and the routing consequences of losing it.
 */

import { create } from 'zustand';

import { api } from '../lib/api';
import { AppError } from '../lib/errors';
import { http } from '../lib/http';
import { tokenStore } from '../lib/tokens';
import type { Firm, Session, User } from '../lib/schemas';

export type SessionStatus = 'unknown' | 'restoring' | 'anonymous' | 'authenticated' | 'share';

/** What the OTP screen needs to render its countdown honestly. */
export interface OtpChallenge {
  readonly email: string;
  readonly sentAt: number;
  readonly resendAfterSeconds: number;
  readonly expiresInSeconds: number;
  /** Dev-only echo of the code; always null outside a dev/test environment. */
  readonly devCode: string | null;
}

/** Read-only client-share mode: a token, a scope, and no firm identity. */
export interface ShareContext {
  readonly token: string;
  readonly sections: readonly string[];
  readonly canComment: boolean;
}

/**
 * What `requestOtp` resolves with — the login screen's countdown depends on it.
 *
 * Structurally the `OtpRequestResult` declared in `pages/_contracts.ts`, with
 * `devCode` as `string | undefined` rather than `| null`: the page renders it
 * with `{devCode !== undefined && …}`, and two ways to say "absent" in one
 * field is how a dev-only affordance ends up rendering an empty box in
 * production.
 */
export interface OtpRequestResult {
  readonly expiresInSeconds: number;
  readonly resendAfterSeconds: number;
  /** DEV ONLY. Populated only when the API is running a mock mailer. */
  readonly devCode?: string | undefined;
}

export interface SessionState {
  status: SessionStatus;
  user: User | null;
  firm: Firm | null;
  otp: OtpChallenge | null;
  share: ShareContext | null;
  /** Last auth failure worth showing on the sign-in screen. */
  error: AppError | null;
  busy: boolean;

  // ── actions ────────────────────────────────────────────────────────────
  /** Try to restore a session from the refresh credential. Safe to call twice. */
  bootstrap: () => Promise<void>;

  /**
   * Ask for a sign-in code.
   *
   * **Rejects with an `AppError` on failure** rather than returning a flag. The
   * login page needs the problem detail — a rate-limit message and a mistyped
   * address are different screens — and `status`/`error` on this store are for
   * the parts of the app that are not the login page. Both are updated too.
   */
  requestOtp: (email: string) => Promise<OtpRequestResult>;

  /**
   * Exchange the code for a session. Rejects with an `AppError` on failure; the
   * login page branches on `error.code` (`otp_invalid`, `otp_expired`) to count
   * attempts down.
   *
   * `email` is passed explicitly rather than read from the pending challenge so
   * the call is self-describing and testable in isolation.
   *
   * `{ email, code }` and nothing else: `VerifyRequest` on the server is
   * declared `extra="forbid"`, so a `name`/`firmName` here is a 422. Creating a
   * firm is {@link SessionActions.signUp}, which ends by issuing a code that
   * this call then verifies.
   */
  verifyOtp: (email: string, code: string) => Promise<void>;

  /**
   * Create a firm and its first admin, then send that admin a sign-in code.
   *
   * Resolves with the same challenge shape as `requestOtp`, because that is
   * exactly where signup lands: the new admin still has to prove they own the
   * address. Rejects with an `AppError`; unlike sign-in, this route *does* admit
   * that an address is already registered (`email_already_registered`), because
   * a signup form that silently does nothing strands the user.
   */
  signUp: (input: {
    firmName: string;
    name: string;
    email: string;
    coaNumber?: string;
  }) => Promise<OtpRequestResult>;

  signOut: (options?: { everywhere?: boolean }) => Promise<void>;
  /** Enter read-only share mode (the `/share/:token` route). */
  enterShareMode: (context: ShareContext) => void;
  clearError: () => void;
}

/** Single-flight guard: React 18 StrictMode mounts effects twice in dev. */
let bootstrapInflight: Promise<void> | null = null;

function adoptSession(session: Session): void {
  tokenStore.set({
    accessToken: session.accessToken,
    expiresInSeconds: session.expiresIn,
    refreshToken: session.refreshToken,
  });
  http.resetAuthLost();
}

export const useSessionStore = create<SessionState>()((set, get) => ({
  status: 'unknown',
  user: null,
  firm: null,
  otp: null,
  share: null,
  error: null,
  busy: false,

  bootstrap: async () => {
    if (bootstrapInflight) return bootstrapInflight;
    if (get().status === 'authenticated') return;

    const run = (async (): Promise<void> => {
      set({ status: 'restoring', error: null });
      try {
        // Works in both transports: with a cookie the browser attaches it, with
        // a body token `HttpClient` sends the one it kept in sessionStorage.
        const session = await api.auth.refresh();
        adoptSession(session);
        set({ status: 'authenticated', user: session.user, firm: session.firm, error: null });
      } catch (err) {
        const error = AppError.from(err);
        // Being signed out is the ordinary case here, not an error to shout
        // about. Only a genuine outage is worth surfacing.
        set({
          status: 'anonymous',
          user: null,
          firm: null,
          error: error.isOffline ? error : null,
        });
      } finally {
        bootstrapInflight = null;
      }
    })();

    bootstrapInflight = run;
    return run;
  },

  requestOtp: async (email) => {
    const address = email.trim().toLowerCase();
    set({ busy: true, error: null });
    try {
      const result = await api.auth.requestOtp({ email: address });
      set({
        busy: false,
        otp: {
          email: address,
          sentAt: Date.now(),
          resendAfterSeconds: result.resendAfterSeconds,
          expiresInSeconds: result.expiresInSeconds,
          devCode: result.devCode,
        },
      });
      return {
        expiresInSeconds: result.expiresInSeconds,
        resendAfterSeconds: result.resendAfterSeconds,
        ...(result.devCode === null ? {} : { devCode: result.devCode }),
      };
    } catch (err) {
      const error = AppError.from(err);
      set({ busy: false, error });
      throw error;
    }
  },

  signUp: async (input) => {
    const address = input.email.trim().toLowerCase();
    set({ busy: true, error: null });
    try {
      const result = await api.auth.signup({
        firmName: input.firmName.trim(),
        name: input.name.trim(),
        email: address,
        ...(input.coaNumber === undefined || input.coaNumber.trim() === ''
          ? {}
          : { coaNumber: input.coaNumber.trim() }),
      });
      set({
        busy: false,
        otp: {
          email: address,
          sentAt: Date.now(),
          resendAfterSeconds: result.resendAfterSeconds,
          expiresInSeconds: result.expiresInSeconds,
          devCode: result.devCode,
        },
      });
      return {
        expiresInSeconds: result.expiresInSeconds,
        resendAfterSeconds: result.resendAfterSeconds,
        ...(result.devCode === null ? {} : { devCode: result.devCode }),
      };
    } catch (err) {
      const error = AppError.from(err);
      set({ busy: false, error });
      throw error;
    }
  },

  verifyOtp: async (email, code) => {
    const address = email.trim().toLowerCase();
    set({ busy: true, error: null });
    try {
      const session = await api.auth.verifyOtp({ email: address, code });
      adoptSession(session);
      set({
        busy: false,
        status: 'authenticated',
        user: session.user,
        firm: session.firm,
        otp: null,
        error: null,
      });
    } catch (err) {
      const error = AppError.from(err);
      set({ busy: false, error });
      throw error;
    }
  },

  signOut: async (options = {}) => {
    // Clear locally FIRST. If the network call fails we still want this tab
    // signed out — a logout that silently did nothing is a security bug.
    tokenStore.clear();
    set({ status: 'anonymous', user: null, firm: null, otp: null, share: null, error: null });
    try {
      await api.auth.logout({ everywhere: options.everywhere ?? false });
    } catch {
      // Best effort. The refresh family expires on its own.
    }
  },

  enterShareMode: (context) =>
    set({ status: 'share', share: context, user: null, firm: null, error: null }),

  clearError: () => set({ error: null }),
}));

/**
 * Wire the transport's "your session is gone" signal into the store.
 *
 * Called once from `main.tsx`. It has to be a registration rather than an
 * import-time side effect inside `http.ts`, because that would make the HTTP
 * layer depend on a Zustand store and drag React into every client test.
 */
export function installSessionWatcher(): void {
  http.setAuthLostHandler((error) => {
    const state = useSessionStore.getState();
    // A share viewer has no session to lose; a failed share token is handled by
    // the share route itself, which can say something far more useful.
    if (state.status === 'share') return;
    tokenStore.clear();
    useSessionStore.setState({
      status: 'anonymous',
      user: null,
      firm: null,
      // Only explain the sign-out when the user was actually signed in. During
      // boot this fires for "no cookie", which is not news.
      error: state.status === 'authenticated' ? error : null,
    });
  });
}

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectStatus = (s: SessionState): SessionStatus => s.status;
export const selectUser = (s: SessionState): User | null => s.user;
export const selectFirm = (s: SessionState): Firm | null => s.firm;
export const selectIsAuthenticated = (s: SessionState): boolean => s.status === 'authenticated';
/** True while we do not yet know — the router must wait, not redirect. */
export const selectIsResolvingSession = (s: SessionState): boolean =>
  s.status === 'unknown' || s.status === 'restoring';
export const selectIsAdmin = (s: SessionState): boolean => s.user?.role === 'admin';
/** Share viewers are read-only by construction (§13). */
export const selectCanWrite = (s: SessionState): boolean => s.status === 'authenticated';
export const selectShareContext = (s: SessionState): ShareContext | null => s.share;
export const selectAuthError = (s: SessionState): AppError | null => s.error;
/** True while an OTP request or verify is in flight — disables the submit button. */
export const selectIsAuthBusy = (s: SessionState): boolean => s.busy;
export const selectOtpChallenge = (s: SessionState): OtpChallenge | null => s.otp;
