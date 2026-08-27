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
 */

import { useEffect, useRef, useState } from 'react';

import { Badge, Button, Icon, IconButton, Skeleton, cn } from '@garh/ui';

import type { AppError } from '../../lib/errors';
import type { Comment } from '../../lib/schemas';
import { formatRelative } from '../../lib/units';

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
  // Refresh on every open, not merely mount: the badge count and any client
  // comments left since the last look are stale by exactly one refetch.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) onRefresh();
    wasOpen.current = open;
  }, [open, onRefresh]);

  if (!open) return null;

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
        <IconButton label="Close comments" icon="x" size="sm" variant="ghost" onClick={onClose} />
      </header>

      <CommentList
        comments={comments}
        loading={loading}
        error={error}
        resolvingId={resolvingId}
        onRefresh={onRefresh}
        onResolve={onResolve}
      />

      <Composer busy={busy} onAdd={onAdd} />
    </aside>
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
  onRefresh,
  onResolve,
}: {
  readonly comments: readonly Comment[];
  readonly loading: boolean;
  readonly error: AppError | null;
  readonly resolvingId: string | null;
  readonly onRefresh: () => void;
  readonly onResolve: (commentId: string) => void;
}): JSX.Element {
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
    <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto" aria-label="Comment thread">
      {comments.map((comment) => (
        <li key={comment.id} className="px-3 py-3">
          <div className="flex items-baseline gap-2">
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
