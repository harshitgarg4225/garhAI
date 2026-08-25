/**
 * `copilot` — the chat rail's state machine (§10, Phase 6).
 *
 * The pipeline, client side:
 *
 *   send(command)
 *      │  turn: 'thinking' — set when the request starts, cleared in the same
 *      │  code path that resolves it, so the indicator is honest by construction
 *      ├─ POST /projects/:id/copilot        (server: LLM → 4 gates → proposal)
 *      ├─ cannotDo / needsClarification →   honest cards, NOTHING dispatched
 *      └─ ops[] → model.dryRun(ops)         local fold on a fork, <10 ms (§14)
 *             ├─ ok   → 'ready': diff VM + before/after docs for the canvases
 *             └─ fail → 'error': the doc moved since the server checked — say so
 *
 *   apply(turn)  → model.dispatch(EXACTLY turn.ops, { groupId, source:'copilot' })
 *                  ONE pre-allocated group → one undo step; then the §15 toast
 *                  ("Copilot edit applied — Undo").
 *   reject(turn) → dispatches nothing, drops the fork. No trace anywhere.
 *
 * Golden rule 1 holds by construction: this store owns NO design state and
 * has no write path of its own. The only way a copilot suggestion reaches the
 * document is `useModelStore.dispatch` — the same validate-and-fold path as a
 * hand edit, where the server sequencer checks everything again.
 *
 * A zustand store (module-scoped) rather than component state so closing and
 * reopening the rail keeps the conversation, and so tests can drive it
 * without rendering.
 */

import { create } from 'zustand';

import type { Op } from '@garh/model';

import type { DiffPreviewVM, Problem } from '../../components/types';
import { AppError, ERROR_CODES, type ApiValidationIssue } from '../../lib/errors';
import { newGroupId } from '../../lib/ids';
import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useUiStore } from '../../stores/ui';

import { proposeCopilot } from './api';
import { pickDiffStoreyId } from './docPlan';
import { toDiffOps } from './plain';
import type { CopilotProposal, CopilotTurn, CopilotWireOp } from './types';

// ---------------------------------------------------------------------------
// Wire ops → model ops
// ---------------------------------------------------------------------------

/**
 * 1:1, order-preserving, nothing added, nothing dropped. The cast is the same
 * one `lib/schemas.ts::toModelOp` makes for persisted ops: the payload was
 * schema-validated server-side and will be validated AGAIN by the local fold
 * and by the sequencer — a client-side re-narrowing here would be a third
 * copy of the taxonomy that can only drift.
 */
export function toModelOps(ops: readonly CopilotWireOp[]): Op[] {
  return ops.map(
    (op) =>
      ({
        type: op.type,
        payload: op.payload,
        ...(op.clientOpId === undefined ? {} : { clientOpId: op.clientOpId }),
      }) as unknown as Op,
  );
}

// ---------------------------------------------------------------------------
// Problems — every failure names its next step (golden rule 9)
// ---------------------------------------------------------------------------

/**
 * The 429 message. Deliberately specific and calm: the per-firm LLM limiter is
 * FAIL-CLOSED (garh_api/ratelimit.py `llm.per_firm`) — when the limiter's
 * backend is down every copilot call is refused with this same 429, so this
 * copy must read as "wait a moment", never as "something is broken".
 */
function rateLimitProblem(error: AppError): Problem {
  const wait = error.retryAfterSeconds;
  const waitText =
    wait !== null && wait > 0
      ? wait <= 90
        ? `about ${Math.max(5, Math.round(wait / 5) * 5)} seconds`
        : `about ${Math.ceil(wait / 60)} minutes`
      : 'a minute';
  return {
    code: error.code,
    status: 429,
    message: `The copilot has hit this studio's AI usage limit for the moment. Your design is untouched.`,
    action: `Wait ${waitText} and press Retry — your command is kept. Drawing tools are unaffected.`,
  };
}

function toProblem(error: AppError): Problem {
  if (error.status === 429 || error.code === ERROR_CODES.rateLimited) {
    return rateLimitProblem(error);
  }
  return {
    code: error.code,
    message: error.message,
    action: error.action,
    ...(error.status === 0 ? {} : { status: error.status }),
  };
}

