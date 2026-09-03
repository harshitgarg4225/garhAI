/**
 * The typed client for the §11 API surface.
 *
 * This module is a flat catalogue of endpoints and nothing else. Transport
 * concerns — bearer tokens, the single-flight refresh, idempotency keys,
 * timeouts, problem+json → {@link AppError} — all live in `lib/http.ts`;
 * response shapes live in `lib/schemas.ts`. Every method here therefore reads
 * as one line of intent plus one zod parser, and that is the point: an endpoint
 * that needs special handling should make that obvious by being the exception.
 *
 * Conventions:
 *   - Responses are zod-validated at the boundary. A shape we do not recognise
 *     becomes a `malformed_response` AppError telling the user to reload, not a
 *     `TypeError` three components deep.
 *   - Every mutating call carries an `Idempotency-Key` automatically (§11).
 *     The op endpoint additionally carries per-op `clientOpId`s, which is the
 *     finer-grained unit the sequencer actually dedupes on.
 *   - Downloads are never fetched here. `api.sheets.download()` and export jobs
 *     answer a short-lived signed URL the browser opens directly (§13). The one
 *     deliberate exception is `api.sheets.content()`, which returns SVG markup
 *     because the viewer has to put it in the document to zoom and hit-test it.
 *   - Nothing in this file reads `import.meta.env`; configuration comes from
 *     `lib/env.ts`, which is the audited boundary.
 */

import {
  abortError,
  AppError,
  malformedResponseError,
  networkError,
  problemToAppError,
  timeoutError,
} from './errors';
import { http, type HttpClient, type QueryValue } from './http';
import { newClientOpId, newIdempotencyKey } from './ids';
import {
  ackSchema,
  annotationSchema,
  briefParseSchema,
  briefSchema,
  commentSchema,
  complianceSchema,
  copilotDecisionSchema,
  copilotProposeSchema,
  cursorPageSchema,
  deletedSchema,
  dxfImportJobSchema,
  exportJobSchema,
  facadeKitSchema,
  furnitureItemSchema,
  jobSchema,
  materialItemSchema,
  metaSchema,
  modelStateSchema,
  opsAppendSchema,
  opsSinceSchema,
  otpRequestSchema,
  plotSchema,
  projectDetailSchema,
  projectSchema,
  renderJobSchema,
  renderPackSchema,
  renderUploadsSchema,
  sharedProjectSchema,
  downloadSchema,
  drawingPreferencesSchema,
  reviewTraySchema,
  rulepackSummarySchema,
  sessionSchema,
  sheetContentSchema,
  sheetSetSchema,
  projectSubmissionSchema,
  sheetSummarySchema,
  submissionReadinessSchema,
  submissionTemplateListSchema,
  shareLinkSchema,
  versionRestoreSchema,
  versionCompareSchema,
  versionSchema,
  type Brief,
  type BriefParse,
  type Comment,
  type ComplianceReport,
  type CopilotPropose,
  type DxfImportJob,
  type ExportJob,
  type FacadeKit,
  type FurnitureItem,
  type Job,
  type MaterialItem,
  type Meta,
  type ModelState,
  type OpEnvelope,
  type OpsAppendResult,
  type OpsSince,
  type OtpRequest,
  type Plot,
  type Project,
  type ProjectDetail,
  type RenderJob,
  type RenderMode,
  type RenderPack,
  type SharedProject,
  type RenderUploadSlot,
  type RulepackSummary,
  type Session,
  type ShareLink,
  type DownloadLink,
  type DrawingPreferencesResponse,
  type ReviewTrayResponse,
  type ShareSection,
  type SheetAnnotationResponse,
  type SheetContentResponse,
  type SheetSetResponse,
  type ProjectSubmissionResponse,
  type SheetSetSummaryResponse,
  type SubmissionReadinessResponse,
  type SubmissionTemplateListResponse,
  type Version,
  type VersionCompareResponse,
  type VersionRestore,
} from './schemas';

import type { Op } from '@garh/model';
// A VALUE import, not `import type`: `api.sheets.annotations` composes
// `z.array(annotationSchema)` at runtime. It was type-only, which typechecks
// perfectly and throws `z is not defined` on the first call.
import { z } from 'zod';

/** Common per-call options every method accepts. */
export interface CallOptions {
  readonly signal?: AbortSignal | undefined;
}

/** Cursor page as the UI consumes it. */
export interface Page<T> {
  readonly items: T[];
  readonly nextCursor: string | null;
  readonly hasMore: boolean;
}

export interface ListProjectsQuery extends CallOptions {
  readonly cursor?: string | null;
  readonly limit?: number;
  readonly status?: string;
  readonly search?: string;
  readonly includeArchived?: boolean;
}

export interface AppendOpsInput extends CallOptions {
  readonly projectId: string;
  /** Model-core ops. `clientOpId` is filled in here if the caller omitted it. */
  readonly ops: readonly Op[];
  /** The index the client believes is HEAD. `-1` for an empty branch. */
  readonly baseIdx: number;
  readonly groupId?: string;
  readonly source?: 'manual' | 'copilot' | 'solver' | 'system';
  readonly versionBranch?: string | null;
}

export interface SolveInput extends CallOptions {
  readonly projectId: string;
  /** Solver knobs: seed, option count, locked room ids, per-floor regen (§5.7). */
  readonly params?: Record<string, unknown>;
}

/**
 * The three §9 control maps, captured from the ONE live viewport.
 *
 * Either base64 PNG bytes (inline; the server stashes them in Redis for the
 * worker) or already-uploaded object URLs from {@link ApiClient.renders.uploadSlots}.
 * A pack of eight shots must use the URL form — 24 inline PNGs blow the request
 * body cap — and `features/renders/api.ts` falls back to inline only when the
 * browser cannot reach storage directly.
 */
export interface RenderCaptureInputs {
  readonly viewportPng?: string | undefined;
  readonly depthPng?: string | undefined;
  readonly edgesPng?: string | undefined;
  readonly viewportUrl?: string | undefined;
  readonly depthUrl?: string | undefined;
  readonly edgesUrl?: string | undefined;
}

export interface RenderInput extends CallOptions {
  readonly projectId: string;
  readonly designVersionId?: string | null;
  readonly mode: RenderMode;
  readonly preset: string;
  /** Camera state: `{eyeMm, targetMm, fovDeg, storeyId}` — integer mm (§9). */
  readonly view: Record<string, unknown>;
  /** The capture set. Omit only for a provider that needs no control maps. */
  readonly inputs?: RenderCaptureInputs;
  /** Same seed + same design + same preset = the same image (mock provider). */
  readonly seed?: number;
  readonly width?: number;
  readonly height?: number;
  readonly promptExtras?: string;
  readonly params?: Record<string, unknown>;
}

/** One shot of the §9 client pack. */
export interface RenderPackShot {
  readonly slug: string;
  readonly preset: string;
  readonly mode: RenderMode;
  readonly view: Record<string, unknown>;
  readonly inputs: RenderCaptureInputs;
}

export interface RenderPackInput extends CallOptions {
  readonly projectId: string;
  readonly designVersionId?: string | null;
  /** Base seed. Shot *i* renders with `seed + i`, so a pack is reproducible. */
  readonly seed?: number;
  readonly width?: number;
  readonly height?: number;
  readonly shots: readonly RenderPackShot[];
}

export type ExportKind = 'pdf-set' | 'dxf' | 'gltf' | 'png-pack';

export interface UploadDxfInput extends CallOptions {
  readonly projectId: string;
  /** The file bytes, straight from the `<input type="file">`. */
  readonly file: Blob;
  /** Display name; the job carries it so the picker can say what it parsed. */
  readonly filename: string;
}

/** Uploads get a longer leash than JSON calls: 20 MB on a slow uplink is real. */
const UPLOAD_TIMEOUT_MS = 120_000;

