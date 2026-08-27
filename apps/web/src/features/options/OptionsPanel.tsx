/**
 * OptionsPanel — the composition root the integrator mounts (suggested home:
 * the Plan tab above the canvas, or its own Options tab). Everything below it
 * is this feature's own; the only outside contracts are the stores and
 * lib/{api,sse}.
 *
 * States, per §15:
 *   - no job & no options   → empty state that teaches "Generate"
 *   - job running           → GenerationTheater (real events only)
 *   - succeeded, <3 options → cards + the honest banner from the gates outcome
 *   - succeeded, 0 options / failed → explains why + retry, with the
 *     discard-reason summary when the worker sent one
 *
 * Apply confirms when the current model already has geometry: the option
 * replaces it as ONE atomic op group — a single undo step — and the confirm
 * copy says exactly that. On an empty model it applies immediately; a confirm
 * for a no-loss action is just friction.
 */

import { useMemo, useState } from 'react';

import {
  Button,
  ConfirmDialog,
  EmptyState,
  Icon,
  SkeletonRegion,
  SkeletonText,
  useToast,
} from '@garh/ui';

import { useModelStore } from '../../stores/model';
import { CompareTwo } from './CompareTwo';
import { GenerationTheater } from './GenerationTheater';
import { OptionCard, floorName } from './OptionCard';
import { assumptionEditOp, effectiveBanner, moreLikeThisParams, newSeedParams } from './stats';
import { theaterFromJob } from './theater';
import { useOptionActions, useSolveOutcome, useSolverJob, useTheater } from './useOptions';
import type { PlanOption, PtMm } from './types';

import type { Op } from '@garh/model';

export interface OptionsPanelProps {
  readonly projectId: string;
  /** Plot boundary for the faint outline under each mini plan. */
  readonly plotOutline?: readonly PtMm[] | undefined;
  /** True once plot + brief exist; the empty state teaches the order. */
  readonly briefReady?: boolean | undefined;
  readonly className?: string | undefined;
}

interface PendingApply {
  readonly option: PlanOption;
  readonly index: number;
}

