/**
 * Zod schemas for the §11 surface — the client's half of the wire contract.
 *
 * These mirror `apps/api/garh_api/schemas/*.py`, which is camelCase on the wire
 * by construction (`alias_generator=to_camel`). Where the Python model exists,
 * the schema here matches it field for field; where it does not yet (jobs,
 * sheets, exports, share links, catalogs), the schema is derived from §11 and
 * the queue vocabulary in `garh_api/queue.py`, and says so.
 *
 * Two rules govern how strict these are:
 *
 * 1. **Objects strip unknown keys** (zod's default). A server that adds a field
 *    must not break a browser tab that has been open since before the deploy.
 * 2. **The folded model document is NOT re-validated here.** `ProjectDoc` has
 *    one authoritative validator — `assertValidModel()` in `@garh/model` — and
 *    a second, hand-maintained zod copy of a 700-line document type would drift
 *    within a week and disagree with the golden tests when it did. What this
 *    module checks is that the *envelope* is well-formed; `asProjectDoc()`
 *    checks the document's top-level shape and, in dev only, runs the real
 *    validator so a genuinely broken snapshot is loud during development.
 */

import { z } from 'zod';

import { SCHEMA_VERSION, assertValidModel, type Op, type ProjectDoc } from '@garh/model';

import { env } from './env';

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

/**
 * Server-side ids are uuids and op ids are ULIDs, but the client validates
 * "non-empty string" rather than the format. Format is the server's invariant;
 * a stricter client only invents failures on fixtures and seed data.
 */
const id = z.string().min(1);
/** ISO-8601 as the API emits it. Kept as a string — see `lib/units.ts` for display. */
const isoDateTime = z.string().min(1);
const intMm = z.number().int();

export const pointMmSchema = z.object({ x: intMm, y: intMm });

export const cursorPageSchema = <T extends z.ZodTypeAny>(item: T) =>
  z.object({
    items: z.array(item),
    nextCursor: z.string().nullable().default(null),
    hasMore: z.boolean().default(false),
  });

// ---------------------------------------------------------------------------
// Meta / bootstrap
// ---------------------------------------------------------------------------

export const metaSchema = z.object({
  service: z.string(),
  version: z.string(),
  env: z.string(),
  apiPrefix: z.string(),
  modelSchemaVersion: z.number().int(),
  flags: z.record(z.boolean()).default({}),
  providers: z.record(z.string()).default({}),
  limits: z.record(z.number().int()).default({}),
  serverTime: isoDateTime,
});
export type Meta = z.infer<typeof metaSchema>;

// ---------------------------------------------------------------------------
// Auth (§11 POST /auth/otp, /auth/verify; §13 refresh rotation)
// ---------------------------------------------------------------------------

export const userSchema = z.object({
  id,
  email: z.string(),
  name: z.string().default(''),
  role: z.enum(['admin', 'member']).catch('member'),
  coaNumber: z.string().nullable().default(null),
});
export type User = z.infer<typeof userSchema>;

export const firmSchema = z.object({
  id,
  name: z.string(),
  logoUrl: z.string().nullable().default(null),
  settings: z.record(z.unknown()).default({}),
});
export type Firm = z.infer<typeof firmSchema>;

export const otpRequestSchema = z.object({
  expiresInSeconds: z.number().int().nonnegative(),
  /** The 60s resend cooldown the UI counts down against. */
  resendAfterSeconds: z.number().int().nonnegative().default(60),
  /** Only ever populated when `DEV_ECHO_OTP` is on in a dev/test environment. */
  devCode: z.string().nullable().default(null),
});
export type OtpRequest = z.infer<typeof otpRequestSchema>;

export const sessionSchema = z.object({
  accessToken: z.string().min(1),
  /** Access-token lifetime in seconds (15 min per §11). */
  expiresIn: z.number().int().nonnegative(),
  /**
   * Present ONLY when the server is not setting an httpOnly refresh cookie.
   * See `lib/tokens.ts` for why the client treats its presence as a signal.
   */
  refreshToken: z.string().nullable().default(null),
  user: userSchema,
  firm: firmSchema,
});
export type Session = z.infer<typeof sessionSchema>;

// ---------------------------------------------------------------------------
// Projects, plot, brief (garh_api/schemas/project.py)
// ---------------------------------------------------------------------------

export const PROJECT_STATUSES = ['draft', 'brief', 'options', 'design', 'drawings', 'archived'] as const;

/**
 * The four F10 dashboard chips, as the API derives them.
 *
 * Every field is nullable with a `null` default, so a build of the API that has
 * not implemented a marker yet parses cleanly and the dashboard renders an
 * honest "todo" chip rather than an optimistic guess (`deriveStages()` in
 * `pages/_contracts.ts` encodes that rule).
 */
export const projectProgressSchema = z.object({
  briefCompleteness: z.number().int().nullable().default(null),
  optionCount: z.number().int().nullable().default(null),
  appliedOptionId: z.string().nullable().default(null),
  wallCount: z.number().int().nullable().default(null),
  sheetCount: z.number().int().nullable().default(null),
  sheetsStale: z.boolean().nullable().default(null),
  complianceCheckedAt: isoDateTime.nullable().default(null),
});
export type ProjectProgress = z.infer<typeof projectProgressSchema>;