const STALE_FOLD_PROBLEM: Problem = {
  code: 'copilot_stale_fold',
  message: 'The design changed while the copilot was thinking, so its edit no longer fits.',
  action: 'Press Retry to get a suggestion against the current plan.',
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export interface CopilotState {
  turns: CopilotTurn[];
  /** True while a request is on the wire. The ONLY source of "thinking". */
  busy: boolean;
  /** Sent commands, oldest first — the input's ↑/↓ history. */
  history: string[];

  send: (command: string) => Promise<void>;
  /** Abort the in-flight request. The turn ends as 'cancelled'. */
  cancel: () => void;
  apply: (turnId: string) => void;
  reject: (turnId: string) => void;
  /** Answer a clarifying question — sends `command — reply` as a new turn. */
  clarify: (turnId: string, reply: string) => Promise<void>;
  /** Re-send a failed turn's command. */
  retry: (turnId: string) => Promise<void>;
  clear: () => void;
}

const HISTORY_MAX = 50;

let turnSeq = 0;
function nextTurnId(): string {
  turnSeq += 1;
  return `cpt_${turnSeq}`;
}

/** In-flight request controller. Module-level: timers/aborters are not state. */
let inflight: AbortController | null = null;

function emptyTurn(command: string): CopilotTurn {
  return {
    id: nextTurnId(),
    command,
    status: 'thinking',
    at: Date.now(),
    proposal: null,
    ops: [],
    groupId: null,
    diff: null,
    beforeDoc: null,
    afterDoc: null,
    storeyId: null,
    problem: null,
    issues: [],
  };
}

export const useCopilotStore = create<CopilotState>()((set, get) => {
  function patchTurn(id: string, patch: Partial<CopilotTurn>): void {
    set((s) => ({
      turns: s.turns.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    }));
  }

  /** Resolve a proposal into the turn's final pre-apply state. */
  function settle(turnId: string, proposal: CopilotProposal): void {
    if (proposal.cannotDo !== null) {
      patchTurn(turnId, { status: 'cannot', proposal });
      return;
    }
    if (proposal.needsClarification !== null) {
      patchTurn(turnId, { status: 'clarify', proposal });
      return;
    }
    if (!proposal.applicable || proposal.ops.length === 0) {
      // The server said "not applicable" without a refusal sentence. Should
      // not happen per the contract; refuse honestly rather than invent ops.
      patchTurn(turnId, {
        status: 'cannot',
        proposal: {
          ...proposal,
          cannotDo:
            "I couldn't turn that into a safe edit. Try describing it differently, or make the change with the drawing tools.",
        },
        issues: proposal.issues,
      });
      return;
    }

    // The dry-run fold on a fork (§10) — pure, local, never persisted. This is
    // both the after-document for the mini-canvas AND a re-validation against
    // the CURRENT doc, which may have moved since the server's own dry run.
    const model = useModelStore.getState();
    const beforeDoc = model.doc;
    const ops = toModelOps(proposal.ops);
    const outcome = model.dryRun(ops);

    if (!outcome.ok) {
      patchTurn(turnId, {
        status: 'error',
        proposal,
        problem: STALE_FOLD_PROBLEM,
        issues: outcome.issues.map((issue) => ({ ...issue })),
      });
      return;
    }

    const diffOps = toDiffOps(proposal.ops, proposal.plainLanguage, beforeDoc);
    const touched: string[] = [];
    for (const row of diffOps) {
      for (const id of row.elementIds) if (!touched.includes(id)) touched.push(id);
    }
    const diff: DiffPreviewVM = {
      intent: proposal.intent,
      ops: diffOps,
      source: 'copilot',
    };

    patchTurn(turnId, {
      status: 'ready',
      proposal,
      ops,
      // Pre-allocated so the diff the user approves and the group in the op
      // log are one and the same — and so undo grabs the whole thing at once.
      // The server mints one per proposal; using ITS id is what keeps §10's
      // eval log correlated (propose line ↔ decision line). Falling back to a
      // local id keeps the store usable against a stub that omits it.
      groupId: proposal.groupId ?? newGroupId(),
      diff,
      beforeDoc,
      afterDoc: outcome.doc,
      storeyId: pickDiffStoreyId(
        outcome.doc,
        touched,
        useUiStore.getState().activeStoreyId,
      ),
    });
  }

  return {
    turns: [],
    busy: false,
    history: [],

    send: async (command: string) => {
      const trimmed = command.trim();
      if (trimmed === '') return;
      // One request at a time — a second Enter must not silently fork the
      // conversation. The input stays editable; the send path just declines.
      if (get().busy) return;

      const model = useModelStore.getState();
      const turn = emptyTurn(trimmed);

      set((s) => ({
        turns: [...s.turns, turn],
        history: [...s.history.filter((h) => h !== trimmed), trimmed].slice(-HISTORY_MAX),
      }));

      if (model.projectId === null || model.status !== 'ready') {
        patchTurn(turn.id, {
          status: 'error',
          problem: {
            code: 'copilot_no_project',
            message: "The design hasn't finished loading yet.",
            action: 'Wait for the plan to appear, then send the command again.',
          },
        });
        return;
      }

      const controller = new AbortController();
      inflight = controller;
      set({ busy: true });

      try {
        const proposal = await proposeCopilot({
          projectId: model.projectId,
          command: trimmed,
          activeStoreyId: useUiStore.getState().activeStoreyId,
          selectionIds: useSelectionStore.getState().ids,
          signal: controller.signal,
        });
        settle(turn.id, proposal);
      } catch (err) {
        const error = AppError.from(err);
        if (error.isAborted) {
          patchTurn(turn.id, { status: 'cancelled' });
        } else {
          patchTurn(turn.id, { status: 'error', problem: toProblem(error) });
        }
      } finally {
        if (inflight === controller) inflight = null;
        set({ busy: false });
      }
    },

    cancel: () => {
      inflight?.abort();
    },

    apply: (turnId: string) => {
      const turn = get().turns.find((t) => t.id === turnId);
      if (turn === undefined || turn.status !== 'ready' || turn.groupId === null) return;
      if (turn.ops.length === 0) return;

      // EXACTLY the returned ops, as ONE group. `dispatch` stamps every op
      // with this groupId, folds them atomically, records ONE undo entry and
      // queues ONE append with source 'copilot' — nothing bespoke, no side door.
      const result = useModelStore.getState().dispatch(turn.ops, {
        label: 'Copilot edit',
        source: 'copilot',
        groupId: turn.groupId,
      });

      if (!result.ok) {
        // The doc moved between preview and click and the fold now refuses.
        patchTurn(turnId, {
          status: 'error',
          problem: STALE_FOLD_PROBLEM,
          issues: result.issues.map((issue) => ({ ...issue })),
          beforeDoc: null,
          afterDoc: null,
        });
        return;
      }

      patchTurn(turnId, { status: 'applied', beforeDoc: null, afterDoc: null });

      // §15: "Copilot edit applied — Undo". Same semantics as every other
      // undo toast: the action pops the most recent step, which is this group.
      useUiStore.getState().pushToast({
        tone: 'success',
        title: 'Copilot edit applied',
        description:
          turn.ops.length === 1 ? null : `${turn.ops.length} changes, one undo step.`,
        action: { label: 'Undo', run: () => void useModelStore.getState().undo() },
      });
    },

    reject: (turnId: string) => {
      const turn = get().turns.find((t) => t.id === turnId);
      if (turn === undefined) return;
      // Nothing is dispatched, nothing was ever folded into the real doc —
      // dropping the fork and the ops IS the whole operation.
      patchTurn(turnId, {
        status: 'rejected',
        ops: [],
        diff: null,
        beforeDoc: null,
        afterDoc: null,
      });
    },

    clarify: async (turnId: string, reply: string) => {
      const turn = get().turns.find((t) => t.id === turnId);
      if (turn === undefined) return;
      const clean = reply.trim();
      if (clean === '') return;
      await get().send(`${turn.command} — ${clean}`);
    },

    retry: async (turnId: string) => {
      const turn = get().turns.find((t) => t.id === turnId);
      if (turn === undefined) return;
      await get().send(turn.command);
    },

    clear: () => set({ turns: [] }),
  };
});

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectTurns = (s: CopilotState): CopilotTurn[] => s.turns;
export const selectBusy = (s: CopilotState): boolean => s.busy;
export const selectHistory = (s: CopilotState): string[] => s.history;

/** Issues → a Problem-shaped detail line for the error card. */
export function issueSummary(issues: readonly ApiValidationIssue[]): string | null {
  const first = issues[0];
  if (first === undefined) return null;
  return issues.length === 1 ? first.message : `${first.message} (+${issues.length - 1} more)`;
}
