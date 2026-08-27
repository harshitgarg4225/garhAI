/**
 * The client half of the §11 error contract.
 *
 * Every error the API emits is `application/problem+json`:
 *
 *     { "code": "op_sequence_conflict",
 *       "message": "This design moved on while you were editing.",
 *       "action": "Fetch ops since your base index, rebase, and re-send.",
 *       "requestId": "9f2c…" }
 *
 * (see `apps/api/garh_api/errors.py`, which is the authority for the code list).
 *
 * `AppError` is the single error type that leaves this client. Golden rule 9 —
 * *errors say what to do next* — is enforced structurally: `action` is a
 * required, non-empty string on every instance, including the ones we synthesise
 * for network failures and for responses that were not problem+json at all. If a
 * failure mode has no next step, it is not finished being designed.
 *
 * Two subclasses exist because the model store branches on them:
 *   - {@link OpConflictError}  → 409, carries `headIdx` → REBASE
 *   - {@link OpRejectionError} → 422, carries `issues[]` → ROLL BACK + toast
 */

import type { ValidationIssue } from '@garh/model';

// ---------------------------------------------------------------------------
// Codes — mirrors garh_api.errors.ERROR_CODES plus the client-only ones
// ---------------------------------------------------------------------------

/**
 * Stable machine codes. Clients switch on these; the server treats changing one
 * as a breaking API change. The last three are client-side only — the server
 * never sends them because in those cases there was no response at all.
 */
export const ERROR_CODES = {
  // generic
  internal: 'internal_error',
  serviceUnavailable: 'service_unavailable',
  notFound: 'not_found',
  methodNotAllowed: 'method_not_allowed',
  invalidRequest: 'invalid_request',
  validationFailed: 'validation_failed',
  conflict: 'conflict',
  payloadTooLarge: 'payload_too_large',
  unsupportedMediaType: 'unsupported_media_type',
  rateLimited: 'rate_limited',
  // auth / session
  unauthenticated: 'unauthenticated',
  tokenExpired: 'token_expired',
  tokenInvalid: 'token_invalid',
  tokenRevoked: 'token_revoked',
  refreshMissing: 'refresh_token_missing',
  refreshInvalid: 'refresh_token_invalid',
  refreshRevoked: 'refresh_token_revoked',
  refreshReused: 'refresh_token_reused',
  otpInvalid: 'otp_invalid',
  otpRateLimited: 'otp_rate_limited',
  emailAlreadyRegistered: 'email_already_registered',
  accountUnknown: 'account_unknown',
  // authorisation / tenancy
  permissionDenied: 'permission_denied',
  tenantContextRequired: 'tenant_context_required',
  opSequenceConflict: 'op_sequence_conflict',
  invalidCursor: 'invalid_cursor',
  // op log
  opRejected: 'op_rejected',
  // share links
  shareLinkInvalid: 'share_link_invalid',
  // client-side only: there was no response to read a code from
  network: 'network_error',
  timeout: 'request_timeout',
  aborted: 'request_aborted',
  malformedResponse: 'malformed_response',
} as const;

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES];

/** Codes that mean "your credential is no longer good" → re-authenticate. */
const AUTH_CODES: ReadonlySet<string> = new Set<string>([
  ERROR_CODES.unauthenticated,
  ERROR_CODES.tokenExpired,
  ERROR_CODES.tokenInvalid,
  ERROR_CODES.tokenRevoked,
  ERROR_CODES.refreshMissing,
  ERROR_CODES.refreshInvalid,
  ERROR_CODES.refreshRevoked,
  ERROR_CODES.refreshReused,
]);

/** Codes worth retrying with backoff. A 409 is NOT one — it needs a rebase. */
const RETRYABLE_CODES: ReadonlySet<string> = new Set<string>([
  ERROR_CODES.network,
  ERROR_CODES.timeout,
  ERROR_CODES.serviceUnavailable,
  ERROR_CODES.rateLimited,
  ERROR_CODES.internal,
]);

// ---------------------------------------------------------------------------
// The wire shape
// ---------------------------------------------------------------------------

/** RFC 7807 body as this API writes it. Extra members are allowed and kept. */
export interface ProblemJson {
  readonly code: string;
  readonly message: string;
  readonly action: string;
  readonly requestId?: string | null;
  readonly [extra: string]: unknown;
}

/**
 * A validation issue as it arrives from the API, normalised onto the model
 * core's {@link ValidationIssue} shape.
 *
 * The wire form (`garh_api.schemas.ops.ValidationIssueOut`) and the model core's
 * form differ in two places — the wire uses a single `elementId` and omits
 * `severity`, while the model core uses `elementIds[]` and always sets one.
 * {@link normaliseIssue} reconciles them so the UI has exactly one type to
 * render, whether the issue came from a local fold or from the server.
 */
