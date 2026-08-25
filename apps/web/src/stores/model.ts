/**
 * `model` — THE ONLY WRITER of design state (§12, golden rule 1).
 *
 * Components never mutate the document. They call {@link ModelState.dispatch}
 * with typed ops from `@garh/model`, and everything else — folding, queueing,
 * conflict resolution, undo history, the autosave badge — happens here.
 *
 * ## The optimistic pipeline
 *
 * ```
 *   dispatch(ops)
 *      │
 *      ├─ applyGroup(doc, ops)         local fold, <10 ms (§14)
 *      │     └─ rejected? nothing changed, return the issues, done.
 *      ├─ doc = next                   canvas repaints immediately
 *      ├─ pending.push(group)          queued for the server
 *      └─ flush()                      serial, one group at a time
 *              ├─ 200 → fold the same ops onto serverDoc, advance baseIdx,
 *              │        and (when quiescent) check our stateHash against the
 *              │        server's. A mismatch means the two folds disagree,
 *              │        which is not a thing to paper over: reload.
 *              ├─ 409 → REBASE: pull ops since baseIdx, replay them onto
 *              │        serverDoc, re-apply the pending queue on top, retry.
 *              ├─ 422 → ROLL BACK that group, drop it from history, toast why.
 *              └─ 5xx / offline → keep the queue, back off, badge says so.
 * ```
 *
 * Two documents are held, and the distinction is the whole design:
 *
 *   - **`serverDoc`** is what the server has confirmed, folded to `baseIdx`.
 *     It is the only safe base for a rebase.
 *   - **`doc`** is `serverDoc` + every pending group. It is what the canvas
 *     draws, and it is never derived from itself — every recomputation starts
 *     from `serverDoc` and re-applies the queue, so a rollback cannot leave
 *     half an edit behind.
 *
 * ## Undo
 *
 * Undo does not delete history: it appends the inverse ops as a new group, per
 * §4. That keeps the op log append-only, which is what makes version restore
 * and provenance work at all. The consequence is that undo is itself an
 * optimistic dispatch and can itself be rejected — handled, not hidden.
 *
 * ## Why not `UndoStack` from `@garh/model`
 *
 * The model core ships a perfectly good `UndoStack`, and this store deliberately
 * keeps its own arrays instead. The reason: a group can be *rejected by the
 * server after it was pushed onto the stack*, and the history has to forget it
 * — including recomputing the inverses of every group that was re-applied on
 * top of a rebase. `UndoStack` has no removal API and should not grow one for
 * a client-only concern. The entry shape is the core's `UndoEntry`, so the two
 * stay interchangeable if that ever changes.
 */

import { create } from 'zustand';

import {
  OpRejectedError,
  applyGroup,
  emptyProjectDoc,
  fold,
  stateHash,
  type Op,
  type OpSource,
  type ProjectDoc,
  type ValidationIssue,
} from '@garh/model';

import { api } from '../lib/api';
import { env } from '../lib/env';
import { AppError, OpConflictError, OpRejectionError } from '../lib/errors';
import { newClientOpId, newGroupId } from '../lib/ids';
import { asProjectDoc, toModelOp, type PersistedOp } from '../lib/schemas';
import { formatVersionLabel } from '../lib/units';
import { useSelectionStore } from './selection';
import { useUiStore } from './ui';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ModelStatus = 'idle' | 'loading' | 'ready' | 'error';

/**
 * What the autosave badge shows (§15 "Saved · v214").
 *
 * `conflict` is not in the list on purpose: a conflict is resolved by rebasing,
 * automatically, and flashing a scary word at the user for something the client
 * handled in 200 ms would be theatre.
 */
export type SaveState = 'idle' | 'saved' | 'pending' | 'saving' | 'offline' | 'error';

/** A group of ops applied locally and waiting for the server. */
export interface PendingGroup {
  readonly groupId: string;
  /** Exactly as applied, each carrying its `clientOpId` — see the note below. */
  readonly ops: readonly Op[];
  /** Recomputed on every rebase, because an inverse is state-dependent. */
  readonly inverse: readonly Op[];
  readonly label: string;
  readonly source: OpSource;
  readonly attempts: number;
}

