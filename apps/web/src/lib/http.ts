/**
 * The transport underneath `lib/api.ts`.
 *
 * Everything that is about *how* a request is made lives here — auth headers,
 * the single-flight token refresh, idempotency keys, timeouts, problem+json
 * parsing — so that `api.ts` is a flat list of typed endpoints and nothing else.
 *
 * ## The single-flight refresh
 *
 * A project screen fires six requests on mount. If the access token has just
 * expired, all six get a 401 at roughly the same instant. The naive fix — each
 * one refreshes and retries — sends six refresh calls, and with **refresh token
 * rotation** (§13) five of them present a token that the first call already
 * rotated. The server correctly reads that as reuse, revokes the whole family,
 * and signs the user out. The bug looks like "the app randomly logs me out".
 *
 * So: at most one refresh is ever in flight. Concurrent callers await the same
 * promise, and each retries its own request exactly once with whatever token
 * that refresh produced. `refreshInflight` is the entire mechanism.
 */

import { env } from './env';
import {
  AppError,
  ERROR_CODES,
  abortError,
  malformedResponseError,
  networkError,
  problemToAppError,
  timeoutError,
} from './errors';
import { newIdempotencyKey } from './ids';
import { tokenStore, type TokenStore } from './tokens';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions<T> {
  readonly method?: HttpMethod;
  /** Path relative to the API base, always starting with `/`. */
  readonly path: string;
  readonly query?: Readonly<Record<string, QueryValue>>;
  readonly body?: unknown;
  readonly signal?: AbortSignal | undefined;
  readonly timeoutMs?: number;
  /**
   * `'required'` (default) sends the bearer token and refreshes on 401;
   * `'none'` sends no credential — used by the auth endpoints themselves and by
   * the share-link viewer surface, which authenticates with a token in the path.
   */
  readonly auth?: 'required' | 'none';
  /**
   * Explicit key for a replay-safe mutation (§11). Generated automatically for
   * POST/PUT/PATCH/DELETE; pass `null` to suppress it on endpoints that are
   * naturally idempotent and reject the header.
   */
  readonly idempotencyKey?: string | null;
  readonly headers?: Readonly<Record<string, string>>;
  /** Boundary validation. Anything that throws here becomes a malformed-response error. */
  readonly parse: (data: unknown) => T;
  /** Retries for transport failures. Only ever applied to GET (see below). */
  readonly retry?: number;
}

/** Every request gets one of these — the deadline that stops a hung tab. */
const DEFAULT_TIMEOUT_MS = 20_000;
/** Reads may be retried on a transport failure; writes may not, without a key. */
const RETRYABLE_METHODS: ReadonlySet<HttpMethod> = new Set<HttpMethod>(['GET']);
/** Methods that carry an `Idempotency-Key` by default. */
const KEYED_METHODS: ReadonlySet<HttpMethod> = new Set<HttpMethod>([
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
]);

export interface HttpClientOptions {
  readonly baseUrl?: string;
  readonly tokens?: TokenStore;
  readonly fetchImpl?: typeof fetch;
  /**
   * Called when the session is definitively gone (refresh failed, or was
   * rejected as reused). The session store wires this to "sign out and route to
   * /login", and it fires at most once per lost session.
   */
  readonly onAuthLost?: (error: AppError) => void;
}

/** Shape of `POST /auth/refresh`, as far as the transport cares. */
interface RefreshResponseShape {
  accessToken: string;
  expiresIn: number;
  refreshToken?: string | null;
}

function buildQuery(query: Readonly<Record<string, QueryValue>> | undefined): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.append(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : '';
}

/** Merge an external abort signal with our own timeout, without leaking timers. */
function withTimeout(
  external: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; cleanup: () => void; timedOut: () => boolean } {
  const controller = new AbortController();
  let didTimeout = false;

  const onExternalAbort = (): void => controller.abort(external?.reason);
  const timer = setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, timeoutMs);

  if (external) {
    if (external.aborted) controller.abort(external.reason);
    else external.addEventListener('abort', onExternalAbort, { once: true });
  }

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      if (external) external.removeEventListener('abort', onExternalAbort);
    },
    timedOut: () => didTimeout,
  };
}

async function readJson(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    // A non-JSON body from a proxy or a load balancer. Preserve a slice of it —
    // "<html><head><title>502 Bad Gateway" in a log is worth ten guesses.
    return { __nonJsonBody: text.slice(0, 512) };
  }
}

export class HttpClient {
  private readonly baseUrl: string;
  private readonly tokens: TokenStore;
  private readonly fetchImpl: typeof fetch;
  private onAuthLost: ((error: AppError) => void) | null;

