/**
 * LayerPanel — the permanent side panel every CAD program has, for the nine §7
 * drawing layers.
 *
 * Three controls per row, in the order a drafter reaches for them:
 *
 *   visibility  get it out of the way while I work
 *   isolate     show me only this
 *   lock        keep it on screen but stop me nudging it
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THIS COMPONENT HOLDS NO POLICY
 * ════════════════════════════════════════════════════════════════════════════
 * Every row comes from `layerRows(state)`, including whether its controls are
 * live. That matters for A-TITL: the plan editor never draws a title block (the
 * drawings service's sheet frame does), so its row is rendered with the reason
 * stated instead of with a switch that would flip a boolean nothing on this
 * surface reads. A control that silently does nothing is the failure mode this
 * whole feature is built to avoid; showing the layer and saying where it lives
 * is the honest version.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THREE GLYPHS ARE DRAWN HERE AND NOT IMPORTED
 * ════════════════════════════════════════════════════════════════════════════
 * `@garh/ui`'s icon set has `lock` but no eye, no struck-through eye and no
 * open padlock — and that package is not this feature's to extend. The three
 * paths below follow its exact conventions (24-unit box, `currentColor`
 * stroke, 1.75 width, round caps) so they sit correctly beside the shared
 * icons, and the open padlock is the shared `lock` path with one half of the
 * shackle swung open so the pair reads as one state. If they are ever wanted
 * elsewhere, they move into `@garh/ui` unchanged.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ACCESSIBILITY (§15)
 * ════════════════════════════════════════════════════════════════════════════
 * Every control is a real `<button>` with an `aria-label` that names the layer
 * AND the action it will perform ("Hide Walls", "Show Walls"), plus
 * `aria-pressed` for its toggle state. A screen-reader user tabbing the panel
 * hears nine layers and what each switch will do, not nine unlabelled icons.
 */

import { Icon, cn } from '@garh/ui';

import { aciSwatchHex } from './layerSpecs';
import { selectHiddenCount, selectLockedCount, useLayerStore, type LayerRow } from './store';
import { useLayerRows } from './useLayerView';

// ---------------------------------------------------------------------------
// Glyphs (see the header for why they live here)
// ---------------------------------------------------------------------------

const EYE_PATH =
  'M2.5 12 C5 7.6 8.4 5.6 12 5.6 C15.6 5.6 19 7.6 21.5 12 ' +
  'C19 16.4 15.6 18.4 12 18.4 C8.4 18.4 5 16.4 2.5 12 Z ' +
  'M12 9.2 A2.8 2.8 0 1 0 12 14.8 A2.8 2.8 0 0 0 12 9.2 Z';
const EYE_OFF_PATH = `${EYE_PATH} M4 20 L20 4`;
/** The shared `lock` path with the right half of the shackle swung open. */
const UNLOCK_PATH =
  'M7.5 10.5 L7.5 8 A4.5 4.5 0 0 1 16.5 8 M5.5 10.5 L18.5 10.5 L18.5 20 L5.5 20 Z';

function Glyph({ d, size = 15 }: { d: string; size?: number | undefined }): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden={true}
      focusable="false"
    >
      <path d={d} />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// One row
// ---------------------------------------------------------------------------

const CONTROL =
  'garh-focus-ring inline-flex h-7 w-7 items-center justify-center rounded transition-colors';

/**
 * The ACI swatch.
 *
 * ACI 7 has no colour of its own — it means "black on white paper, white on a
 * dark screen" — so it is painted with the ink token, which does exactly that
 * in both themes. Every other index is AutoCAD's own RGB, unaltered, because
 * the architect is choosing layers they will later open in AutoCAD.
 */
function Swatch({ aci, dim }: { aci: number; dim: boolean }): JSX.Element {
  const hex = aciSwatchHex(aci);
  return (
    <span
      className={cn(
        'h-3 w-3 shrink-0 rounded-full border border-line-strong transition-opacity',
        hex === null ? 'bg-ink' : null,
        dim ? 'opacity-30' : null,
      )}
      style={hex === null ? undefined : { backgroundColor: hex }}
      aria-hidden={true}
    />
  );
}