export const projectSchema = z.object({
  id,
  name: z.string(),
  /** Not narrowed to the enum: the server owns the vocabulary and may extend it. */
  status: z.string(),
  units: z.enum(['ft-in', 'm']).catch('ft-in'),
  cityPack: z.string().nullable().default(null),
  architectOfRecord: z.string().nullable().default(null),
  demo: z.boolean().default(false),
  createdAt: isoDateTime,
  updatedAt: isoDateTime,

  /**
   * Denormalised summary for the dashboard card. All optional, all defaulted:
   * the list endpoint may not join the plot and brief in yet, and a card that
   * shows fewer facts is fine where a card that fails to parse is not. When the
   * fields are absent the project store fills what it can from the detail
   * response (`GET /projects/:id` carries plot + brief).
   */
  clientName: z.string().nullable().default(null),
  /** Integer mm² — geometry never arrives as a float, not even summarised. */
  plotAreaMm2: intMm.nullable().default(null),
  /** Storeys above ground: 1 for G+1. */
  storeysAbove: z.number().int().nullable().default(null),
  bedrooms: z.number().int().nullable().default(null),
  thumbnailUrl: z.string().nullable().default(null),
  progress: projectProgressSchema.nullable().default(null),
});
export type Project = z.infer<typeof projectSchema>;

export const plotSchema = z.object({
  id,
  projectId: id,
  boundary: z.array(pointMmSchema).default([]),
  northDeg: z.number().int().default(0),
  roads: z
    .array(
      z.object({
        edgeIndex: z.number().int().nonnegative(),
        widthMm: intMm.nullable().default(null),
        name: z.string().nullable().default(null),
      }),
    )
    .default([]),
  regProfile: z.record(z.unknown()).default({}),
  source: z.string().default('manual'),
  updatedAt: isoDateTime,
});
export type Plot = z.infer<typeof plotSchema>;

export const briefSchema = z.object({
  id,
  projectId: id,
  data: z.record(z.unknown()).default({}),
  vastuMode: z.enum(['off', 'advisory', 'strict']).catch('off'),
  completeness: z.number().int().min(0).max(100).default(0),
  updatedAt: isoDateTime,
});
export type Brief = z.infer<typeof briefSchema>;

export const briefAssumptionSchema = z.object({
  field: z.string(),
  value: z.unknown(),
  reason: z.string(),
  cite: z.string().nullable().default(null),
});
export type BriefAssumption = z.infer<typeof briefAssumptionSchema>;

export const briefParseSchema = z.object({
  provider: z.string(),
  data: z.record(z.unknown()).default({}),
  assumptions: z.array(briefAssumptionSchema).default([]),
  completeness: z.number().int().min(0).max(100).default(0),
  applied: z.boolean().default(false),
  brief: briefSchema.nullable().default(null),
  warnings: z.array(z.string()).default([]),
});
export type BriefParse = z.infer<typeof briefParseSchema>;

export const versionSchema = z.object({
  id,
  projectId: id,
  name: z.string().nullable().default(null),
  kind: z.enum(['auto', 'named', 'option']).catch('auto'),
  parentId: z.string().nullable().default(null),
  versionBranch: id,
  opSeqStart: z.number().int().nullable().default(null),
  opSeqEnd: z.number().int().nullable().default(null),
  snapshotHash: z.string().nullable().default(null),
  hasSnapshot: z.boolean().default(false),
  createdAt: isoDateTime,
});
export type Version = z.infer<typeof versionSchema>;

export const versionRestoreSchema = z.object({
  version: versionSchema,
  versionBranch: id,
  headIdx: z.number().int(),
  opsCopied: z.number().int().default(0),
  stateHash: z.string().nullable().default(null),
});
export type VersionRestore = z.infer<typeof versionRestoreSchema>;

export const projectDetailSchema = z.object({
  project: projectSchema,
  plot: plotSchema.nullable().default(null),
  brief: briefSchema.nullable().default(null),
  versionBranch: id,
  headIdx: z.number().int(),
  latestVersion: versionSchema.nullable().default(null),
  openCommentCount: z.number().int().default(0),
});
export type ProjectDetail = z.infer<typeof projectDetailSchema>;

// ---------------------------------------------------------------------------
// Ops (garh_api/schemas/ops.py) — the contract the model store lives on
// ---------------------------------------------------------------------------

export const opSchema = z.object({
  seq: z.number().int(),
  idx: z.number().int(),
  type: z.string(),
  payload: z.record(z.unknown()).default({}),
  inverse: z.record(z.unknown()).nullable().default(null),
  source: z.string(),
  actor: z.string().nullable().default(null),
  clientOpId: z.string().nullable().default(null),
  groupId: z.string().nullable().default(null),
  createdAt: isoDateTime,
});
export type PersistedOp = z.infer<typeof opSchema>;

export const opsAppendSchema = z.object({
  applied: z.array(opSchema).default([]),
  firstIdx: z.number().int(),
  lastIdx: z.number().int(),
  headIdx: z.number().int(),
  versionBranch: id,
  /** True when the server recognised our `clientOpId`s and did nothing. */
  alreadyApplied: z.boolean().default(false),
  stateHash: z.string().nullable().default(null),
  snapshotVersionId: z.string().nullable().default(null),
  rendersMarkedStale: z.number().int().default(0),
});
export type OpsAppendResult = z.infer<typeof opsAppendSchema>;

export const opsSinceSchema = z.object({
  ops: z.array(opSchema).default([]),
  sinceIdx: z.number().int(),
  headIdx: z.number().int(),
  versionBranch: id,
  hasMore: z.boolean().default(false),
});
export type OpsSince = z.infer<typeof opsSinceSchema>;

