/**
 * AutosaveBadge — "Saved · v214".
 *
 * §12: "op round-trip indicator = subtle autosave badge". §15: optimistic ops
 * apply locally and roll back on reject, so this badge is the ONLY thing
 * telling the architect whether what they see is also what the server has. It
 * has to be honest in all five states, including the two nobody designs for:
 * offline, and "the server rejected your change".
 *
 * States:
 *   saved      everything acknowledged; shows the version number
 *   saving     ops queued, round-trip in flight (<100ms is the budget, so this
 *              usually flashes — that is fine, it is reassurance not a task)
 *   offline    we cannot reach the server; edits are still applied locally and
 *              queued, and we say exactly that
 *   conflict   a 409 — someone/something else advanced the op log; the client
 *              must rebase. Offers the reload as a real button.
 *   error      anything else; offers retry.
 */

import { Badge, Button, Icon, Spinner, Tooltip, cn } from '@garh/ui';

export type SaveState = 'saved' | 'saving' | 'offline' | 'conflict' | 'error';

export interface AutosaveBadgeProps {
  state: SaveState;
  /** Server-acknowledged op index — the "v214". */
  version?: number | undefined;
  /** How many local ops are still unacknowledged. */
  pendingCount?: number | undefined;
  /** Retry the queue / reload after a conflict. */
  onRecover?: (() => void) | undefined;
  className?: string | undefined;
}

export function AutosaveBadge({
  state,
  version,
  pendingCount = 0,
  onRecover,
  className,
}: AutosaveBadgeProps): JSX.Element {
  const versionText = version === undefined ? '' : ` · v${version}`;

  if (state === 'saving') {
    return (
      <Tooltip
        delayMs={400}
        content={
          pendingCount > 1
            ? `${pendingCount} changes on their way to the server.`
            : 'Sending your change to the server.'
        }
      >
        <span
          className={cn('inline-flex items-center gap-1.5 text-2xs text-ink-subtle garh-nums', className)}
          role="status"
        >
          <Spinner size={12} />
          Saving{versionText}
        </span>
      </Tooltip>
    );
  }

  if (state === 'saved') {
    return (
      <Tooltip delayMs={400} content="Every change is saved. Undo still works — nothing is final.">
        <span
          className={cn('inline-flex items-center gap-1.5 text-2xs text-ink-subtle garh-nums', className)}
          role="status"
        >
          <Icon name="check" size={12} className="text-pass" />
          Saved{versionText}
        </span>
      </Tooltip>
    );
  }

  if (state === 'offline') {
    return (
      <span className={cn('inline-flex items-center gap-2', className)} role="status">
        <Badge tone="warn" icon="alert-triangle">
          Offline
        </Badge>
        <span className="text-2xs text-ink-muted garh-nums">
          {pendingCount > 0
            ? `${pendingCount} change${pendingCount === 1 ? '' : 's'} waiting`
            : 'Changes are kept on this device'}
        </span>
      </span>
    );
  }

  if (state === 'conflict') {
    return (
      <span className={cn('inline-flex items-center gap-2', className)} role="alert">
        <Badge tone="warn" icon="refresh">
          Out of date
        </Badge>
        <span className="text-2xs text-ink-muted">This project moved on somewhere else.</span>
        {onRecover === undefined ? null : (
          <Button size="sm" variant="secondary" onClick={onRecover}>
            Load the latest
          </Button>
        )}
      </span>
    );
  }

  return (
    <span className={cn('inline-flex items-center gap-2', className)} role="alert">
      <Badge tone="fail" icon="alert-circle">
        Not saved
      </Badge>
      <span className="text-2xs text-ink-muted">
        Your work is still here — we just could not save it.
      </span>
      {onRecover === undefined ? null : (
        <Button size="sm" variant="secondary" onClick={onRecover}>
          Try again
        </Button>
      )}
    </span>
  );
}
