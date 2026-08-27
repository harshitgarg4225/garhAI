/**
 * CommentsPanel — the project's comment thread, docked right like the copilot
 * rail (§12) and sharing its visual grammar: soft-circle icon header, divided
 * list, composer along the bottom edge.
 *
 * The comments themselves come from two doors — the team (this panel) and
 * clients on a comment-enabled share link (§13) — and the server keeps only
 * the open ones in the list. A row resolved from here stays visible with a
 * "Resolved" badge until the next refresh, so the action reads as done rather
 * than as the row disappearing.
 *
 * Props-in, JSX-out: the state lives in `useComments`, owned by the shell so
 * the top-bar badge and this list can never disagree.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CANVAS HALF (pinned comments)
 * ════════════════════════════════════════════════════════════════════════════
 * A comment can be pinned to a point on the plan. The pin itself is drawn by
 * `features/canvas/copresence`, on the far side of the `<Canvas>` boundary; the
 * two halves meet in `pinStore`, and this panel is the half a person drives:
 *
 *   · "Pin on the plan" ARMS placement. The canvas puts up a hint banner and
 *     the next click there captures a point; the composer then posts the
 *     comment with that anchor. Escape cancels from either side.
 *   · Clicking a pin on the canvas FOCUSES its thread here — the row is
 *     scrolled to and highlighted for a beat.
 *   · Because the shell owns `open` and this feature may not reach into it, a
 *     pin click that needs the panel sets `panelForcedOpen` in the store and
 *     this component treats `open || forced` as its real visibility. `onClose`
 *     clears both, so the close button still closes.
 */

import { useEffect, useRef, useState } from 'react';

import { Badge, Button, Icon, IconButton, Skeleton, cn } from '@garh/ui';

import type { AppError } from '../../lib/errors';
import type { Comment } from '../../lib/schemas';
import { formatRelative } from '../../lib/units';
import { numberPlanPins } from './anchor';
import { useCommentPinStore } from './pinStore';

/** How long a canvas-focused row stays washed before the highlight clears. */
const FOCUS_HIGHLIGHT_MS = 2_000;

/**
 * Minimal CSS.escape, for the attribute selector that finds a focused row.
 *
 * Comment ids are server UUIDs, so in practice nothing needs escaping — but a
 * selector built by string concatenation from data is a selector-injection bug
 * waiting for the day an id format changes, and `CSS.escape` is missing in
 * jsdom, so a plain call would pass in the browser and throw in the tests.
 */
