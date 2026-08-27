/**
 * SideRail — the left tool rail of the project shell (§12).
 *
 * The keyboard map is fixed by §12: V select · W wall · D door · N window ·
 * S stair · B balcony · M measure · F furniture. The rail exists so that
 * mouse-only users get the same tools (§15 accessibility: "canvas tools have
 * toolbar-button equivalents"), and so that the shortcut is discoverable —
 * every tooltip shows the key.
 *
 * The rail is rendered from Phase 0 onward with the tools DISABLED and an
 * honest reason, rather than hidden. The layout, the shortcut vocabulary and
 * the muscle memory are part of the product; pretending the tools do not exist
 * until Phase 4 would mean redesigning the shell twice.
 *
 * This component owns no tool state. The `model`/`ui` stores own the active
 * tool; the rail is told which one is active and reports clicks.
 */

import { Icon, Tooltip, ShortcutHint, cn } from '@garh/ui';
import type { IconName } from '@garh/ui';

/** Tool ids match the canvas tool state machine names (§12). */
export const TOOL_IDS = [
  'select',
  'wall',
  'door',
  'window',
  'stair',
  'balcony',
  'furniture',
  'measure',
] as const;
export type ToolId = (typeof TOOL_IDS)[number];

interface ToolDef {
  id: ToolId;
  label: string;
  key: string;
  icon: IconName;
  /** One line explaining what the tool does, shown under the name on hover. */
  blurb: string;
}

export const TOOLS: readonly ToolDef[] = [
  { id: 'select', label: 'Select', key: 'V', icon: 'cursor', blurb: 'Pick and move anything.' },
  {
    id: 'wall',
    label: 'Wall',
    key: 'W',
    icon: 'wall',
    blurb: 'Draw walls. Type a length while drawing.',
  },
  { id: 'door', label: 'Door', key: 'D', icon: 'door', blurb: 'Place a door on a wall.' },
  {
    id: 'window',
    label: 'Window',
    key: 'N',
    icon: 'window',
    blurb: 'Place a window or ventilator.',
  },
  {
    id: 'stair',
    label: 'Stair',
    key: 'S',
    icon: 'stair',
    blurb: 'Place a staircase; risers come from the floor height.',
  },
  {
    id: 'balcony',
    label: 'Balcony',
    key: 'B',
    icon: 'balcony',
    blurb: 'Draw a balcony or projection.',
  },
  {
    id: 'furniture',
    label: 'Furniture',
    key: 'F',
    icon: 'sofa',
    blurb: 'Place furniture at real Indian sizes.',
  },
  {
    id: 'measure',
    label: 'Measure',
    key: 'M',
    icon: 'ruler',
    blurb: 'Measure between two points.',
  },
];

/** Snap grid the canvas is on. Mirrors `ui.snapMode`. */
export type SnapMode = 'module' | 'fine' | 'off';

const SNAP_LABEL: Readonly<Record<SnapMode, string>> = {
  module: 'Snapping to 115 mm',
  fine: 'Snapping to 25 mm',
  off: 'Snapping off',
};

const SNAP_BLURB: Readonly<Record<SnapMode, string>> = {
  module: 'Half-brick module. Press G for the fine grid.',
  fine: '25 mm, for detail work. Press G to go back to 115 mm.',
  off: 'Nothing is being rounded. Press G for the 115 mm module.',
};

export interface SideRailProps {
  activeTool: ToolId;
  onToolChange: (tool: ToolId) => void;
  /** Disable every tool with one honest reason. */
  disabledReason?: string | undefined;

  /** 115 mm module / 25 mm fine / off (§F4). `G` cycles the first two. */
  snapMode?: SnapMode | undefined;
  onSnapModeChange?: ((mode: SnapMode) => void) | undefined;

  /** Grid visibility — a different thing from snapping, and often wanted off. */
  gridVisible?: boolean | undefined;
  onGridToggle?: (() => void) | undefined;

  className?: string | undefined;
}