/**
 * POST a raw binary body and parse the JSON response.
 *
 * This exists because `HttpClient.request` is deliberately a JSON transport —
 * it stringifies every body — and teaching it about blobs for one endpoint
 * would complicate the path every other call takes. The contract stays
 * identical from the caller's side: same problem+json → {@link AppError}
 * mapping, same timeout/abort semantics, same `Idempotency-Key`, and the
 * bearer comes from the client's own token store (refreshed proactively by
 * `authorization()`, so a 401 here means a genuinely dead session rather than
 * an expired token).
 */
async function postBinary<T>(
  client: HttpClient,
  input: {
    path: string;
    query?: Readonly<Record<string, QueryValue>>;
    body: Blob;
    contentType: string;
    parse: (data: unknown) => T;
    signal?: AbortSignal | undefined;
  },
): Promise<T> {
  const endpoint = `POST ${input.path}`;
  const token = await client.authorization();

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, UPLOAD_TIMEOUT_MS);
  const onExternalAbort = (): void => controller.abort(input.signal?.reason);
  if (input.signal) {
    if (input.signal.aborted) controller.abort(input.signal.reason);
    else input.signal.addEventListener('abort', onExternalAbort, { once: true });
  }

  let response: Response;
  try {
    response = await fetch(client.url(input.path, input.query), {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': input.contentType,
        'Idempotency-Key': newIdempotencyKey(),
        ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      },
      credentials: 'include',
      cache: 'no-store',
      redirect: 'follow',
      referrerPolicy: 'strict-origin-when-cross-origin',
      body: input.body,
      signal: controller.signal,
    });
  } catch (err) {
    if (timedOut) throw timeoutError(endpoint, UPLOAD_TIMEOUT_MS);
    if (input.signal?.aborted) throw abortError(endpoint);
    throw networkError(endpoint, err);
  } finally {
    clearTimeout(timer);
    if (input.signal) input.signal.removeEventListener('abort', onExternalAbort);
  }

  const requestId = response.headers.get('x-request-id');
  const text = await response.text();
  let data: unknown = null;
  if (text !== '') {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = { __nonJsonBody: text.slice(0, 512) };
    }
  }

  if (!response.ok) {
    const retryAfter = response.headers.get('retry-after');
    throw problemToAppError(response.status, data, {
      endpoint,
      requestId,
      retryAfterSeconds: retryAfter === null ? null : Number.parseInt(retryAfter, 10),
    });
  }

  try {
    return input.parse(data);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw malformedResponseError(endpoint, detail, err);
  }
}

/** Turn a zod schema into the `parse` callback `HttpClient` expects. */
function parser<S extends z.ZodTypeAny>(schema: S): (data: unknown) => z.infer<S> {
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return -- ZodTypeAny.parse is `any` by zod's own typing; the schema itself is the runtime proof of the shape.
  return (data: unknown) => schema.parse(data) as z.infer<S>;
}

function pageParser<S extends z.ZodTypeAny>(schema: S): (data: unknown) => Page<z.infer<S>> {
  const page = cursorPageSchema(schema);
  return (data: unknown) => {
    const parsed = page.parse(data);
    return { items: parsed.items, nextCursor: parsed.nextCursor, hasMore: parsed.hasMore };
  };
}

function projectPath(projectId: string, suffix = ''): string {
  return `/projects/${encodeURIComponent(projectId)}${suffix}`;
}

/**
 * Deadline for a live-cursor post. Two seconds, not the 20s default: at ~10Hz
 * a request that has not landed in two seconds is describing a pointer position
 * twenty moves stale, and letting it hang only ties up a connection slot.
 */
const CURSOR_TIMEOUT_MS = 2_000;

// ---------------------------------------------------------------------------
// Usage against the trial allowance (GET /billing/usage)
// ---------------------------------------------------------------------------
// The same numbers the quota gate and the spend cap enforce — the API reads both
// through one repository method, so what the architect sees is what refuses the
// next Generate. Declared beside its binding like the templates schema above.

export const usageLineSchema = z.object({
  kind: z.string(),
  used: z.number().int().nonnegative(),
  /** `null` = unlimited on this plan. */
  allowance: z.number().int().nonnegative().nullable().default(null),
  remaining: z.number().int().nonnegative().nullable().default(null),
});
export const spendBudgetSchema = z.object({
  capUsd: z.string(),
  spentUsd: z.string(),
  remainingUsd: z.string(),
  capMicros: z.number().int(),
  spentMicros: z.number().int(),
  remainingMicros: z.number().int(),
  /** False when the cap is 0 — the budget is reported but nothing refuses on it. */
  enforced: z.boolean().default(true),
});
export const usageSchema = z.object({
  planCode: z.string(),
  effectivePlanCode: z.string(),
  periodStart: z.string(),
  periodEnd: z.string(),
  lines: z.array(usageLineSchema).default([]),
  spend: spendBudgetSchema.nullable().default(null),
});
export type Usage = z.infer<typeof usageSchema>;
export type UsageLine = z.infer<typeof usageLineSchema>;
export type SpendBudget = z.infer<typeof spendBudgetSchema>;

// ---------------------------------------------------------------------------
// Project templates (GET /templates → POST /projects {templateId})
// ---------------------------------------------------------------------------
// Declared here rather than in `lib/schemas.ts` deliberately: the registry card
// is consumed by exactly one flow (the new-project dialog), and keeping its
// schema beside its binding keeps this file's edit self-contained.

const projectTemplateSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  description: z.string().default(''),
  /** Human chip for the picker card ("30 × 40 ft"); empty for the blank template. */
  plotSizeLabel: z.string().default(''),
  tags: z.array(z.string()).default([]),
  /** 'plan' carries a solved, compliant two-floor plan; 'starter' only a plot + brief. */
  kind: z.enum(['blank', 'starter', 'plan']).default('starter'),
  /** A data: URL of the plan drawn through the sheet renderer — an <img>, never a document. */
  previewUrl: z.string().nullable().default(null),
});
const projectTemplatesSchema = z.object({
  templates: z.array(projectTemplateSchema).default([]),
});

/** One starter template card, as `GET /templates` lists them (picker order). */
export type ProjectTemplate = z.infer<typeof projectTemplateSchema>;

// ---------------------------------------------------------------------------
// Tracing underlay (§ Rayon parity — upload a plan image and draw over it)
// ---------------------------------------------------------------------------
// Declared here for the same reason `projectTemplateSchema` is: exactly one
// feature consumes it (`features/underlay`), and keeping the schema beside its
// four bindings keeps the edit self-contained.
//
// The shape is `UnderlayOut` in `apps/api/garh_api/schemas/underlay.py`, and
// the split between float and integer there is deliberate and load-bearing:
// `mmPerPx` is a raster display scale (a float, `gt=0`), while the origin is
// integer millimetres like every other position in the product. A patch that
// sends `originXMm: 1200.5` is a 422 from `Mm`'s StrictInt, so every caller
// rounds through `roundMm` before it gets here.

const underlaySchema = z.object({
  objectKey: z.string().min(1),
  /** Presigned GET, minted per response — §13 caps it at ~10 minutes. */
  imageUrl: z.string().min(1),
  widthPx: z.number().int().positive(),
  heightPx: z.number().int().positive(),
  mmPerPx: z.number().positive(),
  originXMm: z.number().int(),
  originYMm: z.number().int(),
  opacity: z.number().min(0).max(1),
  locked: z.boolean(),
  visible: z.boolean(),
});

/** The one underlay of a project, with a freshly signed image URL. */
export type Underlay = z.infer<typeof underlaySchema>;

/**
 * A partial update. Every member optional, and NOTHING else may be sent —
 * `UnderlayPatchIn` is `extra="forbid"`, so slipping `widthPx` in here (or a
 * whole record round-tripped from a GET) is a 422, by design: the image facts
 * come from real uploaded bytes, never from a JSON claim.
 */
export interface UnderlayPatch {
  readonly mmPerPx?: number;
  readonly originXMm?: number;
  readonly originYMm?: number;
  readonly opacity?: number;
  readonly locked?: boolean;
  readonly visible?: boolean;
}

