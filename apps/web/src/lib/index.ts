/**
 * `src/lib` — everything between the app and the outside world.
 *
 *   env        validated `VITE_*` configuration (§13's secrets boundary)
 *   errors     `AppError` + problem+json parsing (golden rule 9)
 *   tokens     access/refresh token custody
 *   http       fetch transport: bearer, single-flight refresh, idempotency
 *   schemas    zod schemas for the §11 wire surface
 *   api        the typed endpoint catalogue
 *   sse        job progress streams (§15 generation theater)
 *   ids        ULIDs for `clientOpId`, group ids, idempotency keys
 *   units      the mm ⇄ display boundary + Indian defaults (§15)
 *   keymap     the keyboard map and its registration hook (§12)
 *   shortcuts  what each keyboard command does in this app
 *   share      share-link URLs, WhatsApp deep links, clipboard
 *
 * ## Do not import this barrel from a store
 *
 * `shortcuts` imports the stores, so `stores/* → lib/index → lib/shortcuts →
 * stores/*` is a cycle. Stores import the specific module they need
 * (`../lib/api`, `../lib/errors`), and that is why. Components and pages may
 * use the barrel freely.
 */

// ── Configuration ──────────────────────────────────────────────────────────
export { env, appOrigin, appUrlFor, DEFAULT_UNITS_DISPLAY } from './env';
export type { AppEnv } from './env';

// ── Errors ─────────────────────────────────────────────────────────────────
export {
  AppError,
  OpConflictError,
  OpRejectionError,
  ERROR_CODES,
  normaliseIssue,
  problemToAppError,
  toProblemDetail,
  abortError,
  networkError,
  timeoutError,
  malformedResponseError,
} from './errors';
export type { ErrorCode, ProblemJson, ProblemDetail, ApiValidationIssue, AppErrorInit } from './errors';

// ── Transport ──────────────────────────────────────────────────────────────
export { http, HttpClient } from './http';
export type { HttpMethod, HttpClientOptions, RequestOptions, QueryValue } from './http';
export { tokenStore, TokenStore, REFRESH_SKEW_SECONDS } from './tokens';
export type { AuthTokens, RefreshTransport, TokenListener } from './tokens';

// ── The API ────────────────────────────────────────────────────────────────
export { api, createApiClient, toOpEnvelopes } from './api';
export type {
  ApiClient,
  CallOptions,
  Page,
  ListProjectsQuery,
  AppendOpsInput,
  SolveInput,
  RenderInput,
  RenderCaptureInputs,
  RenderPackInput,
  RenderPackShot,
  ExportKind,
} from './api';

// ── Wire schemas ───────────────────────────────────────────────────────────
export * from './schemas';

// ── Live job events ────────────────────────────────────────────────────────
export { subscribeJobEvents, parseSseBuffer } from './sse';
export type { JobEventHandlers, JobEventOptions } from './sse';

// ── Ids ────────────────────────────────────────────────────────────────────
export { newClientOpId, newGroupId, newIdempotencyKey, newUuid } from './ids';

// ── Units and Indian display defaults ──────────────────────────────────────
export * from './units';

// ── Keyboard ───────────────────────────────────────────────────────────────
export {
  KEY_BINDINGS,
  KEY_BINDINGS_BY_COMMAND,
  TOOL_IDS,
  TOOL_SHORTCUT,
  COMMAND_IDS,
  useKeyboardMap,
  matchBinding,
  formatShortcut,
  isTypingTarget,
  isCanvasTarget,
} from './keymap';
export type {
  ToolId,
  CommandId,
  CommandHandler,
  CommandHandlers,
  KeyBinding,
  KeyboardMapOptions,
  BindingScope,
  ModifierSpec,
  MatchOptions,
} from './keymap';
export { useAppShortcuts, defaultCommandHandlers } from './shortcuts';

// ── Sharing ────────────────────────────────────────────────────────────────
export { shareViewerPath, shareViewerUrl, whatsappShareUrl, copyToClipboard } from './share';
export type { WhatsAppMessage } from './share';