export type ApiValidationIssue = Omit<ValidationIssue, 'code'> & {
  /**
   * A `ValidationCode` in practice — typed as `string` on purpose. The server
   * may run a newer model core than this bundle, and narrowing to today's union
   * would make an unknown-but-perfectly-valid code a type error at the one
   * place we least want to crash: rendering the reason an edit was rejected.
   */
  readonly code: string;
  /** Index of the offending op within the submitted batch, when the server said. */
  readonly opIndex?: number;
  readonly opType?: string;
};

function asString(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}

function asNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}

/** Accepts either the wire form or a model-core issue; always returns our form. */
export function normaliseIssue(raw: unknown): ApiValidationIssue | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const code = asString(r.code);
  const message = asString(r.message);
  if (!code || !message) return null;

  const elementIds: string[] = Array.isArray(r.elementIds)
    ? r.elementIds.filter((x): x is string => typeof x === 'string')
    : [];
  const single = asString(r.elementId);
  if (single && !elementIds.includes(single)) elementIds.push(single);

  const severity = r.severity === 'warning' ? 'warning' : 'error';
  const actual = r.actual;
  const limit = r.limit;

  return {
    code,
    message,
    severity,
    elementIds,
    ...(asString(r.field) === undefined ? {} : { field: asString(r.field) as string }),
    ...(typeof actual === 'number' || typeof actual === 'string' || actual === null
      ? { actual }
      : {}),
    ...(typeof limit === 'number' || typeof limit === 'string' || limit === null ? { limit } : {}),
    ...(asString(r.fix) === undefined ? {} : { fix: asString(r.fix) as string }),
    ...(asNumber(r.opIndex) === undefined ? {} : { opIndex: asNumber(r.opIndex) as number }),
    ...(asString(r.opType) === undefined ? {} : { opType: asString(r.opType) as string }),
  };
}

// ---------------------------------------------------------------------------
// AppError
// ---------------------------------------------------------------------------

export interface AppErrorInit {
  readonly code: string;
  readonly message: string;
  readonly action: string;
  /** HTTP status, or 0 when the request never produced a response. */
  readonly status?: number;
  readonly requestId?: string | null;
  readonly retryAfterSeconds?: number | null;
  /** Remaining problem+json members (`headIdx`, `errors`, `limit`, …). */
  readonly data?: Readonly<Record<string, unknown>>;
  readonly cause?: unknown;
  /** Method + path, for the log line. Never includes a query string (§13 privacy). */
  readonly endpoint?: string | null;
}

/**
 * The only error type this client throws. Anything caught from `fetch`, from
 * `JSON.parse`, or from a zod boundary parse is converted into one of these
 * before it leaves the API layer, so no call site ever has to handle `unknown`.
 */
export class AppError extends Error {
  readonly code: string;
  /** The one thing the user (or the client) should do next. Never empty. */
  readonly action: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;
  readonly data: Readonly<Record<string, unknown>>;
  readonly endpoint: string | null;

  constructor(init: AppErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = 'AppError';
    this.code = init.code;
    this.action = init.action;
    this.status = init.status ?? 0;
    this.requestId = init.requestId ?? null;
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.data = Object.freeze({ ...(init.data ?? {}) });
    this.endpoint = init.endpoint ?? null;
  }

  /** True when re-issuing the identical request could plausibly succeed. */
  get retryable(): boolean {
    return RETRYABLE_CODES.has(this.code) || this.status >= 500;
  }

  /** True when the fix is "sign in again", not "try again". */
  get isAuthFailure(): boolean {
    return AUTH_CODES.has(this.code);
  }

  /** True when the request was cancelled by us (navigating away, superseded). */
  get isAborted(): boolean {
    return this.code === ERROR_CODES.aborted;
  }

  /** True when the browser could not reach the API at all. */
  get isOffline(): boolean {
    return this.code === ERROR_CODES.network;
  }

  /** Round-trips back to the wire shape — handy for logging and for tests. */
  toProblem(): ProblemJson {
    return {
      ...this.data,
      code: this.code,
      message: this.message,
      action: this.action,
      requestId: this.requestId,
    };
  }