export const modelStateSchema = z.object({
  projectId: id,
  versionBranch: id,
  designVersionId: z.string().nullable().default(null),
  schemaVersion: z.number().int(),
  /** The folded document, or null when the log is short enough to replay whole. */
  snapshot: z.unknown().nullable().default(null),
  snapshotHash: z.string().nullable().default(null),
  baseIdx: z.number().int(),
  headIdx: z.number().int(),
  ops: z.array(opSchema).default([]),
  stateHash: z.string().nullable().default(null),
  truncated: z.boolean().default(false),
});
export type ModelState = z.infer<typeof modelStateSchema>;

/**
 * `GET /share/:token` — the viewer's entry point (`SharedProjectOut`).
 *
 * Deliberately narrower than `projectDetailSchema`: the server tells a client
 * with a link exactly what they were sent — a name, units, the scope — and
 * nothing about the practice that sent it.
 */
export const sharedProjectSchema = z.object({
  projectName: z.string(),
  units: z.enum(['ft-in', 'm']).catch('ft-in'),
  cityPack: z.string().nullable().default(null),
  sections: z.array(z.string()).default([]),
  canComment: z.boolean().default(false),
  expiresAt: isoDateTime.nullable().default(null),
  designVersionId: z.string().nullable().default(null),
  updatedAt: isoDateTime.nullable().default(null),
});
export type SharedProject = z.infer<typeof sharedProjectSchema>;

// ---------------------------------------------------------------------------
// The folded document
// ---------------------------------------------------------------------------

/** Cheap structural gate. Anything deeper is `@garh/model`'s job, not ours. */
function looksLikeProjectDoc(value: unknown): value is ProjectDoc {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.schemaVersion === 'number' &&
    typeof v.plot === 'object' &&
    v.plot !== null &&
    typeof v.brief === 'object' &&
    v.brief !== null &&
    typeof v.house === 'object' &&
    v.house !== null &&
    Array.isArray(v.annotations)
  );
}

/**
 * Adopt a server snapshot as a `ProjectDoc`.
 *
 * @throws Error when the shape is wrong or the schema version is from the
 * future — in which case the honest answer is "reload for the new bundle",
 * not "fold ops onto a document we do not understand".
 */
export function asProjectDoc(value: unknown): ProjectDoc {
  if (!looksLikeProjectDoc(value)) {
    throw new Error('Snapshot is not a ProjectDoc (expected schemaVersion, plot, brief, house).');
  }
  if (value.schemaVersion > SCHEMA_VERSION) {
    throw new Error(
      `Snapshot uses model schema v${value.schemaVersion}; this app understands v${SCHEMA_VERSION}.`,
    );
  }
  // The full invariant sweep is O(walls + openings + rooms). Worth it in dev,
  // where a bad snapshot should stop the world; skipped in production, where
  // the server already validated every op that produced it and the §15 budget
  // is "open project → interactive canvas <2s".
  if (env.isDev) assertValidModel(value);
  return value;
}

/** An op as it goes onto the wire — the client's half of `OpIn`. */
export interface OpEnvelope {
  readonly type: string;
  readonly payload: Record<string, unknown>;
  readonly clientOpId: string;
  readonly groupId?: string;
}

/** Persisted op → the model core's `Op`. Trusted: the server validated it. */
export function toModelOp(op: PersistedOp): Op {
  return {
    type: op.type,
    payload: op.payload,
    ...(op.clientOpId === null ? {} : { clientOpId: op.clientOpId }),
    ...(op.groupId === null ? {} : { groupId: op.groupId }),
  } as unknown as Op;
}

// ---------------------------------------------------------------------------
// Jobs — solver, render, drawings, export (§9, §11; queue.py vocabulary)
// ---------------------------------------------------------------------------

export const JOB_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const JOB_KINDS = ['solver', 'render', 'drawings'] as const;
export type JobKind = (typeof JOB_KINDS)[number];

export const jobSchema = z.object({
  id,
  kind: z.enum(JOB_KINDS).catch('solver'),
  type: z.string().default(''),
  projectId: id,
  status: z.enum(JOB_STATUSES).catch('queued'),
  progress: z.number().int().min(0).max(100).default(0),
  stage: z.string().nullable().default(null),
  message: z.string().nullable().default(null),
  error: z.string().nullable().default(null),
  /** Kind-specific result: solver options, render output url, sheet ids, … */
  result: z.record(z.unknown()).nullable().default(null),
  params: z.record(z.unknown()).default({}),
  designVersionId: z.string().nullable().default(null),
  queuePosition: z.number().int().nullable().default(null),
  createdAt: isoDateTime,
  updatedAt: isoDateTime.nullable().default(null),
});
export type Job = z.infer<typeof jobSchema>;

/**
 * One SSE frame from `/solver-jobs/:id/events` or `/render-jobs/:id/events`.
 * Field-for-field `garh_api.queue.ProgressEvent.to_dict()`.
 */
/** The server's own worker-event→status table (`garh_api.queue._EVENT_STATUS`). */
const PROGRESS_EVENT_STATUS: Readonly<Record<string, (typeof JOB_STATUSES)[number]>> = {
  queued: 'queued',
  started: 'running',
  stage: 'running',
  progress: 'running',
  artifact: 'running',
  warning: 'running',
  retrying: 'running',
  succeeded: 'succeeded',
  failed: 'failed',
  dead_lettered: 'failed',
  cancelled: 'cancelled',
};
const PROGRESS_TERMINAL_TYPES = new Set(['succeeded', 'failed', 'cancelled', 'dead_lettered']);

