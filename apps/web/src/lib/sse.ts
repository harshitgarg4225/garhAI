/**
 * Server-sent events for job progress (§9 renders, §11 SSE endpoints,
 * §15 "generation theater… never a fake bar").
 *
 * ## Why not `EventSource`
 *
 * `EventSource` cannot set request headers, so an authenticated stream would
 * have to carry the access token in the query string. That puts a bearer
 * credential into browser history, referrer headers, proxy logs and every
 * server access log on the path — for a token that grants full firm access.
 * §13 rules it out, so this module reads the stream with `fetch` instead and
 * sends a normal `Authorization` header.
 *
 * The cost is that reconnection is ours to implement. That is the loop below:
 * exponential backoff, and `Last-Event-ID` set to the highest `seq` we have
 * seen so the server can replay the events we missed
 * (`garh_api.queue.replay_progress_events`).
 *
 * ## What a consumer gets
 *
 * Events exactly as the worker emitted them, in order, with duplicates dropped
 * by `seq`. No synthesised progress: if a worker goes quiet, the stream goes
 * quiet, because §15 says the progress bar tells the truth or says nothing.
 */

import { AppError, ERROR_CODES, problemToAppError } from './errors';
import { http } from './http';
import {
  progressEventFromState,
  progressEventSchema,
  type JobKind,
  type ProgressEvent,
} from './schemas';

/** Path of the SSE endpoint for each job kind (§11). */
const EVENT_PATHS: Readonly<Record<JobKind, (jobId: string) => string>> = {
  solver: (jobId) => `/solver-jobs/${encodeURIComponent(jobId)}/events`,
  render: (jobId) => `/render-jobs/${encodeURIComponent(jobId)}/events`,
  // §11 lists SSE for solver and render explicitly. Sheet/export generation is
  // the same worker machinery (queue.py: JOB_KIND_DRAWINGS), so it gets the
  // matching path; confirm when the drawings router lands.
  drawings: (jobId) => `/drawings-jobs/${encodeURIComponent(jobId)}/events`,
};

export interface JobEventHandlers {
  /** Every event, in `seq` order, duplicates already removed. */
  readonly onEvent: (event: ProgressEvent) => void;
  /**
   * Transport or protocol failure. The stream keeps retrying after this unless
   * the error is fatal (auth, 404) — `error.retryable` says which.
   */
  readonly onError?: (error: AppError) => void;
  /** Fired once, when the stream ends for good (terminal event or unsubscribe). */
  readonly onClose?: () => void;
}

export interface JobEventOptions extends JobEventHandlers {
  readonly jobId: string;
  readonly kind: JobKind;
  /** Resume point. Defaults to 0 = "replay everything you still have". */
  readonly sinceSeq?: number;
  /** Overall cap on reconnect attempts before giving up. */
  readonly maxRetries?: number;
}

const FIRST_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;
// Deliberately no read timeout: a queued solver job can legitimately be silent
// for a minute, and killing the stream for that would be a bug, not a safeguard.

interface SseFrame {
  event: string;
  data: string;
  id: string | null;
}

/**
 * Split an SSE buffer into complete frames, returning the leftover partial.
 * Handles `\n\n`, `\r\n\r\n` and `\r\r` separators per the WHATWG parse rules.
 */
export function parseSseBuffer(buffer: string): { frames: SseFrame[]; rest: string } {
  const normalised = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const chunks = normalised.split('\n\n');
  const rest = chunks.pop() ?? '';
  const frames: SseFrame[] = [];

  for (const chunk of chunks) {
    let event = 'message';
    let id: string | null = null;
    const dataLines: string[] = [];

    for (const line of chunk.split('\n')) {
      if (line.length === 0 || line.startsWith(':')) continue; // comment / keep-alive
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      // "If value starts with a single U+0020 SPACE, remove it."
      let value = colon === -1 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);

      if (field === 'event') event = value;
      else if (field === 'data') dataLines.push(value);
      else if (field === 'id') id = value;
      // `retry:` is ignored — our backoff policy is not the server's to set.
    }

    if (dataLines.length > 0) frames.push({ event, data: dataLines.join('\n'), id });
  }

  return { frames, rest };
}

