/**
 * HatchPatternPicker.tsx — the fifteen patterns, choosable.
 *
 * Fifteen patterns have existed in the drawing engine since the library was
 * vendored, and until this component an architect could reach exactly none of
 * them. That is the whole reason A-9 exists, so the grid shows ALL of them —
 * every pattern the renderer can draw, drawn — rather than a curated few.
 *
 * The pattern a material implies is marked rather than hidden: an architect
 * choosing "stone" over the implied "brick" should be able to see what they
 * are overriding, and see it in the same visual language.
 *
 * Each tile is a real `<button>` with `aria-pressed`, so the keyboard and a
 * screen reader get the same control the mouse does, and the spec can click
 * the thing a person clicks.
 */

import { cn } from '@garh/ui';

import { HatchSwatch } from './HatchSwatch';
import { HATCH_PATTERN_KEYS, hatchPattern, type HatchPatternKey } from './patterns';

export interface HatchPatternPickerProps {
  /** The pattern currently in force. */
  readonly value: HatchPatternKey;
  readonly onChange: (pattern: HatchPatternKey) => void;
  /** The pattern the assigned material implies, marked in the grid. */
  readonly implied?: HatchPatternKey | null | undefined;
  readonly swatchSize?: number | undefined;
  readonly className?: string | undefined;
  /** Accessible name for the group. */
  readonly label?: string | undefined;
}

export function HatchPatternPicker({
  value,
  onChange,
  implied = null,
  swatchSize = 40,
  className,
  label = 'Hatch pattern',
}: HatchPatternPickerProps): JSX.Element {
  return (
    <div
      className={cn('grid grid-cols-3 gap-1.5 sm:grid-cols-4', className)}
      role="group"
      aria-label={label}
    >
      {HATCH_PATTERN_KEYS.map((key) => {
        const definition = hatchPattern(key);
        const selected = key === value;
        const isImplied = key === implied;
        return (
          <button
            key={key}
            type="button"
            data-pattern={key}
            aria-pressed={selected}
            aria-label={`${definition.label}${isImplied ? ' (from the material)' : ''}`}
            title={`${definition.label} — ${definition.acadName}`}
            onClick={() => {
              onChange(key);
            }}
            className={cn(
              'garh-focus-ring flex flex-col items-center gap-1 rounded border p-1.5 text-center',
              selected ? 'border-ink bg-surface-muted' : 'border-line hover:bg-surface-muted',
            )}
          >
            <HatchSwatch
              pattern={key}
              size={swatchSize}
              // The visible caption below already names it; a second
              // announcement of the same words is noise on a screen reader.
              label={null}
              className={cn('rounded-sm border border-line', selected && 'border-ink')}
            />
            <span className="w-full truncate text-2xs leading-3 text-ink">{definition.label}</span>
            {isImplied ? (
              <span className="text-2xs leading-3 text-ink-subtle">from material</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
