/**
 * ToolOptionsBar.tsx — the parametric controls the active tool reads.
 *
 * §F4 asks for a wall thickness selector (230 / 200 / 150 / 115 + custom) and
 * for parametric openings "from the inspector". This is that surface, sitting
 * under the top bar and changing with the tool — the values it edits are
 * exactly `ToolSettings`, the same object the tools receive and the same one
 * `X` writes when it flips a swing.
 *
 * ACCESSIBILITY (§15): every control here is a real, labelled form control, so
 * the whole tool layer is operable with no pointer and no keyboard shortcuts —
 * which is the actual requirement, rather than "the shortcuts are documented".
 *
 * Nothing in this file writes the document. Tool settings are chrome; the ops
 * are emitted by the tools.
 */

import type { JSX } from 'react';

import { Button, LengthInput, Select, cn, type SelectOption } from '@garh/ui';
import {
  OPENING_SWINGS,
  RAILING_KINDS,
  STAIR_KINDS,
  WALL_KINDS,
  type Direction4,
  type OpeningSwing,
  type RailingKind,
  type StairKind,
  type UnitsDisplay,
  type WallKind,
} from '@garh/model';

import type { FurnitureItem } from '../../../lib/schemas';
import { useUiStore } from '../../../stores/ui';
import { MAX_TOOL_WALL_THICKNESS_MM, WALL_THICKNESS_PRESETS } from './constants';
import type { OpeningParams } from './types';
import { useToolSettings } from './useToolSettings';

export interface ToolOptionsBarProps {
  /** `/catalog/furniture`. Empty until it loads; the picker says so. */
  furnitureCatalog?: readonly FurnitureItem[] | undefined;
  /** Project display units, for the length fields. */
  unitsDisplay?: UnitsDisplay | undefined;
  className?: string | undefined;
}

const SWING_LABELS: Readonly<Record<OpeningSwing, string>> = {
  'in-left': 'Opens in, left hand',
  'in-right': 'Opens in, right hand',
  'out-left': 'Opens out, left hand',
  'out-right': 'Opens out, right hand',
};

const WALL_KIND_LABELS: Readonly<Record<WallKind, string>> = {
  external: 'External',
  internal: 'Internal',
  parapet: 'Parapet',
};

const STAIR_KIND_LABELS: Readonly<Record<StairKind, string>> = {
  straight: 'Straight',
  dogleg: 'Dogleg',
  L: 'L-shaped',
  U: 'U-shaped',
};

const RAILING_LABELS: Readonly<Record<RailingKind, string>> = {
  ms: 'MS railing',
  glass: 'Glass',
  masonry: 'Masonry',
  ms_glass: 'MS + glass',
  none: 'None',
};

const DIRECTIONS: readonly Direction4[] = ['N', 'E', 'S', 'W'];
const DIRECTION_LABELS: Readonly<Record<Direction4, string>> = {
  N: 'Up towards north',
  E: 'Up towards east',
  S: 'Up towards south',
  W: 'Up towards west',
};

function options<T extends string>(
  values: readonly T[],
  labels: Readonly<Record<T, string>>,
): SelectOption<T>[] {
  return values.map((value) => ({ value, label: labels[value] }));
}

