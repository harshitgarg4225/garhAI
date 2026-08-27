/**
 * Inspector — the right panel of the project shell (§12).
 *
 * "Right inspector (selection properties, all editable, mm/ft-in aware
 * inputs)". Every number in here is a `LengthInput`, so typing `12'6"` into a
 * wall-thickness box works and stores 3810 mm. §15's "numbers editable
 * everywhere / no dead text" applies to this panel more than anywhere else.
 *
 * The inspector is a SHELL. It does not know about walls or openings; the
 * canvas layer (Phase 4) supplies the property rows for whatever is selected.
 * That keeps the panel chrome — header, empty state, scrolling, sections — in
 * one place while element editors evolve.
 */

import type { ReactNode } from 'react';
import type { UnitsDisplay } from '@garh/model';
import { EmptyState, Icon, LengthInput, PanelSection, cn } from '@garh/ui';
import type { IconName } from '@garh/ui';

export interface InspectorProps {
  /** What is selected, e.g. "Wall" or "3 items". Absent = nothing selected. */
  selectionTitle?: string | undefined;
  selectionIcon?: IconName | undefined;
  /** The element id, shown small and monospace — copilot and bug reports need it. */
  selectionId?: string | undefined;
  /** Property rows for the selection. Phase 4 canvas supplies these. */
  children?: ReactNode;
  /** Shown instead of the empty state while the model is loading. */
  loading?: boolean | undefined;
  /** Message for the empty state, overridable per tab. */
  emptyHint?: string | undefined;
  className?: string | undefined;
}

export function Inspector({
  selectionTitle,
  selectionIcon = 'cursor',
  selectionId,
  children,
  loading = false,
  emptyHint,
  className,
}: InspectorProps): JSX.Element {
  return (
    <aside
      aria-label="Inspector"
      className={cn(
        'flex h-full w-inspector shrink-0 flex-col border-l border-line bg-surface',
        className,
      )}
    >
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-line px-3">
        <Icon name={selectionIcon} size={15} className="text-ink-subtle" />
        <h2 className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-ink-subtle">
          {selectionTitle ?? 'Inspector'}
        </h2>
        {selectionId === undefined ? null : (
          <code
            className="max-w-[9rem] truncate font-mono text-2xs text-ink-subtle"
            title={selectionId}
          >
            {selectionId}
          </code>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-3">
            <div className="flex flex-col gap-3">
              <div className="h-2.5 w-16 animate-pulse rounded bg-surface-muted" />
              <div className="h-9 w-full animate-pulse rounded-md bg-surface-muted" />
              <div className="h-2.5 w-20 animate-pulse rounded bg-surface-muted" />
              <div className="h-9 w-full animate-pulse rounded-md bg-surface-muted" />
            </div>
          </div>
        ) : selectionTitle === undefined ? (
          <EmptyState
            size="sm"
            icon="cursor"
            title="Nothing selected"
            description={
              emptyHint ??
              'Click a wall, door or room on the plan and its properties appear here — all editable.'
            }
            demoAction={{
              notApplicable:
                'The inspector lives inside a project; the demo-project offer belongs on the tab-level empty state, not here.',
            }}
          />
        ) : (
          children
        )}
      </div>
    </aside>
  );
}

/**
 * A labelled millimetre property row for the inspector.
 *
 * This is the shape every element editor should use, so that "wall thickness"
 * and "sill height" behave identically: type any unit, see the mm, Escape to
 * revert, and the commit dispatches ONE op.
 */
export function LengthProperty({
  label,
  valueMm,
  onCommitMm,
  display,
  hint,
  minMm,
  maxMm,
  bareUnit = 'mm',
  disabled,
}: {
  label: string;
  valueMm: number | null;
  onCommitMm: (mm: number) => void;
  display: UnitsDisplay;
  hint?: string | undefined;
  minMm?: number | undefined;
  maxMm?: number | undefined;
  /** Inspector fields are usually native-mm (thickness, sill), hence the default. */
  bareUnit?: 'mm' | 'ft-in' | 'm' | undefined;
  disabled?: boolean | undefined;
}): JSX.Element {
  return (
    <LengthInput
      label={label}
      valueMm={valueMm}
      onCommitMm={onCommitMm}
      display={display}
      bareUnit={bareUnit}
      hint={hint}
      minMm={minMm}
      maxMm={maxMm}
      disabled={disabled}
    />
  );
}

/** Re-exported so element editors do not each import from two places. */
export { PanelSection };
