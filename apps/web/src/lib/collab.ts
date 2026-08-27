/**
 * Live-collaboration stream — the client half of the multiplayer base layer.
 *
 * `GET /projects/:id/collab/events` is an authenticated SSE feed carrying four
 * frame kinds:
 *
 *   `hello`     — sent once on connect: the branch HEAD and who is here now.
 *   `ops`       — someone's op group landed. Carries the new `headIdx` (also
 *                 the frame's `id:`), the actor, and the §4 `source` so the UI
 *                 can distinguish a colleague's hand edit from a solver apply.
 *   `presence`  — the roster changed (someone opened or closed the project).
 *   `cursor`    — one collaborator's live pointer, plot-local integer mm, at
 *                 roughly 10Hz per moving user. Stored nowhere on either side:
 *                 a missed frame is superseded ~100ms later, which is why this
 *                 one is the only frame kind with no `id:` (see below).
 *
 * Everything transport-shaped is inherited from `lib/sse.ts` and for the same
 * reason documented there: `EventSource` cannot set headers, and §13 forbids a
 * bearer token in a query string, so the stream is read with `fetch` plus an
 * `Authorization` header and reconnection is ours — exponential backoff, with
 * `Last-Event-ID` set to the highest `headIdx` seen so the server can resume
 * rather than replay.
 *
 * Frames are zod-parsed with the schemas.ts discipline: strict on what matters,
 * and a frame that does not parse is DROPPED, never thrown — a malformed
 * presence ping from a newer server must not kill the plan you are drawing.
 * What this module never does is fold ops: an `ops` frame is a doorbell, and
 * the model store's `pull()` (the only safe remote-sync primitive) answers it.
 */

import { z } from 'zod';

import { AppError, ERROR_CODES, problemToAppError } from './errors';
import { http } from './http';
import { abortableSleep, parseSseBuffer } from './sse';

// ---------------------------------------------------------------------------
// Frame schemas (the frozen wire contract)
// ---------------------------------------------------------------------------

export const collabUserSchema = z.object({
  userId: z.string().min(1),
  name: z.string().default(''),
});
export type CollabUser = z.infer<typeof collabUserSchema>;

export const collabHelloSchema = z.object({
  headIdx: z.number().int(),
  presence: z.array(collabUserSchema).default([]),
});
export type CollabHello = z.infer<typeof collabHelloSchema>;

/** §4's op provenance vocabulary, mirrored from `AppendOpsInput.source`. */
export const COLLAB_OP_SOURCES = ['manual', 'copilot', 'solver', 'system'] as const;
export type CollabOpSource = (typeof COLLAB_OP_SOURCES)[number];

/**
 * One `ops` frame. `headIdx` strict — a doorbell that cannot say which door is
 * useless — while `source` catches to `'manual'` so a server that grows a new
 * source keeps syncing silently (the quiet default) instead of failing to parse.
 */
export const collabOpsSchema = z.object({
  headIdx: z.number().int(),
  versionBranch: z.string().min(1),
  actorId: z.string().nullable().default(null),
  source: z.enum(COLLAB_OP_SOURCES).catch('manual'),
  groupId: z.string().nullable().default(null),
});
export type CollabOpsFrame = z.infer<typeof collabOpsSchema>;

export const collabPresenceSchema = z.object({
  users: z.array(collabUserSchema).default([]),
});

/**
 * One `cursor` frame: where a collaborator's pointer is, plot-local integer mm.
 *
 * STRICTNESS, FIELD BY FIELD — the same "strict on what matters" rule the ops
 * schema follows, applied to a frame that arrives ten times a second:
 *
 *   `userId`       required. Identity is the map key AND the own-echo filter;
 *                  a cursor with no owner is both unrenderable and unfilterable.
 *   `x` / `y`      required integers. A cursor without a position is nothing.
 *                  Integer because the server's `Mm` type is integer mm and a
 *                  float here would mean the contract moved under us.
 *   `name`         defaults to `''`, exactly like `collabUserSchema` — a
 *                  nameless chip is a cosmetic loss, not a reason to drop.
 *   `storeyIndex`  required, nullable, and deliberately NOT defaulted. The
 *                  publisher always sends the key (an absent one is malformed
 *                  on the server side too), and this is the field that decides
 *                  whether a cursor is drawn on the storey you are looking at.
 *                  Defaulting a missing key to `null` would quietly paint a
 *                  colleague's ground-floor pointer onto your first-floor plan
 *                  — a wrong answer that looks like a working feature, which is
 *                  the failure mode this codebase keeps getting bitten by.
 *                  Dropping the frame instead makes a contract break *visible*
 *                  (no cursors at all) rather than subtly wrong.
 */