export interface UploadUnderlayInput extends CallOptions {
  readonly projectId: string;
  /** The image bytes, straight from the `<input type="file">`. */
  readonly file: Blob;
  /**
   * Override the declared content type. Normally left alone: the file's own
   * `type` is sent, and the server sniffs the magic bytes anyway — the header
   * is a claim, the bytes are the fact.
   */
  readonly contentType?: string;
}

/**
 * The API's own 404 code for "this project has no underlay yet".
 *
 * The route gives it a code of its own precisely so the client can tell that
 * from "no such project" without string-matching a message. For the canvas it
 * is a normal state (render the upload affordance), which is why
 * {@link ApiClient.underlay.get} answers `null` rather than throwing.
 */
// ---------------------------------------------------------------------------
// The project's inspiration board (§11 — the client's pictures, and what each is for)
// ---------------------------------------------------------------------------
// Declared beside the underlay for the same reason: one feature consumes it
// (`features/references`), and the schema belongs next to its bindings.
//
// The vocabulary below is NOT a free-text field, and that is load-bearing. It is
// the same tuple as `services/render/references.SCOPES` and
// `garh_api.models.REFERENCE_SCOPES` — a Python-side gate
// (`test_reference_vocabulary.py`) keeps those two equal, and `references.test.ts`
// keeps this copy equal to the OpenAPI enum. A scope the render side cannot read
// is a picture that steers nothing, forever, with no message.

export const REFERENCE_SCOPES = [
  'whole-house',
  'facade',
  'interior',
  'kitchen',
  'living',
  'bedroom',
  'bathroom',
  'landscape',
  'material',
] as const;
export type ReferenceScope = (typeof REFERENCE_SCOPES)[number];

/** How hard to push a reference. `avoid` is the opposite of `guide`, not a weaker one. */
export const REFERENCE_INTENTS = ['match', 'guide', 'avoid'] as const;
export type ReferenceIntent = (typeof REFERENCE_INTENTS)[number];

const referenceSchema = z.object({
  id: z.string().min(1),
  projectId: z.string().min(1),
  label: z.string(),
  scope: z.enum(REFERENCE_SCOPES),
  /** What to take from it, in the architect's words. Empty = not yet answered. */
  why: z.string(),
  /** What to leave. Steers the negative prompt. */
  ignore: z.string(),
  intent: z.enum(REFERENCE_INTENTS),
  position: z.number().int(),
  filename: z.string(),
  widthPx: z.number().int(),
  heightPx: z.number().int(),
  /** Presigned GET, minted per response — §13 caps it at ~10 minutes. */
  imageUrl: z.string().nullable().optional(),
  createdAt: z.string().nullable().optional(),
});

/** One picture on the board, with the architect's four answers. */
export type ProjectReference = z.infer<typeof referenceSchema>;

const referenceListSchema = z.object({ references: z.array(referenceSchema) });

const referenceConflictSchema = z.object({
  /** `competing` | `out-of-view` | `unusable` */
  kind: z.string(),
  referenceIds: z.array(z.string()),
  question: z.string(),
  /** What happens if the architect does nothing. Always present, by design. */
  default: z.string(),
});
export type ReferenceConflict = z.infer<typeof referenceConflictSchema>;

const referenceReviewSchema = z.object({
  projectId: z.string(),
  preset: z.string(),
  applies: z.array(referenceSchema),
  notInView: z.array(referenceSchema),
  conflicts: z.array(referenceConflictSchema),
  positive: z.string(),
  negative: z.string(),
});

/** What the board contributes to one preset, and what to settle first. */
export type ReferenceReview = z.infer<typeof referenceReviewSchema>;

/** A partial annotation. Only the members you pass change. */
export interface ReferencePatch {
  readonly label?: string;
  readonly scope?: ReferenceScope;
  readonly why?: string;
  readonly ignore?: string;
  readonly intent?: ReferenceIntent;
  readonly position?: number;
}

export interface AddReferenceInput extends CallOptions {
  readonly projectId: string;
  readonly file: Blob;
  readonly contentType?: string;
}

export const NO_UNDERLAY_CODE = 'no_underlay';

/** True for the one 404 that means "nothing uploaded yet", not "went wrong". */
function isNoUnderlay(error: unknown): boolean {
  return error instanceof AppError && error.status === 404 && error.code === NO_UNDERLAY_CODE;
}

/**
 * The content type to declare for an upload.
 *
 * `Blob.type` is empty for a file the OS could not classify, and an empty
 * `Content-Type` header would let the server's multipart branch see `""` and
 * fall through to raw bytes — which is what we want anyway. Naming the octet
 * stream explicitly says "these are bytes, sniff them" rather than leaving the
 * header to whatever the fetch implementation invents.
 */
function uploadContentType(file: Blob, override?: string): string {
  if (override !== undefined && override !== '') return override;
  return file.type === '' ? 'application/octet-stream' : file.type;
}

/**
 * Model-core ops → the wire envelope. Every op leaves with a `clientOpId`
 * because an op without one cannot be deduplicated, and a retry after a
 * timeout would then apply the same wall twice.
 */
export function toOpEnvelopes(ops: readonly Op[], groupId?: string): OpEnvelope[] {
  return ops.map((op) => {
    const meta = op as { clientOpId?: string; groupId?: string };
    const group = groupId ?? meta.groupId;
    return {
      type: op.type,
      payload: op.payload as unknown as Record<string, unknown>,
      clientOpId: meta.clientOpId ?? newClientOpId(),
      ...(group === undefined ? {} : { groupId: group }),
    };
  });
}

/**
 * Build the client. The app uses the {@link api} singleton; tests build their
 * own over a stub `HttpClient` so no global state leaks between cases.
 */
