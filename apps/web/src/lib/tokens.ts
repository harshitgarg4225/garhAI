/**
 * Where the session credentials live, and — more importantly — where they do not.
 *
 * §13 asks for "SameSite=Lax cookies for refresh". The client is written to
 * prefer exactly that, and to degrade honestly when it is not available:
 *
 *   **Cookie transport (preferred).** `POST /auth/verify` sets an httpOnly
 *   refresh cookie and returns no `refreshToken` in the body. JavaScript then
 *   holds only the 15-minute access token, in memory, and a page reload
 *   re-establishes the session by calling `POST /auth/refresh` with credentials.
 *   An XSS cannot read the refresh token at all.
 *
 *   **Body transport (fallback).** If the verify response *does* carry a
 *   `refreshToken`, the server is not setting a cookie, so we keep it in
 *   `sessionStorage` — deliberately not `localStorage`. sessionStorage is
 *   per-tab and dies with the tab, which bounds the blast radius of a stolen
 *   device to one open window. It is a worse position than the cookie and this
 *   module says so out loud rather than pretending otherwise.
 *
 * The ACCESS token is never persisted in either mode. It is short-lived by
 * design, and writing it to storage would trade away the one part of the
 * §13 posture that costs nothing.
 */

/** Storage key. Namespaced so two apps on one origin cannot collide. */
const STORAGE_KEY = 'garh.auth.v1';

/** Refresh this many seconds before the access token actually expires. */
export const REFRESH_SKEW_SECONDS = 60;

export type RefreshTransport = 'cookie' | 'body';

export interface AuthTokens {
  readonly accessToken: string;
  /** Unix seconds. */
  readonly accessExpiresAt: number;
  /** Null under cookie transport — the browser holds it, we never see it. */
  readonly refreshToken: string | null;
  readonly refreshTransport: RefreshTransport;
}

/** The only part of the session that survives a reload under body transport. */
interface PersistedAuth {
  readonly refreshToken: string;
  readonly transport: 'body';
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

/**
 * `sessionStorage` access that cannot throw. Safari in private mode, an iframe
 * with third-party storage blocked, and a browser with cookies disabled all
 * raise on `sessionStorage` — and none of those is a reason to fail to boot.
 */
function safeSessionStorage(): Storage | null {
  try {
    if (typeof globalThis.sessionStorage === 'undefined') return null;
    const probe = '__garh_probe__';
    globalThis.sessionStorage.setItem(probe, '1');
    globalThis.sessionStorage.removeItem(probe);
    return globalThis.sessionStorage;
  } catch {
    return null;
  }
}

function readPersisted(): PersistedAuth | null {
  const store = safeSessionStorage();
  if (!store) return null;
  const raw = store.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof (parsed as { refreshToken?: unknown }).refreshToken === 'string'
    ) {
      return { refreshToken: (parsed as { refreshToken: string }).refreshToken, transport: 'body' };
    }
  } catch {
    // Corrupt entry: drop it. A malformed credential is not recoverable and
    // keeping it around only produces a confusing 401 loop.
  }
  store.removeItem(STORAGE_KEY);
  return null;
}

function writePersisted(refreshToken: string | null): void {
  const store = safeSessionStorage();
  if (!store) return;
  try {
    if (refreshToken === null) store.removeItem(STORAGE_KEY);
    else store.setItem(STORAGE_KEY, JSON.stringify({ refreshToken, transport: 'body' }));
  } catch {
    // Quota or a blocked partition — the session simply will not survive a
    // reload. Silently degrading beats refusing to sign the user in.
  }
}

export type TokenListener = (tokens: AuthTokens | null) => void;

/**
 * Holds the current credentials and notifies subscribers when they change.
 *
 * Kept separate from the session store so that the HTTP layer can read a token
 * without importing a Zustand store (which would make `lib/` depend on
 * `stores/`, and make the client untestable without React).
 */
export class TokenStore {
  private tokens: AuthTokens | null = null;
  private readonly listeners = new Set<TokenListener>();

  constructor() {
    const persisted = readPersisted();
    if (persisted) {
      // We have a refresh token but no access token: the next request triggers
      // a refresh, which is exactly the boot path we want.
      this.tokens = {
        accessToken: '',
        accessExpiresAt: 0,
        refreshToken: persisted.refreshToken,
        refreshTransport: 'body',
      };
    }
  }

  get current(): AuthTokens | null {
    return this.tokens;
  }

  get accessToken(): string | null {
    const t = this.tokens;
    return t?.accessToken ? t.accessToken : null;
  }

  get refreshToken(): string | null {
    return this.tokens?.refreshToken ?? null;
  }

  /**
   * True when we hold something that could become a valid session: either a
   * refresh token, or the possibility of a refresh cookie. The cookie is
   * invisible to JS, so "possible" is the honest answer before the first call.
   */
  get hasRefreshCredential(): boolean {
    return this.tokens?.refreshToken != null;
  }

  /** True when there is no usable access token right now. */
  get needsRefresh(): boolean {
    const t = this.tokens;
    if (!t?.accessToken) return true;
    return t.accessExpiresAt - REFRESH_SKEW_SECONDS <= nowSeconds();
  }

  /**
   * Adopt a freshly issued pair. Transport is inferred from the payload: a
   * body-carried refresh token means the server is not setting a cookie.
   */
  set(input: {
    accessToken: string;
    expiresInSeconds: number;
    refreshToken?: string | null;
  }): void {
    const refreshToken = input.refreshToken ?? null;
    const transport: RefreshTransport = refreshToken === null ? 'cookie' : 'body';
    this.tokens = {
      accessToken: input.accessToken,
      accessExpiresAt: nowSeconds() + Math.max(0, Math.floor(input.expiresInSeconds)),
      refreshToken,
      refreshTransport: transport,
    };
    writePersisted(transport === 'body' ? refreshToken : null);
    this.emit();
  }

  /** Forget everything. Called on logout and on any unrecoverable auth failure. */
  clear(): void {
    this.tokens = null;
    writePersisted(null);
    this.emit();
  }

  subscribe(listener: TokenListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.tokens);
  }
}

/** The process-wide token store. One per tab, by construction. */
export const tokenStore = new TokenStore();
