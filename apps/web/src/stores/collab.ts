/**
 * `collab` — who else has this project open, where their pointers are, and the
 * quiet-sync loop.
 *
 * Three halves (the third arrived with live cursors), deliberately separable:
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
 *  3. Remote cursors: a `userId → position` map fed by the stream's `cursor`
 *     frames and EXPIRED on a timer. Expiry is the whole design problem here.
 *     Presence is authoritative (the server re-reads the roster and replaces
 *     the list wholesale), but cursors are not: nothing is stored server-side,
 *     so a user who closes the tab, sleeps the laptop or loses the network
 *     sends no goodbye — their last position would sit on the plan forever,
 *     a ghost pointing at a wall nobody is looking at. So a cursor is a fact
 *     with a shelf life: {@link CURSOR_TTL_MS} of silence and it is gone.
 *
 * The cursor half is written as pure reducers over a plain `Map`
 * ({@link upsertCursor}, {@link pruneCursors}) with the store as a thin shell,
 * for the same reason the ops scheduler is a factory: expiry is the part that
 * can be wrong, and it is worth being able to test it with a clock argument
 * instead of a real ten-second wait.
 *
 * The scheduler is a factory with injected deps rather than a store method so
 * a unit test can prove the two behaviours that matter — "pull only when the
 * frame is ahead" and "a burst is one pull" — without a network or a model.
 */

import { create } from 'zustand';

import {
  subscribeProjectCollab,
  type CollabCursorFrame,
  type CollabOpSource,
  type CollabUser,
} from '../lib/collab';
import { useModelStore } from './model';
import { useSessionStore } from './session';
import { useUiStore } from './ui';

// ---------------------------------------------------------------------------
// Presence store
// ---------------------------------------------------------------------------

/** One collaborator's pointer, as the canvas renders it. */
export interface RemoteCursor {
  readonly userId: string;
  readonly name: string;
  /** Plot-local integer millimetres, exactly as the model uses them. */
  readonly x: number;
  readonly y: number;
  /** Which storey they are on, or null for "not storey-bound". */
  readonly storeyIndex: number | null;
  /** `Date.now()` of the frame that produced this entry — the expiry clock. */
  readonly at: number;
}

/**
 * How long a cursor survives without a new frame.
 *
 * The publisher runs at ~10Hz while moving and sends NOTHING while still, so
 * this is not "10s late" — it is "10s after the last movement". Long enough
 * that a colleague reading a drawing keeps their pointer on screen, short
 * enough that a closed tab stops haunting the plan within one glance.
 */
export const CURSOR_TTL_MS = 10_000;

/** How often the idle sweeper runs. Coarse: expiry is a housekeeping deadline,
 *  not an animation, and a timer that fires 60×/minute for nothing is waste. */
export const CURSOR_SWEEP_MS = 2_000;

export interface CollabState {
  /** Everyone the server says is here — including yourself; filter at render. */
  users: CollabUser[];
  /** True only while the SSE stream is actually up. */
  connected: boolean;
  /**
   * Remote pointers by `userId`. NEVER contains your own — the stream layer
   * drops the echo before it reaches here ({@link isOwnCursorEcho}).
   *
   * A `Map` rather than an array because every write is a keyed upsert at
   * pointer rate, and a replaced-by-identity `Map` is what lets Zustand's
   * default `Object.is` comparison do the right thing without a custom
   * equality function on the selector.
   */
  cursors: ReadonlyMap<string, RemoteCursor>;

  setPresence: (users: readonly CollabUser[]) => void;
  setConnected: (connected: boolean) => void;
  /** One `cursor` frame landed. `at` is injectable so tests own the clock. */
  noteCursor: (frame: CollabCursorFrame, at?: number) => void;
  /** Drop everything past its TTL. Cheap and idempotent; safe on a timer. */
  sweepCursors: (now?: number) => void;
  reset: () => void;
}

// ---------------------------------------------------------------------------
// Cursor reducers — pure, so the expiry rule is testable without waiting 10s
// ---------------------------------------------------------------------------

/** Upsert one frame. Always a NEW map: the position changed, so React must see it. */
export function upsertCursor(
  cursors: ReadonlyMap<string, RemoteCursor>,
  frame: CollabCursorFrame,
  at: number,
): ReadonlyMap<string, RemoteCursor> {
  const next = new Map(cursors);
  next.set(frame.userId, {
    userId: frame.userId,
    name: frame.name,
    x: frame.x,
    y: frame.y,
    storeyIndex: frame.storeyIndex,
    at,
  });
  return next;
}