/**
 * Subscribe to a job's progress stream.
 *
 * @returns an unsubscribe function. Idempotent, and safe to call from a React
 * effect cleanup — it aborts the in-flight request rather than leaving a
 * socket open behind a closed panel.
 */
export function subscribeJobEvents(options: JobEventOptions): () => void {
  const controller = new AbortController();
  let closed = false;
  let lastSeq = options.sinceSeq ?? 0;
  let attempt = 0;
  const maxRetries = options.maxRetries ?? Number.POSITIVE_INFINITY;

  const close = (): void => {
    if (closed) return;
    closed = true;
    controller.abort();
    options.onClose?.();
  };

  const pathFor = EVENT_PATHS[options.kind];

  const run = async (): Promise<void> => {
    while (!closed) {
      let sawEvent = false;
      try {
        const token = await http.authorization();
        const headers = new Headers({ Accept: 'text/event-stream' });
        if (token) headers.set('Authorization', `Bearer ${token}`);
        if (lastSeq > 0) headers.set('Last-Event-ID', String(lastSeq));

        const response = await fetch(http.url(pathFor(options.jobId)), {
          method: 'GET',
          headers,
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal,
        });

        if (!response.ok) {
          const body: unknown = await response.json().catch(() => null);
          const error = problemToAppError(response.status, body, {
            endpoint: `GET ${pathFor(options.jobId)}`,
            requestId: response.headers.get('x-request-id'),
          });
          // A 404 or an auth failure will not get better by trying again.
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
            message: 'This browser could not open the live progress stream.',
            action: 'Progress will still update when the job finishes — reload to see it.',
            endpoint: pathFor(options.jobId),
          });
        }

        attempt = 0; // a successful connect resets the backoff
        // Manual TextDecoder rather than `pipeThrough(new TextDecoderStream())`:
        // the transform stream is missing in a couple of the browsers we still
        // support, and a multi-byte character split across two chunks must not
        // be mangled — hence `{ stream: true }`.
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
            // The opening `state` frame is the JOB ROW (how a late connector
            // learns a finished job's outcome); everything else is a worker
            // progress event. They are different wire shapes — parsing the row
            // as a progress event used to fail silently and drop the outcome.
            const parsed =
              frame.event === 'state' ? safeParseState(frame.data) : safeParseEvent(frame.data);
            if (!parsed) continue;
            // Dedupe across a reconnect replay.
            if (parsed.seq > 0 && parsed.seq <= lastSeq) continue;
            if (parsed.seq > lastSeq) lastSeq = parsed.seq;
            sawEvent = true;
            options.onEvent(parsed);
            if (parsed.terminal || frame.event === 'done' || frame.event === 'error') {
              close();
              return;
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
      attempt += 1;
      if (attempt > maxRetries) {
        close();
        return;
      }
      // A stream that produced events before dropping is probably healthy and
      // merely got cut; reconnect fast. One that produced nothing may be a
      // server that cannot serve it, so back off harder.
      const base = sawEvent ? FIRST_BACKOFF_MS : FIRST_BACKOFF_MS * 2;
      const delay = Math.min(base * 2 ** (attempt - 1), MAX_BACKOFF_MS);
      await sleep(delay, controller.signal);
    }
  };

  void run();
  return close;
}

function safeParseEvent(data: string): ProgressEvent | null {
  try {
    const parsed = progressEventSchema.safeParse(JSON.parse(data));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

function safeParseState(data: string): ProgressEvent | null {
  try {
    return progressEventFromState(JSON.parse(data));
  } catch {
    return null;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      resolve();
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}