export function SideRail({
  activeTool,
  onToolChange,
  disabledReason,
  snapMode = 'module',
  onSnapModeChange,
  gridVisible = true,
  onGridToggle,
  className,
}: SideRailProps): JSX.Element {
  const disabled = disabledReason !== undefined;
  const snapOn = snapMode !== 'off';

  return (
    <div
      role="toolbar"
      aria-orientation="vertical"
      aria-label="Drawing tools"
      className={cn(
        'flex h-full w-rail shrink-0 flex-col items-center gap-1 border-r border-line bg-surface py-2',
        className,
      )}
    >
      {TOOLS.map((tool) => {
        const active = tool.id === activeTool && !disabled;
        return (
          <Tooltip
            key={tool.id}
            placement="right"
            delayMs={120}
            content={
              <span className="block">
                <ShortcutHint label={tool.label} keys={tool.key} />
                <span className="mt-0.5 block text-ink-muted">
                  {disabled ? disabledReason : tool.blurb}
                </span>
              </span>
            }
          >
            <button
              type="button"
              aria-label={`${tool.label} tool (${tool.key})`}
              aria-pressed={active}
              aria-disabled={disabled || undefined}
              disabled={disabled}
              onClick={() => onToolChange(tool.id)}
              className={cn(
                'garh-focus-ring flex h-9 w-9 items-center justify-center rounded-md transition-colors',
                active
                  ? 'bg-brand-soft text-brand-ink'
                  : 'text-ink-muted hover:bg-surface-muted hover:text-ink',
                disabled && 'cursor-not-allowed opacity-40 hover:bg-transparent',
              )}
            >
              <Icon name={tool.icon} size={18} />
            </button>
          </Tooltip>
        );
      })}

      <span className="my-1 h-px w-6 bg-line" aria-hidden="true" />

      {/* Snap: G cycles module ↔ fine, and the button offers "off" as the
          third state, because a shortcut that cycles three ways is a shortcut
          nobody can predict. */}
      <Tooltip
        placement="right"
        delayMs={120}
        content={
          <span className="block">
            <ShortcutHint label={SNAP_LABEL[snapMode]} keys="G" />
            <span className="mt-0.5 block text-ink-muted">{SNAP_BLURB[snapMode]}</span>
          </span>
        }
      >
        <button
          type="button"
          aria-label={`${SNAP_LABEL[snapMode]}. Click to change (G)`}
          aria-pressed={snapOn}
          aria-disabled={onSnapModeChange === undefined || undefined}
          disabled={onSnapModeChange === undefined}
          onClick={() =>
            onSnapModeChange?.(
              snapMode === 'module' ? 'fine' : snapMode === 'fine' ? 'off' : 'module',
            )
          }
          className={cn(
            'garh-focus-ring flex h-9 w-9 items-center justify-center rounded-md text-2xs font-semibold transition-colors',
            snapOn
              ? 'bg-brand-soft text-brand-ink'
              : 'text-ink-muted hover:bg-surface-muted hover:text-ink',
            onSnapModeChange === undefined && 'cursor-not-allowed opacity-40',
          )}
        >
          {snapMode === 'off' ? <Icon name="grid" size={18} /> : snapMode === 'fine' ? '25' : '115'}
        </button>
      </Tooltip>

      <Tooltip
        placement="right"
        delayMs={120}
        content={
          <span className="block">
            <ShortcutHint label={gridVisible ? 'Hide the grid' : 'Show the grid'} keys="⇧G" />
            <span className="mt-0.5 block text-ink-muted">
              Only changes what you see. Snapping keeps working either way.
            </span>
          </span>
        }
      >
        <button
          type="button"
          aria-label={gridVisible ? 'Hide the grid (Shift+G)' : 'Show the grid (Shift+G)'}
          aria-pressed={gridVisible}
          aria-disabled={onGridToggle === undefined || undefined}
          disabled={onGridToggle === undefined}
          onClick={onGridToggle}
          className={cn(
            'garh-focus-ring flex h-9 w-9 items-center justify-center rounded-md transition-colors',
            gridVisible ? 'text-ink' : 'text-ink-subtle hover:bg-surface-muted hover:text-ink',
            onGridToggle === undefined && 'cursor-not-allowed opacity-40',
          )}
        >
          <Icon name="grid" size={18} />
        </button>
      </Tooltip>
    </div>
  );
}
