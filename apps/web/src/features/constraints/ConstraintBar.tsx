/**
 * ConstraintBar.tsx — the six drafting constraints, on the canvas (C-3).
 *
 * Appears only when a wall is selected, because that is the only moment any of these
 * mean anything, and a permanently-visible bar of six greyed buttons is six pieces of
 * chrome an architect has to read past all day.
 *
 * ## The anchor is stated, not assumed
 *
 * "Make parallel" has infinitely many answers, and every CAD package resolves it the
 * same way: the first thing you selected is the reference. That rule is invisible
 * unless you say it, so the bar names the anchor wall by its position in the selection
 * — "matching the first wall" — rather than leaving the architect to discover which of
 * their two walls is about to move.
 */

import { Button, Icon, cn } from '@garh/ui';
import type { ConstraintKind } from '@garh/model';

import { useSelectionStore } from '../../stores/selection';
import { requiredWalls, runConstraint } from './actions';

interface Entry {
  readonly kind: ConstraintKind;
  readonly label: string;
  /** Shown on hover. Says what MOVES, which is the part that surprises people. */
  readonly title: string;
}

const ENTRIES: readonly Entry[] = [
  {
    kind: 'horizontal',
    label: 'Horizontal',
    title: 'Turn the wall exactly horizontal, keeping its length and its junction.',
  },
  {
    kind: 'vertical',
    label: 'Vertical',
    title: 'Turn the wall exactly vertical, keeping its length and its junction.',
  },
  {
    kind: 'parallel',
    label: 'Parallel',
    title: 'Turn the other walls to match the first one. The first wall does not move.',
  },
  {
    kind: 'perpendicular',
    label: 'Perpendicular',
    title: 'Turn the other walls square to the first one. The first wall does not move.',
  },
  {
    kind: 'collinear',
    label: 'Collinear',
    title: 'Put the other walls on the first one’s line. The first wall does not move.',
  },
  {
    kind: 'equal-length',
    label: 'Equal length',
    title: 'Give the other walls the first one’s length. The first wall does not move.',
  },
];

export interface ConstraintBarProps {
  className?: string;
}

export function ConstraintBar({ className }: ConstraintBarProps): JSX.Element | null {
  // Subscribed, not read once: the bar has to appear and disappear with the selection.
  const ids = useSelectionStore((s) => s.ids);
  const kinds = useSelectionStore((s) => s.kinds);
  const walls = ids.filter((id) => kinds[id] === 'wall');

  if (walls.length === 0) return null;

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1 rounded-md border border-line bg-surface px-2 py-1.5',
        className,
      )}
      role="group"
      aria-label="Geometric constraints"
      data-testid="constraint-bar"
    >
      <Icon name="ruler" className="h-3.5 w-3.5 text-ink-subtle" aria-hidden />
      {ENTRIES.map((entry) => {
        const enabled = walls.length >= requiredWalls(entry.kind);
        return (
          <Button
            key={entry.kind}
            size="sm"
            variant="ghost"
            disabled={!enabled}
            title={entry.title}
            onClick={() => runConstraint(entry.kind)}
          >
            {entry.label}
          </Button>
        );
      })}
      {walls.length > 1 ? (
        <span className="ml-1 text-2xs text-ink-subtle">matching the first wall selected</span>
      ) : (
        <span className="ml-1 text-2xs text-ink-subtle">
          shift-click a second wall to match one to another
        </span>
      )}
    </div>
  );
}

export default ConstraintBar;