export function createApiClient(client: HttpClient = http) {
  return {
    /** The underlying transport. Exposed for the SSE reader and for tests. */
    http: client,

    // ── Bootstrap ──────────────────────────────────────────────────────────
    meta: {
      /** Flags, provider names and limits — fetched before the shell renders. */
      get: (opts: CallOptions = {}): Promise<Meta> =>
        client.request({ path: '/meta', auth: 'none', parse: parser(metaSchema), ...opts }),
    },

    // ── Auth (§11 /auth/otp, /auth/verify; §13 rotation + logout-all) ──────
    auth: {
      /**
       * Ask for a sign-in code. The response is identical for a known and an
       * unknown address — do not build UI that implies otherwise.
       */
      requestOtp: (input: { email: string }, opts: CallOptions = {}): Promise<OtpRequest> =>
        client.request({
          method: 'POST',
          path: '/auth/otp',
          auth: 'none',
          body: { email: input.email },
          parse: parser(otpRequestSchema),
          ...opts,
        }),

      /**
       * Exchange an emailed code for a session.
       *
       * `{ email, code }` and nothing else: `VerifyRequest` is declared with
       * `extra="forbid"`, so sending `name`/`firmName` here is a 422 "Extra
       * inputs are not permitted". Creating a firm is `signup` below, which ends
       * by issuing a code that this call then verifies.
       */
      verifyOtp: (
        input: { email: string; code: string },
        opts: CallOptions = {},
      ): Promise<Session> =>
        client.request({
          method: 'POST',
          path: '/auth/verify',
          auth: 'none',
          body: { email: input.email, code: input.code },
          parse: parser(sessionSchema),
          ...opts,
        }),

      /**
       * Create a firm and its first admin, then send that admin a code.
       *
       * Returns an OTP challenge, NOT a session — a new user still has to prove
       * they own the address, so signup ends exactly where sign-in does and the
       * caller falls into `verifyOtp`. This is the one auth route that admits an
       * address is taken (409 `email_already_registered`); sign-in stays
       * deliberately non-enumerable.
       */
      signup: (
        input: { firmName: string; name: string; email: string; coaNumber?: string },
        opts: CallOptions = {},
      ): Promise<OtpRequest> =>
        client.request({
          method: 'POST',
          path: '/auth/signup',
          auth: 'none',
          body: {
            firmName: input.firmName,
            name: input.name,
            email: input.email,
            ...(input.coaNumber === undefined ? {} : { coaNumber: input.coaNumber }),
          },
          parse: parser(otpRequestSchema),
          ...opts,
        }),

      /**
       * Exchange the refresh credential for a new access token.
       *
       * Ordinary code should not call this: `HttpClient` refreshes on demand,
       * single-flight, and calling it by hand risks the rotation-reuse
       * cascade the transport exists to prevent. It is here for the session
       * store's explicit "restore my session on boot" step.
       */
      refresh: (opts: CallOptions = {}): Promise<Session> =>
        client.request({
          method: 'POST',
          path: '/auth/refresh',
          auth: 'none',
          parse: parser(sessionSchema),
          ...opts,
        }),

      /**
       * Ends this session, or every session when `everywhere` is set.
       *
       * Two DIFFERENT routes, not a flag: `POST /auth/logout` declares no body
       * parameter at all (FastAPI discards one) and only revokes the current
       * refresh family. `POST /auth/logout-all` bumps the user's token
       * generation, which is what invalidates access tokens still inside their
       * 15-minute window. Posting `{everywhere: true}` to `/auth/logout` made
       * "sign out on all devices" a silent no-op — exactly the wrong outcome
       * after a suspected compromise.
       *
       * `/auth/logout-all` needs a live access token, so it keeps the default
       * `auth` mode.
       */
      logout: (input: { everywhere?: boolean } = {}, opts: CallOptions = {}): Promise<unknown> =>
        client.request({
          method: 'POST',
          path: input.everywhere === true ? '/auth/logout-all' : '/auth/logout',
          parse: parser(ackSchema),
          ...opts,
        }),

      /** Who am I — used to re-hydrate the shell after a refresh-only boot. */
      me: (opts: CallOptions = {}): Promise<Session> =>
        client.request({ path: '/auth/me', parse: parser(sessionSchema), ...opts }),
    },

    // ── Projects ───────────────────────────────────────────────────────────
    projects: {
      list: (query: ListProjectsQuery = {}): Promise<Page<Project>> => {
        const q: Record<string, QueryValue> = {
          cursor: query.cursor ?? undefined,
          limit: query.limit,
          status: query.status,
          q: query.search,
          includeArchived: query.includeArchived,
        };
        return client.request({
          path: '/projects',
          query: q,
          parse: pageParser(projectSchema),
          ...(query.signal === undefined ? {} : { signal: query.signal }),
        });
      },

      create: (
        input: {
          name: string;
          units?: 'ft-in' | 'm';
          cityPack?: string | null;
          /** A template id from `api.templates.list()`; omitted/'blank' = empty project. */
          templateId?: string;
        },
        opts: CallOptions = {},
      ): Promise<Project> =>
        client.request({
          method: 'POST',
          path: '/projects',
          body: input,
          parse: parser(projectSchema),
          ...opts,
        }),

      /** One round trip for the project shell: project + plot + brief + head. */
      get: (projectId: string, opts: CallOptions = {}): Promise<ProjectDetail> =>
        client.request({
          path: projectPath(projectId),
          parse: parser(projectDetailSchema),
          ...opts,
        }),

      update: (
        projectId: string,
        patch: {
          name?: string;
          status?: string;
          units?: 'ft-in' | 'm';
          cityPack?: string | null;
          architectOfRecord?: string | null;
          clearArchitectOfRecord?: boolean;
        },
        opts: CallOptions = {},
      ): Promise<Project> =>
        client.request({
          method: 'PATCH',
          path: projectPath(projectId),
          body: patch,
          parse: parser(projectSchema),
          ...opts,
        }),

      remove: (projectId: string, opts: CallOptions = {}): Promise<{ id: string }> =>
        client.request({
          method: 'DELETE',
          path: projectPath(projectId),
          parse: parser(deletedSchema),
          ...opts,
        }),
    },

    // ── Trial usage: generations used, money left ────────────────────────
    billing: {
      /** Used vs allowed per metered kind, plus the spend budget, for the firm. */
      usage: (opts: CallOptions = {}): Promise<Usage> =>
        client.request({ path: '/billing/usage', parse: parser(usageSchema), ...opts }),
    },

    // ── Project templates (Rayon-parity starters, applied server-side) ─────
    templates: {
      /** The starter-template registry, picker order ("Blank" first). */
      list: (opts: CallOptions = {}): Promise<ProjectTemplate[]> =>
        client.request({
          path: '/templates',
          parse: (data: unknown) => projectTemplatesSchema.parse(data).templates,
          ...opts,
        }),
    },

    // ── Plot & brief ───────────────────────────────────────────────────────
    plot: {
      put: (
        projectId: string,
        input: {
          boundary?: { x: number; y: number }[];
          northDeg?: number;
          roads?: { edgeIndex: number; widthMm: number | null }[];
          regProfile?: Record<string, unknown>;
          source?: string;
        },
        opts: CallOptions = {},
      ): Promise<Plot> =>
        client.request({
          method: 'PUT',
          path: projectPath(projectId, '/plot'),
          body: input,
          parse: parser(plotSchema),
          ...opts,
        }),
    },

    brief: {
      put: (
        projectId: string,
        input: {
          data?: Record<string, unknown>;
          merge?: boolean;
          vastuMode?: 'off' | 'advisory' | 'strict';
          completeness?: number;
        },
        opts: CallOptions = {},
      ): Promise<Brief> =>
        client.request({
          method: 'PUT',
          path: projectPath(projectId, '/brief'),
          body: input,
          parse: parser(briefSchema),
          ...opts,
        }),

      /**
       * Free text → structured brief fields + an assumption for every value the
       * model had to invent (golden rule 4).
       *
       * A parse is a pure suggestion: the server never applies it (`applied` is
       * always false and `brief` always null). After review, the client
       * dispatches a `brief.update` op through the model store — the same
       * undoable path as typing into the form. `apply` remains in the signature
       * for wire compatibility only; the server ignores it.
       */
      parse: (
        projectId: string,
        input: { text: string; knownFields?: Record<string, unknown>; apply?: boolean },
        opts: CallOptions = {},
      ): Promise<BriefParse> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/brief/parse'),
          body: input,
          // The LLM call is the slow part; the default 20s deadline is too tight.
          timeoutMs: 60_000,
          parse: parser(briefParseSchema),
          ...opts,
        }),
    },

    // ── DXF boundary import (Phase 2 F1; §13 upload rules) ─────────────────
    imports: {
      /**
       * Upload a DXF and queue the boundary extraction. Answers 202 with a job
       * — the layer candidates do not exist yet. Poll {@link imports.job} (or
       * stream the job's `eventsUrl`) until it is terminal; on success the
       * job's `result.layers` holds per-layer closed rings ready to become a
       * `plot.set_boundary` op with `source: "dxf"`.
       *
       * The body goes up raw as `application/dxf` (not multipart) with the
       * display name in the query — the simpler of the two shapes the server
       * accepts. Limits are the server's to enforce: too large → 413, not a
       * DXF → 415, both as problem+json with a next action.
       */
      uploadDxf: (input: UploadDxfInput): Promise<DxfImportJob> =>
        postBinary(client, {
          path: projectPath(input.projectId, '/import/dxf'),
          query: { filename: input.filename },
          body: input.file,
          contentType: 'application/dxf',
          parse: parser(dxfImportJobSchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      /**
       * Import job state — the poll target for the layer picker. The GET also
       * pins a succeeded job's result onto the record server-side, so the
       * result outlives the 1h event backlog inside the record's 24h TTL.
       */
      job: (jobId: string, opts: CallOptions = {}): Promise<DxfImportJob> =>
        client.request({
          path: `/import-jobs/${encodeURIComponent(jobId)}`,
          parse: parser(dxfImportJobSchema),
          ...opts,
        }),
    },

    // ── Tracing underlay (the "scan a plan and draw over it" aid) ──────────
    // Plain project-scoped CRUD: no job, no op log, no undo. The underlay is
    // deliberately NOT model state — it is a view aid attached to a project,
    // so it never enters the op sequence and never changes a state hash.
    underlay: {
      /**
       * The project's underlay, or `null` when it has none.
       *
       * `null` rather than a throw is the whole point of the server's
       * `no_underlay` code: "not uploaded yet" is the state every project
       * starts in, and a canvas that treated it as an error would render a
       * failure banner on every new project. Any OTHER 404 (a project that is
       * not yours, or does not exist) still throws — that one IS a bail-out.
       *
       * The `imageUrl` on the answer is signed fresh per response, so this is
       * also the "my texture URL expired" refresh call.
       */
      get: async (projectId: string, opts: CallOptions = {}): Promise<Underlay | null> => {
        try {
          return await client.request({
            path: projectPath(projectId, '/underlay'),
            parse: parser(underlaySchema),
            ...opts,
          });
        } catch (err) {
          if (isNoUnderlay(err)) return null;
          throw err;
        }
      },

      /**
       * Upload (or replace) the image. Answers the full record — there is no
       * job here, the upload IS the result and its `imageUrl` is immediately
       * loadable.
       *
       * Raw bytes, not multipart: the server accepts both and sniffs the magic
       * bytes either way, and the raw form is one fewer encoding layer between
       * the file the architect chose and the bytes that get stored. Limits are
       * the server's to enforce — too large → 413, not a PNG/JPEG → 415, over
       * the edge cap → 422, each as problem+json with a next action.
       */
      upload: (input: UploadUnderlayInput): Promise<Underlay> =>
        postBinary(client, {
          path: projectPath(input.projectId, '/underlay/image'),
          body: input.file,
          contentType: uploadContentType(input.file, input.contentType),
          parse: parser(underlaySchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      /**
       * Adjust calibration or view state. Only the members you pass change.
       *
       * Positions must already be integer millimetres (see {@link UnderlayPatch}).
       */
      patch: (projectId: string, patch: UnderlayPatch, opts: CallOptions = {}): Promise<Underlay> =>
        client.request({
          method: 'PATCH',
          path: projectPath(projectId, '/underlay'),
          body: patch,
          parse: parser(underlaySchema),
          ...opts,
        }),

      /** Remove the underlay. The stored image goes with it, best-effort. */
      remove: (projectId: string, opts: CallOptions = {}): Promise<{ ok: boolean }> =>
        client.request({
          method: 'DELETE',
          path: projectPath(projectId, '/underlay'),
          parse: parser(ackSchema),
          ...opts,
        }),
    },

    // ── The inspiration board (§11: the client's pictures, and what each is for) ──
    // Project-scoped CRUD like the underlay, and for the same reason: a reference
    // steers a render's prompt and touches no geometry, so it never enters the op
    // sequence and never changes a state hash.
    references: {
      /** The board in the architect's own order — the only ranking that exists. */
      list: (projectId: string, opts: CallOptions = {}): Promise<ProjectReference[]> =>
        client
          .request({
            path: projectPath(projectId, '/references'),
            parse: parser(referenceListSchema),
            ...opts,
          })
          .then((page) => page.references),

      /**
       * Pin a picture. It arrives UNANNOTATED on purpose — the architect says what
       * it is for in a second step, and until they do, `review` asks them to. A
       * scope guessed from a filename would be wrong silently.
       */
      add: (input: AddReferenceInput): Promise<ProjectReference> =>
        postBinary(client, {
          path: projectPath(input.projectId, '/references'),
          body: input.file,
          contentType: uploadContentType(input.file, input.contentType),
          parse: parser(referenceSchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      /** Answer one or more of the four questions. Absent members are left alone. */
      annotate: (
        projectId: string,
        referenceId: string,
        patch: ReferencePatch,
        opts: CallOptions = {},
      ): Promise<ProjectReference> =>
        client.request({
          method: 'PATCH',
          path: projectPath(projectId, `/references/${referenceId}`),
          body: patch,
          parse: parser(referenceSchema),
          ...opts,
        }),

      /** Take it off the board. The stored image goes with it, best-effort. */
      remove: (
        projectId: string,
        referenceId: string,
        opts: CallOptions = {},
      ): Promise<{ ok: boolean }> =>
        client.request({
          method: 'DELETE',
          path: projectPath(projectId, `/references/${referenceId}`),
          parse: parser(ackSchema),
          ...opts,
        }),

      /**
       * What the board contributes to one render, and what to settle first.
       *
       * Called BEFORE rendering, not after: a render is a thing a client is shown,
       * and "which kitchen did you mean" is a question with a real answer. It also
       * returns the exact prompt fragments the model will receive, so the
       * instruction the architect wrote and the instruction the model gets are
       * visibly the same thing.
       */
      review: (
        projectId: string,
        preset: string,
        opts: CallOptions = {},
      ): Promise<ReferenceReview> =>
        client.request({
          path: projectPath(projectId, '/references/review'),
          query: { preset },
          parse: parser(referenceReviewSchema),
          ...opts,
        }),
    },

    // ── The op log (§4, §11) ───────────────────────────────────────────────
    ops: {
      /**
       * Append a batch. A 409 comes back as an {@link OpConflictError} carrying
       * the server's real `headIdx`; a 422 as an `OpRejectionError` carrying the
       * issues. The model store branches on exactly those two.
       */
      append: (input: AppendOpsInput): Promise<OpsAppendResult> =>
        client.request({
          method: 'POST',
          path: projectPath(input.projectId, '/ops'),
          body: {
            ops: toOpEnvelopes(input.ops, input.groupId),
            baseIdx: input.baseIdx,
            source: input.source ?? 'manual',
            ...(input.groupId === undefined ? {} : { groupId: input.groupId }),
            ...(input.versionBranch == null ? {} : { versionBranch: input.versionBranch }),
          },
          parse: parser(opsAppendSchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      /** Incremental sync. `since` is exclusive: pass your current `baseIdx`. */
      since: (
        projectId: string,
        since: number,
        options: CallOptions & { limit?: number; versionBranch?: string | null } = {},
      ): Promise<OpsSince> =>
        client.request({
          path: projectPath(projectId, '/ops'),
          query: {
            since,
            limit: options.limit,
            versionBranch: options.versionBranch ?? undefined,
          },
          parse: parser(opsSinceSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /** Snapshot + tail. The §15 "<2s to interactive" path. */
      model: (
        projectId: string,
        options: CallOptions & { version?: string | null } = {},
      ): Promise<ModelState> =>
        client.request({
          path: projectPath(projectId, '/model'),
          query: { version: options.version ?? undefined },
          parse: parser(modelStateSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),
    },

    // ── Versions (§F10) ────────────────────────────────────────────────────
    versions: {
      list: (
        projectId: string,
        options: CallOptions & { cursor?: string | null; limit?: number } = {},
      ): Promise<Page<Version>> =>
        client.request({
          path: projectPath(projectId, '/versions'),
          query: { cursor: options.cursor ?? undefined, limit: options.limit },
          parse: pageParser(versionSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      create: (
        projectId: string,
        input: { name: string },
        opts: CallOptions = {},
      ): Promise<Version> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/versions'),
          body: input,
          parse: parser(versionSchema),
          ...opts,
        }),

      /**
       * What changed between two versions (C-8).
       *
       * Both ids are required. Defaulting the missing side to "latest" would be a
       * compare whose meaning changes every time someone else edits the project.
       */
      compare: (
        projectId: string,
        a: string,
        b: string,
        options: CallOptions = {},
      ): Promise<VersionCompareResponse> =>
        client.request({
          path: projectPath(projectId, '/versions/compare'),
          query: { a, b },
          parse: parser(versionCompareSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /**
       * Restore forks a new branch rather than deleting history. The client must
       * re-hydrate the model afterwards — the returned `versionBranch` and
       * `headIdx` are the new base.
       */
      restore: (
        projectId: string,
        versionId: string,
        opts: CallOptions = {},
      ): Promise<VersionRestore> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, `/versions/${encodeURIComponent(versionId)}/restore`),
          parse: parser(versionRestoreSchema),
          ...opts,
        }),
    },

    // ── Solver (§5) ────────────────────────────────────────────────────────
    solver: {
      start: (input: SolveInput): Promise<Job> =>
        client.request({
          method: 'POST',
          path: projectPath(input.projectId, '/solve'),
          body: { params: input.params ?? {} },
          parse: parser(jobSchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      get: (jobId: string, opts: CallOptions = {}): Promise<Job> =>
        client.request({
          path: `/solver-jobs/${encodeURIComponent(jobId)}`,
          parse: parser(jobSchema),
          ...opts,
        }),

      cancel: (jobId: string, opts: CallOptions = {}): Promise<Job> =>
        client.request({
          method: 'POST',
          path: `/solver-jobs/${encodeURIComponent(jobId)}/cancel`,
          parse: parser(jobSchema),
          ...opts,
        }),
    },

    // ── Renders (§9) ───────────────────────────────────────────────────────
    /**
     * Every method here parses with `renderJobSchema`, NOT `jobSchema`. The
     * server's render row carries no `kind` discriminator, so `jobSchema` used
     * to label every render `kind: 'solver'` and the jobs store then opened its
     * SSE stream against the solver endpoint. See the schema's own note.
     */
    renders: {
      start: (input: RenderInput): Promise<RenderJob> =>
        client.request({
          method: 'POST',
          path: projectPath(input.projectId, '/renders'),
          body: {
            mode: input.mode,
            preset: input.preset,
            view: input.view,
            inputs: input.inputs ?? {},
            promptExtras: input.promptExtras ?? '',
            ...(input.seed === undefined ? {} : { seed: input.seed }),
            ...(input.width === undefined ? {} : { width: input.width }),
            ...(input.height === undefined ? {} : { height: input.height }),
            ...(input.designVersionId == null ? {} : { designVersionId: input.designVersionId }),
          },
          // Uploading a viewport + depth + edge maps is not a 20s operation on
          // a slow connection.
          timeoutMs: 120_000,
          parse: parser(renderJobSchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      get: (jobId: string, opts: CallOptions = {}): Promise<RenderJob> =>
        client.request({
          path: `/render-jobs/${encodeURIComponent(jobId)}`,
          parse: parser(renderJobSchema),
          ...opts,
        }),

      /**
       * What is running right now (`GET /projects/:id/renders`). The jobs store
       * adopts these on entering a project; the gallery uses {@link history},
       * whose image links are re-presigned per request.
       */
      list: (
        projectId: string,
        options: CallOptions & { cursor?: string | null; limit?: number } = {},
      ): Promise<Page<RenderJob>> =>
        client.request({
          path: projectPath(projectId, '/renders'),
          query: { cursor: options.cursor ?? undefined, limit: options.limit },
          parse: pageParser(renderJobSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /**
       * The §9 gallery: newest first, `stale` straight from the server (the op
       * pipeline flips it on every visual edit — never guessed client-side),
       * and `outputUrl` re-signed on each request because stored presigned GETs
       * expire in ten minutes (§13).
       */
      history: (
        projectId: string,
        options: CallOptions & { cursor?: string | null; limit?: number } = {},
      ): Promise<Page<RenderJob>> =>
        client.request({
          path: projectPath(projectId, '/render-history'),
          query: { cursor: options.cursor ?? undefined, limit: options.limit },
          parse: pageParser(renderJobSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /** Presigned PUT/GET pairs so captures go browser→storage, not through us. */
      uploadSlots: (
        projectId: string,
        count: number,
        opts: CallOptions = {},
      ): Promise<RenderUploadSlot[]> =>
        client
          .request({
            method: 'POST',
            path: projectPath(projectId, '/renders/uploads'),
            body: { count },
            parse: parser(renderUploadsSchema),
            ...opts,
          })
          .then((out) => out.slots),

      /** The eight-shot client pack, as ONE job group sharing a `packId` (§9). */
      clientPack: (input: RenderPackInput): Promise<RenderPack> =>
        client.request({
          method: 'POST',
          path: projectPath(input.projectId, '/renders/client-pack'),
          body: {
            shots: input.shots,
            ...(input.seed === undefined ? {} : { seed: input.seed }),
            ...(input.width === undefined ? {} : { width: input.width }),
            ...(input.height === undefined ? {} : { height: input.height }),
            ...(input.designVersionId == null ? {} : { designVersionId: input.designVersionId }),
          },
          // Eight capture sets is the heaviest upload in the product.
          timeoutMs: 300_000,
          parse: parser(renderPackSchema),
          ...(input.signal === undefined ? {} : { signal: input.signal }),
        }),

      pack: (projectId: string, packId: string, opts: CallOptions = {}): Promise<RenderPack> =>
        client.request({
          path: projectPath(projectId, `/render-packs/${encodeURIComponent(packId)}`),
          parse: parser(renderPackSchema),
          ...opts,
        }),

      /**
       * Zip a finished pack. Answers an ordinary export job, so the download
       * rides the existing signed `/downloads/{token}` path rather than a
       * second, render-only download mechanism. 409 until every shot succeeds.
       */
      archivePack: (
        projectId: string,
        packId: string,
        opts: CallOptions = {},
      ): Promise<ExportJob> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, `/render-packs/${encodeURIComponent(packId)}/archive`),
          timeoutMs: 120_000,
          parse: parser(exportJobSchema),
          ...opts,
        }),

      cancel: (jobId: string, opts: CallOptions = {}): Promise<RenderJob> =>
        client.request({
          method: 'POST',
          path: `/render-jobs/${encodeURIComponent(jobId)}/cancel`,
          parse: parser(renderJobSchema),
          ...opts,
        }),
    },

    // ── Copilot (§10) ──────────────────────────────────────────────────────
    /**
     * Natural language in, a previewable op diff out — and NOTHING else.
     *
     * `propose` never writes. Applying is the client's separate act: dispatch
     * the returned ops through the model store, which appends them to
     * `POST /projects/:id/ops` with this proposal's `groupId` (one undo group)
     * and `source: 'copilot'` at `baseIdx`. Same sequencer, same validation,
     * same single-writer lock as a hand edit — the §13 containment boundary is
     * that there is no other door.
     */
    copilot: {
      /**
       * The client sends the command and what the architect has open. It does
       * NOT send the model document: the server builds its own PII-free summary
       * (§13), and a client that shipped the doc up would bypass that.
       *
       * Note the field is `text` on the wire (`CopilotCommandIn`), not
       * `command` — sending `command` is a 422 `extra_forbidden`.
       */
      propose: (
        projectId: string,
        input: {
          command: string;
          activeStoreyId?: string | null;
          selectionIds?: readonly string[];
          versionBranch?: string | null;
        },
        opts: CallOptions = {},
      ): Promise<CopilotPropose> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/copilot'),
          body: {
            text: input.command,
            ...(input.activeStoreyId == null ? {} : { activeStoreyId: input.activeStoreyId }),
            ...(input.selectionIds === undefined || input.selectionIds.length === 0
              ? {}
              : { selectionIds: [...input.selectionIds].slice(0, 20) }),
            ...(input.versionBranch == null ? {} : { versionBranch: input.versionBranch }),
          },
          // The LLM round trip is I/O-bound; the default 20s deadline is too tight.
          timeoutMs: 60_000,
          parse: parser(copilotProposeSchema),
          ...opts,
        }),

      /**
       * Record what the human chose — the second half of §10's eval log.
       *
       * Log-only: it writes no ops, spends no credits and touches no state. The
       * apply itself already went through `POST /ops` (or didn't, which is the
       * whole point of reject). Fire-and-forget at the call site: a failure to
       * log must never look like a failure to apply.
       */
      decision: (
        projectId: string,
        input: {
          command: string;
          outcome: 'applied' | 'rejected';
          opsCount: number;
          groupId?: string | null;
          intent?: string | null;
        },
        opts: CallOptions = {},
      ): Promise<{ logged: boolean }> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/copilot/decision'),
          body: {
            command: input.command,
            outcome: input.outcome,
            opsCount: input.opsCount,
            ...(input.groupId == null ? {} : { groupId: input.groupId }),
            ...(input.intent == null ? {} : { intent: input.intent.slice(0, 300) }),
          },
          parse: parser(copilotDecisionSchema),
          ...opts,
        }),
    },

    // ── Compliance (§6) ────────────────────────────────────────────────────
    compliance: {
      get: (
        projectId: string,
        options: CallOptions & { version?: string | null } = {},
      ): Promise<ComplianceReport> =>
        client.request({
          path: projectPath(projectId, '/compliance'),
          query: { version: options.version ?? undefined },
          parse: parser(complianceSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),
    },

    // ── Sheets & exports (§7, §F9) ─────────────────────────────────────────
    sheets: {
      /**
       * Queue a sheet-set generation. Answers the CURRENT set plus the new job —
       * `SheetSetOut`, not a bare job, so the tab can keep showing yesterday's sheets
       * while today's are drawn instead of blanking the grid.
       */
      generate: (
        projectId: string,
        input: {
          designVersionId?: string | null;
          kinds?: string[];
          scaleDenominator?: number;
          sheetSize?: string;
          dimToJamb?: boolean | null;
          titleBlock?: Record<string, unknown> | null;
          revisions?: readonly Record<string, unknown>[] | null;
          formats?: string[];
        } = {},
        opts: CallOptions = {},
      ): Promise<SheetSetResponse> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/sheets/generate'),
          body: input,
          parse: parser(sheetSetSchema),
          ...opts,
        }),

      /**
       * The generated set for a version.
       *
       * NOT a cursor page. The server answers `SheetSetOut`
       * (`{projectId, designVersionId, sheets[], generatedAt}`); this used to be parsed
       * with `pageParser`, which threw on every real response and left the Sheets tab
       * permanently empty for projects that had a full set.
       */
      list: (
        projectId: string,
        options: CallOptions & { version?: string | null } = {},
      ): Promise<SheetSetResponse> =>
        client.request({
          path: projectPath(projectId, '/sheets'),
          query: { version: options.version ?? undefined },
          parse: parser(sheetSetSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /** Set-level facts: chain count, the §7 sum invariant, skipped sheets, notes. */
      summary: (
        projectId: string,
        options: CallOptions & { version?: string | null } = {},
      ): Promise<SheetSetSummaryResponse> =>
        client.request({
          path: projectPath(projectId, '/sheets/summary'),
          query: { version: options.version ?? undefined },
          parse: parser(sheetSummarySchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /**
       * One sheet's sanitised SVG, inline.
       *
       * The viewer needs the markup in the document to pan, zoom and hit-test, so this
       * one endpoint returns bytes rather than a signed URL — see the server's
       * `get_sheet_content` docstring for why that is the right trade for a 20 kB
       * drawing and the wrong one for a 40 MB PDF.
       */
      content: (
        projectId: string,
        sheetId: string,
        options: CallOptions = {},
      ): Promise<SheetContentResponse> =>
        client.request({
          path: projectPath(projectId, `/sheets/${encodeURIComponent(sheetId)}/content`),
          parse: parser(sheetContentSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /** The D13 review tray: annotations whose anchor did not survive. */
      reviewTray: (
        projectId: string,
        options: CallOptions & { reconcile?: boolean } = {},
      ): Promise<ReviewTrayResponse> =>
        client.request({
          path: projectPath(projectId, '/sheets/review-tray'),
          query: { reconcile: options.reconcile === false ? 'false' : undefined },
          parse: parser(reviewTraySchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      annotations: (
        projectId: string,
        sheetId: string,
        options: CallOptions = {},
      ): Promise<SheetAnnotationResponse[]> =>
        client.request({
          path: projectPath(projectId, `/sheets/${encodeURIComponent(sheetId)}/annotations`),
          parse: parser(z.array(annotationSchema)),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /**
       * What each sanctioning authority wants of a set (D-4).
       *
       * Returns a LIST, never one template: Bengaluru has two authorities under one
       * rule pack, and a caller that took the first would show half the city the wrong
       * checklist.
       */
      submissionTemplates: (
        options: CallOptions & { cityPack?: string | null } = {},
      ): Promise<SubmissionTemplateListResponse> =>
        client.request({
          path: '/submission-templates',
          query: { cityPack: options.cityPack ?? undefined },
          parse: parser(submissionTemplateListSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /** This project's authority and its statutory identifiers. */
      submission: (
        projectId: string,
        options: CallOptions = {},
      ): Promise<ProjectSubmissionResponse> =>
        client.request({
          path: projectPath(projectId, '/submission'),
          parse: parser(projectSubmissionSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      saveSubmission: (
        projectId: string,
        input: { authority: string | null; fields: Record<string, string> },
        options: CallOptions = {},
      ): Promise<ProjectSubmissionResponse> =>
        client.request({
          method: 'PUT',
          path: projectPath(projectId, '/submission'),
          body: input,
          parse: parser(projectSubmissionSchema),
          ...options,
        }),

      /** What still stands between this set and the municipal counter. */
      submissionReadiness: (
        projectId: string,
        options: CallOptions & { authority?: string | null; version?: string | null } = {},
      ): Promise<SubmissionReadinessResponse> =>
        client.request({
          path: projectPath(projectId, '/sheets/submission-readiness'),
          query: {
            authority: options.authority ?? undefined,
            version: options.version ?? undefined,
          },
          parse: parser(submissionReadinessSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      /** The firm's title-block template and drafting conventions. */
      preferences: (options: CallOptions = {}): Promise<DrawingPreferencesResponse> =>
        client.request({
          path: '/firm/drawing-preferences',
          parse: parser(drawingPreferencesSchema),
          ...(options.signal === undefined ? {} : { signal: options.signal }),
        }),

      savePreferences: (
        input: Record<string, unknown>,
        opts: CallOptions = {},
      ): Promise<DrawingPreferencesResponse> =>
        client.request({
          method: 'PUT',
          path: '/firm/drawing-preferences',
          body: input,
          parse: parser(drawingPreferencesSchema),
          ...opts,
        }),

      /**
       * Mint a short-lived signed download link for one sheet.
       *
       * A two-step, and deliberately so: the endpoint answers `DownloadOut`
       * (`{url, expiresAt, filename, contentType}`) rather than the bytes, because §11
       * routes every download through a signed URL. The previous `assetUrl()` handed
       * the API path itself to an `<a href>`, which downloaded a JSON document named
       * `A-02A.pdf`.
       */
      download: (
        projectId: string,
        sheetId: string,
        format: 'svg' | 'dxf' | 'pdf',
        opts: CallOptions = {},
      ): Promise<DownloadLink> =>
        client.request({
          path: projectPath(projectId, `/sheets/${encodeURIComponent(sheetId)}.${format}`),
          parse: parser(downloadSchema),
          ...opts,
        }),
    },

    exports: {
      create: (
        projectId: string,
        input: { kind: ExportKind; params?: Record<string, unknown> },
        opts: CallOptions = {},
      ): Promise<ExportJob> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/export'),
          body: { kind: input.kind, params: input.params ?? {} },
          parse: parser(exportJobSchema),
          ...opts,
        }),

      get: (jobId: string, opts: CallOptions = {}): Promise<ExportJob> =>
        client.request({
          path: `/export-jobs/${encodeURIComponent(jobId)}`,
          parse: parser(exportJobSchema),
          ...opts,
        }),
    },

    // ── Sharing & comments (§F10, §13) ─────────────────────────────────────
    share: {
      /**
       * Mint a scoped read-only link. The token is returned exactly once — it
       * is stored hashed, so a UI that loses it cannot ask for it again.
       */
      create: (
        projectId: string,
        input: { sections: ShareSection[]; canComment?: boolean; expiresInDays?: number },
        opts: CallOptions = {},
      ): Promise<ShareLink> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/share'),
          // TOP-LEVEL fields, exactly as ShareLinkCreateIn reads them. The
          // earlier nested `{scope: {...}}` shape 422'd on every call (the
          // server forbids extra members, §13) — this method had simply never
          // been executed. The e2e share helper pins the working shape.
          body: {
            sections: input.sections,
            canComment: input.canComment ?? false,
            expiresInDays: input.expiresInDays,
          },
          parse: parser(shareLinkSchema),
          ...opts,
        }),

      list: (projectId: string, opts: CallOptions = {}): Promise<Page<ShareLink>> =>
        client.request({
          path: projectPath(projectId, '/share'),
          parse: pageParser(shareLinkSchema),
          ...opts,
        }),

      revoke: (shareId: string, opts: CallOptions = {}): Promise<{ id: string }> =>
        client.request({
          method: 'DELETE',
          path: `/share/${encodeURIComponent(shareId)}`,
          parse: parser(deletedSchema),
          ...opts,
        }),
    },

    comments: {
      /**
       * Open (unresolved) comments, oldest first.
       *
       * A BARE ARRAY, not a cursor page: the route answers `list[CommentOut]`
       * (routers/share.py `list_comments`) and takes no query parameters. This
       * used to parse with `pageParser` and pass a `resolved` filter the server
       * does not read — the same never-executed class as `share.create`'s
       * nested body: it would have thrown `malformed_response` on every real
       * response. Callers wanting newest-first reverse the result.
       */
      list: (projectId: string, opts: CallOptions = {}): Promise<Comment[]> =>
        client.request({
          path: projectPath(projectId, '/comments'),
          parse: (data) => z.array(commentSchema).parse(data),
          ...opts,
        }),

      create: (
        projectId: string,
        input: { body: string; anchor?: Record<string, unknown>; authorName?: string },
        opts: CallOptions = {},
      ): Promise<Comment> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/comments'),
          body: input,
          parse: parser(commentSchema),
          ...opts,
        }),

      /**
       * Resolve / unresolve a comment.
       *
       * `POST /comments/:id/resolve?resolved=` — NOT a PATCH under the project
       * path, which does not exist and answered 404/405. The route is firm-scoped
       * by the comment id inside the repository, so it carries no project
       * segment; `projectId` stays in the signature because every caller has it
       * and the store keys its cache by it.
       */
      setResolved: (
        _projectId: string,
        commentId: string,
        resolved: boolean,
        opts: CallOptions = {},
      ): Promise<Comment> =>
        client.request({
          method: 'POST',
          path: `/comments/${encodeURIComponent(commentId)}/resolve`,
          query: { resolved },
          parse: parser(commentSchema),
          ...opts,
        }),
    },

    /**
     * Live collaboration. The *stream* half is not here on purpose:
     * `lib/collab.ts` reads SSE with `fetch` + an `Authorization` header
     * because `EventSource` cannot set one, so it needs the transport, not the
     * JSON client. What is here is the one plain request the feature makes.
     */
    collab: {
      /**
       * Broadcast where my pointer is. Fire-and-forget, ~10Hz while moving.
       *
       * Four deliberate departures from the house default, all forced by the
       * call rate rather than by taste:
       *
       *  · **`parse: () => undefined`.** The route answers `204 No Content`
       *    (`collab_cursor`), and `readJson` already turns a 204 into `null`.
       *    There is no body to validate, so validating one would only invent a
       *    way to fail.
       *  · **`idempotencyKey: null`.** `CallOptions`' default stamps a fresh
       *    key on every POST. Replay protection is meaningless for a message
       *    that stores nothing and is superseded 100ms later, and the header is
       *    ~50 bytes on a request whose body is ~40.
       *  · **A short timeout.** The 20s default is a deadline for a request
       *    someone is waiting on. Nobody waits on a cursor; a post still in
       *    flight after two seconds has already been overtaken by the next one.
       *  · **Identity is NOT in the body.** The server stamps `userId`/`name`
       *    from the authenticated context and ignores any client claim, so
       *    sending one would be a lie with no effect. See `CursorIn`.
       *
       * Callers must swallow rejections. A dropped cursor is a dropped cursor;
       * it is never worth a toast, and it must never reach an error boundary.
       */
      cursor: (
        projectId: string,
        input: { x: number; y: number; storeyIndex: number | null },
        opts: CallOptions = {},
      ): Promise<void> =>
        client.request({
          method: 'POST',
          path: projectPath(projectId, '/collab/cursor'),
          body: input,
          idempotencyKey: null,
          timeoutMs: CURSOR_TIMEOUT_MS,
          parse: () => undefined,
          ...opts,
        }),
    },

    /**
     * The read-only client-share surface. Authenticated by the token in the
     * path, never by a bearer — `auth: 'none'` is load-bearing here, because
     * sending a firm's access token to a link a client forwarded to a
     * contractor is exactly the leak the separate surface exists to prevent.
     */
    shareViewer: {
      project: (token: string, opts: CallOptions = {}): Promise<SharedProject> =>
        client.request({
          path: `/share/${encodeURIComponent(token)}`,
          auth: 'none',
          // sharedProjectSchema, NOT projectDetailSchema: the server answers
          // with the deliberately narrow SharedProjectOut (name, units, scope)
          // and the detail parser would reject it.
          parse: parser(sharedProjectSchema),
          ...opts,
        }),

      renders: (token: string, opts: CallOptions = {}): Promise<RenderJob[]> =>
        client.request({
          path: `/share/${encodeURIComponent(token)}/renders`,
          auth: 'none',
          parse: (data) => z.array(renderJobSchema).parse(data),
          ...opts,
        }),

      sheets: (token: string, opts: CallOptions = {}): Promise<SheetSetResponse> =>
        client.request({
          path: `/share/${encodeURIComponent(token)}/sheets`,
          auth: 'none',
          parse: parser(sheetSetSchema),
          ...opts,
        }),

      model: (token: string, opts: CallOptions = {}): Promise<ModelState> =>
        client.request({
          path: `/share/${encodeURIComponent(token)}/model`,
          auth: 'none',
          parse: parser(modelStateSchema),
          ...opts,
        }),

      comment: (
        token: string,
        input: { body: string; authorName: string; anchor?: Record<string, unknown> },
        opts: CallOptions = {},
      ): Promise<Comment> =>
        client.request({
          method: 'POST',
          path: `/share/${encodeURIComponent(token)}/comments`,
          auth: 'none',
          body: input,
          parse: parser(commentSchema),
          ...opts,
        }),
    },

    // ── Catalogs (§11) ─────────────────────────────────────────────────────
    catalog: {
      rulepacks: (opts: CallOptions = {}): Promise<Page<RulepackSummary>> =>
        client.request({ path: '/rulepacks', parse: pageParser(rulepackSummarySchema), ...opts }),

      furniture: (opts: CallOptions = {}): Promise<Page<FurnitureItem>> =>
        client.request({
          path: '/catalog/furniture',
          parse: pageParser(furnitureItemSchema),
          ...opts,
        }),

      materials: (opts: CallOptions = {}): Promise<Page<MaterialItem>> =>
        client.request({
          path: '/catalog/materials',
          parse: pageParser(materialItemSchema),
          ...opts,
        }),

      facadeKits: (opts: CallOptions = {}): Promise<Page<FacadeKit>> =>
        client.request({
          path: '/catalog/facade-kits',
          parse: pageParser(facadeKitSchema),
          ...opts,
        }),
    },
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** The app-wide client. */
export const api: ApiClient = createApiClient();

/** Re-exported so one import covers both calling an endpoint and handling its failure. */
export { AppError, OpConflictError, OpRejectionError } from './errors';
