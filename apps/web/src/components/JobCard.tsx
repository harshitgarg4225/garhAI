/**
 * JobCard — solver / render / sheet / export jobs, told honestly.
 *
 * §15 "Generation theater": progress messages are driven by real worker events
 * ("Placing staircase…", "Checking BBMP setbacks…"), never invented, and
 * "job cards show queue position". §15 also forbids the fake bar — when the
 * worker cannot report a percentage we pass `progress: null` and `ProgressBar`
 * renders an indeterminate sweep instead of creeping to 90%.
 *
 * Golden rule 9: a failed job always shows the problem's next action as a real
 * button — `onRetry` for retryable failures, plus whatever `ProblemPanel`'s
 * recovery resolver decides.
 */

import { formatIndianDate } from '@garh/model';
import { Badge, Button, Card, Icon, ProgressBar, Spinner, cn } from '@garh/ui';
import type { BadgeTone, IconName } from '@garh/ui';
import type { JobKind, JobStatus, JobVM } from './types';

const KIND_LABEL: Readonly<Record<JobKind, string>> = {
  solver: 'Plan options',
  render: 'Render',
  sheets: 'Drawing set',
  export: 'Export',
};

const KIND_ICON: Readonly<Record<JobKind, IconName>> = {
  solver: 'sparkles',
  render: 'image',
  sheets: 'sheet',
  export: 'download',
};

const STATUS_TONE: Readonly<Record<JobStatus, BadgeTone>> = {
  queued: 'neutral',
  running: 'info',
  succeeded: 'pass',
  failed: 'fail',
  cancelled: 'neutral',
};

const STATUS_LABEL: Readonly<Record<JobStatus, string>> = {
  queued: 'Waiting',
  running: 'Working',
  succeeded: 'Ready',
  failed: 'Stopped',
  cancelled: 'Cancelled',
};

/**
 * The honest fallback line for each state. A worker that reports its own stage
 * overrides this via `stageMessage`; these are what we say when it has not
 * reported anything yet.
 */
function fallbackMessage(job: JobVM): string {
  switch (job.status) {
    case 'queued':
      return job.queuePosition === null
        ? 'In the queue. It will start as soon as a worker is free.'
        : job.queuePosition === 1
          ? 'Next in the queue.'
          : `${job.queuePosition - 1} job${job.queuePosition - 1 === 1 ? '' : 's'} ahead of this one.`;
    case 'running':
      return 'Working on it.';
    case 'succeeded':
      return 'Done.';
    case 'failed':
      return (
        job.error?.message ??
        'This job stopped before it finished. Nothing was changed in your design.'
      );
    case 'cancelled':
      return 'You cancelled this one. Nothing was changed.';
  }
}

export interface JobCardProps {
  job: JobVM;
  /** Cancel a queued/running job. */
  onCancel?: ((jobId: string) => void) | undefined;
  /** Re-run a failed job with the same parameters. */
  onRetry?: ((jobId: string) => void) | undefined;
  /** Open the result. Rendered as a button so the page owns navigation. */
  onOpenResult?: ((job: JobVM) => void) | undefined;
  /** Remove a finished card from the tray. */
  onDismiss?: ((jobId: string) => void) | undefined;
  compact?: boolean | undefined;
  className?: string | undefined;
}

export function JobCard({
  job,
  onCancel,
  onRetry,
  onOpenResult,
  onDismiss,
  compact = false,
  className,
}: JobCardProps): JSX.Element {
  const active = job.status === 'queued' || job.status === 'running';
  const message = job.stageMessage ?? fallbackMessage(job);

  return (
    <Card className={cn('p-3', className)}>
      <div className="flex items-start gap-3">
        <span
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
            job.status === 'failed'
              ? 'bg-fail-soft text-fail-ink'
              : job.status === 'succeeded'
                ? 'bg-pass-soft text-pass-ink'
                : 'bg-surface-muted text-ink-muted',
          )}
        >
          {job.status === 'running' ? (
            <Spinner size={16} />
          ) : (
            <Icon name={KIND_ICON[job.kind]} size={16} />
          )}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="truncate text-sm font-medium text-ink">{KIND_LABEL[job.kind]}</h4>
            <Badge tone={STATUS_TONE[job.status]} dot={active}>
              {STATUS_LABEL[job.status]}
            </Badge>
          </div>

          <p
            className={cn(
              'mt-0.5 text-xs leading-5',
              job.status === 'failed' ? 'text-fail-ink' : 'text-ink-muted',
            )}
            // Announce stage changes politely so a screen-reader user hears the
            // job progressing rather than having to poll the card.
            aria-live={active ? 'polite' : undefined}
          >
            {message}
          </p>

          {job.status === 'failed' && job.error?.action !== undefined ? (
            <p className="mt-0.5 text-xs leading-5 text-ink-muted">{job.error.action}</p>
          ) : null}

          {active && !compact ? (
            <ProgressBar
              className="mt-2"
              value={job.status === 'queued' ? null : job.progress}
              label={job.status === 'queued' ? 'Waiting for a worker' : 'Progress'}
              detail={
                job.status === 'queued' && job.queuePosition !== null
                  ? `#${job.queuePosition} in queue`
                  : job.progress === null
                    ? undefined
                    : `${Math.round(job.progress)}%`
              }
            />
          ) : null}

          {job.startedAt !== undefined && !compact ? (
            <p className="mt-1.5 text-2xs text-ink-subtle garh-nums">
              Started {formatIndianDate(job.startedAt)}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {job.status === 'succeeded' && onOpenResult !== undefined ? (
            <Button size="sm" variant="primary" onClick={() => onOpenResult(job)}>
              {job.resultLabel ?? 'Open'}
            </Button>
          ) : null}
          {job.status === 'failed' && onRetry !== undefined ? (
            <Button
              size="sm"
              variant="secondary"
              iconLeft="refresh"
              onClick={() => onRetry(job.id)}
            >
              Try again
            </Button>
          ) : null}
          {active && onCancel !== undefined ? (
            <Button size="sm" variant="ghost" onClick={() => onCancel(job.id)}>
              Cancel
            </Button>
          ) : null}
          {!active && onDismiss !== undefined ? (
            <Button size="sm" variant="ghost" onClick={() => onDismiss(job.id)}>
              Dismiss
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/**
 * A stack of job cards for the header tray. Empty renders nothing — an empty
 * job tray is not a screen the user asked to see.
 */
export function JobList({
  jobs,
  onCancel,
  onRetry,
  onOpenResult,
  onDismiss,
  className,
}: {
  jobs: readonly JobVM[];
  onCancel?: ((jobId: string) => void) | undefined;
  onRetry?: ((jobId: string) => void) | undefined;
  onOpenResult?: ((job: JobVM) => void) | undefined;
  onDismiss?: ((jobId: string) => void) | undefined;
  className?: string | undefined;
}): JSX.Element | null {
  if (jobs.length === 0) return null;
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {jobs.map((job) => (
        <JobCard
          key={job.id}
          job={job}
          onCancel={onCancel}
          onRetry={onRetry}
          onOpenResult={onOpenResult}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}
