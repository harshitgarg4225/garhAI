/**
 * StoreyPanel.tsx — navigate the storeys, and copy one onto another.
 *
 * A collapsible card in the plan overlay, in the same visual language as the
 * sun and underlay panels (`border-line`, `bg-surface/95`, 2xs labels,
 * `garh-nums` on every figure).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY A PANEL WHEN THE TOP BAR ALREADY HAS STOREY TABS
 * ════════════════════════════════════════════════════════════════════════════
 * The tabs in `TopBar` are a switcher and nothing more: "GF · FF", one word
 * each, no room for the two numbers that decide whether you are on the right
 * floor (its FFL, and what is actually on it). They stay the fast path — this
 * panel is the one place that shows the stack as a stack, tallest first, the
 * way a section reads, and the only place that can act on a storey.
 *
 * Both write `ui.activeStoreyId`. There is no second "current storey" anywhere
 * in this feature — see `store.ts` for why that matters.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * POINTER EVENTS
 * ════════════════════════════════════════════════════════════════════════════
 * No pointer guard of its own, for the reason `MeasurePanel` states: the ONE
 * native listener `CanvasRoot` puts on its overlay wrapper already stops a
 * press on a panel button from also dropping a wall point behind it. This panel
 * only has to opt back into hit-testing with `pointer-events-auto`.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE DESTRUCTIVE CASE IS A DIALOG, NOT A TOAST
 * ════════════════════════════════════════════════════════════════════════════
 * Copying onto a storey that already has walls deletes them. That is a real
 * loss of work, so it is confirmed BEFORE it happens, with the count of what
 * will go, in a dialog that does not dismiss on a stray backdrop click. It is
 * still one undo afterwards — the confirm is not a substitute for the undo, it
 * is what stops the undo from being needed.
 *
 * The dialog's counts come from `planStoreyCopy`'s own plan, so what it
 * promises and what the ops do cannot drift.
 */

import { useMemo, useState } from 'react';

import { Button, Dialog, Icon, Select, cn, type SelectOption } from '@garh/ui';

import type { UnitsDisplay } from '@garh/model';

import { formatLengthDisplay } from '../../lib/units';
import { useModelStore } from '../../stores/model';
import { selectActiveStoreyId, useUiStore } from '../../stores/ui';
import { storeyFflMm } from '../../pages/project/plan/planGeometry';
import { runAddStorey, runStoreyCopy } from './actions';
import {
  describeCounts,
  isStoreyEmpty,
  planStoreyCopy,
  storeyContentCounts,
  type StoreyCopyPlan,
  type StoreyCopyTarget,
} from './copyStorey';
import { storeyBelow } from './ghostGeometry';
import { MAX_GHOST_OPACITY, MIN_GHOST_OPACITY, useStoreysStore } from './store';

/** The sentinel the target `<select>` uses for "a new storey on top". */
const NEW_STOREY_VALUE = '__new__';

export interface StoreyPanelProps {
  readonly className?: string | undefined;
}