export const collabCursorSchema = z.object({
  userId: z.string().min(1),
  name: z.string().default(''),
  x: z.number().int(),
  y: z.number().int(),
  storeyIndex: z.number().int().nullable(),
});
export type CollabCursorFrame = z.infer<typeof collabCursorSchema>;

/** A parsed frame, discriminated for the subscriber. */
export type CollabFrame =
  | { readonly kind: 'hello'; readonly hello: CollabHello }
  | { readonly kind: 'ops'; readonly ops: CollabOpsFrame }
  | { readonly kind: 'presence'; readonly users: readonly CollabUser[] }
  | { readonly kind: 'cursor'; readonly cursor: CollabCursorFrame };

/**
 * True when this cursor frame is our OWN pointer coming back to us.
 *
 * The server fans every cursor out to every subscriber, author included, and
 * says so explicitly (`_cursor_frame_from_message`: "no own-cursor filtering …
 * the client drops frames carrying its own userId"). So this predicate is the
 * whole of that contract on our side, and it is a named exported function
 * rather than an inline `===` for one reason: a filter that silently stops
 * filtering is invisible — you would see a second cursor lagging your own by a
 * network round trip and assume a colleague was mirroring you. Exported, it is
 * negative-testable, and `collab.test.ts` proves that inverting it fails.
 *
 * `selfUserId === null` (signed out, or identity not resolved yet) drops
 * nothing: a frame we cannot attribute is better rendered than discarded.
 */
export function isOwnCursorEcho(
  frame: CollabCursorFrame,
  selfUserId: string | null | undefined,
): boolean {
  return selfUserId !== null && selfUserId !== undefined && frame.userId === selfUserId;
}

/**
 * Parse one SSE frame into the collab vocabulary.
 *
 * Returns `null` for anything unusable: malformed JSON, a shape that fails its
 * schema, or an event name this client does not know (the server's future, not
 * our error). Never throws — the drop-don't-crash rule of the §11 boundary.
 */