  /** The one in-flight refresh, shared by every caller that needs it. */
  private refreshInflight: Promise<string | null> | null = null;
  /** Set once per lost session so `onAuthLost` cannot fire in a loop. */
  private authLostAnnounced = false;

  constructor(options: HttpClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? env.apiBaseUrl).replace(/\/+$/, '');
    this.tokens = options.tokens ?? tokenStore;
    // Bound: an unbound `fetch` throws "Illegal invocation" in some browsers.
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.onAuthLost = options.onAuthLost ?? null;
  }

  /** Wired by the session store at boot. */
  setAuthLostHandler(handler: ((error: AppError) => void) | null): void {
    this.onAuthLost = handler;
  }

  /** Absolute URL for a path — used by the SSE reader and by download links. */
  url(path: string, query?: Readonly<Record<string, QueryValue>>): string {
    return `${this.baseUrl}${path}${buildQuery(query)}`;
  }

  /** Current bearer, refreshing first if it is missing or about to expire. */
  async authorization(): Promise<string | null> {
    if (this.tokens.needsRefresh) {
      const token = await this.refreshOnce();
      return token;
    }
    return this.tokens.accessToken;
  }

  async request<T>(options: RequestOptions<T>): Promise<T> {
    const method = options.method ?? 'GET';
    const endpoint = `${method} ${options.path}`;
    const auth = options.auth ?? 'required';

    // Generated ONCE, outside the retry loop: a replay after a refresh must
    // reuse the same key, or a request the server already applied gets applied
    // a second time.
    const idempotencyKey =
      options.idempotencyKey === undefined
        ? KEYED_METHODS.has(method)
          ? newIdempotencyKey()
          : null
        : options.idempotencyKey;

    // Proactive refresh: cheaper than a guaranteed 401 round trip, and it means
    // the reactive path below only ever handles genuine revocations.
    if (auth === 'required' && this.tokens.needsRefresh && this.tokens.hasRefreshCredential) {
      await this.refreshOnce();
    }

    const maxTransportRetries = RETRYABLE_METHODS.has(method) ? (options.retry ?? 1) : 0;
    let refreshRetried = false;
    let refreshGaveUp = false;
    let transportAttempt = 0;

    for (;;) {
      let response: Response;
      try {
        response = await this.send(options, method, auth, idempotencyKey);
      } catch (err) {
        if (err instanceof AppError) {
          const canRetry =
            err.code === ERROR_CODES.network && transportAttempt < maxTransportRetries;
          if (!canRetry) throw err;
          transportAttempt += 1;
          await sleep(200 * transportAttempt);
          continue;
        }
        throw AppError.from(err);
      }

      if (response.ok) {
        return this.parseSuccess(response, options.parse, endpoint);
      }

      const requestId = response.headers.get('x-request-id');
      const retryAfterHeader = response.headers.get('retry-after');
      const body = await readJson(response);
      const error = problemToAppError(response.status, body, {
        endpoint,
        requestId,
        retryAfterSeconds: retryAfterHeader === null ? null : Number.parseInt(retryAfterHeader, 10),
      });

      // Reactive refresh: exactly once, and only for a credential problem on a
      // request that was actually carrying a credential.
      if (
        response.status === 401 &&
        auth === 'required' &&
        !refreshRetried &&
        error.isAuthFailure &&
        error.code !== ERROR_CODES.refreshReused
      ) {
        refreshRetried = true;
        const token = await this.refreshOnce();
        if (token) continue;
        refreshGaveUp = true;
      }

      // When a refresh was attempted and came back empty, `refreshOnce` has
      // already announced the loss in the branches where the session is
      // genuinely gone — and deliberately NOT when the refresh merely failed
      // to reach the server. Announcing here on the original 401 would turn
      // "offline for a minute" into "signed out" (see the http spec).
      if (error.isAuthFailure && !refreshGaveUp) this.announceAuthLost(error);
      throw error;
    }
  }

  private async send(
    options: RequestOptions<unknown>,
    method: HttpMethod,
    auth: 'required' | 'none',
    idempotencyKey: string | null,
  ): Promise<Response> {
    const headers = new Headers({ Accept: 'application/json' });
    for (const [k, v] of Object.entries(options.headers ?? {})) headers.set(k, v);

    let bodyInit: BodyInit | undefined;
    if (options.body !== undefined) {
      headers.set('Content-Type', 'application/json');
      bodyInit = JSON.stringify(options.body);
    }
    if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);
    if (auth === 'required') {
      const token = this.tokens.accessToken;
      if (token) headers.set('Authorization', `Bearer ${token}`);
    }

    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const { signal, cleanup, timedOut } = withTimeout(options.signal, timeoutMs);
    const endpoint = `${method} ${options.path}`;

    try {
      return await this.fetchImpl(this.url(options.path, options.query), {
        method,
        headers,
        // Required for the httpOnly refresh cookie (§13). Same-origin deploys
        // are unaffected; cross-origin ones need the API's CORS allowlist to
        // set `Access-Control-Allow-Credentials`.
        credentials: 'include',
        // Never let a proxy hand us a stale op log.
        cache: 'no-store',
        redirect: 'follow',
        referrerPolicy: 'strict-origin-when-cross-origin',
        ...(bodyInit === undefined ? {} : { body: bodyInit }),
        signal,
      });
    } catch (err) {
      if (timedOut()) throw timeoutError(endpoint, timeoutMs);
      if (options.signal?.aborted) throw abortError(endpoint);
      throw networkError(endpoint, err);
    } finally {
      cleanup();
    }
  }

  private async parseSuccess<T>(
    response: Response,
    parse: (data: unknown) => T,
    endpoint: string,
  ): Promise<T> {
    const data = await readJson(response);
    try {
      return parse(data);
    } catch (err) {
      // A shape we do not recognise is a deploy-skew problem, not a user
      // problem, and the honest next step is "reload to get the new bundle".
      const detail = err instanceof Error ? err.message : String(err);
      throw malformedResponseError(endpoint, detail, err);
    }
  }

  /**
   * Refresh the access token, at most once concurrently.
   *
   * Returns the new access token, or `null` when the session is gone. Never
   * throws: callers use the null to decide between "retry" and "give up", and
   * an exception here would surface as a confusing error on whichever unlucky
   * request happened to trigger the refresh.
   */
  private refreshOnce(): Promise<string | null> {
    if (this.refreshInflight) return this.refreshInflight;

    const attempt = (async (): Promise<string | null> => {
      const refreshToken = this.tokens.refreshToken;
      // With cookie transport there is nothing to send — the browser attaches
      // the cookie — so an absent token is not by itself a reason to bail.
      const body = refreshToken === null ? undefined : { refreshToken };

      try {
        const response = await this.send(
          {
            path: '/auth/refresh',
            method: 'POST',
            auth: 'none',
            parse: (d) => d,
            ...(body === undefined ? {} : { body }),
          },
          'POST',
          'none',
          null,
        );

        if (!response.ok) {
          const requestId = response.headers.get('x-request-id');
          const payload = await readJson(response);
          const error = problemToAppError(response.status, payload, {
            endpoint: 'POST /auth/refresh',
            requestId,
          });
          this.tokens.clear();
          this.announceAuthLost(error);
          return null;
        }

        const payload = await readJson(response);
        const parsed = readRefresh(payload);
        if (!parsed) {
          this.tokens.clear();
          this.announceAuthLost(
            malformedResponseError('POST /auth/refresh', 'missing accessToken/expiresIn'),
          );
          return null;
        }

        this.tokens.set({
          accessToken: parsed.accessToken,
          expiresInSeconds: parsed.expiresIn,
          refreshToken: parsed.refreshToken ?? null,
        });
        this.authLostAnnounced = false;
        return parsed.accessToken;
      } catch (err) {
        // A transport failure is NOT a lost session: the token may still be
        // perfectly valid and the network merely down. Leave the credentials
        // alone so the app recovers when connectivity returns.
        const error = AppError.from(err);
        if (!error.isOffline && error.code !== ERROR_CODES.timeout) {
          this.tokens.clear();
          this.announceAuthLost(error);
        }
        return null;
      } finally {
        this.refreshInflight = null;
      }
    })();

    this.refreshInflight = attempt;
    return attempt;
  }

  private announceAuthLost(error: AppError): void {
    if (this.authLostAnnounced) return;
    this.authLostAnnounced = true;
    this.onAuthLost?.(error);
  }

  /** Called by the session store after a successful sign-in. */
  resetAuthLost(): void {
    this.authLostAnnounced = false;
  }
}

function readRefresh(payload: unknown): RefreshResponseShape | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const p = payload as Record<string, unknown>;
  const accessToken = p.accessToken;
  const expiresIn = p.expiresIn;
  if (typeof accessToken !== 'string' || accessToken.length === 0) return null;
  if (typeof expiresIn !== 'number' || !Number.isFinite(expiresIn)) return null;
  const refreshToken = typeof p.refreshToken === 'string' ? p.refreshToken : null;
  return { accessToken, expiresIn, refreshToken };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** The client the app uses. Tests construct their own with a stub `fetch`. */
export const http = new HttpClient();
