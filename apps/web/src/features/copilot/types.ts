/**
 * Copilot view models — the shapes the panel renders, one file, no React.
 *
 * The server's contract (services/llm/copilot.py `CopilotProposal.to_json()`)
 * arrives through `api.ts` and is normalised into {@link CopilotProposal}
 * here. Everything downstream — the turn list, the diff preview, the tests —
 * works on these types and never on raw wire JSON.
 *
 * The one invariant that matters (§10, locked): the LLM's output only ever
 * becomes `@garh/model` typed ops that go through the SAME `dispatch` path as
 * a hand edit. A turn holds those ops verbatim; nothing in this feature
 * mutates, filters or "fixes up" what the validated route returned.
 */

import type { Op, ProjectDoc } from '@garh/model';

import type { ApiValidationIssue } from '../../lib/errors';
import type { DiffPreviewVM, Problem } from '../../components/types';

// ---------------------------------------------------------------------------
// The proposal, as the client consumes it
// ---------------------------------------------------------------------------

/** Wire op: `{type, payload}` exactly as the §4 taxonomy writes it. */
export interface CopilotWireOp {
  readonly type: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly clientOpId?: string | undefined;
}

/** `POST /projects/:id/copilot` response, zod-validated in `api.ts`. */
export interface CopilotProposal {
  /** True only when `ops` passed every server gate and may be previewed. */
  readonly applicable: boolean;
  /** One sentence echoing what the copilot understood. Always present. */
  readonly intent: string;
  readonly ops: readonly CopilotWireOp[];
  /** One plain-language line per op, same order. May be shorter than `ops`. */
  readonly plainLanguage: readonly string[];
  /** The honest refusal (§10) — never accompanied by ops. */
  readonly cannotDo: string | null;
  /** The question to ask before proposing anything. Never with ops either. */
  readonly needsClarification: string | null;
  /** Why the server rejected the candidate ops, when it did. */
  readonly issues: readonly ApiValidationIssue[];
  readonly selfCorrected: boolean;

  // ── Server-minted, added in the Phase-6 integration ────────────────────
  // All optional: the store falls back to its own values, and the tests
  // construct proposals without them.

  /**
   * The proposal's group id, minted by the route. Applying with THIS id is
   * what lets §10's eval log correlate "here is what I proposed" with "here is
   * what the human did about it". The store prefers it over a locally minted
   * one and only falls back when the field is absent.
   */
  readonly groupId?: string | undefined;
  /** Branch HEAD the server validated against. A 409 on apply means it moved. */
  readonly baseIdx?: number | undefined;
  /** 1, or 2 when the single self-correction pass was used (§10). */
  readonly attempts?: number | undefined;
  /**
   * Whether the rules engine actually ran on the server's dry run. `false`
   * means "could not be checked" (no plot boundary yet) — never a pass.
   */
  readonly rulesChecked?: boolean | undefined;
  /** §14 telemetry: the server-side dry-run fold duration, in ms. */
  readonly dryRunMs?: number | undefined;
}

// ---------------------------------------------------------------------------
// Turns
// ---------------------------------------------------------------------------

/**
 * Lifecycle of one command.
 *
 *   thinking → ready → applied | rejected      (the happy path)
 *   thinking → cannot | clarify                (honest non-answers)
 *   thinking → error | cancelled               (transport / stale / stopped)
 *
 * "thinking" is honest by construction: it is set when the request starts and
 * cleared in the same code path that resolves it, so the indicator can never
 * outlive the request (§15 — job state shown honestly).
 */
export type CopilotTurnStatus =
  | 'thinking'
  | 'ready'
  | 'applied'
  | 'rejected'
  | 'cannot'
  | 'clarify'
  | 'error'
  | 'cancelled';

export interface CopilotTurn {
  readonly id: string;
  /** The command as typed (trimmed). Echoed as the user bubble. */
  readonly command: string;
  readonly status: CopilotTurnStatus;
  readonly at: number;

  /** The server's answer, once it arrived. */
  readonly proposal: CopilotProposal | null;

  /**
   * The model-core ops, converted 1:1 from `proposal.ops`. These are what
   * Apply dispatches — EXACTLY these, no more, no fewer.
   */
  readonly ops: readonly Op[];

  /**
   * Pre-allocated group id. Allocated when the diff becomes ready so the diff
   * the user saw and the group that lands in the op log are the same thing,
   * and so tests can assert "one group, this group".
   */
  readonly groupId: string | null;

  /** View model for the shared DiffPreview. Present only while `ready`. */
  readonly diff: DiffPreviewVM | null;

  /**
   * Before/after documents for the mini-canvases. `after` comes from the
   * client-side dry-run fold (`model.dryRun`) — pure, local, never persisted.
   * Both are dropped on apply/reject so a long chat does not pin old docs.
   */
  readonly beforeDoc: ProjectDoc | null;
  readonly afterDoc: ProjectDoc | null;
  /** Storey the mini-canvases draw (the one the change touches). */
  readonly storeyId: string | null;

  /** Failure detail for the `error` status. Always has a next action. */
  readonly problem: Problem | null;
  /** Client-fold rejections (the design moved since the server checked). */
  readonly issues: readonly ApiValidationIssue[];
}
