/**
 * ToolHud.tsx — the inline numeric entry, the live readouts, and the tool's
 * chips.
 *
 * §12 requires that typing a number while drawing overrides the mouse AND that
 * the entry is shown inline. This is the "shown" half: a small DOM overlay that
 * mounts into `<CanvasRoot overlay={…}>` and renders
 *
 *   ┌──────────────────────────────────────────────┐
 *   │  Length  3600▌   3,600 mm · 11'-10"          │   ← the entry, while typing
 *   ├──────────────────────────────────────────────┤
 *   │  Length 12'-0"   Angle 0°   Thickness 230 mm │   ← live readouts
 *   │  ⚠ There is already a wall along that line.  │   ← chips (never blocking)
 *   │  Click for the next corner · Enter finishes  │   ← the hint
 *   └──────────────────────────────────────────────┘
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THIS IS ALLOWED TO BE REACT
 * ────────────────────────────────────────────────────────────────────────────
 * §14 forbids a React render per pointer move *in the scene*. This subtree is
 * a dozen DOM nodes, it subscribes through `useSyncExternalStore` to a bus that
 * only notifies when the preview version actually changed, and pointer moves
 * are already coalesced to one per animation frame upstream. It also unmounts
 * itself entirely when there is nothing to say. The scene never re-renders
 * because of anything in here.
 *
 * POSITIONING — a known limit, stated plainly: the entry is anchored to the
 * bottom of the canvas, not to the cursor, because turning `cursorMm` into
 * screen pixels needs the camera and the camera belongs to the overlay layer.
 * `preview.cursorMm` is published for exactly that reason; when the overlays
 * agent wires a projection helper, this component takes an optional `anchorPx`
 * and follows the pointer. Everything else about it is final.
 */

import type { JSX } from 'react';

import { Chip, cn, type ChipSeverity } from '@garh/ui';

import { useToolPreview, type ToolPreviewBus } from './previewBus';
import type { ToolChip } from './types';

export interface ToolHudProps {
  /** Override the preview source (specs, split views). */
  bus?: ToolPreviewBus | undefined;
  className?: string | undefined;
}

const SEVERITY: Readonly<Record<ToolChip['severity'], ChipSeverity>> = {
  info: 'info',
  warning: 'warn',
  error: 'fail',
};

export function ToolHud({ bus, className }: ToolHudProps): JSX.Element | null {
  const preview = useToolPreview(bus);
  if (preview === null) return null;

  const hasEntry = preview.entry !== null;
  const hasReadouts = preview.readouts.length > 0;
  const hasChips = preview.chips.length > 0 || preview.blocked !== null;
  const showHint = preview.phase !== 'idle' || hasChips;
  if (!hasEntry && !hasReadouts && !hasChips && !showHint) return null;

  return (
    <div
      className={cn(
        'pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-2 p-3',
        className,
      )}
      // The HUD narrates a drawing in progress; announcing every millimetre
      // would be unusable, so it is polite and only the summary line is live.
      aria-live="polite"
      aria-atomic="false"
    >
      {preview.entry !== null ? (
        <div className="pointer-events-auto flex max-w-full flex-col gap-1 rounded-lg border border-brand/40 bg-surface px-3 py-2 shadow-lg">
          <div className="flex items-baseline gap-2">
            <span className="text-2xs uppercase tracking-wide text-ink-subtle">
              {preview.entry.label}
            </span>
            <span className="font-mono text-base tabular-nums text-ink">
              {preview.entry.buffer}
              <span className="ml-px inline-block h-4 w-px animate-pulse bg-brand align-middle" />
            </span>
            {preview.entry.echo !== '' ? (
              <span className="text-xs text-ink-subtle">{preview.entry.echo}</span>
            ) : null}
          </div>
          {preview.entry.error !== null ? (
            <span className="text-2xs text-fail-ink">{preview.entry.error}</span>
          ) : null}
          {preview.entry.fields.length > 1 ? (
            <span className="text-2xs text-ink-subtle">
              Tab switches to {fieldsAfter(preview.entry.fieldId, preview.entry.fields)}
            </span>
          ) : null}
        </div>
      ) : null}

      {hasReadouts ? (
        <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 rounded-lg bg-surface/90 px-3 py-1.5 text-xs shadow-sm backdrop-blur">
          {preview.readouts.map((readout) => (
            <span key={readout.id} className="flex items-baseline gap-1.5">
              <span className="text-2xs uppercase tracking-wide text-ink-subtle">
                {readout.label}
              </span>
              <span
                className={cn(
                  'tabular-nums text-ink',
                  readout.emphasis === true && 'text-sm font-semibold',
                )}
              >
                {readout.value}
              </span>
            </span>
          ))}
        </div>
      ) : null}

      {hasChips ? (
        <div className="pointer-events-auto flex flex-wrap items-center justify-center gap-2">
          {preview.blocked !== null ? (
            <Chip severity="fail" size="md" title={preview.blocked.fix ?? undefined}>
              {preview.blocked.message}
              {preview.blocked.fix !== null ? (
                <span className="ml-1 font-normal text-ink-subtle">{preview.blocked.fix}</span>
              ) : null}
            </Chip>
          ) : null}
          {preview.chips.map((chip) => (
            <Chip
              key={chip.id}
              severity={SEVERITY[chip.severity]}
              size="md"
              title={[chip.cite, chip.fix].filter((x) => x !== null).join(' · ') || undefined}
            >
              {chip.text}
            </Chip>
          ))}
        </div>
      ) : null}

      {showHint ? (
        <p className="text-2xs text-ink-subtle" aria-live="polite">
          {preview.hint}
        </p>
      ) : null}
    </div>
  );
}

/** "Width" / "Width, then Sill" — names the fields Tab will reach. */
function fieldsAfter(
  currentId: string,
  fields: readonly { readonly id: string; readonly label: string }[],
): string {
  const others = fields.filter((f) => f.id !== currentId).map((f) => f.label);
  if (others.length === 0) return 'the other field';
  if (others.length === 1) return others[0] ?? '';
  return `${others.slice(0, -1).join(', ')} and ${others[others.length - 1] ?? ''}`;
}
