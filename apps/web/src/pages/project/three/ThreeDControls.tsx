/**
 * ThreeDControls — the 3D view's DOM chrome: the storey-visibility switch and
 * the honest §15 status chip. Pure view of `stores/three.ts` + the model's
 * storey list; every action is a store write, never an op (visibility and
 * telemetry are viewing conditions, like the camera).
 *
 * THE STATUS CHIP IS A CONTRACT, NOT DECORATION. It is the §14/§8 honesty
 * surface in words: how long the last incremental rebuild took and whether
 * walls currently render without their opening holes (Manifold WASM still
 * loading, or unavailable). Its `data-garh-*` attributes are read by
 * `e2e/tests/three-d.spec.ts` — the budget assertion and the sun-scrub
 * "no geometry rebuild" assertion both poll them — so renaming them is an e2e
 * change, not a cleanup.
 */

import { cn } from '@garh/ui';

import { useModelStore } from '../../../stores/model';
import { useThreeStore } from '../../../stores/three';

// ---------------------------------------------------------------------------
// Storey visibility — see one storey, or the whole building
// ---------------------------------------------------------------------------

export function StoreyVisibilityBar({
  className,
}: {
  className?: string | undefined;
}): JSX.Element | null {
  const storeys = useModelStore((s) => s.doc.house.storeys);
  const visibleStoreyId = useThreeStore((s) => s.visibleStoreyId);
  const setVisibleStorey = useThreeStore((s) => s.setVisibleStorey);

  // One storey has no "see one storey" question to answer.
  if (storeys.length < 2) return null;

  return (
    <div
      role="group"
      aria-label="Storeys shown in 3D"
      className={cn(
        'pointer-events-auto flex items-center gap-0.5 rounded-md border border-line bg-surface/95 p-0.5 shadow-sm backdrop-blur',
        className,
      )}
    >
      <StoreyChip
        label="All"
        title="Show the whole building"
        active={visibleStoreyId === null}
        onClick={() => setVisibleStorey(null)}
      />
      {storeys.map((storey, index) => (
        <StoreyChip
          key={storey.id}
          label={shortStoreyLabel(storey.name, index)}
          title={`Show only ${storey.name}`}
          active={visibleStoreyId === storey.id}
          onClick={() => setVisibleStorey(storey.id)}
        />
      ))}
    </div>
  );
}

function StoreyChip({
  label,
  title,
  active,
  onClick,
}: {
  label: string;
  title: string;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded px-2 py-1 text-2xs font-medium transition-colors',
        active
          ? 'bg-brand-soft text-brand-ink'
          : 'text-ink-muted hover:bg-surface-sunken hover:text-ink',
      )}
    >
      {label}
    </button>
  );
}

/** Same shortening rule as the shell's storey tabs ("Ground Floor" → "Ground"). */
function shortStoreyLabel(name: string, index: number): string {
  const trimmed = name.replace(/\s*floor\s*$/i, '').trim();
  if (trimmed !== '') return trimmed;
  return index === 0 ? 'Ground' : `Level ${index}`;
}

// ---------------------------------------------------------------------------
// The honest status chip
// ---------------------------------------------------------------------------

export function ThreeDStatusChip({
  className,
}: {
  className?: string | undefined;
}): JSX.Element | null {
  const engineStatus = useThreeStore((s) => s.engineStatus);
  const engineDetail = useThreeStore((s) => s.engineDetail);
  const lastRebuild = useThreeStore((s) => s.lastRebuild);

  if (lastRebuild === null && engineStatus === 'idle') return null;

  const ms = lastRebuild === null ? null : Math.round(lastRebuild.ms * 10) / 10;
  const overBudget = ms !== null && ms >= 100;

  const engineText =
    engineStatus === 'ready'
      ? null
      : engineStatus === 'loading'
        ? 'openings cut once the engine loads'
        : engineStatus === 'unavailable'
          ? 'openings drawn on walls, not cut through'
          : null;

  return (
    <span
      className={cn(
        'pointer-events-none flex items-center gap-1.5 rounded-md border border-line bg-surface/90 px-2 py-1 text-2xs backdrop-blur garh-nums',
        overBudget ? 'text-warn-ink' : 'text-ink-muted',
        className,
      )}
      role="status"
      aria-label="3D view status"
      title={engineDetail ?? undefined}
      data-garh-3d-status={engineStatus}
      data-garh-rebuild-count={lastRebuild?.rebuildCount ?? 0}
      data-garh-rebuild-ms={ms ?? ''}
      data-garh-holes={lastRebuild === null ? '' : String(lastRebuild.holesApplied)}
    >
      {ms !== null ? <span>{`3D updated in ${ms} ms`}</span> : null}
      {engineText !== null ? (
        <span>
          {ms !== null ? '· ' : ''}
          {engineText}
        </span>
      ) : null}
    </span>
  );
}