/**
 * One SSE `progress` frame, EXACTLY as `garh_api.queue.ProgressEvent.encode()`
 * puts it on the wire: the WORKER's vocabulary — `type` / `percent` / `tsMs` —
 * not the job row's `status` / `progress` / `at`. The first version of this
 * schema was written against a server method that does not exist, and its
 * `.catch('running')` + `.default(false)` dressed every real event as a
 * non-terminal "running" — so no browser could ever see a job finish, and no
 * test could go red. Found the first time a browser actually consumed the
 * stream (the options theater sat on "Waiting in the queue…" while the worker
 * logged `job.succeeded`). The transform derives the row vocabulary every
 * consumer speaks, using the server's own type→status table above.
 */
export const progressEventSchema = z
  .object({
    schemaVersion: z.number().int().default(1),
    jobId: z.string(),
    type: z.string(),
    seq: z.number().int().default(0),
    tsMs: z.number().int().default(0),
    /** Stable machine token the UI maps to §15's staged copy. */
    stage: z.string().nullable().default(null),
    message: z.string().nullable().default(null),
    percent: z.number().int().min(0).max(100).nullable().default(null),
    data: z.record(z.unknown()).default({}),
  })
  .transform((raw) => ({
    eventVersion: raw.schemaVersion,
    jobId: raw.jobId,
    jobKind: typeof raw.data.kind === 'string' ? raw.data.kind : '',
    seq: raw.seq,
    at: new Date(raw.tsMs).toISOString(),
    status: PROGRESS_EVENT_STATUS[raw.type] ?? ('running' as const),
    progress: raw.percent ?? 0,
    stage: raw.stage,
    message: raw.message,
    data: raw.data,
    terminal: PROGRESS_TERMINAL_TYPES.has(raw.type),
  }));
export type ProgressEvent = z.infer<typeof progressEventSchema>;

/**
 * The stream's opening `state` frame is the JOB ROW, not a worker event — it
 * is how a client that connects after the job finished still learns the
 * outcome. It used to fail the progress parse and be dropped silently, which
 * left late connectors (and every reconnect) stuck on their last known state.
 */
const sseStateFrameSchema = z.object({
  id: z.string(),
  kind: z.string().default(''),
  status: z.enum(JOB_STATUSES).catch('running'),
  progress: z.number().int().min(0).max(100).default(0),
  stage: z.string().nullable().default(null),
  error: z.string().nullable().default(null),
  updatedAt: isoDateTime.nullable().default(null),
});

export function progressEventFromState(data: unknown): ProgressEvent | null {
  const row = sseStateFrameSchema.safeParse(data);
  if (!row.success) return null;
  const { status } = row.data;
  return {
    eventVersion: 1,
    jobId: row.data.id,
    jobKind: row.data.kind,
    seq: 0,
    at: row.data.updatedAt ?? new Date(0).toISOString(),
    status,
    progress: row.data.progress,
    stage: row.data.stage,
    message: row.data.error,
    data: row.data.error === null ? {} : { message: row.data.error },
    terminal: status === 'succeeded' || status === 'failed' || status === 'cancelled',
  };
}

// ---------------------------------------------------------------------------
// Renders (§9) — Phase 7
// ---------------------------------------------------------------------------

export const RENDER_MODES = ['precise', 'explore'] as const;
export type RenderMode = (typeof RENDER_MODES)[number];

/**
 * `RenderJobOut` (apps/api/garh_api/schemas/jobs.py), field for field — and the
 * reason it is NOT `jobSchema`.
 *
 * `RenderJobOut` carries no `kind` discriminator (nor `type`, `stage`,
 * `message`, `result`). Parsed with `jobSchema`, whose `kind` is
 * `z.enum(JOB_KINDS).catch('solver')`, every render row came back labelled
 * `kind: 'solver'`. That is not cosmetic: the jobs store routes the SSE
 * subscription off `kind`, so a render's progress stream was opened against
 * `/solver-jobs/:id/events` (404, silent — a dropped stream is not treated as a
 * failed job), `renders` rows could not be cancelled or retried down the right
 * branch, and `useRenderHistory`'s "a render finished, re-list" subscription —
 * which is what makes a finished image appear — tested `job.kind === 'render'`
 * and was never true.
 *
 * So this schema mirrors the render row and then TRANSFORMS it into something
 * that is also a valid {@link Job}: the discriminator is stamped from the
 * endpoint that returned it (which is the only honest source — the server
 * genuinely does not send one), and `queueDepth` is surfaced as the
 * `queuePosition` the job cards read. Extra render-only fields (`stale`,
 * `outputUrl`, `mode`, `view`) ride along for the history grid.
 */
export const renderJobSchema = z
  .object({
    id,
    projectId: id,
    designVersionId: z.string().nullable().default(null),
    mode: z.enum(RENDER_MODES).catch('explore'),
    provider: z.string().default('mock'),
    status: z.enum(JOB_STATUSES).catch('queued'),
    progress: z.number().int().min(0).max(100).default(0),
    /** Re-presigned per request by the server (§13: ≤10 min), never stored. */
    outputUrl: z.string().nullable().default(null),
    /** §9: true once the design moved on. Server-computed — never guessed here. */
    stale: z.boolean().default(false),
    error: z.string().nullable().default(null),
    view: z.record(z.unknown()).default({}),
    params: z.record(z.unknown()).default({}),
    queueDepth: z.number().int().nullable().default(null),
    eventsUrl: z.string().nullable().default(null),
    createdAt: isoDateTime,
    updatedAt: isoDateTime.nullable().default(null),
  })
  .transform((row) => ({
    ...row,
    kind: 'render' as const,
    type: 'render.image',
    stage: null as string | null,
    message: null as string | null,
    result: row.outputUrl === null ? null : ({ outputUrl: row.outputUrl } as Record<string, unknown>),
    queuePosition: row.queueDepth,
  }));