/**
 * Drop every cursor whose last frame is older than `ttlMs`.
 *
 * Returns the SAME map instance when nothing expired. That identity is
 * load-bearing, not a micro-optimisation: this runs on a 2s interval for the
 * whole life of an open project, and returning a fresh map every time would
 * re-render the cursor layer forever on a project where nobody is moving.
 */
export function pruneCursors(
  cursors: ReadonlyMap<string, RemoteCursor>,
  now: number,
  ttlMs: number = CURSOR_TTL_MS,
): ReadonlyMap<string, RemoteCursor> {
  let stale: string[] | null = null;
  for (const [userId, cursor] of cursors) {
    if (now - cursor.at < ttlMs) continue;
    (stale ??= []).push(userId);
  }
  if (stale === null) return cursors;
  const next = new Map(cursors);
  for (const userId of stale) next.delete(userId);
  return next;
}

/**
 * The cursors to draw on one storey, newest-first-insensitive.
 *
 * `storeyIndex === null` means "not storey-bound" and shows everywhere — the
 * honest rendering of a cursor whose owner is not on a storey at all (plot
 * editing, or a client on a view with no storey concept). A cursor bound to a
 * DIFFERENT storey is hidden rather than dimmed: it is pointing at geometry you
 * cannot see, so drawing it would be pointing at nothing.
 *
 * The `now`/`ttlMs` filter repeats what the sweeper does, deliberately. Browsers
 * throttle timers in a backgrounded tab to once a minute or stop them entirely,
 * so the sweeper alone would let a ghost survive a tab switch; filtering at read
 * time makes the TTL true at the moment of drawing rather than at the moment of
 * the last timer tick.
 */
export function visibleCursors(
  cursors: ReadonlyMap<string, RemoteCursor>,
  storeyIndex: number | null,
  now: number,
  ttlMs: number = CURSOR_TTL_MS,
): RemoteCursor[] {
  const out: RemoteCursor[] = [];
  for (const cursor of cursors.values()) {
    if (now - cursor.at >= ttlMs) continue;
    if (cursor.storeyIndex !== null && storeyIndex !== null && cursor.storeyIndex !== storeyIndex) {
      continue;
    }
    out.push(cursor);
  }
  // Stable order so the DOM nodes do not shuffle between frames.
  out.sort((a, b) => (a.userId < b.userId ? -1 : a.userId > b.userId ? 1 : 0));
  return out;
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

const EMPTY_CURSORS: ReadonlyMap<string, RemoteCursor> = new Map();

export const useCollabStore = create<CollabState>()((set) => ({
  users: [],
  connected: false,
  cursors: EMPTY_CURSORS,

  setPresence: (users) => set({ users: dedupeUsers(users) }),
  setConnected: (connected) => set({ connected }),
  // Prune on write as well as on the timer: a frame arriving is the cheapest
  // moment to notice that somebody else went quiet, and it keeps the map small
  // without depending on the sweeper having fired recently.
  noteCursor: (frame, at = Date.now()) =>
    set((s) => ({ cursors: pruneCursors(upsertCursor(s.cursors, frame, at), at) })),
  sweepCursors: (now = Date.now()) => set((s) => ({ cursors: pruneCursors(s.cursors, now) })),
  reset: () => set({ users: [], connected: false, cursors: EMPTY_CURSORS }),
}));

export const selectCollabUsers = (s: CollabState): CollabUser[] => s.users;
export const selectCollabConnected = (s: CollabState): boolean => s.connected;
export const selectCollabCursors = (s: CollabState): ReadonlyMap<string, RemoteCursor> => s.cursors;

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
    onCursor: (frame) => useCollabStore.getState().noteCursor(frame),
    selfUserId: () => useSessionStore.getState().user?.id ?? null,
    onConnected: (connected) => {
      useCollabStore.getState().setConnected(connected);
      // A dropped stream means no more cursor frames, so every entry in the map
      // is now a position nobody is refreshing. Let the TTL retire them rather
      // than clearing instantly: a reconnect usually lands inside the window,
      // and cursors that blink out and back on every hiccup read as breakage.
      if (!connected) useCollabStore.getState().sweepCursors();
    },
  });

  // The idle sweeper. Without it a cursor only expires when ANOTHER cursor
  // frame arrives — so the last person to leave a two-person session would
  // leave their pointer on the plan until the tab was reloaded, which is
  // precisely the ghost this timer exists to bury.
  const sweeper = setInterval(() => {
    useCollabStore.getState().sweepCursors();
  }, CURSOR_SWEEP_MS);

  return () => {
    scheduler.cancel();
    clearInterval(sweeper);
    stop();
    useCollabStore.getState().reset();
  };
}