export function ToolOptionsBar({
  furnitureCatalog = [],
  unitsDisplay = 'ft-in',
  className,
}: ToolOptionsBarProps): JSX.Element | null {
  const activeTool = useUiStore((s) => s.activeTool);
  const settings = useToolSettings();

  const body = ((): JSX.Element | null => {
    switch (activeTool) {
      case 'wall':
        return (
          <>
            <fieldset className="flex items-center gap-1">
              <legend className="sr-only">Wall thickness</legend>
              <span className="text-2xs uppercase tracking-wide text-ink-subtle">Thickness</span>
              {WALL_THICKNESS_PRESETS.map((mm) => (
                <Button
                  key={mm}
                  size="sm"
                  variant={settings.wallThicknessMm === mm ? 'primary' : 'secondary'}
                  aria-pressed={settings.wallThicknessMm === mm}
                  onClick={() => {
                    settings.patch({ wallThicknessMm: mm });
                  }}
                >
                  {mm}
                </Button>
              ))}
              <div className="w-28">
                <LengthInput
                  label="Custom thickness"
                  labelHidden
                  valueMm={settings.wallThicknessMm}
                  onCommitMm={(mm) => {
                    settings.patch({ wallThicknessMm: mm });
                  }}
                  display={unitsDisplay}
                  bareUnit="mm"
                  minMm={1}
                  maxMm={MAX_TOOL_WALL_THICKNESS_MM}
                  placeholder="Custom"
                />
              </div>
            </fieldset>

            <label className="flex items-center gap-1.5 text-xs text-ink">
              <span className="sr-only">Wall kind</span>
              <Select
                value={settings.wallKind}
                onValueChange={(value) => {
                  settings.patch({ wallKind: value });
                }}
                options={options(WALL_KINDS, WALL_KIND_LABELS)}
                aria-label="Wall kind"
              />
            </label>

            <Button
              size="sm"
              variant={settings.ortho ? 'primary' : 'ghost'}
              aria-pressed={settings.ortho}
              onClick={() => {
                settings.patch({ ortho: !settings.ortho });
              }}
              title="Hold Shift while drawing to invert this"
            >
              Keep square
            </Button>

            <label className="flex items-center gap-1.5 text-xs text-ink">
              <input
                type="checkbox"
                checked={settings.wallLoadBearing}
                onChange={(event) => {
                  settings.patch({ wallLoadBearing: event.target.checked });
                }}
              />
              Load bearing
            </label>
          </>
        );

      case 'door':
      case 'window': {
        const kind =
          activeTool === 'door'
            ? 'door'
            : settings.windowVariant === 'ventilator'
              ? 'ventilator'
              : 'window';
        const params: OpeningParams =
          kind === 'door'
            ? settings.door
            : kind === 'window'
              ? settings.window
              : settings.ventilator;
        const write = (patch: Partial<OpeningParams>): void => {
          const next = { ...params, ...patch };
          settings.patch(
            kind === 'door'
              ? { door: next }
              : kind === 'window'
                ? { window: next }
                : { ventilator: next },
          );
        };
        return (
          <>
            {activeTool === 'window' ? (
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant={settings.windowVariant === 'window' ? 'primary' : 'secondary'}
                  aria-pressed={settings.windowVariant === 'window'}
                  onClick={() => {
                    settings.patch({ windowVariant: 'window' });
                  }}
                >
                  Window
                </Button>
                <Button
                  size="sm"
                  variant={settings.windowVariant === 'ventilator' ? 'primary' : 'secondary'}
                  aria-pressed={settings.windowVariant === 'ventilator'}
                  onClick={() => {
                    settings.patch({ windowVariant: 'ventilator' });
                  }}
                >
                  Ventilator
                </Button>
              </div>
            ) : null}
            <div className="w-32">
              <LengthInput
                label="Width"
                valueMm={params.widthMm}
                onCommitMm={(mm) => {
                  write({ widthMm: mm });
                }}
                display={unitsDisplay}
                bareUnit="mm"
                minMm={300}
              />
            </div>
            <div className="w-32">
              <LengthInput
                label="Height"
                valueMm={params.heightMm}
                onCommitMm={(mm) => {
                  write({ heightMm: mm });
                }}
                display={unitsDisplay}
                bareUnit="mm"
                minMm={300}
              />
            </div>
            {kind !== 'door' ? (
              <div className="w-32">
                <LengthInput
                  label="Sill"
                  valueMm={params.sillMm}
                  onCommitMm={(mm) => {
                    write({ sillMm: mm });
                  }}
                  display={unitsDisplay}
                  bareUnit="mm"
                  minMm={0}
                />
              </div>
            ) : null}
            <Select
              value={settings.swing}
              onValueChange={(value) => {
                settings.patch({ swing: value });
              }}
              options={options(OPENING_SWINGS, SWING_LABELS)}
              aria-label="Swing"
            />
          </>
        );
      }

      case 'stair':
        return (
          <>
            <Select
              value={settings.stairKind}
              onValueChange={(value) => {
                settings.patch({ stairKind: value });
              }}
              options={options(STAIR_KINDS, STAIR_KIND_LABELS)}
              aria-label="Stair type"
            />
            <Select
              value={settings.stairDirection}
              onValueChange={(value) => {
                settings.patch({ stairDirection: value });
              }}
              options={options(DIRECTIONS, DIRECTION_LABELS)}
              aria-label="Direction of travel"
            />
            <div className="w-32">
              <LengthInput
                label="Flight width"
                valueMm={settings.stairWidthMm}
                onCommitMm={(mm) => {
                  settings.patch({ stairWidthMm: mm });
                }}
                display={unitsDisplay}
                bareUnit="mm"
                minMm={600}
              />
            </div>
          </>
        );

      case 'balcony':
        return (
          <>
            <Select
              value={settings.railingKind}
              onValueChange={(value) => {
                settings.patch({ railingKind: value });
              }}
              options={options(RAILING_KINDS, RAILING_LABELS)}
              aria-label="Railing"
            />
            <div className="w-32">
              <LengthInput
                label="Railing height"
                valueMm={settings.railingHeightMm}
                onCommitMm={(mm) => {
                  settings.patch({ railingHeightMm: mm });
                }}
                display={unitsDisplay}
                bareUnit="mm"
                minMm={600}
              />
            </div>
          </>
        );

      case 'furniture':
        return (
          <>
            <Select
              value={settings.furnitureCatalogId ?? ''}
              onValueChange={(value) => {
                settings.patch({ furnitureCatalogId: value === '' ? null : value });
              }}
              options={furnitureCatalog.map((item) => ({
                value: item.id,
                label: `${item.name} · ${String(item.widthMm)} × ${String(item.depthMm)}`,
                group: item.category === '' ? undefined : item.category,
              }))}
              placeholder={furnitureCatalog.length === 0 ? 'Loading catalogue…' : 'Pick a piece'}
              aria-label="Furniture"
            />
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                settings.patch({
                  furnitureRotationDeg: (settings.furnitureRotationDeg + 90) % 360,
                });
              }}
              title="X rotates by 90°"
            >
              Rotate {settings.furnitureRotationDeg}°
            </Button>
          </>
        );

      case 'measure':
      case 'select':
      default:
        return null;
    }
  })();

  if (body === null) return null;

  return (
    <div
      className={cn(
        'flex flex-wrap items-end gap-3 border-b border-neutral-line bg-surface px-3 py-2',
        className,
      )}
      role="toolbar"
      aria-label="Tool options"
    >
      {body}
    </div>
  );
}