  /**
   * Coerce anything into an `AppError`. Used at the outer edge of async store
   * actions so a `catch (e: unknown)` never has to type-narrow by hand.
   */
  static from(err: unknown): AppError {
    if (err instanceof AppError) return err;
    if (isDomAbort(err)) return abortError();
    if (err instanceof Error) {
      return new AppError({
        code: ERROR_CODES.internal,
        message: err.message || 'Something went wrong.',
        action: 'Try again. If it keeps happening, reload the page.',
        cause: err,
      });
    }
    return new AppError({
      code: ERROR_CODES.internal,
      message: 'Something went wrong.',
      action: 'Try again. If it keeps happening, reload the page.',
      cause: err,
    });
  }
}

/** 409 from `POST /projects/:id/ops`: someone else's ops landed first. */
export class OpConflictError extends AppError {
  /** The server's real head index — rebase onto this. */
  readonly headIdx: number;
  /** The index we claimed to be based on. */
  readonly baseIdx: number | null;

  constructor(init: AppErrorInit & { headIdx: number; baseIdx?: number | null }) {
    super({ ...init, code: ERROR_CODES.opSequenceConflict });
    this.name = 'OpConflictError';
    this.headIdx = init.headIdx;
    this.baseIdx = init.baseIdx ?? null;
  }
}

/** 422 from the op sequencer: the op is invalid against the server's state. */
export class OpRejectionError extends AppError {
  readonly issues: readonly ApiValidationIssue[];
  readonly opType: string | null;
  /** Present when the server also told us where its head is. */
  readonly headIdx: number | null;

  constructor(
    init: AppErrorInit & {
      issues: readonly ApiValidationIssue[];
      opType?: string | null;
      headIdx?: number | null;
    },
  ) {
    super({ ...init, code: ERROR_CODES.opRejected });
    this.name = 'OpRejectionError';
    this.issues = init.issues;
    this.opType = init.opType ?? null;
    this.headIdx = init.headIdx ?? null;
  }

  /** First issue's `fix`, if the server offered one — the "Fix it" button copy. */
  get firstFix(): string | null {
    for (const issue of this.issues) {
      if (issue.fix) return issue.fix;
    }
    return null;
  }
}

// ---------------------------------------------------------------------------
// Constructors for the failures that produce no response body
// ---------------------------------------------------------------------------

function isDomAbort(err: unknown): boolean {
  return (
    typeof err === 'object' &&
    err !== null &&
    'name' in err &&
    (err as { name?: unknown }).name === 'AbortError'
  );
}

export function abortError(endpoint?: string): AppError {
  return new AppError({
    code: ERROR_CODES.aborted,
    message: 'That request was cancelled.',
    action: 'Nothing to do — this happens when you navigate away mid-request.',
    status: 0,
    endpoint: endpoint ?? null,
  });
}

export function networkError(endpoint: string, cause: unknown): AppError {
  return new AppError({
    code: ERROR_CODES.network,
    message: "We couldn't reach Garh AI.",
    action: 'Check your connection — your edits are saved locally and will sync automatically.',
    status: 0,
    endpoint,
    cause,
  });
}

export function timeoutError(endpoint: string, ms: number): AppError {
  return new AppError({
    code: ERROR_CODES.timeout,
    message: 'That took too long to respond.',
    action: 'Try again in a moment.',
    status: 0,
    endpoint,
    data: { timeoutMs: ms },
  });
}

export function malformedResponseError(
  endpoint: string,
  detail: string,
  cause?: unknown,
): AppError {
  return new AppError({
    code: ERROR_CODES.malformedResponse,
    message: "Garh AI sent back something this version of the app doesn't understand.",
    action: 'Reload the page to pick up the latest version.',
    status: 0,
    endpoint,
    data: { detail },
    cause,
  });
}

// ---------------------------------------------------------------------------
// Parsing a response into an AppError
// ---------------------------------------------------------------------------

/** Fallback copy for a status that arrived without a usable problem+json body. */
function fallbackForStatus(status: number): { code: string; message: string; action: string } {
  if (status === 401) {
    return {
      code: ERROR_CODES.unauthenticated,
      message: "You're not signed in.",
      action: 'Sign in and try again.',
    };
  }
  if (status === 403) {
    return {
      code: ERROR_CODES.permissionDenied,
      message: "You don't have access to that.",
      action: 'Ask a firm admin for access.',
    };
  }
  if (status === 404) {
    return {
      code: ERROR_CODES.notFound,
      message: "We couldn't find that.",
      action: 'Go back and pick it from the list.',
    };
  }
  if (status === 409) {
    return {
      code: ERROR_CODES.conflict,
      message: 'That changed while you were working on it.',
      action: 'Reload and try again.',
    };
  }
  if (status === 413) {
    return {
      code: ERROR_CODES.payloadTooLarge,
      message: 'That file is too large.',
      action: 'Upload a smaller file.',
    };
  }
  if (status === 429) {
    return {
      code: ERROR_CODES.rateLimited,
      message: "That's a lot of requests in a short time.",
      action: 'Wait a few seconds and try again.',
    };
  }
  if (status >= 500) {
    return {
      code: ERROR_CODES.internal,
      message: 'Something went wrong on our side.',
      action: 'Try again. If it keeps happening, contact support with the request id.',
    };
  }
  return {
    code: ERROR_CODES.invalidRequest,
    message: "We couldn't complete that.",
    action: 'Try again.',
  };
}