export function OptionsPanel({
  projectId,
  plotOutline,
  briefReady = true,
  className,
}: OptionsPanelProps): JSX.Element {
  const { toast } = useToast();
  const { job, isRunning, generate } = useSolverJob(projectId);
  // Subscribe only while the job is live; a terminal job's SSE backlog may be
  // gone, and reconnect-looping against it would be noise. The row state is
  // the honest fallback (theaterFromJob below).
  const liveTheater = useTheater(job !== null && !isTerminalStatus(job.status) ? job.id : null);
  const theater = liveTheater.status !== 'idle' || job === null ? liveTheater : theaterFromJob(job);
  const { outcome, loading, error, reload } = useSolveOutcome(job?.id ?? null, job?.status ?? null);
  const actions = useOptionActions(projectId);

  const [pendingApply, setPendingApply] = useState<PendingApply | null>(null);
  const [compareIds, setCompareIds] = useState<readonly string[]>([]);
  const [appliedOptionId, setAppliedOptionId] = useState<string | null>(null);

  const options = outcome?.options ?? [];
  const banner = outcome !== null ? effectiveBanner(outcome) : null;

  const comparePair = useMemo(() => {
    if (compareIds.length !== 2) return null;
    const ia = options.findIndex((o) => o.id === compareIds[0]);
    const ib = options.findIndex((o) => o.id === compareIds[1]);
    if (ia === -1 || ib === -1) return null;
    return { a: options[ia] as PlanOption, b: options[ib] as PlanOption, ia, ib };
  }, [compareIds, options]);

  // ── actions ─────────────────────────────────────────────────────────────

  const doApply = (option: PlanOption, index: number): void => {
    if (outcome === null) return;
    const result = actions.apply(option, index, outcome.jobId);
    if (result.ok) {
      setAppliedOptionId(option.id);
      toast({
        title: `Option ${index + 1} applied`,
        description: 'One undo step brings your previous design back.',
        severity: 'pass',
      });
    } else {
      toast({
        title: 'That plan could not be applied',
        description: result.issues[0]?.message ?? 'The model rejected one of its edits.',
        severity: 'fail',
        action: { label: 'Try another option', onClick: () => setCompareIds([]) },
      });
    }
  };

  const requestApply = (option: PlanOption, index: number): void => {
    if (actions.modelHasGeometry && option.id !== appliedOptionId) {
      setPendingApply({ option, index });
    } else {
      doApply(option, index);
    }
  };

  const startGenerate = (params?: Record<string, unknown>): void => {
    setCompareIds([]);
    setAppliedOptionId(null);
    generate(params ?? {}).catch((err: unknown) => {
      toast({
        title: 'Could not start plan generation',
        description: err instanceof Error ? err.message : 'Try again in a moment.',
        severity: 'fail',
        action: { label: 'Try again', onClick: () => startGenerate(params) },
      });
    });
  };

  const editAssumption = (field: string, raw: string): boolean => {
    const op = assumptionEditOp(field, raw);
    if (op === null) {
      toast({
        title: 'That value did not parse',
        description: 'Lengths take "12\'6"" or "3.8m"; areas take "120 sqft" or "11 sqm".',
        severity: 'warn',
      });
      return false;
    }
    const result = useModelStore
      .getState()
      .dispatch([op as unknown as Op], { label: 'Assumption updated', source: 'manual' });
    if (result.ok) {
      toast({
        title: 'Assumption updated',
        description: 'The next generation uses your value.',
        severity: 'pass',
      });
    }
    return result.ok;
  };

  const toggleCompare = (optionId: string): void => {
    setCompareIds((ids) => {
      if (ids.includes(optionId)) return ids.filter((id) => id !== optionId);
      // Any two: selecting a third swaps out the older selection.
      return ids.length >= 2 ? [ids[1] as string, optionId] : [...ids, optionId];
    });
  };

  // ── render ──────────────────────────────────────────────────────────────

  const showEmptyState = job === null;
  const failed = job !== null && job.status === 'failed';
  const succeededEmpty =
    job !== null &&
    job.status === 'succeeded' &&
    !loading &&
    outcome !== null &&
    options.length === 0;

  return (
    <div className={className}>
      <div className="space-y-4">
        {showEmptyState ? (
          <EmptyState
            icon="sparkles"
            title="No plan options yet"
            description={
              briefReady
                ? 'Generate 3–5 compliant floor plan options from your plot and brief. Every option passes the hard rule checks before you see it.'
                : 'Finish the plot boundary and brief first — the generator reads both. Then come back here and generate options.'
            }
            action={
              briefReady
                ? { label: 'Generate plan options', onClick: () => startGenerate() }
                : undefined
            }
            demoAction={{ notApplicable: 'Generation runs on your own plot and brief.' }}
          />
        ) : null}

        {job !== null && (isRunning || failed || job.status === 'cancelled') ? (
          <GenerationTheater
            theater={theater}
            onRetry={failed || job.status === 'cancelled' ? () => startGenerate() : undefined}
          />
        ) : null}

        {loading ? (
          <SkeletonRegion label="Loading plan options">
            <SkeletonText lines={4} />
          </SkeletonRegion>
        ) : null}

        {error !== null ? (
          <div role="alert" className="rounded-md border border-fail-line bg-fail-soft p-3">
            <p className="text-sm font-medium text-fail-ink">{error.message}</p>
            <p className="mt-1 text-xs text-fail-ink">{error.action}</p>
            <Button className="mt-2" size="sm" variant="secondary" onClick={reload}>
              <Icon name="refresh" size={14} /> Reload options
            </Button>
          </div>
        ) : null}

        {succeededEmpty ? (
          <EmptyState
            icon="info"
            title="No plan cleared the quality checks"
            description={
              outcome !== null && outcome.rejectedByGates > 0
                ? `${outcome.considered} layouts were tried and ${outcome.rejectedByGates} were discarded by the checks (room minimums, circulation, furniture fit). Loosening a room size or a must-face in the brief usually unlocks it.`
                : 'The plot, setbacks and brief left no workable layout. Loosening a room size or a must-face in the brief usually unlocks it.'
            }
            action={{ label: 'Try again', onClick: () => startGenerate(newSeedParams()) }}
            demoAction={{ notApplicable: 'Generation runs on your own plot and brief.' }}
          />
        ) : null}

        {options.length > 0 ? (
          <>
            {banner !== null ? (
              <p
                role="status"
                className="rounded-md border border-info-line bg-surface-muted px-3 py-2 text-sm text-ink"
              >
                <Icon name="info" size={14} className="mr-1.5 inline-block align-text-bottom" />
                {banner}
              </p>
            ) : null}

            {comparePair !== null ? (
              <CompareTwo
                a={comparePair.a}
                b={comparePair.b}
                indexA={comparePair.ia}
                indexB={comparePair.ib}
                outline={plotOutline}
                onClose={() => setCompareIds([])}
                onApply={requestApply}
              />
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {options.map((option, index) => (
                <OptionCard
                  key={option.id}
                  option={option}
                  optionIndex={index}
                  outline={plotOutline}
                  applied={option.id === appliedOptionId}
                  onApply={() => requestApply(option, index)}
                  onMoreLikeThis={
                    outcome !== null
                      ? () => startGenerate(moreLikeThisParams(outcome.params, option))
                      : undefined
                  }
                  compareSelected={compareIds.includes(option.id)}
                  onToggleCompare={() => toggleCompare(option.id)}
                  onEditAssumption={editAssumption}
                />
              ))}
            </div>

            <RegenControls
              lockableRooms={actions.lockableRooms}
              onToggleRoomLock={(roomId, locked) => {
                actions.setRoomLocked(roomId, locked);
              }}
              onRegenerateOthers={() => {
                actions.regenerateOthers().catch(() => {
                  toast({
                    title: 'Could not start the re-solve',
                    severity: 'fail',
                    action: { label: 'Try again', onClick: () => void actions.regenerateOthers() },
                  });
                });
              }}
              onRegenerateFloor={(floor) => {
                actions.regenerateFloor(floor).catch(() => {
                  toast({
                    title: 'Could not start the re-solve',
                    severity: 'fail',
                    action: {
                      label: 'Try again',
                      onClick: () => void actions.regenerateFloor(floor),
                    },
                  });
                });
              }}
              onNewSeed={() => startGenerate(newSeedParams())}
              disabled={isRunning}
            />
          </>
        ) : null}
      </div>

      <ConfirmDialog
        open={pendingApply !== null}
        onOpenChange={(open) => {
          if (!open) setPendingApply(null);
        }}
        title="Replace the current design?"
        description={
          pendingApply !== null
            ? `Applying Option ${pendingApply.index + 1} replaces the walls, rooms and stairs currently in your model. It lands as one change — a single Undo brings everything back.`
            : ''
        }
        confirmLabel="Apply and replace"
        cancelLabel="Keep my design"
        onConfirm={() => {
          if (pendingApply !== null) doApply(pendingApply.option, pendingApply.index);
          setPendingApply(null);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lock rooms + regenerate controls
// ---------------------------------------------------------------------------

interface RegenControlsProps {
  readonly lockableRooms: readonly {
    readonly roomId: string;
    readonly label: string;
    readonly storeyIndex: number;
    readonly locked: boolean;
  }[];
  readonly onToggleRoomLock: (roomId: string, locked: boolean) => void;
  readonly onRegenerateOthers: () => void;
  readonly onRegenerateFloor: (storeyIndex: number) => void;
  readonly onNewSeed: () => void;
  readonly disabled: boolean;
}

function RegenControls({
  lockableRooms,
  onToggleRoomLock,
  onRegenerateOthers,
  onRegenerateFloor,
  onNewSeed,
  disabled,
}: RegenControlsProps): JSX.Element {
  const lockedCount = lockableRooms.filter((r) => r.locked).length;
  const floors = [...new Set(lockableRooms.map((r) => r.storeyIndex))].sort((a, b) => a - b);

  return (
    <section
      aria-label="Regenerate"
      className="space-y-3 rounded-lg border border-line bg-surface-muted p-4"
    >
      <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
        Refine the search
      </h4>

      {lockableRooms.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-ink-muted">
            Lock the rooms you want to keep, then regenerate the rest. Locked rooms come back with
            the same shape in the same place.
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {lockableRooms.map((room) => (
              <li key={room.roomId}>
                <button
                  type="button"
                  aria-pressed={room.locked}
                  onClick={() => onToggleRoomLock(room.roomId, !room.locked)}
                  className={
                    room.locked
                      ? 'inline-flex items-center gap-1 rounded-full border border-brand bg-brand-soft px-2 py-0.5 text-2xs font-medium text-ink'
                      : 'inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-2xs text-ink-muted hover:border-line-strong'
                  }
                >
                  <Icon name="lock" size={10} />
                  {room.label}
                </button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={disabled || lockedCount === 0}
              onClick={onRegenerateOthers}
            >
              <Icon name="refresh" size={14} />
              Regenerate the other rooms
            </Button>
            {floors.length > 1
              ? floors.map((floor) => (
                  <Button
                    key={floor}
                    size="sm"
                    variant="ghost"
                    disabled={disabled}
                    onClick={() => onRegenerateFloor(floor)}
                  >
                    Redo {floorName(floor)} floor
                  </Button>
                ))
              : null}
          </div>
        </div>
      ) : (
        <p className="text-xs text-ink-subtle">
          Apply an option first to lock rooms and regenerate around them.
        </p>
      )}

      <div className="border-t border-line pt-2">
        <Button size="sm" variant="ghost" disabled={disabled} onClick={onNewSeed}>
          <Icon name="sparkles" size={14} />
          Try a fresh direction
        </Button>
      </div>
    </section>
  );
}

function isTerminalStatus(status: string): boolean {
  return status === 'succeeded' || status === 'failed' || status === 'cancelled';
}