function LayerRowItem({ row }: { row: LayerRow }): JSX.Element {
  // Actions are read imperatively, not subscribed to. Nine rows × three
  // subscriptions to values that never change is churn for nothing, and the
  // row already re-renders from `useLayerRows` when the state moves. Same
  // pattern as `PlanPage`'s `useUiStore.getState().setViewMode(…)`.
  const store = useLayerStore.getState;

  const inert = !row.actsOnCanvas;
  const dim = !row.visible || inert;

  return (
    <li
      className={cn(
        'flex items-center gap-2 rounded px-1 py-1',
        row.isolated ? 'bg-brand-soft' : 'hover:bg-surface-muted',
      )}
    >
      <button
        type="button"
        className={cn(
          CONTROL,
          row.visible ? 'text-ink-muted hover:text-ink' : 'text-ink-subtle hover:text-ink-muted',
          inert ? 'cursor-not-allowed opacity-40 hover:text-ink-subtle' : null,
        )}
        aria-pressed={row.visible}
        aria-label={`${row.visible ? 'Hide' : 'Show'} ${row.label} (${row.name})`}
        disabled={inert}
        onClick={() => store().toggleVisible(row.name)}
      >
        <Glyph d={row.visible ? EYE_PATH : EYE_OFF_PATH} />
      </button>

      <Swatch aci={row.aci} dim={dim} />

      {/* The description carries the layer's real meaning from layers.py; it is
          the title so the row stays one line at panel width. */}
      <span className="min-w-0 flex-1" title={row.description}>
        <span
          className={cn(
            'block truncate text-xs leading-4',
            dim ? 'text-ink-subtle' : 'text-ink',
            row.locked ? 'italic' : null,
          )}
        >
          {row.label}
        </span>
        <span className="block truncate font-mono text-2xs leading-3 text-ink-subtle">
          {row.name}
          {inert ? ' · sheet only' : null}
        </span>
      </span>

      <button
        type="button"
        className={cn(
          CONTROL,
          row.isolated ? 'text-brand-ink' : 'text-ink-subtle hover:text-ink',
          inert ? 'cursor-not-allowed opacity-40 hover:text-ink-subtle' : null,
        )}
        aria-pressed={row.isolated}
        aria-label={
          inert
            ? `Isolate unavailable for ${row.label} — ${row.unavailableReason ?? ''}`
            : row.isolated
              ? `Stop isolating ${row.label} (${row.name})`
              : `Isolate ${row.label} (${row.name})`
        }
        title={row.unavailableReason ?? undefined}
        disabled={inert}
        onClick={() => store().toggleIsolate(row.name)}
      >
        <Icon name="filter" size={15} />
      </button>

      <button
        type="button"
        className={cn(CONTROL, row.locked ? 'text-warn-ink' : 'text-ink-subtle hover:text-ink')}
        aria-pressed={row.locked}
        aria-label={`${row.locked ? 'Unlock' : 'Lock'} ${row.label} (${row.name})`}
        onClick={() => store().toggleLocked(row.name)}
      >
        {row.locked ? <Icon name="lock" size={15} /> : <Glyph d={UNLOCK_PATH} />}
      </button>
    </li>
  );
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

export interface LayerPanelProps {
  className?: string | undefined;
}

export function LayerPanel({ className }: LayerPanelProps): JSX.Element {
  const rows = useLayerRows();
  const hidden = useLayerStore(selectHiddenCount);
  const lockedCount = useLayerStore(selectLockedCount);
  const isolated = useLayerStore((s) => s.isolated);
  const showAll = useLayerStore((s) => s.showAll);
  const exitIsolate = useLayerStore((s) => s.exitIsolate);

  return (
    <section
      className={cn(
        'rounded-lg border border-line bg-surface/95 shadow-lg backdrop-blur',
        className,
      )}
      aria-label="Drawing layers"
    >
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <Icon name="layers" size={15} className="text-ink-muted" />
        <h2 className="flex-1 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
          Layers
        </h2>
        {/* Counts, not a list: the point is "something is off", and the rows
            below already say which. `garh-nums` is the tabular figure class. */}
        {hidden > 0 ? (
          <span className="garh-nums text-2xs text-ink-subtle">{hidden} hidden</span>
        ) : null}
        {lockedCount > 0 ? (
          <span className="garh-nums text-2xs text-warn-ink">{lockedCount} locked</span>
        ) : null}
        <button
          type="button"
          className="garh-focus-ring rounded px-1.5 py-0.5 text-2xs text-ink-muted hover:bg-surface-muted hover:text-ink disabled:opacity-40"
          onClick={showAll}
          disabled={hidden === 0}
        >
          Show all
        </button>
      </header>

      {isolated === null ? null : (
        <div className="flex items-center gap-2 border-b border-line bg-brand-soft px-3 py-1.5">
          <span className="flex-1 text-2xs text-brand-ink">
            Isolated: <span className="font-mono">{isolated}</span>
          </span>
          <button
            type="button"
            className="garh-focus-ring rounded px-1.5 py-0.5 text-2xs font-medium text-brand-ink hover:bg-surface"
            onClick={exitIsolate}
          >
            Exit
          </button>
        </div>
      )}

      <ul className="space-y-0.5 p-1.5">
        {rows.map((row) => (
          <LayerRowItem key={row.name} row={row} />
        ))}
      </ul>
    </section>
  );
}
