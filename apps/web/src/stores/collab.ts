/**
 * `collab` — who else has this project open, and the quiet-sync loop.
 *
 * Two halves, deliberately separable:
 *
 *  1. A tiny presence store the top bar's avatar chips read. The stream's
 *     roster REPLACES the list wholesale (the server owns membership); the
 *     store only dedupes and answers "is the stream even up?" so the UI never
 *     shows company the socket cannot vouch for.
 *  2. The remote-ops scheduler: an `ops` frame is a doorbell, and the ONLY
 *     legal response is `useModelStore.pull()` — the primitive that drains the
 *     pending queue before rebasing, which is what makes it safe to call while
 *     the user is mid-edit. This module never folds ops itself. Frames arrive
 *     in bursts (a solver apply is dozens of groups), so the pull is debounced
 *     trailing-edge; and per the Rayon rule, sync is silent for a colleague's
 *     hand edits — only a `solver`/`copilot` change earns a toast, because
 *     those can rearrange a plan while you are looking at it.
 *
 * The scheduler is a factory with injected deps rather than a store method so
 * a unit test can prove the two behaviours that matter — "pull only when the
 * frame is ahead" and "a burst is one pull" — without a network or a model.
 */

import { create } from 'zustand';

import { subscribeProjectCollab, type CollabOpSource, type CollabUser } from '../lib/collab';
import { useModelStore } from './model';
import { useUiStore } from './ui';

// ---------------------------------------------------------------------------
// Presence store
// ---------------------------------------------------------------------------

export interface CollabState {
  /** Everyone the server says is here — including yourself; filter at render. */
  users: CollabUser[];
  /** True only while the SSE stream is actually up. */
  connected: boolean;

  setPresence: (users: readonly CollabUser[]) => void;
  setConnected: (connected: boolean) => void;
  reset: () => void;
}

/** Keep the first occurrence per `userId` — a duplicate row is a server hiccup. */
function dedupeUsers(users: readonly CollabUser[]): CollabUser[] {
  const seen = new Set<string>();
  const out: CollabUser[] = [];
  for (const user of users) {
    if (seen.has(user.userId)) continue;
    seen.add(user.userId);
    out.push(user);
  }
  return out;
}

export const useCollabStore = create<CollabState>()((set) => ({
  users: [],
  connected: false,

  setPresence: (users) => set({ users: dedupeUsers(users) }),
  setConnected: (connected) => set({ connected }),
  reset: () => set({ users: [], connected: false }),
}));

export const selectCollabUsers = (s: CollabState): CollabUser[] => s.users;
export const selectCollabConnected = (s: CollabState): boolean => s.connected;

// ---------------------------------------------------------------------------
// Remote-ops scheduler
// ---------------------------------------------------------------------------

/** Sources whose remote changes are announced; a colleague's edit stays silent. */
export type AnnounceSource = Extract<CollabOpSource, 'solver' | 'copilot'>;

export interface RemoteOpsSchedulerDeps {
  /** The client's current confirmed HEAD (`selectHeadIdx`). */
  readonly getHeadIdx: () => number;
  /** The model store's safe remote-sync primitive. */
  readonly pull: () => Promise<void>;
  /** Called at most once per pulled burst that contained a solver/copilot op. */
  readonly announce: (source: AnnounceSource) => void;
  /** Trailing debounce for op bursts. */
  readonly debounceMs?: number;
}

export interface RemoteOpsScheduler {
  /** Feed one head signal (`ops` frame, or `hello` after a reconnect). */
  readonly notice: (headIdx: number, source: CollabOpSource) => void;
  /** Drop the pending debounce — the unmount path. */
  readonly cancel: () => void;
}

const DEFAULT_DEBOUNCE_MS = 250;

export function createRemoteOpsScheduler(deps: RemoteOpsSchedulerDeps): RemoteOpsScheduler {
  const debounceMs = deps.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  let timer: ReturnType<typeof setTimeout> | null = null;
  /** The loudest source seen this burst; solver/copilot outrank silence. */
  let pendingAnnounce: AnnounceSource | null = null;

  const fire = (): void => {
    timer = null;
    const source = pendingAnnounce;
    pendingAnnounce = null;
    deps
      .pull()
      .then(() => {
        // Announce only after the pull, so the toast describes a change that is
        // actually on screen rather than one still in flight.
        if (source !== null) deps.announce(source);
      })
      .catch(() => {
        // `pull()` reports its own failures through the model store's
        // `syncError`; a rejected promise here must not take the tab down.
      });
  };

  return {
    notice: (headIdx, source) => {
      // Our own appends advance `headIdx` through the POST /ops response, so
      // their echo frames (and any replay after a reconnect) land here as
      // "nothing new" and cost nothing.
      if (headIdx <= deps.getHeadIdx()) return;
      if (source === 'solver' || source === 'copilot') pendingAnnounce = source;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(fire, debounceMs);
    },
    cancel: () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      pendingAnnounce = null;
    },
  };
}

// ---------------------------------------------------------------------------
// Lifecycle wiring
// ---------------------------------------------------------------------------

/**
 * Open the collaboration stream for one project and wire it into the stores.
 * Returns the teardown; `pages/useProjectCollab.ts` ties it to the shell's
 * mount/unmount.
 *
 * A fatal stream error (the endpoint missing, an auth refusal) is deliberately
 * quiet here: collaboration is an ambient layer, the model store still syncs
 * on flush/rebase, and a toast on every project open while the API half rolls
 * out would train users to ignore toasts. `connected: false` is the honest
 * signal, and the chips simply are not there.
 */
export function startProjectCollab(projectId: string): () => void {
  const scheduler = createRemoteOpsScheduler({
    getHeadIdx: () => useModelStore.getState().headIdx,
    pull: () => useModelStore.getState().pull(),
    announce: (source) => {
      useUiStore.getState().pushToast({
        tone: 'info',
        title: source === 'solver' ? 'Plan updated by the solver' : 'Plan updated by the copilot',
        durationMs: 4_000,
        dedupeKey: 'collab-remote-update',
      });
    },
  });

  const stop = subscribeProjectCollab({
    projectId,
    // `hello` carries the branch HEAD: after a reconnect it is the catch-up
    // signal for everything missed while offline. `system` keeps it silent.
    onHello: (hello) => scheduler.notice(hello.headIdx, 'system'),
    onOps: (frame) => scheduler.notice(frame.headIdx, frame.source),
    onPresence: (users) => useCollabStore.getState().setPresence(users),
    onConnected: (connected) => useCollabStore.getState().setConnected(connected),
  });

  return () => {
    scheduler.cancel();
    stop();
    useCollabStore.getState().reset();
  };
}