export type RenderJob = z.infer<typeof renderJobSchema>;

/** `RenderPackOut` — the §9 client pack as one job group. */
export const renderPackSchema = z.object({
  packId: z.string().min(1),
  projectId: id,
  designVersionId: z.string().nullable().default(null),
  status: z.enum(JOB_STATUSES).catch('running'),
  progress: z.number().int().min(0).max(100).default(0),
  jobs: z.array(renderJobSchema).default([]),
});
export type RenderPack = z.infer<typeof renderPackSchema>;

/** `UploadSlotOut` — one presigned PUT/GET pair for a capture image. */
export const renderUploadSlotSchema = z.object({
  putUrl: z.string().min(1),
  getUrl: z.string().min(1),
  key: z.string().min(1),
});
export type RenderUploadSlot = z.infer<typeof renderUploadSlotSchema>;

export const renderUploadsSchema = z.object({
  slots: z.array(renderUploadSlotSchema).default([]),
});

// ---------------------------------------------------------------------------
// Copilot (§10) — Phase 6
// ---------------------------------------------------------------------------

/**
 * `CopilotOutcome` (apps/api/garh_api/schemas/copilot.py). The server answers in
 * exactly one of these four classes; `ops` is the only one carrying a diff.
 */
export const COPILOT_OUTCOMES = ['ops', 'needsClarification', 'cannotDo', 'invalid'] as const;
export type CopilotOutcome = (typeof COPILOT_OUTCOMES)[number];

/**
 * One proposed op with the plain-language line the diff panel shows.
 *
 * `payload` is passed through as an opaque record ON PURPOSE. It was already
 * checked against OP_CATALOG server-side, it will be checked again by the local
 * dry-run fold, and a third time by the op sequencer on apply. A hand-written
 * zod copy of the op taxonomy here would be a fourth definition that can only
 * drift — and the one place drift would be dangerous is exactly here.
 */
export const copilotOpSchema = z.object({
  type: z.string().min(1),
  payload: z.record(z.unknown()).default({}),
  description: z.string().default(''),
});

export const copilotIssueSchema = z.object({
  code: z.string().default('invalid'),
  message: z.string().default(''),
  severity: z.string().default('error'),
  elementIds: z.array(z.string()).default([]),
  field: z.string().nullable().default(null),
});

/**
 * `CopilotProposeOut` — a proposal, never an applied change.
 *
 * Strict on `ops`: a malformed op list must fail the parse rather than render a
 * diff for ops nobody understood. Tolerant on `issues`, which is explanatory.
 */
export const copilotProposeSchema = z.object({
  outcome: z.enum(COPILOT_OUTCOMES).catch('invalid'),
  intent: z.string().default(''),
  ops: z.array(copilotOpSchema).default([]),
  needsClarification: z.string().nullable().default(null),
  cannotDo: z.string().nullable().default(null),
  issues: z.array(copilotIssueSchema).default([]),
  /** Mint-once group id: applying with it makes the whole diff ONE undo step. */
  groupId: z.string().min(1),
  /** The branch HEAD the proposal was validated against. Apply at this index. */
  baseIdx: z.number().int(),
  versionBranch: z.string().min(1),
  provider: z.string().default('mock'),
  attempts: z.number().int().default(1),
  selfCorrected: z.boolean().default(false),
  /**
   * Whether the rules engine actually ran on the dry-run result. `false` means
   * "could not be checked" (no plot boundary, engine absent) — never a pass.
   */
  rulesChecked: z.boolean().default(true),
  /** §14 telemetry: the server-side dry-run fold's duration, in ms. */
  dryRunMs: z.number().default(0),
});
export type CopilotPropose = z.infer<typeof copilotProposeSchema>;

export const copilotDecisionSchema = z.object({
  logged: z.boolean().default(true),
});

// ---------------------------------------------------------------------------
// Compliance (§6)
// ---------------------------------------------------------------------------

/**
 * Mirrors `RuleResult.to_json()` (apps/api/garh_rules/results.py) — the row shape
 * `compliance_reports.results` stores and `GET /compliance` returns.
 *
 * Field names matter here and two were wrong: the engine emits `packId`, not
 * `pack`, and `fixAvailable`, not `autofixAvailable`. Because `z.object` strips
 * unknown keys and both wrong fields carried `.default()`, the UI silently saw
 * `pack: null` on every row and never offered a "Fix it" button for a rule that
 * had a computable auto-fix.
 */
