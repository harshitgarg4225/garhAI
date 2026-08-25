/**
 * The heads-up readout while an item is being placed: what it is, how big, how
 * it is turned, what the tool thinks of the spot, and which keys do what.
 *
 * ## Why the DOM is written by hand here
 *
 * This is the one component that must react to POSE changes, which arrive at
 * pointer rate. Re-rendering it through React state would put a full render
 * pass — and the reconciliation of every sibling — inside the pointer-move
 * path, which is exactly the thing §14's 16 ms budget cannot afford.
 *
 * So: React renders the structure ONCE, and a subscription to the placement
 * controller's imperative channel writes `textContent` and one class name on
 * the nodes that change. That is a handful of DOM writes per move, no
 * reconciliation, no garbage. React still owns everything else — the panel only
 * mounts and unmounts through normal rendering.
 *
 * The advisory list is capped at three lines. A HUD that grows to eight rows
 * covers the plan it is describing.
 *
 * ## Advisories vs compliance chips
 *
 * These update live because they describe the thing under your cursor. The
 * debounced (≤500 ms) compliance strip is a different surface with different
 * rules; see {@link furnitureAdvisoryChips} for the chip-shaped view of the
 * same data, which is what belongs there.
 */

import { useEffect, useMemo, useRef } from 'react';

import { formatDimensionPair, formatLengthDisplay } from '../../../lib/units';
import { Chip, cn } from '@garh/ui';
import type { PlacementPoseState } from './placement';
import type { PlacementIssue } from './types';
import { useFurniturePlacement } from './useFurniturePlacement';

const MAX_ISSUE_LINES = 3;

export interface FurniturePlacementHudProps {
  readonly className?: string | undefined;
}

export function FurniturePlacementHud({ className }: FurniturePlacementHudProps): JSX.Element | null {
  const { controller, phase, armedItem, unitsDisplay } = useFurniturePlacement();

  const rootRef = useRef<HTMLDivElement>(null);
  const dimsRef = useRef<HTMLSpanElement>(null);
  const angleRef = useRef<HTMLSpanElement>(null);
  const posRef = useRef<HTMLSpanElement>(null);
  const issuesRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const write = (state: PlacementPoseState): void => {
      const root = rootRef.current;
      if (root === null) return;

      const item = state.item;
      if (item === null || state.phase === 'idle') {
        root.dataset.tone = 'ok';
        return;
      }

      root.dataset.tone = state.tone;

      if (dimsRef.current !== null) {
        dimsRef.current.textContent = formatDimensionPair(
          item.widthMm,
          item.depthMm,
          unitsDisplay,
        );
      }
      if (angleRef.current !== null) {
        angleRef.current.textContent = `${state.pose.rotationDeg}°`;
      }
      if (posRef.current !== null) {
        posRef.current.textContent = `${formatLengthDisplay(
          state.pose.pt.x,
          unitsDisplay,
        )}, ${formatLengthDisplay(state.pose.pt.y, unitsDisplay)}`;
      }
      writeIssues(issuesRef.current, state.issues);
    };

    write(controller.getPoseState());
    return controller.subscribePose(write);
    // `phase` is a dependency because this component renders `null` while idle:
    // the refs below only exist once it is armed, so the first write has to
    // happen again after that render or the panel opens showing nothing.
  }, [controller, unitsDisplay, phase]);

  const hints = useMemo(
    () => [
      { keys: 'R', text: 'rotate 90°' },
      { keys: 'Alt-drag', text: 'free rotate' },
      { keys: 'type a number', text: 'exact angle' },
      { keys: 'X / Y', text: 'exact position' },
      { keys: 'Enter', text: 'place' },
      { keys: 'Esc', text: 'cancel' },
    ],
    [],
  );

  if (phase === 'idle' || armedItem === null) return null;

  return (
    <div
      ref={rootRef}
      data-tone="ok"
      role="status"
      aria-live="polite"
      className={cn(
        'pointer-events-none flex max-w-sm flex-col gap-1.5 rounded-lg border bg-surface/95 p-3 shadow-lg',
        'data-[tone=ok]:border-line data-[tone=info]:border-info data-[tone=warn]:border-warn',
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate text-sm font-medium text-ink">{armedItem.name}</span>
        <span ref={dimsRef} className="shrink-0 text-xs text-ink-muted garh-nums" />
      </div>

      <div className="flex items-center gap-3 text-xs text-ink-muted garh-nums">
        <span>
          Turned <span ref={angleRef}>0°</span>
        </span>
        <span ref={posRef} />
      </div>

      <ul ref={issuesRef} className="flex flex-col gap-1 empty:hidden" />

      {armedItem.clearanceMm > 0 ? (
        <Chip size="sm" severity={armedItem.clearanceAssumed ? 'info' : 'neutral'} icon="ruler">
          {formatLengthDisplay(armedItem.clearanceMm, unitsDisplay)} access shown in front
          {armedItem.clearanceAssumed ? ' (assumed)' : ''}
        </Chip>
      ) : null}

      <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[11px] text-ink-subtle">
        {hints.map((hint) => (
          <span key={hint.keys}>
            <kbd className="rounded border border-line px-1 font-sans">{hint.keys}</kbd> {hint.text}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * Rewrite the advisory list in place.
 *
 * Rows are recycled rather than replaced: the common case is the same two
 * advisories with one changed word, and recycling keeps that to a `textContent`
 * write instead of a DOM teardown per pointer move.
 */
function writeIssues(list: HTMLUListElement | null, issues: readonly PlacementIssue[]): void {
  if (list === null) return;
  const shown = issues.slice(0, MAX_ISSUE_LINES);

  while (list.childElementCount > shown.length) {
    const last = list.lastElementChild;
    if (last === null) break;
    list.removeChild(last);
  }
  while (list.childElementCount < shown.length) {
    const li = document.createElement('li');
    li.className = 'text-xs leading-snug';
    list.appendChild(li);
  }

  shown.forEach((issue, index) => {
    const li = list.children[index];
    if (!(li instanceof HTMLElement)) return;
    li.className =
      issue.severity === 'warn'
        ? 'text-xs leading-snug text-warn-ink'
        : 'text-xs leading-snug text-ink-muted';
    li.textContent = `${issue.message} ${issue.fixHint}`;
    li.title = issue.basis;
  });
}