function cssEscape(value: string): string {
  return value.replace(/["\\]/g, '\\$&');
}

export interface CommentsPanelProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** Newest first, from `useComments`. */
  readonly comments: readonly Comment[];
  readonly loading: boolean;
  readonly error: AppError | null;
  readonly unresolvedCount: number;
  readonly busy: boolean;
  readonly resolvingId: string | null;
  readonly onRefresh: () => void;
  readonly onAdd: (body: string) => Promise<boolean>;
  readonly onResolve: (commentId: string) => void;
  readonly className?: string | undefined;
}

export function CommentsPanel({
  open,
  onClose,
  comments,
  loading,
  error,
  unresolvedCount,
  busy,
  resolvingId,
  onRefresh,
  onAdd,
  onResolve,
  className,
}: CommentsPanelProps): JSX.Element | null {
  const forcedOpen = useCommentPinStore((s) => s.panelForcedOpen);
  const focusedCommentId = useCommentPinStore((s) => s.focusedCommentId);
  const placementPhase = useCommentPinStore((s) => s.placement.phase);
  const showResolvedPins = useCommentPinStore((s) => s.showResolvedPins);
  const dispatchPlacement = useCommentPinStore((s) => s.dispatchPlacement);
  const closePinPanel = useCommentPinStore((s) => s.closePanel);
  const setShowResolvedPins = useCommentPinStore((s) => s.setShowResolvedPins);

  const visible = open || forcedOpen;

  // Refresh on every open, not merely mount: the badge count and any client
  // comments left since the last look are stale by exactly one refetch.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (visible && !wasOpen.current) onRefresh();
    wasOpen.current = visible;
  }, [visible, onRefresh]);

  // Closing the panel ends placement. Leaving it armed would keep the canvas in
  // a mode whose only exit affordance had just been dismissed — the trap every
  // modal cursor state has to be checked for.
  useEffect(() => {
    if (!visible && placementPhase !== 'idle') dispatchPlacement({ type: 'cancel' });
  }, [visible, placementPhase, dispatchPlacement]);

  const close = (): void => {
    closePinPanel();
    dispatchPlacement({ type: 'cancel' });
    onClose();
  };

  if (!visible) return null;

  return (
    <aside
      aria-label="Comments"
      className={cn(
        'flex h-full w-80 shrink-0 flex-col border-l border-line bg-surface xl:w-96',
        className,
      )}
    >
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-ink">
          <Icon name="message" size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">Comments</h2>
          <p className="truncate text-2xs text-ink-subtle">
            {unresolvedCount === 0
              ? 'Nothing open'
              : `${unresolvedCount} open comment${unresolvedCount === 1 ? '' : 's'}`}
          </p>
        </div>
        <IconButton label="Close comments" icon="x" size="sm" variant="ghost" onClick={close} />
      </header>

      <CommentList
        comments={comments}
        loading={loading}
        error={error}
        resolvingId={resolvingId}
        focusedCommentId={focusedCommentId}
        onRefresh={onRefresh}
        onResolve={onResolve}
      />

      <PinControls
        comments={comments}
        showResolvedPins={showResolvedPins}
        onToggleResolvedPins={setShowResolvedPins}
      />

      <Composer busy={busy} onAdd={onAdd} />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Pin controls — arm placement, and decide whether resolved pins are drawn
// ---------------------------------------------------------------------------

/**
 * The strip between the thread and the composer.
 *
 * It reads the placement machine directly rather than taking props, because the
 * canvas can change that state (a click captures a point, Escape cancels) and
 * this strip has to follow — props from the shell could not carry a change that
 * originates on the other side of the `<Canvas>` boundary.
 *
 * The Escape handler lives here as well as in the canvas layer, on purpose:
 * placement is armed from this panel and the panel can be open on a tab with no
 * plan canvas mounted at all, where the layer's handler does not exist. Both
 * dispatch the same idempotent `cancel`, so the duplicate costs nothing and the
 * mode can never be left with no way out.
 */
function PinControls({
  comments,
  showResolvedPins,
  onToggleResolvedPins,
}: {
  readonly comments: readonly Comment[];
  readonly showResolvedPins: boolean;
  readonly onToggleResolvedPins: (show: boolean) => void;
}): JSX.Element {
  const placement = useCommentPinStore((s) => s.placement);
  const dispatchPlacement = useCommentPinStore((s) => s.dispatchPlacement);

  useEffect(() => {
    if (placement.phase === 'idle') return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      dispatchPlacement({ type: 'cancel' });
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [placement.phase, dispatchPlacement]);

  const pins = numberPlanPins(comments);
  const resolvedPinCount = pins.filter((pin) => pin.comment.resolved).length;

  return (
    <div className="border-t border-line px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        {placement.phase === 'armed' ? (
          <>
            <Badge tone="brand" icon="pin">
              Click the plan
            </Badge>
            <Button variant="ghost" size="sm" onClick={() => dispatchPlacement({ type: 'cancel' })}>
              Cancel
            </Button>
          </>
        ) : placement.phase === 'placed' ? (
          <>
            <Badge tone="pass" icon="check">
              Point set
            </Badge>
            <span className="text-2xs text-ink-subtle">Write the comment below.</span>
            <Button variant="ghost" size="sm" onClick={() => dispatchPlacement({ type: 'arm' })}>
              Move
            </Button>
          </>
        ) : (
          <Button
            variant="secondary"
            size="sm"
            iconLeft="pin"
            onClick={() => dispatchPlacement({ type: 'arm' })}
          >
            Pin on the plan
          </Button>
        )}

        {resolvedPinCount > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            aria-pressed={showResolvedPins}
            onClick={() => onToggleResolvedPins(!showResolvedPins)}
          >
            {showResolvedPins ? 'Hide resolved pins' : `Show resolved pins (${resolvedPinCount})`}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The thread
// ---------------------------------------------------------------------------

function CommentList({
  comments,
  loading,
  error,
  resolvingId,
  focusedCommentId,
  onRefresh,
  onResolve,
}: {
  readonly comments: readonly Comment[];
  readonly loading: boolean;
  readonly error: AppError | null;
  readonly resolvingId: string | null;
  readonly focusedCommentId: string | null;
  readonly onRefresh: () => void;
  readonly onResolve: (commentId: string) => void;
}): JSX.Element {
  const listRef = useRef<HTMLUListElement>(null);
  const clearFocus = useCommentPinStore((s) => s.focusComment);

  // A pin was clicked on the canvas: bring its row into view and leave the
  // highlight up long enough to be noticed, then clear it.
  //
  // The focus is cleared from the STORE rather than kept as local state so the
  // same pin can be clicked again immediately — a highlight that only fires on
  // a *change* of id would do nothing the second time, which reads as the pin
  // having stopped working.
  useEffect(() => {
    if (focusedCommentId === null) return undefined;
    const row = listRef.current?.querySelector(
      `[data-comment-id="${cssEscape(focusedCommentId)}"]`,
    );
    row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    const timer = setTimeout(() => clearFocus(null), FOCUS_HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [focusedCommentId, clearFocus]);

  const pinNumbers = new Map(numberPlanPins(comments).map((pin) => [pin.comment.id, pin.number]));

  if (loading && comments.length === 0) {
    return (
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3" aria-busy="true">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="space-y-1.5">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ))}
        <span className="sr-only" role="status">
          Loading comments
        </span>
      </div>
    );
  }

  if (error !== null && comments.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-4 py-6 text-center">
        <p className="text-sm text-ink-muted">{error.message}</p>
        <Button variant="secondary" size="sm" iconLeft="refresh" onClick={onRefresh}>
          Try again
        </Button>
      </div>
    );
  }

  if (comments.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-6 py-6">
        <p className="text-center text-sm leading-6 text-ink-muted">
          No open comments. Your team can add one below, and clients can comment through a share
          link that allows it.
        </p>
      </div>
    );
  }

  return (
    <ul
      ref={listRef}
      className="min-h-0 flex-1 divide-y divide-line overflow-y-auto"
      aria-label="Comment thread"
    >
      {comments.map((comment) => (
        <li
          key={comment.id}
          data-comment-id={comment.id}
          className={cn(
            'px-3 py-3 transition-colors',
            // The highlight is a background wash, not a border: a border would
            // shift every row below it by a pixel as it came and went.
            focusedCommentId === comment.id ? 'bg-brand-soft/60' : null,
          )}
        >
          <div className="flex items-baseline gap-2">
            {pinNumbers.has(comment.id) ? (
              <Badge tone="brand" icon="pin">
                {pinNumbers.get(comment.id)}
              </Badge>
            ) : null}
            <span className="min-w-0 truncate text-xs font-semibold text-ink">
              {comment.authorName === '' ? 'Someone' : comment.authorName}
            </span>
            {comment.fromShareLink ? <Badge tone="info">Client</Badge> : null}
            <span className="ml-auto shrink-0 text-2xs text-ink-subtle garh-nums">
              {formatRelative(comment.createdAt)}
            </span>
          </div>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-5 text-ink">
            {comment.body}
          </p>
          <div className="mt-1.5 flex items-center">
            {comment.resolved ? (
              <Badge tone="pass" icon="check">
                Resolved
              </Badge>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                iconLeft="check"
                loading={resolvingId === comment.id}
                loadingLabel="Resolving this comment"
                onClick={() => onResolve(comment.id)}
              >
                Resolve
              </Button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

function Composer({
  busy,
  onAdd,
}: {
  readonly busy: boolean;
  readonly onAdd: (body: string) => Promise<boolean>;
}): JSX.Element {
  const [value, setValue] = useState('');

  const submit = (): void => {
    if (busy || value.trim() === '') return;
    void onAdd(value).then((ok) => {
      // Only a posted comment clears the box; a failure keeps the words so
      // "Try again" on the toast has something to try with.
      if (ok) setValue('');
    });
  };

  return (
    <form
      className="border-t border-line px-3 py-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor="project-comment" className="sr-only">
        Add a comment
      </label>
      <div className="flex items-end gap-2">
        <textarea
          id="project-comment"
          value={value}
          rows={2}
          placeholder="Add a comment for the team…"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          className={cn(
            'garh-focus-ring min-h-[3.25rem] w-full resize-none rounded-md border border-line',
            'bg-surface px-2.5 py-1.5 text-sm leading-5 text-ink placeholder:text-ink-subtle',
          )}
        />
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={busy || value.trim() === ''}
          loading={busy}
          loadingLabel="Posting your comment"
        >
          Comment
        </Button>
      </div>
      <p className="mt-1.5 text-2xs text-ink-subtle">
        <kbd className="rounded border border-line px-1">Enter</kbd> posts ·{' '}
        <kbd className="rounded border border-line px-1">⇧Enter</kbd> for a new line
      </p>
    </form>
  );
}

export default CommentsPanel;