/** One undoable unit. Same shape as `@garh/model`'s `UndoEntry`. */
export interface HistoryEntry {
  readonly groupId: string;
  readonly ops: readonly Op[];
  readonly inverse: readonly Op[];
  readonly label: string;
}

export interface DispatchOptions {
  /** Undo-toast copy: "Wall deleted". Sentence case, no trailing period. */
  readonly label?: string;
  readonly source?: OpSource;
  /** Skip the undo stack — used by undo/redo themselves, which manage it. */
  readonly recordHistory?: boolean;
  /** Reuse an existing group id (the copilot pre-allocates one for its diff). */
  readonly groupId?: string;
}

export type DispatchResult =
  | { readonly ok: true; readonly groupId: string; readonly doc: ProjectDoc }
  | { readonly ok: false; readonly issues: readonly ValidationIssue[] };

export interface SaveBadge {
  readonly state: SaveState;
  /** `Saved · v214`, `Saving…`, `Offline — 3 changes waiting`. */
  readonly label: string;
  readonly detail: string | null;
}

export interface ModelState {
  projectId: string | null;
  versionBranch: string | null;
  status: ModelStatus;
  loadError: AppError | null;

  /** Optimistic document: `serverDoc` + the pending queue. What you see. */
  doc: ProjectDoc;
  /** Server-confirmed document, folded to `baseIdx`. The rebase base. */
  serverDoc: ProjectDoc;
  baseIdx: number;
  headIdx: number;

  pending: PendingGroup[];
  flushing: boolean;
  saveState: SaveState;
  lastSavedAt: number | null;
  syncError: AppError | null;
  /**
   * Set when our fold and the server's produced different state hashes. The
   * document on screen can no longer be trusted, so the UI must offer a reload
   * rather than let the user keep drawing on a divergent state.
   */
  divergedAt: number | null;

  undoStack: HistoryEntry[];
  redoStack: HistoryEntry[];

  // ── actions ────────────────────────────────────────────────────────────
  hydrate: (projectId: string, options?: { version?: string | null }) => Promise<void>;
  reset: () => void;

  /** Apply ops as one atomic group. The only way design state changes. */
  dispatch: (ops: readonly Op[], options?: DispatchOptions) => DispatchResult;
  dispatchOne: (op: Op, options?: DispatchOptions) => DispatchResult;

  /**
   * Fold ops against the current document WITHOUT applying them. The copilot
   * and solver diff previews use this (§10: dry-run fold on a fork).
   */
  dryRun: (
    ops: readonly Op[],
  ) => { ok: true; doc: ProjectDoc } | { ok: false; issues: readonly ValidationIssue[] };

  undo: () => boolean;
  redo: () => boolean;