export const complianceResultSchema = z.object({
  ruleId: z.string(),
  packId: z.string().nullable().default(null),
  status: z.enum(['pass', 'warn', 'fail', 'not_applicable']).catch('not_applicable'),
  severity: z.string().nullable().default(null),
  title: z.string().nullable().default(null),
  message: z.string().nullable().default(null),
  actual: z.union([z.number(), z.string(), z.boolean(), z.null()]).default(null),
  limit: z.union([z.number(), z.string(), z.boolean(), z.null()]).default(null),
  unit: z.string().nullable().default(null),
  cite: z.string().nullable().default(null),
  citeShort: z.string().nullable().default(null),
  fixHint: z.string().nullable().default(null),
  /** True when the pack's `autofix` block yields an applicable op group. */
  fixAvailable: z.boolean().default(false),
  elements: z.array(z.string()).default([]),
  confidence: z.string().nullable().default(null),
  checkType: z.string().nullable().default(null),
  /** A hard rule is a solver gate (§5.6), not merely a red chip. */
  hard: z.boolean().default(false),
  overridden: z.boolean().default(false),
  overrideReason: z.string().nullable().default(null),
  notApplicableReason: z.string().nullable().default(null),
});
export type ComplianceResult = z.infer<typeof complianceResultSchema>;

export const complianceSchema = z.object({
  /** False means "nobody has run the rules yet" — never an implied pass. */
  evaluated: z.boolean().default(false),
  projectId: id,
  designVersionId: z.string().nullable().default(null),
  reportId: z.string().nullable().default(null),
  packVersions: z.record(z.unknown()).default({}),
  results: z.array(complianceResultSchema).default([]),
  counts: z.record(z.number().int()).default({}),
  createdAt: isoDateTime.nullable().default(null),
  /**
   * True = run just now against the working state, not persisted. False on a
   * frozen report, which is the one the sheets and the share link quote (§7).
   */
  live: z.boolean().default(false),
  /**
   * Only when `evaluated` is false: why not, in a sentence the UI can show.
   * "Not checked yet" and "checked and clean" must never look the same (§15).
   */
  reason: z.string().nullable().default(null),
  worstStatus: z.enum(['pass', 'warn', 'fail', 'not_applicable']).nullable().default(null),
  /** Approximations the projection made. Shown in the report's detail view. */
  notes: z.array(z.string()).default([]),
});
export type ComplianceReport = z.infer<typeof complianceSchema>;

// ---------------------------------------------------------------------------
// DXF boundary import (Phase 2 F1; garh_api/schemas/imports.py field for field)
// ---------------------------------------------------------------------------

/**
 * One closed-boundary candidate: a CCW integer-mm ring in plot-local
 * coordinates, first vertex not repeated — exactly the polygon
 * `plot.set_boundary` accepts. `closedArea` is mm² (shoelace on the ring).
 */
export const dxfPolylineSchema = z.object({
  points: z.array(pointMmSchema),
  closedArea: z.number().int().nonnegative(),
});
export type DxfPolyline = z.infer<typeof dxfPolylineSchema>;

/** Empty `polylines` is meaningful: the layer exists but holds nothing closed. */
export const dxfLayerSchema = z.object({
  name: z.string(),
  polylines: z.array(dxfPolylineSchema).default([]),
});
export type DxfLayer = z.infer<typeof dxfLayerSchema>;

/**
 * How drawing units were mapped to millimetres. `mmPerUnit` is a decimal
 * STRING ("25.4") by contract — no float crosses the geometry boundary, even
 * as metadata. `assumed` true = `$INSUNITS` was 0/unknown and mm was assumed;
 * the picker renders that as an assumption chip (golden rule 4).
 */
export const dxfUnitsSchema = z.object({
  insunits: z.number().int(),
  mmPerUnit: z.string(),
  assumed: z.boolean(),
});
export type DxfUnits = z.infer<typeof dxfUnitsSchema>;

export const dxfImportResultSchema = z.object({
  layers: z.array(dxfLayerSchema).default([]),
  units: dxfUnitsSchema.nullable().default(null),
  /** Entities dropped and why (openPolylines, overVertexCap, …). Never fatal. */
  skipped: z.record(z.number().int()).default({}),
});
export type DxfImportResult = z.infer<typeof dxfImportResultSchema>;

/**
 * An import job (`POST /projects/:id/import/dxf`, `GET /import-jobs/:id`).
 * `result` appears once the worker succeeds and stays for the record's 24h
 * TTL; a succeeded job with a null `result` outlived its result window and
 * the honest next action is to upload the file again.
 */
export const dxfImportJobSchema = z.object({
  id,
  projectId: id,
  kind: z.string().default('dxf-import'),
  status: z.enum(JOB_STATUSES).catch('queued'),
  progress: z.number().int().min(0).max(100).default(0),
  filename: z.string().nullable().default(null),
  sizeBytes: z.number().int().nullable().default(null),
  /** The worker's own copy on failure: what happened + what to do next. */
  error: z.string().nullable().default(null),
  /** SSE stream for live progress. Server-relative; do not hardcode a path. */
  eventsUrl: z.string().nullable().default(null),
  result: dxfImportResultSchema.nullable().default(null),
  createdAt: isoDateTime.nullable().default(null),
  updatedAt: isoDateTime.nullable().default(null),
});
export type DxfImportJob = z.infer<typeof dxfImportJobSchema>;

// ---------------------------------------------------------------------------
// Sheets, exports, shares, comments, catalogs
// (provisional: reconcile with the Python schemas when those land — see the
//  contract note in this agent's handover)
// ---------------------------------------------------------------------------

export const SHEET_KINDS = [
  'site',
  'floor_plan',
  'elevation',
  'section',
  'schedule',
  'area_statement',
] as const;

/**
 * Mirrors `SheetOut` (apps/api/garh_api/schemas/jobs.py) field for field.
 *
 * Two things this got wrong before, both of which broke the Sheets tab:
 *   - `number`/`title` are `Optional[StrictStr] = None` on the server and
 *     serialise as JSON `null`. `z.string().default('')` only fills `undefined`,
 *     so a sheet with no title raised a ZodError, surfaced as `malformed_response`.
 *   - `scale`/`layout` do not exist in the response at all. The real fields are
 *     `scaleDenominator` and `artifacts` — and because `z.object` strips unknown
 *     keys, `artifacts` (the svg/dxf/pdf download paths) never reached the UI.
 */