export function StoreyPanel({ className }: StoreyPanelProps): JSX.Element {
  const house = useModelStore((s) => s.doc.house);
  const activeStoreyId = useUiStore(selectActiveStoreyId);
  const setActiveStorey = useUiStore((s) => s.setActiveStorey);
  const ghostVisible = useStoreysStore((s) => s.ghostVisible);
  const ghostOpacity = useStoreysStore((s) => s.ghostOpacity);
  const setGhostVisible = useStoreysStore((s) => s.setGhostVisible);
  const setGhostOpacity = useStoreysStore((s) => s.setGhostOpacity);

  const [open, setOpen] = useState(true);
  const [copyOpen, setCopyOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const storeys = house.storeys;
  const display = house.meta.unitsDisplay;
  const below = storeyBelow(house, activeStoreyId);

  // Tallest first: the panel reads like a section, and "the floor above" is
  // genuinely above the one you are on.
  const rows = useMemo(
    () =>
      storeys
        .map((storey) => ({
          storey,
          fflMm: storeyFflMm(house, storey.id),
          counts: storeyContentCounts(house, storey.id),
        }))
        .reverse(),
    [house, storeys],
  );

  return (
    <>
      <div
        className={cn(
          'pointer-events-auto flex w-64 flex-col gap-2 rounded-md border border-line bg-surface/95 p-3 shadow-sm backdrop-blur',
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            className="garh-focus-ring -m-1 flex min-w-0 items-center gap-1.5 rounded p-1 text-left"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <Icon name="layers" size={14} className="shrink-0 text-ink-subtle" />
            <h3 className="text-xs font-semibold text-ink">Storeys</h3>
            <Icon
              name={open ? 'chevron-down' : 'chevron-up'}
              size={14}
              className="shrink-0 text-ink-subtle"
            />
          </button>
          <span className="shrink-0 text-2xs text-ink-subtle garh-nums">
            {storeys.length === 0 ? 'none' : `${String(storeys.length)} floors`}
          </span>
        </div>

        {open ? (
          <>
            {storeys.length === 0 ? (
              <p className="text-2xs leading-4 text-ink-subtle">
                This design has no storeys yet. Generating options from the brief creates the ground
                floor; you can also add one here and draw on it.
              </p>
            ) : (
              <div role="radiogroup" aria-label="Active storey" className="flex flex-col gap-1">
                {rows.map((row) => (
                  <StoreyRow
                    key={row.storey.id}
                    name={row.storey.name}
                    display={display}
                    fflMm={row.fflMm}
                    heightMm={row.storey.heightMm}
                    summary={isStoreyEmpty(row.counts) ? 'empty' : describeCounts(row.counts)}
                    active={row.storey.id === activeStoreyId}
                    isGhost={row.storey.id === below?.id}
                    onSelect={() => setActiveStorey(row.storey.id)}
                  />
                ))}
              </div>
            )}

            {/* ── the storey below, as a faded underlay ─────────────────── */}
            <div className="flex flex-col gap-1 border-t border-line pt-2">
              <label className="flex items-center justify-between gap-2 text-2xs font-medium text-ink-muted">
                <span className="flex min-w-0 items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={ghostVisible}
                    disabled={below === null}
                    onChange={(e) => setGhostVisible(e.target.checked)}
                    aria-label="Show the storey below"
                  />
                  <span className="truncate">
                    {below === null ? 'Nothing below' : `Show ${below.name} below`}
                  </span>
                </span>
                {below === null ? null : (
                  <span className="shrink-0 text-ink garh-nums">
                    {String(Math.round(ghostOpacity * 100))}%
                  </span>
                )}
              </label>
              {below === null ? (
                <p className="text-2xs leading-4 text-ink-subtle">
                  {activeStoreyId === null
                    ? 'Pick a storey to draw on.'
                    : 'This is the lowest storey, so there is nothing to trace over.'}
                </p>
              ) : (
                <input
                  type="range"
                  min={Math.round(MIN_GHOST_OPACITY * 100)}
                  max={Math.round(MAX_GHOST_OPACITY * 100)}
                  step={5}
                  value={Math.round(ghostOpacity * 100)}
                  disabled={!ghostVisible}
                  className="w-full"
                  aria-label="Storey below opacity"
                  onChange={(e) => setGhostOpacity(Number(e.target.value) / 100)}
                />
              )}
            </div>

            <div className="flex flex-wrap gap-1 border-t border-line pt-2">
              <Button
                size="sm"
                variant="secondary"
                iconLeft="layers"
                onClick={() => {
                  const outcome = runAddStorey();
                  setNotice(outcome.ok ? null : outcome.message);
                }}
              >
                Add storey
              </Button>
              <Button
                size="sm"
                variant="secondary"
                iconLeft="copy"
                disabled={storeys.length === 0}
                onClick={() => setCopyOpen(true)}
              >
                Copy storey…
              </Button>
            </div>

            {notice === null ? null : (
              <p className="text-2xs leading-4 text-fail-ink" role="alert">
                {notice}
              </p>
            )}
          </>
        ) : null}
      </div>

      {/* Mounted only while open, so every visit starts from the active storey
          rather than from whatever was picked last time. */}
      {copyOpen ? (
        <CopyStoreyDialog onClose={() => setCopyOpen(false)} defaultSourceId={activeStoreyId} />
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// One row of the stack
// ---------------------------------------------------------------------------

interface StoreyRowProps {
  readonly name: string;
  readonly display: UnitsDisplay;
  readonly fflMm: number;
  readonly heightMm: number;
  readonly summary: string;
  readonly active: boolean;
  readonly isGhost: boolean;
  readonly onSelect: () => void;
}

function StoreyRow({
  name,
  display,
  fflMm,
  heightMm,
  summary,
  active,
  isGhost,
  onSelect,
}: StoreyRowProps): JSX.Element {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onSelect}
      className={cn(
        'garh-focus-ring flex w-full flex-col gap-0.5 rounded border px-2 py-1.5 text-left transition-colors',
        active
          ? 'border-brand bg-brand-soft text-ink'
          : 'border-transparent text-ink-muted hover:border-line hover:text-ink',
      )}
    >
      <span className="flex items-baseline justify-between gap-2">
        <span className="truncate text-xs font-medium">{name}</span>
        {/* FFL is the number that tells you which floor you are on in a
            section, so it is the one the row leads with. */}
        <span className="shrink-0 text-2xs text-ink-subtle garh-nums">
          FFL {formatLengthDisplay(fflMm, display)}
        </span>
      </span>
      <span className="flex items-baseline justify-between gap-2 text-2xs text-ink-subtle">
        <span className="truncate">{summary}</span>
        <span className="shrink-0 garh-nums">
          {isGhost ? 'below · ' : ''}
          {formatLengthDisplay(heightMm, display)} f-f
        </span>
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// The copy dialog
// ---------------------------------------------------------------------------

/**
 * "New storey on top", or a storey that exists.
 *
 * A target equal to the source can only happen for a moment — the source select
 * changed and the target select has not caught up — and it falls back to a new
 * storey rather than planning a copy onto itself.
 */
function targetOf(targetValue: string, sourceId: string): StoreyCopyTarget {
  return targetValue === NEW_STOREY_VALUE || targetValue === sourceId
    ? { kind: 'new' }
    : { kind: 'existing', storeyId: targetValue };
}

interface CopyStoreyDialogProps {
  readonly onClose: () => void;
  readonly defaultSourceId: string | null;
}

function CopyStoreyDialog({ onClose, defaultSourceId }: CopyStoreyDialogProps): JSX.Element {
  const house = useModelStore((s) => s.doc.house);
  const doc = useModelStore((s) => s.doc);
  const display = house.meta.unitsDisplay;

  // Seeded once, on mount: the parent only mounts this while the dialog is
  // open, so "the active storey" is read at the moment the architect asked for
  // it and cannot be reset under them by a collaborator's op landing.
  const [sourceId, setSourceId] = useState<string>(defaultSourceId ?? house.storeys[0]?.id ?? '');
  const [targetValue, setTargetValue] = useState<string>(NEW_STOREY_VALUE);
  const [matchHeight, setMatchHeight] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const sourceOptions = useMemo<SelectOption[]>(
    () => house.storeys.map((s) => ({ value: s.id, label: s.name })),
    [house.storeys],
  );
  const targetOptions = useMemo<SelectOption[]>(
    () => [
      ...house.storeys
        .filter((s) => s.id !== sourceId)
        .map((s) => ({ value: s.id, label: s.name })),
      { value: NEW_STOREY_VALUE, label: 'New storey on top' },
    ],
    [house.storeys, sourceId],
  );

  // The plan is computed live, so the dialog describes the ops it will actually
  // dispatch — not a second guess at what a copy does.
  const planned = useMemo(
    () =>
      sourceId === ''
        ? null
        : planStoreyCopy(doc, {
            sourceStoreyId: sourceId,
            target: targetOf(targetValue, sourceId),
            matchHeight,
          }),
    [doc, sourceId, targetValue, matchHeight],
  );

  const plan: StoreyCopyPlan | null = planned?.ok === true ? planned.plan : null;
  const refusal = planned !== null && !planned.ok ? planned.refusal : null;
  const destructive = plan !== null && !isStoreyEmpty(plan.replaced);

  const confirm = (): void => {
    if (sourceId === '') return;
    const outcome = runStoreyCopy({
      sourceStoreyId: sourceId,
      target: targetOf(targetValue, sourceId),
      matchHeight,
    });
    if (!outcome.ok) {
      setError(outcome.refusal.message);
      return;
    }
    onClose();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title="Copy a storey"
      description="Duplicate everything on one floor onto another — then change the three things that differ."
      size="md"
      /* A stray backdrop click must not start a copy that deletes a floor. */
      dismissOnBackdrop={!destructive}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={destructive ? 'danger' : 'primary'}
            disabled={plan === null}
            onClick={confirm}
          >
            {destructive ? 'Replace and copy' : 'Copy'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
            Copy from
            <Select value={sourceId} onValueChange={setSourceId} options={sourceOptions} />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
            Onto
            <Select value={targetValue} onValueChange={setTargetValue} options={targetOptions} />
          </label>
        </div>

        {plan === null ? null : (
          <p className="text-xs leading-5 text-ink-muted">
            {describeCounts(plan.copied)} will be copied to{' '}
            <span className="font-medium text-ink">{plan.targetName}</span>
            {plan.roomsCarried === 0
              ? '. '
              : `, carrying ${String(plan.roomsCarried)} room ${
                  plan.roomsCarried === 1 ? 'name' : 'names'
                }. `}
            Materials and facade components are not copied.
          </p>
        )}

        {plan?.heightChangeMm != null ? (
          <p className="text-xs leading-5 text-ink-muted garh-nums">
            {plan.targetName}&rsquo;s floor-to-floor height changes from{' '}
            {formatLengthDisplay(plan.heightChangeMm[0], display)} to{' '}
            {formatLengthDisplay(plan.heightChangeMm[1], display)}.
          </p>
        ) : null}

        {/* Only for an existing target: a NEW storey is created at the source's
            height already, so the option would be a control over nothing. */}
        {targetOf(targetValue, sourceId).kind === 'existing' ? (
          <label className="flex items-start gap-2 text-xs text-ink-muted">
            <input
              type="checkbox"
              checked={matchHeight}
              onChange={(e) => setMatchHeight(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Match the floor-to-floor height.
              <span className="block text-2xs text-ink-subtle">
                A stair only fits a storey of the height it was drawn for, and a window cannot be
                taller than the floor it sits in — so a copy onto a shorter storey is refused
                without this.
              </span>
            </span>
          </label>
        ) : null}

        {destructive && plan !== null ? (
          <p
            className="rounded border border-fail-line bg-fail-soft px-2 py-1.5 text-xs leading-5 text-fail-ink"
            role="status"
          >
            <Icon name="alert-triangle" size={14} className="mr-1 inline align-[-2px]" />
            {plan.targetName} already has {describeCounts(plan.replaced)}. They will be deleted.
            This is one undo step — ⌘Z puts them back.
          </p>
        ) : null}

        {refusal === null ? null : (
          <p className="text-xs leading-5 text-fail-ink" role="alert">
            {refusal.message}
          </p>
        )}
        {error === null ? null : (
          <p className="text-xs leading-5 text-fail-ink" role="alert">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}

export default StoreyPanel;
