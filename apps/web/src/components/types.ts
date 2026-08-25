/**
 * View models for the shared components.
 *
 * WHY THESE EXIST SEPARATELY FROM THE API DTOs
 * ---------------------------------------------------------------------------
 * Components in this folder are presentational: they take props and render.
 * None of them import a store or the API client. That keeps them testable
 * without a server, lets the pages own all data-fetching, and — practically —
 * means the API response shape can change without touching 15 components.
 *
 * Pages map `api.*` responses into these shapes. Where a field mirrors the DB
 * it uses the same allowed values as `apps/api/garh_api/models.py` (which
 * exports them as tuples: PROJECT_STATUSES, JOB_STATUSES, …) so the mapping is
 * a rename at most, never a reinterpretation.
 *
 * Lengths and areas are integer millimetres here too. Formatting to
 * "1,200 sq ft · 133 gaj" happens in the component, via @garh/model.
 */

import type { UnitsDisplay } from '@garh/model';

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

/** The F10 dashboard status chips, in the order the product spec lists them. */
export const PROJECT_STAGES = ['brief', 'options', 'design', 'drawings'] as const;
export type ProjectStage = (typeof PROJECT_STAGES)[number];

/**
 * Stage state.
 *  - `todo`     nothing done yet
 *  - `active`   started, not finished (brief half-filled, options generated but
 *               none chosen)
 *  - `done`     complete enough to move on
 *  - `stale`    was done, but something upstream changed (renders/sheets after
 *               a model edit — the same `stale` flag the API sets on renders)
 */
export const STAGE_STATES = ['todo', 'active', 'done', 'stale'] as const;
export type StageState = (typeof STAGE_STATES)[number];

export type ProjectStages = Readonly<Record<ProjectStage, StageState>>;

/** Mirrors `projects.status` (CHECK-constrained in the DB). */
export type ProjectStatus = 'draft' | 'active' | 'archived';

export interface ProjectSummaryVM {
  id: string;
  name: string;
  /** Client or site name — the second line on the card. */
  clientName?: string | undefined;
  /** City pack id: 'blr' | 'ncr' | 'hyd' | 'custom'. */
  cityPack?: string | undefined;
  /** Human city label for display ("Bengaluru"). */
  cityLabel?: string | undefined;
  /** Integer mm². `null` until the plot is drawn. */
  plotAreaMm2: number | null;
  /** "G+1 · 3 BHK" — precomputed by the page from the brief. */
  configuration?: string | undefined;
  status: ProjectStatus;
  stages: ProjectStages;
  /** ISO 8601. Rendered as DD-MM-YYYY. */
  updatedAt: string;
  unitsDisplay: UnitsDisplay;
  /** The seeded demo project gets a badge and never a delete button. */
  isDemo: boolean;
  /** Mini-plan preview, when one has been generated. */
  thumbnailUrl?: string | undefined;
}

// ---------------------------------------------------------------------------
// Compliance
// ---------------------------------------------------------------------------

/** Mirrors the rules-engine result status. */
export type ComplianceResultStatus = 'pass' | 'warn' | 'fail' | 'not_applicable';

export interface ComplianceIssueVM {
  /** Rule id from the pack: "blr.setback.front.9m". */
  ruleId: string;
  status: ComplianceResultStatus;
  /** The one-line human sentence, written by the rules layer. */
  message: string;
  /** "BBMP Bye-laws 2020, Table 6a". */
  cite?: string | undefined;
  /** Packs ship at `seed` until an empanelled architect reviews them. */
  confidence?: 'seed' | 'reviewed' | 'verified' | undefined;
  /** Element ids to highlight on the canvas when the chip is clicked. */
  elementIds: readonly string[];
  /** True when the pack supplies a computable auto-fix op. */
  fixAvailable: boolean;
  /** Human hint shown when there is no computable fix. */
  fixHint?: string | undefined;
}

/**
 * A stable list key for a compliance result.
 *
 * `ruleId` alone is NOT unique. The rules engine evaluates scoped checks once
 * per element — `room_area_min` produces one result per room, `setback_min` one
 * per plot edge — so a three-bedroom plan yields three results carrying the
 * same rule id. Keying a list on `ruleId` gives React duplicate keys, which it
 * resolves by reusing the wrong DOM node: fix a bedroom and the chip that
 * disappears may be a different bedroom's.
 *
 * The scope is what disambiguates, so the key is the rule plus the elements it
 * was evaluated against. Callers append the index as a final tiebreak for the
 * pathological case of two unscoped results from one rule.
 */
export function complianceIssueKey(issue: ComplianceIssueVM): string {
  return issue.elementIds.length === 0
    ? issue.ruleId
    : `${issue.ruleId}@${[...issue.elementIds].join('+')}`;
}

// ---------------------------------------------------------------------------
// Jobs (solver / render / sheets / export)
// ---------------------------------------------------------------------------

export type JobKind = 'solver' | 'render' | 'sheets' | 'export';

/** Mirrors `JOB_STATUSES` in the API models module. */
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface JobVM {
  id: string;
  kind: JobKind;
  status: JobStatus;
  /** 0–100 when the worker reports real progress; `null` when it cannot. */
  progress: number | null;
  /**
   * The current stage message, driven by real worker events — §15 forbids
   * inventing these. "Placing staircase…", "Checking BBMP setbacks…".
   */
  stageMessage?: string | undefined;
  /** 1-based position while queued. `null` when running or unknown. */
  queuePosition: number | null;
  /** ISO 8601. */
  startedAt?: string | undefined;
  /** Problem detail on failure — always with a next action. */
  error?: Problem | undefined;
  /** Where the result lives once it succeeded. */
  resultHref?: string | undefined;
  resultLabel?: string | undefined;
}

// ---------------------------------------------------------------------------
// Errors — problem+json (§11)
// ---------------------------------------------------------------------------

/**
 * The API's error body: `{code, message, action}`. `action` is a human sentence
 * ("Refresh and try again"), not a machine instruction — `resolveRecovery` in
 * ErrorBoundary.tsx turns a code into an actual button.
 */
export interface Problem {
  code: string;
  message: string;
  action?: string | undefined;
  /** Present on 409 op-sequence conflicts (`OpSequenceConflictError`). */
  headIdx?: number | undefined;
  /** HTTP status, when the caller knows it. */
  status?: number | undefined;
}

// ---------------------------------------------------------------------------
// Diff preview (copilot + solver share one component, §12)
// ---------------------------------------------------------------------------

export type DiffOpKind = 'add' | 'move' | 'resize' | 'remove' | 'edit' | 'assign';

export interface DiffOpVM {
  /** Stable key for React. */
  id: string;
  /** The op type from the taxonomy, e.g. `wall.move`. Shown as a monospace tag. */
  opType: string;
  kind: DiffOpKind;
  /**
   * Plain language, no jargon (§15): "Move the kitchen wall 300 mm east" —
   * not "wall.move wall_01H…{a,b}".
   */
  text: string;
  /** Elements this op touches — used to highlight in the mini-canvases. */
  elementIds: readonly string[];
}

export interface DiffPreviewVM {
  /** What the user asked for, echoed back: "attached bath to bedroom 2". */
  intent: string;
  ops: readonly DiffOpVM[];
  /** Compliance deltas the change causes, so nothing is a surprise. */
  newIssues?: readonly ComplianceIssueVM[];
  resolvedIssues?: readonly ComplianceIssueVM[];
  /** Where the diff came from — drives the header wording. */
  source: 'copilot' | 'solver' | 'autofix';
}