export const sheetSchema = z.object({
  id,
  projectId: id,
  designVersionId: z.string().nullable().default(null),
  kind: z.string(),
  number: z.string().nullable().default(null),
  title: z.string().nullable().default(null),
  scaleDenominator: z.number().int().nullable().default(null),
  /** Available formats → download paths (svg | dxf | pdf). */
  artifacts: z.record(z.string()).default({}),
  annotationCount: z.number().int().default(0),
  orphanedAnnotationCount: z.number().int().default(0),
  generatedAt: isoDateTime.nullable().default(null),
});
export type Sheet = z.infer<typeof sheetSchema>;

export const exportJobSchema = jobSchema.extend({
  /** Short-lived signed URL (§13: ≤10 min). Absent until the job succeeds. */
  downloadUrl: z.string().nullable().default(null),
  expiresAt: isoDateTime.nullable().default(null),
});
export type ExportJob = z.infer<typeof exportJobSchema>;

export const SHARE_SECTIONS = ['plan', 'three_d', 'renders', 'sheets', 'compliance'] as const;
export type ShareSection = (typeof SHARE_SECTIONS)[number];

/**
 * Mirrors `ShareLinkOut` (apps/api/garh_api/schemas/project.py), which returns
 * `sections` and `canComment` FLAT — not nested under `scope`. The nested shape
 * had a `.default()`, so it never threw; it silently yielded
 * `{sections: [], canComment: false}` for every link and the share dialog showed
 * no sections and no comment permission on links that had both.
 */
export const shareLinkSchema = z.object({
  id,
  projectId: id,
  /** Returned exactly once, at creation. Never retrievable again (§13). */
  url: z.string().nullable().default(null),
  token: z.string().nullable().default(null),
  sections: z.array(z.string()).default([]),
  canComment: z.boolean().default(false),
  /** wa.me deep link with a preformatted message (§15). */
  whatsappUrl: z.string().nullable().default(null),
  expiresAt: isoDateTime.nullable().default(null),
  revoked: z.boolean().default(false),
  createdAt: isoDateTime,
});
export type ShareLink = z.infer<typeof shareLinkSchema>;

export const commentSchema = z.object({
  id,
  projectId: id,
  body: z.string(),
  authorName: z.string().default(''),
  anchor: z.record(z.unknown()).default({}),
  resolved: z.boolean().default(false),
  fromShareLink: z.boolean().default(false),
  createdAt: isoDateTime,
});
export type Comment = z.infer<typeof commentSchema>;

export const rulepackSummarySchema = z.object({
  id: z.string(),
  name: z.string().default(''),
  version: z.string().default(''),
  extends: z.string().nullable().default(null),
  citationsBase: z.string().nullable().default(null),
  ruleCount: z.number().int().default(0),
  confidence: z.string().default('seed'),
});
export type RulepackSummary = z.infer<typeof rulepackSummarySchema>;

export const furnitureItemSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.string().default(''),
  widthMm: intMm,
  depthMm: intMm,
  heightMm: intMm,
  /**
   * Access strip in front of the item, in mm. `CatalogItemOut` has served this
   * since Phase 0 and the seeded catalogue sets it on all 45 items; it was
   * missing here, and zod strips unknown keys, so the canvas was falling back
   * to a per-category assumption and flagging every clearance as assumed.
   * Defaulted rather than required so an older API still parses.
   */
  clearanceMm: intMm.default(0),
  assetUrl: z.string().nullable().default(null),
  roomTypes: z.array(z.string()).default([]),
});
export type FurnitureItem = z.infer<typeof furnitureItemSchema>;

export const materialItemSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.string().default(''),
  colorHex: z.string().nullable().default(null),
  textureUrl: z.string().nullable().default(null),
  surfaceGroups: z.array(z.string()).default([]),
});
export type MaterialItem = z.infer<typeof materialItemSchema>;

export const facadeKitSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().default(''),
  thumbnailUrl: z.string().nullable().default(null),
  colorways: z.array(z.string()).default([]),
});
export type FacadeKit = z.infer<typeof facadeKitSchema>;

export const ackSchema = z.object({ ok: z.boolean().default(true) });
export const deletedSchema = z.object({ id, deleted: z.boolean().default(true) });

/** `parse` for endpoints whose body we deliberately ignore (204, acks). */
export const passthrough = (value: unknown): unknown => value;

// ---------------------------------------------------------------------------
// Phase 8 — the drawing set beyond `sheetSchema` (§7, F7-A, D13)
//
// `sheetSchema` above is one row. These five are the rest of the §7 surface, and
// they live here rather than in `features/sheets/` for the same reason every other
// endpoint shape does: `lib/api.ts` parses them, and a schema defined in a feature
// would make the transport layer depend on a feature layer.
// ---------------------------------------------------------------------------

/**
 * `GET /projects/:id/sheets` and `POST .../sheets/generate`.
 *
 * **Not a cursor page.** `api.sheets.list` used to parse this with `pageParser`,
 * which threw on every real response — the Sheets tab showed "nothing generated"
 * for projects that had a complete set. Mirrors `garh_api.schemas.jobs.SheetSetOut`.
 */
