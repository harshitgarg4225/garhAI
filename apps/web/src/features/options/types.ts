/**
 * features/options — client-side contracts for the §15 options experience.
 *
 * Everything here mirrors a producer someone else owns, so every shape is
 * zod-validated AT THIS BOUNDARY and parsed defensively:
 *
 *   - `planOptionSchema`   mirrors `services/solver/types.py PlanOption.to_json()`
 *   - `solveResultSchema`  mirrors `SolveResult.to_json()` (the worker's JobResult data)
 *   - `solverJobDetailSchema` accepts BOTH row shapes in the wild today:
 *       `garh_api.schemas.jobs.SolverJobOut` (top-level `options`) and a queue
 *       result envelope (`result.options`). `readSolveOutcome()` normalises.
 *
 * The generic `jobSchema` in `lib/schemas.ts` deliberately strips kind-specific
 * fields, so this feature fetches the solver job row through `api.http` with its
 * own parser rather than through `api.solver.get`. That keeps the coupling to
 * the wire format inside this one file.
 *
 * `placements` is OPTIONAL on the option schema: the solver emits room
 * rectangles for the mini-plan labels (see the phase return notes — the field is
 * part of the coordinated contract, but a payload without it still renders a
 * wall-only silhouette rather than failing the whole screen).
 */

import { z } from 'zod';

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

/** Integer-mm point, exactly as ops carry it. */
export const ptSchema = z.object({ x: z.number().int(), y: z.number().int() });
export type PtMm = z.infer<typeof ptSchema>;

/** One op from the option's expansion. Trusted only after the model store folds it. */
export const optionOpSchema = z.object({
  type: z.string(),
  payload: z.record(z.unknown()).default({}),
});
export type OptionOp = z.infer<typeof optionOpSchema>;

// ---------------------------------------------------------------------------
// Scores (ScoreBreakdown.to_json — every component is a 0–100 integer)
// ---------------------------------------------------------------------------

const score = z.number().int().min(0).max(100).catch(0);

export const scoreBreakdownSchema = z.object({
  targetAreaFit: score.default(0),
  adjacency: score.default(0),
  circulation: score.default(0),
  daylight: score.default(0),
  vastu: score.default(0),
  furnitureFit: score.default(0),
  plumbingStack: score.default(0),
  privacy: score.default(0),
  compactness: score.default(0),
  composite: score.default(0),
  /** Integer percent of built-up area; §5.6 gates on ≤18. */
  circulationPercent: z.number().int().min(0).catch(0).default(0),
});
export type ScoreBreakdown = z.infer<typeof scoreBreakdownSchema>;

// ---------------------------------------------------------------------------
// Assumptions (services/common/assumptions.py Assumption.to_json)
// ---------------------------------------------------------------------------

export const assumptionSchema = z.object({
  field: z.string(),
  value: z.unknown(),
  reason: z.string().default(''),
  cite: z.string().nullish(),
  source: z.string().default('system'),
});
export type AssumptionJson = z.infer<typeof assumptionSchema>;

// ---------------------------------------------------------------------------
// Compliance rows (RuleResult.to_json — same rows GET /compliance returns)
// ---------------------------------------------------------------------------

export const optionComplianceRowSchema = z.object({
  ruleId: z.string(),
  packId: z.string().nullish(),
  status: z.enum(['pass', 'warn', 'fail', 'not_applicable']).catch('not_applicable'),
  title: z.string().nullish(),
  message: z.string().nullish(),
  actual: z.union([z.number(), z.string(), z.boolean(), z.null()]).optional(),
  limit: z.union([z.number(), z.string(), z.boolean(), z.null()]).optional(),
  cite: z.string().nullish(),
  /** A hard rule is a §5.6 gate; presentable options always pass these. */
  hard: z.boolean().catch(false).default(false),
  elements: z.array(z.string()).catch([]).default([]),
});
export type OptionComplianceRow = z.infer<typeof optionComplianceRowSchema>;

// ---------------------------------------------------------------------------
// Room placements (RoomPlacement — integer-mm rectangles, plot-local coords)
// ---------------------------------------------------------------------------

export const placementSchema = z.object({
  roomKey: z.string(),
  roomType: z.string(),
  storeyIndex: z.number().int().catch(0).default(0),
  xMm: z.number().int(),
  yMm: z.number().int(),
  widthMm: z.number().int(),
  depthMm: z.number().int(),
  roomId: z.string().nullish(),
});
export type Placement = z.infer<typeof placementSchema>;

// ---------------------------------------------------------------------------
// The option itself (PlanOption.to_json)
// ---------------------------------------------------------------------------

export const planOptionSchema = z.object({
  id: z.string(),
  /** Rank among returned options, 0 = best. */
  rank: z.number().int().catch(0).default(0),
  scores: scoreBreakdownSchema.catch(scoreBreakdownSchema.parse({})),
  ops: z.array(optionOpSchema).default([]),
  signature: z.array(z.string()).default([]),
  stairAnchorId: z.string().default(''),
  builtUpMm2: z.number().int().catch(0).default(0),
  footprintMm2: z.number().int().catch(0).default(0),
  rationaleFacts: z.array(z.string()).default([]),
  assumptions: z.array(assumptionSchema).default([]),
  compliance: z.array(optionComplianceRowSchema).default([]),
  /** Coordinated addition — room rectangles for mini-plan labels. Optional. */
  placements: z.array(placementSchema).optional(),
});
export type PlanOption = z.infer<typeof planOptionSchema>;