/**
 * Build the right `AppError` subclass from a non-2xx response body.
 *
 * `body` is whatever JSON we managed to read (or `null`). The status is trusted
 * over the body: a 409 with a body that forgot `headIdx` still becomes an
 * `OpConflictError`, using the fallback head we were given, because the model
 * store's rebase path is safer than its rollback path when we are unsure.
 */
export function problemToAppError(
  status: number,
  body: unknown,
  ctx: { endpoint: string; requestId?: string | null; retryAfterSeconds?: number | null },
): AppError {
  const record: Record<string, unknown> =
    typeof body === 'object' && body !== null ? { ...(body as Record<string, unknown>) } : {};

  const fallback = fallbackForStatus(status);
  const code = asString(record.code) ?? fallback.code;
  const message = asString(record.message) ?? fallback.message;
  const action = asString(record.action) ?? fallback.action;
  const requestId = asString(record.requestId) ?? ctx.requestId ?? null;
  const retryAfterSeconds = asNumber(record.retryAfterSeconds) ?? ctx.retryAfterSeconds ?? null;

  // Strip the four canonical members; everything else is context worth keeping.
  const data: Record<string, unknown> = { ...record };
  delete data.code;
  delete data.message;
  delete data.action;
  delete data.requestId;

  const base: AppErrorInit = {
    code,
    message,
    action,
    status,
    requestId,
    retryAfterSeconds,
    data,
    endpoint: ctx.endpoint,
  };

  if (code === ERROR_CODES.opRejected || (status === 422 && Array.isArray(record.issues))) {
    const rawIssues = Array.isArray(record.issues) ? record.issues : [];
    const issues = rawIssues.map(normaliseIssue).filter((i): i is ApiValidationIssue => i !== null);
    return new OpRejectionError({
      ...base,
      issues,
      opType: asString(record.opType) ?? null,
      headIdx: asNumber(record.headIdx) ?? null,
    });
  }

  if (code === ERROR_CODES.opSequenceConflict) {
    return new OpConflictError({
      ...base,
      headIdx: asNumber(record.headIdx) ?? -1,
      baseIdx: asNumber(record.baseIdx) ?? null,
    });
  }

  return new AppError(base);
}

// ---------------------------------------------------------------------------
// Plain problem detail — what the presentational layer renders
// ---------------------------------------------------------------------------

/**
 * A problem, flattened to plain data.
 *
 * Structurally identical to `components/types.ts`'s `Problem`, which is what
 * `<ProblemPanel>` and the error boundary take. It is declared here rather than
 * imported so that the store and transport layers never depend on the component
 * tree — the coupling runs one way, and TypeScript's structural typing keeps
 * the two in agreement for free. If you add a field there, add it here.
 *
 * The reason stores hold this and not an `AppError`: an `Error` instance in
 * Zustand state is a live object with a stack, a `cause` chain, and reference
 * identity that changes on every retry. Rendering state should be a value.
 */
export interface ProblemDetail {
  code: string;
  message: string;
  action?: string | undefined;
  status?: number | undefined;
  /** Present on 409 op-sequence conflicts. */
  headIdx?: number | undefined;
  /** Support correlation id, when the server supplied one. */
  requestId?: string | undefined;
}

/**
 * Coerce anything thrown into a {@link ProblemDetail}.
 *
 * Never throws, never returns undefined, and always carries an `action` —
 * `AppError` guarantees one, and everything else routes through `AppError.from`
 * which supplies a sane default. Golden rule 9 holds all the way to the pixel.
 */
export function toProblemDetail(err: unknown): ProblemDetail {
  const error = AppError.from(err);
  const headIdx =
    error instanceof OpConflictError
      ? error.headIdx
      : error instanceof OpRejectionError && error.headIdx !== null
        ? error.headIdx
        : undefined;

  return {
    code: error.code,
    message: error.message,
    action: error.action,
    status: error.status,
    ...(headIdx === undefined ? {} : { headIdx }),
    ...(error.requestId === null ? {} : { requestId: error.requestId }),
  };
}