  /** Push the queue now. Resolves when it is empty or has stalled. */
  flush: () => Promise<void>;
  /** Fetch and apply remote ops (after a solver apply, or on window focus). */
  pull: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Module-level plumbing (timers are not state)
// ---------------------------------------------------------------------------

let retryTimer: ReturnType<typeof setTimeout> | null = null;
/** Guards against two flush loops running at once across async boundaries. */
let flushInflight: Promise<void> | null = null;

const MAX_RETRY_DELAY_MS = 30_000;
const BASE_RETRY_DELAY_MS = 750;
/** A group that keeps failing is dropped rather than retried forever. */
const MAX_ATTEMPTS = 8;
/** Safety valve on the catch-up loop in `hydrate`. */
const MAX_TAIL_PAGES = 50;

function clearRetry(): void {
  if (retryTimer !== null) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
}

type GroupOutcome =
  | { ok: true; model: ProjectDoc; inverse: readonly Op[]; ops: readonly Op[] }
  | { ok: false; issues: readonly ValidationIssue[] };

/** `applyGroup` without the exception — atomic, so a failure changes nothing. */
function tryApplyGroup(doc: ProjectDoc, ops: readonly Op[], groupId: string): GroupOutcome {
  try {
    const result = applyGroup(doc, ops, groupId);
    return { ok: true, model: result.model, inverse: result.inverse, ops: result.ops };
  } catch (err) {
    if (err instanceof OpRejectedError) return { ok: false, issues: err.issues };
    throw err;
  }
}

/**
 * Stamp every op with its group and a `clientOpId`.
 *
 * The `clientOpId` is generated ONCE, here, and travels with the op for the
 * rest of its life. Generating it at send time instead would give every retry a
 * fresh id, which defeats the server's deduplication and turns a timeout into a
 * duplicated wall.
 */
function stampOps(ops: readonly Op[], groupId: string): Op[] {
  return ops.map((op) => {
    const meta = op as { clientOpId?: string };
    return {
      ...op,
      groupId,
      clientOpId: meta.clientOpId ?? newClientOpId(),
    } as Op;
  });
}

/** Every element id in a document — for pruning a stale selection. */
function collectElementIds(doc: ProjectDoc): Set<string> {
  const ids = new Set<string>();
  const h = doc.house;
  for (const s of h.storeys) ids.add(s.id);
  for (const w of h.walls) ids.add(w.id);
  for (const o of h.openings) ids.add(o.id);
  for (const r of h.rooms) ids.add(r.id);
  for (const s of h.stairs) ids.add(s.id);
  for (const s of h.slabs) ids.add(s.id);
  for (const c of h.columns) ids.add(c.id);
  for (const f of h.furniture) ids.add(f.id);
  for (const b of h.balconies) ids.add(b.id);
  for (const m of h.materials) ids.add(m.id);
  for (const c of h.facade.components) ids.add(c.id);
  for (const a of doc.annotations) ids.add(a.id);
  return ids;
}

function toast(input: Parameters<ReturnType<typeof useUiStore.getState>['pushToast']>[0]): void {
  useUiStore.getState().pushToast(input);
}

/** Plain-language summary of why an op was refused. */
function describeIssues(issues: readonly ValidationIssue[]): string {
  const first = issues[0];
  if (!first) return 'That change is not valid here.';
  return issues.length === 1 ? first.message : `${first.message} (+${issues.length - 1} more)`;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

/**
 * The empty document. Called with NO argument on purpose: `emptyProjectDoc()`
 * defaults to `'ft-in'`, and the Python mirror does the same. Passing the
 * user's preference here would change `house.meta.unitsDisplay`, and therefore
 * the canonical JSON, and therefore the state hash — silently, and only for
 * projects whose log replays from empty.
 */
function freshDoc(): ProjectDoc {
  return emptyProjectDoc();
}

export const useModelStore = create<ModelState>()((set, get) => ({
  projectId: null,
  versionBranch: null,
  status: 'idle',
  loadError: null,

  doc: freshDoc(),
  serverDoc: freshDoc(),
  baseIdx: -1,
  headIdx: -1,

  pending: [],
  flushing: false,
  saveState: 'idle',
  lastSavedAt: null,
  syncError: null,
  divergedAt: null,

  undoStack: [],
  redoStack: [],

  // ── Loading ────────────────────────────────────────────────────────────

  hydrate: async (projectId, options = {}) => {
    clearRetry();
    set({
      projectId,
      status: 'loading',
      loadError: null,
      pending: [],
      undoStack: [],
      redoStack: [],
      syncError: null,
      divergedAt: null,
      saveState: 'idle',
    });

    try {
      const state = await api.ops.model(projectId, {
        ...(options.version == null ? {} : { version: options.version }),
      });

      let doc = state.snapshot == null ? freshDoc() : asProjectDoc(state.snapshot);
      let idx = state.baseIdx;
      doc = foldPersisted(doc, state.ops);
      idx = lastIdx(state.ops, idx);

      // The server caps the tail it will inline. Walk the rest before showing
      // anything: a half-loaded plan is worse than a skeleton for another 200ms.
      let pages = 0;
      while (idx < state.headIdx && pages < MAX_TAIL_PAGES) {
        const page = await api.ops.since(projectId, idx);
        if (page.ops.length === 0) break;
        doc = foldPersisted(doc, page.ops);
        idx = lastIdx(page.ops, idx);
        pages += 1;
      }

      // The server's hash is authoritative. If ours differs, our fold and its
      // fold disagree — a model-core version skew — and every subsequent op we
      // send would compound the divergence.
      const serverHash = state.stateHash;
      const diverged = serverHash != null && stateHash(doc) !== serverHash;

      set({
        doc,
        serverDoc: doc,
        baseIdx: idx,
        headIdx: Math.max(idx, state.headIdx),
        versionBranch: state.versionBranch,
        status: 'ready',
        saveState: 'saved',
        lastSavedAt: Date.now(),
        divergedAt: diverged ? Date.now() : null,
      });

      // Point the canvas at a storey that exists.
      const ui = useUiStore.getState();
      const first = doc.house.storeys[0];
      const stillValid =
        ui.activeStoreyId !== null && doc.house.storeys.some((s) => s.id === ui.activeStoreyId);
      if (!stillValid) ui.setActiveStorey(first?.id ?? null);

      if (diverged) {
        toast({
          tone: 'error',
          title: 'This design loaded differently than the server has it.',
          action: { label: 'Reload', run: () => window.location.reload() },
          durationMs: 0,
        });
      }
    } catch (err) {
      const error = AppError.from(err);
      if (error.isAborted) return;
      set({ status: 'error', loadError: error });
    }
  },

  reset: () => {
    clearRetry();
    flushInflight = null;
    set({
      projectId: null,
      versionBranch: null,
      status: 'idle',
      loadError: null,
      doc: freshDoc(),
      serverDoc: freshDoc(),
      baseIdx: -1,
      headIdx: -1,
      pending: [],
      flushing: false,
      saveState: 'idle',
      lastSavedAt: null,
      syncError: null,
      divergedAt: null,
      undoStack: [],
      redoStack: [],
    });
  },

  // ── Dispatch ───────────────────────────────────────────────────────────

  dispatch: (ops, options = {}) => {
    if (ops.length === 0) {
      return { ok: false, issues: [] };
    }
    const s = get();

    // Refuse to write on top of a document we know is wrong. Continuing would
    // send ops the server cannot apply and bury the real problem.
    if (s.divergedAt !== null) {
      return {
        ok: false,
        issues: [
          {
            code: 'SCHEMA_VERSION_UNSUPPORTED',
            message: 'This design is out of sync with the server. Reload before editing.',
            severity: 'error',
            elementIds: [],
            fix: 'Reload the page.',
          },
        ],
      };
    }

    const groupId = options.groupId ?? newGroupId();
    const stamped = stampOps(ops, groupId);

    // The <10 ms path (§14). Everything after this is off the interaction
    // critical path.
    const outcome = tryApplyGroup(s.doc, stamped, groupId);
    if (!outcome.ok) return { ok: false, issues: outcome.issues };

    const group: PendingGroup = {
      groupId,
      ops: outcome.ops,
      inverse: outcome.inverse,
      label: options.label ?? 'Change',
      source: options.source ?? 'manual',
      attempts: 0,
    };

    const record = options.recordHistory !== false;
    set({
      doc: outcome.model,
      pending: [...s.pending, group],
      saveState: 'pending',
      syncError: null,
      undoStack: record
        ? [...s.undoStack, { groupId, ops: group.ops, inverse: group.inverse, label: group.label }]
        : s.undoStack,
      // A new edit forks history: whatever was undone is no longer redoable.
      redoStack: record ? [] : s.redoStack,
    });

    useSelectionStore.getState().prune(collectElementIds(outcome.model));
    void get().flush();

    return { ok: true, groupId, doc: outcome.model };
  },

  dispatchOne: (op, options) => get().dispatch([op], options),

  dryRun: (ops) => {
    const outcome = tryApplyGroup(get().doc, ops, 'dryrun');
    return outcome.ok ? { ok: true, doc: outcome.model } : { ok: false, issues: outcome.issues };
  },

  // ── Undo / redo ────────────────────────────────────────────────────────

  undo: () => {
    const s = get();
    const entry = s.undoStack[s.undoStack.length - 1];
    if (!entry) return false;

    // Pop first: whether the inverse applies or not, this entry is spent.
    set({ undoStack: s.undoStack.slice(0, -1) });

    const result = get().dispatch(entry.inverse, {
      label: `Undo ${entry.label.toLowerCase()}`,
      source: 'manual',
      recordHistory: false,
    });

    if (!result.ok) {
      // The state moved under this entry (a rebase, or a later edit that
      // removed what it referred to). Say so rather than silently doing nothing.
      toast({
        tone: 'warning',
        title: "That change can't be undone any more.",
        description: describeIssues(result.issues),
      });
      return false;
    }

    set((st) => ({ redoStack: [...st.redoStack, entry] }));
    return true;
  },

  redo: () => {
    const s = get();
    const entry = s.redoStack[s.redoStack.length - 1];
    if (!entry) return false;

    set({ redoStack: s.redoStack.slice(0, -1) });

    const result = get().dispatch(entry.ops, {
      label: entry.label,
      source: 'manual',
      recordHistory: false,
    });

    if (!result.ok) {
      toast({
        tone: 'warning',
        title: "That change can't be redone any more.",
        description: describeIssues(result.issues),
      });
      return false;
    }

    set((st) => ({
      undoStack: [
        ...st.undoStack,
        { groupId: result.groupId, ops: entry.ops, inverse: entry.inverse, label: entry.label },
      ],
    }));
    return true;
  },

  // ── Sync ───────────────────────────────────────────────────────────────

  flush: () => {
    if (flushInflight) return flushInflight;
    const run = runFlush(set, get).finally(() => {
      flushInflight = null;
    });
    flushInflight = run;
    return run;
  },

  pull: async () => {
    const s = get();
    if (!s.projectId || s.status !== 'ready') return;

    /*
     * Never rebase around an in-flight append.
     *
     * If a group is on the wire when we fetch the tail, the tail does not
     * contain it — so the rebase can conclude the group "no longer applies"
     * and drop it locally, moments before the server confirms it applied. The
     * two states then disagree permanently, and the user's edit is on screen
     * nowhere but the server.
     *
     * Draining first costs nothing: the flush's own 409 path rebases at the
     * one moment it is safe to, which is after the server has told us the
     * group was NOT applied.
     */
    if (s.pending.length > 0 || s.flushing) {
      await get().flush();
      return;
    }

    try {
      const page = await api.ops.since(s.projectId, s.baseIdx);
      if (page.ops.length === 0) {
        set({ headIdx: Math.max(s.headIdx, page.headIdx) });
        return;
      }
      rebaseOnto(set, get, page.ops, page.headIdx);
    } catch (err) {
      const error = AppError.from(err);
      if (!error.isAborted) set({ syncError: error });
    }
  },
}));

// ---------------------------------------------------------------------------
// Flush loop
// ---------------------------------------------------------------------------

type SetState = (
  partial:
    | Partial<ModelState>
    | ((state: ModelState) => Partial<ModelState>),
) => void;
type GetState = () => ModelState;

async function runFlush(set: SetState, get: GetState): Promise<void> {
  const initial = get();
  if (initial.pending.length === 0 || !initial.projectId) return;
  if (initial.divergedAt !== null) return;

  clearRetry();
  set({ flushing: true, saveState: 'saving' });

  try {
    for (;;) {
      const s = get();
      const group = s.pending[0];
      if (!group || !s.projectId) break;

      try {
        const result = await api.ops.append({
          projectId: s.projectId,
          ops: group.ops,
          baseIdx: s.baseIdx,
          groupId: group.groupId,
          source: group.source,
          ...(s.versionBranch === null ? {} : { versionBranch: s.versionBranch }),
        });
        acceptAppend(set, get, group, result);
        continue;
      } catch (err) {
        // 409 — someone else's ops landed first. Rebase and retry this group.
        if (err instanceof OpConflictError) {
          const rebased = await pullAndRebase(set, get, err.headIdx);
          if (rebased) continue;
          set({ saveState: 'error', syncError: err, flushing: false });
          return;
        }

        // 422 — the server refused the op itself. Retrying cannot help.
        if (err instanceof OpRejectionError) {
          rollbackGroup(set, get, group.groupId);
          toast({
            tone: 'error',
            title: err.issues[0]?.message ?? err.message,
            description: err.firstFix ?? err.action,
            requestId: err.requestId,
          });
          continue;
        }

        const error = AppError.from(err);
        if (error.isAborted) {
          set({ flushing: false });
          return;
        }

        if (error.retryable && group.attempts + 1 < MAX_ATTEMPTS) {
          bumpAttempts(set, get, group.groupId);
          const attempts = group.attempts + 1;
          const delay =
            error.retryAfterSeconds != null
              ? error.retryAfterSeconds * 1000
              : Math.min(BASE_RETRY_DELAY_MS * 2 ** (attempts - 1), MAX_RETRY_DELAY_MS);
          set({
            flushing: false,
            saveState: error.isOffline ? 'offline' : 'error',
            syncError: error,
          });
          scheduleRetry(get, delay);
          return;
        }

        // Out of options: drop the group so the queue is not blocked forever,
        // and say clearly that the edit did not stick.
        rollbackGroup(set, get, group.groupId);
        set({ saveState: 'error', syncError: error });
        toast({
          tone: 'error',
          title: `We couldn't save "${group.label.toLowerCase()}".`,
          description: error.action,
          requestId: error.requestId,
          dedupeKey: 'flush-failed',
        });
        continue;
      }
    }

    const done = get();
    set({
      flushing: false,
      saveState: done.pending.length === 0 ? 'saved' : 'pending',
      lastSavedAt: done.pending.length === 0 ? Date.now() : done.lastSavedAt,
      syncError: done.pending.length === 0 ? null : done.syncError,
    });
  } catch (err) {
    // A bug in this loop must not leave `flushing` stuck true, which would wedge
    // the queue permanently.
    set({ flushing: false, saveState: 'error', syncError: AppError.from(err) });
  }
}

function scheduleRetry(get: GetState, delayMs: number): void {
  clearRetry();
  retryTimer = setTimeout(() => {
    retryTimer = null;
    void get().flush();
  }, delayMs);
}

function bumpAttempts(set: SetState, get: GetState, groupId: string): void {
  set({
    pending: get().pending.map((g) =>
      g.groupId === groupId ? { ...g, attempts: g.attempts + 1 } : g,
    ),
  });
}

/** A group the server accepted: fold it onto `serverDoc` and advance. */
function acceptAppend(
  set: SetState,
  get: GetState,
  group: PendingGroup,
  result: {
    firstIdx: number;
    lastIdx: number;
    headIdx: number;
    stateHash: string | null;
    alreadyApplied: boolean;
  },
): void {
  const s = get();

  // Same ops, same fold, same result — this is what makes the op log work.
  // A throw here would mean the client's own two documents disagree, which is
  // a bug in this store, not a user-facing condition.
  let nextServer: ProjectDoc;
  try {
    nextServer = applyGroup(s.serverDoc, group.ops, group.groupId).model;
  } catch (err) {
    set({
      divergedAt: Date.now(),
      saveState: 'error',
      syncError: AppError.from(err),
      pending: s.pending.filter((g) => g.groupId !== group.groupId),
    });
    return;
  }

  const remaining = s.pending.filter((g) => g.groupId !== group.groupId);

  // Hashing the whole document is not free. Do it when the queue has drained
  // (the natural quiescent checkpoint) and always in development, where a
  // divergence should stop the world immediately.
  let diverged = false;
  if (result.stateHash != null && (env.isDev || remaining.length === 0)) {
    diverged = stateHash(nextServer) !== result.stateHash;
  }

  set({
    serverDoc: nextServer,
    baseIdx: result.lastIdx,
    headIdx: Math.max(result.headIdx, result.lastIdx),
    pending: remaining,
    syncError: null,
    divergedAt: diverged ? Date.now() : s.divergedAt,
  });

  if (diverged) {
    toast({
      tone: 'error',
      title: 'This design has drifted out of sync with the server.',
      description: 'Reload to pick up the authoritative version. Recent edits were saved.',
      action: { label: 'Reload', run: () => window.location.reload() },
      durationMs: 0,
    });
  }
}

/** Fetch ops since `baseIdx` and rebase. Returns false if the fetch failed. */
async function pullAndRebase(set: SetState, get: GetState, serverHeadIdx: number): Promise<boolean> {
  const s = get();
  if (!s.projectId) return false;
  try {
    const page = await api.ops.since(s.projectId, s.baseIdx);
    rebaseOnto(set, get, page.ops, Math.max(page.headIdx, serverHeadIdx));
    return true;
  } catch (err) {
    set({ syncError: AppError.from(err) });
    return false;
  }
}

/**
 * REBASE. Replay remote ops onto `serverDoc`, then re-apply the pending queue
 * on top of the result.
 *
 * A pending group that no longer applies — because the remote change deleted
 * the wall it moved, say — is dropped, removed from history, and reported. The
 * alternative (forcing it through) is how two tabs produce a plan neither
 * person drew.
 */
function rebaseOnto(
  set: SetState,
  get: GetState,
  remoteOps: readonly PersistedOp[],
  newHeadIdx: number,
): void {
  const s = get();

  let nextServer = s.serverDoc;
  try {
    nextServer = foldPersisted(nextServer, remoteOps);
  } catch (err) {
    // We cannot fold what the server says happened. Reload is the only honest
    // option — carrying on would diverge further with every edit.
    set({ divergedAt: Date.now(), syncError: AppError.from(err) });
    toast({
      tone: 'error',
      title: "We couldn't apply a change made elsewhere.",
      description: 'Reload to get back in sync.',
      action: { label: 'Reload', run: () => window.location.reload() },
      durationMs: 0,
    });
    return;
  }

  const kept: PendingGroup[] = [];
  const droppedIds: string[] = [];
  const droppedLabels: string[] = [];
  let nextDoc = nextServer;

  for (const group of s.pending) {
    const outcome = tryApplyGroup(nextDoc, group.ops, group.groupId);
    if (outcome.ok) {
      nextDoc = outcome.model;
      // The inverse is recomputed against the NEW state. Keeping the old one
      // would make undo apply an inverse for a state that no longer exists.
      kept.push({ ...group, inverse: outcome.inverse });
    } else {
      droppedIds.push(group.groupId);
      droppedLabels.push(group.label);
    }
  }

  const dropped = new Set(droppedIds);
  set({
    serverDoc: nextServer,
    doc: nextDoc,
    baseIdx: newHeadIdx,
    headIdx: newHeadIdx,
    pending: kept,
    undoStack: syncHistory(s.undoStack, kept, dropped),
    redoStack: s.redoStack.filter((e) => !dropped.has(e.groupId)),
  });

  useSelectionStore.getState().prune(collectElementIds(nextDoc));

  if (droppedLabels.length > 0) {
    toast({
      tone: 'warning',
      title:
        droppedLabels.length === 1
          ? `"${droppedLabels[0] ?? 'A change'}" was undone — the design changed elsewhere.`
          : `${droppedLabels.length} of your changes were undone — the design changed elsewhere.`,
      description: 'Nothing was lost on the server; re-apply them if you still want them.',
    });
  }
}

/**
 * Roll one group back: remove it from the queue, rebuild `doc` from
 * `serverDoc` plus what is left, and forget it in history.
 *
 * Rebuilding from `serverDoc` rather than trying to "un-apply" in place is the
 * point — an inverse computed against an older state is not guaranteed to
 * apply, and the rebuild is exact by construction.
 */
function rollbackGroup(set: SetState, get: GetState, groupId: string): void {
  const s = get();
  const remaining = s.pending.filter((g) => g.groupId !== groupId);

  const kept: PendingGroup[] = [];
  const droppedIds = new Set<string>([groupId]);
  let nextDoc = s.serverDoc;

  for (const group of remaining) {
    const outcome = tryApplyGroup(nextDoc, group.ops, group.groupId);
    if (outcome.ok) {
      nextDoc = outcome.model;
      kept.push({ ...group, inverse: outcome.inverse });
    } else {
      // A follow-on edit that only made sense on top of the rejected one.
      droppedIds.add(group.groupId);
    }
  }

  set({
    doc: nextDoc,
    pending: kept,
    undoStack: syncHistory(s.undoStack, kept, droppedIds),
    redoStack: s.redoStack.filter((e) => !droppedIds.has(e.groupId)),
  });

  useSelectionStore.getState().prune(collectElementIds(nextDoc));
}

/**
 * Keep the undo stack consistent with the queue: forget dropped groups, and
 * refresh the inverses of groups that were re-applied against a new state.
 */
function syncHistory(
  stack: readonly HistoryEntry[],
  kept: readonly PendingGroup[],
  dropped: ReadonlySet<string>,
): HistoryEntry[] {
  const byId = new Map(kept.map((g) => [g.groupId, g]));
  const out: HistoryEntry[] = [];
  for (const entry of stack) {
    if (dropped.has(entry.groupId)) continue;
    const group = byId.get(entry.groupId);
    out.push(group ? { ...entry, inverse: group.inverse } : entry);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Fold helpers
// ---------------------------------------------------------------------------

/** Fold persisted (server-validated) ops. `computeInverse: false` — no undo needed. */
function foldPersisted(doc: ProjectDoc, ops: readonly PersistedOp[]): ProjectDoc {
  let current = doc;
  for (const op of ops) {
    current = fold(current, toModelOp(op), { computeInverse: false }).model;
  }
  return current;
}

function lastIdx(ops: readonly PersistedOp[], fallback: number): number {
  const last = ops[ops.length - 1];
  return last ? last.idx : fallback;
}

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectDoc = (s: ModelState): ProjectDoc => s.doc;
export const selectHouse = (s: ModelState) => s.doc.house;
export const selectPlot = (s: ModelState) => s.doc.plot;
export const selectBrief = (s: ModelState) => s.doc.brief;
export const selectStatus = (s: ModelState): ModelStatus => s.status;
export const selectIsReady = (s: ModelState): boolean => s.status === 'ready';
export const selectCanUndo = (s: ModelState): boolean => s.undoStack.length > 0;
export const selectCanRedo = (s: ModelState): boolean => s.redoStack.length > 0;
export const selectNextUndoLabel = (s: ModelState): string | null =>
  s.undoStack[s.undoStack.length - 1]?.label ?? null;
export const selectPendingCount = (s: ModelState): number => s.pending.length;
/** True when there is work the server has not confirmed — the unload guard. */
export const selectHasUnsavedWork = (s: ModelState): boolean => s.pending.length > 0;
/**
 * The design's version counter: the index of the last op the SERVER has
 * confirmed on this branch.
 *
 * Two consumers depend on the precise meaning, so it is worth stating:
 *
 *  - the autosave badge renders it as "v14";
 *  - **the §9 render stale banner** (Phase 7) watches it. It moves only when
 *    the server accepts an op group, which is the same moment the server marks
 *    existing renders `stale: true` — so "headIdx changed" is the client's
 *    honest cue to re-list the gallery and let the SERVER's `stale` flag drive
 *    the banner. The client never computes staleness itself: an optimistic
 *    local edit that the server later rejects must not gray out a good render.
 *
 * That is why it tracks `headIdx` and not `pending.length`: a keystroke that
 * has not been confirmed has not invalidated anything yet.
 */
export const selectHeadIdx = (s: ModelState): number => s.headIdx;
/** The branch those op indices are counted on. `null` = the project default. */
export const selectVersionBranch = (s: ModelState): string | null => s.versionBranch;
export const selectDiverged = (s: ModelState): boolean => s.divergedAt !== null;

/** Storey lookup for the storey tabs. */
export const selectStoreys = (s: ModelState) => s.doc.house.storeys;

/**
 * The autosave badge (§15). One selector so the top bar, the share dialog and
 * the unload guard all describe the same reality.
 */
export const selectSaveBadge = (s: ModelState): SaveBadge => {
  const version = formatVersionLabel(s.headIdx);
  if (s.status !== 'ready') return { state: 'idle', label: '', detail: null };
  if (s.divergedAt !== null) {
    return { state: 'error', label: 'Out of sync', detail: 'Reload to continue editing.' };
  }
  if (s.saveState === 'offline') {
    return {
      state: 'offline',
      label: `Offline — ${s.pending.length} change${s.pending.length === 1 ? '' : 's'} waiting`,
      detail: 'They will save automatically when you reconnect.',
    };
  }
  if (s.saveState === 'error') {
    return {
      state: 'error',
      label: "Couldn't save",
      detail: s.syncError?.action ?? 'Try again in a moment.',
    };
  }
  if (s.saveState === 'saving' || s.pending.length > 0) {
    return { state: 'saving', label: 'Saving…', detail: null };
  }
  return { state: 'saved', label: `Saved · ${version}`, detail: null };
};