/** BuildableEnvelope.to_json — only what the UI reads. */
export const envelopeSchema = z.object({
  polygon: z.array(ptSchema).default([]),
  areaMm2: z.number().int().catch(0).default(0),
  assumptions: z.array(assumptionSchema).default([]),
  notes: z.array(z.string()).default([]),
});
export type EnvelopeJson = z.infer<typeof envelopeSchema>;

/** SolveResult.to_json — the worker's terminal payload. */
export const solveResultSchema = z.object({
  options: z.array(planOptionSchema).default([]),
  envelope: envelopeSchema.optional(),
  banner: z.string().optional(),
  considered: z.number().int().catch(0).default(0),
  rejectedByGates: z.number().int().catch(0).default(0),
});
export type SolveResultJson = z.infer<typeof solveResultSchema>;

// ---------------------------------------------------------------------------
// The solver job row, fetched loosely (see module docstring for why not jobSchema)
// ---------------------------------------------------------------------------

export const solverJobDetailSchema = z.object({
  id: z.string(),
  projectId: z.string().optional(),
  status: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled']).catch('queued'),
  progress: z.number().int().catch(0).default(0),
  stage: z.string().nullish(),
  error: z.string().nullish(),
  params: z.record(z.unknown()).catch({}).default({}),
  /** SolverJobOut shape: options at the top level. */
  options: z.array(z.unknown()).nullish(),
  optionCount: z.number().int().catch(0).default(0),
  /** Queue envelope shape: options under result. */
  result: z.record(z.unknown()).nullish(),
  queueDepth: z.number().int().nullish(),
  createdAt: z.string().optional(),
});
export type SolverJobDetail = z.infer<typeof solverJobDetailSchema>;

/** What the options screen renders once a job is terminal. */
export interface SolveOutcome {
  readonly jobId: string;
  readonly status: SolverJobDetail['status'];
  readonly options: readonly PlanOption[];
  readonly envelope: EnvelopeJson | null;
  /** §5.6 honest banner, when the worker sent one. */
  readonly banner: string | null;
  readonly considered: number;
  readonly rejectedByGates: number;
  readonly error: string | null;
  /** The params the job ran with — seed family, locked rooms (for re-runs). */
  readonly params: Readonly<Record<string, unknown>>;
}

/**
 * Normalise the two row shapes into one outcome. Options that fail to parse
 * individually are dropped (never rendered half-formed) rather than sinking
 * the ones that did parse.
 */
export function readSolveOutcome(row: SolverJobDetail): SolveOutcome {
  const rawList: unknown[] = Array.isArray(row.options)
    ? row.options
    : Array.isArray(row.result?.options)
      ? (row.result?.options as unknown[])
      : [];

  const options: PlanOption[] = [];
  for (const raw of rawList) {
    const parsed = planOptionSchema.safeParse(raw);
    if (parsed.success) options.push(parsed.data);
  }
  options.sort((a, b) => a.rank - b.rank);

  const resultEnvelope = row.result?.envelope;
  const envelope = envelopeSchema.safeParse(resultEnvelope);
  // Two sources, and the fallback is the one that matters. `result.banner` is the
  // worker's terminal payload, which only reaches here when the API relays the whole
  // result; the solver row now also carries the sentence in its own `banner` column,
  // and that is the path that survives a plain `GET /solver-jobs/:id`. Without the
  // fallback an architect whose Generate produced nothing gets no reason at all —
  // the blank screen this field exists to prevent.
  const banner = row.result?.banner ?? (row as { banner?: unknown }).banner;
  const considered = row.result?.considered;
  const rejected = row.result?.rejectedByGates;

  return {
    jobId: row.id,
    status: row.status,
    options,
    envelope: envelope.success ? envelope.data : null,
    banner: typeof banner === 'string' && banner !== '' ? banner : null,
    considered: typeof considered === 'number' ? considered : 0,
    rejectedByGates: typeof rejected === 'number' ? rejected : 0,
    error: row.error ?? null,
    params: row.params,
  };
}

// ---------------------------------------------------------------------------
// Mini-plan data carried on 'plan-option' artifact events (generation theater)
// ---------------------------------------------------------------------------

/**
 * The coordinated event contract: a `plan-option` artifact event's `data` may
 * carry `miniPlan` so the theater can draw the silhouette the moment the
 * option clears the gates, without waiting for the terminal result. Optional
 * end to end — a worker that omits it still gets a score-only silhouette card.
 */
export const miniPlanSchema = z.object({
  walls: z
    .array(
      z.object({
        a: ptSchema,
        b: ptSchema,
        thicknessMm: z.number().int().catch(115).default(115),
        kind: z.string().optional(),
      }),
    )
    .default([]),
  /** Optional room label anchors (centre points, plot-local mm). */
  rooms: z
    .array(z.object({ label: z.string(), x: z.number().int(), y: z.number().int() }))
    .default([]),
  storeyIndex: z.number().int().catch(0).default(0),
});
export type MiniPlan = z.infer<typeof miniPlanSchema>;