export const sheetSetSchema = z.object({
  projectId: id,
  designVersionId: z.string().nullable().default(null),
  sheets: z.array(sheetSchema).default([]),
  /** The queued generation job, present only on the `generate` response.
   *  Typed as the export-job row (mirrors `SheetSetOut.job: ExportJobOut`)
   *  so the jobs store can track it without a cast. */
  job: exportJobSchema.nullable().default(null),
  generatedAt: isoDateTime.nullable().default(null),
});
export type SheetSetResponse = z.infer<typeof sheetSetSchema>;

/**
 * `GET /projects/:id/sheets/summary`.
 *
 * `chainSumOk` is §7 step 5 ("chains must sum exactly") carried to the UI. The worker
 * asserts it before a sheet exists, so it is expected to be true — which is exactly
 * why it is worth showing: it is the product's central claim about its own drawings.
 */
export const sheetSummarySchema = z.object({
  projectId: id,
  designVersionId: z.string().nullable().default(null),
  sheetCount: z.number().int().default(0),
  chainCount: z.number().int().default(0),
  chainSumOk: z.boolean().default(true),
  labelCollisions: z.number().int().default(0),
  skipped: z.array(z.record(z.unknown())).default([]),
  notes: z.array(z.string()).default([]),
  formatsAvailable: z.array(z.string()).default([]),
  generatedAt: isoDateTime.nullable().default(null),
});
export type SheetSetSummaryResponse = z.infer<typeof sheetSummarySchema>;

/** `GET /projects/:id/sheets/:sid/content` — the viewer's SVG, already sanitised. */
export const sheetContentSchema = z.object({
  sheetId: id,
  slug: z.string().nullable().default(null),
  number: z.string().nullable().default(null),
  title: z.string().nullable().default(null),
  kind: z.string(),
  scaleDenominator: z.number().int().nullable().default(null),
  paper: z.string().nullable().default(null),
  widthMm: z.number().int().nullable().default(null),
  heightMm: z.number().int().nullable().default(null),
  svg: z.string(),
  bytes: z.number().int().default(0),
  generatedAt: isoDateTime.nullable().default(null),
});
export type SheetContentResponse = z.infer<typeof sheetContentSchema>;

/**
 * One annotation. `modelAnnotationId` — not `id` — is what op 32 addresses: `id` is
 * the projection row's UUID, and the op log knows the note by its own ULID.
 */
export const annotationSchema = z.object({
  id,
  modelAnnotationId: z.string().nullable().default(null),
  sheetId: id,
  sheetSlug: z.string().nullable().default(null),
  sheetNumber: z.string().nullable().default(null),
  sheetKind: z.string().nullable().default(null),
  anchorElementId: z.string().nullable().default(null),
  anchorKind: z.string().default('element'),
  payload: z.record(z.unknown()).default({}),
  orphaned: z.boolean().default(false),
  /** Element ids still drawn on that sheet — what the re-attach picker offers. */
  reattachCandidates: z.array(z.string()).default([]),
  createdAt: isoDateTime.nullable().default(null),
});
export type SheetAnnotationResponse = z.infer<typeof annotationSchema>;

/** `GET /projects/:id/sheets/review-tray` — the D13 surface. */
export const reviewTraySchema = z.object({
  projectId: id,
  designVersionId: z.string().nullable().default(null),
  orphaned: z.array(annotationSchema).default([]),
  attachedCount: z.number().int().default(0),
  /**
   * The server's own wording of the no-fuzzy-matching promise. Printed verbatim so a
   * copy edit in the UI cannot quietly promise behaviour the engine does not have.
   */
  policy: z.string(),
  /** False means "these counts are as last written", not "we just checked". */
  reconciled: z.boolean().default(false),
});
export type ReviewTrayResponse = z.infer<typeof reviewTraySchema>;

export const revisionRowSchema = z.object({
  revision: z.string(),
  date: z.string().default(''),
  note: z.string().default(''),
});
export type RevisionRowValue = z.infer<typeof revisionRowSchema>;

export const titleBlockSchema = z.object({
  firmName: z.string().default(''),
  projectName: z.string().default(''),
  clientName: z.string().default(''),
  revision: z.string().default('A'),
  date: z.string().default(''),
  drawnBy: z.string().default(''),
  checkedBy: z.string().default(''),
  notes: z.string().default(''),
  logoUrl: z.string().nullable().default(null),
});
export type TitleBlockValue = z.infer<typeof titleBlockSchema>;

/** `GET|PUT /firm/drawing-preferences` — the §7 title-block editor's document. */
export const drawingPreferencesSchema = z.object({
  titleBlock: titleBlockSchema,
  dimToJamb: z.boolean().default(false),
  sheetNumberPrefix: z.string().default('A'),
  defaultScaleDenominator: z.number().int().default(100),
  defaultSheetSize: z.string().default('A2'),
  revisions: z.array(revisionRowSchema).default([]),
  /** `firm` once a template is saved, `defaults` before that. Shown as a chip. */
  source: z.string().default('defaults'),
  firmLogoUrl: z.string().nullable().default(null),
});
export type DrawingPreferencesResponse = z.infer<typeof drawingPreferencesSchema>;

/** `DownloadOut` — every §11 download is a short-lived signed URL, not bytes. */
export const downloadSchema = z.object({
  url: z.string(),
  expiresAt: isoDateTime.nullable().default(null),
  filename: z.string().default('download'),
  contentType: z.string().default('application/octet-stream'),
});
export type DownloadLink = z.infer<typeof downloadSchema>;
