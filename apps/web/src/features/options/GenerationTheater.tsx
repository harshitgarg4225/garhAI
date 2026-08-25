/**
 * GenerationTheater — §15's honest solver progress. A stage timeline whose
 * every word came off the wire (services/solver/pipeline.py STAGES emits the
 * copy; this component renders `message` verbatim), plan silhouettes appearing
 * as candidates clear the gates, queue position while queued, and an honest
 * failure card with retry.
 *
 * NEVER a fake bar. The percent shown is the last one an event delivered; when
 * the worker is silent the number simply stops, and the active stage keeps a
 * spinner that says "working" — which is all we actually know.
 */

import { Button, Icon, ProgressBar, Spinner, cn } from '@garh/ui';

import { miniPlanFromEvent } from './planGeometry';
import { MiniPlanSvg } from './MiniPlanSvg';
import type { TheaterSilhouette, TheaterStage, TheaterState } from './theater';

export interface GenerationTheaterProps {
  readonly theater: TheaterState;
  /** Re-submit the failed job. Wired to the jobs store / generate action. */
  readonly onRetry?: (() => void) | undefined;
  readonly className?: string | undefined;
}

export function GenerationTheater({
  theater,
  onRetry,
  className,
}: GenerationTheaterProps): JSX.Element | null {
  if (theater.status === 'idle') return null;

  return (
    <section
      aria-label="Plan generation progress"
      aria-live="polite"
      className={cn('space-y-4 rounded-lg border border-line bg-surface p-4', className)}
    >
      {theater.status === 'queued' ? <QueuedNote position={theater.queuePosition} /> : null}

      {theater.percent !== null && !theater.done ? (
        <ProgressBar value={theater.percent} label="Plan generation" />
      ) : null}

      {theater.stages.length > 0 ? (
        <ol className="space-y-2">
          {theater.stages.map((stage) => (
            <StageRow key={stage.id} stage={stage} jobDone={theater.done} />
          ))}
        </ol>
      ) : theater.status === 'running' ? (
        // Events not heard yet (worker booting) — say that, invent nothing.
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          <Spinner size={14} /> Starting up…
        </p>
      ) : null}

      {theater.warnings.map((warning, i) => (
        <p key={i} className="flex items-start gap-2 text-xs text-warn-ink">
          <Icon name="info" size={14} className="mt-0.5 shrink-0" />
          {warning}
        </p>
      ))}

      {theater.silhouettes.length > 0 ? (
        <Silhouettes silhouettes={theater.silhouettes} />
      ) : null}

      {theater.status === 'failed' && theater.failure !== null ? (
        <FailureCard
          message={theater.failure.message}
          action={theater.failure.action}
          discardSummary={theater.failure.discardSummary}
          onRetry={onRetry}
        />
      ) : null}

      {theater.status === 'cancelled' ? (
        <p className="text-sm text-ink-muted">Generation cancelled. Nothing was changed.</p>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------

function QueuedNote({ position }: { position: number | null }): JSX.Element {
  return (
    <p className="flex items-center gap-2 text-sm text-ink-muted">
      <Icon name="clock" size={14} className="shrink-0" />
      {position !== null && position > 0
        ? `Waiting in the queue — ${position} ${position === 1 ? 'job' : 'jobs'} ahead of this one.`
        : 'Waiting in the queue…'}
    </p>
  );
}

function StageRow({ stage, jobDone }: { stage: TheaterStage; jobDone: boolean }): JSX.Element {
  const done = stage.state === 'done' || jobDone;
  return (
    <li className="flex items-start gap-2.5">
      <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
        {done ? (
          <Icon name="check" size={14} className="text-pass" title="Done" />
        ) : (
          <Spinner size={14} />
        )}
      </span>
      <div className="min-w-0">
        <p className={cn('text-sm', done ? 'text-ink-subtle' : 'text-ink')}>{stage.message}</p>
        {stage.detail !== null && !done ? (
          <p className="text-2xs text-ink-subtle">{stage.detail}</p>
        ) : null}
      </div>
    </li>
  );
}

function Silhouettes({
  silhouettes,
}: {
  silhouettes: readonly TheaterSilhouette[];
}): JSX.Element {
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-ink-muted">
        {silhouettes.length === 1
          ? '1 plan has cleared the checks'
          : `${silhouettes.length} plans have cleared the checks`}
      </p>
      <ul className="flex flex-wrap gap-2">
        {silhouettes.map((s) => (
          <li
            key={s.optionId}
            className="w-20 rounded-md border border-line bg-surface-muted p-1.5 text-center"
          >
            {s.miniPlan !== null ? (
              <MiniPlanSvg
                geometry={miniPlanFromEvent(s.miniPlan)}
                showLabels={false}
                label={`Plan ${s.rank + 1} silhouette`}
              />
            ) : (
              <div
                aria-hidden
                className="flex aspect-square items-center justify-center rounded bg-surface-sunken"
              >
                <Icon name="home" size={20} className="text-ink-subtle" />
              </div>
            )}
            <p className="mt-1 text-2xs text-ink-muted">{s.composite}/100</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FailureCard({
  message,
  action,
  discardSummary,
  onRetry,
}: {
  message: string;
  action: string | null;
  discardSummary: string | null;
  onRetry: (() => void) | undefined;
}): JSX.Element {
  return (
    <div
      role="alert"
      className="space-y-2 rounded-md border border-fail-line bg-fail-soft p-3"
    >
      <p className="text-sm font-medium text-fail-ink">{message}</p>
      {discardSummary !== null ? <p className="text-xs text-fail-ink">{discardSummary}</p> : null}
      <p className="text-xs text-fail-ink">
        {action ??
          'Try again — if it keeps failing, loosen a brief requirement (a room size or a must-face) and re-run.'}
      </p>
      {onRetry !== undefined ? (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <Icon name="refresh" size={14} /> Try again
        </Button>
      ) : null}
    </div>
  );
}