export function parseCollabFrame(event: string, data: string): CollabFrame | null {
  let json: unknown;
  try {
    json = JSON.parse(data);
  } catch {
    return null;
  }

  if (event === 'hello') {
    const parsed = collabHelloSchema.safeParse(json);
    return parsed.success ? { kind: 'hello', hello: parsed.data } : null;
  }
  if (event === 'ops') {
    const parsed = collabOpsSchema.safeParse(json);
    return parsed.success ? { kind: 'ops', ops: parsed.data } : null;
  }
  if (event === 'presence') {
    const parsed = collabPresenceSchema.safeParse(json);
    return parsed.success ? { kind: 'presence', users: parsed.data.users } : null;
  }
  if (event === 'cursor') {
    const parsed = collabCursorSchema.safeParse(json);
    return parsed.success ? { kind: 'cursor', cursor: parsed.data } : null;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Subscription
// ---------------------------------------------------------------------------

export interface CollabSubscribeOptions {
  readonly projectId: string;
  /** An op group landed somewhere. The consumer decides whether to pull. */
  readonly onOps: (frame: CollabOpsFrame) => void;
  /** The current roster, replacing any previous one. Includes yourself. */
  readonly onPresence: (users: readonly CollabUser[]) => void;
  /**
   * One collaborator's pointer moved. Never fired for your own echo — see
   * {@link isOwnCursorEcho}. Optional, so a surface with no canvas (the brief
   * tab, the dashboard) simply never asks for the traffic.
   */
  readonly onCursor?: ((frame: CollabCursorFrame) => void) | undefined;
  /**
   * Who "you" are, for the own-echo filter. A GETTER, not a value: the stream
   * is opened from the project shell's mount effect and the session store may
   * still be rehydrating an identity at that instant. Reading it per frame
   * costs one property access and removes a race whose only symptom would be a
   * ghost cursor shadowing your own until the next reload.
   */
  readonly selfUserId?: (() => string | null) | undefined;
  /** The opening frame — carries the HEAD to catch up to after a reconnect. */
  readonly onHello?: (hello: CollabHello) => void;
  /** Stream up (true after each successful connect) / down (each drop). */
  readonly onConnected?: (connected: boolean) => void;
  /**
   * Transport or protocol failure. The stream keeps retrying after this unless
   * the error is fatal (auth, 404) — `error.retryable` says which.
   */
  readonly onError?: (error: AppError) => void;
  /** Overall cap on reconnect attempts before giving up. */
  readonly maxRetries?: number;
}

// Same backoff policy as the job streams (`lib/sse.ts`), and deliberately no
// read timeout for the same reason: a quiet project is legitimately silent
// between keep-alive pings.
const FIRST_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;

/**
 * Subscribe to a project's collaboration stream.
 *
 * @returns an unsubscribe function — idempotent, safe as a React effect
 * cleanup; it aborts the in-flight request rather than leaving a socket open
 * behind a closed project.
 */
export function subscribeProjectCollab(options: CollabSubscribeOptions): () => void {
  const controller = new AbortController();
  let closed = false;
  let attempt = 0;
  /** Highest `headIdx` seen — the `ops` frames' own `id:`, offered back on reconnect. */
  let lastHeadIdx = -1;
  const maxRetries = options.maxRetries ?? Number.POSITIVE_INFINITY;
  const path = `/projects/${encodeURIComponent(options.projectId)}/collab/events`;

  const close = (): void => {
    if (closed) return;
    closed = true;
    controller.abort();
    options.onConnected?.(false);
  };

  const run = async (): Promise<void> => {
    while (!closed) {
      let sawFrame = false;
      try {
        const token = await http.authorization();
        const headers = new Headers({ Accept: 'text/event-stream' });
        if (token) headers.set('Authorization', `Bearer ${token}`);
        if (lastHeadIdx >= 0) headers.set('Last-Event-ID', String(lastHeadIdx));

        const response = await fetch(http.url(path), {
          method: 'GET',
          headers,
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal,
        });

        if (!response.ok) {
          const body: unknown = await response.json().catch(() => null);
          const error = problemToAppError(response.status, body, {
            endpoint: `GET ${path}`,
            requestId: response.headers.get('x-request-id'),
          });
          // Auth failures and a missing endpoint will not get better by
          // retrying — and the latter is expected while the API half of this
          // feature is still rolling out, so the caller decides how loud to be.
          if (!error.retryable) {
            options.onError?.(error);
            close();
            return;
          }
          throw error;
        }

        if (!response.body) {
          throw new AppError({
            code: ERROR_CODES.malformedResponse,
            message: 'This browser could not open the live collaboration stream.',
            action: 'Edits still save; reload to see what teammates change.',
            endpoint: path,
          });
        }

        attempt = 0; // a successful connect resets the backoff
        options.onConnected?.(true);

        // Manual TextDecoder for the same two reasons as lib/sse.ts: transform
        // streams are missing in browsers we still support, and a multi-byte
        // character split across chunks must not be mangled.
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseSseBuffer(buffer);
          buffer = rest;

          for (const frame of frames) {
            const parsed = parseCollabFrame(frame.event, frame.data);
            if (parsed === null) continue; // malformed or unknown: dropped, never thrown
            sawFrame = true;
            if (parsed.kind === 'hello') {
              lastHeadIdx = Math.max(lastHeadIdx, parsed.hello.headIdx);
              options.onHello?.(parsed.hello);
              options.onPresence(parsed.hello.presence);
            } else if (parsed.kind === 'ops') {
              lastHeadIdx = Math.max(lastHeadIdx, parsed.ops.headIdx);
              options.onOps(parsed.ops);
            } else if (parsed.kind === 'cursor') {
              // NOTE the absent `lastHeadIdx` update, and see the module header:
              // cursor frames deliberately carry no `id:`, because this client
              // offers the highest id it has seen back as `Last-Event-ID` and
              // the server reads that header as an ops head. Treating a cursor
              // as a head would corrupt reconnect catch-up every time anyone
              // twitched a mouse.
              if (!isOwnCursorEcho(parsed.cursor, options.selfUserId?.() ?? null)) {
                options.onCursor?.(parsed.cursor);
              }
            } else {
              options.onPresence(parsed.users);
            }
          }
        }
      } catch (err) {
        if (closed) return;
        const error = AppError.from(err);
        if (error.isAborted) return;
        options.onError?.(error);
      }

      if (closed) return;
      // The stream dropped: say so before backing off, so presence chips do
      // not claim company the socket can no longer vouch for.
      options.onConnected?.(false);
      attempt += 1;
      if (attempt > maxRetries) {
        close();
        return;
      }
      const base = sawFrame ? FIRST_BACKOFF_MS : FIRST_BACKOFF_MS * 2;
      const delay = Math.min(base * 2 ** (attempt - 1), MAX_BACKOFF_MS);
      await abortableSleep(delay, controller.signal);
    }
  };

  void run();
  return close;
}
